from __future__ import annotations

import sqlite3


def _build_inventory_filters(
    query: str,
    available_only: bool,
    include_reference_aliases: bool = False,
) -> tuple[str, list[object]]:
    cleaned = query.strip()
    params: list[object] = []
    where_clauses: list[str] = []

    if cleaned:
        normalized_wildcard = f"%{''.join(ch for ch in cleaned.upper() if ch.isalnum())}%"
        wildcard = f"%{cleaned}%"
        search_conditions = [
            """
            (
                p.part_number LIKE ? OR
                p.description LIKE ? OR
                p.normalized_part_number LIKE ?
            )
            """
        ]
        params.extend([wildcard, wildcard, normalized_wildcard])

        if include_reference_aliases:
            search_conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM reference_parts rp
                    LEFT JOIN reference_part_aliases rpa ON rpa.reference_part_id = rp.id
                    WHERE (
                        rp.normalized_part_number = p.normalized_part_number
                        OR rpa.normalized_alias_part_number = p.normalized_part_number
                    )
                    AND (
                        UPPER(rp.part_number) LIKE ?
                        OR UPPER(COALESCE(rp.description, '')) LIKE ?
                        OR UPPER(COALESCE(rpa.alias_part_number, '')) LIKE ?
                        OR REPLACE(UPPER(rp.part_number), '-', '') LIKE '%' || ? || '%'
                        OR REPLACE(UPPER(COALESCE(rpa.alias_part_number, '')), '-', '') LIKE '%' || ? || '%'
                    )
                )
                """
            )
            upper_wildcard = f"%{cleaned.upper()}%"
            params.extend([
                upper_wildcard,
                upper_wildcard,
                upper_wildcard,
                normalized_wildcard.strip('%'),
                normalized_wildcard.strip('%'),
            ])

        where_clauses.append("(" + " OR ".join(search_conditions) + ")")

    if available_only:
        where_clauses.append("ib.qty_on_hand > 0")

    return (" AND ".join(where_clauses) if where_clauses else "1 = 1"), params


def search_inventory(
    conn: sqlite3.Connection,
    query: str,
    available_only: bool = True,
    limit: int = 100,
    include_reference_aliases: bool = False,
) -> list[sqlite3.Row]:
    where_sql, params = _build_inventory_filters(
        query,
        available_only,
        include_reference_aliases=include_reference_aliases,
    )
    params.append(limit)

    return conn.execute(
        f"""
        SELECT
            p.part_number,
            p.description,
            p.manufacturer AS oem,
            p.uom,
            l.warehouse_code,
            l.location_code,
            ib.qty_on_hand,
            ib.updated_at
        FROM inventory_balances ib
        INNER JOIN parts p ON p.id = ib.part_id
        INNER JOIN locations l ON l.id = ib.location_id
        WHERE {where_sql}
        ORDER BY p.part_number ASC, l.warehouse_code ASC, l.location_code ASC
        LIMIT ?
        """,
        params,
    ).fetchall()


def search_inventory_records(
    conn: sqlite3.Connection,
    query: str,
    available_only: bool = True,
    limit: int = 100,
) -> list[sqlite3.Row]:
    where_sql, params = _build_inventory_filters(query, available_only)
    params.append(limit)

    return conn.execute(
        f"""
        SELECT
            p.id AS part_id,
            l.id AS location_id,
            p.part_number,
            p.description,
            p.manufacturer AS oem,
            p.uom,
            l.warehouse_code,
            l.location_code,
            ib.qty_on_hand,
            ib.updated_at
        FROM inventory_balances ib
        INNER JOIN parts p ON p.id = ib.part_id
        INNER JOIN locations l ON l.id = ib.location_id
        WHERE {where_sql}
        ORDER BY p.part_number ASC, l.warehouse_code ASC, l.location_code ASC
        LIMIT ?
        """,
        params,
    ).fetchall()


def recent_transactions(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            t.created_at,
            t.transaction_type,
            t.qty_change,
            t.reference,
            t.created_by,
            t.notes,
            p.part_number,
            l.warehouse_code,
            l.location_code
        FROM inventory_transactions t
        INNER JOIN parts p ON p.id = t.part_id
        INNER JOIN locations l ON l.id = t.location_id
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def transactions_for_import_run(
    conn: sqlite3.Connection,
    reference: str,
    limit: int = 500,
) -> list[sqlite3.Row]:
    cleaned_reference = reference.strip()
    if not cleaned_reference:
        return []

    return conn.execute(
        """
        SELECT
            t.created_at,
            t.transaction_type,
            t.qty_change,
            t.reference,
            t.created_by,
            t.notes,
            p.part_number,
            p.description,
            p.manufacturer AS oem,
            l.warehouse_code,
            l.location_code
        FROM inventory_transactions t
        INNER JOIN parts p ON p.id = t.part_id
        INNER JOIN locations l ON l.id = t.location_id
        WHERE t.reference = ?
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT ?
        """,
        (cleaned_reference, limit),
    ).fetchall()

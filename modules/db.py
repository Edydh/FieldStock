from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from modules.utils import normalize_part_number, normalize_text


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "fieldstock.db"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

# Curated local aliases that should be visible in inventory search even when
# external reference imports are incomplete.
DEFAULT_LOCAL_PART_ALIASES: dict[str, list[str]] = {
    "FK6YW": ["10DXV", "K4PPV", "KVY4F"],
}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema)
        ensure_default_local_aliases(conn)


def upsert_part(
    conn: sqlite3.Connection,
    part_number: str,
    description: str,
    uom: str = "",
    manufacturer: str = "",
    category: str = "",
) -> int:
    normalized_part_number = normalize_part_number(part_number)
    normalized_description = normalize_text(description)
    conn.execute(
        """
        INSERT INTO parts (
            part_number,
            description,
            manufacturer,
            category,
            uom,
            normalized_part_number,
            normalized_description
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_part_number) DO UPDATE SET
            part_number = excluded.part_number,
            description = excluded.description,
            manufacturer = COALESCE(NULLIF(excluded.manufacturer, ''), parts.manufacturer),
            category = COALESCE(NULLIF(excluded.category, ''), parts.category),
            uom = COALESCE(NULLIF(excluded.uom, ''), parts.uom),
            normalized_description = excluded.normalized_description,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            part_number.strip(),
            description.strip(),
            manufacturer.strip(),
            category.strip(),
            uom.strip(),
            normalized_part_number,
            normalized_description,
        ),
    )
    row = conn.execute(
        "SELECT id FROM parts WHERE normalized_part_number = ?",
        (normalized_part_number,),
    ).fetchone()
    return int(row["id"])


def upsert_location(conn: sqlite3.Connection, warehouse_code: str, location_code: str) -> int:
    normalized_warehouse = normalize_text(warehouse_code)
    normalized_location = normalize_text(location_code)
    conn.execute(
        """
        INSERT INTO locations (warehouse_code, location_code)
        VALUES (?, ?)
        ON CONFLICT(warehouse_code, location_code) DO UPDATE SET
            updated_at = CURRENT_TIMESTAMP
        """,
        (normalized_warehouse, normalized_location),
    )
    row = conn.execute(
        "SELECT id FROM locations WHERE warehouse_code = ? AND location_code = ?",
        (normalized_warehouse, normalized_location),
    ).fetchone()
    return int(row["id"])


def upsert_local_part_aliases(
    conn: sqlite3.Connection,
    part_id: int,
    aliases: Iterable[str],
) -> int:
    inserted = 0
    for alias in aliases:
        normalized_alias = normalize_part_number(alias)
        if not normalized_alias:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO local_part_aliases (
                part_id,
                alias_part_number,
                normalized_alias_part_number
            ) VALUES (?, ?, ?)
            """,
            (part_id, str(alias).strip(), normalized_alias),
        )
        if cursor.rowcount > 0:
            inserted += 1
    return inserted


def ensure_default_local_aliases(conn: sqlite3.Connection) -> int:
    inserted = 0
    for canonical_part, aliases in DEFAULT_LOCAL_PART_ALIASES.items():
        row = conn.execute(
            "SELECT id FROM parts WHERE normalized_part_number = ?",
            (normalize_part_number(canonical_part),),
        ).fetchone()
        if row is None:
            continue
        inserted += upsert_local_part_aliases(conn, int(row["id"]), aliases)
    return inserted


def set_inventory_balance(
    conn: sqlite3.Connection,
    part_id: int,
    location_id: int,
    qty_on_hand: float,
    created_by: str,
    reference: str,
) -> None:
    current = conn.execute(
        "SELECT qty_on_hand FROM inventory_balances WHERE part_id = ? AND location_id = ?",
        (part_id, location_id),
    ).fetchone()
    previous_qty = float(current["qty_on_hand"]) if current else 0.0
    change = qty_on_hand - previous_qty

    conn.execute(
        """
        INSERT INTO inventory_balances (part_id, location_id, qty_on_hand)
        VALUES (?, ?, ?)
        ON CONFLICT(part_id, location_id) DO UPDATE SET
            qty_on_hand = excluded.qty_on_hand,
            row_version = inventory_balances.row_version + 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (part_id, location_id, qty_on_hand),
    )

    conn.execute(
        """
        INSERT INTO inventory_transactions (
            part_id,
            location_id,
            transaction_type,
            qty_change,
            reference,
            created_by,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            part_id,
            location_id,
            "snapshot_import",
            change,
            reference,
            created_by,
            "Snapshot import applied",
        ),
    )


def record_inventory_adjustment(
    conn: sqlite3.Connection,
    part_id: int,
    location_id: int,
    qty_change: float,
    created_by: str,
    reference: str,
    notes: str = "",
) -> None:
    current = conn.execute(
        "SELECT qty_on_hand FROM inventory_balances WHERE part_id = ? AND location_id = ?",
        (part_id, location_id),
    ).fetchone()
    current_qty = float(current["qty_on_hand"]) if current else 0.0
    new_qty = current_qty + qty_change
    if new_qty < 0:
        raise ValueError("Adjustment would make inventory negative.")

    conn.execute(
        """
        INSERT INTO inventory_balances (part_id, location_id, qty_on_hand)
        VALUES (?, ?, ?)
        ON CONFLICT(part_id, location_id) DO UPDATE SET
            qty_on_hand = ?,
            row_version = inventory_balances.row_version + 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (part_id, location_id, new_qty, new_qty),
    )
    conn.execute(
        """
        INSERT INTO inventory_transactions (
            part_id,
            location_id,
            transaction_type,
            qty_change,
            reference,
            created_by,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (part_id, location_id, "manual_adjustment", qty_change, reference, created_by, notes),
    )


def clear_balances_for_missing_keys(
    conn: sqlite3.Connection,
    valid_keys: Iterable[tuple[int, int]],
    created_by: str,
    reference: str,
) -> int:
    key_set = {tuple(key) for key in valid_keys}
    rows = conn.execute(
        "SELECT part_id, location_id, qty_on_hand FROM inventory_balances WHERE qty_on_hand <> 0"
    ).fetchall()
    cleared = 0
    for row in rows:
        key = (int(row["part_id"]), int(row["location_id"]))
        if key in key_set:
            continue
        previous_qty = float(row["qty_on_hand"])
        conn.execute(
            """
            UPDATE inventory_balances
            SET qty_on_hand = 0,
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE part_id = ? AND location_id = ?
            """,
            key,
        )
        conn.execute(
            """
            INSERT INTO inventory_transactions (
                part_id,
                location_id,
                transaction_type,
                qty_change,
                reference,
                created_by,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key[0],
                key[1],
                "snapshot_import",
                -previous_qty,
                reference,
                created_by,
                "Snapshot import zeroed missing balance",
            ),
        )
        cleared += 1
    return cleared

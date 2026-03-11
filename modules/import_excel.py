from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any

import pandas as pd
import sqlite3

from modules.db import clear_balances_for_missing_keys, upsert_location, upsert_part, set_inventory_balance
from modules.utils import normalize_part_number, normalize_text, safe_float


DEFAULT_COLUMN_MAP = {
    "part_number": "Product identification",
    "description": "Product name",
    "manufacturer": "OEM",
    "warehouse": "Warehouse",
    "location_code": "Location",
    "uom": "Inventory unit",
    "quantity": "Total available",
}

OPTIONAL_COLUMN_KEYS = {"manufacturer"}


@dataclass
class ImportResult:
    rows_read: int
    rows_imported: int
    balances_zeroed: int


@dataclass
class ImportImpactResult:
    rows_to_import: int
    new_balances: int
    updated_balances: int
    unchanged_balances: int
    balances_to_zero: int
    balance_change_preview: list[dict[str, Any]]
    zero_balance_preview: list[dict[str, Any]]


def _record_import_run(
    conn: sqlite3.Connection,
    reference: str,
    created_by: str,
    source_filename: str,
    rows_read: int,
    rows_imported: int,
    balances_zeroed: int,
) -> None:
    conn.execute(
        """
        INSERT INTO import_runs (
            reference,
            created_by,
            source_filename,
            rows_read,
            rows_imported,
            balances_zeroed
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            reference,
            created_by,
            source_filename,
            rows_read,
            rows_imported,
            balances_zeroed,
        ),
    )


def read_excel_preview(file_bytes: bytes, max_rows: int = 20) -> tuple[pd.DataFrame, list[str]]:
    dataframe = pd.read_excel(BytesIO(file_bytes))
    dataframe = dataframe.fillna("")
    preview = dataframe.head(max_rows)
    return preview, [str(column) for column in dataframe.columns]


def _prepare_dataframe(file_bytes: bytes, column_map: dict[str, str]) -> pd.DataFrame:
    dataframe = pd.read_excel(BytesIO(file_bytes))
    dataframe = dataframe.fillna("")
    missing_columns = [
        source
        for key, source in column_map.items()
        if key not in OPTIONAL_COLUMN_KEYS and source not in dataframe.columns
    ]
    if missing_columns:
        joined = ", ".join(missing_columns)
        raise ValueError(f"Missing expected columns: {joined}")

    manufacturer_source = column_map.get("manufacturer", "")
    if manufacturer_source and manufacturer_source in dataframe.columns:
        manufacturer_series = dataframe[manufacturer_source].astype(str).str.strip()
    else:
        manufacturer_series = pd.Series("", index=dataframe.index, dtype="object")

    prepared = pd.DataFrame(
        {
            "part_number": dataframe[column_map["part_number"]].astype(str).str.strip(),
            "description": dataframe[column_map["description"]].astype(str).str.strip(),
            "manufacturer": manufacturer_series,
            "warehouse": dataframe[column_map["warehouse"]].astype(str).str.strip(),
            "location_code": dataframe[column_map["location_code"]].astype(str).str.strip(),
            "uom": dataframe[column_map["uom"]].astype(str).str.strip(),
            "quantity": dataframe[column_map["quantity"]],
        }
    )
    prepared = prepared[prepared["part_number"] != ""]
    prepared["warehouse"] = prepared["warehouse"].replace("", "UNSPECIFIED")
    prepared["location_code"] = prepared["location_code"].replace("", "UNASSIGNED")
    prepared["quantity"] = prepared["quantity"].apply(safe_float)

    grouped = (
        prepared.groupby(["part_number", "description", "manufacturer", "warehouse", "location_code", "uom"], dropna=False)[
            "quantity"
        ]
        .sum()
        .reset_index()
    )
    return grouped.sort_values(["part_number", "warehouse", "location_code"]).reset_index(drop=True)


def import_snapshot(
    conn: sqlite3.Connection,
    file_bytes: bytes,
    column_map: dict[str, str] | None,
    created_by: str,
    reference: str,
    source_filename: str = "",
) -> ImportResult:
    mapping = column_map or DEFAULT_COLUMN_MAP
    prepared = _prepare_dataframe(file_bytes, mapping)
    imported_keys: list[tuple[int, int]] = []

    with conn:
        for row in prepared.itertuples(index=False):
            part_id = upsert_part(
                conn=conn,
                part_number=row.part_number,
                description=row.description,
                manufacturer=row.manufacturer,
                uom=row.uom,
            )
            location_id = upsert_location(
                conn=conn,
                warehouse_code=row.warehouse,
                location_code=row.location_code,
            )
            set_inventory_balance(
                conn=conn,
                part_id=part_id,
                location_id=location_id,
                qty_on_hand=row.quantity,
                created_by=created_by,
                reference=reference,
            )
            imported_keys.append((part_id, location_id))

        zeroed = clear_balances_for_missing_keys(
            conn=conn,
            valid_keys=imported_keys,
            created_by=created_by,
            reference=reference,
        )

        _record_import_run(
            conn=conn,
            reference=reference,
            created_by=created_by,
            source_filename=source_filename,
            rows_read=len(prepared.index),
            rows_imported=len(prepared.index),
            balances_zeroed=zeroed,
        )

    return ImportResult(
        rows_read=len(prepared.index),
        rows_imported=len(prepared.index),
        balances_zeroed=zeroed,
    )


def analyze_snapshot_import(
    conn: sqlite3.Connection,
    file_bytes: bytes,
    column_map: dict[str, str] | None = None,
) -> ImportImpactResult:
    mapping = column_map or DEFAULT_COLUMN_MAP
    prepared = _prepare_dataframe(file_bytes, mapping)
    existing_rows = conn.execute(
        """
        SELECT
            p.part_number,
            p.description,
            p.manufacturer,
            p.uom,
            p.normalized_part_number,
            l.warehouse_code,
            l.location_code,
            ib.qty_on_hand
        FROM inventory_balances ib
        INNER JOIN parts p ON p.id = ib.part_id
        INNER JOIN locations l ON l.id = ib.location_id
        """
    ).fetchall()

    existing_by_key: dict[tuple[str, str, str], sqlite3.Row] = {}
    for row in existing_rows:
        key = (
            str(row["normalized_part_number"]),
            normalize_text(row["warehouse_code"]),
            normalize_text(row["location_code"]),
        )
        existing_by_key[key] = row

    imported_keys: set[tuple[str, str, str]] = set()
    balance_change_preview: list[dict[str, Any]] = []
    new_balances = 0
    updated_balances = 0
    unchanged_balances = 0

    for row in prepared.itertuples(index=False):
        key = (
            normalize_part_number(row.part_number),
            normalize_text(row.warehouse),
            normalize_text(row.location_code),
        )
        imported_keys.add(key)
        existing = existing_by_key.get(key)
        new_qty = float(row.quantity)

        if existing is None:
            new_balances += 1
            balance_change_preview.append(
                {
                    "change_type": "New balance",
                    "part_number": row.part_number,
                    "description": row.description,
                    "OEM": row.manufacturer,
                    "warehouse_code": normalize_text(row.warehouse),
                    "location_code": normalize_text(row.location_code),
                    "previous_qty": 0.0,
                    "new_qty": new_qty,
                    "qty_change": new_qty,
                }
            )
            continue

        previous_qty = float(existing["qty_on_hand"])
        if previous_qty == new_qty:
            unchanged_balances += 1
            continue

        updated_balances += 1
        balance_change_preview.append(
            {
                "change_type": "Update balance",
                "part_number": row.part_number,
                "description": row.description,
                "OEM": row.manufacturer or existing["manufacturer"],
                "warehouse_code": normalize_text(row.warehouse),
                "location_code": normalize_text(row.location_code),
                "previous_qty": previous_qty,
                "new_qty": new_qty,
                "qty_change": new_qty - previous_qty,
            }
        )

    zero_balance_preview: list[dict[str, Any]] = []
    for key, row in existing_by_key.items():
        if key in imported_keys:
            continue
        previous_qty = float(row["qty_on_hand"])
        if previous_qty == 0:
            continue
        zero_balance_preview.append(
            {
                "change_type": "Zero missing balance",
                "part_number": row["part_number"],
                "description": row["description"],
                "OEM": row["manufacturer"],
                "warehouse_code": row["warehouse_code"],
                "location_code": row["location_code"],
                "previous_qty": previous_qty,
                "new_qty": 0.0,
                "qty_change": -previous_qty,
            }
        )

    return ImportImpactResult(
        rows_to_import=len(prepared.index),
        new_balances=new_balances,
        updated_balances=updated_balances,
        unchanged_balances=unchanged_balances,
        balances_to_zero=len(zero_balance_preview),
        balance_change_preview=balance_change_preview,
        zero_balance_preview=zero_balance_preview,
    )


def recent_import_runs(
    conn: sqlite3.Connection,
    limit: int = 20,
    reference_query: str = "",
    created_at_from: date | str | None = None,
    created_at_to: date | str | None = None,
) -> list[sqlite3.Row]:
    where_clauses: list[str] = []
    params: list[object] = []

    cleaned_reference = reference_query.strip()
    if cleaned_reference:
        where_clauses.append("reference LIKE ?")
        params.append(f"%{cleaned_reference}%")

    if created_at_from is not None:
        where_clauses.append("date(created_at) >= date(?)")
        params.append(str(created_at_from))

    if created_at_to is not None:
        where_clauses.append("date(created_at) <= date(?)")
        params.append(str(created_at_to))

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(limit)

    return conn.execute(
        f"""
        SELECT
            created_at,
            reference,
            created_by,
            source_filename,
            rows_read,
            rows_imported,
            balances_zeroed
        FROM import_runs
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def summarize_import(file_bytes: bytes, column_map: dict[str, str] | None = None) -> dict[str, Any]:
    mapping = column_map or DEFAULT_COLUMN_MAP
    prepared = _prepare_dataframe(file_bytes, mapping)
    return {
        "rows": len(prepared.index),
        "unique_parts": prepared["part_number"].nunique(),
        "warehouses": sorted(prepared["warehouse"].unique().tolist()),
        "locations": prepared["location_code"].nunique(),
        "total_quantity": float(prepared["quantity"].sum()),
    }

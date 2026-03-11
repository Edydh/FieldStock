from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pandas as pd
import sqlite3

from modules.db import clear_balances_for_missing_keys, upsert_location, upsert_part, set_inventory_balance
from modules.utils import safe_float


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

    return ImportResult(
        rows_read=len(prepared.index),
        rows_imported=len(prepared.index),
        balances_zeroed=zeroed,
    )


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

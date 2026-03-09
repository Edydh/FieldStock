from __future__ import annotations

import sqlite3
from io import BytesIO
from typing import Iterator

import pandas as pd
import pytest

from modules.db import SCHEMA_PATH, record_inventory_adjustment
from modules.import_excel import import_snapshot, summarize_import
from modules.search import search_inventory


def make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def make_excel_bytes(rows: list[dict[str, object]]) -> bytes:
    dataframe = pd.DataFrame(rows)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False)
    return buffer.getvalue()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = make_connection()
    try:
        yield connection
    finally:
        connection.close()


def test_snapshot_import_creates_balances_and_transactions(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 2,
            },
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 3,
            },
        ]
    )

    result = import_snapshot(
        conn=conn,
        file_bytes=file_bytes,
        column_map=None,
        created_by="tester",
        reference="import-001",
    )

    balance = conn.execute(
        """
        SELECT ib.qty_on_hand
        FROM inventory_balances ib
        INNER JOIN parts p ON p.id = ib.part_id
        INNER JOIN locations l ON l.id = ib.location_id
        WHERE p.part_number = ? AND l.warehouse_code = ? AND l.location_code = ?
        """,
        ("P-100", "MAIN", "A1"),
    ).fetchone()
    transaction_count = conn.execute("SELECT COUNT(*) AS count FROM inventory_transactions").fetchone()["count"]

    assert result.rows_read == 1
    assert result.rows_imported == 1
    assert result.balances_zeroed == 0
    assert balance is not None
    assert float(balance["qty_on_hand"]) == 5.0
    assert transaction_count == 1


def test_snapshot_import_zeroes_missing_balances(conn: sqlite3.Connection) -> None:
    initial_file = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 5,
            },
            {
                "Product identification": "P-200",
                "Product name": "Drive 600GB",
                "Warehouse": "Main",
                "Location": "B2",
                "Inventory unit": "EA",
                "Total available": 2,
            },
        ]
    )
    updated_file = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 4,
            }
        ]
    )

    import_snapshot(
        conn=conn,
        file_bytes=initial_file,
        column_map=None,
        created_by="tester",
        reference="import-001",
    )
    result = import_snapshot(
        conn=conn,
        file_bytes=updated_file,
        column_map=None,
        created_by="tester",
        reference="import-002",
    )

    remaining_balance = conn.execute(
        """
        SELECT ib.qty_on_hand
        FROM inventory_balances ib
        INNER JOIN parts p ON p.id = ib.part_id
        WHERE p.part_number = ?
        """,
        ("P-100",),
    ).fetchone()
    cleared_balance = conn.execute(
        """
        SELECT ib.qty_on_hand
        FROM inventory_balances ib
        INNER JOIN parts p ON p.id = ib.part_id
        WHERE p.part_number = ?
        """,
        ("P-200",),
    ).fetchone()
    zeroing_transaction = conn.execute(
        """
        SELECT qty_change, notes
        FROM inventory_transactions t
        INNER JOIN parts p ON p.id = t.part_id
        WHERE p.part_number = ?
        ORDER BY t.id DESC
        LIMIT 1
        """,
        ("P-200",),
    ).fetchone()

    assert result.rows_read == 1
    assert result.rows_imported == 1
    assert result.balances_zeroed == 1
    assert remaining_balance is not None
    assert float(remaining_balance["qty_on_hand"]) == 4.0
    assert cleared_balance is not None
    assert float(cleared_balance["qty_on_hand"]) == 0.0
    assert zeroing_transaction is not None
    assert float(zeroing_transaction["qty_change"]) == -2.0
    assert zeroing_transaction["notes"] == "Snapshot import zeroed missing balance"


def test_manual_adjustment_rejects_negative_inventory(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-300",
                "Product name": "Memory 16GB",
                "Warehouse": "Main",
                "Location": "C3",
                "Inventory unit": "EA",
                "Total available": 1,
            }
        ]
    )
    import_snapshot(
        conn=conn,
        file_bytes=file_bytes,
        column_map=None,
        created_by="tester",
        reference="import-001",
    )

    balance_row = conn.execute(
        "SELECT part_id, location_id FROM inventory_balances LIMIT 1"
    ).fetchone()

    with pytest.raises(ValueError, match="negative"):
        record_inventory_adjustment(
            conn=conn,
            part_id=int(balance_row["part_id"]),
            location_id=int(balance_row["location_id"]),
            qty_change=-2,
            created_by="tester",
            reference="adjust-001",
            notes="consume stock",
        )

    unchanged_balance = conn.execute(
        "SELECT qty_on_hand FROM inventory_balances LIMIT 1"
    ).fetchone()
    adjustment_transactions = conn.execute(
        "SELECT COUNT(*) AS count FROM inventory_transactions WHERE transaction_type = 'manual_adjustment'"
    ).fetchone()["count"]

    assert unchanged_balance is not None
    assert float(unchanged_balance["qty_on_hand"]) == 1.0
    assert adjustment_transactions == 0


def test_search_inventory_matches_normalized_part_number(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-100-A",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 5,
            }
        ]
    )
    import_snapshot(
        conn=conn,
        file_bytes=file_bytes,
        column_map=None,
        created_by="tester",
        reference="import-001",
    )

    rows = search_inventory(conn, query="p100a", available_only=True)

    assert len(rows) == 1
    assert rows[0]["part_number"] == "P-100-A"


def test_search_inventory_available_only_filters_zero_stock(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 5,
            },
            {
                "Product identification": "P-200",
                "Product name": "Drive 600GB",
                "Warehouse": "Main",
                "Location": "B2",
                "Inventory unit": "EA",
                "Total available": 0,
            },
        ]
    )
    import_snapshot(
        conn=conn,
        file_bytes=file_bytes,
        column_map=None,
        created_by="tester",
        reference="import-001",
    )

    available_rows = search_inventory(conn, query="Drive", available_only=True)
    all_rows = search_inventory(conn, query="Drive", available_only=False)

    assert len(available_rows) == 1
    assert available_rows[0]["part_number"] == "P-100"
    assert len(all_rows) == 2


def test_summarize_import_raises_for_missing_required_columns() -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Total available": 5,
            }
        ]
    )

    with pytest.raises(ValueError, match="Missing expected columns"):
        summarize_import(file_bytes)
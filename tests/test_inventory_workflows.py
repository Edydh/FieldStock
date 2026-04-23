from __future__ import annotations

import sqlite3
from io import BytesIO
from typing import Iterator

import pandas as pd
import pytest

from modules.db import SCHEMA_PATH, record_inventory_adjustment
from modules.import_excel import analyze_snapshot_import, import_snapshot, recent_import_runs, summarize_import
from modules.reference_import import import_reference_rows
from modules.search import recent_transactions, search_inventory, transactions_for_import_run


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
    import_run = conn.execute(
        "SELECT reference, created_by, rows_read, rows_imported, balances_zeroed FROM import_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()

    assert result.rows_read == 1
    assert result.rows_imported == 1
    assert result.balances_zeroed == 0
    assert balance is not None
    assert float(balance["qty_on_hand"]) == 5.0
    assert transaction_count == 1
    assert import_run is not None
    assert import_run["reference"] == "import-001"
    assert import_run["created_by"] == "tester"
    assert import_run["rows_read"] == 1
    assert import_run["rows_imported"] == 1
    assert import_run["balances_zeroed"] == 0


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


def test_recent_transactions_includes_manual_adjustment_notes(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-300",
                "Product name": "Memory 16GB",
                "Warehouse": "Main",
                "Location": "C3",
                "Inventory unit": "EA",
                "Total available": 3,
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

    record_inventory_adjustment(
        conn=conn,
        part_id=int(balance_row["part_id"]),
        location_id=int(balance_row["location_id"]),
        qty_change=-1,
        created_by="tester",
        reference="adjust-001",
        notes="Consumed one DIMM for bench repair",
    )

    transactions = recent_transactions(conn)

    assert len(transactions) >= 1
    assert transactions[0]["reference"] == "adjust-001"
    assert transactions[0]["notes"] == "Consumed one DIMM for bench repair"


def test_search_inventory_matches_normalized_part_number(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-100-A",
                "Product name": "Drive 300GB",
                "OEM": "Seagate",
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
    assert rows[0]["oem"] == "Seagate"


def test_snapshot_import_stores_oem_in_search_results(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-500",
                "Product name": "SSD 960GB",
                "OEM": "Samsung",
                "Warehouse": "Main",
                "Location": "D4",
                "Inventory unit": "EA",
                "Total available": 6,
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

    row = conn.execute(
        "SELECT manufacturer FROM parts WHERE part_number = ?",
        ("P-500",),
    ).fetchone()
    rows = search_inventory(conn, query="P-500", available_only=True)

    assert row is not None
    assert row["manufacturer"] == "Samsung"
    assert len(rows) == 1
    assert rows[0]["oem"] == "Samsung"


def test_search_inventory_matches_reference_aliases_when_enabled(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "872737001",
                "Product name": "Drive 1.2TB",
                "OEM": "HPE",
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

    import_reference_rows(
        conn,
        rows=[
            {
                "part_number": "872737-001",
                "description": "HP G8 G9 1.2-TB 12G 10K 2.5 SAS SC",
                "manufacturer": "HPE",
                "system_models": ["G8 G9"],
                "aliases": ["872479-B21", "872479-S21", "876936-002"],
            }
        ],
        source_name="HardDrivesDirect",
        source_type="web_listing",
        source_url="https://example.test/hp",
    )

    rows_without_alias = search_inventory(
        conn,
        query="872479-B21",
        available_only=True,
        include_reference_aliases=False,
    )
    rows_with_alias = search_inventory(
        conn,
        query="872479-B21",
        available_only=True,
        include_reference_aliases=True,
    )

    assert rows_without_alias == []
    assert len(rows_with_alias) == 1
    assert rows_with_alias[0]["part_number"] == "872737001"


def test_analyze_snapshot_import_summarizes_expected_balance_changes(conn: sqlite3.Connection) -> None:
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
    next_file = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 4,
            },
            {
                "Product identification": "P-300",
                "Product name": "Drive 900GB",
                "Warehouse": "Main",
                "Location": "C3",
                "Inventory unit": "EA",
                "Total available": 1,
            },
        ]
    )

    import_snapshot(
        conn=conn,
        file_bytes=initial_file,
        column_map=None,
        created_by="tester",
        reference="import-001",
    )

    impact = analyze_snapshot_import(conn, next_file)

    assert impact.rows_to_import == 2
    assert impact.new_balances == 1
    assert impact.updated_balances == 1
    assert impact.unchanged_balances == 0
    assert impact.balances_to_zero == 1
    assert {row["change_type"] for row in impact.balance_change_preview} == {"New balance", "Update balance"}
    assert impact.zero_balance_preview[0]["part_number"] == "P-200"


def test_analyze_snapshot_import_counts_unchanged_balances(conn: sqlite3.Connection) -> None:
    file_bytes = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
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

    impact = analyze_snapshot_import(conn, file_bytes)

    assert impact.rows_to_import == 1
    assert impact.new_balances == 0
    assert impact.updated_balances == 0
    assert impact.unchanged_balances == 1
    assert impact.balances_to_zero == 0


def test_recent_import_runs_returns_latest_runs_first(conn: sqlite3.Connection) -> None:
    first_file = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 5,
            }
        ]
    )
    second_file = make_excel_bytes(
        [
            {
                "Product identification": "P-200",
                "Product name": "Drive 600GB",
                "Warehouse": "Main",
                "Location": "B2",
                "Inventory unit": "EA",
                "Total available": 2,
            }
        ]
    )

    import_snapshot(
        conn=conn,
        file_bytes=first_file,
        column_map=None,
        created_by="tester-a",
        reference="import-001",
        source_filename="first.xlsx",
    )
    import_snapshot(
        conn=conn,
        file_bytes=second_file,
        column_map=None,
        created_by="tester-b",
        reference="import-002",
        source_filename="second.xlsx",
    )

    runs = recent_import_runs(conn)

    assert len(runs) == 2
    assert runs[0]["reference"] == "import-002"
    assert runs[0]["created_by"] == "tester-b"
    assert runs[0]["source_filename"] == "second.xlsx"
    assert runs[0]["rows_imported"] == 1
    assert runs[0]["balances_zeroed"] == 1
    assert runs[1]["reference"] == "import-001"


def test_recent_import_runs_filters_by_reference(conn: sqlite3.Connection) -> None:
    first_file = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 5,
            }
        ]
    )
    second_file = make_excel_bytes(
        [
            {
                "Product identification": "P-200",
                "Product name": "Drive 600GB",
                "Warehouse": "Main",
                "Location": "B2",
                "Inventory unit": "EA",
                "Total available": 2,
            }
        ]
    )

    import_snapshot(conn=conn, file_bytes=first_file, column_map=None, created_by="tester", reference="cycle-001")
    import_snapshot(conn=conn, file_bytes=second_file, column_map=None, created_by="tester", reference="month-end-002")

    runs = recent_import_runs(conn, reference_query="month-end")

    assert len(runs) == 1
    assert runs[0]["reference"] == "month-end-002"


def test_recent_import_runs_filters_by_date_range(conn: sqlite3.Connection) -> None:
    first_file = make_excel_bytes(
        [
            {
                "Product identification": "P-100",
                "Product name": "Drive 300GB",
                "Warehouse": "Main",
                "Location": "A1",
                "Inventory unit": "EA",
                "Total available": 5,
            }
        ]
    )
    second_file = make_excel_bytes(
        [
            {
                "Product identification": "P-200",
                "Product name": "Drive 600GB",
                "Warehouse": "Main",
                "Location": "B2",
                "Inventory unit": "EA",
                "Total available": 2,
            }
        ]
    )

    import_snapshot(conn=conn, file_bytes=first_file, column_map=None, created_by="tester", reference="import-001")
    import_snapshot(conn=conn, file_bytes=second_file, column_map=None, created_by="tester", reference="import-002")

    conn.execute("UPDATE import_runs SET created_at = ? WHERE reference = ?", ("2026-03-01 09:00:00", "import-001"))
    conn.execute("UPDATE import_runs SET created_at = ? WHERE reference = ?", ("2026-03-10 18:30:00", "import-002"))

    runs = recent_import_runs(conn, created_at_from="2026-03-05", created_at_to="2026-03-11")

    assert len(runs) == 1
    assert runs[0]["reference"] == "import-002"


def test_transactions_for_import_run_returns_related_snapshot_rows(conn: sqlite3.Connection) -> None:
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

    import_snapshot(conn=conn, file_bytes=initial_file, column_map=None, created_by="tester", reference="import-001")
    import_snapshot(conn=conn, file_bytes=updated_file, column_map=None, created_by="tester", reference="import-002")

    rows = transactions_for_import_run(conn, "import-002")

    assert len(rows) == 2
    assert {row["part_number"] for row in rows} == {"P-100", "P-200"}
    assert {float(row["qty_change"]) for row in rows} == {-1.0, -2.0}
    assert all(row["reference"] == "import-002" for row in rows)


def test_transactions_for_import_run_returns_empty_for_blank_reference(conn: sqlite3.Connection) -> None:
    assert transactions_for_import_run(conn, "") == []


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
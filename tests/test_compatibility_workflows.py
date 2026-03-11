from __future__ import annotations

import sqlite3
from typing import Iterator

import pytest

from modules.db import SCHEMA_PATH, record_inventory_adjustment, upsert_location, upsert_part
from modules.reference_import import extract_candidate_models, import_reference_rows, parse_harddrivesdirect_listing
from modules.reference_search import compatibility_inventory_for_model, inventory_model_priority_matrix, search_inventory_detected_models, search_system_models


def make_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = make_connection()
    try:
        yield connection
    finally:
        connection.close()


def test_import_reference_rows_creates_models_and_compatibility(conn: sqlite3.Connection) -> None:
    result = import_reference_rows(
        conn,
        rows=[
            {
                "part_number": "872737-001",
                "description": "HP G8 G9 1.2-TB 12G 10K 2.5 SAS SC",
                "manufacturer": "HPE",
                "system_models": ["G8 G9"],
                "aliases": ["872737-001", "872737001"],
            }
        ],
        source_name="HardDrivesDirect",
        source_type="web_listing",
        source_url="https://example.test/hp",
    )

    models = search_system_models(conn, "G8 G9")

    assert result.rows_seen == 1
    assert result.parts_upserted == 1
    assert result.models_upserted == 1
    assert result.compatibilities_upserted == 1
    assert len(models) == 1
    assert models[0]["model_name"] == "G8 G9"
    assert models[0]["compatible_part_count"] == 1


def test_compatibility_search_matches_local_inventory_by_alias(conn: sqlite3.Connection) -> None:
    part_id = upsert_part(
        conn,
        part_number="872737001",
        description="Local stock HPE SAS drive",
        manufacturer="HPE",
        uom="EA",
    )
    location_id = upsert_location(conn, "Main", "A1")
    record_inventory_adjustment(
        conn,
        part_id=part_id,
        location_id=location_id,
        qty_change=4,
        created_by="tester",
        reference="seed-001",
        notes="Seed local stock",
    )

    import_reference_rows(
        conn,
        rows=[
            {
                "part_number": "872737-001",
                "description": "HP G8 G9 1.2-TB 12G 10K 2.5 SAS SC",
                "manufacturer": "HPE",
                "system_models": ["G8 G9"],
                "aliases": ["872737001", "872737-001"],
            }
        ],
        source_name="HardDrivesDirect",
        source_type="web_listing",
        source_url="https://example.test/hp",
    )

    model = search_system_models(conn, "G8 G9")[0]
    rows = compatibility_inventory_for_model(conn, int(model["id"]), available_only=True)

    assert len(rows) == 1
    assert rows[0]["reference_part_number"] == "872737-001"
    assert rows[0]["local_part_number"] == "872737001"
    assert float(rows[0]["qty_on_hand"]) == 4.0
    assert rows[0]["match_status"] == "Matched in inventory"


def test_parse_harddrivesdirect_listing_extracts_rows_and_models() -> None:
    html = """
    <html>
      <body>
        <table>
          <tr><th>Product Name</th><th>Part#</th></tr>
          <tr>
            <td><a href="product_info.php?products_id=1">872737-001 HP G8 G9 1.2-TB 12G 10K 2.5 SAS SC</a></td>
            <td>872737-001</td>
          </tr>
          <tr>
            <td><a href="product_info.php?products_id=2">830287-B21 HP Intel Xeon E5-4655v4 2.5GHz DL560 G9</a></td>
            <td>830287-B21</td>
          </tr>
        </table>
      </body>
    </html>
    """

    rows = parse_harddrivesdirect_listing(html, "https://www.harddrivesdirect.com/sample")

    assert len(rows) == 2
    assert rows[0]["part_number"] == "872737-001"
    assert "G8 G9" in rows[0]["system_models"]
    assert "DL560 G9" in rows[1]["system_models"]


def test_extract_candidate_models_handles_common_patterns() -> None:
    models = extract_candidate_models("HP ProLiant DL560 G9 compatible with G8 G9 storage and MSA2 shelves")

    assert "DL560 G9" in models
    assert "G8 G9" in models
    assert "MSA2" in models


def test_inventory_model_priority_matrix_ranks_common_models(conn: sqlite3.Connection) -> None:
    for part_number, description, manufacturer in [
        ("HP-001", "ProLiant DL380p G8 Fan Module", "HPE"),
        ("HP-002", "ProLiant DL380p G8 System Board", "HPE"),
        ("HP-003", "ProLiant DL380p G8 Power Supply", "HPE"),
        ("DELL-001", "PowerEdge R640 System Board", "Dell"),
        ("DELL-002", "PowerEdge R640 PCIe Riser Board", "Dell"),
        ("DELL-003", "PowerEdge R640 Power Supply", "Dell"),
        ("DELL-004", "PowerEdge R640 Fan Module", "Dell"),
        ("DELL-005", "PowerEdge R720 Backplane", "Dell"),
        ("DELL-006", "PowerEdge R720 System Board", "Dell"),
    ]:
        upsert_part(
            conn,
            part_number=part_number,
            description=description,
            manufacturer=manufacturer,
            uom="EA",
        )

    matrix = inventory_model_priority_matrix(conn, limit=5)

    assert len(matrix) >= 3
    assert matrix[0]["brand"] == "DELL"
    assert matrix[0]["model"] == "R640"
    assert matrix[0]["inventory_mentions"] == 4
    assert matrix[0]["category_diversity"] >= 3

    model_names = [row["model"] for row in matrix[:3]]
    assert "DL380P G8" in model_names
    assert "R720" in model_names


def test_inventory_model_priority_matrix_filters_by_brand(conn: sqlite3.Connection) -> None:
    upsert_part(conn, part_number="HP-100", description="ProLiant DL360 G10 Fan Module", manufacturer="HPE", uom="EA")
    upsert_part(conn, part_number="DELL-100", description="PowerEdge R740 System Board", manufacturer="Dell", uom="EA")

    hp_rows = inventory_model_priority_matrix(conn, brand_filter="HP/HPE", limit=10)
    dell_rows = inventory_model_priority_matrix(conn, brand_filter="DELL", limit=10)

    assert hp_rows
    assert all(row["brand"] == "HP/HPE" for row in hp_rows)
    assert dell_rows
    assert all(row["brand"] == "DELL" for row in dell_rows)


def test_search_inventory_detected_models_finds_hp_model_mentions(conn: sqlite3.Connection) -> None:
    upsert_part(conn, part_number="HP-200", description="ProLiant DL360p G8 System Board", manufacturer="HPE", uom="EA")
    upsert_part(conn, part_number="HP-201", description="ProLiant DL360p G8 Fan Module", manufacturer="HPE", uom="EA")

    rows = search_inventory_detected_models(conn, "DL360P G8", limit=10)

    assert rows
    assert rows[0]["brand"] == "HP/HPE"
    assert rows[0]["model"] == "DL360P G8"
    assert rows[0]["inventory_mentions"] == 2
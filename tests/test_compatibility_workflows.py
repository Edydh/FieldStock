from __future__ import annotations

import sqlite3
from typing import Iterator

import pytest
import requests

from modules.db import SCHEMA_PATH, record_inventory_adjustment, upsert_location, upsert_part
from modules.reference_import import ReferenceHtmlAnalysis, analyze_reference_html, analyze_reference_pdf_pages, extract_candidate_models, extract_harddrivesdirect_listing_links, extract_harddrivesdirect_page_models, import_reference_html, import_reference_rows, import_reference_url, parse_harddrivesdirect_listing, parse_harddrivesdirect_product_page, parse_harddrivesdirect_search_results, parse_reference_pdf_pages, repair_compatibility_model_links
from modules.reference_search import compatibility_inventory_for_model, compatibility_source_summary, inventory_model_priority_matrix, search_inventory_detected_models, search_reference_parts, search_related_compatibility, search_system_models


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


def test_import_reference_rows_reuses_existing_source_for_same_url(conn: sqlite3.Connection) -> None:
    import_reference_rows(
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
        source_name="HardDrivesDirect HP",
        source_type="saved_html",
        source_url="https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php",
    )

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
        source_name="Renamed HP Source",
        source_type="saved_html",
        source_url="https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php",
    )

    source_count = conn.execute("SELECT COUNT(*) FROM reference_sources").fetchone()[0]
    part_count = conn.execute("SELECT COUNT(*) FROM reference_parts").fetchone()[0]
    compatibility_count = conn.execute("SELECT COUNT(*) FROM system_part_compatibility").fetchone()[0]
    source_name = conn.execute("SELECT source_name FROM reference_sources").fetchone()[0]

    assert result.parts_upserted == 0
    assert result.compatibilities_upserted == 0
    assert source_count == 1
    assert part_count == 1
    assert compatibility_count == 1
    assert source_name == "Renamed HP Source"


def test_compatibility_source_summary_groups_by_source(conn: sqlite3.Connection) -> None:
    import_reference_rows(
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
        source_name="HardDrivesDirect HP",
        source_type="saved_html",
        source_url="https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php",
    )

    rows = compatibility_source_summary(conn)

    assert rows
    assert rows[0]["source_name"] == "HardDrivesDirect HP"
    assert rows[0]["source_type"] == "saved_html"
    assert rows[0]["source_url"] == "https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php"
    assert int(rows[0]["reference_part_count"]) == 1
    assert int(rows[0]["system_model_count"]) == 1
    assert "HPE" in rows[0]["manufacturers"]


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


def test_search_reference_parts_matches_part_number_and_alias(conn: sqlite3.Connection) -> None:
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
        qty_change=2,
        created_by="tester",
        reference="seed-002",
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

    rows = search_reference_parts(conn, "872737001", available_only=True, limit=20)

    assert rows
    assert rows[0]["reference_part_number"] == "872737-001"
    assert rows[0]["local_part_number"] == "872737001"
    assert rows[0]["match_status"] == "Matched in inventory"
    assert int(rows[0]["compatible_model_count"]) == 1
    assert "872737001" in rows[0]["alias_part_numbers"]


def test_search_system_models_returns_browse_results_when_query_empty(conn: sqlite3.Connection) -> None:
    import_reference_rows(
        conn,
        rows=[
            {
                "part_number": "830287-B21",
                "description": "830287-B21 HP Intel Xeon E5-4655v4 2.5GHz DL560 G9",
                "manufacturer": "HPE",
                "system_models": ["DL560 G9"],
                "aliases": ["830287-B21", "830287B21"],
            }
        ],
        source_name="HardDrivesDirect",
        source_type="web_listing",
        source_url="https://example.test/hp",
    )

    rows = search_system_models(conn, "", limit=20)

    assert rows
    assert rows[0]["model_name"] == "DL560 G9"


def test_search_related_compatibility_expands_from_matched_part_to_related_model(conn: sqlite3.Connection) -> None:
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
        qty_change=3,
        created_by="tester",
        reference="seed-003",
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
            },
            {
                "part_number": "830287-B21",
                "description": "830287-B21 HP Intel Xeon E5-4655v4 2.5GHz G8 G9",
                "manufacturer": "HPE",
                "system_models": ["G8 G9"],
                "aliases": ["830287-B21", "830287B21"],
            },
        ],
        source_name="HardDrivesDirect",
        source_type="web_listing",
        source_url="https://example.test/hp",
    )

    rows = search_related_compatibility(conn, "872737001", available_only=False, limit=20)

    assert rows
    reference_part_numbers = {row["reference_part_number"] for row in rows}
    assert "872737-001" in reference_part_numbers
    assert "830287-B21" in reference_part_numbers
    assert any(row["relation_type"] == "Matched reference part" for row in rows)
    matched_row = next(row for row in rows if row["reference_part_number"] == "872737-001")
    assert "872737001" in matched_row["alias_part_numbers"]


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


def test_extract_harddrivesdirect_listing_links_finds_listing_pages() -> None:
        html = """
        <html>
                <body>
                        <a href="HTML_HP_SAS_SATA_1.php">HP page 1</a>
                        <a href="HTML_Dell_SAS_SATA_1.php">Dell page 1</a>
                        <a href="proliant_configuration.php">Configuration</a>
                </body>
        </html>
        """

        links = extract_harddrivesdirect_listing_links(html, "https://www.harddrivesdirect.com/proliant_configuration.php")

        assert links == [
                "https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php",
                "https://www.harddrivesdirect.com/HTML_Dell_SAS_SATA_1.php",
        ]


def test_analyze_reference_html_classifies_direct_listing() -> None:
        html = """
        <html>
            <head><title>HP SAS SATA Listing</title></head>
            <body>
                <table>
                    <tr><th>Product Name</th><th>Part#</th></tr>
                    <tr>
                        <td><a href="product_info.php?products_id=1">872737-001 HP G8 G9 1.2-TB 12G 10K 2.5 SAS SC</a></td>
                        <td>872737-001</td>
                    </tr>
                </table>
            </body>
        </html>
        """

        analysis = analyze_reference_html(html, "https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php")

        assert isinstance(analysis, ReferenceHtmlAnalysis)
        assert analysis.page_kind == "direct_listing"
        assert analysis.rows_detected == 1


def test_analyze_reference_html_classifies_configuration_page() -> None:
        html = """
        <html>
            <head><title>HP Proliant Options</title></head>
            <body>
                <a href="HTML_HP_SAS_SATA_1.php">HP page 1</a>
                <a href="HTML_Dell_SAS_SATA_1.php">Dell page 1</a>
            </body>
        </html>
        """

        analysis = analyze_reference_html(html, "https://www.harddrivesdirect.com/proliant_configuration.php")

        assert analysis.page_kind == "configuration_index"
        assert len(analysis.listing_links) == 2


def test_analyze_reference_html_classifies_model_category_page() -> None:
        html = """
        <html>
            <head><title>DL20 G9 Hard Drives</title></head>
            <body>
                <h1>Proliant DL20 G9 Hard Drives</h1>
                <p>Server options and hard drives for DL20 G9.</p>
            </body>
        </html>
        """

        analysis = analyze_reference_html(html, "https://www.harddrivesdirect.com/proliant_build_DL20_G9_Hard_Drives.php")

        assert analysis.page_kind == "model_category_unknown"
        assert "DL20 G9" in analysis.detected_models


def test_extract_harddrivesdirect_page_models_reads_page_level_compatibility() -> None:
        html = """
        <html>
            <body>
                <p>
                    These HP Solid State Drives are compatible with these G10+ / G11 / G12 Servers & Systems
                    HP Proliant DL Series: DL20 (G10+) DL360 (G11) DL380 (G12)
                    Specifically designed for HPE Proliant Servers with 2.5-inch Basic Carrier Drive Bays
                </p>
            </body>
        </html>
        """

        models = extract_harddrivesdirect_page_models(html)

        assert "DL20 G10" in models
        assert "DL360 G11" in models
        assert "DL380 G12" in models


def test_parse_harddrivesdirect_search_results_extracts_products_and_page_models() -> None:
        html = """
        <html>
            <head><title>HP 2.5 G10+ G11 SAS BC SSD Specials</title></head>
            <body>
                <p>
                    These HP Solid State Drives are compatible with these G10+ / G11 / G12 Servers & Systems
                    HP Proliant DL Series: DL20 (G10+) DL360 (G11) DL380 (G12)
                    Specifically designed for HPE Proliant Servers with 2.5-inch Basic Carrier Drive Bays
                </p>
                <div>
                    <a href="product_info.php?products_id=1">HP 2.5 G10+ / G11 Basic Carrier SSD Write-Intensive P40480-B21 400-GB</a>
                    <span>$395.95 New SSD Special Part# P40480-B21</span>
                </div>
                <div>
                    <a href="product_info.php?products_id=2">HP 2.5 G10+ / G11 Basic Carrier SSD Mixed-Use P40560-B21 800-GB</a>
                    <span>$525.95 New SSD Special Part# P40560-B21</span>
                </div>
            </body>
        </html>
        """

        rows = parse_harddrivesdirect_search_results(html, "https://www.harddrivesdirect.com/search.php")

        assert len(rows) == 2
        assert rows[0]["part_number"] == "P40480-B21"
        assert "DL20 G10" in rows[0]["system_models"]
        assert rows[1]["part_number"] == "P40560-B21"


def test_analyze_reference_html_classifies_search_results_listing() -> None:
        html = """
        <html>
            <head><title>HP 2.5 G10+ G11 SAS BC SSD Specials</title></head>
            <body>
                <p>
                    These HP Solid State Drives are compatible with these G10+ / G11 / G12 Servers & Systems
                    HP Proliant DL Series: DL20 (G10+) DL360 (G11) DL380 (G12)
                    Specifically designed for HPE Proliant Servers with 2.5-inch Basic Carrier Drive Bays
                </p>
                <div>
                    <a href="product_info.php?products_id=1">HP 2.5 G10+ / G11 Basic Carrier SSD Write-Intensive P40480-B21 400-GB</a>
                    <span>$395.95 New SSD Special Part# P40480-B21</span>
                </div>
            </body>
        </html>
        """

        analysis = analyze_reference_html(html, "https://www.harddrivesdirect.com/search.php")

        assert analysis.page_kind == "search_results_listing"
        assert analysis.rows_detected == 1


def test_import_reference_html_imports_search_results_listing(conn: sqlite3.Connection) -> None:
        html = """
        <html>
            <head><title>HP 2.5 G10+ G11 SAS BC SSD Specials</title></head>
            <body>
                <p>
                    These HP Solid State Drives are compatible with these G10+ / G11 / G12 Servers & Systems
                    HP Proliant DL Series: DL20 (G10+) DL360 (G11) DL380 (G12)
                    Specifically designed for HPE Proliant Servers with 2.5-inch Basic Carrier Drive Bays
                </p>
                <div>
                    <a href="product_info.php?products_id=1">HP 2.5 G10+ / G11 Basic Carrier SSD Write-Intensive P40480-B21 400-GB</a>
                    <span>$395.95 New SSD Special Part# P40480-B21</span>
                </div>
            </body>
        </html>
        """

        result = import_reference_html(
                conn,
                html=html,
                page_url="https://www.harddrivesdirect.com/SAS_2_SFF_G10_BC_ssd_search_hp.php",
                source_name="HP BC SSD Search",
        )

        models = search_system_models(conn, "DL20 G10")

        assert result.rows_seen == 1
        assert result.parts_upserted == 1
        assert result.compatibilities_upserted >= 1
        assert models


def test_parse_harddrivesdirect_product_page_extracts_aliases_and_attributes() -> None:
        html = """
        <html>
            <head><title>461137-B21 HP 1-TB 3G 7.2K 3.5 DP SAS</title></head>
            <body>
                <h1>461137-B21 HP 1-TB 3G 7.2K 3.5 DP SAS</h1>
                <p>
                    Description: HP 1TB 3.5-inch LFF SAS 3Gb/s 7.2K RPM Midline (MDL) Dual Port (DP) Hot-Plug Hard Drive
                    In HP 3.5-inch LFF SAS Hot-Plug Hard Drive tray (as pictured)
                    For HP G1-G7 Proliant SAS Servers and Storage Arrays
                    Genuine HP Serial number and Firmware
                </p>
                <p>
                    Part Number(s)
                    Option Part# 461137-B21
                    SmartBuy Part# 461137-S21
                    Spare Part# 461289-001
                    Assembly Part# 461134-003
                    Model# MB1000BAWJP
                </p>
                <p>
                    Specifications:
                    Category Proliant HardDrive
                    Sub-Category 7.2K
                    Generation SAS
                    Part Number 461137-B21
                    Products ID 455653
                    Capacity 1TB
                    Interface Serial Attached SCSI (SAS)
                    Drive Dimensions 3.5 inches x 1/3H (Low Profile)
                    Spindle Speed 7200RPM
                    External Data Transfer 3GB/s
                    Ports Dual Port
                </p>
            </body>
        </html>
        """

        row = parse_harddrivesdirect_product_page(html, "https://www.harddrivesdirect.com/product_info.php?products_id=455653")

        assert row is not None
        assert row["part_number"] == "461137-B21"
        assert "461137-S21" in row["aliases"]
        assert row["attributes"]["capacity"] == "1TB"
        assert row["attributes"]["ports"] == "DUAL PORT"
        assert row["attributes"]["model_number"] == "MB1000BAWJP"


def test_parse_harddrivesdirect_product_page_extracts_emc_aliases_and_models() -> None:
        html = """
        <html>
            <head><title>005049033 EMC 600-GB 4-GB 15K 3.5 FC HDD</title></head>
            <body>
                <h1>005049033 EMC 600-GB 4-GB 15K 3.5 FC HDD</h1>
                <p>
                    Description: Genuine EMC 600GB 15K 3.5-inch 4GB FibreChannel (FC) Hot-Plug Hard Drive
                    in 3.5-inch EMC Hot-Plug HDD Drive Tray (as pictured)
                    Genuine EMC serial number and firmware
                </p>
                <p>
                    Part Number(s)
                    EMC Part# 005048952
                    EMC Part# 005049033
                    EMC Part# 005049118
                    EMC Part# 005049160
                    EMC Part# 005049694
                    EMC Part# 005050919
                    EMC Part# 005050920
                    Model# CX-4G15-600
                </p>
                <p>
                    Specifications:
                    Category EMC HardDrive
                    Sub-Category 15K
                    Generation Fibre Channel
                    Part Number 005049033
                    Products ID 477385
                    Capacity 600GB
                    Form Factor 3.5 inches
                    Interface Fibre Channel
                    Rotational Speed 15000RPM
                    Manufacturer Dell EMC
                    Bytes/sector 520
                    Hot Swap Tray Included
                </p>
                <p>
                    005049033 Compatible Servers and Storage Arrays:
                    For the EMC CX4 Series Storage Systems
                </p>
                <table>
                    <tr><th>EMC VNX Hard Drives</th></tr>
                    <tr><td>EMC VNX 5100</td><td>EMC VNX 5200</td></tr>
                    <tr><td>EMC VNX 5300</td><td>EMC VNX 5400</td></tr>
                    <tr><th>EMC VNXe Hard Drives</th></tr>
                    <tr><td>EMC VNXe 3100</td><td>EMC VNXe 3150</td></tr>
                    <tr><th>EMC CLARiiON CX3 Hard Drives</th></tr>
                    <tr><td>EMC CX3 10</td><td>EMC CX3 20</td></tr>
                    <tr><th>EMC CLARiiON CX4 Hard Drives</th></tr>
                    <tr><td>EMC CX4 120</td><td>EMC CX4 240</td></tr>
                    <tr><td>EMC CX4 480</td><td>EMC CX4 960</td></tr>
                </table>
            </body>
        </html>
        """

        row = parse_harddrivesdirect_product_page(html, "https://www.harddrivesdirect.com/canada/product_info.php?products_id=477385_005049033")

        assert row is not None
        assert row["part_number"] == "005049033"
        assert set(row["aliases"]) == {
            "005048952",
            "005049033",
            "005049118",
            "005049160",
            "005049694",
            "005050919",
            "005050920",
        }
        assert row["manufacturer"] == "Dell EMC"
        assert row["attributes"]["capacity"] == "600GB"
        assert row["attributes"]["interface"] == "FIBRE CHANNEL"
        assert row["attributes"]["model_number"] == "CX-4G15-600"
        assert "VNX 5100" in row["system_models"]
        assert "CX4 960" in row["system_models"]


def test_import_reference_html_imports_emc_product_detail_aliases_and_compatibility(conn: sqlite3.Connection) -> None:
        html = """
        <html>
            <head><title>005049033 EMC 600-GB 4-GB 15K 3.5 FC HDD</title></head>
            <body>
                <h1>005049033 EMC 600-GB 4-GB 15K 3.5 FC HDD</h1>
                <p>
                    Description: Genuine EMC 600GB 15K 3.5-inch 4GB FibreChannel (FC) Hot-Plug Hard Drive
                    in 3.5-inch EMC Hot-Plug HDD Drive Tray (as pictured)
                </p>
                <p>
                    Part Number(s)
                    EMC Part# 005048952
                    EMC Part# 005049033
                    EMC Part# 005049118
                    EMC Part# 005049160
                    EMC Part# 005049694
                    EMC Part# 005050919
                    EMC Part# 005050920
                    Model# CX-4G15-600
                </p>
                <p>
                    Specifications:
                    Category EMC HardDrive
                    Sub-Category 15K
                    Generation Fibre Channel
                    Part Number 005049033
                    Products ID 477385
                    Capacity 600GB
                    Interface Fibre Channel
                    Rotational Speed 15000RPM
                    Manufacturer Dell EMC
                    Bytes/sector 520
                    Hot Swap Tray Included
                </p>
                <table>
                    <tr><td>EMC VNX 5100</td><td>EMC VNX 5200</td></tr>
                    <tr><td>EMC VNXe 3100</td><td>EMC VNXe 3150</td></tr>
                    <tr><td>EMC CX3 10</td><td>EMC CX3 20</td></tr>
                    <tr><td>EMC CX4 120</td><td>EMC CX4 240</td></tr>
                    <tr><td>EMC CX4 480</td><td>EMC CX4 960</td></tr>
                </table>
            </body>
        </html>
        """

        result = import_reference_html(
                conn,
                html=html,
                page_url="https://www.harddrivesdirect.com/canada/product_info.php?products_id=477385_005049033",
                source_name="EMC FC HDD Product Detail",
        )

        alias_rows = conn.execute(
            "SELECT alias_part_number FROM reference_part_aliases ORDER BY alias_part_number"
        ).fetchall()
        models = search_system_models(conn, "VNX 5100")
        rows = search_reference_parts(conn, "005050919", available_only=False, limit=20)

        assert result.rows_seen == 1
        assert result.parts_upserted == 1
        assert result.aliases_upserted == 7
        assert result.compatibilities_upserted >= 5
        assert [row[0] for row in alias_rows] == [
            "005048952",
            "005049033",
            "005049118",
            "005049160",
            "005049694",
            "005050919",
            "005050920",
        ]
        assert models
        assert rows
        assert rows[0]["reference_part_number"] == "005049033"
        assert rows[0]["reference_manufacturer"] == "Dell EMC"
        assert int(rows[0]["compatible_model_count"]) >= 5


def test_analyze_reference_html_prefers_product_detail_over_listing_shape() -> None:
        html = """
        <html>
            <head><title>005049033 EMC 600-GB 4-GB 15K 3.5 FC HDD</title></head>
            <body>
                <p>
                    Description: Genuine EMC 600GB 15K 3.5-inch 4GB Fibre Channel (FC) Hot-Plug Hard Drive
                </p>
                <p>
                    Part Number(s)
                    EMC Part# 005048952
                    EMC Part# 005049033
                    EMC Part# 005049118
                </p>
                <p>
                    Specifications:
                    Part Number 005049033
                    Capacity 600GB
                    Interface Fibre Channel
                    Manufacturer Dell EMC
                </p>
                <table>
                    <tr><td>005049033 EMC 600-GB 4-GB 15K 3.5 FC HDD</td><td>005049033</td></tr>
                </table>
            </body>
        </html>
        """

        analysis = analyze_reference_html(html, "https://www.harddrivesdirect.com/canada/product_info.php?products_id=477385_005049033")

        assert analysis.page_kind == "product_detail"
        assert analysis.rows_detected == 1


def test_analyze_reference_html_classifies_product_detail_page() -> None:
        html = """
        <html>
            <head><title>461137-B21 HP 1-TB 3G 7.2K 3.5 DP SAS</title></head>
            <body>
                <p>Part Number(s) Option Part# 461137-B21 SmartBuy Part# 461137-S21</p>
                <p>Specifications: Capacity 1TB Interface Serial Attached SCSI (SAS)</p>
            </body>
        </html>
        """

        analysis = analyze_reference_html(html, "https://www.harddrivesdirect.com/product_info.php?products_id=455653")

        assert analysis.page_kind == "product_detail"
        assert analysis.rows_detected == 1


def test_import_reference_html_imports_product_detail_attributes(conn: sqlite3.Connection) -> None:
        html = """
        <html>
            <head><title>461137-B21 HP 1-TB 3G 7.2K 3.5 DP SAS</title></head>
            <body>
                <h1>461137-B21 HP 1-TB 3G 7.2K 3.5 DP SAS</h1>
                <p>
                    Description: HP 1TB 3.5-inch LFF SAS 3Gb/s 7.2K RPM Midline (MDL) Dual Port (DP) Hot-Plug Hard Drive
                    For HP G1-G7 Proliant SAS Servers and Storage Arrays
                </p>
                <p>
                    Part Number(s)
                    Option Part# 461137-B21
                    SmartBuy Part# 461137-S21
                    Spare Part# 461289-001
                    Assembly Part# 461134-003
                    Model# MB1000BAWJP
                </p>
                <p>
                    Specifications:
                    Category Proliant HardDrive
                    Capacity 1TB
                    Interface Serial Attached SCSI (SAS)
                    Ports Dual Port
                </p>
            </body>
        </html>
        """

        result = import_reference_html(
                conn,
                html=html,
                page_url="https://www.harddrivesdirect.com/product_info.php?products_id=455653",
                source_name="461137 Product Page",
        )

        alias_rows = conn.execute("SELECT alias_part_number FROM reference_part_aliases ORDER BY alias_part_number").fetchall()
        attribute_rows = conn.execute(
                "SELECT attribute_name, attribute_value FROM reference_part_attributes ORDER BY attribute_name"
        ).fetchall()

        assert result.rows_seen == 1
        assert any(row[0] == "461137-S21" for row in alias_rows)
        assert any(row[0] == "capacity" and row[1] == "1TB" for row in attribute_rows)
        assert any(row[0] == "ports" and row[1] == "DUAL PORT" for row in attribute_rows)


def test_parse_reference_pdf_pages_extracts_part_rows_and_attributes() -> None:
    page_texts = [
        """
        HP ProLiant DL180 Generation 5
        Storage Options
        507127-B21 HP 500GB 7.2K LFF SATA HDD OPTION KIT FOR DL180 G5
        458928-001 SPARE HDD CARRIER FOR DL180 G5
        """
    ]

    rows = parse_reference_pdf_pages(page_texts, "DL180-G5-manual.pdf", document_title="HP ProLiant DL180 G5 Manual")

    assert len(rows) == 2
    assert rows[0]["part_number"] == "507127-B21"
    assert "DL180 G5" in rows[0]["system_models"]
    assert rows[0]["attributes"]["document_type"] == "pdf_manual"
    assert rows[0]["attributes"]["source_page"] == "1"
    assert rows[0]["attributes"]["document_section"] == "STORAGE OPTIONS"


def test_analyze_reference_pdf_pages_classifies_text_pdf_with_parts() -> None:
    page_texts = [
        """
        HP ProLiant DL180 Generation 5
        Storage Options
        507127-B21 HP 500GB 7.2K LFF SATA HDD OPTION KIT FOR DL180 G5
        """
    ]

    analysis = analyze_reference_pdf_pages(page_texts, "DL180-G5-manual.pdf", document_title="HP ProLiant DL180 G5 Manual")

    assert analysis.page_kind == "text_pdf_manual"
    assert analysis.page_count == 1
    assert analysis.rows_detected == 1
    assert "DL180 G5" in analysis.detected_models
    assert "507127-B21" in analysis.detected_part_numbers


def test_analyze_reference_pdf_pages_classifies_text_pdf_without_parts() -> None:
    page_texts = [
        """
        HP ProLiant DL180 Generation 5
        Specifications
        Supports twelve large form factor SATA drives and redundant power supplies.
        """
    ]

    analysis = analyze_reference_pdf_pages(page_texts, "DL180-G5-manual.pdf", document_title="HP ProLiant DL180 G5 Manual")

    assert analysis.page_kind == "text_pdf_no_parts"
    assert analysis.rows_detected == 0
    assert "DL180 G5" in analysis.detected_models


def test_parse_reference_pdf_pages_prefers_adjacent_label_over_note_text() -> None:
    page_texts = [
        """
        Optional Upgrades
        512 MB Battery-backed write cache upgrade
        405148-B21
        Battery-backed write cache upgrade
        QuickSpecs
        """
    ]

    rows = parse_reference_pdf_pages(page_texts, "DL180-G5-manual.pdf", document_title="HP ProLiant DL180 G5 Manual")

    assert len(rows) == 1
    assert rows[0]["part_number"] == "405148-B21"
    assert rows[0]["description"] == "512 MB BATTERY-BACKED WRITE CACHE UPGRADE"


def test_parse_reference_pdf_pages_extracts_inline_description_without_blank_result() -> None:
    page_texts = [
        """
        NOTE:
        The addition of a Smart Array Controller requires the addition of a
        SAS/SATA Multi-lane cable (464830-B21)
        ONLY.
        """
    ]

    rows = parse_reference_pdf_pages(page_texts, "DL180-G5-manual.pdf", document_title="HP ProLiant DL180 G5 Manual")

    assert len(rows) == 1
    assert rows[0]["part_number"] == "464830-B21"
    assert "SAS/SATA MULTI-LANE CABLE" in rows[0]["description"]


def test_import_reference_html_imports_saved_listing(conn: sqlite3.Connection) -> None:
    html = """
    <html>
        <body>
            <table>
                <tr><th>Product Name</th><th>Part#</th></tr>
                <tr>
                    <td><a href="product_info.php?products_id=1">872737-001 HP G8 G9 1.2-TB 12G 10K 2.5 SAS SC</a></td>
                    <td>872737-001</td>
                </tr>
            </table>
        </body>
    </html>
    """

    result = import_reference_html(
        conn,
        html=html,
        page_url="https://example.test/hp",
        source_name="Saved Page",
    )

    models = search_system_models(conn, "G8 G9")

    assert result.rows_seen == 1
    assert result.parts_upserted == 1
    assert result.compatibilities_upserted == 1
    assert len(models) == 1


def test_import_reference_html_rejects_configuration_page_with_listing_hints(conn: sqlite3.Connection) -> None:
    html = """
    <html>
      <body>
        <a href="HTML_HP_SAS_SATA_1.php">HP page 1</a>
        <a href="HTML_Dell_SAS_SATA_1.php">Dell page 1</a>
      </body>
    </html>
    """

    with pytest.raises(RuntimeError, match="configuration or index page"):
        import_reference_html(
            conn,
            html=html,
            page_url="https://www.harddrivesdirect.com/proliant_configuration.php",
            source_name="Saved Config Page",
        )


def test_import_reference_url_returns_actionable_error_on_403(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(*args: object, **kwargs: object) -> requests.Response:
        response = requests.Response()
        response.status_code = 403
        response.url = "https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php"
        raise requests.HTTPError(response=response)

    monkeypatch.setattr("modules.reference_import.requests.get", fake_get)

    with pytest.raises(RuntimeError, match="save it as an HTML file"):
        import_reference_url(conn, "https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php")


def test_extract_candidate_models_handles_common_patterns() -> None:
    models = extract_candidate_models("HP ProLiant DL560 G9 compatible with G8 G9 storage and MSA2 shelves")

    assert "DL560 G9" in models
    assert "G8 G9" in models
    assert "MSA2" in models


def test_extract_candidate_models_normalizes_gen_shorthand() -> None:
    models = extract_candidate_models("ProLiant DL380p Gen8 System Board")

    assert "DL380P G8" in models


def test_repair_compatibility_model_links_backfills_generation_specific_models(conn: sqlite3.Connection) -> None:
    import_reference_rows(
        conn,
        rows=[
            {
                "part_number": "14900L-13",
                "description": "HP 32GB memory module",
                "manufacturer": "HPE",
                "system_models": ["PROLIANT DL380P"],
                "source_title": "HPE ProLiant Gen8 Servers - Part Number Memory Matrix",
            }
        ],
        source_name="PDF Manual",
        source_type="saved_pdf",
        source_url="https://example.test/manual.pdf#page=6",
    )

    before_rows = search_related_compatibility(conn, "DL380P G8", available_only=False, limit=20)
    repair_result = repair_compatibility_model_links(conn)
    after_rows = search_related_compatibility(conn, "DL380P G8", available_only=False, limit=20)
    repaired_models = search_system_models(conn, "DL380P G8", limit=20)

    assert before_rows == []
    assert repair_result.models_upserted >= 1
    assert repair_result.compatibilities_upserted >= 1
    assert after_rows
    assert any(row["model_name"] == "DL380P G8" for row in repaired_models)


def test_repair_compatibility_model_links_is_idempotent(conn: sqlite3.Connection) -> None:
    import_reference_rows(
        conn,
        rows=[
            {
                "part_number": "14900L-13",
                "description": "HP 32GB memory module",
                "manufacturer": "HPE",
                "system_models": ["PROLIANT DL380P"],
                "source_title": "HPE ProLiant Gen8 Servers - Part Number Memory Matrix",
            }
        ],
        source_name="PDF Manual",
        source_type="saved_pdf",
        source_url="https://example.test/manual.pdf#page=6",
    )

    first_result = repair_compatibility_model_links(conn)
    second_result = repair_compatibility_model_links(conn)

    assert first_result.compatibilities_upserted >= 1
    assert second_result.models_upserted == 0
    assert second_result.compatibilities_upserted == 0


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
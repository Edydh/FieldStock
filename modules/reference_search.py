from __future__ import annotations

from collections import Counter, defaultdict
import re
import sqlite3

from modules.utils import normalize_model_name, normalize_part_number


HP_MODEL_PATTERN = re.compile(r"\b(?:DL|ML|BL|SL)\s*\d{2,4}[A-Z]{0,3}\s*G\d+\b", re.I)
HP_STORAGE_PATTERN = re.compile(r"\bMSA\d+\b", re.I)
DELL_POWEREDGE_PATTERN = re.compile(r"\bPOWEREDGE\s+([RTM]\d{3,4}(?:XD|XS|II)?)\b", re.I)
DELL_POWERVAULT_PATTERN = re.compile(r"\bPOWERVAULT\s+(MD\d{3,4})\b", re.I)
DELL_SC_PATTERN = re.compile(r"\b(DELL\s+EMC\s+SC\d{3,4})\b", re.I)

PART_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("system_board", re.compile(r"\bSYSTEM BOARD\b|\bMOTHERBOARD\b", re.I)),
    ("backplane", re.compile(r"\bBACKPLANE\b", re.I)),
    ("controller", re.compile(r"\bCONTROLLER\b|\bPERC\b|\bSMART ARRAY\b|\bRAID\b", re.I)),
    ("fan", re.compile(r"\bFAN\b", re.I)),
    ("power", re.compile(r"\bPOWER SUPPLY\b|\bPSU\b|\bPOWER DISTRIBUTION\b", re.I)),
    ("riser", re.compile(r"\bRISER\b", re.I)),
    ("drive_bay", re.compile(r"\bCAGE\b|\bTRAY\b|\bCADDY\b|\bSFF\b|\bLFF\b", re.I)),
    ("processor", re.compile(r"\bCPU\b|\bXEON\b|\bOPTERON\b", re.I)),
    ("memory", re.compile(r"\bDIMM\b|\bMEMORY\b|\bRAM\b|\bSDRAM\b", re.I)),
]


def _extract_inventory_models(description: str, manufacturer: str) -> list[tuple[str, str]]:
    text = description.upper().strip()
    manufacturer_text = manufacturer.upper().strip()
    matches: list[tuple[str, str]] = []

    hp_context = any(token in text for token in ("HP", "HPE", "PROLIANT")) or any(
        token in manufacturer_text for token in ("HP", "HPE")
    )
    if hp_context:
        for pattern in (HP_MODEL_PATTERN, HP_STORAGE_PATTERN):
            for match in pattern.finditer(text):
                model = re.sub(r"\s+", " ", match.group(0).upper()).strip()
                matches.append(("HP/HPE", model))

    dell_context = any(token in text for token in ("DELL", "POWEREDGE", "POWERVAULT", "DELL EMC")) or "DELL" in manufacturer_text
    if dell_context:
        for pattern in (DELL_POWEREDGE_PATTERN, DELL_POWERVAULT_PATTERN, DELL_SC_PATTERN):
            for match in pattern.finditer(text):
                model = re.sub(r"\s+", " ", match.group(1).upper()).strip()
                matches.append(("DELL", model))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in matches:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _model_family(brand: str, model: str) -> str:
    cleaned = model.upper().strip()
    if brand == "HP/HPE":
        match = re.match(r"([A-Z]+\d{2,4})", cleaned)
        if match:
            return match.group(1)
        return cleaned

    if cleaned.startswith("DELL EMC SC"):
        return cleaned.replace("DELL EMC ", "")

    return cleaned


def _longevity_score(brand: str, model: str) -> int:
    cleaned = model.upper().strip()
    if brand == "HP/HPE":
        generation_match = re.search(r"\bG(\d+)\b", cleaned)
        if generation_match:
            generation = int(generation_match.group(1))
            if generation >= 10:
                return 5
            if generation == 9:
                return 4
            if generation == 8:
                return 3
            return 2
        if cleaned.startswith("MSA"):
            return 4
        return 3

    series_match = re.match(r"([RT])([0-9]{3,4})", cleaned)
    if series_match:
        generation = int(series_match.group(2)[1]) if len(series_match.group(2)) >= 2 else 0
        if generation >= 4:
            return 5
        if generation == 3:
            return 4
        if generation == 2:
            return 3
        return 2
    if cleaned.startswith("MD") or cleaned.startswith("SC"):
        return 4
    return 3


def _part_categories(description: str) -> set[str]:
    categories = {
        category_name
        for category_name, pattern in PART_CATEGORY_PATTERNS
        if pattern.search(description)
    }
    return categories or {"other"}


def inventory_model_priority_matrix(
    conn: sqlite3.Connection,
    brand_filter: str = "",
    limit: int = 20,
) -> list[dict[str, object]]:
    rows = conn.execute("SELECT part_number, description, manufacturer FROM parts ORDER BY part_number ASC").fetchall()

    model_counts: Counter[tuple[str, str]] = Counter()
    model_parts: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    model_categories: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    examples: defaultdict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    family_counts: Counter[tuple[str, str]] = Counter()

    for row in rows:
        part_number = str(row["part_number"] or "").strip()
        description = str(row["description"] or "").strip()
        manufacturer = str(row["manufacturer"] or "").strip()
        extracted_models = _extract_inventory_models(description, manufacturer)
        categories = _part_categories(description)

        for brand, model in extracted_models:
            key = (brand, model)
            family_key = (brand, _model_family(brand, model))
            model_counts[key] += 1
            family_counts[family_key] += 1
            model_parts[key].add(part_number)
            model_categories[key].update(categories)
            if len(examples[key]) < 3:
                examples[key].append({"part_number": part_number, "description": description})

    matrix: list[dict[str, object]] = []
    normalized_brand_filter = brand_filter.strip().upper()
    for (brand, model), mention_count in model_counts.items():
        if normalized_brand_filter and brand.upper() != normalized_brand_filter:
            continue

        family = _model_family(brand, model)
        family_coverage = family_counts[(brand, family)]
        distinct_part_count = len(model_parts[(brand, model)])
        category_diversity = len(model_categories[(brand, model)])
        longevity = _longevity_score(brand, model)
        priority_score = (mention_count * 3) + distinct_part_count + (category_diversity * 2) + family_coverage + (longevity * 3)

        matrix.append(
            {
                "brand": brand,
                "model": model,
                "family": family,
                "inventory_mentions": mention_count,
                "distinct_parts": distinct_part_count,
                "category_diversity": category_diversity,
                "family_coverage": family_coverage,
                "longevity_score": longevity,
                "priority_score": priority_score,
                "examples": examples[(brand, model)],
            }
        )

    matrix.sort(
        key=lambda row: (
            -int(row["priority_score"]),
            -int(row["inventory_mentions"]),
            -int(row["distinct_parts"]),
            str(row["brand"]),
            str(row["model"]),
        )
    )
    return matrix[:limit]


def search_inventory_detected_models(
    conn: sqlite3.Connection,
    query: str,
    brand_filter: str = "",
    limit: int = 50,
) -> list[dict[str, object]]:
    cleaned = query.strip()
    normalized_query = normalize_model_name(cleaned)
    candidates = inventory_model_priority_matrix(conn, brand_filter=brand_filter, limit=1000)
    if not cleaned:
        return candidates[:limit]

    matched: list[dict[str, object]] = []
    for candidate in candidates:
        model = str(candidate["model"])
        family = str(candidate["family"])
        if (
            cleaned.upper() in model.upper()
            or cleaned.upper() in family.upper()
            or normalized_query in normalize_model_name(model)
            or normalized_query in normalize_model_name(family)
        ):
            matched.append(candidate)
    return matched[:limit]


def search_system_models(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 50,
) -> list[sqlite3.Row]:
    cleaned = query.strip()
    normalized = f"%{normalize_model_name(cleaned)}%"
    wildcard = f"%{cleaned.upper()}%"
    return conn.execute(
        """
        SELECT
            sm.id,
            sm.manufacturer,
            sm.model_name,
            sm.model_family,
            COUNT(DISTINCT spc.reference_part_id) AS compatible_part_count
        FROM system_models sm
        LEFT JOIN system_part_compatibility spc ON spc.system_model_id = sm.id
        WHERE (? = '' OR sm.model_name LIKE ? OR sm.normalized_model_name LIKE ?)
        GROUP BY sm.id, sm.manufacturer, sm.model_name, sm.model_family
        ORDER BY
            CASE WHEN sm.normalized_model_name = ? THEN 0 ELSE 1 END,
            compatible_part_count DESC,
            sm.model_name ASC
        LIMIT ?
        """,
        (cleaned, wildcard, normalized, normalize_model_name(cleaned), limit),
    ).fetchall()


def search_reference_parts(
    conn: sqlite3.Connection,
    query: str,
    available_only: bool = False,
    limit: int = 200,
) -> list[sqlite3.Row]:
    cleaned = query.strip()
    wildcard = f"%{cleaned.upper()}%"
    normalized_part = normalize_part_number(cleaned)
    available_sql = "HAVING COALESCE(local_inventory.total_qty_on_hand, 0) > 0" if available_only else ""

    return conn.execute(
        f"""
        WITH model_counts AS (
            SELECT
                spc.reference_part_id,
                COUNT(DISTINCT spc.system_model_id) AS compatible_model_count,
                GROUP_CONCAT(DISTINCT sm.model_name) AS compatible_models
            FROM system_part_compatibility spc
            INNER JOIN system_models sm ON sm.id = spc.system_model_id
            GROUP BY spc.reference_part_id
        ),
        local_inventory AS (
            SELECT
                p.normalized_part_number,
                p.part_number AS local_part_number,
                p.description AS local_description,
                p.manufacturer AS local_oem,
                SUM(ib.qty_on_hand) AS total_qty_on_hand,
                GROUP_CONCAT(
                    l.warehouse_code || '/' || l.location_code || ' (' || printf('%.2f', ib.qty_on_hand) || ')',
                    '; '
                ) AS stock_locations
            FROM parts p
            INNER JOIN inventory_balances ib ON ib.part_id = p.id
            INNER JOIN locations l ON l.id = ib.location_id
            GROUP BY p.normalized_part_number, p.part_number, p.description, p.manufacturer
        )
        SELECT
            rp.id,
            rp.part_number AS reference_part_number,
            rp.description AS reference_description,
            rp.manufacturer AS reference_manufacturer,
            rs.source_name,
            rp.product_url,
            COALESCE(mc.compatible_model_count, 0) AS compatible_model_count,
            COALESCE(mc.compatible_models, '') AS compatible_models,
            local_inventory.local_part_number,
            local_inventory.local_description,
            local_inventory.local_oem,
            COALESCE(local_inventory.total_qty_on_hand, 0) AS qty_on_hand,
            local_inventory.stock_locations,
            CASE WHEN local_inventory.local_part_number IS NULL THEN 'No local match' ELSE 'Matched in inventory' END AS match_status
        FROM reference_parts rp
        INNER JOIN reference_sources rs ON rs.id = rp.source_id
        LEFT JOIN model_counts mc ON mc.reference_part_id = rp.id
        LEFT JOIN reference_part_aliases rpa ON rpa.reference_part_id = rp.id
        LEFT JOIN local_inventory
            ON local_inventory.normalized_part_number = rp.normalized_part_number
            OR local_inventory.normalized_part_number = rpa.normalized_alias_part_number
        WHERE (
            ? = ''
            OR UPPER(rp.part_number) LIKE ?
            OR UPPER(COALESCE(rp.description, '')) LIKE ?
            OR UPPER(COALESCE(rp.manufacturer, '')) LIKE ?
            OR UPPER(COALESCE(rpa.alias_part_number, '')) LIKE ?
            OR REPLACE(UPPER(rp.part_number), '-', '') LIKE '%' || ? || '%'
            OR REPLACE(UPPER(COALESCE(rpa.alias_part_number, '')), '-', '') LIKE '%' || ? || '%'
        )
        GROUP BY
            rp.id,
            rp.part_number,
            rp.description,
            rp.manufacturer,
            rs.source_name,
            rp.product_url,
            mc.compatible_model_count,
            mc.compatible_models,
            local_inventory.local_part_number,
            local_inventory.local_description,
            local_inventory.local_oem,
            local_inventory.total_qty_on_hand,
            local_inventory.stock_locations
        {available_sql}
        ORDER BY
            CASE WHEN UPPER(rp.part_number) = UPPER(?) THEN 0 ELSE 1 END,
            qty_on_hand DESC,
            compatible_model_count DESC,
            rp.part_number ASC
        LIMIT ?
        """,
        (cleaned, wildcard, wildcard, wildcard, wildcard, normalized_part, normalized_part, cleaned, limit),
    ).fetchall()


def search_related_compatibility(
    conn: sqlite3.Connection,
    query: str,
    available_only: bool = True,
    limit: int = 500,
) -> list[sqlite3.Row]:
    cleaned = query.strip()
    if not cleaned:
        return []

    wildcard = f"%{cleaned.upper()}%"
    normalized_model = f"%{normalize_model_name(cleaned)}%"
    normalized_part = normalize_part_number(cleaned)
    available_sql = "AND COALESCE(local_inventory.total_qty_on_hand, 0) > 0" if available_only else ""

    return conn.execute(
        f"""
        WITH matched_models AS (
            SELECT sm.id
            FROM system_models sm
            WHERE sm.model_name LIKE ? OR sm.normalized_model_name LIKE ?
        ),
        matched_parts AS (
            SELECT DISTINCT rp.id
            FROM reference_parts rp
            LEFT JOIN reference_part_aliases rpa ON rpa.reference_part_id = rp.id
            WHERE (
                UPPER(rp.part_number) LIKE ?
                OR UPPER(COALESCE(rp.description, '')) LIKE ?
                OR UPPER(COALESCE(rp.manufacturer, '')) LIKE ?
                OR UPPER(COALESCE(rpa.alias_part_number, '')) LIKE ?
                OR REPLACE(UPPER(rp.part_number), '-', '') LIKE '%' || ? || '%'
                OR REPLACE(UPPER(COALESCE(rpa.alias_part_number, '')), '-', '') LIKE '%' || ? || '%'
            )
        ),
        selected_models AS (
            SELECT id FROM matched_models
            UNION
            SELECT DISTINCT spc.system_model_id
            FROM system_part_compatibility spc
            INNER JOIN matched_parts mp ON mp.id = spc.reference_part_id
        ),
        reference_candidates AS (
            SELECT
                rp.id AS reference_part_id,
                rp.normalized_part_number AS normalized_value
            FROM reference_parts rp
            UNION
            SELECT
                rpa.reference_part_id,
                rpa.normalized_alias_part_number AS normalized_value
            FROM reference_part_aliases rpa
        ),
        local_inventory AS (
            SELECT
                p.normalized_part_number,
                p.part_number AS local_part_number,
                p.description AS local_description,
                p.manufacturer AS local_oem,
                SUM(ib.qty_on_hand) AS total_qty_on_hand,
                GROUP_CONCAT(
                    l.warehouse_code || '/' || l.location_code || ' (' || printf('%.2f', ib.qty_on_hand) || ')',
                    '; '
                ) AS stock_locations
            FROM parts p
            INNER JOIN inventory_balances ib ON ib.part_id = p.id
            INNER JOIN locations l ON l.id = ib.location_id
            GROUP BY p.normalized_part_number, p.part_number, p.description, p.manufacturer
        )
        SELECT
            sm.model_name,
            sm.manufacturer AS model_manufacturer,
            rp.part_number AS reference_part_number,
            rp.description AS reference_description,
            rp.manufacturer AS reference_manufacturer,
            rs.source_name,
            spc.source_url,
            spc.confidence,
            local_inventory.local_part_number,
            local_inventory.local_description,
            local_inventory.local_oem,
            COALESCE(local_inventory.total_qty_on_hand, 0) AS qty_on_hand,
            local_inventory.stock_locations,
            CASE WHEN local_inventory.local_part_number IS NULL THEN 'No local match' ELSE 'Matched in inventory' END AS match_status,
            CASE
                WHEN sm.id IN (SELECT id FROM matched_models) AND rp.id IN (SELECT id FROM matched_parts) THEN 'Matched model and part'
                WHEN sm.id IN (SELECT id FROM matched_models) THEN 'Matched system model'
                WHEN rp.id IN (SELECT id FROM matched_parts) THEN 'Matched reference part'
                ELSE 'Related compatibility'
            END AS relation_type
        FROM system_part_compatibility spc
        INNER JOIN selected_models selected ON selected.id = spc.system_model_id
        INNER JOIN system_models sm ON sm.id = spc.system_model_id
        INNER JOIN reference_parts rp ON rp.id = spc.reference_part_id
        INNER JOIN reference_sources rs ON rs.id = spc.source_id
        LEFT JOIN reference_candidates rc ON rc.reference_part_id = rp.id
        LEFT JOIN local_inventory ON local_inventory.normalized_part_number = rc.normalized_value
        WHERE 1 = 1
        {available_sql}
        GROUP BY
            sm.id,
            sm.model_name,
            sm.manufacturer,
            rp.id,
            rp.part_number,
            rp.description,
            rp.manufacturer,
            rs.source_name,
            spc.source_url,
            spc.confidence,
            local_inventory.local_part_number,
            local_inventory.local_description,
            local_inventory.local_oem,
            local_inventory.total_qty_on_hand,
            local_inventory.stock_locations
        ORDER BY
            CASE relation_type
                WHEN 'Matched model and part' THEN 0
                WHEN 'Matched system model' THEN 1
                WHEN 'Matched reference part' THEN 2
                ELSE 3
            END,
            qty_on_hand DESC,
            sm.model_name ASC,
            rp.part_number ASC
        LIMIT ?
        """,
        (wildcard, normalized_model, wildcard, wildcard, wildcard, wildcard, normalized_part, normalized_part, limit),
    ).fetchall()


def compatibility_inventory_for_model(
    conn: sqlite3.Connection,
    system_model_id: int,
    available_only: bool = True,
    limit: int = 500,
) -> list[sqlite3.Row]:
    available_sql = "AND COALESCE(local_inventory.total_qty_on_hand, 0) > 0" if available_only else ""
    return conn.execute(
        f"""
        WITH reference_candidates AS (
            SELECT
                rp.id AS reference_part_id,
                rp.normalized_part_number AS normalized_value
            FROM reference_parts rp
            UNION
            SELECT
                rpa.reference_part_id,
                rpa.normalized_alias_part_number AS normalized_value
            FROM reference_part_aliases rpa
        ),
        local_inventory AS (
            SELECT
                p.normalized_part_number,
                p.part_number AS local_part_number,
                p.description AS local_description,
                p.manufacturer AS local_oem,
                SUM(ib.qty_on_hand) AS total_qty_on_hand,
                GROUP_CONCAT(
                    l.warehouse_code || '/' || l.location_code || ' (' || printf('%.2f', ib.qty_on_hand) || ')',
                    '; '
                ) AS stock_locations
            FROM parts p
            INNER JOIN inventory_balances ib ON ib.part_id = p.id
            INNER JOIN locations l ON l.id = ib.location_id
            GROUP BY p.normalized_part_number, p.part_number, p.description, p.manufacturer
        )
        SELECT
            sm.model_name,
            rp.part_number AS reference_part_number,
            rp.description AS reference_description,
            rp.manufacturer AS reference_manufacturer,
            rs.source_name,
            spc.source_url,
            spc.confidence,
            local_inventory.local_part_number,
            local_inventory.local_description,
            local_inventory.local_oem,
            COALESCE(local_inventory.total_qty_on_hand, 0) AS qty_on_hand,
            local_inventory.stock_locations,
            CASE WHEN local_inventory.local_part_number IS NULL THEN 'No local match' ELSE 'Matched in inventory' END AS match_status
        FROM system_part_compatibility spc
        INNER JOIN system_models sm ON sm.id = spc.system_model_id
        INNER JOIN reference_parts rp ON rp.id = spc.reference_part_id
        INNER JOIN reference_sources rs ON rs.id = spc.source_id
        LEFT JOIN reference_candidates rc ON rc.reference_part_id = rp.id
        LEFT JOIN local_inventory ON local_inventory.normalized_part_number = rc.normalized_value
        WHERE sm.id = ?
        {available_sql}
        GROUP BY
            sm.model_name,
            rp.id,
            rp.part_number,
            rp.description,
            rp.manufacturer,
            rs.source_name,
            spc.source_url,
            spc.confidence,
            local_inventory.local_part_number,
            local_inventory.local_description,
            local_inventory.local_oem,
            local_inventory.total_qty_on_hand,
            local_inventory.stock_locations
        ORDER BY qty_on_hand DESC, rp.part_number ASC
        LIMIT ?
        """,
        (system_model_id, limit),
    ).fetchall()


def compatibility_reference_summary(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM system_models) AS system_models,
            (SELECT COUNT(*) FROM reference_parts) AS reference_parts,
            (SELECT COUNT(*) FROM system_part_compatibility) AS compatibilities,
            (SELECT COUNT(*) FROM reference_sources) AS sources
        """
    ).fetchone()
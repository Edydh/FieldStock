from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
import re
import sqlite3
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from modules.utils import normalize_model_name, normalize_part_number, normalize_text


DEFAULT_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.harddrivesdirect.com/",
}


MODEL_PATTERNS = [
    re.compile(r"\b(?:DL|ML|BL|SL)\s*\d+[A-Z-]*\s*G\d+(?:\+)?(?=[^A-Z0-9]|$)"),
    re.compile(r"\b(?:PROLIANT\s+)?(?:DL|ML|BL|SL)\s*\d+[A-Z-]*\b"),
    re.compile(r"\bG\d+(?:\+)?\s*-\s*G\d+(?:\+)?(?=[^A-Z0-9]|$)"),
    re.compile(r"\bG\d+(?:\+)?(?:\s+G\d+(?:\+)?)+(?=[^A-Z0-9]|$)"),
    re.compile(r"\bG\d+(?:\+)?\s*-\s*G\d+(?:\+)?(?=[^A-Z0-9]|$)"),
    re.compile(r"\bMSA\d+\b"),
    re.compile(r"\bEVA\s+M\d+\b"),
    re.compile(r"\bM\d{4}\b"),
    re.compile(r"\b3PAR\b"),
]

PART_NUMBER_PATTERN = re.compile(r"\b(?=[A-Z0-9-]*\d)[A-Z0-9]{4,10}-[A-Z0-9]{2,4}\b")
GENERATION_TOKEN_PATTERN = re.compile(r"\b(?:GEN(?:ERATION)?\s*|G)(\d+)(?:\+)?\b", re.I)


@dataclass(slots=True)
class ReferenceImportResult:
    rows_seen: int
    parts_upserted: int
    models_upserted: int
    aliases_upserted: int
    compatibilities_upserted: int


@dataclass(slots=True)
class CompatibilityModelRepairResult:
    compatibilities_scanned: int
    models_upserted: int
    compatibilities_upserted: int


@dataclass(slots=True)
class ReferenceHtmlAnalysis:
    page_kind: str
    page_title: str
    rows_detected: int
    listing_links: list[str]
    detected_models: list[str]
    guidance: str


@dataclass(slots=True)
class ReferencePdfAnalysis:
    page_kind: str
    page_title: str
    page_count: int
    rows_detected: int
    detected_models: list[str]
    detected_part_numbers: list[str]
    guidance: str


PRODUCT_ATTRIBUTE_LABELS = {
    "OPTION PART#": "option_part_number",
    "SMARTBUY PART#": "smartbuy_part_number",
    "SPARE PART#": "spare_part_number",
    "ASSEMBLY PART#": "assembly_part_number",
    "MODEL#": "model_number",
    "CATEGORY": "category",
    "SUB-CATEGORY": "sub_category",
    "GENERATION": "generation",
    "PART NUMBER": "part_number",
    "PRODUCTS ID": "product_id",
    "CAPACITY": "capacity",
    "INTERFACE": "interface",
    "ENCLOSURE": "enclosure",
    "DRIVE DIMENSIONS": "drive_dimensions",
    "SPINDLE SPEED": "spindle_speed",
    "EXTERNAL DATA TRANSFER": "external_data_transfer",
    "SEEK TIME": "seek_time",
    "HOTSWAP": "hot_swap",
    "MANUFACTURER": "manufacturer_name",
    "LIMITED WARRANTY": "limited_warranty",
    "HOT SWAP TRAY": "hot_swap_tray",
    "PORTS": "ports",
}


def extract_candidate_models(product_name: str) -> list[str]:
    cleaned = normalize_text(product_name)
    cleaned = re.sub(r"\(\s*(G\d+(?:\+)?)\s*\)", r" \1", cleaned)
    cleaned = re.sub(r"\bGEN\s*(\d+)\b", r" G\1", cleaned)
    cleaned = re.sub(r"\bGENERATION\s+(\d+)\b", r" G\1", cleaned)
    cleaned = cleaned.replace("+", "")
    matches: list[str] = []
    for pattern in MODEL_PATTERNS:
        matches.extend(match.group(0) for match in pattern.finditer(cleaned))

    seen: set[str] = set()
    ordered: list[str] = []
    for match in matches:
        display_name = re.sub(r"\s+", " ", match).strip()
        normalized = normalize_model_name(display_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(display_name)
    return ordered


def _generation_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in GENERATION_TOKEN_PATTERN.finditer(normalize_text(value)):
        token = f"G{match.group(1)}"
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _backfill_model_candidates(existing_model_name: str, context_text: str) -> list[str]:
    normalized_model = normalize_model_name(existing_model_name)
    if not normalized_model or re.search(r"G\d+", normalized_model):
        return []
    if re.search(r"\b(?:PROLIANT\s+)?(?:DL|ML|BL|SL)\d+[A-Z-]*\b", normalize_text(existing_model_name)) is None:
        return []

    generation_tokens = _generation_tokens(context_text)
    if len(generation_tokens) != 1:
        return []

    generated_models = extract_candidate_models(f"{existing_model_name} {generation_tokens[0]}")
    return [
        model_name
        for model_name in generated_models
        if normalize_model_name(model_name) != normalized_model and re.search(r"G\d+", normalize_model_name(model_name))
    ]


def _is_likely_reference_part_number(candidate: str) -> bool:
    normalized_candidate = normalize_text(candidate)
    if not normalized_candidate:
        return False
    if PART_NUMBER_PATTERN.fullmatch(normalized_candidate) is None:
        return False
    if re.fullmatch(r"20\d{2}-20\d{2}", normalized_candidate):
        return False
    if re.fullmatch(r"\d{2,5}BASE-[A-Z0-9]+", normalized_candidate):
        return False
    return True


def infer_alias_part_numbers(part_number: str) -> list[str]:
    cleaned = normalize_text(part_number)
    aliases = {cleaned}
    aliases.add(cleaned.replace("-SC", ""))
    aliases.add(cleaned.replace("-S21", ""))
    aliases.add(cleaned.replace("-B21", ""))
    aliases.add(cleaned.replace(" ", ""))
    aliases = {alias.strip("-") for alias in aliases if alias.strip("-")}
    return sorted(aliases)


def _upsert_reference_source(
    conn: sqlite3.Connection,
    source_name: str,
    source_type: str,
    source_url: str,
) -> int:
    cleaned_url = source_url.strip()
    existing_by_url = None
    if cleaned_url:
        existing_by_url = conn.execute(
            """
            SELECT id
            FROM reference_sources
            WHERE source_type = ? AND source_url = ?
            """,
            (source_type.strip(), cleaned_url),
        ).fetchone()
    if existing_by_url is not None:
        conn.execute(
            """
            UPDATE reference_sources
            SET source_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (source_name.strip(), int(existing_by_url["id"])),
        )
        return int(existing_by_url["id"])

    conn.execute(
        """
        INSERT INTO reference_sources (source_name, source_type, source_url)
        VALUES (?, ?, ?)
        ON CONFLICT(source_name, source_type, source_url) DO UPDATE SET
            updated_at = CURRENT_TIMESTAMP
        """,
        (source_name.strip(), source_type.strip(), cleaned_url),
    )
    row = conn.execute(
        """
        SELECT id
        FROM reference_sources
        WHERE source_name = ? AND source_type = ? AND source_url = ?
        """,
        (source_name.strip(), source_type.strip(), cleaned_url),
    ).fetchone()
    return int(row["id"])


def _upsert_system_model(
    conn: sqlite3.Connection,
    manufacturer: str,
    model_name: str,
    model_family: str = "",
) -> tuple[int, bool]:
    normalized_model = normalize_model_name(model_name)
    existing = conn.execute(
        "SELECT id FROM system_models WHERE normalized_model_name = ?",
        (normalized_model,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO system_models (manufacturer, model_name, normalized_model_name, model_family)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(normalized_model_name) DO UPDATE SET
            manufacturer = COALESCE(NULLIF(excluded.manufacturer, ''), system_models.manufacturer),
            model_name = excluded.model_name,
            model_family = COALESCE(NULLIF(excluded.model_family, ''), system_models.model_family),
            updated_at = CURRENT_TIMESTAMP
        """,
        (manufacturer.strip(), model_name.strip(), normalized_model, model_family.strip()),
    )
    row = conn.execute(
        "SELECT id FROM system_models WHERE normalized_model_name = ?",
        (normalized_model,),
    ).fetchone()
    return int(row["id"]), existing is None


def _upsert_reference_part(
    conn: sqlite3.Connection,
    source_id: int,
    part_number: str,
    description: str,
    manufacturer: str,
    product_url: str,
    source_title: str,
) -> tuple[int, bool]:
    normalized = normalize_part_number(part_number)
    existing = conn.execute(
        "SELECT id FROM reference_parts WHERE source_id = ? AND normalized_part_number = ?",
        (source_id, normalized),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO reference_parts (
            source_id,
            part_number,
            normalized_part_number,
            description,
            manufacturer,
            product_url,
            source_title
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id, normalized_part_number) DO UPDATE SET
            part_number = excluded.part_number,
            description = COALESCE(NULLIF(excluded.description, ''), reference_parts.description),
            manufacturer = COALESCE(NULLIF(excluded.manufacturer, ''), reference_parts.manufacturer),
            product_url = COALESCE(NULLIF(excluded.product_url, ''), reference_parts.product_url),
            source_title = COALESCE(NULLIF(excluded.source_title, ''), reference_parts.source_title),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            source_id,
            part_number.strip(),
            normalized,
            description.strip(),
            manufacturer.strip(),
            product_url.strip(),
            source_title.strip(),
        ),
    )
    row = conn.execute(
        "SELECT id FROM reference_parts WHERE source_id = ? AND normalized_part_number = ?",
        (source_id, normalized),
    ).fetchone()
    return int(row["id"]), existing is None


def _upsert_reference_aliases(
    conn: sqlite3.Connection,
    reference_part_id: int,
    aliases: Iterable[str],
) -> int:
    inserted = 0
    for alias in aliases:
        normalized = normalize_part_number(alias)
        if not normalized:
            continue
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO reference_part_aliases (
                reference_part_id,
                alias_part_number,
                normalized_alias_part_number
            ) VALUES (?, ?, ?)
            """,
            (reference_part_id, alias.strip(), normalized),
        )
        if cursor.rowcount > 0:
            inserted += 1
    return inserted


def _upsert_reference_attributes(
    conn: sqlite3.Connection,
    reference_part_id: int,
    attributes: dict[str, str],
) -> int:
    updated = 0
    for name, value in attributes.items():
        cleaned_name = name.strip()
        cleaned_value = str(value).strip()
        if not cleaned_name or not cleaned_value:
            continue
        normalized_name = normalize_text(cleaned_name)
        normalized_value = normalize_text(cleaned_value)
        cursor = conn.execute(
            """
            INSERT INTO reference_part_attributes (
                reference_part_id,
                attribute_name,
                attribute_value,
                normalized_attribute_name,
                normalized_attribute_value
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(reference_part_id, normalized_attribute_name) DO UPDATE SET
                attribute_name = excluded.attribute_name,
                attribute_value = excluded.attribute_value,
                normalized_attribute_value = excluded.normalized_attribute_value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (reference_part_id, cleaned_name, cleaned_value, normalized_name, normalized_value),
        )
        if cursor.rowcount > 0:
            updated += 1
    return updated


def _upsert_compatibility(
    conn: sqlite3.Connection,
    system_model_id: int,
    reference_part_id: int,
    source_id: int,
    evidence: str,
    source_url: str,
    confidence: float,
) -> bool:
    existing = conn.execute(
        """
        SELECT 1
        FROM system_part_compatibility
        WHERE system_model_id = ? AND reference_part_id = ? AND source_id = ?
        """,
        (system_model_id, reference_part_id, source_id),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO system_part_compatibility (
            system_model_id,
            reference_part_id,
            source_id,
            evidence,
            source_url,
            confidence
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(system_model_id, reference_part_id, source_id) DO UPDATE SET
            evidence = COALESCE(NULLIF(excluded.evidence, ''), system_part_compatibility.evidence),
            source_url = COALESCE(NULLIF(excluded.source_url, ''), system_part_compatibility.source_url),
            confidence = MAX(system_part_compatibility.confidence, excluded.confidence)
        """,
        (system_model_id, reference_part_id, source_id, evidence.strip(), source_url.strip(), confidence),
    )
    return existing is None


def import_reference_rows(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, object]],
    source_name: str,
    source_type: str,
    source_url: str = "",
) -> ReferenceImportResult:
    source_id = _upsert_reference_source(conn, source_name, source_type, source_url)

    rows_seen = 0
    parts_upserted = 0
    models_upserted = 0
    aliases_upserted = 0
    compatibilities_upserted = 0

    for row in rows:
        rows_seen += 1
        part_number = str(row.get("part_number", "")).strip()
        description = str(row.get("description", "")).strip()
        manufacturer = str(row.get("manufacturer", "")).strip()
        product_url = str(row.get("product_url", "")).strip()
        source_title = str(row.get("source_title", description)).strip()
        explicit_models = row.get("system_models") or []
        if isinstance(explicit_models, str):
            explicit_models = [explicit_models]

        if not part_number:
            continue

        reference_part_id, part_inserted = _upsert_reference_part(
            conn,
            source_id=source_id,
            part_number=part_number,
            description=description,
            manufacturer=manufacturer,
            product_url=product_url,
            source_title=source_title,
        )
        parts_upserted += int(part_inserted)

        alias_values = row.get("aliases") or infer_alias_part_numbers(part_number)
        if isinstance(alias_values, str):
            alias_values = [value.strip() for value in alias_values.split(",") if value.strip()]
        aliases_upserted += _upsert_reference_aliases(conn, reference_part_id, alias_values)

        attribute_values = row.get("attributes") or {}
        if isinstance(attribute_values, dict):
            _upsert_reference_attributes(conn, reference_part_id, {str(key): str(value) for key, value in attribute_values.items()})

        model_names = list(explicit_models) or extract_candidate_models(description)
        for model_name in model_names:
            manufacturer_guess = manufacturer or ("HPE" if "HP" in normalize_text(description) or "HPE" in normalize_text(description) else "")
            system_model_id, model_inserted = _upsert_system_model(
                conn,
                manufacturer=manufacturer_guess,
                model_name=str(model_name),
                model_family=str(model_name),
            )
            models_upserted += int(model_inserted)
            compatibility_inserted = _upsert_compatibility(
                conn,
                system_model_id=system_model_id,
                reference_part_id=reference_part_id,
                source_id=source_id,
                evidence=description,
                source_url=product_url or source_url,
                confidence=0.75,
            )
            compatibilities_upserted += int(compatibility_inserted)

    return ReferenceImportResult(
        rows_seen=rows_seen,
        parts_upserted=parts_upserted,
        models_upserted=models_upserted,
        aliases_upserted=aliases_upserted,
        compatibilities_upserted=compatibilities_upserted,
    )


def repair_compatibility_model_links(conn: sqlite3.Connection) -> CompatibilityModelRepairResult:
    rows = conn.execute(
        """
        SELECT
            spc.reference_part_id,
            spc.source_id,
            spc.evidence,
            spc.source_url,
            spc.confidence,
            sm.manufacturer AS model_manufacturer,
            sm.model_name,
            rp.description AS reference_description,
            rp.manufacturer AS reference_manufacturer,
            rp.source_title
        FROM system_part_compatibility spc
        INNER JOIN system_models sm ON sm.id = spc.system_model_id
        INNER JOIN reference_parts rp ON rp.id = spc.reference_part_id
        ORDER BY spc.id ASC
        """
    ).fetchall()

    models_upserted = 0
    compatibilities_upserted = 0
    for row in rows:
        evidence = str(row["evidence"] or "").strip()
        reference_description = str(row["reference_description"] or "").strip()
        source_title = str(row["source_title"] or "").strip()
        existing_model_name = str(row["model_name"] or "").strip()
        manufacturer_guess = str(row["model_manufacturer"] or row["reference_manufacturer"] or "").strip()

        candidate_models: list[str] = []
        for text_value in (evidence, reference_description):
            for model_name in extract_candidate_models(text_value):
                if model_name not in candidate_models:
                    candidate_models.append(model_name)

        context_text = " ".join(value for value in (evidence, reference_description, source_title) if value)
        for model_name in _backfill_model_candidates(existing_model_name, context_text):
            if model_name not in candidate_models:
                candidate_models.append(model_name)

        for model_name in candidate_models:
            system_model_id, model_inserted = _upsert_system_model(
                conn,
                manufacturer=manufacturer_guess,
                model_name=model_name,
                model_family=model_name,
            )
            models_upserted += int(model_inserted)
            compatibility_inserted = _upsert_compatibility(
                conn,
                system_model_id=system_model_id,
                reference_part_id=int(row["reference_part_id"]),
                source_id=int(row["source_id"]),
                evidence=evidence or reference_description,
                source_url=str(row["source_url"] or ""),
                confidence=float(row["confidence"] or 0.75),
            )
            compatibilities_upserted += int(compatibility_inserted)

    return CompatibilityModelRepairResult(
        compatibilities_scanned=len(rows),
        models_upserted=models_upserted,
        compatibilities_upserted=compatibilities_upserted,
    )


def parse_harddrivesdirect_listing(html: str, page_url: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue

        product_name = normalize_text(cells[0].get_text(" ", strip=True))
        part_number = normalize_text(cells[1].get_text(" ", strip=True))
        if not product_name or not part_number:
            continue
        if "PRODUCT NAME" in product_name or "PART#" in part_number:
            continue

        normalized_part = normalize_part_number(part_number)
        key = (normalized_part, product_name)
        if not _is_likely_reference_part_number(part_number) or key in seen:
            continue
        seen.add(key)

        anchor = tr.find("a", href=True)
        product_url = urljoin(page_url, anchor["href"]) if anchor else page_url
        manufacturer = "HPE" if product_name.startswith("HP ") or " HP " in product_name or "HPE" in product_name else ""
        rows.append(
            {
                "part_number": part_number,
                "description": product_name,
                "manufacturer": manufacturer,
                "product_url": product_url,
                "source_title": product_name,
                "system_models": extract_candidate_models(product_name),
                "aliases": infer_alias_part_numbers(part_number),
            }
        )

    return rows


def extract_harddrivesdirect_page_models(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = normalize_text(soup.get_text(" ", strip=True))
    compatibility_match = re.search(
        r"COMPATIBLE WITH (?:THE FOLLOWING|THESE)\s+(.*?)(?:SPECIFICALLY DESIGNED|DISPLAYING \d+ TO \d+|RESULT PAGES|COUNTRY US DOLLAR PRICES|CONTACT US)",
        page_text,
    )
    if compatibility_match:
        return extract_candidate_models(compatibility_match.group(1))
    return []


def _clean_harddrivesdirect_product_text(text: str, part_number: str) -> str:
    cleaned = normalize_text(text)
    cleaned = re.sub(r"\$\s*[0-9,]+(?:\.[0-9]{2})?", " ", cleaned)
    cleaned = re.sub(r"\bNEW\s+(?:SSD|HDD)?\s*SPECIAL\b", " ", cleaned)
    cleaned = re.sub(r"\bSPECIAL\b", " ", cleaned)
    cleaned = re.sub(r"\bPART#\s*" + re.escape(part_number) + r"\b", " ", cleaned)
    cleaned = cleaned.replace("FLAG", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_harddrivesdirect_search_results(html: str, page_url: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    common_models = extract_harddrivesdirect_page_models(html)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        anchor_text = normalize_text(anchor.get_text(" ", strip=True))
        part_match = PART_NUMBER_PATTERN.search(anchor_text)
        if part_match is None:
            continue

        part_number = part_match.group(0)
        if not _is_likely_reference_part_number(part_number):
            continue
        normalized_part = normalize_part_number(part_number)
        if not normalized_part or normalized_part in seen:
            continue

        container = anchor.find_parent(["td", "div", "li"])
        container_text = normalize_text(container.get_text(" ", strip=True)) if container is not None else anchor_text
        description = _clean_harddrivesdirect_product_text(container_text or anchor_text, part_number)
        if part_number not in description and anchor_text:
            description = _clean_harddrivesdirect_product_text(anchor_text, part_number)

        if not description:
            continue

        seen.add(normalized_part)
        product_url = urljoin(page_url, anchor["href"])
        manufacturer = "HPE" if any(token in description for token in ("HP ", "HPE", "PROLIANT")) else ""
        rows.append(
            {
                "part_number": part_number,
                "description": description,
                "manufacturer": manufacturer,
                "product_url": product_url,
                "source_title": description,
                "system_models": common_models or extract_candidate_models(description),
                "aliases": infer_alias_part_numbers(part_number),
            }
        )

    return rows


def parse_reference_rows(html: str, page_url: str) -> list[dict[str, object]]:
    combined_rows = parse_harddrivesdirect_listing(html, page_url)
    if combined_rows:
        return combined_rows
    search_rows = parse_harddrivesdirect_search_results(html, page_url)
    if search_rows:
        return search_rows
    product_row = parse_harddrivesdirect_product_page(html, page_url)
    return [product_row] if product_row is not None else []


def _extract_product_text_block(normalized_text: str, start_label: str, end_labels: tuple[str, ...]) -> str:
    start_index = normalized_text.find(start_label)
    if start_index == -1:
        return ""
    block = normalized_text[start_index + len(start_label):]
    end_positions = [block.find(label) for label in end_labels if block.find(label) != -1]
    if end_positions:
        block = block[: min(end_positions)]
    return block.strip()


def _extract_product_aliases(normalized_text: str) -> tuple[str, list[str], dict[str, str]]:
    aliases_block = _extract_product_text_block(normalized_text, "PART NUMBER(S)", ("OVERVIEW:", "SPECIFICATIONS:"))
    alias_attributes: dict[str, str] = {}
    aliases: list[str] = []
    primary_part = ""
    for label, attribute_name in PRODUCT_ATTRIBUTE_LABELS.items():
        if "PART#" not in label and label != "MODEL#":
            continue
        match = re.search(label + r"\s+([A-Z0-9-]+)", aliases_block)
        if match is None:
            continue
        value = match.group(1).strip()
        alias_attributes[attribute_name] = value
        if attribute_name == "option_part_number":
            primary_part = value
        else:
            aliases.append(value)

    if primary_part:
        aliases.append(primary_part)

    seen: set[str] = set()
    deduped_aliases: list[str] = []
    for alias in aliases:
        normalized = normalize_part_number(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped_aliases.append(alias)

    return primary_part, deduped_aliases, alias_attributes


def _extract_product_specifications(normalized_text: str) -> dict[str, str]:
    specs_block = _extract_product_text_block(normalized_text, "SPECIFICATIONS:", ("REPLACEMENT INSTRUCTIONS", "PART#", "QUANTITY:", "OTHER CONDITIONS:"))
    attributes: dict[str, str] = {}
    for label, attribute_name in PRODUCT_ATTRIBUTE_LABELS.items():
        if label in {"OPTION PART#", "SMARTBUY PART#", "SPARE PART#", "ASSEMBLY PART#", "MODEL#"}:
            continue
        match = re.search(label + r"\s+(.+?)(?=(?:" + "|".join(re.escape(candidate) for candidate in PRODUCT_ATTRIBUTE_LABELS.keys()) + r")\s|$)", specs_block)
        if match is None:
            continue
        attributes[attribute_name] = match.group(1).strip()
    return attributes


def parse_harddrivesdirect_product_page(html: str, page_url: str) -> dict[str, object] | None:
    soup = BeautifulSoup(html, "html.parser")
    title_candidates = [soup.title.get_text(" ", strip=True) if soup.title else ""]
    heading = soup.find(["h1", "h2"])
    if heading is not None:
        title_candidates.append(heading.get_text(" ", strip=True))
    title = next((candidate.strip() for candidate in title_candidates if candidate and candidate.strip()), "")

    normalized_text = normalize_text(soup.get_text(" ", strip=True))
    if "SPECIFICATIONS:" not in normalized_text or "PART NUMBER(S)" not in normalized_text:
        return None

    primary_part, aliases, alias_attributes = _extract_product_aliases(normalized_text)
    if not primary_part:
        part_match = PART_NUMBER_PATTERN.search(normalized_text)
        if part_match is None:
            return None
        primary_part = part_match.group(0)
    if not _is_likely_reference_part_number(primary_part):
        return None

    description_block = _extract_product_text_block(normalized_text, "DESCRIPTION:", ("PART NUMBER(S)", "OVERVIEW:"))
    description = description_block or normalize_text(title)
    attributes = alias_attributes
    attributes.update(_extract_product_specifications(normalized_text))
    page_models = extract_harddrivesdirect_page_models(html)
    if not page_models:
        compatibility_block = _extract_product_text_block(normalized_text, "THIS HP PART#", ("FLAT RATE SHIPPING OPTIONS", "OTHER IN STOCK", "SHOPPERAPPROVED"))
        page_models = extract_candidate_models(compatibility_block)

    manufacturer = "HPE" if any(token in description for token in ("HP ", "HPE", "PROLIANT")) else ""
    product_url = page_url
    return {
        "part_number": primary_part,
        "description": description,
        "manufacturer": manufacturer,
        "product_url": product_url,
        "source_title": normalize_text(title) or description,
        "system_models": page_models,
        "aliases": aliases,
        "attributes": attributes,
    }


def _extract_pdf_page_texts(pdf_bytes: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "PDF import support is not installed. Install the pypdf package and restart the app."
        ) from exc

    reader = PdfReader(BytesIO(pdf_bytes))
    page_texts: list[str] = []
    for page in reader.pages:
        page_texts.append(page.extract_text() or "")
    return page_texts


def _guess_document_title(page_texts: list[str], fallback_title: str) -> str:
    cleaned_fallback = normalize_text(fallback_title)
    cleaned_fallback = re.sub(r"\.PDF$", "", cleaned_fallback).strip()
    if cleaned_fallback and len(cleaned_fallback) >= 8:
        return cleaned_fallback[:160]

    for page_text in page_texts[:2]:
        for raw_line in page_text.splitlines():
            cleaned_line = normalize_text(raw_line)
            if cleaned_line and len(cleaned_line) >= 12:
                return cleaned_line[:160]
    return fallback_title


def _looks_like_pdf_heading(line: str) -> bool:
    if not line or len(line) > 100:
        return False
    if PART_NUMBER_PATTERN.search(line):
        return False
    alpha_count = sum(character.isalpha() for character in line)
    return alpha_count >= 6


def _clean_pdf_description_candidate(text: str, part_number: str) -> str:
    cleaned = normalize_text(text)
    cleaned = re.sub(re.escape(normalize_text(part_number)), " ", cleaned)
    cleaned = re.sub(r"\b(?:OPTION|SMARTBUY|SPARE|ASSEMBLY)\s+PART#\s*", " ", cleaned)
    cleaned = re.sub(r"\bPART\s+NUMBER\b", " ", cleaned)
    cleaned = re.sub(r"\bNOTE:?(?:NOTE:?)*\b", " ", cleaned)
    cleaned = re.sub(r"\bQUICKSPECS\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;:-")
    if len(cleaned) > 240:
        cleaned = cleaned[:240].rsplit(" ", 1)[0]
    return cleaned


def _pdf_description_score(candidate: str, context_line: str) -> int:
    if not candidate:
        return -100

    score = 0
    upper_candidate = candidate.upper()
    score += min(len(candidate), 120)
    if 12 <= len(candidate) <= 120:
        score += 20
    if PART_NUMBER_PATTERN.search(candidate):
        score -= 60
    if any(token in upper_candidate for token in ("NOTE", "REQUIRED", "QUICKSPECS", "OPTIONAL UPGRADESOPTIONAL UPGRADES")):
        score -= 35
    if any(token in upper_candidate for token in ("REQUIRES THE ADDITION OF", "IF 2 PROCESSORS ARE DESIRED", "YOU MUST START WITH")):
        score -= 45
    if any(token in upper_candidate for token in ("CABLE", "CONTROLLER", "UPGRADE", "SWITCH", "ADAPTER", "DRIVE", "MEMORY", "PROCESSOR", "POWER", "RAIL", "KIT")):
        score += 25
    if len(candidate) <= 60 and any(token in upper_candidate for token in ("CABLE", "CONTROLLER", "UPGRADE", "SWITCH", "ADAPTER", "DRIVE", "MEMORY", "PROCESSOR", "POWER", "RAIL", "KIT")):
        score += 18
    if context_line == "previous":
        score += 8
    if context_line == "next":
        score += 6
    if context_line == "current":
        score += 10
    if context_line == "current" and any(token in upper_candidate for token in ("CABLE", "CONTROLLER", "UPGRADE", "SWITCH", "ADAPTER", "DRIVE", "MEMORY", "PROCESSOR", "POWER", "RAIL", "KIT")):
        score += 18
    return score


def _clean_pdf_part_description(lines: list[str], line_index: int, part_number: str) -> str:
    candidate_specs: list[tuple[str, str]] = []
    for offset, label in ((-2, "previous_far"), (-1, "previous"), (0, "current"), (1, "next"), (2, "next_far")):
        candidate_index = line_index + offset
        if 0 <= candidate_index < len(lines):
            candidate_text = _clean_pdf_description_candidate(lines[candidate_index], part_number)
            candidate_specs.append((candidate_text, label))

    best_candidate = ""
    best_score = -1000
    for candidate_text, label in candidate_specs:
        score = _pdf_description_score(candidate_text, label)
        if score > best_score:
            best_candidate = candidate_text
            best_score = score

    if best_candidate:
        return best_candidate

    fallback = _clean_pdf_description_candidate(lines[line_index], part_number)
    return fallback or normalize_text(part_number)


def parse_reference_pdf_pages(
    page_texts: list[str],
    page_url: str,
    document_title: str = "",
) -> list[dict[str, object]]:
    fallback_title = normalize_text(document_title) or normalize_text(page_url)
    title = _guess_document_title(page_texts, fallback_title)
    document_text = " ".join(normalize_text(page_text) for page_text in page_texts if page_text)
    document_models = extract_candidate_models(document_text)
    document_manufacturer = ""
    if any(token in document_text for token in ("PROLIANT", "HPE", "HP ")):
        document_manufacturer = "HPE"
    elif any(token in document_text for token in ("POWEREDGE", "DELL")):
        document_manufacturer = "DELL"

    rows: list[dict[str, object]] = []
    seen_parts: set[str] = set()
    for page_number, page_text in enumerate(page_texts, start=1):
        normalized_lines = [normalize_text(line) for line in page_text.splitlines()]
        normalized_lines = [line for line in normalized_lines if line]
        if not normalized_lines:
            continue

        page_models = extract_candidate_models(" ".join(normalized_lines)) or document_models
        current_heading = ""
        for line_index, line in enumerate(normalized_lines):
            if _looks_like_pdf_heading(line):
                current_heading = line

            part_matches = [match.group(0) for match in PART_NUMBER_PATTERN.finditer(line)]
            if not part_matches:
                continue

            for part_number in part_matches:
                normalized_part = normalize_part_number(part_number)
                if not _is_likely_reference_part_number(part_number) or normalized_part in seen_parts:
                    continue
                seen_parts.add(normalized_part)

                description = _clean_pdf_part_description(normalized_lines, line_index, part_number)
                if not description:
                    description = line

                attributes = {
                    "document_type": "pdf_manual",
                    "source_page": str(page_number),
                }
                if current_heading:
                    attributes["document_section"] = current_heading

                rows.append(
                    {
                        "part_number": part_number,
                        "description": description,
                        "manufacturer": document_manufacturer,
                        "product_url": f"{page_url}#page={page_number}" if page_url else "",
                        "source_title": title,
                        "system_models": page_models,
                        "aliases": infer_alias_part_numbers(part_number),
                        "attributes": attributes,
                    }
                )

    return rows


def analyze_reference_pdf_pages(
    page_texts: list[str],
    page_url: str,
    document_title: str = "",
) -> ReferencePdfAnalysis:
    fallback_title = normalize_text(document_title) or normalize_text(page_url)
    title = _guess_document_title(page_texts, fallback_title)
    normalized_pages = [normalize_text(page_text) for page_text in page_texts if normalize_text(page_text)]
    if not normalized_pages:
        return ReferencePdfAnalysis(
            page_kind="empty_or_scanned_pdf",
            page_title=title,
            page_count=len(page_texts),
            rows_detected=0,
            detected_models=[],
            detected_part_numbers=[],
            guidance="No extractable text was found in this PDF. It may be image-only and would need OCR support.",
        )

    rows = parse_reference_pdf_pages(page_texts, page_url, document_title=document_title)
    document_text = " ".join(normalized_pages)
    detected_models = extract_candidate_models(document_text)
    detected_part_numbers = sorted({str(row["part_number"]) for row in rows})[:25]
    if rows:
        return ReferencePdfAnalysis(
            page_kind="text_pdf_manual",
            page_title=title,
            page_count=len(page_texts),
            rows_detected=len(rows),
            detected_models=detected_models,
            detected_part_numbers=detected_part_numbers,
            guidance="This PDF contains extractable text and explicit part references that can be imported.",
        )

    return ReferencePdfAnalysis(
        page_kind="text_pdf_no_parts",
        page_title=title,
        page_count=len(page_texts),
        rows_detected=0,
        detected_models=detected_models,
        detected_part_numbers=[],
        guidance="This PDF contains extractable text, but no explicit part numbers were detected for first-pass import.",
    )


def analyze_reference_pdf(
    pdf_bytes: bytes,
    page_url: str,
    document_title: str = "",
) -> ReferencePdfAnalysis:
    page_texts = _extract_pdf_page_texts(pdf_bytes)
    return analyze_reference_pdf_pages(page_texts, page_url, document_title=document_title)


def parse_reference_pdf(
    pdf_bytes: bytes,
    page_url: str,
    document_title: str = "",
) -> list[dict[str, object]]:
    page_texts = _extract_pdf_page_texts(pdf_bytes)
    return parse_reference_pdf_pages(page_texts, page_url, document_title=document_title)


def import_reference_pdf(
    conn: sqlite3.Connection,
    pdf_bytes: bytes,
    page_url: str,
    source_name: str = "PDF Manual",
    source_type: str = "saved_pdf",
    document_title: str = "",
) -> ReferenceImportResult:
    page_texts = _extract_pdf_page_texts(pdf_bytes)
    analysis = analyze_reference_pdf_pages(page_texts, page_url, document_title=document_title)
    if analysis.page_kind == "empty_or_scanned_pdf":
        raise RuntimeError(
            "No extractable text was found in this PDF. First-pass PDF support only handles text-based PDFs."
        )
    if analysis.page_kind == "text_pdf_no_parts":
        raise RuntimeError(
            "This PDF contains readable text, but no explicit part numbers were detected. "
            "First-pass PDF import only creates reference rows when the manual lists option, spare, or assembly part numbers."
        )

    rows = parse_reference_pdf_pages(page_texts, page_url, document_title=document_title)
    return import_reference_rows(
        conn,
        rows=rows,
        source_name=source_name,
        source_type=source_type,
        source_url=page_url,
    )


def extract_harddrivesdirect_listing_links(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href:
            continue
        absolute_url = urljoin(page_url, href)
        normalized_url = absolute_url.upper()
        if "HARDDRIVESDIRECT.COM" not in normalized_url:
            continue
        if "HTML_" not in normalized_url:
            continue
        if "SAS_SATA" not in normalized_url and "SSD" not in normalized_url and "NVME" not in normalized_url:
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append(absolute_url)

    return links


def analyze_reference_html(html: str, page_url: str) -> ReferenceHtmlAnalysis:
    soup = BeautifulSoup(html, "html.parser")
    title_candidates = [
        soup.title.get_text(" ", strip=True) if soup.title else "",
    ]
    heading = soup.find(["h1", "h2"])
    if heading is not None:
        title_candidates.append(heading.get_text(" ", strip=True))

    page_title = next((candidate.strip() for candidate in title_candidates if candidate and candidate.strip()), page_url)
    normalized_text = normalize_text(soup.get_text(" ", strip=True))
    listing_rows = parse_harddrivesdirect_listing(html, page_url)
    search_rows = parse_harddrivesdirect_search_results(html, page_url)
    product_row = parse_harddrivesdirect_product_page(html, page_url)
    listing_links = extract_harddrivesdirect_listing_links(html, page_url)
    detected_models = extract_harddrivesdirect_page_models(html) or extract_candidate_models(page_title)

    if listing_rows:
        return ReferenceHtmlAnalysis(
            page_kind="direct_listing",
            page_title=page_title,
            rows_detected=len(listing_rows),
            listing_links=listing_links,
            detected_models=detected_models,
            guidance="This page contains direct part rows and can be imported.",
        )

    if search_rows:
        return ReferenceHtmlAnalysis(
            page_kind="search_results_listing",
            page_title=page_title,
            rows_detected=len(search_rows),
            listing_links=listing_links,
            detected_models=detected_models,
            guidance="This page contains product search results with page-level compatibility context and can be imported.",
        )

    if product_row is not None:
        return ReferenceHtmlAnalysis(
            page_kind="product_detail",
            page_title=page_title,
            rows_detected=1,
            listing_links=listing_links,
            detected_models=detected_models or [str(model) for model in product_row.get("system_models", [])],
            guidance="This page contains one detailed product record with aliases and specifications and can be imported.",
        )

    if listing_links:
        return ReferenceHtmlAnalysis(
            page_kind="configuration_index",
            page_title=page_title,
            rows_detected=0,
            listing_links=listing_links,
            detected_models=detected_models,
            guidance="This page looks like a configuration or index page. Import one of the linked listing pages instead.",
        )

    model_page_tokens = (
        "OPTIONS",
        "HARD DRIVES",
        "SOLID STATE DRIVES",
        "POWER SUPPLIES",
        "CONTROLLERS",
        "MEMORY",
        "PROCESSORS",
    )
    if (
        any(token in normalized_text for token in model_page_tokens)
        and ("PROLIANT" in normalized_text or bool(detected_models))
    ):
        return ReferenceHtmlAnalysis(
            page_kind="model_category_unknown",
            page_title=page_title,
            rows_detected=0,
            listing_links=[],
            detected_models=detected_models,
            guidance="This page looks model-specific, but no direct part rows were detected in the saved HTML.",
        )

    return ReferenceHtmlAnalysis(
        page_kind="unknown",
        page_title=page_title,
        rows_detected=0,
        listing_links=listing_links,
        detected_models=detected_models,
        guidance="The saved HTML could not be recognized as an importable listing page.",
    )


def import_reference_html(
    conn: sqlite3.Connection,
    html: str,
    page_url: str,
    source_name: str = "HardDrivesDirect",
    source_type: str = "saved_html",
) -> ReferenceImportResult:
    analysis = analyze_reference_html(html, page_url)
    rows = parse_reference_rows(html, page_url)
    if analysis.page_kind == "configuration_index":
        preview_links = ", ".join(analysis.listing_links[:5])
        raise RuntimeError(
            "This saved HTML looks like a HardDrivesDirect configuration or index page, not a direct parts listing. "
            f"Import one of the linked listing pages instead. Examples: {preview_links}"
        )
    if analysis.page_kind == "model_category_unknown":
        raise RuntimeError(
            "This saved HTML looks like a model-specific or category-specific page, but no direct part rows were detected. "
            "Inspect the saved page structure before importing it."
        )
    if analysis.page_kind not in {"direct_listing", "search_results_listing", "product_detail"}:
        raise RuntimeError("The saved HTML could not be recognized as an importable listing page.")

    return import_reference_rows(
        conn,
        rows=rows,
        source_name=source_name,
        source_type=source_type,
        source_url=page_url,
    )


def import_reference_url(
    conn: sqlite3.Connection,
    url: str,
    source_name: str = "HardDrivesDirect",
) -> ReferenceImportResult:
    try:
        response = requests.get(url, headers=DEFAULT_WEB_HEADERS, timeout=30)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 403:
            raise RuntimeError(
                "The source website blocked the automated request with HTTP 403. "
                "Open the page in your browser, save it as an HTML file, then use the saved HTML import option in the app."
            ) from exc
        raise

    return import_reference_html(
        conn,
        html=response.text,
        page_url=url,
        source_name=source_name,
        source_type="web_listing",
    )
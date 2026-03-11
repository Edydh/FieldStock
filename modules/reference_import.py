from __future__ import annotations

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


@dataclass(slots=True)
class ReferenceImportResult:
    rows_seen: int
    parts_upserted: int
    models_upserted: int
    aliases_upserted: int
    compatibilities_upserted: int


@dataclass(slots=True)
class ReferenceHtmlAnalysis:
    page_kind: str
    page_title: str
    rows_detected: int
    listing_links: list[str]
    detected_models: list[str]
    guidance: str


def extract_candidate_models(product_name: str) -> list[str]:
    cleaned = normalize_text(product_name)
    cleaned = re.sub(r"\(\s*(G\d+(?:\+)?)\s*\)", r" \1", cleaned)
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
        if not normalized_part or key in seen:
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
    return parse_harddrivesdirect_search_results(html, page_url)


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
    rows = listing_rows or search_rows
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
    if analysis.page_kind not in {"direct_listing", "search_results_listing"}:
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
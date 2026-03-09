from __future__ import annotations

import re
from typing import Any


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_part_number(value: Any) -> str:
    text = normalize_text(value)
    return re.sub(r"[^A-Z0-9]", "", text)


def safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    return float(value)

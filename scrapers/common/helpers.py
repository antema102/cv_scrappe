from __future__ import annotations

import re
from typing import Any


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def extract_job_id(job_url: str) -> str:
    match = re.search(r"-(\d+)$", job_url)
    return match.group(1) if match else job_url


def html_text(element: Any | None) -> str:
    if element is None:
        return ""
    return normalize_text(element.get_text(" ", strip=True))


def first_text(soup: Any, selector: str) -> str:
    element = soup.select_one(selector)
    return normalize_text(element.get_text(" ", strip=True)) if element else ""

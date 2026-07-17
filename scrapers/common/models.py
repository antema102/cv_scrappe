from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class JobListing:
    job_id: str
    title: str
    job_url: str
    company_name: str
    company_url: str
    company_id: str
    detail: dict[str, Any]


@dataclass(slots=True)
class CompanyProfile:
    company_id: str
    company_url: str
    name: str
    city: str
    country: str
    sector: str
    website: str
    description: str
    logo_url: str

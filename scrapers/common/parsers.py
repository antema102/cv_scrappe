from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

from .helpers import extract_job_id, first_text, html_text, normalize_text
from .models import CompanyProfile, JobListing

if TYPE_CHECKING:
    from .country_config import CountryConfig


# ---------------------------------------------------------------------------
# Page recruteurs
# ---------------------------------------------------------------------------

def parse_recruiter_anchor(anchor: Any, config: "CountryConfig") -> dict[str, Any] | None:
    href = anchor.get("href", "")
    company_id = config.extract_company_id(href)
    if not company_id:
        return None
    img = anchor.find("img")
    name = normalize_text(img.get("alt", "")) if img else ""
    logo_url = config.absolute_url(img.get("src", "")) if img else ""
    return {
        "company_id": company_id,
        "company_url": config.company_profile_url(company_id),
        "name": name,
        "logo_url": logo_url,
    }


# ---------------------------------------------------------------------------
# Pagination (FR + EN)
# ---------------------------------------------------------------------------

_LAST_PAGE_TITLES = (
    "Aller à la dernière page",   # FR
    "Go to last page",            # EN
)


def get_total_pages(soup: BeautifulSoup) -> int:
    for title in _LAST_PAGE_TITLES:
        last_link = soup.select_one(f"li.pager-item a[title='{title}']")
        if last_link:
            href = last_link.get("href", "")
            match = re.search(r"[?&]page=(\d+)", str(href))
            return int(match.group(1)) + 1 if match else 1
    return 1


# ---------------------------------------------------------------------------
# Carte d'offre d'emploi
# ---------------------------------------------------------------------------

def parse_listing_card(card: Any, config: "CountryConfig") -> JobListing | None:
    job_anchor = card.select_one("h3 a")
    if job_anchor is None:
        return None
    company_anchor = card.select_one("a.card-job-company.company-name")
    company_name = normalize_text(company_anchor.get_text(" ", strip=True)) if company_anchor else ""
    job_url = config.absolute_url(job_anchor.get("href"))
    company_url = config.absolute_url(company_anchor.get("href")) if company_anchor else ""
    return JobListing(
        job_id=extract_job_id(job_url),
        title=normalize_text(job_anchor.get_text(" ", strip=True)),
        job_url=job_url,
        company_name=company_name,
        company_url=company_url,
        company_id=config.extract_company_id(company_url),
        detail={},
    )


# ---------------------------------------------------------------------------
# Détail d'une offre
# ---------------------------------------------------------------------------

def extract_criteria(soup: Any) -> dict[str, str]:
    criteria: dict[str, str] = {}
    for item in soup.select("ul.arrow-list > li"):
        key = item.find("strong")
        value = item.find("span")
        if key is None or value is None:
            continue
        label = normalize_text(key.get_text(" ", strip=True)).rstrip(" :")
        criteria[label] = normalize_text(value.get_text(" ", strip=True))
    return criteria


def extract_job_sections(soup: BeautifulSoup) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for section in soup.select(".card-block-content > section"):
        sections.append({
            "heading": first_text(section, "h3.job-title"),
            "description": html_text(section.select_one(".job-description")),
            "qualifications": [
                normalize_text(item.get_text(" ", strip=True))
                for item in section.select(".job-qualifications li")
                if normalize_text(item.get_text(" ", strip=True))
            ],
            "criteria": extract_criteria(section),
            "skills": [
                normalize_text(item.get_text(" ", strip=True))
                for item in section.select("ul.skills li")
                if normalize_text(item.get_text(" ", strip=True))
            ],
        })
    return sections


def build_job_detail(soup: BeautifulSoup, job_url: str) -> dict[str, Any]:
    return {
        "job_url": job_url,
        "headline": first_text(soup, "h3.job-title"),
        "description": html_text(soup.select_one(".job-description")),
        "qualifications": [
            normalize_text(item.get_text(" ", strip=True))
            for item in soup.select(".job-qualifications li")
            if normalize_text(item.get_text(" ", strip=True))
        ],
        "criteria": extract_criteria(soup),
        "skills": [
            normalize_text(item.get_text(" ", strip=True))
            for item in soup.select("ul.skills li")
            if normalize_text(item.get_text(" ", strip=True))
        ],
        "sections": extract_job_sections(soup),
    }


# ---------------------------------------------------------------------------
# Profil entreprise (FR + EN)
# ---------------------------------------------------------------------------

def _field(fields: dict[str, str], *keys: str) -> str:
    for k in keys:
        if fields.get(k):
            return fields[k]
    return ""


def extract_company_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in soup.select(".card-block-company li"):
        key = item.find("strong")
        value = item.find("span")
        if key is None or value is None:
            continue
        label = normalize_text(key.get_text(" ", strip=True)).rstrip(":")
        fields[label] = normalize_text(value.get_text(" ", strip=True))
    return fields


def build_company_profile(
    soup: BeautifulSoup,
    company_url: str,
    fallback: dict[str, Any],
    config: "CountryConfig",
) -> CompanyProfile:
    fields = extract_company_fields(soup)
    logo_tag = soup.select_one(".card-block-company picture img")
    logo_url = logo_tag.get("src", "") if logo_tag else fallback.get("logo_url", "")
    return CompanyProfile(
        company_id=config.extract_company_id(company_url),
        company_url=company_url,
        name=_field(fields, "Entreprise", "Company", "Organisation") or fallback.get("name", ""),
        city=_field(fields, "Ville", "City"),
        country=_field(fields, "Pays", "Country"),
        sector=_field(fields, "Secteur d´activité", "Industry", "Sector", "Industries"),
        website=_field(fields, "Site Internet", "Website"),
        description=first_text(soup, ".card-block-company-description p"),
        logo_url=logo_url,
    )

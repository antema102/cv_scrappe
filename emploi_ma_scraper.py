from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from seleniumbase import SB

from emploi_scraper import ApiClient, JsonStore

PROJECT_ROOT = Path(__file__).resolve().parent
BASE_URL = "https://www.emploiburkina.com"
RECRUITERS_URL = f"{BASE_URL}/recruteurs"
JOBS_SEARCH_BASE = f"{BASE_URL}/recherche-jobs-burkina-faso"
PROFILE_DIR = PROJECT_ROOT / "my_custom_profile"
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
JOBS_FILE = OUTPUT_DIR / "jobs_ma.json"
COMPANIES_FILE = OUTPUT_DIR / "companies_ma.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _extract_company_id(url: str) -> str:
    """Extrait l'ID entreprise depuis /recruteur/ID ou is_recruiter_nid:ID."""
    match = re.search(r"/recruteur/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"is_recruiter_nid(?:%3A|:)(\d+)", url)
    if match:
        return match.group(1)
    return ""


def _absolute_url(href: str | None) -> str:
    return urljoin(BASE_URL, href or "")


def _company_jobs_url(company_id: str) -> str:
    return f"{JOBS_SEARCH_BASE}?fq%5B0%5D=is_recruiter_nid%3A{company_id}"


def _company_profile_url(company_id: str) -> str:
    return f"{BASE_URL}/recruteur/{company_id}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class EmploiMaScraper:
    def __init__(self, api_url: str | None = None) -> None:
        self.company_store = JsonStore(COMPANIES_FILE)
        self.job_store = JsonStore(JOBS_FILE)
        self.api_client = ApiClient(api_url) if api_url else None

    def scrape_all(self) -> dict[str, Any]:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        all_jobs: list[dict[str, Any]] = []
        all_new_companies: list[dict[str, Any]] = []

        with SB(
            uc=True,
            locale="en",
            user_data_dir=str(PROFILE_DIR),
            disable_js=False,
        ) as sb:
            sb.activate_cdp_mode()

            # Étape 1 : récupérer la liste des recruteurs
            companies = self._scrape_recruiter_list(sb)
            print(f"[RECRUTEURS] {len(companies)} entreprises détectées")

            # Étape 2 : pour chaque entreprise, scraper le profil + les offres
            for idx, entry in enumerate(companies, 1):
                company_id = entry["company_id"]
                print(f"[ENTREPRISE {idx}/{len(companies)}] {entry['name']} (id={company_id})")

                company_result = self._get_or_scrape_company(sb, company_id, entry)
                if company_result["is_new"]:
                    all_new_companies.append(company_result["data"])

                if not company_result["has_jobs"]:
                    print(f"  [SKIP] Pas d'offres disponibles pour {entry['name']}")
                    continue

                company_jobs = self._scrape_company_jobs(sb, company_id, company_result["data"])
                all_jobs.extend(company_jobs)

        return {"jobs": all_jobs, "new_companies": all_new_companies}

    # ------------------------------------------------------------------
    # Liste des recruteurs
    # ------------------------------------------------------------------

    def _scrape_recruiter_list(self, sb: SB) -> list[dict[str, Any]]:
        companies: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        self._goto_with_retry(sb, RECRUITERS_URL, ".card-block-content")
        soup = self._page_soup(sb)
        total_pages = self._get_total_pages(soup)
        print(f"[RECRUTEURS] Pages: {total_pages}")

        page = 0
        while page < total_pages:
            if page > 0:
                sb.sleep(3)
                self._goto_with_retry(sb, f"{RECRUITERS_URL}?page={page}", ".card-block-content")
                soup = self._page_soup(sb)

            for anchor in soup.select(".card-block-content a[href]"):
                entry = self._parse_recruiter_anchor(anchor)
                if entry and entry["company_id"] not in seen_ids:
                    seen_ids.add(entry["company_id"])
                    companies.append(entry)

            page += 1

        return companies

    def _parse_recruiter_anchor(self, anchor: Any) -> dict[str, Any] | None:
        href = anchor.get("href", "")
        company_id = _extract_company_id(href)
        if not company_id:
            return None

        img = anchor.find("img")
        name = _normalize_text(img.get("alt", "")) if img else ""
        logo_url = _absolute_url(img.get("src", "")) if img else ""

        return {
            "company_id": company_id,
            "company_url": _company_profile_url(company_id),
            "name": name,
            "logo_url": logo_url,
        }

    # ------------------------------------------------------------------
    # Profil entreprise
    # ------------------------------------------------------------------

    def _get_or_scrape_company(
        self,
        sb: SB,
        company_id: str,
        fallback_entry: dict[str, Any],
    ) -> dict[str, Any]:
        cached = self.company_store.get(company_id)
        if cached is not None:
            return {"is_new": False, "data": cached, "has_jobs": cached.get("has_jobs", True)}

        profile = self.scrape_company_detail(sb, _company_profile_url(company_id), fallback_entry)
        # La page entreprise est encore chargée — on vérifie le lien "Nos offres d'emploi"
        soup = self._page_soup(sb)
        has_jobs = bool(soup.select_one(".card-block-links a[href*='is_recruiter_nid']"))

        payload = asdict(profile)
        payload["has_jobs"] = has_jobs
        self.company_store.set(company_id, payload)
        if self.api_client:
            self.api_client.upsert_company(payload)
        return {"is_new": True, "data": payload, "has_jobs": has_jobs}

    def scrape_company_detail(
        self,
        sb: SB,
        company_url: str,
        fallback: dict[str, Any] | None = None,
    ) -> CompanyProfile:
        fb = fallback or {}
        for attempt in range(3):
            if attempt == 0:
                sb.goto(company_url)
            else:
                print("[REFRESH] Actualisation page entreprise")
                sb.refresh()
            try:
                self._wait_for_company(sb)
                self._maybe_solve_captcha(sb)
                break
            except Exception:
                print(f"[RETRY] Entreprise tentative {attempt + 1}/3 : {company_url}")
                self._maybe_solve_captcha(sb)
                sb.sleep(5)
        else:
            print(f"[WARN] Page entreprise introuvable : {company_url}")
            return CompanyProfile(
                company_id=_extract_company_id(company_url),
                company_url=company_url,
                name=fb.get("name", ""),
                city="", country="", sector="",
                website="", description="",
                logo_url=fb.get("logo_url", ""),
            )

        soup = self._page_soup(sb)
        fields = self._extract_company_fields(soup)
        logo_tag = soup.select_one(".card-block-company picture img")
        logo_url = logo_tag.get("src", "") if logo_tag else fb.get("logo_url", "")

        return CompanyProfile(
            company_id=_extract_company_id(company_url),
            company_url=company_url,
            name=fields.get("Entreprise", fb.get("name", "")),
            city=fields.get("Ville", ""),
            country=fields.get("Pays", ""),
            sector=fields.get("Secteur d´activité", ""),
            website=fields.get("Site Internet", ""),
            description=self._first_text(soup, ".card-block-company-description p"),
            logo_url=logo_url,
        )

    # ------------------------------------------------------------------
    # Offres d'une entreprise
    # ------------------------------------------------------------------

    def _scrape_company_jobs(
        self,
        sb: SB,
        company_id: str,
        company_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        base_url = _company_jobs_url(company_id)

        sb.goto(base_url)
        sb.sleep(2)
        try:
            sb.wait_for_element(".page-search-jobs-content", timeout=15)
        except Exception:
            self._maybe_solve_captcha(sb)
            # Aucune offre active pour cette entreprise
            return jobs

        soup = self._page_soup(sb)
        if not soup.select(".page-search-jobs-content .card.card-job"):
            return jobs

        total_pages = self._get_total_pages(soup)
        print(f"  [OFFRES] {total_pages} page(s)")

        page = 0
        while page < total_pages:
            if page > 0:
                sb.sleep(3)
                self._goto_with_retry(sb, f"{base_url}&page={page}", ".page-search-jobs-content")
                soup = self._page_soup(sb)

            for card in soup.select(".page-search-jobs-content .card.card-job"):
                listing = self._parse_listing_card(card)
                if listing is None:
                    continue

                if self._job_already_scraped(listing.job_id):
                    print(f"  [SKIP] Offre {listing.job_id} déjà présente")
                    continue

                detail = self.scrape_job_detail(sb, listing.job_url)
                listing.detail = detail
                job_payload = {**asdict(listing), "company_profile": company_data}
                jobs.append(job_payload)
                self.job_store.set(listing.job_id, job_payload)
                if self.api_client:
                    self.api_client.upsert_job(job_payload)

            page += 1

        return jobs

    # ------------------------------------------------------------------
    # Détail d'une offre
    # ------------------------------------------------------------------

    def scrape_job_detail(self, sb: SB, job_url: str) -> dict[str, Any]:
        for attempt in range(3):
            if attempt == 0:
                sb.goto(job_url)
            else:
                print("[REFRESH] Actualisation page détail")
                sb.refresh()
            try:
                self._wait_for_detail(sb)
                self._maybe_solve_captcha(sb)
                break
            except Exception:
                print(f"[RETRY] Détail tentative {attempt + 1}/3 : {job_url}")
                self._maybe_solve_captcha(sb)
                sb.sleep(5)
        else:
            print(f"[WARN] Page détail introuvable : {job_url}")
            return {
                "job_url": job_url, "headline": "", "description": "",
                "qualifications": [], "criteria": {}, "skills": [], "sections": [],
            }

        soup = self._page_soup(sb)
        return {
            "job_url": job_url,
            "headline": self._first_text(soup, "h3.job-title"),
            "description": self._html_text(soup.select_one(".job-description")),
            "qualifications": [
                _normalize_text(item.get_text(" ", strip=True))
                for item in soup.select(".job-qualifications li")
                if _normalize_text(item.get_text(" ", strip=True))
            ],
            "criteria": self._extract_criteria(soup),
            "skills": [
                _normalize_text(item.get_text(" ", strip=True))
                for item in soup.select("ul.skills li")
                if _normalize_text(item.get_text(" ", strip=True))
            ],
            "sections": self._extract_job_sections(soup),
        }

    # ------------------------------------------------------------------
    # Parsing HTML
    # ------------------------------------------------------------------

    def _parse_listing_card(self, card: Any) -> JobListing | None:
        job_anchor = card.select_one("h3 a")
        if job_anchor is None:
            return None

        company_anchor = card.select_one("a.card-job-company.company-name")
        company_name = _normalize_text(company_anchor.get_text(" ", strip=True)) if company_anchor else ""
        job_url = _absolute_url(job_anchor.get("href"))
        company_url = _absolute_url(company_anchor.get("href")) if company_anchor else ""
        company_id = _extract_company_id(company_url)
        job_id = self._extract_job_id(job_url)

        return JobListing(
            job_id=job_id,
            title=_normalize_text(job_anchor.get_text(" ", strip=True)),
            job_url=job_url,
            company_name=company_name,
            company_url=company_url,
            company_id=company_id,
            detail={},
        )

    def _extract_job_id(self, job_url: str) -> str:
        match = re.search(r"-(\d+)$", job_url)
        return match.group(1) if match else job_url

    def _extract_criteria(self, soup: BeautifulSoup) -> dict[str, str]:
        criteria: dict[str, str] = {}
        for item in soup.select("ul.arrow-list > li"):
            key = item.find("strong")
            value = item.find("span")
            if key is None or value is None:
                continue
            label = _normalize_text(key.get_text(" ", strip=True)).rstrip(" :")
            criteria[label] = _normalize_text(value.get_text(" ", strip=True))
        return criteria

    def _extract_job_sections(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for section in soup.select(".card-block-content > section"):
            sections.append({
                "heading": self._first_text(section, "h3.job-title"),
                "description": self._html_text(section.select_one(".job-description")),
                "qualifications": [
                    _normalize_text(item.get_text(" ", strip=True))
                    for item in section.select(".job-qualifications li")
                    if _normalize_text(item.get_text(" ", strip=True))
                ],
                "criteria": self._extract_criteria(section),
                "skills": [
                    _normalize_text(item.get_text(" ", strip=True))
                    for item in section.select("ul.skills li")
                    if _normalize_text(item.get_text(" ", strip=True))
                ],
            })
        return sections

    def _extract_company_fields(self, soup: BeautifulSoup) -> dict[str, str]:
        fields: dict[str, str] = {}
        for item in soup.select(".card-block-company li"):
            key = item.find("strong")
            value = item.find("span")
            if key is None or value is None:
                continue
            label = _normalize_text(key.get_text(" ", strip=True)).rstrip(":")
            fields[label] = _normalize_text(value.get_text(" ", strip=True))
        return fields

    # ------------------------------------------------------------------
    # Navigation / attentes
    # ------------------------------------------------------------------

    def _goto_with_retry(self, sb: SB, url: str, wait_selector: str, retries: int = 5) -> None:
        for attempt in range(retries):
            if attempt == 0:
                sb.goto(url)
            else:
                sb.refresh()
            try:
                sb.wait_for_element(wait_selector, timeout=30)
                return
            except Exception:
                print(f"[RETRY] {attempt + 1}/{retries} — {url}")
                self._maybe_solve_captcha(sb)
                sb.sleep(8)
        raise RuntimeError(f"Élément '{wait_selector}' introuvable après {retries} tentatives : {url}")

    def _get_total_pages(self, soup: BeautifulSoup) -> int:
        last_link = soup.select_one("li.pager-item a[title='Aller à la dernière page']")
        if last_link is None:
            return 1
        href = last_link.get("href", "")
        match = re.search(r"[?&]page=(\d+)", str(href))
        return int(match.group(1)) + 1 if match else 1

    def _job_already_scraped(self, job_id: str) -> bool:
        if self.api_client:
            return self.api_client.job_exists(job_id)
        return self.job_store.has(job_id)

    def _page_soup(self, sb: SB) -> BeautifulSoup:
        return BeautifulSoup(sb.get_page_source(), "html.parser")

    def _wait_for_detail(self, sb: SB) -> None:
        sb.wait_for_element(".card-block-content", timeout=20)

    def _wait_for_company(self, sb: SB) -> None:
        sb.wait_for_element(".card-block-company", timeout=20)

    def _maybe_solve_captcha(self, sb: SB) -> None:
        try:
            sb.solve_captcha()
        except Exception:
            pass

    def _html_text(self, element: Any | None) -> str:
        if element is None:
            return ""
        return _normalize_text(element.get_text(" ", strip=True))

    def _first_text(self, soup: BeautifulSoup, selector: str) -> str:
        element = soup.select_one(selector)
        return _normalize_text(element.get_text(" ", strip=True)) if element else ""

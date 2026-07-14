from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from seleniumbase import SB


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_URL = "https://www.emploi.cg"
LISTING_URL = f"{BASE_URL}/recherche-jobs-congo-brazzaville"
PROFILE_DIR = PROJECT_ROOT / "my_custom_profile"
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
JOBS_FILE = OUTPUT_DIR / "jobs.json"
COMPANIES_FILE = OUTPUT_DIR / "companies.json"


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _extract_company_id(url: str) -> str:
    match = re.search(r"/recruteur/(\d+)", url)
    return match.group(1) if match else ""


def _absolute_url(href: str | None) -> str:
    return urljoin(BASE_URL, href or "")


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


class JsonStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.file_path.exists():
            return {}

        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

        if isinstance(payload, dict):
            return {str(key): dict(value) for key, value in payload.items() if isinstance(value, dict)}

        return {}

    def save(self) -> None:
        self.file_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def has(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value
        self.save()

    def count(self) -> int:
        return len(self._data)


class ApiClient:
    """Envoie les données scrappées vers le backend Node.js."""

    def __init__(self, base_url: str) -> None:
        import requests as _requests
        self._requests = _requests
        self.base_url = base_url.rstrip("/")

    def upsert_job(self, job: dict[str, Any]) -> None:
        try:
            resp = self._requests.post(
                f"{self.base_url}/api/jobs",
                json=job,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"[API] Erreur job {job.get('job_id')}: {exc}")

    def upsert_company(self, company: dict[str, Any]) -> None:
        try:
            resp = self._requests.post(
                f"{self.base_url}/api/companies",
                json=company,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"[API] Erreur entreprise {company.get('company_id')}: {exc}")

    def job_exists(self, job_id: str) -> bool:
        try:
            resp = self._requests.get(
                f"{self.base_url}/api/jobs/{job_id}",
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


class EmploiMaScraper:
    def __init__(self, api_url: str | None = None) -> None:
        self.company_store = JsonStore(COMPANIES_FILE)
        self.job_store = JsonStore(JOBS_FILE)
        self.api_client = ApiClient(api_url) if api_url else None

    def scrape_listings(self) -> dict[str, Any]:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        jobs: list[dict[str, Any]] = []
        new_companies: list[dict[str, Any]] = []

        with SB(
            uc=True,
            locale="en",
            user_data_dir=str(PROFILE_DIR),
            disable_js=False,
        ) as sb:
            sb.activate_cdp_mode()

            # Chargement de la première page avec retry
            self._goto_listing_page(sb, LISTING_URL)
            soup = self._page_soup(sb)
            total_pages = self._get_total_pages(soup)
            print(f"Pages détectées : {total_pages}")

            page = 0
            while page < total_pages:
                print(f"[PAGE] {page + 1}/{total_pages}")

                if page > 0:
                    page_url = f"{LISTING_URL}?page={page}"
                    sb.sleep(3)
                    self._goto_listing_page(sb, page_url)
                    soup = self._page_soup(sb)

                for card in soup.select(".page-search-jobs-content .card.card-job"):
                    listing = self._parse_listing_card(card)
                    if listing is None:
                        continue

                    if self._job_already_scraped(listing.job_id):
                        print(f"[SKIP] Offre {listing.job_id} déjà présente, ignorée")
                        continue

                    detail = self.scrape_job_detail(sb, listing.job_url)
                    company_result = self._get_or_scrape_company(
                        sb, listing.company_id, listing.company_url
                    )

                    listing.detail = detail
                    job_payload = {
                        **asdict(listing),
                        "company_profile": company_result["data"],
                    }
                    jobs.append(job_payload)
                    self.job_store.set(listing.job_id, job_payload)
                    if self.api_client:
                        self.api_client.upsert_job(job_payload)

                    if company_result["is_new"]:
                        new_companies.append(company_result["data"])

                page += 1

        return {
            "jobs": jobs,
            "new_companies": new_companies,
        }

    def scrape_job_detail(self, sb: SB, job_url: str) -> dict[str, Any]:
        for attempt in range(3):
            if attempt == 0:
                sb.goto(job_url)
            else:
                print(f"[REFRESH] Actualisation de la page d\u00e9tail")
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
            print(f"[WARN] Page détail introuvable, données vides : {job_url}")
            return {
                "job_url": job_url,
                "headline": "",
                "description": "",
                "qualifications": [],
                "criteria": {},
                "skills": [],
                "sections": [],
            }

        soup = self._page_soup(sb)
        sections = self._extract_job_sections(soup)

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
            "sections": sections,
        }

    def _get_or_scrape_company(
        self,
        sb: SB,
        company_id: str,
        company_url: str,
    ) -> dict[str, Any]:
        cached = self.company_store.get(company_id)
        if cached is not None:
            return {"is_new": False, "data": cached}

        company_profile = self.scrape_company_detail(sb, company_url)
        payload = asdict(company_profile)
        self.company_store.set(company_id, payload)
        if self.api_client:
            self.api_client.upsert_company(payload)
        return {"is_new": True, "data": payload}

    def scrape_company_detail(self, sb: SB, company_url: str) -> CompanyProfile:
        for attempt in range(3):
            if attempt == 0:
                sb.goto(company_url)
            else:
                print(f"[REFRESH] Actualisation de la page entreprise")
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
            print(f"[WARN] Page entreprise introuvable, données vides : {company_url}")
            return CompanyProfile(
                company_id=_extract_company_id(company_url),
                company_url=company_url,
                name="", city="", country="", sector="",
                website="", description="", logo_url="",
            )

        soup = self._page_soup(sb)
        company_fields = self._extract_company_fields(soup)
        logo = soup.select_one(".card-block-company picture img")

        return CompanyProfile(
            company_id=_extract_company_id(company_url),
            company_url=company_url,
            name=company_fields.get("Entreprise", ""),
            city=company_fields.get("Ville", ""),
            country=company_fields.get("Pays", ""),
            sector=company_fields.get("Secteur d´activité", ""),
            website=company_fields.get("Site Internet", ""),
            description=self._first_text(soup, ".card-block-company-description p"),
            logo_url=logo.get("src", "") if logo else "",
        )

    def _parse_listing_card(self, card: Any) -> JobListing | None:
        company_anchor = card.select_one("a.card-job-company.company-name")
        if company_anchor is None:
            return None

        company_name = _normalize_text(company_anchor.get_text(" ", strip=True))
        if company_name.upper() == "N.C.":
            return None

        job_anchor = card.select_one("h3 a")
        if job_anchor is None:
            return None

        job_url = _absolute_url(job_anchor.get("href"))
        company_url = _absolute_url(company_anchor.get("href"))
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

    def _goto_listing_page(self, sb: SB, url: str, retries: int = 5) -> None:
        for attempt in range(retries):
            if attempt == 0:
                sb.goto(url)
            else:
                print(f"[REFRESH] Actualisation de la page listing")
                sb.refresh()
            try:
                self._wait_for_listing(sb)
                return
            except Exception:
                print(f"[RETRY] Tentative {attempt + 1}/{retries} - captcha possible")
                self._maybe_solve_captcha(sb)
                sb.sleep(8)
        raise RuntimeError(f"Page listing introuvable apr\u00e8s {retries} tentatives : {url}")

    def _get_total_pages(self, soup: BeautifulSoup) -> int:
        last_link = soup.select_one(
            "li.pager-item a[title='Aller à la dernière page']"
        )
        if last_link is None:
            return 1
        href = last_link.get("href", "")
        match = re.search(r"[?&]page=(\d+)", str(href))
        return int(match.group(1)) + 1 if match else 1

    def _job_already_scraped(self, job_id: str) -> bool:
        if self.api_client:
            return self.api_client.job_exists(job_id)
        return self.job_store.has(job_id)

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
            section_data: dict[str, Any] = {
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
            }
            sections.append(section_data)
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

    def _page_soup(self, sb: SB) -> BeautifulSoup:
        return BeautifulSoup(sb.get_page_source(), "html.parser")

    def _wait_for_listing(self, sb: SB) -> None:
        sb.wait_for_element(".page-search-jobs-content", timeout=30)

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
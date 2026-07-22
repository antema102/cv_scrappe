from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from bs4 import BeautifulSoup
from seleniumbase import SB

from scrapers.emploi_scraper import ApiClient, JsonStore

from .browser_helpers import maybe_solve_captcha
from .country_config import PROFILE_DIR, OUTPUT_DIR, CountryConfig
from .models import CompanyProfile
from .parsers import (
    build_company_profile,
    build_job_detail,
    get_total_pages,
    parse_listing_card,
    parse_recruiter_anchor,
)


class CloudflareBlockError(Exception):
    """Levée quand Cloudflare bloque la navigation après plusieurs tentatives."""

    def __init__(self, url: str) -> None:
        super().__init__(f"Cloudflare bloque après plusieurs tentatives : {url}")
        self.url = url


class BaseJobScraper:
    def __init__(self, config: CountryConfig, api_url: str | None = None) -> None:
        self.config = config
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.company_store = JsonStore(config.companies_file)
        self.job_store = JsonStore(config.jobs_file)
        self.api_client = ApiClient(api_url) if api_url else None

    # ------------------------------------------------------------------
    # Point d'entrée
    # ------------------------------------------------------------------

    def scrape_all(self) -> dict[str, Any]:
        all_jobs: list[dict[str, Any]] = []
        all_new_companies: list[dict[str, Any]] = []

        while True:
            try:
                with SB(
                    uc=True,
                    locale="en",
                    user_data_dir=str(PROFILE_DIR),
                    disable_js=False,
                    headless=True,
                ) as sb:
                    sb.activate_cdp_mode()
                    self._run_session(sb, all_jobs, all_new_companies)
                break
            except CloudflareBlockError as e:
                print(f"\n[CLOUDFLARE] Blocage détecté — {e.url}")
                print("[CLOUDFLARE] Navigateur fermé. Reprise dans 5 minutes...\n")
                time.sleep(300)
                print("[CLOUDFLARE] Reprise du scraping...")

        return {"jobs": all_jobs, "new_companies": all_new_companies}

    def _run_session(
        self,
        sb: SB,
        all_jobs: list[dict[str, Any]],
        all_new_companies: list[dict[str, Any]],
    ) -> None:
        companies = self._scrape_recruiter_list(sb)
        print(f"[{self.config.code.upper()}] {len(companies)} entreprises détectées")

        for idx, entry in enumerate(companies, 1):
            company_id = entry["company_id"]
            print(
                f"  [ENTREPRISE {idx}/{len(companies)}] "
                f"{entry['name']} (id={company_id})"
            )

            company_result = self._get_or_scrape_company(sb, company_id, entry)
            if company_result["is_new"]:
                all_new_companies.append(company_result["data"])

            if not company_result["has_jobs"]:
                print(f"    [SKIP] Pas d'offres pour {entry['name']}")
                continue

            jobs = self._scrape_company_jobs(sb, company_id, company_result["data"])
            all_jobs.extend(jobs)

    # ------------------------------------------------------------------
    # Liste des recruteurs
    # ------------------------------------------------------------------

    def _scrape_recruiter_list(self, sb: SB) -> list[dict[str, Any]]:
        companies: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        self._goto_with_retry(sb, self.config.recruiters_url, ".card-block-content")
        soup = self._get_soup(sb)
        total_pages = get_total_pages(soup)
        print(f"  [RECRUTEURS] {total_pages} page(s)")

        page = 0
        while page < total_pages:
            if page > 0:
                sb.sleep(3)
                url = f"{self.config.recruiters_url}?page={page}"
                self._goto_with_retry(sb, url, ".card-block-content")
                soup = self._get_soup(sb)

            for anchor in soup.select(".card-block-content a[href]"):
                entry = parse_recruiter_anchor(anchor, self.config)
                if entry and entry["company_id"] not in seen_ids:
                    seen_ids.add(entry["company_id"])
                    companies.append(entry)

            page += 1

        return companies

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

        profile = self._scrape_company_detail(sb, company_id, fallback_entry)
        soup = self._get_soup(sb)
        has_jobs = bool(soup.select_one(".card-block-links a[href*='is_recruiter_nid']"))

        payload = asdict(profile)
        payload["has_jobs"] = has_jobs
        self.company_store.set(company_id, payload)
        if self.api_client:
            self.api_client.upsert_company(payload)
        return {"is_new": True, "data": payload, "has_jobs": has_jobs}

    def _scrape_company_detail(
        self,
        sb: SB,
        company_id: str,
        fallback: dict[str, Any] | None = None,
    ) -> CompanyProfile:
        fb = fallback or {}
        company_url = self.config.company_profile_url(company_id)

        for attempt in range(3):
            if attempt == 0:
                sb.goto(company_url)
            else:
                print("    [REFRESH] Actualisation page entreprise")
                sb.refresh()
            try:
                sb.wait_for_element(".card-block-company", timeout=20)
                maybe_solve_captcha(sb)
                break
            except Exception:
                print(f"    [RETRY] Entreprise {attempt + 1}/3 : {company_url}")
                maybe_solve_captcha(sb)
                sb.sleep(5)
        else:
            print(f"    [WARN] Page entreprise introuvable : {company_url}")
            return CompanyProfile(
                company_id=company_id,
                company_url=company_url,
                name=fb.get("name", ""),
                city="", country="", sector="",
                website="", description="",
                logo_url=fb.get("logo_url", ""),
            )

        return build_company_profile(self._get_soup(sb), company_url, fb, self.config)

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
        base_url = self.config.company_jobs_url(company_id)

        sb.goto(base_url)
        sb.sleep(2)
        try:
            sb.wait_for_element(".page-search-jobs-content", timeout=15)
        except Exception:
            maybe_solve_captcha(sb)
            return jobs

        soup = self._get_soup(sb)
        if not soup.select(".page-search-jobs-content .card.card-job"):
            return jobs

        total_pages = get_total_pages(soup)
        print(f"    [OFFRES] {total_pages} page(s)")

        page = 0
        while page < total_pages:
            if page > 0:
                sb.sleep(3)
                self._goto_with_retry(sb, f"{base_url}&page={page}", ".page-search-jobs-content")
                soup = self._get_soup(sb)

            for card in soup.select(".page-search-jobs-content .card.card-job"):
                listing = parse_listing_card(card, self.config)
                if listing is None:
                    continue
                if self._job_already_scraped(listing.job_id):
                    print(f"    [SKIP] Offre {listing.job_id} déjà présente")
                    continue

                detail = self._scrape_job_detail(sb, listing.job_url)
                listing.detail = detail
                job_payload = {**asdict(listing), "company_profile": company_data}
                jobs.append(job_payload)
                self.job_store.set(listing.job_id, job_payload)
                if self.api_client:
                    self.api_client.upsert_job(job_payload)
                self._save_job_publication(listing.job_id, detail.get("published_at"))

            page += 1

        return jobs

    # ------------------------------------------------------------------
    # Détail d'une offre
    # ------------------------------------------------------------------

    def _scrape_job_detail(self, sb: SB, job_url: str) -> dict[str, Any]:
        for attempt in range(3):
            if attempt == 0:
                sb.goto(job_url)
            else:
                print("    [REFRESH] Actualisation page détail")
                sb.refresh()
            try:
                sb.wait_for_element(".card-block-content", timeout=20)
                maybe_solve_captcha(sb)
                break
            except Exception:
                print(f"    [RETRY] Détail {attempt + 1}/3 : {job_url}")
                maybe_solve_captcha(sb)
                sb.sleep(5)
        else:
            print(f"    [WARN] Page détail introuvable : {job_url}")
            return {
                "job_url": job_url, "headline": "", "description": "",
                "qualifications": [], "criteria": {}, "skills": [], "sections": [],
                "published_at": None,
            }

        return build_job_detail(self._get_soup(sb), job_url)
    
    # ------------------------------------------------------------------
    # Publications d'une offre
    # ------------------------------------------------------------------

    def _save_job_publication(self, job_id: str, published_at: str | None) -> None:
        """Enregistre la date de publication d'une offre dans le backend."""
        if self.api_client:
            self.api_client.upsert_job_publication(job_id, published_at)
    # ------------------------------------------------------------------
    # Helpers navigation
    # ------------------------------------------------------------------

    def _goto_with_retry(self, sb: SB, url: str, wait_selector: str, retries: int = 3) -> None:
        for attempt in range(retries):
            if attempt == 0:
                sb.goto(url)
            else:
                sb.refresh()
            try:
                sb.wait_for_element(wait_selector, timeout=20)
                return
            except Exception:
                print(f"    [RETRY] {attempt + 1}/{retries} — {url}")
                maybe_solve_captcha(sb)
                sb.sleep(8)
        raise CloudflareBlockError(url)

    def _get_soup(self, sb: SB) -> BeautifulSoup:
        return BeautifulSoup(sb.get_page_source(), "html.parser")

    def _job_already_scraped(self, job_id: str) -> bool:
        if self.api_client:
            return self.api_client.job_exists(job_id)
        return self.job_store.has(job_id)

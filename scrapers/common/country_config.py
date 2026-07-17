from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # cv_scrappe/
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
PROFILE_DIR = PROJECT_ROOT / "my_custom_profile"
PROFILE_DIR_CV = PROJECT_ROOT / "my_custom_profile_2"


@dataclass(frozen=True, slots=True)
class CvDownloadPattern:
    name: str
    template: str
    start: int
    end: int | None = None
    miss_threshold: int = 10


@dataclass(frozen=True)
class CountryConfig:
    code: str
    base_url: str
    jobs_search_path: str
    recruiters_path: str = "/recruteurs"
    recruiter_path_prefix: str = "/recruteur/"
    cv_storage_path: str = "/sites/default/files/private/cv/"
    cv_patterns: tuple[CvDownloadPattern, ...] = (
        CvDownloadPattern(name="cv_*.pdf", template="cv_{}.pdf", start=0, miss_threshold=10),
        CvDownloadPattern(name="mon_cv_*.pdf", template="mon_cv_{}.pdf", start=0, miss_threshold=10),
        CvDownloadPattern(name="cv*.pdf", template="cv{}.pdf", start=1, end=30),
    )

    # ------------------------------------------------------------------
    # URLs dérivées
    # ------------------------------------------------------------------

    @property
    def recruiters_url(self) -> str:
        return f"{self.base_url}{self.recruiters_path}"

    @property
    def jobs_search_base(self) -> str:
        return f"{self.base_url}{self.jobs_search_path}"

    @property
    def cv_base_url(self) -> str:
        normalized_base = self.base_url.rstrip("/")
        normalized_path = "/" + self.cv_storage_path.strip("/") + "/"
        return f"{normalized_base}{normalized_path}"

    @property
    def jobs_file(self) -> Path:
        return OUTPUT_DIR / f"jobs_{self.code}.json"

    @property
    def companies_file(self) -> Path:
        return OUTPUT_DIR / f"companies_{self.code}.json"

    # ------------------------------------------------------------------
    # Constructeurs d'URL
    # ------------------------------------------------------------------

    def company_profile_url(self, company_id: str) -> str:
        return f"{self.base_url}{self.recruiter_path_prefix}{company_id}"

    def company_jobs_url(self, company_id: str) -> str:
        return f"{self.jobs_search_base}?fq%5B0%5D=is_recruiter_nid%3A{company_id}"

    def absolute_url(self, href: str | None) -> str:
        return urljoin(self.base_url, href or "")

    # ------------------------------------------------------------------
    # Extraction d'ID
    # ------------------------------------------------------------------

    def extract_company_id(self, url: str) -> str:
        prefix = re.escape(self.recruiter_path_prefix)
        match = re.search(rf"{prefix}(\d+)", url)
        if match:
            return match.group(1)
        match = re.search(r"is_recruiter_nid(?:%3A|:)(\d+)", url)
        if match:
            return match.group(1)
        return ""

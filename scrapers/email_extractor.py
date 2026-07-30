"""
email_extractor.py
==================
Extrait automatiquement les emails d'entreprises via le Mode IA de Google Search.
Compatible Shadow DOM (Google AI Mode utilise des shadow roots ouverts).

Utilisation :
    python -m scrapers.email_extractor                        # toutes les entreprises
    python -m scrapers.email_extractor --country senegal      # un pays
    python -m scrapers.email_extractor --limit 20             # 20 premières
    python -m scrapers.email_extractor --company-id abc123    # une seule entreprise
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from selenium.common.exceptions import StaleElementReferenceException
from seleniumbase import SB
from scrapers.common.browser_helpers import maybe_solve_captcha
from scrapers.emploi_scraper import JsonStore

# ---------------------------------------------------------------------------
# Chemins & configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
PROFILE_DIR = PROJECT_ROOT / "my_custom_profile_3"
FALLBACK_PROFILE = PROJECT_ROOT / "my_custom_profile_1"
EMAILS_FILE = OUTPUT_DIR / "company_emails.json"

# URL du backend — surchargeable via variable d'environnement SCRAPER_API_URL
BACKEND_URL: str = os.getenv("SCRAPER_API_URL", "http://localhost:3500")

GOOGLE_AI_MODE_URL = "https://www.google.com/search?udm=50&hl=fr"

DELAY_BETWEEN_SEARCHES: float = 0
MAX_RETRIES: int = 3

# Taille d'une page lors de la récupération paginée des entreprises depuis le
# backend (celui-ci plafonne à 10000 résultats par requête en mode ?scrape=1 —
# voir backend/src/routes/companies.routes.ts). CompanyBatchFetcher boucle sur
# autant de pages que nécessaire pour tout récupérer, sans jamais matérialiser
# l'ensemble en mémoire (un batch = une page = une session navigateur).
FETCH_PAGE_SIZE: int = 5000
FETCH_MAX_RETRIES: int = 3

# Retry réseau intelligent : backoff exponentiel plafonné + jitter, uniquement
# pour les erreurs transitoires (timeout, connexion, 429, 500, 502, 503, 504).
# Les erreurs définitives (401, 403, 404, ...) échouent immédiatement.
RETRY_BASE_DELAY: float = 1.0
RETRY_MAX_DELAY: float = 30.0
RETRY_JITTER: float = 0.5
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Progression persistée (reprise après interruption) — un fichier par job_id,
# clé = "{country}::{page_size}" (voir CompanyBatchFetcher).
PROGRESS_DIR = OUTPUT_DIR

# ---------------------------------------------------------------------------
# Regex email
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b')
# Numéros de téléphone : séquence de chiffres avec séparateurs optionnels, 8–16 chiffres au total
_PHONE_RE = re.compile(r'(?<!\w)\+?[\d][\d\s()\-./]{6,20}[\d](?!\w)')

_FAKE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js",
    ".webp", ".ico", ".pdf", ".zip",
})
_BLACKLISTED_DOMAINS = frozenset({
    "example.com", "test.com", "sentry.io", "wixpress.com",
    "schema.org", "w3.org", "openxmlformats.org",
})
_BLACKLISTED_PREFIXES = frozenset({
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer", "bounce", "postmaster",
})

# Pré-filtre grossier utilisé par run() avant même d'appeler process_company()
# (qui applique sa propre liste, légèrement plus large, sur chaque entreprise).
_PREFILTER_INVALID_WEBSITE_VALUES = frozenset({
    "", "non disponible", "non disponible.", "n/a", "na", "null", "none",
})

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sélecteurs — regroupés ici pour faciliter la maintenance
# ---------------------------------------------------------------------------

# Zone de saisie — aria/placeholder/role d'abord, classes ensuite
_INPUT_CSS: list[str] = [
    "textarea[placeholder='Posez une question']",
    "div.Txyg0d > textarea",
    "textarea",
]

# Conteneur réponse IA — role/aria d'abord, classes ensuite
_RESPONSE_CSS: list[str] = [
    "div.mZJni.Dn7Fzd",  # conteneur réponse IA (confirmé)
]
# Sélecteur unique du conteneur de réponse — utilisé pour find_elements() + WebElement direct
_RESPONSE_SEL: str = _RESPONSE_CSS[0]
# Indicateurs de chargement IA (leur disparition = streaming terminé)
_LOADING_CSS: list[str] = [
    ".EHAKZe",
    "[data-is-streaming='true']",
    "[aria-label*='chargement']",
    "[aria-label*='loading']",
    " ",
]

# Bannière de consentement Google
_CONSENT_CSS: list[str] = [
    "button#L2AGLb",
    "button[aria-label='Tout accepter']",
    "button[aria-label='Accept all']",
    ".QS5gu.sy4vM",
]

class _SkippedSentinel:
    """Valeur retournée par process_company() quand l'entreprise est ignorée volontairement.
    Distincte de None (= vrai échec de traitement) pour ne pas polluer les compteurs d'échecs."""
    __slots__ = ()
    def __repr__(self) -> str:
        return "SKIPPED"

_SKIPPED = _SkippedSentinel()

# ---------------------------------------------------------------------------
# EmailStore — cache JSON (pattern JsonStore du projet)
# ---------------------------------------------------------------------------
class EmailStore:
    """Stockage JSON des résultats d'emails — clé = company_id."""

    def __init__(self, file_path: Path = EMAILS_FILE) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.file_path.exists():
            return {}
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return {str(k): dict(v) for k, v in payload.items() if isinstance(v, dict)}

    def save(self) -> None:
        self.file_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def has(self, company_id: str) -> bool:
        return company_id in self._data

    def get(self, company_id: str) -> dict[str, Any] | None:
        return self._data.get(company_id)

    def set(self, company_id: str, result: dict[str, Any]) -> None:
        self._data[company_id] = result
        self.save()

    def count(self) -> int:
        return len(self._data)

# ---------------------------------------------------------------------------
# GoogleAiEmailExtractor
# ---------------------------------------------------------------------------
class GoogleAiEmailExtractor:
    """
    Extrait les emails d'entreprises en interrogeant le Mode IA de Google.
    Compatible Shadow DOM (Google AI Mode utilise des shadow roots ouverts).

    Flux par entreprise :
      1. open_ai_mode()     → navigate vers udm=50
      2. find_ai_input()    → détecte la zone de saisie (shadow DOM inclus)
      3. send_query()       → saisit "[nom] email" et envoie
      4. wait_for_response()→ attend la fin du streaming
      5. extract_response() → récupère le texte (shadow DOM inclus)
      6. extract_emails()   → regex email
      7. save_result()      → JsonStore
    """

    def __init__(
        self,
        store: EmailStore,
        delay: float = DELAY_BETWEEN_SEARCHES,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.store = store
        self.delay = delay
        self.max_retries = max_retries


    # ------------------------------------------------------------------
    # Vérification AI Mode
    # ------------------------------------------------------------------

    def _is_in_ai_mode(self, sb: SB) -> bool:
        """Retourne True si le navigateur est déjà sur Google AI Mode."""
        try:
            return "udm=50" in sb.get_current_url()
        except Exception:
            return False

    def _ensure_ai_mode(self, sb: SB) -> None:
        """
        Vérifie qu'on est toujours sur AI Mode.
        Si on en a été éjecté (captcha, erreur...), retour direct par URL.
        Ne passe JAMAIS par Google Home.
        """
        if self._is_in_ai_mode(sb):
            return
        log.info("Plus en AI Mode — retour direct via URL")
        sb.open(GOOGLE_AI_MODE_URL)
        sb.wait_for_ready_state_complete()
        maybe_solve_captcha(sb)
        self._dismiss_consent(sb)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def open_ai_mode(self, sb: SB) -> None:
        """
        Ouvre Google AI Mode.
        Tente d'abord le clic sur le bouton depuis la home,
        puis navigue directement vers udm=50 si nécessaire.
        """

        log.info("Navigation directe vers Google AI Mode")
        sb.open(GOOGLE_AI_MODE_URL)
        sb.wait_for_ready_state_complete()
        maybe_solve_captcha(sb)
        self._dismiss_consent(sb)
            
    def _dismiss_consent(self, sb: SB) -> None:
        """Ferme la bannière de consentement Google si présente."""
        for sel in _CONSENT_CSS:
            try:
                sb.wait_for_element_visible(sel, timeout=3)
                sb.click(sel)
                sb.wait_for_ready_state_complete()
                log.info("Consentement accepté")
                return
            except Exception:
                continue


    def send_query(self, sb: SB, query: str, timeout: int = 15) -> bool:
        """
        Trouve le textarea actif de Google AI Mode et envoie la requête.
        Ne retourne pas un sélecteur : utilise directement le WebElement trouvé.
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                sb.wait_for_ready_state_complete()
                for sel in _INPUT_CSS:
                    elements = sb.find_elements(sel)

                    for element in elements:
                        try:
                            if (
                                element.is_displayed()
                                and element.is_enabled()
                                and element.size["width"] > 0
                            ):

                                element.click()
                                element.clear()
                                element.send_keys(query)
                                element.send_keys("\ue007")

                                return True

                        except Exception as e:
                            print(
                                "DEBUG: élément ignoré:",
                                e
                            )
                            continue

            except Exception as e:
                print(
                    "DEBUG recherche input:",
                    e
                )

            sb.sleep(1)

        log.error(
            "Impossible d'envoyer la requête : %r",
            query
        )

        return False

    # ------------------------------------------------------------------
    # Attente de la réponse IA
    # ------------------------------------------------------------------

    def wait_for_response(self, sb: SB, previous_count: int, max_wait: int = 10) -> Any | None:
        """
        Attend l'apparition d'un NOUVEAU conteneur de réponse.
        Compare le nombre courant d'éléments _RESPONSE_SEL avec `previous_count`.
        Retourne le dernier WebElement (réponse à la dernière requête), ou None si timeout.
        """
        # Étape 1 : attendre qu'un nouvel élément apparaisse
        deadline = time.time() + max_wait
        response_element: Any | None = None

        while time.time() < deadline:
            try:
                elements = sb.find_elements(_RESPONSE_SEL)
                if len(elements) > previous_count:
                    response_element = elements[-1]
                    log.info(
                        "Nouveau conteneur détecté (count %d → %d)",
                        previous_count, len(elements),
                    )
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if response_element is None:
            log.warning(
                "Aucun nouveau conteneur de réponse apparu (timeout %ds)", max_wait
            )
            return None

        # Étape 2 : disparition des indicateurs de chargement
        for sel in _LOADING_CSS:
            try:
                sb.wait_for_element_absent(sel, timeout=30)
                log.info("Chargement terminé (%s absent)", sel)
                break
            except Exception:
                continue

        # Étape 3 : stabilité du texte sur CE WebElement précis
        response_element = self._wait_element_text_stable(
            sb, response_element, max_wait=max_wait
        )
        return response_element

    def _wait_element_text_stable(
        self,
        sb: SB,
        element: Any,
        max_wait: int = 10,
        stable_for: float = 3.0,
        poll: float = 1.0,
    ) -> Any:
        """
        Attend que le texte d'un WebElement précis cesse de changer.
        Opère sur l'élément directement (pas via sélecteur CSS) pour éviter
        de lire le mauvais élément en cas de multiples réponses dans la page.
        Gère StaleElementReferenceException en récupérant le dernier élément.
        Le sleep ici est intentionnel : nécessaire pour le polling.
        Retourne l'élément courant (potentiellement re-fetché).
        """
        prev = ""
        stable_elapsed = 0.0
        total_elapsed = 0.0
        current_element = element

        while total_elapsed < max_wait:
            try:
                current = current_element.text
            except StaleElementReferenceException:
                # Google a remplacé le nœud DOM — récupérer le dernier élément
                try:
                    elements = sb.find_elements(_RESPONSE_SEL)
                    if elements:
                        current_element = elements[-1]
                        current = current_element.text
                    else:
                        current = ""
                except Exception:
                    current = ""
            except Exception:
                current = ""

            if current and current == prev:
                stable_elapsed += poll
                if stable_elapsed >= stable_for:
                    log.info("Réponse stable (%.1fs inchangée)", stable_elapsed)
                    return current_element
            else:
                stable_elapsed = 0.0
                prev = current

            time.sleep(poll)
            total_elapsed += poll

        log.warning("Timeout stabilité texte (%ds)", max_wait)
        return current_element

    # ------------------------------------------------------------------
    # Extraction de la réponse
    # ------------------------------------------------------------------

    def extract_response(self, sb: SB, response_element: Any | None) -> str:
        """
        Récupère le texte brut depuis le WebElement précis de la dernière réponse IA.
        Utilise element.text directement — jamais sb.get_text("div.mZJni.Dn7Fzd") —
        pour garantir que c'est bien la réponse correspondant à la dernière requête.
        Gère StaleElementReferenceException en récupérant le dernier élément connu.
        """
        if response_element is None:
            log.warning("Aucun élément de réponse fourni")
            return ""

        try:
            text = response_element.text
            if text and len(text) > 30:
                return text[:5000]
        except StaleElementReferenceException:
            try:
                elements = sb.find_elements(_RESPONSE_SEL)
                if elements:
                    text = elements[-1].text
                    if text and len(text) > 30:
                        return text[:5000]
            except Exception:
                pass
        except Exception:
            pass

        log.warning("Impossible d'extraire le texte de la réponse IA")
        return ""

    # ------------------------------------------------------------------
    # Parsing email / site web
    # ------------------------------------------------------------------

    def extract_emails(self, text: str) -> list[str]:
        """Extrait et filtre les adresses email depuis un texte brut."""
        seen: dict[str, None] = {}
        result: list[str] = []
        for email in _EMAIL_RE.findall(text):
            low = email.lower()
            local, _, domain = low.partition("@")
            if any(low.endswith(x) for x in _FAKE_EXTENSIONS):
                continue
            if domain in _BLACKLISTED_DOMAINS:
                continue
            if any(low.startswith(p) for p in _BLACKLISTED_PREFIXES):
                continue
            if low not in seen:
                seen[low] = None
                result.append(email)
        return result

    def extract_phone_numbers(self, text: str) -> list[str]:
        """Extrait et dédoublonne les numéros de téléphone depuis un texte brut."""
        seen: dict[str, None] = {}
        result: list[str] = []
        for raw in _PHONE_RE.findall(text):
            raw = raw.strip()
            digits = re.sub(r'\D', '', raw)
            if not (7 <= len(digits) <= 15):
                continue
            if digits not in seen:
                seen[digits] = None
                result.append(raw)
        return result

    def extract_website(self, text: str) -> str:
        """Extrait le premier site web officiel depuis la réponse IA."""
        _URL_RE = re.compile(
            r'https?://(?:www\.)?[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?:/[^\s<>"]*)?'
        )
        skip = {
            "google", "facebook", "twitter", "linkedin",
            "instagram", "youtube", "wikipedia", "bing", "yahoo",
        }
        for url in _URL_RE.findall(text):
            root = url.split("/")[2].lstrip("www.").split(".")[0].lower()
            if root not in skip:
                return url.rstrip(".,;)")
        return ""

    # ------------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------------

    def save_result(self, result: dict[str, Any]) -> None:
        """Sauvegarde un résultat dans le store JSON."""
        self.store.set(result["company_id"], result)

    # ------------------------------------------------------------------
    # Traitement d'une entreprise
    # ------------------------------------------------------------------
    def process_company(self, sb: SB, company: dict[str, Any]) -> dict[str, Any] | None:
        """
        Traite une entreprise sans quitter Google AI Mode.
        open_ai_mode() est appelé une seule fois dans run() avant la boucle.
        
        Retourne :
            - dict          → succès
            - _SKIPPED      → entreprise ignorée volontairement (website invalide) — PAS un échec
            - None          → vrai échec de traitement Google AI Mode


        """
        company_id = str(company.get("company_id", "")).strip()
        company_name = str(company.get("name", "")).strip()
        website = str(company.get("website", "")).strip()
        website_lower = website.casefold()

        if (
            not company_id
            or not company_name
            or not website
            or website_lower in {
                "",
                "non disponible",
                "non disponible.",
                "n/a",
                "na",
                "null",
                "none",
                "not available",
                "not available.",
            }
        ):
            log.info(
                "Entreprise ignorée — website invalide : %s (website=%r)",
                company_name,
                website,
            )
            return _SKIPPED

        query = f":{website} email"

        log.info(
            "Traitement : %s (id=%s)",
            company_name,
            company_id,
        )

        for attempt in range(1, self.max_retries + 1):
            try:
                self._ensure_ai_mode(sb)

                # Compter les conteneurs existants AVANT d'envoyer la requête
                try:
                    previous_count = len(sb.find_elements(_RESPONSE_SEL))
                except Exception:
                    previous_count = 0

                if not self.send_query(sb, query):
                    continue

                response_element = self.wait_for_response(sb, previous_count)

                if response_element is None:
                    log.warning(
                        "  [%d/%d] Pas de nouvelle réponse reçue",
                        attempt,
                        self.max_retries,
                    )
                    continue

                maybe_solve_captcha(sb)

                response_text = self.extract_response(sb, response_element)
                emails = self.extract_emails(response_text)
                phone_numbers = self.extract_phone_numbers(response_text)

                website_result = (
                    self.extract_website(response_text)
                    or company.get("website", "")
                )

                now = datetime.now(timezone.utc).isoformat()

                result: dict[str, Any] = {
                    "company_id": company_id,
                    "company_name": company_name,
                    "website": website_result,
                    "email": emails[0] if emails else "",
                    "all_emails": emails,
                    "phone_numbers": phone_numbers,
                    "source": "google_ai_mode",
                    "google_ai_response": response_text[:3500],
                    "created_at": now,
                    "updated_at": now,
                }

                log.info(
                    "  → %d email(s) : %s | %d téléphone(s)",
                    len(emails),
                    ", ".join(emails) if emails else "(aucun)",
                    len(phone_numbers),
                )

                return result

            except Exception as exc:
                log.error(
                    "  [%d/%d] Erreur : %s",
                    attempt,
                    self.max_retries,
                    exc,
                )

                maybe_solve_captcha(sb)

                if attempt < self.max_retries:
                    sb.wait_for_ready_state_complete()

        log.warning("Échec définitif : %s", company_name)
        return None

    # ------------------------------------------------------------------
    # Filtrage d'un batch (cache / website invalide)
    # ------------------------------------------------------------------

    def _filter_batch(
        self, batch: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Retire du batch les entreprises déjà en cache ou sans website exploitable."""
        pending: list[dict[str, Any]] = []
        skipped_cache = 0
        skipped_no_website = 0

        for company in batch:
            cid = str(company.get("company_id", "")).strip()
            if self.store.has(cid):
                skipped_cache += 1
                continue

            website = str(company.get("website", "")).strip().lower()
            if website in _PREFILTER_INVALID_WEBSITE_VALUES:
                skipped_no_website += 1
                continue

            pending.append(company)

        return pending, skipped_cache, skipped_no_website

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def run(self, fetcher: CompanyBatchFetcher) -> dict[str, int]:
        """
        Traite les entreprises fournies par `fetcher`, batch par batch — un
        batch = une page récupérée du backend = une session navigateur.
        Ne matérialise jamais l'ensemble des entreprises en mémoire (streaming).

        Ignore :
        - entreprises déjà présentes dans le cache
        - entreprises sans website exploitable

        Reprend automatiquement à la dernière page traitée si `fetcher` a une
        progression persistée (voir CompanyBatchFetcher).

        Retourne :
        { total, processed, found_emails, skipped_cache, skipped_no_website, failed }
        """
        processed = 0
        found_emails = 0
        failed = 0
        skipped_cache = 0
        skipped_no_website = 0
        batch_num = 0
        current_profile = PROFILE_DIR
        batch_durations: list[float] = []

        raw_batch = fetcher.fetch_next_batch()
        if raw_batch is None:
            log.error(
                "Aucune entreprise à traiter. Vérifiez le backend (%s) ou les "
                "filtres --country/--company-id.",
                BACKEND_URL,
            )
            return {
                "total": fetcher.total or 0,
                "processed": 0, "found_emails": 0,
                "skipped_cache": 0, "skipped_no_website": 0, "failed": 0,
            }

        while raw_batch is not None:
            batch_num += 1
            batch_start = time.monotonic()
            total_batches = fetcher.total_batches()

            pending, batch_skipped_cache, batch_skipped_no_website = self._filter_batch(raw_batch)
            skipped_cache += batch_skipped_cache
            skipped_no_website += batch_skipped_no_website

            log.info(
                "[BATCH %d/%s] %d entreprise(s) reçue(s) — %d à traiter "
                "(cache=%d, sans website=%d)",
                batch_num, total_batches or "?", len(raw_batch),
                len(pending), batch_skipped_cache, batch_skipped_no_website,
            )

            batch_processed = 0
            batch_failed = 0

            while pending:
                try:
                    with SB(
                        uc=True,
                        locale="fr",
                        user_data_dir=str(current_profile),
                        disable_js=False,
                        headless=True,
                    ) as sb:
                        sb.activate_cdp_mode()
                        self.open_ai_mode(sb)
                        consecutive_failures = 0

                        for company_idx, company in enumerate(pending):
                            idx = company_idx + 1
                            result = self.process_company(sb, company)
                            cid = str(company.get("company_id", "")).strip()

                            if result is _SKIPPED:
                                # Website invalide → skip silencieux, aucun compteur d'échec touché
                                skipped_no_website += 1

                            elif result is not None:
                                consecutive_failures = 0
                                self.save_result(result)
                                processed += 1
                                batch_processed += 1
                                if result.get("email"):
                                    found_emails += 1
                                send_contact_info_to_backend(
                                    company_id=result["company_id"],
                                    emails=result.get("all_emails", []),
                                    phone_numbers=result.get("phone_numbers", []),
                                )
                            else:
                                # Vrai échec Google AI Mode — comportement inchangé
                                if cid:
                                    now = datetime.now(timezone.utc).isoformat()
                                    self.save_result(
                                        {
                                            "company_id": cid,
                                            "company_name": company.get("name", ""),
                                            "website": company.get("website", ""),
                                            "email": "",
                                            "all_emails": [],
                                            "phone_numbers": [],
                                            "source": "google_ai_mode",
                                            "google_ai_response": "",
                                            "created_at": now,
                                            "updated_at": now,
                                        }
                                    )
                                failed += 1
                                batch_failed += 1
                                consecutive_failures += 1
                                if consecutive_failures >= 1:
                                    next_profile = (
                                        FALLBACK_PROFILE
                                        if current_profile == PROFILE_DIR
                                        else PROFILE_DIR
                                    )
                                    log.warning(
                                        "3 échecs consécutifs sur le conteneur de réponse IA "
                                        "— basculement vers %s",
                                        next_profile.name,
                                    )
                                    remaining = pending[company_idx + 1:]
                                    pending = list(remaining)
                                    current_profile = next_profile
                                    break

                            if idx < len(pending):
                                log.info("Pause %.1fs...", self.delay)
                                time.sleep(self.delay)
                        else:
                            pending = []

                except Exception as exc:
                    log.error("[SESSION] Erreur navigateur : %s", exc)
                    failed += len(pending)
                    batch_failed += len(pending)
                    pending = []
                    time.sleep(10)

            fetcher.mark_batch_done()

            duration = time.monotonic() - batch_start
            batch_durations.append(duration)
            avg_duration = sum(batch_durations) / len(batch_durations)
            remaining_batches = (total_batches - batch_num) if total_batches else None
            eta = (
                avg_duration * remaining_batches
                if remaining_batches and remaining_batches > 0 else None
            )
            percent = (
                fetcher.estimated_total_processed() / fetcher.total * 100
                if fetcher.total else 0.0
            )

            log.info(
                "[BATCH %d/%s] terminé en %.1fs — traitées=%d, échecs=%d "
                "(cumulé : traitées=%d, ignorées=%d, échecs=%d) — %.1f%% — "
                "moyenne=%.1fs/batch%s",
                batch_num, total_batches or "?", duration,
                batch_processed, batch_failed,
                processed, skipped_cache + skipped_no_website, failed,
                percent, avg_duration,
                f", ETA≈{eta:.0f}s" if eta is not None else "",
            )

            raw_batch = fetcher.fetch_next_batch()

        if fetcher.failed:
            log.warning(
                "Flux interrompu par une erreur réseau — relancer le script "
                "reprendra automatiquement à la page suivante"
            )
        else:
            fetcher.reset_progress()

        total_available = fetcher.total or 0
        # Estimation incluant les entreprises déjà couvertes lors d'un lancement
        # précédent (reprise) — comparer uniquement processed+skipped+failed de
        # CE lancement au total backend produirait un faux écart après reprise.
        total_accounted = fetcher.estimated_total_processed() if fetcher.total else 0
        if total_available and not fetcher.failed:
            if total_accounted < total_available:
                log.warning(
                    "Vérification finale : écart détecté — environ %d entreprise(s) "
                    "couverte(s) (cumul, reprise incluse) contre %d annoncée(s) par "
                    "le backend (delta≈%d)",
                    total_accounted, total_available, total_available - total_accounted,
                )
            else:
                log.info(
                    "Vérification finale OK — %d/%d entreprises couvertes (reprise incluse)",
                    total_accounted, total_available,
                )

        log.info(
            "Terminé — traités=%d | emails=%d | cache=%d | sans website=%d | échecs=%d",
            processed,
            found_emails,
            skipped_cache,
            skipped_no_website,
            failed,
        )
        return {
            "total": total_available,
            "processed": processed,
            "found_emails": found_emails,
            "skipped_cache": skipped_cache,
            "skipped_no_website": skipped_no_website,
            "failed": failed,
        }

# ---------------------------------------------------------------------------
# Retry réseau intelligent
# ---------------------------------------------------------------------------

def _is_transient_error(exc: Exception) -> bool:
    """
    Timeout/connexion : transitoire (réessayable).
    HTTPError : seulement 429/500/502/503/504 sont transitoires — le reste
    (401, 403, 404, ...) est une erreur définitive, jamais réessayée.
    """
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status in _TRANSIENT_STATUS_CODES
    return False


def _retry_delay(attempt: int) -> float:
    """Backoff exponentiel plafonné + jitter aléatoire (évite les retries synchronisés)."""
    exponential = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** (attempt - 1)))
    return exponential + random.uniform(0, RETRY_JITTER)


# ---------------------------------------------------------------------------
# Récupération des entreprises depuis le backend
# ---------------------------------------------------------------------------

def _fetch_companies_page(
    params: dict[str, Any],
    max_retries: int = FETCH_MAX_RETRIES,
) -> tuple[list[dict[str, Any]], int | None]:
    """
    Récupère une seule page depuis le backend.

    Retry avec backoff exponentiel + jitter uniquement sur erreurs transitoires
    (timeout, connexion, 429, 500, 502, 503, 504). Les erreurs définitives
    (401, 403, 404, ...) échouent immédiatement, sans retry.

    Retourne (companies, total). `total` vaut None en cas d'échec définitif ou
    de réponse mal formée — signal d'arrêt pour l'appelant.
    """
    page = params.get("page")
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(f"{BACKEND_URL}/api/companies", params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            last_exc = exc
            if not _is_transient_error(exc):
                log.error("Erreur définitive backend (page %s) : %s", page, exc)
                return [], None
            log.warning(
                "[%d/%d] Erreur transitoire backend (page %s) : %s",
                attempt, max_retries, page, exc,
            )
            if attempt < max_retries:
                time.sleep(_retry_delay(attempt))
            continue
        else:
            companies = data.get("companies", [])
            if not isinstance(companies, list):
                log.error("Format inattendu de la réponse backend : %s", type(companies))
                return [], None
            return companies, int(data.get("total", len(companies)))

    log.error(
        "Abandon de la page %s après %d tentative(s) (dernière erreur : %s)",
        page, max_retries, last_exc,
    )
    return [], None


@dataclass(slots=True)
class _FetchState:
    page: int = 1
    total: int | None = None
    exhausted: bool = False
    failed: bool = False


class CompanyBatchFetcher:
    """
    Récupère les entreprises à scraper depuis le backend page par page, en
    streaming — sans jamais matérialiser l'ensemble en mémoire.

    Persiste la progression (dernière page entièrement traitée) via JsonStore
    pour reprendre automatiquement après interruption sans re-télécharger les
    pages déjà traitées. Dédoublonne les company_id au sein d'un même passage
    (au cas où le backend renverrait accidentellement des doublons entre pages,
    par exemple si le jeu de données change pendant l'exécution).

    Utilisation :
        fetcher = CompanyBatchFetcher(country=..., page_size=..., job_id="...")
        while True:
            batch = fetcher.fetch_next_batch()
            if batch is None:
                break
            ... traiter entièrement le batch ...
            fetcher.mark_batch_done()
        if not fetcher.failed:
            fetcher.reset_progress()
    """

    def __init__(
        self,
        country: str | None = None,
        company_id: str | None = None,
        limit: int = 0,
        page_size: int = FETCH_PAGE_SIZE,
        resume: bool = True,
        job_id: str = "default",
    ) -> None:
        self.country = country
        self.company_id = company_id
        self.limit = limit
        self.page_size = page_size

        self._resumable = resume and not company_id
        self._progress = (
            JsonStore(PROGRESS_DIR / f"fetch_progress_{job_id}.json")
            if self._resumable else None
        )
        self._progress_key = f"{country or 'all'}::{page_size}"

        self._state = _FetchState()
        self._seen_ids: set[str] = set()
        self._yielded = 0

        if self._progress is not None:
            saved = self._progress.get(self._progress_key)
            last_completed = int((saved or {}).get("last_completed_page", 0))
            if last_completed > 0:
                self._state.page = last_completed + 1
                log.info(
                    "[RESUME] Reprise à la page %d (pays=%s, page_size=%d)",
                    self._state.page, country or "all", page_size,
                )

        # Page de départ de CE lancement — sert à estimer, lors de la
        # vérification finale, combien d'entreprises ont déjà été traitées
        # lors d'un lancement précédent (voir run()).
        self.initial_page = self._state.page

    @property
    def total(self) -> int | None:
        return self._state.total

    @property
    def yielded(self) -> int:
        return self._yielded

    @property
    def failed(self) -> bool:
        return self._state.failed

    def estimated_total_processed(self) -> int:
        """
        Nombre d'entreprises couvertes au total, y compris lors d'un lancement
        précédent en cas de reprise (self._yielded ne compte que ce que CE
        fetcher a rendu depuis sa création). Utilisé pour le % de progression
        et la vérification finale, qui doivent rester cohérents après reprise.
        """
        already_covered = (self.initial_page - 1) * self.page_size
        return already_covered + self._yielded

    def total_batches(self) -> int | None:
        if self._state.total is None:
            return None
        effective_total = min(self._state.total, self.limit) if self.limit > 0 else self._state.total
        return max(1, -(-effective_total // self.page_size))  # division entière arrondie au sup.

    def fetch_next_batch(self) -> list[dict[str, Any]] | None:
        """Récupère la prochaine page non encore traitée. None = flux épuisé."""
        if self._state.exhausted:
            return None
        if self.limit > 0 and self._yielded >= self.limit:
            self._state.exhausted = True
            return None

        params: dict[str, Any] = {
            "scrape": "1",
            "page": self._state.page,
            "limit": self.page_size,
        }
        if self.country:
            params["country"] = self.country

        companies, total = _fetch_companies_page(params)

        if total is None:
            self._state.exhausted = True
            self._state.failed = True
            log.error(
                "Flux d'entreprises interrompu par une erreur réseau définitive "
                "à la page %d — un nouveau lancement reprendra à cette page",
                self._state.page,
            )
            return None

        self._state.total = total

        if not companies:
            self._state.exhausted = True
            return None

        unique: list[dict[str, Any]] = []
        for company in companies:
            cid = str(company.get("company_id", "")).strip()
            if cid:
                if cid in self._seen_ids:
                    continue
                self._seen_ids.add(cid)
            unique.append(company)

        if self.company_id:
            unique = [c for c in unique if str(c.get("company_id", "")) == self.company_id]
            if unique:
                self._state.exhausted = True  # trouvée — inutile de paginer plus loin

        if self.limit > 0:
            unique = unique[: self.limit - self._yielded]

        self._yielded += len(unique)

        # Détection de fin de flux basée sur le numéro de page absolu (et non
        # sur self._yielded, qui ne reflète que ce que CE fetcher a rendu —
        # après une reprise, il ne compte pas les pages déjà traitées lors
        # d'un lancement précédent). Évite une requête HTTP inutile pour une
        # page finale vide quand la dernière page réelle est pleine.
        if len(companies) < self.page_size:
            self._state.exhausted = True
        elif self._state.total is not None:
            last_page_number = -(-self._state.total // self.page_size)
            if self._state.page >= last_page_number:
                self._state.exhausted = True

        return unique

    def mark_batch_done(self) -> None:
        """À appeler une fois le batch retourné par fetch_next_batch() entièrement traité."""
        if self._progress is not None:
            self._progress.set(
                self._progress_key,
                {
                    "last_completed_page": self._state.page,
                    "total": self._state.total,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        self._state.page += 1

    def reset_progress(self) -> None:
        """
        Efface la progression persistée — à appeler une fois le flux
        entièrement épuisé avec succès, pour qu'un futur lancement reparte de
        la première page (les entreprises déjà traitées auront de toute façon
        disparu du filtre ?scrape=1 côté backend une fois leurs emails enregistrés).
        """
        if self._progress is not None:
            self._progress.set(
                self._progress_key,
                {
                    "last_completed_page": 0,
                    "total": None,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )


def fetch_companies_from_backend(
    country: str | None = None,
    company_id: str | None = None,
    limit: int = 0,
    page_size: int = FETCH_PAGE_SIZE,
) -> list[dict[str, Any]]:
    """
    Récupère en une fois la totalité des entreprises correspondant aux filtres
    (pagine automatiquement en interne via CompanyBatchFetcher, avec le même
    retry intelligent). Conservée pour un usage ponctuel où tout tenir en
    mémoire est acceptable ; pour un traitement en flux avec reprise sur
    incident, utiliser directement CompanyBatchFetcher (voir run()).

    Filtre côté backend (?scrape=1) :
    - entreprises avec website non vide
    - entreprises sans emails encore scrapés
    """
    log.info("Récupération des entreprises depuis le backend (%s)...", BACKEND_URL)

    fetcher = CompanyBatchFetcher(
        country=country, company_id=company_id, limit=limit,
        page_size=page_size, resume=False,
    )
    companies: list[dict[str, Any]] = []
    while True:
        batch = fetcher.fetch_next_batch()
        if batch is None:
            break
        companies.extend(batch)
        fetcher.mark_batch_done()

    if company_id:
        log.info("Filtre company_id=%r → %d entreprise(s)", company_id, len(companies))
    elif fetcher.total is not None and limit <= 0 and len(companies) < fetcher.total:
        log.warning(
            "Récupération incomplète : %d/%d entreprises obtenues côté backend",
            len(companies), fetcher.total,
        )

    log.info(
        "%d entreprises récupérées (total disponible côté backend : %s)",
        len(companies),
        fetcher.total if fetcher.total is not None else "inconnu",
    )
    return companies


# ---------------------------------------------------------------------------
# Envoi des résultats de scraping au backend
# ---------------------------------------------------------------------------

def send_contact_info_to_backend(
    company_id: str,
    emails: list[str],
    phone_numbers: list[str],
    max_retries: int = 3,
) -> bool:
    """
    Envoie les emails et numéros de téléphone scrapés via
    PATCH /api/companies/:company_id/contact.

    Retry intelligent (backoff + jitter) sur erreurs transitoires uniquement ;
    échec immédiat sur erreur définitive (401/403/404/...).
    Retourne True si le backend confirme l'enregistrement, False sinon.
    """
    if not emails and not phone_numbers:
        return True  # rien à envoyer

    url = f"{BACKEND_URL}/api/companies/{company_id}/contact"
    payload: dict[str, Any] = {"emails": emails, "phone_numbers": phone_numbers}

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.patch(url, json=payload, timeout=15)
            resp.raise_for_status()
            log.info("Résultats enregistrés pour company_id=%s", company_id)
            return True
        except Exception as exc:
            if not _is_transient_error(exc):
                log.error(
                    "Erreur définitive envoi backend company_id=%s : %s", company_id, exc,
                )
                return False
            log.warning(
                "[%d/%d] Erreur transitoire envoi backend company_id=%s : %s",
                attempt, max_retries, company_id, exc,
            )
            if attempt < max_retries:
                time.sleep(_retry_delay(attempt))

    log.error("Échec envoi backend pour company_id=%s", company_id)
    return False

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extrait les emails d'entreprises via Google AI Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python -m scrapers.email_extractor
  python -m scrapers.email_extractor --country senegal
  python -m scrapers.email_extractor --limit 10 --delay 6
  python -m scrapers.email_extractor --company-id abc123
        """,
    )
    parser.add_argument(
        "--country",
        metavar="NOM",
        help="Filtrer par nom de pays tel que stocké en base (ex: 'Algérie', 'Sénégal') — tous si omis",
    )
    parser.add_argument(
        "--company-id",
        metavar="ID",
        help="Traiter une seule entreprise par son identifiant",
    )
    parser.add_argument(
        "--output",
        metavar="FICHIER",
        default=str(EMAILS_FILE),
        help=f"Fichier JSON de cache local secondaire (défaut : {EMAILS_FILE})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_BETWEEN_SEARCHES,
        metavar="SECONDES",
        help=f"Délai entre les recherches (défaut : {DELAY_BETWEEN_SEARCHES}s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Limiter le nombre d'entreprises traitées (0 = toutes)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=FETCH_PAGE_SIZE,
        metavar="N",
        help=(
            "Entreprises par batch (récupération backend + session navigateur), "
            f"défaut : {FETCH_PAGE_SIZE}"
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=MAX_RETRIES,
        metavar="N",
        help=f"Tentatives max par entreprise (défaut : {MAX_RETRIES})",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignorer la progression persistée et repartir de la première page",
    )
    args = parser.parse_args()

    store = EmailStore(Path(args.output))
    fetcher = CompanyBatchFetcher(
        country=args.country,
        company_id=args.company_id,
        limit=args.limit,
        page_size=args.batch_size,
        resume=not args.no_resume,
        job_id="google_ai_mode",
    )

    extractor = GoogleAiEmailExtractor(
        store=store,
        delay=args.delay,
        max_retries=args.retries,
    )
    extractor.run(fetcher)


if __name__ == "__main__":
    main()
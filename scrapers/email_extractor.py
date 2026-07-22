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
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from selenium.common.exceptions import StaleElementReferenceException
from seleniumbase import SB
from scrapers.common.browser_helpers import maybe_solve_captcha

# ---------------------------------------------------------------------------
# Chemins & configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
PROFILE_DIR = PROJECT_ROOT / "my_custom_profile_3"
EMAILS_FILE = OUTPUT_DIR / "company_emails.json"

# URL du backend — surchargeable via variable d'environnement SCRAPER_API_URL
BACKEND_URL: str = os.getenv("SCRAPER_API_URL", "http://localhost:3500")

GOOGLE_AI_MODE_URL = "https://www.google.com/search?udm=50&hl=fr"

DELAY_BETWEEN_SEARCHES: float = 2.0
MAX_RETRIES: int = 3
BATCH_SIZE: int = 5000

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
    "textarea",    # textarea conversation AI Mode (confirmé)
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
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.store = store
        self.delay = delay
        self.max_retries = max_retries
        self.batch_size = batch_size


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

    def wait_for_response(self, sb: SB, previous_count: int, max_wait: int = 45) -> Any | None:
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
        max_wait: int = 30,
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
        Ici on se contente de taper la requête dans le textarea existant.
        """
        company_id = str(company.get("company_id", "")).strip()
        company_name = str(company.get("name", "")).strip()
        website = str(company.get("website", "")).strip()

        if not company_id or not company_name:
            log.warning("Entreprise ignorée — id ou nom manquant : %s", company)
            return None

        query = f":{website} email"
        log.info("Traitement : %s (id=%s)", company_name, company_id)

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
                        attempt, self.max_retries,
                    )
                    continue

                maybe_solve_captcha(sb)

                response_text = self.extract_response(sb, response_element)
                emails = self.extract_emails(response_text)
                phone_numbers = self.extract_phone_numbers(response_text)
                website = self.extract_website(response_text) or company.get("website", "")
                now = datetime.now(timezone.utc).isoformat()

                result: dict[str, Any] = {
                    "company_id": company_id,
                    "company_name": company_name,
                    "website": website,
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
                    len(emails), ", ".join(emails) if emails else "(aucun)",
                    len(phone_numbers),
                )
                
                return result

            except Exception as exc:
                log.error("  [%d/%d] Erreur : %s", attempt, self.max_retries, exc)
                maybe_solve_captcha(sb)
                if attempt < self.max_retries:
                    sb.wait_for_ready_state_complete()

        log.warning("Échec définitif : %s", company_name)
        return None

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def run(self, companies: list[dict[str, Any]]) -> dict[str, int]:
        """
        Traite une liste d'entreprises par batch (un navigateur par batch).
        Ignore :
        - entreprises déjà présentes dans le cache
        - entreprises sans website exploitable
        Retourne :
        {
            total,
            processed,
            found_emails,
            skipped_cache,
            skipped_no_website,
            failed
        }
        """
        total = len(companies)
        print(f"total companies: {total}")
        skipped_cache = 0
        skipped_no_website = 0
        pending: list[dict[str, Any]] = []
        for company in companies:

            cid = str(company.get("company_id", "")).strip()
            if self.store.has(cid):
                skipped_cache += 1
                continue

            website = str(
                company.get("website", "")

            ).strip().lower()

            if website in (
                "",
                "non disponible",
                "non disponible.",
                "n/a",
                "na",
                "null",
                "none",
            ):
                skipped_no_website += 1
                continue

            pending.append(company)

        if skipped_cache:
            log.info(
                "%d entreprises déjà traitées (cache)",
                skipped_cache
            )

        if skipped_no_website:
            log.info(
                "%d entreprises ignorées (website absent/non disponible)",
                skipped_no_website
            )

        log.info(
            "Démarrage — %d entreprises à traiter",
            len(pending)
        )

        processed = 0
        found_emails = 0
        failed = 0
        batch_num = 0

        while pending:
            batch = pending[: self.batch_size]
            pending = pending[self.batch_size :]
            batch_num += 1
            log.info(
                "[BATCH %d] %d entreprises",
                batch_num,
                len(batch)
            )
            try:
                with SB(
                    uc=True,
                    locale="fr",
                    user_data_dir=str(PROFILE_DIR),
                    disable_js=False,
                    headless=True,
                ) as sb:
                    sb.activate_cdp_mode()
                    self.open_ai_mode(sb)
                    for idx, company in enumerate(batch, 1):
                        result = self.process_company(
                            sb,
                            company
                        )
                        cid = str(
                            company.get(
                                "company_id",
                                ""
                            )
                        ).strip()
                        if result is not None:
                            self.save_result(result)
                            processed += 1
                            if result.get("email"):
                                found_emails += 1
                            send_contact_info_to_backend(
                                company_id=result["company_id"],
                                emails=result.get("all_emails", []),
                                phone_numbers=result.get("phone_numbers", []),
                            )
                        else:
                            if cid:

                                now = datetime.now(
                                    timezone.utc
                                ).isoformat()

                                self.save_result(
                                    {
                                        "company_id": cid,
                                        "company_name": company.get(
                                            "name",
                                            ""
                                        ),
                                        "website": company.get(
                                            "website",
                                            ""
                                        ),
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

                        if idx < len(batch):
                            log.info(
                                "Pause %.1fs...",
                                self.delay
                            )
                            time.sleep(
                                self.delay
                            )

            except Exception as exc:
                log.error(
                    "[SESSION] Erreur navigateur : %s",
                    exc
                )
                failed += len(batch)
                time.sleep(10)

        log.info(
            "Terminé — traités=%d | emails=%d | cache=%d | sans website=%d | échecs=%d",
            processed,
            found_emails,
            skipped_cache,
            skipped_no_website,
            failed,
        )
        return {
            "total": total,
            "processed": processed,
            "found_emails": found_emails,
            "skipped_cache": skipped_cache,
            "skipped_no_website": skipped_no_website,
            "failed": failed,
        }

# ---------------------------------------------------------------------------
# Récupération des entreprises depuis le backend
# ---------------------------------------------------------------------------

def fetch_companies_from_backend(
    country: str | None = None,
    company_id: str | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """
    Récupère les entreprises à scraper depuis le backend API.

    Filtre côté backend (?scrape=1) :
    - entreprises avec website non vide
    - entreprises sans emails encore scrapés

    Args:
        country:    nom de pays tel que stocké en base (ex: 'Algérie') — tous si None
        company_id: filtrer sur un identifiant précis
        limit:      nombre max (0 = backend décide, défaut 10000)

    Returns:
        Liste de dicts entreprises avec au minimum 'company_id', 'name', 'website'.
    """
    log.info("Récupération des entreprises depuis le backend (%s)...",BACKEND_URL)

    params: dict[str, Any] = {"scrape": "1"}
    if limit > 0:
        params["limit"] = limit
    if country:
        params["country"] = country

    try:
        response = requests.get(
            f"{BACKEND_URL}/api/companies",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.ConnectionError as exc:
        log.error("Connexion au backend impossible (%s) : %s", BACKEND_URL, exc)
        return []
    except requests.exceptions.Timeout:
        log.error("Timeout lors de la connexion au backend (%s)", BACKEND_URL)
        return []
    except requests.exceptions.HTTPError as exc:
        log.error("Erreur HTTP backend : %s", exc)
        return []
    except Exception as exc:
        log.error("Erreur inattendue récupération entreprises : %s", exc)
        return []

    companies: list[dict[str, Any]] = data.get("companies", [])
    if not isinstance(companies, list):
        log.error("Format inattendu de la réponse backend : %s", type(companies))
        return []

    if company_id:
        companies = [c for c in companies if str(c.get("company_id", "")) == company_id]
        log.info("Filtre company_id=%r → %d entreprise(s)", company_id, len(companies))

    log.info(
        "%d entreprises récupérées (total disponible côté backend : %d)",
        len(companies),
        data.get("total", len(companies)),
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
        except requests.exceptions.HTTPError as exc:
            log.error(
                "[%d/%d] Erreur HTTP envoi backend company_id=%s : %s",
                attempt, max_retries, company_id, exc,
            )
            break  # erreur HTTP (4xx/5xx) → pas de retry
        except requests.exceptions.RequestException as exc:
            log.warning(
                "[%d/%d] Erreur réseau envoi backend company_id=%s : %s",
                attempt, max_retries, company_id, exc,
            )
            if attempt < max_retries:
                time.sleep(2 * attempt)

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
        default=BATCH_SIZE,
        metavar="N",
        help=f"Entreprises par session navigateur (défaut : {BATCH_SIZE})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=MAX_RETRIES,
        metavar="N",
        help=f"Tentatives max par entreprise (défaut : {MAX_RETRIES})",
    )
    args = parser.parse_args()

    store = EmailStore(Path(args.output))
    companies = fetch_companies_from_backend(
        country=args.country,
        company_id=args.company_id,
        limit=args.limit,
    )

    if not companies:
        log.error(
            "Aucune entreprise trouvée. "
            "Vérifiez le backend (%s) ou les filtres --country/--company-id",
            BACKEND_URL,
        )
        return

    extractor = GoogleAiEmailExtractor(
        store=store,
        delay=args.delay,
        max_retries=args.retries,
        batch_size=args.batch_size,
    )
    extractor.run(companies)


if __name__ == "__main__":
    main()
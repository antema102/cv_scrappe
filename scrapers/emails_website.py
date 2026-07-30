"""
emails_website.py
==================
Extrait les emails de contact d'entreprises en crawlant directement leur site web
(alternative complémentaire à email_extractor.py, qui utilise le Mode IA de Google).
Compatible Shadow DOM, iframes same-origin, JSON-LD, commentaires HTML et entités HTML.

Utilisation :
    python -m scrapers.emails_website                        # toutes les entreprises
    python -m scrapers.emails_website --country senegal      # un pays
    python -m scrapers.emails_website --limit 20             # 20 premières
    python -m scrapers.emails_website --company-id abc123    # une seule entreprise
"""

from __future__ import annotations

import html
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from seleniumbase import SB

from scrapers.common.browser_helpers import maybe_solve_captcha
from scrapers.common.helpers import normalize_text
from scrapers.email_extractor import CompanyBatchFetcher, send_contact_info_to_backend
from scrapers.emploi_scraper import JsonStore

# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
PROFILE_DIR = PROJECT_ROOT / "my_custom_profile_4"
EMAILS_FILE = OUTPUT_DIR / "company_emails_website.json"

# ---------------------------------------------------------------------------
# Navigation / résilience
# ---------------------------------------------------------------------------

NAV_TIMEOUT: int = 20
RETRY_ATTEMPTS: int = 3
RETRY_SLEEP: float = 5
DELAY_BETWEEN_COMPANIES: float = 0.0
BATCH_SIZE: int = 10000

MAX_PAGES_PER_DOMAIN: int = 6
PRIORITY_STOP_COUNT: int = 2

_ALLOWED_SCHEMES = frozenset({"http", "https"})

IGNORED_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mjs",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".rar", ".7z",
    ".mp4", ".mp3", ".avi", ".mov",
})

# ---------------------------------------------------------------------------
# Pages candidates (FR + EN) — priorité décroissante
# ---------------------------------------------------------------------------

CANDIDATE_PAGE_KEYWORDS: tuple[str, ...] = (
    "contact", "contact us", "contact-us", "contactez nous", "contactez-nous",
    "nous contacter", "support", "help", "customer service", "about", "about us",
    "about-us", "company", "team", "our team", "staff", "leadership", "legal",
    "mentions legales", "mentions légales", "privacy policy", "terms", "imprint",
    "footer", "sitemap",
)

# ---------------------------------------------------------------------------
# Domaines/services tiers à exclure des emails détectés
# ---------------------------------------------------------------------------

THIRD_PARTY_DOMAIN_TOKENS: tuple[str, ...] = (
    "google", "gstatic", "googleapis", "facebook", "fbcdn", "twitter", "linkedin",
    "youtube", "cloudflare", "wix", "wixpress", "shopify", "squarespace", "hubspot",
    "intercom", "zendesk", "hotjar", "segment", "sentry", "bugsnag", "rollbar",
    "stripe", "paypal", "mailchimp", "brevo", "sendinblue", "sendgrid", "amazonaws",
    "azure", "vercel", "netlify", "schema.org", "w3.org", "openxmlformats.org",
    "example.com", "test.com",
)

_BLACKLISTED_LOCAL_PREFIXES = frozenset({
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer", "bounce", "postmaster",
})

# ---------------------------------------------------------------------------
# Priorisation des emails trouvés
# ---------------------------------------------------------------------------

EMAIL_PRIORITY_PREFIXES: tuple[str, ...] = (
    "contact", "hello", "info", "support", "sales", "office", "commercial",
    "service", "admin", "hr", "career", "jobs", "recruitment", "marketing",
    "communication", "direction",
)

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b')
_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://')

# Formes obfusquées explicites de la spec — uniquement des motifs entre
# parenthèses/crochets, jamais de remplacement de mots isolés (trop de faux positifs).
_OBFUSCATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'\(\s*at\s*\)', re.IGNORECASE), "@"),
    (re.compile(r'\[\s*at\s*\]', re.IGNORECASE), "@"),
    (re.compile(r'\(\s*dot\s*\)', re.IGNORECASE), "."),
    (re.compile(r'\[\s*dot\s*\]', re.IGNORECASE), "."),
)

_INVALID_WEBSITE_VALUES = frozenset({
    "", "non disponible", "non disponible.", "n/a", "na", "null", "none",
    "not available", "not available.",
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
# Sentinelle "ignoré"
# ---------------------------------------------------------------------------

class _SkippedSentinel:
    """Retourné par process_company() quand l'entreprise n'a pas de website exploitable."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "SKIPPED"


_SKIPPED = _SkippedSentinel()


# ---------------------------------------------------------------------------
# Utilitaires URL
# ---------------------------------------------------------------------------

def ensure_scheme(url: str) -> str:
    url = url.strip()
    return url if _SCHEME_RE.match(url) else f"https://{url}"


def normalize_url(url: str, base_url: str) -> str:
    absolute = urljoin(base_url, url or "")
    parsed = urlparse(absolute)
    path = parsed.path.rstrip("/") or "/"
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        fragment="",
    )
    return normalized.geturl()


def is_navigable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        return False
    lower_path = parsed.path.lower()
    return not any(lower_path.endswith(ext) for ext in IGNORED_EXTENSIONS)


def same_domain(url: str, root_domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.") == root_domain


def root_domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


# ---------------------------------------------------------------------------
# Correspondance de mots-clés (accents/casse indifférents)
# ---------------------------------------------------------------------------

def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


_FOLDED_KEYWORDS: tuple[str, ...] = tuple(_fold(keyword) for keyword in CANDIDATE_PAGE_KEYWORDS)


def _keyword_rank(texts: list[str]) -> int | None:
    """Retourne l'index de priorité du premier mot-clé candidat trouvé, ou None."""
    folded_texts = [_fold(normalize_text(text)) for text in texts if text]
    if not folded_texts:
        return None
    for index, folded_keyword in enumerate(_FOLDED_KEYWORDS):
        if any(folded_keyword in text for text in folded_texts):
            return index
    return None


# ---------------------------------------------------------------------------
# Extraction / priorisation des emails
# ---------------------------------------------------------------------------

def deobfuscate(text: str) -> str:
    text = html.unescape(text)
    for pattern, replacement in _OBFUSCATION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def extract_emails(raw_text: str) -> list[str]:
    text = deobfuscate(raw_text)
    seen: dict[str, None] = {}
    result: list[str] = []

    for match in _EMAIL_RE.findall(text):
        email = match.strip().strip(".,;:)('\"").lower()
        local, _, domain = email.partition("@")
        if not local or "." not in domain:
            continue
        if any(email.endswith(ext) for ext in IGNORED_EXTENSIONS):
            continue
        if any(token in domain for token in THIRD_PARTY_DOMAIN_TOKENS):
            continue
        if any(local.startswith(prefix) for prefix in _BLACKLISTED_LOCAL_PREFIXES):
            continue
        if email in seen:
            continue
        seen[email] = None
        result.append(email)

    return result


def _priority_rank(email: str) -> int:
    local = email.split("@", 1)[0]
    for index, prefix in enumerate(EMAIL_PRIORITY_PREFIXES):
        if local == prefix or local.startswith(prefix):
            return index
    return len(EMAIL_PRIORITY_PREFIXES)


def prioritize_emails(emails: list[str]) -> list[str]:
    return sorted(emails, key=_priority_rank)


def _priority_email_count(emails: list[str]) -> int:
    return sum(1 for email in emails if _priority_rank(email) < len(EMAIL_PRIORITY_PREFIXES))


# ---------------------------------------------------------------------------
# Découverte de liens (texte visible, aria-label, title, alt, data-*, href)
# ---------------------------------------------------------------------------

def discover_links(soup: BeautifulSoup, base_url: str, root_domain: str) -> list[str]:
    ranked: dict[str, int] = {}

    for tag in soup.find_all(["a", "button"]):
        href = tag.get("href")
        if not href:
            continue

        texts = [
            tag.get_text(" ", strip=True),
            tag.get("aria-label", ""),
            tag.get("title", ""),
            tag.get("alt", ""),
        ]
        texts.extend(
            value for key, value in tag.attrs.items()
            if key.startswith("data-") and isinstance(value, str)
        )

        rank = _keyword_rank(texts)
        if rank is None:
            continue

        url = normalize_url(href, base_url)
        if not is_navigable(url) or not same_domain(url, root_domain):
            continue

        if url not in ranked or rank < ranked[url]:
            ranked[url] = rank

    return sorted(ranked, key=ranked.get)


# ---------------------------------------------------------------------------
# Shadow DOM / iframes same-origin
# ---------------------------------------------------------------------------

_SHADOW_DOM_JS = """
function collect(root) {
    let text = '';
    root.querySelectorAll('*').forEach((el) => {
        if (el.shadowRoot) {
            text += el.shadowRoot.innerHTML + ' ';
            text += collect(el.shadowRoot);
        }
    });
    return text;
}
return collect(document);
"""

_IFRAME_SRCS_JS = (
    "return Array.from(document.querySelectorAll('iframe')).map(f => f.src || '');"
)


def collect_shadow_dom_text(sb: SB) -> str:
    try:
        return sb.execute_script(_SHADOW_DOM_JS) or ""
    except Exception:
        return ""


def collect_same_domain_iframe_text(sb: SB, root_domain: str) -> str:
    try:
        iframe_srcs: list[str] = sb.execute_script(_IFRAME_SRCS_JS) or []
    except Exception:
        return ""

    collected: list[str] = []
    for index, src in enumerate(iframe_srcs):
        if not src or not same_domain(src, root_domain):
            continue
        try:
            sb.switch_to_frame(index)
            collected.append(sb.get_page_source())
        except Exception:
            continue
        finally:
            try:
                sb.switch_to_default_content()
            except Exception:
                pass

    return " ".join(collected)


# ---------------------------------------------------------------------------
# Analyse d'une page
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PageResult:
    emails: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def _goto_with_retry(sb: SB, url: str, retries: int = RETRY_ATTEMPTS) -> bool:
    for attempt in range(retries):
        if attempt == 0:
            sb.goto(url)
        else:
            sb.refresh()
        try:
            sb.wait_for_ready_state_complete()
            maybe_solve_captcha(sb)
            return True
        except Exception as exc:
            log.warning("    [RETRY %d/%d] %s — %s", attempt + 1, retries, url, exc)
            maybe_solve_captcha(sb)
            sb.sleep(RETRY_SLEEP)

    log.warning("    [SKIP] Page inaccessible après %d tentative(s) : %s", retries, url)
    return False


def analyze_page(sb: SB, url: str, root_domain: str) -> PageResult:
    if not _goto_with_retry(sb, url):
        return PageResult()

    try:
        page_source = sb.get_page_source()
    except Exception as exc:
        log.warning("    [ERREUR] Source HTML inaccessible — %s : %s", url, exc)
        return PageResult()

    shadow_text = collect_shadow_dom_text(sb)
    iframe_text = collect_same_domain_iframe_text(sb, root_domain)
    full_text = " ".join((page_source, shadow_text, iframe_text))

    soup = BeautifulSoup(page_source, "html.parser")
    return PageResult(
        emails=extract_emails(full_text),
        links=discover_links(soup, url, root_domain),
    )


# ---------------------------------------------------------------------------
# Crawler par domaine
# ---------------------------------------------------------------------------

class WebsiteEmailCrawler:
    """
    Crawl limité d'un domaine à la recherche d'emails de contact.

    Flux :
      1. page d'accueil normalisée en premier
      2. découverte de liens candidats (Contact/About/Legal/...) sur chaque page
      3. arrêt dès PRIORITY_STOP_COUNT emails prioritaires trouvés
      4. jamais plus de max_pages pages visitées, jamais de revisite
    """

    def __init__(self, max_pages: int = MAX_PAGES_PER_DOMAIN) -> None:
        self.max_pages = max_pages

    def crawl_domain(self, sb: SB, website: str) -> dict[str, Any]:
        start_url = ensure_scheme(website)
        root_domain = root_domain_of(start_url)
        if not root_domain:
            return {"emails": [], "pages_visited": []}

        visited: set[str] = set()
        queue: list[str] = [normalize_url(start_url, start_url)]
        found_emails: dict[str, None] = {}
        pages_visited: list[str] = []

        while queue and len(pages_visited) < self.max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            log.info("    [PAGE %d/%d] %s", len(pages_visited) + 1, self.max_pages, url)
            try:
                result = analyze_page(sb, url, root_domain)
            except Exception as exc:
                log.warning("    [ERREUR] %s — %s", url, exc)
                continue

            pages_visited.append(url)
            for email in result.emails:
                found_emails.setdefault(email, None)

            if _priority_email_count(list(found_emails)) >= PRIORITY_STOP_COUNT:
                log.info("    [STOP] %d email(s) prioritaire(s) trouvé(s)", PRIORITY_STOP_COUNT)
                break

            for link in result.links:
                if link not in visited and link not in queue:
                    queue.append(link)

        return {
            "emails": prioritize_emails(list(found_emails)),
            "pages_visited": pages_visited,
        }


# ---------------------------------------------------------------------------
# Traitement d'une entreprise
# ---------------------------------------------------------------------------

def process_company(
    sb: SB,
    company: dict[str, Any],
    crawler: WebsiteEmailCrawler,
) -> dict[str, Any] | _SkippedSentinel | None:
    company_id = str(company.get("company_id", "")).strip()
    company_name = str(company.get("name", "")).strip()
    website = str(company.get("website", "")).strip()

    if (
        not company_id
        or not company_name
        or not website
        or website.casefold() in _INVALID_WEBSITE_VALUES
    ):
        log.info("Entreprise ignorée — website invalide : %s (website=%r)", company_name, website)
        return _SKIPPED

    log.info("Traitement : %s (id=%s) — %s", company_name, company_id, website)

    try:
        crawl = crawler.crawl_domain(sb, website)
    except Exception as exc:
        log.error("  Échec crawl %s : %s", website, exc)
        return None

    emails: list[str] = crawl["emails"]
    now = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "company_id": company_id,
        "company_name": company_name,
        "website": website,
        "email": emails[0] if emails else "",
        "all_emails": emails,
        "pages_visited": crawl["pages_visited"],
        "source": "website_crawl",
        "created_at": now,
        "updated_at": now,
    }

    log.info(
        "  → %d email(s) : %s (%d page(s) visitée(s))",
        len(emails),
        ", ".join(emails) if emails else "(aucun)",
        len(crawl["pages_visited"]),
    )
    return result


# ---------------------------------------------------------------------------
# Filtrage d'un batch (cache / website invalide)
# ---------------------------------------------------------------------------

def _filter_batch(
    batch: list[dict[str, Any]], store: JsonStore
) -> tuple[list[dict[str, Any]], int, int]:
    """Retire du batch les entreprises déjà en cache ou sans website exploitable."""
    pending: list[dict[str, Any]] = []
    skipped_cache = 0
    skipped_no_website = 0

    for company in batch:
        cid = str(company.get("company_id", "")).strip()
        if store.has(cid):
            skipped_cache += 1
            continue

        website = str(company.get("website", "")).strip().casefold()
        if website in _INVALID_WEBSITE_VALUES:
            skipped_no_website += 1
            continue

        pending.append(company)

    return pending, skipped_cache, skipped_no_website


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

def run(
    fetcher: CompanyBatchFetcher,
    store: JsonStore,
    delay: float = DELAY_BETWEEN_COMPANIES,
    max_pages: int = MAX_PAGES_PER_DOMAIN,
) -> dict[str, int]:
    """
    Traite les entreprises fournies par `fetcher`, batch par batch — un batch =
    une page récupérée du backend = une session navigateur. Ne matérialise
    jamais l'ensemble des entreprises en mémoire (streaming).

    Reprend automatiquement à la dernière page traitée si `fetcher` a une
    progression persistée (voir CompanyBatchFetcher).
    """
    processed = 0
    found_emails = 0
    failed = 0
    skipped_cache = 0
    skipped_no_website = 0
    batch_num = 0
    crawler = WebsiteEmailCrawler(max_pages=max_pages)
    batch_durations: list[float] = []

    raw_batch = fetcher.fetch_next_batch()
    if raw_batch is None:
        log.error("Aucune entreprise trouvée. Vérifiez le backend ou les filtres --country/--company-id")
        return {
            "total": fetcher.total or 0,
            "processed": 0, "found_emails": 0,
            "skipped_cache": 0, "skipped_no_website": 0, "failed": 0,
        }

    while raw_batch is not None:
        batch_num += 1
        batch_start = time.monotonic()
        total_batches = fetcher.total_batches()

        pending, batch_skipped_cache, batch_skipped_no_website = _filter_batch(raw_batch, store)
        skipped_cache += batch_skipped_cache
        skipped_no_website += batch_skipped_no_website

        log.info(
            "[BATCH %d/%s] %d entreprise(s) reçue(s) — %d à traiter (cache=%d, sans website=%d)",
            batch_num, total_batches or "?", len(raw_batch),
            len(pending), batch_skipped_cache, batch_skipped_no_website,
        )

        batch_processed = 0
        batch_failed = 0

        try:
            with SB(
                uc=True,
                locale="fr",
                user_data_dir=str(PROFILE_DIR),
                disable_js=False,
                headless=False,
            ) as sb:
                sb.activate_cdp_mode()

                for idx, company in enumerate(pending, start=1):
                    result = process_company(sb, company, crawler)
                    cid = str(company.get("company_id", "")).strip()

                    if result is _SKIPPED:
                        skipped_no_website += 1
                    elif result is not None:
                        store.set(cid, result)
                        processed += 1
                        batch_processed += 1
                        if result.get("email"):
                            found_emails += 1
                        send_contact_info_to_backend(
                            company_id=result["company_id"],
                            emails=result.get("all_emails", []),
                            phone_numbers=[],
                        )
                    else:
                        failed += 1
                        batch_failed += 1

                    if idx < len(pending) and delay:
                        time.sleep(delay)

        except Exception as exc:
            log.error("[SESSION] Erreur navigateur : %s", exc)
            failed += len(pending)
            batch_failed += len(pending)
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
    # précédent (reprise) — comparer uniquement le cumul de CE lancement au
    # total backend produirait un faux écart après reprise.
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
        processed, found_emails, skipped_cache, skipped_no_website, failed,
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
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extrait les emails d'entreprises en crawlant directement leur site web",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python -m scrapers.emails_website
  python -m scrapers.emails_website --country senegal
  python -m scrapers.emails_website --limit 10
  python -m scrapers.emails_website --company-id abc123
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
        help=f"Fichier JSON de cache local (défaut : {EMAILS_FILE})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_BETWEEN_COMPANIES,
        metavar="SECONDES",
        help="Délai entre les entreprises (défaut : 0s)",
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
        help=(
            "Entreprises par batch (récupération backend + session navigateur), "
            f"défaut : {BATCH_SIZE}"
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES_PER_DOMAIN,
        metavar="N",
        help=f"Pages max par domaine (défaut : {MAX_PAGES_PER_DOMAIN})",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignorer la progression persistée et repartir de la première page",
    )
    args = parser.parse_args()

    store = JsonStore(Path(args.output))
    fetcher = CompanyBatchFetcher(
        country=args.country,
        company_id=args.company_id,
        limit=args.limit,
        page_size=args.batch_size,
        resume=not args.no_resume,
        job_id="website_crawl",
    )

    run(
        fetcher,
        store,
        delay=args.delay,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()

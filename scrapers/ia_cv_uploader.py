"""
ia_cv_uploader.py
=================
Envoie les CV scrappés vers l'IA WipWork — `POST {IA_API_URL}/parse/resume`.

Sélection des CV (imposée par la campagne commerciale, appliquée côté backend
par `GET /api/cvs/ia/pending`) :

    { commercial_email_wave: 2, commercial_email_unsubscribed: { $exists: false } }
    + ia_sent != true   (un CV déjà envoyé n'est jamais renvoyé)

Pour chaque CV : le fichier est lu depuis `filepath` (avec repli sur
`--cv-root/<country>/<filename>` si le chemin stocké vient d'une autre
machine), posté en multipart à l'IA avec `enterprise_ids=WipWork` et
`country_ids` = code ISO alpha-3 déduit du pays (`south_africa` -> `ZAF`),
puis le résultat est marqué dans le backend via `PATCH /api/cvs/{id}/ia`.

Utilisation :
    python -m scrapers.ia_cv_uploader --dry-run                 # aucun envoi, liste ce qui partirait
    python -m scrapers.ia_cv_uploader --limit 5                 # test réel sur 5 CV
    python -m scrapers.ia_cv_uploader --country south_africa
    python -m scrapers.ia_cv_uploader --cv-root /srv/cv_scrappe/downloaded_files/cv_files
    python -m scrapers.ia_cv_uploader --use-test                # collections de test côté IA
    python -m scrapers.ia_cv_uploader --max-attempts 3          # ignore les CV ayant déjà échoué 3 fois

Variables d'environnement (lues aussi depuis `.env` / `backend/.env`, git-ignorés) :
    SCRAPER_API_URL  backend Node.js          (défaut http://localhost:3500)
    IA_API_URL       base de l'API IA         (défaut https://bo.wipwork.com/bot)
    IA_API_KEY       clé envoyée en X-API-Key (requise par bo.wipwork.com)
    IA_API_TOKEN     jeton Bearer si requis   (optionnel)
    CV_FILES_ROOT    racine des fichiers CV   (optionnel, ex. /srv/.../cv_files)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator

import requests

from scrapers.common.country_iso import alpha3_for

# ---------------------------------------------------------------------------
# Chemins & configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
CV_FILES_DIR = OUTPUT_DIR / "cv_files"
LEDGER_FILE = OUTPUT_DIR / "ia_uploads.json"


def _load_dotenv(*paths: Path) -> None:
    """Charge des fichiers `KEY=VALUE` sans écraser l'environnement réel.

    Permet de garder la clé d'API hors des fichiers suivis par git (`.env` et
    `backend/.env` sont git-ignorés). Volontairement minimaliste : pas de
    dépendance python-dotenv côté scraper.
    """
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env", PROJECT_ROOT / "backend" / ".env")

BACKEND_URL: str = os.getenv("SCRAPER_API_URL", "http://localhost:3500")
IA_API_URL: str = os.getenv("IA_API_URL", "https://bo.wipwork.com/bot")
IA_API_KEY: str = os.getenv("IA_API_KEY", "")
IA_API_TOKEN: str = os.getenv("IA_API_TOKEN", "")
CV_FILES_ROOT_ENV: str = os.getenv("CV_FILES_ROOT", "")

# Valeurs par défaut de l'appel /parse/resume (cf. documentation de l'API)
DEFAULT_WAVE: int = 2
DEFAULT_ENTERPRISE_ID: str = "WipWork"
DEFAULT_SOURCE: str = "import"
ALLOWED_SOURCES: tuple[str, ...] = ("apply", "save_from_search", "import")
ALLOWED_VISIBILITY: tuple[str, ...] = ("visible", "invisible")
MAX_COUNTRY_IDS: int = 2

# Seuls formats acceptés par l'API (les autres sont rejetés en 400)
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}

# Retry réseau : backoff exponentiel plafonné + jitter, uniquement sur erreurs
# transitoires. Les erreurs de validation (400, 415, 422) échouent immédiatement.
RETRY_BASE_DELAY: float = 2.0
RETRY_MAX_DELAY: float = 30.0
RETRY_JITTER: float = 0.5
_TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

# Parsing d'un CV = plusieurs dizaines de secondes côté IA (upload S3 + LLM)
DEFAULT_TIMEOUT: float = 300.0
DEFAULT_BATCH_SIZE: int = 200
DEFAULT_SLEEP: float = 1.0
DEFAULT_RETRIES: int = 3

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(secret: str) -> str:
    """Masque un secret pour les logs : `bf80…ddbe`."""
    return f"{secret[:4]}…{secret[-4:]}" if len(secret) > 10 else "***"


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------
class UploadError(Exception):
    """Échec d'un envoi vers l'IA.

    `permanent=True` : inutile de réessayer (format refusé, métadonnées
    invalides, ...) — le CV est marqué en échec et la boucle continue.
    """

    def __init__(self, message: str, *, permanent: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.status = status


# ---------------------------------------------------------------------------
# UploadLedger — journal local (pattern JsonStore du projet)
# ---------------------------------------------------------------------------
class UploadLedger:
    """Trace locale des envois — clé = id global du CV (`south_africa_1013`).

    Filet de sécurité si le marquage backend échoue : on garde localement la
    preuve de l'envoi (point_id, user_id) pour ne pas repartir à zéro.
    """

    def __init__(self, file_path: Path = LEDGER_FILE) -> None:
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

    def has_sent(self, cv_id: str) -> bool:
        return self._data.get(cv_id, {}).get("status") == "sent"

    def set(self, cv_id: str, entry: dict[str, Any]) -> None:
        self._data[cv_id] = entry
        self.save()

    def count_sent(self) -> int:
        return sum(1 for e in self._data.values() if e.get("status") == "sent")


# ---------------------------------------------------------------------------
# BackendClient — lecture de la sélection + marquage
# ---------------------------------------------------------------------------
class BackendClient:
    """Client du backend Node.js (`/api/cvs`)."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def fetch_pending(
        self,
        *,
        page: int,
        limit: int,
        wave: str,
        country: str | None = None,
        include_sent: bool = False,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "limit": limit, "wave": wave}
        if country:
            params["country"] = country
        if include_sent:
            params["include_sent"] = "1"
        if max_attempts is not None:
            params["max_attempts"] = max_attempts

        resp = self.session.get(
            f"{self.base_url}/api/cvs/ia/pending",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def stats(self, *, wave: str, country: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"wave": wave}
        if country:
            params["country"] = country
        try:
            resp = self.session.get(
                f"{self.base_url}/api/cvs/ia/stats",
                params=params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # statistiques non bloquantes
            log.warning("Stats indisponibles : %s", exc)
            return {}

    def mark(self, cv_id: str, payload: dict[str, Any]) -> bool:
        """PATCH /api/cvs/{id}/ia — statut 'sent' | 'failed' | 'skipped'."""
        try:
            resp = self.session.patch(
                f"{self.base_url}/api/cvs/{cv_id}/ia",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.error("[BACKEND] Marquage %s (%s) impossible : %s", cv_id, payload.get("status"), exc)
            return False


# ---------------------------------------------------------------------------
# IaClient — POST /parse/resume
# ---------------------------------------------------------------------------
class IaClient:
    """Client de l'API IA WipWork."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        use_test: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, retries)
        self.use_test = use_test
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/parse/resume"

    @staticmethod
    def _detail(resp: requests.Response) -> str:
        """Message d'erreur lisible depuis `detail` (str) ou la liste FastAPI (422)."""
        try:
            payload = resp.json()
        except ValueError:
            return (resp.text or "")[:300]

        detail = payload.get("detail") if isinstance(payload, dict) else payload
        if isinstance(detail, list):
            return "; ".join(
                f"{'.'.join(str(p) for p in item.get('loc', []))}: {item.get('msg')}"
                if isinstance(item, dict)
                else str(item)
                for item in detail
            )
        return str(detail) if detail is not None else (resp.text or "")[:300]

    def upload_resume(
        self,
        *,
        file_path: Path,
        country_ids: list[str],
        enterprise_ids: list[str],
        enterprise_sources: dict[str, str],
        visibility: str,
        is_active: bool,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Poste un CV et retourne la réponse JSON. Lève `UploadError` en cas d'échec."""
        # FastAPI attend les listes en champs multipart répétés
        fields: list[tuple[str, str]] = [
            ("enterprise_sources", json.dumps(enterprise_sources)),
            ("is_active", "true" if is_active else "false"),
            ("visibility", visibility),
        ]
        fields += [("enterprise_ids", eid) for eid in enterprise_ids]
        fields += [("country_ids", cid) for cid in country_ids]
        if user_id:
            fields.append(("user_id", user_id))

        params = {"use_test": "true"} if self.use_test else None
        mime = SUPPORTED_EXTENSIONS[file_path.suffix.lower()]

        last_error: UploadError | None = None

        for attempt in range(1, self.retries + 1):
            try:
                with file_path.open("rb") as fh:
                    resp = self.session.post(
                        self.endpoint,
                        params=params,
                        data=fields,
                        files={"file": (file_path.name, fh, mime)},
                        timeout=self.timeout,
                    )
            except requests.RequestException as exc:
                last_error = UploadError(f"erreur réseau : {exc}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        raise UploadError(
                            "réponse 200 non JSON", permanent=True, status=200
                        ) from None

                detail = self._detail(resp)
                if resp.status_code in _TRANSIENT_STATUS_CODES:
                    last_error = UploadError(
                        f"HTTP {resp.status_code} : {detail}", status=resp.status_code
                    )
                else:
                    # 400 / 404 / 415 / 422 : rejeu inutile
                    raise UploadError(
                        f"HTTP {resp.status_code} : {detail}",
                        permanent=True,
                        status=resp.status_code,
                    )

            if attempt < self.retries:
                delay = min(RETRY_BASE_DELAY * 2 ** (attempt - 1), RETRY_MAX_DELAY)
                delay += random.uniform(0, RETRY_JITTER)
                log.warning(
                    "Tentative %d/%d échouée (%s) — nouvel essai dans %.1fs",
                    attempt,
                    self.retries,
                    last_error,
                    delay,
                )
                time.sleep(delay)

        raise last_error or UploadError("échec inconnu")


# ---------------------------------------------------------------------------
# Résolution du chemin local du CV
# ---------------------------------------------------------------------------
def _candidate_paths(cv: dict[str, Any], roots: list[Path]) -> Iterator[Path]:
    """Chemins possibles pour un CV, du plus précis au plus générique.

    `filepath` en base vient de la machine de scraping (souvent un chemin
    Windows type `C:\\Users\\user\\...`) : inexploitable ailleurs, d'où les
    replis `<root>/<country>/<filename>`.
    """
    raw = str(cv.get("filepath") or "").strip()
    country = str(cv.get("country") or "").strip()
    filename = str(cv.get("filename") or "").strip()

    if raw:
        yield Path(raw)
        if "\\" in raw:  # chemin Windows lu depuis un OS POSIX
            win = PureWindowsPath(raw)
            filename = filename or win.name
            if len(win.parts) >= 2:
                yield Path(win.parts[-2]) / win.name

    if not filename:
        return

    for root in roots:
        if country:
            yield root / country / filename
            yield root / "cv_files" / country / filename
        yield root / filename


def resolve_cv_path(cv: dict[str, Any], roots: list[Path]) -> Path | None:
    """Premier chemin existant parmi les candidats, ou None."""
    for candidate in _candidate_paths(cv, roots):
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # chemin invalide pour l'OS courant
            continue
    return None


# ---------------------------------------------------------------------------
# Options d'envoi
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class UploadOptions:
    enterprise_ids: list[str]
    source: str
    visibility: str
    is_active: bool
    cv_roots: list[Path]
    sleep: float = DEFAULT_SLEEP
    dry_run: bool = False
    mark: bool = True
    user_id_template: str | None = None
    extra_country_ids: list[str] = field(default_factory=list)

    @property
    def enterprise_sources(self) -> dict[str, str]:
        return {eid: self.source for eid in self.enterprise_ids}

    def user_id_for(self, cv: dict[str, Any]) -> str | None:
        if not self.user_id_template:
            return None  # l'API génère `external_user_<uuid>`
        return self.user_id_template.format(
            id=cv.get("id", ""),
            country=cv.get("country", ""),
            cv_id=cv.get("cv_id", ""),
        )


# ---------------------------------------------------------------------------
# Traitement d'un CV
# ---------------------------------------------------------------------------
def process_cv(
    cv: dict[str, Any],
    *,
    backend: BackendClient,
    ia: IaClient,
    ledger: UploadLedger,
    opts: UploadOptions,
) -> str:
    """Envoie un CV et retourne le libellé du résultat (clé de compteur)."""
    cv_id = str(cv.get("id") or "")
    label = f"{cv_id} ({cv.get('nom') or 'sans nom'})"

    # 1. Pays -> alpha-3 (obligatoire : les téléphones stockés sont locaux,
    #    non E.164, donc l'API ne peut pas déduire le pays).
    alpha3 = alpha3_for(cv.get("country"))
    if not alpha3:
        reason = f"pays inconnu '{cv.get('country')}' — ajouter le mapping dans scrapers/common/country_iso.py"
        log.warning("⏭️  %s : %s", label, reason)
        if opts.mark and not opts.dry_run:
            backend.mark(cv_id, {"status": "skipped", "reason": reason})
        return "skipped_country"

    country_ids = [alpha3, *opts.extra_country_ids][:MAX_COUNTRY_IDS]

    # 2. Fichier local
    path = resolve_cv_path(cv, opts.cv_roots)
    if path is None:
        reason = f"fichier introuvable ({cv.get('filepath') or cv.get('filename')})"
        log.warning("⏭️  %s : %s", label, reason)
        if opts.mark and not opts.dry_run:
            backend.mark(cv_id, {"status": "skipped", "reason": reason})
        return "skipped_file"

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        reason = f"format non supporté ({path.suffix or 'sans extension'})"
        log.warning("⏭️  %s : %s", label, reason)
        if opts.mark and not opts.dry_run:
            backend.mark(cv_id, {"status": "skipped", "reason": reason})
        return "skipped_format"

    user_id = opts.user_id_for(cv)

    if opts.dry_run:
        log.info(
            "🔍 [DRY-RUN] %s → country_ids=%s enterprise_ids=%s source=%s fichier=%s",
            label,
            country_ids,
            opts.enterprise_ids,
            opts.source,
            path,
        )
        return "dry_run"

    # 3. Envoi
    try:
        result = ia.upload_resume(
            file_path=path,
            country_ids=country_ids,
            enterprise_ids=opts.enterprise_ids,
            enterprise_sources=opts.enterprise_sources,
            visibility=opts.visibility,
            is_active=opts.is_active,
            user_id=user_id,
        )
    except UploadError as exc:
        log.error("❌ %s : %s", label, exc)
        ledger.set(
            cv_id,
            {
                "status": "failed",
                "at": _now_iso(),
                "file": str(path),
                "error": str(exc),
                "http_status": exc.status,
            },
        )
        if opts.mark:
            backend.mark(cv_id, {"status": "failed", "error": str(exc)})
        return "failed"

    operation = str(result.get("operation_type") or "created")
    log.info(
        "✅ %s → %s (point_id=%s, email=%s)",
        label,
        operation,
        result.get("point_id"),
        result.get("email") or "—",
    )

    ledger.set(
        cv_id,
        {
            "status": "sent",
            "at": _now_iso(),
            "file": str(path),
            "country_ids": country_ids,
            "operation_type": operation,
            "point_id": result.get("point_id"),
            "user_id": result.get("user_id"),
            "email": result.get("email"),
            "storage_path": result.get("storage_path"),
            "duplicate_replaced": result.get("duplicate_replaced"),
        },
    )

    if opts.mark:
        backend.mark(
            cv_id,
            {
                "status": "sent",
                "point_id": result.get("point_id"),
                "user_id": result.get("user_id"),
                "email": result.get("email"),
                "operation_type": operation,
                "storage_path": result.get("storage_path"),
                "country_ids": result.get("country_ids") or country_ids,
                "enterprise_ids": result.get("enterprise_ids") or opts.enterprise_ids,
                "duplicate_replaced": bool(result.get("duplicate_replaced")),
            },
        )

    return "updated" if operation == "updated" else "created"


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------
def run(
    *,
    backend: BackendClient,
    ia: IaClient,
    ledger: UploadLedger,
    opts: UploadOptions,
    wave: str,
    country: str | None,
    limit: int | None,
    batch_size: int,
    include_sent: bool,
    max_attempts: int | None,
) -> dict[str, int]:
    counters: dict[str, int] = {}
    processed: set[str] = set()
    page = 1

    first = backend.fetch_pending(
        page=1,
        limit=batch_size,
        wave=wave,
        country=country,
        include_sent=include_sent,
        max_attempts=max_attempts,
    )
    total = int(first.get("total") or 0)
    log.info(
        "🎯 %d CV éligibles (wave=%s%s) — %s",
        total,
        wave,
        f", country={country}" if country else "",
        f"limite {limit}" if limit else "aucune limite",
    )
    batch: dict[str, Any] | None = first

    try:
        while True:
            if limit is not None and len(processed) >= limit:
                break

            if batch is None:
                batch = backend.fetch_pending(
                    page=page,
                    limit=batch_size,
                    wave=wave,
                    country=country,
                    include_sent=include_sent,
                    max_attempts=max_attempts,
                )

            cvs = batch.get("cvs") or []
            batch = None
            if not cvs:
                break

            # Les CV envoyés quittent le filtre : on relit la page courante.
            # Si elle ne contient plus que des CV déjà traités (échecs, skips,
            # dry-run), on avance d'une page pour ne pas boucler.
            fresh = [c for c in cvs if str(c.get("id") or "") not in processed]
            if not fresh:
                page += 1
                continue

            for cv in fresh:
                if limit is not None and len(processed) >= limit:
                    break

                cv_id = str(cv.get("id") or "")
                if not cv_id:
                    log.warning("CV sans id ignoré : %s", cv.get("_id"))
                    continue

                processed.add(cv_id)

                if ledger.has_sent(cv_id) and not include_sent:
                    log.info("⏭️  %s déjà envoyé (journal local)", cv_id)
                    counters["skipped_ledger"] = counters.get("skipped_ledger", 0) + 1
                    continue

                outcome = process_cv(
                    cv, backend=backend, ia=ia, ledger=ledger, opts=opts
                )
                counters[outcome] = counters.get(outcome, 0) + 1

                done = len(processed)
                if done % 25 == 0:
                    log.info("📊 %d CV traités — %s", done, _format_counters(counters))

                if opts.sleep > 0 and not opts.dry_run:
                    time.sleep(opts.sleep)
    except KeyboardInterrupt:
        log.warning("⛔ Interruption manuelle — arrêt après %d CV", len(processed))

    return counters


def _format_counters(counters: dict[str, int]) -> str:
    if not counters:
        return "aucun CV traité"
    labels = {
        "created": "créés",
        "updated": "mis à jour",
        "failed": "échecs",
        "skipped_country": "pays inconnu",
        "skipped_file": "fichier introuvable",
        "skipped_format": "format refusé",
        "skipped_ledger": "déjà envoyés",
        "dry_run": "simulés",
    }
    return ", ".join(
        f"{count} {labels.get(key, key)}" for key, count in sorted(counters.items())
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Envoie les CV scrappés vers l'IA WipWork (POST /parse/resume)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--api-url", default=BACKEND_URL, help=f"backend Node.js (défaut {BACKEND_URL})")
    parser.add_argument("--ia-url", default=IA_API_URL, help=f"base de l'API IA (défaut {IA_API_URL})")
    parser.add_argument(
        "--api-key", default=IA_API_KEY,
        help="clé envoyée en en-tête X-API-Key (défaut : $IA_API_KEY, lu dans .env)",
    )
    parser.add_argument("--token", default=IA_API_TOKEN, help="jeton Bearer pour l'API IA")
    parser.add_argument(
        "--wave",
        default=str(DEFAULT_WAVE),
        help=f"valeur de commercial_email_wave (défaut {DEFAULT_WAVE}, 'all' pour ignorer)",
    )
    parser.add_argument("--country", help="restreindre à un pays (code interne, ex. south_africa)")
    parser.add_argument("--limit", type=int, help="nombre maximum de CV à traiter")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"taille de page lors de la lecture backend (défaut {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--cv-root", action="append", default=[],
        help="racine des fichiers CV, répétable (défaut : $CV_FILES_ROOT puis downloaded_files/cv_files)",
    )
    parser.add_argument(
        "--enterprise-id", action="append", default=[],
        help=f"enterprise_id, répétable (défaut {DEFAULT_ENTERPRISE_ID})",
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE, choices=ALLOWED_SOURCES,
        help=f"origine de l'upload pour enterprise_sources (défaut {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--visibility", default="visible", choices=ALLOWED_VISIBILITY,
        help="visibilité en recherche vectorielle (défaut visible)",
    )
    parser.add_argument(
        "--is-active", action="store_true",
        help="envoyer is_active=true (défaut : false, comme l'API)",
    )
    parser.add_argument(
        "--extra-country-id", action="append", default=[],
        help="code alpha-3 additionnel (l'API accepte 2 pays maximum)",
    )
    parser.add_argument(
        "--user-id-template",
        help="gabarit de user_id, ex. 'cv_scrappe_{id}' (défaut : généré par l'API)",
    )
    parser.add_argument("--use-test", action="store_true", help="cibler les collections de test de l'IA")
    parser.add_argument(
        "--max-attempts", type=int,
        help="ignorer les CV ayant déjà échoué ce nombre de fois",
    )
    parser.add_argument(
        "--include-sent", action="store_true",
        help="réenvoyer aussi les CV déjà marqués ia_sent",
    )
    parser.add_argument("--no-mark", action="store_true", help="ne rien écrire dans le backend")
    parser.add_argument("--dry-run", action="store_true", help="aucun envoi, affiche seulement ce qui partirait")
    parser.add_argument(
        "--sleep", type=float, default=DEFAULT_SLEEP,
        help=f"pause entre deux envois en secondes (défaut {DEFAULT_SLEEP})",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"timeout d'un envoi en secondes (défaut {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--retries", type=int, default=DEFAULT_RETRIES,
        help=f"tentatives par CV sur erreur transitoire (défaut {DEFAULT_RETRIES})",
    )
    args = parser.parse_args()

    # Racines de recherche des fichiers CV, par ordre de priorité
    cv_roots = [Path(p).expanduser() for p in args.cv_root]
    if CV_FILES_ROOT_ENV:
        cv_roots.append(Path(CV_FILES_ROOT_ENV).expanduser())
    cv_roots.append(CV_FILES_DIR)

    opts = UploadOptions(
        enterprise_ids=args.enterprise_id or [DEFAULT_ENTERPRISE_ID],
        source=args.source,
        visibility=args.visibility,
        is_active=args.is_active,
        cv_roots=cv_roots,
        sleep=max(0.0, args.sleep),
        dry_run=args.dry_run,
        mark=not args.no_mark,
        user_id_template=args.user_id_template,
        extra_country_ids=[c.strip().upper() for c in args.extra_country_id if c.strip()],
    )

    backend = BackendClient(args.api_url)
    ia = IaClient(
        args.ia_url,
        api_key=args.api_key,
        token=args.token,
        timeout=args.timeout,
        retries=args.retries,
        use_test=args.use_test,
    )
    ledger = UploadLedger()

    log.info("🚀 Backend : %s", backend.base_url)
    log.info(
        "🤖 IA : %s%s%s",
        ia.endpoint,
        " (use_test)" if args.use_test else "",
        " [DRY-RUN]" if args.dry_run else "",
    )
    log.info(
        "📦 enterprise_sources=%s visibility=%s is_active=%s",
        json.dumps(opts.enterprise_sources),
        opts.visibility,
        opts.is_active,
    )
    auth_modes: list[str] = []
    if args.api_key:
        auth_modes.append(f"X-API-Key {_mask(args.api_key)}")
    if args.token:
        auth_modes.append(f"Bearer {_mask(args.token)}")
    log.info(
        "🔐 Authentification : %s",
        ", ".join(auth_modes) or "aucune (IA_API_KEY / IA_API_TOKEN vides)",
    )

    stats = backend.stats(wave=args.wave, country=args.country)
    if stats:
        log.info(
            "📈 Avancement : %s éligibles, %s à envoyer, %s envoyés, %s en échec",
            stats.get("eligible"), stats.get("pending"), stats.get("sent"), stats.get("failed"),
        )

    try:
        counters = run(
            backend=backend,
            ia=ia,
            ledger=ledger,
            opts=opts,
            wave=args.wave,
            country=args.country,
            limit=args.limit,
            batch_size=max(1, args.batch_size),
            include_sent=args.include_sent,
            max_attempts=args.max_attempts,
        )
    except requests.RequestException as exc:
        log.error("Backend injoignable (%s) : %s", backend.base_url, exc)
        raise SystemExit(1) from exc

    log.info("🏁 Terminé — %s", _format_counters(counters))
    log.info("🗂️  Journal local : %s (%d envois réussis)", ledger.file_path, ledger.count_sent())


if __name__ == "__main__":
    main()

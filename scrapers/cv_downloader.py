from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from seleniumbase import SB

from scrapers.common.browser_helpers import maybe_solve_captcha
from scrapers.common.cv_parser import extraire_infos_cv
from scrapers.common.country_config import (
    OUTPUT_DIR,
    PROFILE_DIR_CV,
    CountryConfig,
    CvDownloadPattern,
)

_MAX_CLOUDFLARE_RETRIES = 3
_API_URL = os.getenv("SCRAPER_API_URL", "http://localhost:3000")


# ---------------------------------------------------------------------------
# Identifiants CV
# ---------------------------------------------------------------------------

def _extract_cv_id(filename: str) -> int | None:
    """Extrait l'identifiant numérique depuis un nom de fichier PDF."""
    match = re.search(r"(\d+)", filename)
    return int(match.group(1)) if match else None


def _global_id(country: str, cv_id: int) -> str:
    """Construit l'identifiant global unique : '{country}_{cv_id}'."""
    return f"{country}_{cv_id}"


# ---------------------------------------------------------------------------
# Vérification d'existence (backend > disque)
# ---------------------------------------------------------------------------

def _cv_exists_in_backend(global_id: str) -> bool:
    """Interroge le backend pour savoir si ce CV est déjà enregistré."""
    try:
        import requests
        resp = requests.get(
            f"{_API_URL}/api/cvs/{global_id}/exists",
            timeout=10,
        )
        return bool(resp.json().get("exists", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sauvegarde backend
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _save_cv_to_backend(
    global_id: str,
    country: str,
    cv_id: int,
    target: Path,
    infos: dict,
) -> None:
    """Envoie les données du CV vers le backend Node.js."""
    try:
        import requests
        payload = {
            "id": global_id,
            "country": country,
            "cv_id": cv_id,
            "filename": target.name,
            "filepath": str(target),
            "nom": infos.get("nom", ""),
            "emails": infos.get("emails", []),
            "telephones": infos.get("telephones", []),
            "texte": infos.get("texte", ""),
            "methode": infos.get("methode", ""),
            "sha256": _sha256_file(target),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "statut": "ok",
        }
        resp = requests.post(f"{_API_URL}/api/cvs", json=payload, timeout=15)
        resp.raise_for_status()
        print(f"[SAVE] Backend — {global_id}")
    except Exception as exc:
        print(f"[ERR] Sauvegarde backend échouée ({global_id}): {exc}")


# ---------------------------------------------------------------------------
# Injection de cookies manuels dans le navigateur
# ---------------------------------------------------------------------------

def _apply_cookie_header(sb: SB, base_url: str, cookie_header: str) -> None:
    cookie_pairs = [part.strip() for part in cookie_header.split(";") if "=" in part]
    if not cookie_pairs:
        return

    host = urlparse(base_url).hostname or ""
    for pair in cookie_pairs:
        name, value = pair.split("=", 1)
        if not name.strip():
            continue
        try:
            sb.driver.add_cookie({
                "name": name.strip(),
                "value": value.strip(),
                "domain": host,
                "path": "/",
            })
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Renouvellement de session Cloudflare
# ---------------------------------------------------------------------------

def _refresh_cloudflare_session(sb: SB, base_url: str) -> None:
    """Recharge le site pour renouveler la session Cloudflare sans fermer SB."""
    print(f"[CF] Renouvellement session Cloudflare — {base_url}")
    try:
        sb.goto(base_url)
        sb.sleep(3)
        maybe_solve_captcha(sb)
    except Exception as exc:
        print(f"[CF] Échec renouvellement: {exc}")
        return

    try:
        sb.driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass

    print("[CF] Session renouvelée")


# ---------------------------------------------------------------------------
# Téléchargement via CDP Network.loadNetworkResource
# ---------------------------------------------------------------------------

def _cdp_frame_id(sb: SB) -> str | None:
    try:
        tree = sb.driver.execute_cdp_cmd("Page.getFrameTree", {})
        return tree["frameTree"]["frame"]["id"]
    except Exception:
        return None


def _fetch_pdf_via_cdp(sb: SB, url: str) -> tuple[bytes | None, int]:
    """
    Récupère les octets d'un PDF via CDP Network.loadNetworkResource.
    Retourne (contenu_bytes, http_status).
    """
    params: dict = {
        "url": url,
        "options": {"disableCache": False, "includeCredentials": True},
    }
    frame_id = _cdp_frame_id(sb)
    if frame_id:
        params["frameId"] = frame_id

    try:
        result = sb.driver.execute_cdp_cmd("Network.loadNetworkResource", params)
    except Exception as exc:
        print(f"[ERR] CDP loadNetworkResource: {exc}")
        return None, 0

    resource = result.get("resource", {})
    http_status = int(resource.get("httpStatusCode") or 0)

    if not resource.get("success"):
        return None, http_status

    stream_handle = resource.get("stream")
    if not stream_handle:
        return None, http_status

    chunks: list[bytes] = []
    try:
        while True:
            read = sb.driver.execute_cdp_cmd(
                "IO.read",
                {"handle": stream_handle, "size": 65536},
            )
            data: str = read.get("data", "")
            eof: bool = read.get("eof", True)

            if data:
                chunk = (
                    base64.b64decode(data)
                    if read.get("base64Encoded", False)
                    else data.encode("latin-1")
                )
                chunks.append(chunk)

            if eof:
                break
    finally:
        try:
            sb.driver.execute_cdp_cmd("IO.close", {"handle": stream_handle})
        except Exception:
            pass

    return (b"".join(chunks) if chunks else None), http_status


# ---------------------------------------------------------------------------
# Pipeline complet pour un seul fichier PDF
# ---------------------------------------------------------------------------

def _process_one_cv(
    sb: SB,
    url: str,
    target: Path,
    country: str,
    base_url: str,
) -> bool:
    """
    Pipeline complet :
    1. Extraire cv_id depuis le nom du fichier.
    2. Vérifier existence en base (skip si déjà présent).
    3. Télécharger via CDP (avec récupération 403).
    4. Parser le PDF.
    5. Sauvegarder en backend.
    """
    filename = target.name
    cv_id = _extract_cv_id(filename)
    if cv_id is None:
        return False

    gid = _global_id(country, cv_id)

    # Vérification existence en base — la base est la référence
    if _cv_exists_in_backend(gid):
        print(f"[SKIP] {gid} déjà présent")
        return True  # comptabilisé comme OK (déjà traité)

    print(f"[DOWNLOAD] {gid}")

    # Téléchargement avec récupération 403
    cf_retries = 0
    content: bytes | None = None
    while cf_retries <= _MAX_CLOUDFLARE_RETRIES:
        raw, status = _fetch_pdf_via_cdp(sb, url)

        if status == 403:
            cf_retries += 1
            if cf_retries > _MAX_CLOUDFLARE_RETRIES:
                print(f"[403] Abandon après {_MAX_CLOUDFLARE_RETRIES} renouvellements: {url}")
                return False
            print(f"[403] Session expirée — renouvellement {cf_retries}/{_MAX_CLOUDFLARE_RETRIES}")
            _refresh_cloudflare_session(sb, base_url)
            time.sleep(2)
            continue

        if raw and raw.startswith(b"%PDF"):
            content = raw
        break

    if not content:
        return False

    target.write_bytes(content)

    # Parsing
    print(f"[PARSE] Extraction du texte — {gid}")
    infos = extraire_infos_cv(str(target))

    nb_emails = len(infos.get("emails", []))
    nb_phones = len(infos.get("telephones", []))
    print(f"[EMAILS] {nb_emails} trouvé(s)")
    print(f"[PHONES] {nb_phones} trouvé(s)")

    # Sauvegarde
    _save_cv_to_backend(gid, country, cv_id, target, infos)
    print(f"[DONE] {gid}")

    return True


# ---------------------------------------------------------------------------
# Parcours des plages
# ---------------------------------------------------------------------------

def _download_open_range(
    sb: SB,
    base_url: str,
    cv_base_url: str,
    country: str,
    output_dir: Path,
    pattern: CvDownloadPattern,
) -> int:
    downloaded = 0
    misses = 0
    index = pattern.start
    max_index = 20_000

    print(f"[INFO] Pattern '{pattern.name}' — départ index {pattern.start}, max {max_index}")

    print(f"[INFO] Pattern '{pattern.name}' — arrêt après {pattern.miss_threshold} échecs consécutifs")

    while misses < pattern.miss_threshold and index <= max_index:
        filename = pattern.template.format(index)
        url = f"{cv_base_url}{filename}"
        target = output_dir / filename

        ok = _process_one_cv(sb, url, target, country, base_url)
        if ok:
            downloaded += 1
            misses = 0
        else:
            misses += 1
            if misses <= 3:
                print(f"[MISS] {filename}")

        index += 1

    print(
        f"[STOP] Pattern '{pattern.name}' arrêté après "
        f"{pattern.miss_threshold} échecs consécutifs"
    )
    return downloaded


def _download_fixed_range(
    sb: SB,
    base_url: str,
    cv_base_url: str,
    country: str,
    output_dir: Path,
    pattern: CvDownloadPattern,
) -> int:
    if pattern.end is None:
        return 0

    downloaded = 0
    print(f"[INFO] Pattern '{pattern.name}' — plage {pattern.start}..{pattern.end}")

    for index in range(pattern.start, pattern.end + 1):
        filename = pattern.template.format(index)
        url = f"{cv_base_url}{filename}"
        target = output_dir / filename

        ok = _process_one_cv(sb, url, target, country, base_url)
        if ok:
            downloaded += 1

    print(f"[DONE] Pattern '{pattern.name}' terminé")
    return downloaded


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def download_country_cvs(config: CountryConfig, cookie_header: str | None = None) -> dict[str, int]:
    output_dir = OUTPUT_DIR / "cv_files" / config.code
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[START] Téléchargement CV — pays: {config.code}")

    patterns = config.cv_patterns

    if not patterns:
        print(f"[SKIP] Aucun pattern CV configuré pour le pays '{config.code}'")
        return {}

    stats: dict[str, int] = {}

    with SB(
        uc=True,
        locale="en",
        # user_data_dir=str(PROFILE_DIR_CV),
        disable_js=False,
        headless=True,
    ) as sb:
        sb.goto(config.base_url)
        maybe_solve_captcha(sb)

        env_cookie = os.getenv("EMPLOI_MA_COOKIE", "").strip()
        resolved_cookie = cookie_header or env_cookie

        if resolved_cookie:
            _apply_cookie_header(sb, config.base_url, resolved_cookie)
            sb.refresh()
            maybe_solve_captcha(sb)
            print("[AUTH] Cookie de session appliqué")
        else:
            print("[AUTH] Aucun cookie fourni — utilisation de la session profil navigateur")

        try:
            sb.driver.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass

        print(f"\n[CV] Démarrage téléchargements — pays: {config.code}")
        print(f"[CV] Dossier cible: {output_dir}")
        print(f"[CV] Base URL: {config.cv_base_url}")

        for pattern in patterns:
            print(f"\n[RUN] Téléchargement pattern: {pattern.name}")
            if pattern.end is None:
                count = _download_open_range(
                    sb=sb,
                    base_url=config.base_url,
                    cv_base_url=config.cv_base_url,
                    country=config.code,
                    output_dir=output_dir,
                    pattern=pattern,
                )
            else:
                count = _download_fixed_range(
                    sb=sb,
                    base_url=config.base_url,
                    cv_base_url=config.cv_base_url,
                    country=config.code,
                    output_dir=output_dir,
                    pattern=pattern,
                )
            stats[pattern.name] = count

    total = sum(stats.values())
    print("\n[SUMMARY]")
    for key, value in stats.items():
        print(f"  {key:<20} {value} fichier(s)")
    print(f"  {'TOTAL':<20} {total} fichier(s)")
    print(f"  {'Dossier':<20} {output_dir}")

    return stats


def download_public_cvs(cookie_header: str | None = None) -> dict[str, int]:
    """Compatibilité arrière: cible le Maroc si aucun pays n'est précisé."""
    from scrapers.countries import COUNTRIES

    print("[INFO] Téléchargement CV publics — pays par défaut: Maroc")  
    maroc = COUNTRIES.get("maroc")

    if maroc is None:
        print("[SKIP] Configuration pays 'maroc' introuvable")
        return {}
    
    return download_country_cvs(maroc, cookie_header=cookie_header)

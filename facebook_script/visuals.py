"""
facebook_script/visuals.py
==========================
Affiches des offres WipWork jointes aux publications de post_groups.py : une image
1080x1350 (portrait 4:5, format conseillé par Facebook) par tranche de POSTER_OFFERS
offres, numérotées comme dans le texte de la publication (1/4 = offres 1 à 10...).

Dessinées par le script, pas par une IA : page HTML aux couleurs de WipWork (bleu
marine, orange, bleu clair, logo assets/wipwork_logo.png) convertie en PNG par Chrome
headless. Le texte est donc exactement celui des offres — un générateur d'images écrit
40 intitulés avec des fautes. Gratuit, ~1 s par affiche.

Chrome : CHROME_PATH, sinon les emplacements habituels (Windows / Linux). Chaque rendu
utilise un profil temporaire : jamais celui d'un Chrome déjà ouvert (sinon la commande
serait transmise à ce Chrome au lieu de produire l'image).

Sortie : downloaded_files/facebook_share/posters/<date>/<groupe>_<n>.png ; dossiers de
plus de POSTER_KEEP_DAYS jours supprimés à chaque rendu.
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import share

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_FILE = ASSETS_DIR / "wipwork_logo.png"
POSTERS_DIR = share.OUTPUT_DIR / "posters"
POSTER_OFFERS = 10
POSTER_KEEP_DAYS = 3
WIDTH, HEIGHT = 1080, 1350
RENDER_TIMEOUT = 60

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

POSTER_TEMPLATE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ width: {width}px; height: {height}px; font-family: "Segoe UI", Arial, sans-serif; background: #f3f6fb;
         color: #0b1b4d; overflow: hidden; position: relative; }}
  header {{ background: #0a1a6e; color: #fff; padding: 40px 64px 32px; position: relative; overflow: hidden; }}
  header::after {{ content: ""; position: absolute; right: -120px; top: -120px; width: 380px; height: 380px;
                  border-radius: 50%; background: #f06000; opacity: .9; }}
  header::before {{ content: ""; position: absolute; right: 30px; bottom: -190px; width: 240px; height: 240px;
                   border-radius: 50%; background: #20a0f0; opacity: .55; }}
  .logo {{ background: #fff; border-radius: 14px; padding: 10px 18px; display: inline-block; position: relative; z-index: 1; }}
  .logo img {{ height: 64px; display: block; }}
  .logo b {{ display: block; font-size: 40px; color: #0a1a6e; line-height: 64px; }}
  h1 {{ font-size: 76px; font-weight: 800; letter-spacing: -1px; margin-top: 22px; position: relative; z-index: 1; }}
  .sub {{ font-size: 32px; opacity: .9; margin-top: 6px; position: relative; z-index: 1; }}
  .sub b {{ color: #ffb27a; }}
  ol {{ list-style: none; padding: 24px 64px 0; }}
  li {{ display: flex; gap: 22px; align-items: center; background: #fff; border-radius: 16px; padding: 14px 22px;
        margin-bottom: 10px; box-shadow: 0 2px 6px rgba(10, 26, 110, .08); height: 72px; }}
  .num {{ flex: 0 0 48px; height: 48px; border-radius: 50%; background: #f06000; color: #fff; font-weight: 800;
          font-size: 22px; display: flex; align-items: center; justify-content: center; }}
  li div {{ min-width: 0; }}
  .title {{ font-size: 27px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .meta {{ font-size: 21px; color: #4a5a8a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 2px; }}
  footer {{ position: absolute; bottom: 0; left: 0; right: 0; height: 120px; background: #0a1a6e; color: #fff;
            display: flex; align-items: center; justify-content: space-between; padding: 0 64px; }}
  footer .cta {{ font-size: 34px; font-weight: 700; }}
  footer .cta span {{ color: #20a0f0; }}
  footer .page {{ font-size: 24px; background: #f06000; border-radius: 30px; padding: 8px 22px; font-weight: 700; }}
</style></head><body>
<header>
  <div class="logo">{logo}</div>
  <h1>OFFRES D'EMPLOI</h1>
  <p class="sub">{country} · {date} · <b>{online} offres en ligne</b></p>
</header>
<ol>{rows}</ol>
<footer><p class="cta">Postulez sur <span>{site}</span></p><p class="page">{page}/{pages}</p></footer>
</body></html>"""


class VisualError(Exception):
    pass


def find_chrome() -> str:
    for candidate in (os.getenv("CHROME_PATH"), *CHROME_CANDIDATES, shutil.which("google-chrome"), shutil.which("chrome")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise VisualError("Chrome introuvable : définissez CHROME_PATH")


def poster_html(offers: list[dict[str, Any]], first_number: int, page: int, pages: int,
                country: str, today: date, online: int, site_url: str) -> str:
    rows = []
    for number, offer in enumerate(offers, first_number):
        meta = " · ".join(part for part in (share.owner_name(offer), share._location(offer)) if part)
        rows.append(
            f'<li><span class="num">{number}</span><div><p class="title">{html.escape(share._clean(offer.get("name")))}</p>'
            f'<p class="meta">{html.escape(meta)}</p></div></li>'
        )
    logo = f'<img src="{LOGO_FILE.as_uri()}" alt="">' if LOGO_FILE.exists() else "<b>WipWork</b>"
    site = site_url.split("://", 1)[-1].removeprefix("www.").rstrip("/")
    return POSTER_TEMPLATE.format(
        width=WIDTH, height=HEIGHT, logo=logo, country=html.escape(country), date=f"{today:%d/%m/%Y}",
        online=online, rows="".join(rows), site=html.escape(site), page=page, pages=pages,
    )


def _render(chrome: str, html_path: Path, png_path: Path) -> None:
    html_path, png_path = html_path.resolve(), png_path.resolve()  # as_uri() refuse un chemin relatif
    with tempfile.TemporaryDirectory(prefix="poster_chrome_", ignore_cleanup_errors=True) as profile:
        try:
            subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                 "--no-default-browser-check", f"--user-data-dir={profile}", "--force-device-scale-factor=1",
                 f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={png_path}", html_path.as_uri()],
                check=True, capture_output=True, timeout=RENDER_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VisualError(f"rendu Chrome en échec : {type(exc).__name__}") from exc
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise VisualError(f"aucune image produite : {png_path.name}")


def _remove_old_posters(today: date) -> None:
    if not POSTERS_DIR.is_dir():
        return
    limit = today - timedelta(days=POSTER_KEEP_DAYS)
    for folder in POSTERS_DIR.iterdir():
        with suppress(ValueError, OSError):
            if folder.is_dir() and date.fromisoformat(folder.name) < limit:
                shutil.rmtree(folder, ignore_errors=True)


def render_posters(offers: list[dict[str, Any]], name: str, country: str, today: date,
                   online: int, site_url: str) -> list[Path]:
    """Affiches des offres dans l'ordre donné (celui du texte), POSTER_OFFERS par image ; VisualError si échec."""
    chrome = find_chrome()
    _remove_old_posters(today)
    folder = POSTERS_DIR / today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    chunks = [offers[i : i + POSTER_OFFERS] for i in range(0, len(offers), POSTER_OFFERS)]
    paths = []
    for page, chunk in enumerate(chunks, 1):
        stem = f"{name}_{page}"
        html_path = folder / f"{stem}.html"
        html_path.write_text(
            poster_html(chunk, (page - 1) * POSTER_OFFERS + 1, page, len(chunks), country, today, online, site_url),
            encoding="utf-8",
        )
        png_path = folder / f"{stem}.png"
        _render(chrome, html_path, png_path)
        html_path.unlink(missing_ok=True)
        paths.append(png_path)
    return paths

"""
facebook_script/share.py
========================
Prépare les publications Facebook des offres d'emploi WipWork, à partager dans un groupe.

Meta a supprimé l'API Groups (publish_to_groups) en avril 2024 : aucune application ne
peut plus publier dans un groupe. Le script s'arrête donc à des textes prêts à coller ;
la publication (ou sa programmation, possible pour un admin depuis la zone de rédaction
du groupe) reste manuelle.

Source : GET https://bo.wipwork.com/api/job/offers/search?page=N&size=50&pays=Madagascar
(production ; bo.wipwork.fr = serveur de test, --api-url pour le viser). size plafonné à 50
par l'API, toutes les pages sont lues. Lien de chaque offre : https://www.wipwork.com/jobs/<slug>
(balises og: présentes : Facebook affiche titre, description et image de l'offre).

Offres retenues, les plus récentes d'abord :
  - actives, publiques et modérées (moderationStatus "approve") ;
  - encore valables au moins --min-days-left jours (expiresAt) ;
  - hors comptes de test (nom contenant "test", "essai", "demo" ou un email, nom
    "entreprise") et hors noms de share_excluded_owners.txt ;
  - hors doublons (même entreprise + même intitulé + même lieu : 300 sur 1 913 le 16/10/2026) ;
  - jamais déjà préparées (share_state_<pays>.json, --again pour les reprendre).

Champs non affichés quand ils valent la valeur par défaut des offres importées : 1 741
offres sur 1 913 sont en "CDI", stages compris — afficher "CDI" sur un stage induirait en
erreur ; idem "Non spécifié" pour le lieu et l'expérience. Missions / profil = lignes à
puces rangées sous les rubriques « Missions… » / « Profil… » des tâches et de la
description ; les puces placées avant toute rubrique comptent comme missions, sauf sous
l'en-tête d'une publication récapitulative importée (« 📢 NOS OFFRES D'EMPLOI… »,
« opportunités d'emploi du jour ») : elles y listent d'autres offres.

Usage :
    python facebook_script/share.py --preview                 # aperçu, rien n'est marqué comme préparé
    python facebook_script/share.py                           # 10 offres, une publication par offre
    python facebook_script/share.py --limit 15 --digest       # une seule publication récapitulative
    python facebook_script/share.py --group-url https://www.facebook.com/groups/... --open

Sortie : downloaded_files/facebook_share/share_<pays>_<date>.html (bouton « Copier » par
publication) et .txt ; share_state_<pays>.json = offres déjà préparées.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
import webbrowser
from collections import Counter
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files" / "facebook_share"
EXCLUDED_OWNERS_FILE = SCRIPT_DIR / "share_excluded_owners.txt"

DEFAULT_OFFERS_API = "https://bo.wipwork.com/api/job/offers/search"  # bo.wipwork.fr = serveur de test
DEFAULT_SITE_URL = "https://www.wipwork.com"
DEFAULT_COUNTRY = "Madagascar"
PAGE_SIZE = 50  # plafond de l'API, même si on demande plus
REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3

DEFAULT_LIMIT = 10
MAX_MISSIONS = 4
MAX_LINE_CHARS = 160
MAX_LEAD_CHARS = 280
MIN_LEAD_CHARS = 40  # plus court : "Rejoignez notre équipe.", "Offre de stage en conformité."
MAX_EXPERIENCE_CHARS = 60
MAX_HASHTAGS = 6  # un récapitulatif de 10 offres en aurait autant que de secteurs

TEST_OWNER_RE = re.compile(r"\b(?:test\w*|demo)\b|essai")  # sur le nom plié (wipEssaie, test@test.com)
GENERIC_OWNER_NAMES = {"entreprise"}
# Valeurs par défaut des offres importées (pliées) : jamais affichées
DEFAULT_VALUES = {
    "contrat": {"cdi"},
    "lieu": {"non specifie"},
    "experience": {"non specifie"},
}
MAX_PROFILE_LINES = 3
MAX_HEADING_CHARS = 40
BULLET_RE = re.compile(r"^\s*(?:[•\-*▪◦●➢➤➡→✓✔✅►▶·]|\d{1,2}[.)])\s*")
# En-tête d'une publication récapitulative importée telle quelle : ses puces (avant toute rubrique) sont d'autres offres
GENERIC_TEXT_RE = re.compile(
    r"offres? d.?emploi|opportunites? d.?emploi|nos offres|avis de recrutement|rejoignez notre equipe|tolotr ?asa"
)
# Intitulés de rubrique (texte plié, ligne courte sans puce)
MISSIONS_HEADING_RE = re.compile(r"^(?:vos |les |principales )*(?:missions?|taches|responsabilites|activites|attributions)\b")
PROFILE_HEADING_RE = re.compile(r"^(?:le |votre )*(?:profils?|competences|qualifications|exigences|prerequis|qualites)\b")
OTHER_HEADING_RE = re.compile(
    r"^(?:avantages?|conditions|dossiers?|candidatures?|comment postuler|pour postuler|nous offrons|ce que nous offrons|"
    r"contacts?|date limite)\b"
)


class ShareError(Exception):
    pass


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------


def _fold(value: str | None) -> str:
    """Minuscules, sans accents ni ponctuation : comparaison de noms et d'intitulés."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _clean(value: Any) -> str:
    return re.sub(r"[ \t ]+", " ", str(value or "")).strip()


def _lines(text: Any) -> list[str]:
    return [line for line in (_clean(raw) for raw in str(text or "").replace("\r", "\n").split("\n")) if line]


def _shorten(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:") + "…"


def _is_generic(line: str) -> bool:
    return "📢" in line or bool(GENERIC_TEXT_RE.search(_fold(line)))


def _hashtag(value: str) -> str:
    return "#" + "".join(word[:1].upper() + word[1:] for word in re.findall(r"\w+", value))


# ---------------------------------------------------------------------------
# API WipWork
# ---------------------------------------------------------------------------


def _get_page(session: requests.Session, api_url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Nouvelles tentatives sur erreur réseau / 5xx ; 4xx ou réponse illisible = erreur immédiate."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(api_url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            error = type(exc).__name__
        else:
            if response.status_code < 500:
                if response.status_code >= 400:
                    raise ShareError(f"HTTP {response.status_code} : {response.text[:200]}")
                try:
                    payload = response.json()
                except ValueError:
                    raise ShareError(f"réponse non JSON : {response.text[:200]}") from None
                if payload.get("isError") or not isinstance(payload.get("data"), dict):
                    raise ShareError(f"réponse inattendue : {response.text[:200]}")
                return payload["data"]
            error = f"HTTP {response.status_code}"
        if attempt == MAX_ATTEMPTS:
            raise ShareError(error)
        time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def fetch_offers(api_url: str, country: str) -> tuple[list[dict[str, Any]], int]:
    """Toutes les pages ; renvoie (offres uniques, total annoncé par l'API)."""
    session = requests.Session()
    offers: dict[str, dict[str, Any]] = {}
    page, total_pages, announced = 1, 1, 0
    while page <= total_pages:
        data = _get_page(session, api_url, {"page": page, "size": PAGE_SIZE, "pays": country})
        total_pages = int(data.get("totalPages") or 1)
        announced = int(data.get("total") or 0)
        for item in data.get("items") or []:
            if item.get("id"):
                offers.setdefault(item["id"], item)  # tri instable côté API : une offre peut revenir sur deux pages
        page += 1
    return list(offers.values()), announced


# ---------------------------------------------------------------------------
# Sélection
# ---------------------------------------------------------------------------


def owner_name(offer: dict[str, Any]) -> str:
    return _clean((offer.get("proprietaire") or {}).get("name"))


def offer_key(offer: dict[str, Any]) -> str:
    """
    Même entreprise + même intitulé (sans « H/F ») + même lieu = même offre republiée. Le lieu sépare les postes
    homonymes d'une entreprise dans deux villes (LCS RH : coordinateurs trade marketing à Antananarivo ET à Manakara).
    """
    title = re.sub(r"\b(?:h f|f h)\b", " ", _fold(offer.get("name")))
    return f"{_fold(owner_name(offer))}|{' '.join(title.split())}|{_fold(_location(offer))}"


def deadline(offer: dict[str, Any]) -> date | None:
    with suppress(TypeError, ValueError):
        return datetime.fromisoformat(str(offer["expiresAt"]).replace("Z", "+00:00")).astimezone().date()
    return None


def load_excluded_owners(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {_fold(line) for line in lines if line}


def is_test_owner(owner: str, excluded_owners: set[str]) -> bool:
    folded = _fold(owner)
    return "@" in owner or bool(TEST_OWNER_RE.search(folded)) or folded in GENERIC_OWNER_NAMES or folded in excluded_owners


def _skip_reason(offer: dict[str, Any], excluded_owners: set[str], min_days_left: int, today: date) -> str | None:
    if offer.get("status") != "active" or not offer.get("isPublic") or offer.get("moderationStatus") != "approve":
        return "non publiée"
    if is_test_owner(owner_name(offer), excluded_owners):
        return "compte de test ou exclu"
    if not offer.get("slug") or not _fold(offer.get("name")):
        return "sans lien ou sans intitulé"
    expires = deadline(offer)
    if expires is not None and (expires - today).days < min_days_left:
        return "expirée ou expire trop tôt"
    return None


def select_offers(
    offers: list[dict[str, Any]],
    state: dict[str, dict[str, Any]],
    excluded_owners: set[str],
    min_days_left: int,
    today: date,
    include_prepared: bool,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Offres à partager, plus récentes d'abord (un doublon garde sa version la plus récente)."""
    prepared_keys = set() if include_prepared else {entry.get("key") for entry in state.values()}
    seen_keys: set[str] = set()
    kept: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    for offer in sorted(offers, key=lambda o: str(o.get("createdAt") or ""), reverse=True):
        key = offer_key(offer)
        reason = _skip_reason(offer, excluded_owners, min_days_left, today)
        if reason is None and not include_prepared and (offer["id"] in state or key in prepared_keys):
            reason = "déjà préparée"
        if reason is None and key in seen_keys:
            reason = "doublon"
        if reason:
            skipped[reason] += 1
            continue
        seen_keys.add(key)
        kept.append(offer)
    return kept, skipped


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------


def offer_url(offer: dict[str, Any], site_url: str) -> str:
    return f"{site_url.rstrip('/')}/jobs/{quote(str(offer['slug']), safe='')}"


def _shown(offer: dict[str, Any], field: str) -> str:
    value = _clean(offer.get(field))
    return "" if _fold(value) in DEFAULT_VALUES.get(field, set()) else value


def _location(offer: dict[str, Any]) -> str:
    lieu = _shown(offer, "lieu")
    return "" if _fold(lieu) == _fold(offer.get("name")) else lieu  # import raté : lieu = intitulé


def _sections(text: Any) -> dict[str, list[str]]:
    """Puces rangées sous leur rubrique ; avant toute rubrique = missions, sauf après un en-tête récapitulatif."""
    lines = _lines(text)
    found: dict[str, list[str]] = {"missions": [], "profil": []}
    current = None if lines and _is_generic(lines[0]) else "missions"
    for line in lines:
        if BULLET_RE.match(line):
            item = BULLET_RE.sub("", line)
            if current and len(item) >= 3:
                found[current].append(_shorten(item, MAX_LINE_CHARS))
        elif len(line) <= MAX_HEADING_CHARS:
            folded = _fold(line)
            if MISSIONS_HEADING_RE.match(folded):
                current = "missions"
            elif PROFILE_HEADING_RE.match(folded):
                current = "profil"
            elif OTHER_HEADING_RE.match(folded):
                current = None
    return found


def missions_and_profile(offer: dict[str, Any]) -> tuple[list[str], list[str]]:
    tasks, description = _sections(offer.get("taches")), _sections(offer.get("description"))
    missions = tasks["missions"] or description["missions"]
    profile = description["profil"] or tasks["profil"]
    return missions[:MAX_MISSIONS], profile[:MAX_PROFILE_LINES]


def lead(offer: dict[str, Any]) -> str:
    lines = _lines(offer.get("description"))
    if not lines:
        return ""
    first = lines[0]
    if BULLET_RE.match(first) or _is_generic(first) or len(first) < MIN_LEAD_CHARS or _fold(first) == _fold(offer.get("name")):
        return ""
    if _fold(first) in {_fold(BULLET_RE.sub("", line)) for line in _lines(offer.get("taches"))}:
        return ""  # déjà la première mission
    return _shorten(first, MAX_LEAD_CHARS)


def hashtags(country: str, offers: list[dict[str, Any]]) -> str:
    tags = ["#emploi", "#recrutement", _hashtag(country)]
    for offer in offers:
        sector = _clean(offer.get("type"))
        if sector and _fold(sector) != "autre" and _hashtag(sector) not in tags and len(tags) < MAX_HASHTAGS:
            tags.append(_hashtag(sector))
    return " ".join(tags)


def offer_post(offer: dict[str, Any], site_url: str, country: str) -> str:
    title = _clean(offer.get("name"))
    lines = [f"{'🚨 URGENT — ' if offer.get('urgent') else ''}📢 OFFRE D'EMPLOI : {title}"]
    if owner := owner_name(offer):
        lines.append(f"🏢 {owner}")
    if location := _location(offer):
        lines.append(f"📍 {location}")
    if contract := _shown(offer, "contrat"):
        lines.append(f"📄 {contract}")
    experience = _shown(offer, "experience")
    if experience and len(experience) <= MAX_EXPERIENCE_CHARS:
        lines.append(f"🎓 Expérience : {experience}")
    if expires := deadline(offer):
        lines.append(f"⏳ Valable jusqu'au {expires:%d/%m/%Y}")
    if text := lead(offer):
        lines += ["", text]
    missions, profile = missions_and_profile(offer)
    if missions:
        lines += ["", "Missions :", *(f"• {item}" for item in missions)]
    if profile:
        lines += ["", "Profil recherché :", *(f"• {item}" for item in profile)]
    lines += ["", f"👉 Postulez sur WipWork : {offer_url(offer, site_url)}", "", hashtags(country, [offer])]
    return "\n".join(lines)


def digest_post(offers: list[dict[str, Any]], site_url: str, country: str, today: date, header: str | None = None) -> str:
    lines = [header or f"📢 OFFRES D'EMPLOI DU {today:%d/%m/%Y} — {country.upper()}", ""]
    for number, offer in enumerate(offers, 1):
        owner = owner_name(offer)
        location = _location(offer)
        lines.append(f"{number}. {_clean(offer.get('name'))}{f' — {owner}' if owner else ''}{f' ({location})' if location else ''}")
        lines += [f"👉 {offer_url(offer, site_url)}", ""]
    lines += [f"Toutes les offres : {site_url.rstrip('/')}/jobs", "", hashtags(country, offers)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sorties
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Offres à partager</title>
<style>
  :root {{ --bg: #f4f5f7; --card: #ffffff; --text: #1c1e21; --muted: #65676b; --line: #dadde1;
          --accent: #1877f2; --accent-text: #ffffff; --done: #e7f3e8; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #18191a; --card: #242526; --text: #e4e6eb; --muted: #b0b3b8; --line: #3e4042;
            --accent: #2d88ff; --accent-text: #ffffff; --done: #1f3322; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 15px/1.45 system-ui, sans-serif; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: var(--muted); margin: 0 0 20px; }}
  .group {{ display: inline-block; margin-bottom: 20px; }}
  .post {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
  .post.done {{ background: var(--done); }}
  .post header {{ display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-bottom: 10px; }}
  .post h2 {{ font-size: 15px; margin: 0; color: var(--muted); font-weight: 600; }}
  pre {{ white-space: pre-wrap; word-wrap: break-word; font: inherit; margin: 0; }}
  .actions {{ display: flex; gap: 8px; }}
  button, a.button {{ border: 0; border-radius: 6px; padding: 7px 14px; font: inherit; font-weight: 600; cursor: pointer;
                      background: var(--accent); color: var(--accent-text); text-decoration: none; }}
  a.button.secondary {{ background: transparent; color: var(--accent); border: 1px solid var(--line); }}
</style>
</head>
<body>
<main>
<h1>Offres à partager</h1>
<p class="meta">{meta}</p>
{group_link}
{posts}
</main>
<script>
async function copyPost(button) {{
  const post = button.closest('.post');
  const text = post.querySelector('pre').textContent;
  try {{
    await navigator.clipboard.writeText(text);
  }} catch (error) {{
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }}
  button.textContent = 'Copié ✓';
  post.classList.add('done');
}}
</script>
</body>
</html>
"""


def render_html(posts: list[tuple[str, str, str]], meta: str, group_url: str | None) -> str:
    """posts = [(titre, texte, lien de l'offre)]."""
    cards = []
    for number, (title, text, url) in enumerate(posts, 1):
        cards.append(
            f'<section class="post"><header><h2>{number}/{len(posts)} · {html.escape(title)}</h2>'
            f'<div class="actions"><a class="button secondary" href="{html.escape(url)}" target="_blank" rel="noopener">Voir</a>'
            f'<button type="button" onclick="copyPost(this)">Copier</button></div></header>'
            f"<pre>{html.escape(text)}</pre></section>"
        )
    group_link = (
        f'<a class="button group" href="{html.escape(group_url)}" target="_blank" rel="noopener">Ouvrir le groupe Facebook</a>'
        if group_url
        else ""
    )
    return PAGE_TEMPLATE.format(meta=html.escape(meta), group_link=group_link, posts="\n".join(cards))


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offres WipWork -> publications Facebook prêtes à coller dans un groupe")
    parser.add_argument("--country", default=DEFAULT_COUNTRY, help=f"pays de l'API (paramètre pays, défaut {DEFAULT_COUNTRY})")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"offres préparées par lancement (défaut {DEFAULT_LIMIT}, 0 = toutes)")
    parser.add_argument("--digest", action="store_true", help="une seule publication récapitulative au lieu d'une par offre")
    parser.add_argument("--min-days-left", type=int, default=1, help="jours de validité restants minimum (défaut 1 : pas les offres qui expirent aujourd'hui)")
    parser.add_argument("--preview", action="store_true", help="aperçu : affiche les publications sans les marquer comme préparées")
    parser.add_argument("--again", action="store_true", help="reprend aussi les offres déjà préparées")
    parser.add_argument("--group-url", help="lien du groupe Facebook (bouton « Ouvrir le groupe » dans la page)")
    parser.add_argument("--open", action="store_true", help="ouvre la page générée dans le navigateur")
    parser.add_argument("--excluded-owners-file", default=str(EXCLUDED_OWNERS_FILE), help="entreprises jamais partagées, une par ligne")
    parser.add_argument("--api-url", default=os.getenv("WIPWORK_OFFERS_API_URL", DEFAULT_OFFERS_API))
    parser.add_argument("--site-url", default=os.getenv("WIPWORK_SITE_URL", DEFAULT_SITE_URL))
    return parser.parse_args()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")  # emojis des publications sur une console cp1252

    args = parse_args()
    today = date.today()
    country_code = _fold(args.country).replace(" ", "_")
    state_path = OUTPUT_DIR / f"share_state_{country_code}.json"

    try:
        offers, announced = fetch_offers(args.api_url, args.country)
    except ShareError as exc:
        print(f"[ERREUR] API des offres ({args.api_url}) : {exc}")
        sys.exit(1)
    state = load_state(state_path)
    selected, skipped = select_offers(
        offers, state, load_excluded_owners(Path(args.excluded_owners_file)), args.min_days_left, today, args.again
    )
    print(f"[INFO] {len(offers)} offres uniques ({announced} annoncées par l'API), {len(selected)} à partager")
    for reason, count in skipped.most_common():
        print(f"       écartées - {reason} : {count}")

    if args.limit > 0:
        selected = selected[: args.limit]
    if not selected:
        print("[INFO] Rien de nouveau à partager.")
        return

    if args.digest:
        posts = [(f"Récapitulatif ({len(selected)} offres)", digest_post(selected, args.site_url, args.country, today), f"{args.site_url.rstrip('/')}/jobs")]
    else:
        posts = [(_clean(o.get("name")), offer_post(o, args.site_url, args.country), offer_url(o, args.site_url)) for o in selected]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = OUTPUT_DIR / f"share_{country_code}_{stamp}.html"
    txt_path = html_path.with_suffix(".txt")
    meta = f"{args.country} · {len(selected)} offre(s) · {datetime.now():%d/%m/%Y %H:%M}" + (" · aperçu" if args.preview else "")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(posts, meta, args.group_url), encoding="utf-8")
    txt_path.write_text("\n\n==========\n\n".join(text for _, text, _ in posts) + "\n", encoding="utf-8")

    if args.preview:
        for _, text, _ in posts:
            print(f"\n{'-' * 60}\n{text}")
        print(f"\n[INFO] Aperçu : rien n'est marqué comme préparé (relancez sans --preview).")
    else:
        prepared_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for offer in selected:
            state[offer["id"]] = {
                "key": offer_key(offer),
                "title": _clean(offer.get("name")),
                "company": owner_name(offer),
                "url": offer_url(offer, args.site_url),
                "prepared_at": prepared_at,
            }
        _write_json_atomic(state_path, state)
        print(f"[INFO] {len(selected)} offre(s) marquée(s) comme préparée(s) dans {state_path.name} (--again pour les reprendre)")
    print(f"[INFO] Page à copier : {html_path}")
    print(f"[INFO] Texte brut    : {txt_path}")
    if args.open:
        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    main()

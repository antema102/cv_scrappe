"""
facebook_script/post_groups.py
==============================
Publie les offres WipWork dans des groupes Facebook : dans chaque groupe de
facebook_script/share_groups.txt, les --per-post (40) offres les plus récentes jamais encore
publiées dans CE groupe : ~40 nouvelles offres par jour, ce sont donc celles du jour.

La publication (post_text) liste les offres numérotées, chacune avec son lien
wipwork.com/jobs/<slug>, + les affiches jointes (visuals.py : une par tranche de 10 offres,
même numérotation ; --no-images pour le texte seul). --single-link : un seul lien
(wipwork.com/jobs) + 6 exemples de postes, repli si Facebook bloque les publications à liens
multiples (constaté le 19/10/2026). Pas de commentaire : sous une publication en attente
d'approbation, Facebook n'ouvre pas les commentaires.

Pas d'API possible : Meta a supprimé l'API Groups (publish_to_groups) en avril 2024. Le
script pilote Chrome (SeleniumBase CDP, profil persistant connecté à la main) comme une
personne : ouvre le groupe, clique sur « Exprimez-vous… », colle le texte (événement
paste lu par l'éditeur Lexical de Facebook ; repli : frappe ligne par ligne via CDP),
puis clique sur « Publier » et attend la fermeture de la fenêtre de rédaction.

RISQUE (accepté par l'utilisateur le 16/10/2026) : publier automatiquement des messages
semblables dans 20 groupes est ce que Facebook détecte comme spam (publication bloquée
quelques jours, vérification du compte, voire liens wipwork.com signalés), et c'est
contraire aux conditions de Meta. Garde-fous :
  - profil dédié my_custom_profile_facebook_share (--profile) : un compte restreint ne
    casse pas le scraping (pages.py / scraper.py), et pas de conflit de verrou Chrome
    avec run_daily.py ;
  - par défaut automatique et sans fenêtre (headless) ; --assisted : fenêtre visible, le
    script remplit la publication, vous la relisez dans Chrome, ENTRÉE dans le terminal
    pour publier ; --visible : fenêtre visible, publication toujours automatique ;
  - pause aléatoire de --delay-min à --delay-max secondes (60 à 75 s) entre deux groupes ;
  - au plus une publication par groupe toutes les --min-hours heures (20) ;
  - en-tête et ordre des offres différents d'un groupe à l'autre ;
  - arrêt de tout le lancement sur un message de blocage / limite / spam de Facebook,
    sur une page de connexion, ou après MAX_FAILURES_IN_A_ROW échecs d'affilée.

Le compte doit être membre des groupes (sinon groupe ignoré : « not_member »). Publication
soumise à l'approbation des admins = « pending », comptée comme publiée.

Usage :
    python facebook_script/post_groups.py --preview          # textes et affiches par groupe, sans publier
    python facebook_script/post_groups.py                    # automatique, sans fenêtre (= PM2)
    python facebook_script/post_groups.py --max-groups 5
    python facebook_script/post_groups.py --visible          # automatique, fenêtre Chrome visible
    python facebook_script/post_groups.py --assisted         # fenêtre visible, ENTRÉE pour publier chaque groupe
    python facebook_script/post_groups.py --group-url https://www.facebook.com/groups/... --per-post 5
    python facebook_script/post_groups.py --login            # (re)connecter le profil (fenêtre visible)

--auto et --headless (anciens noms, comportement par défaut aujourd'hui) restent acceptés.

PM2 (ecosystem.config.js, app "fb-share-groups") : une fois par jour (cron_restart). Sans
terminal, aucune question n'est posée : session expirée = arrêt avec message, reconnecter
à la main avec --login.

Suivi : downloaded_files/facebook_share/group_posts_<pays>.json (par groupe : offres
publiées, date de la dernière publication, historique des tentatives).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import sys
import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import scraper as fb
import share
import visuals
from mycdp import dom as cdp_dom
from mycdp import input_ as cdp_input
from mycdp import page as cdp_page
from mycdp import runtime as cdp_runtime
from seleniumbase import SB

GROUPS_FILE = fb.SCRIPT_DIR / "share_groups.txt"
PROFILE_DIR = fb.PROJECT_ROOT / "my_custom_profile_facebook_share"
DEBUG_DIR = share.OUTPUT_DIR / "debug"  # capture + boutons visibles d'un groupe en échec

DEFAULT_PER_POST = 40  # ~40 nouvelles offres par jour sur WipWork Madagascar : celles du jour
DEFAULT_MIN_OFFERS = 3  # moins d'offres nouvelles pour un groupe : pas de publication
DEFAULT_MIN_HOURS = 20
DEFAULT_DELAY_MIN = 60
DEFAULT_DELAY_MAX = 75  # un peu de hasard : un rythme parfaitement régulier se repère
PAGE_LOAD_WAIT = 6
COMPOSER_TIMEOUT = 25
COMPOSER_CLICKS = 2
FILL_WAIT = 1.5  # Lexical met le DOM à jour après l'événement paste
LINK_PREVIEW_WAIT = 3  # publication texte seul : laisse Facebook traiter le texte collé avant « Publier »
PUBLISH_ENABLED_TIMEOUT = 10
PUBLISH_TIMEOUT = 45
NOTICE_WAIT = 4  # message « en attente d'approbation » affiché juste après la fermeture
IMAGE_UPLOAD_TIMEOUT = 60  # vignettes des affiches dans la fenêtre / « Publier » réactivé après l'envoi des photos
PUBLISH_IMAGES_TIMEOUT = 120  # fermeture de la fenêtre après « Publier » quand des photos partent avec
MAX_FAILURES_IN_A_ROW = 3

# Un seul lien dans la publication (Facebook bloque une publication de groupe qui en contient beaucoup)
EXAMPLE_OFFERS = 6  # postes cités dans la publication
MAX_CITIES = 4
MAX_CITY_CHARS = 30  # au-delà, c'est une adresse (« Immeuble Le Colisée Tsiadana Tana 101 »), pas une ville
# Message affiché DANS la publication tant qu'un admin ne l'a pas approuvée (texte plié), pas dans une alerte
# (vu le 19/10/2026 : « Votre publication est en attente d'approbation par un admin »)
PENDING_POST_PATTERN = r"publication est en attente|en attente d approbation|pending approval|awaiting approval"
# Texte que Facebook transforme en lien : les exemples de postes qui en contiennent sont écartés (un seul lien voulu)
LINK_LIKE_RE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|mg|fr|org|net|io|co|info|biz)\b", re.I)

HEADERS = (
    "📢 OFFRES D'EMPLOI DU {date} — {country_upper}",
    "💼 {count} offres d'emploi à pourvoir — {country}, {date}",
    "🔎 Vous cherchez un emploi ? Voici {count} offres du {date}",
    "📌 Ils recrutent à {country} — sélection du {date}",
)

COMPOSER_LABELS = (
    r"exprimez-vous|[ée]crivez quelque chose|write something|cr[ée]er une publication|create a public post|"
    r"vous voulez dire quelque chose|quoi de neuf|what'?s on your mind"
)
PUBLISH_LABELS = r"^(?:publier|post|publish)$"
PHOTO_LABELS = r"^(?:photo/vid[ée]o|photo/video|photos?/vid[ée]os?|photos?)$"
JOIN_LABELS = r"^(?:rejoindre le groupe|join group|rejoindre|join)$"
# Sur le texte plié (share._fold) des boîtes de dialogue / alertes apparues après le clic sur « Publier »
BLOCK_RE = re.compile(
    r"vous ne pouvez pas|you can t|temporairement|temporarily|bloque|blocked|limit|spam|standards de la communaute|"
    r"community standards|reessayez plus tard|try again later|nous avons supprime|we removed"
)
PENDING_RE = re.compile(r"en attente|pending|approbation|approval|examin|review")

# Bouton « Exprimez-vous… » du groupe (point à cliquer), fenêtre déjà ouverte, bouton « Rejoindre » visible.
# Un « Rejoindre » ne prouve PAS que le compte n'est pas membre (groupes suggérés, discussions de groupe...) :
# seul compte le bouton « Exprimez-vous… » (open_composer).
COMPOSER_JS = r"""
(() => {
  const ARGS = __ARGS__;
  const label = new RegExp(ARGS.composer, 'i');
  const join = new RegExp(ARGS.join, 'i');
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  if (document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]')) return JSON.stringify({open: true});
  const buttons = [...document.querySelectorAll('[role="button"]')].filter(shown);
  const joinSeen = buttons.some((el) => join.test((el.getAttribute('aria-label') || el.innerText || '').trim()));
  for (const el of buttons) {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!text || text.length > 80 || !label.test(text)) continue;
    el.scrollIntoView({block: 'center'});
    const r = el.getBoundingClientRect();
    return JSON.stringify({join: joinSeen, x: r.left + r.width / 2, y: r.top + r.height / 2});
  }
  return JSON.stringify({join: joinSeen});
})()
"""

# Diagnostic d'un groupe en échec : libellés des boutons / zones de texte visibles
VISIBLE_LABELS_JS = r"""
(() => {
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const labels = [...document.querySelectorAll('[role="button"], [role="textbox"], [contenteditable="true"]')]
    .filter(shown)
    .map((el) => (el.getAttribute('aria-label') || el.innerText || el.getAttribute('aria-placeholder') || '')
      .trim().replace(/\s+/g, ' ').slice(0, 60))
    .filter(Boolean);
  return JSON.stringify({url: location.href, title: document.title, labels: [...new Set(labels)].slice(0, 80)});
})()
"""

# Zone de texte de la fenêtre de rédaction : point à cliquer, texte actuel ; paste / sélection de tout le texte
TEXTBOX_JS = r"""
(() => {
  const ARGS = __ARGS__;
  const box = document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]');
  if (!box) return JSON.stringify({ok: false});
  if (ARGS.paste) {
    box.focus();
    const data = new DataTransfer();
    data.setData('text/plain', ARGS.paste);
    box.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
  }
  if (ARGS.select) {
    box.focus();
    const range = document.createRange();
    range.selectNodeContents(box);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }
  const r = box.getBoundingClientRect();
  return JSON.stringify({ok: true, x: r.left + Math.min(40, r.width / 2), y: r.top + Math.min(20, r.height / 2), text: box.innerText || ''});
})()
"""

# Bouton « Publier » de la fenêtre de rédaction ; open=false = fenêtre fermée
PUBLISH_JS = r"""
(() => {
  const ARGS = __ARGS__;
  const label = new RegExp(ARGS.publish, 'i');
  const box = document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]');
  if (!box) return JSON.stringify({open: false});
  const dialog = box.closest('div[role="dialog"]');
  for (const el of dialog.querySelectorAll('[role="button"]')) {
    const text = (el.getAttribute('aria-label') || el.innerText || '').trim();
    if (!label.test(text)) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    return JSON.stringify({open: true, x: r.left + r.width / 2, y: r.top + r.height / 2,
                           disabled: el.getAttribute('aria-disabled') === 'true'});
  }
  return JSON.stringify({open: true});
})()
"""

# Textes des boîtes de dialogue / alertes hors fenêtre de rédaction (erreurs, « en attente d'approbation »)
NOTICES_JS = r"""
(() => {
  const notices = [];
  for (const el of document.querySelectorAll('[role="dialog"], [role="alertdialog"], [role="alert"], [role="status"]')) {
    if (el.querySelector('div[contenteditable="true"][role="textbox"]')) continue;
    const text = (el.innerText || '').trim();
    if (text) notices.push(text.slice(0, 300));
  }
  return JSON.stringify({open: !!document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]'), notices});
})()
"""


# Bouton « Photo/vidéo » de la fenêtre de rédaction : fait apparaître le champ fichier s'il n'y est pas déjà
PHOTO_BUTTON_JS = r"""
(() => {
  const ARGS = __ARGS__;
  const label = new RegExp(ARGS.photo, 'i');
  const box = document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]');
  if (!box) return JSON.stringify({});
  for (const el of box.closest('div[role="dialog"]').querySelectorAll('[role="button"], [aria-label]')) {
    const text = (el.getAttribute('aria-label') || el.innerText || '').trim();
    if (!label.test(text)) continue;
    const r = el.getBoundingClientRect();
    if (r.width && r.height) return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
  }
  return JSON.stringify({});
})()
"""

# Champ fichier (images) de la fenêtre de rédaction, renvoyé comme objet pour DOM.setFileInputFiles
FILE_INPUT_EXPR = (
    "(() => { const box = document.querySelector('div[role=\"dialog\"] div[contenteditable=\"true\"][role=\"textbox\"]');"
    " if (!box) return null;"
    " const inputs = [...box.closest('div[role=\"dialog\"]').querySelectorAll('input[type=\"file\"]')];"
    " return inputs.find((el) => /image/.test(el.accept || '')) || inputs[0] || null; })()"
)

# Vignettes des photos ajoutées (aperçus locaux blob: / data:) dans la fenêtre de rédaction
PREVIEWS_JS = r"""
(() => {
  const box = document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]');
  if (!box) return JSON.stringify({count: 0});
  const dialog = box.closest('div[role="dialog"]');
  return JSON.stringify({count: dialog.querySelectorAll('img[src^="blob:"], img[src^="data:image"]').length});
})()
"""


# Nombre de messages « publication en attente d'approbation » dans la page : comparé avant / après « Publier »
PENDING_COUNT_JS = r"""
(() => {
  const ARGS = __ARGS__;
  const fold = (s) => (s || '').normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const found = fold(document.body.innerText).match(new RegExp(ARGS.pending, 'g'));
  return JSON.stringify({count: found ? found.length : 0});
})()
"""

class StopRun(Exception):
    """Blocage Facebook, page de connexion ou arrêt demandé : aucun autre groupe ce lancement-ci."""


@dataclass(slots=True)
class Job:
    target: fb.GroupTarget
    offers: list[dict[str, Any]]  # dans l'ordre des affiches
    text: str  # publication : un seul lien (wipwork.com/jobs)
    online: int  # offres partageables en tout (« N offres en ligne » des affiches)


# ---------------------------------------------------------------------------
# Souris / clavier réels (CDP) : événements isTrusted, comme un utilisateur
# ---------------------------------------------------------------------------


def _click(sb: SB, x: float, y: float) -> None:
    left = cdp_input.MouseButton.LEFT
    fb._cdp_send(sb, cdp_input.dispatch_mouse_event(type_="mouseMoved", x=x, y=y))
    sb.sleep(random.uniform(0.1, 0.3))
    fb._cdp_send(sb, cdp_input.dispatch_mouse_event(type_="mousePressed", x=x, y=y, button=left, buttons=1, click_count=1))
    sb.sleep(random.uniform(0.05, 0.15))
    fb._cdp_send(sb, cdp_input.dispatch_mouse_event(type_="mouseReleased", x=x, y=y, button=left, buttons=0, click_count=1))


def _press(sb: SB, key: str, code: str, key_code: int, text: str | None = None) -> None:
    for type_ in ("keyDown", "keyUp"):
        fb._cdp_send(sb, cdp_input.dispatch_key_event(
            type_=type_, key=key, code=code, windows_virtual_key_code=key_code, native_virtual_key_code=key_code,
            text=text if type_ == "keyDown" else None,
        ))


def _type_lines(sb: SB, text: str) -> None:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line:
            fb._cdp_send(sb, cdp_input.insert_text(text=line))
        if index < len(lines) - 1:
            _press(sb, "Enter", "Enter", 13, "\r")
        sb.sleep(random.uniform(0.05, 0.2))


# ---------------------------------------------------------------------------
# Une publication dans un groupe
# ---------------------------------------------------------------------------


def _text_matches(actual: str, expected: str) -> bool:
    """Texte de l'éditeur = texte voulu, aux emojis, espaces et ponctuation près."""
    got, want = share._fold(actual), share._fold(expected)
    return bool(want) and len(got) >= 0.9 * len(want) and want[:40] in got and want[-40:] in got


def save_diagnostics(sb: SB, group_url: str, reason: str) -> None:
    """Capture d'écran + boutons visibles d'un groupe en échec : de quoi ajuster COMPOSER_LABELS / JOIN_LABELS."""
    slug = re.sub(r"[^\w.\-]", "_", group_url.rstrip("/").rsplit("/", 1)[-1])[:80] or "groupe"
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    info = fb._run_js(sb, VISIBLE_LABELS_JS) or {}
    info["reason"] = reason
    share._write_json_atomic(DEBUG_DIR / f"{slug}.json", info)
    with suppress(Exception):
        (DEBUG_DIR / f"{slug}.png").write_bytes(base64.b64decode(fb._cdp_send(sb, cdp_page.capture_screenshot(format_="png"))))
    print(f"  [DIAG] Boutons visibles : {' | '.join(info.get('labels', [])[:25])}")
    print(f"  [DIAG] Capture et détails : {DEBUG_DIR / slug}.png / .json")


def open_composer(sb: SB, group_url: str) -> str:
    """
    "open", "not_member", "no_composer" ; StopRun sur une page de connexion. Le bouton « Exprimez-vous… » est
    cherché jusqu'au bout du délai : « not_member » seulement s'il n'est jamais apparu ET qu'un « Rejoindre » était
    visible (un « Rejoindre » seul est souvent celui d'un groupe suggéré).
    """
    sb.goto(group_url)
    sb.sleep(PAGE_LOAD_WAIT + random.uniform(0, 3))
    if fb._is_auth_wall(fb._page_state(sb)):
        raise StopRun("page de connexion Facebook : relancez avec --login")
    clicks = 0
    composer_seen = join_seen = False
    deadline = time.monotonic() + COMPOSER_TIMEOUT
    while time.monotonic() < deadline:
        found = fb._run_js(sb, COMPOSER_JS, {"composer": COMPOSER_LABELS, "join": JOIN_LABELS}) or {}
        if found.get("open"):
            return "open"
        join_seen = join_seen or bool(found.get("join"))
        if "x" in found:
            composer_seen = True
            if clicks < COMPOSER_CLICKS:
                sb.sleep(random.uniform(0.4, 0.9))  # après scrollIntoView
                _click(sb, found["x"], found["y"])
                clicks += 1
                sb.sleep(2.5)
                continue
        sb.sleep(1)
    status = "not_member" if join_seen and not composer_seen else "no_composer"
    save_diagnostics(sb, group_url, status)
    return status


def fill_composer(sb: SB, text: str) -> bool:
    box = fb._run_js(sb, TEXTBOX_JS, {}) or {}
    if not box.get("ok"):
        return False
    _click(sb, box["x"], box["y"])  # curseur dans l'éditeur : Lexical colle à la sélection courante
    sb.sleep(0.5)
    fb._run_js(sb, TEXTBOX_JS, {"paste": text})
    sb.sleep(FILL_WAIT)
    if _text_matches((fb._run_js(sb, TEXTBOX_JS, {}) or {}).get("text", ""), text):
        return True
    # Repli : l'éditeur a ignoré le paste synthétique -> effacer puis taper ligne par ligne
    clear_composer(sb)
    _type_lines(sb, text)
    sb.sleep(FILL_WAIT)
    return _text_matches((fb._run_js(sb, TEXTBOX_JS, {}) or {}).get("text", ""), text)


def clear_composer(sb: SB) -> None:
    if (fb._run_js(sb, TEXTBOX_JS, {"select": True}) or {}).get("ok"):
        _press(sb, "Backspace", "Backspace", 8)


def discard_composer(sb: SB) -> None:
    """Texte effacé avant de fermer : pas de brouillon ni de « Quitter sans publier ? » bloquant la navigation."""
    with suppress(Exception):
        clear_composer(sb)
        sb.sleep(0.5)
        _press(sb, "Escape", "Escape", 27)
        sb.sleep(1)


def _notices(sb: SB) -> tuple[bool, list[str]]:
    state = fb._run_js(sb, NOTICES_JS) or {}
    return bool(state.get("open")), list(state.get("notices") or [])


def _ask(prompt: str) -> str:
    # Sous PM2, stdin n'est pas un terminal : input() attendrait indéfiniment au lieu de lever EOFError
    if sys.stdin is None or not sys.stdin.isatty():
        raise StopRun("question posée sans terminal (PM2 ?) : lancez sans --assisted")
    try:
        return input(prompt).strip().lower()
    except EOFError:
        raise StopRun("mode assisté sans terminal : relancez dans un terminal, ou sans --assisted") from None


def _file_input(sb: SB) -> str | None:
    """objectId du champ fichier de la fenêtre de rédaction, ou None."""
    try:
        remote, exception = fb._cdp_send(sb, cdp_runtime.evaluate(expression=FILE_INPUT_EXPR, return_by_value=False))
    except Exception:
        return None
    return None if exception is not None else getattr(remote, "object_id", None)


def attach_images(sb: SB, images: list[Path]) -> int:
    """Joint les affiches à la publication ; renvoie le nombre de vignettes vues dans la fenêtre de rédaction."""
    object_id = _file_input(sb)
    if object_id is None:
        button = fb._run_js(sb, PHOTO_BUTTON_JS, {"photo": PHOTO_LABELS}) or {}
        if "x" not in button:
            return 0
        # Sélecteur de fichiers du système intercepté (jamais affiché) : le champ est rempli par CDP
        with suppress(Exception):
            fb._cdp_send(sb, cdp_page.set_intercept_file_chooser_dialog(enabled=True))
        try:
            _click(sb, button["x"], button["y"])
            sb.sleep(2)
            object_id = _file_input(sb)
        finally:
            with suppress(Exception):
                fb._cdp_send(sb, cdp_page.set_intercept_file_chooser_dialog(enabled=False))
        if object_id is None:
            return 0
    try:
        fb._cdp_send(sb, cdp_dom.set_file_input_files(files=[str(path.resolve()) for path in images], object_id=object_id))
    except Exception as exc:
        print(f"  [WARN] Affiches non jointes : {str(exc)[:150]}")
        return 0
    finally:
        with suppress(Exception):
            fb._cdp_send(sb, cdp_runtime.release_object(object_id))
    count = 0
    deadline = time.monotonic() + IMAGE_UPLOAD_TIMEOUT
    while time.monotonic() < deadline:
        sb.sleep(1)
        count = int((fb._run_js(sb, PREVIEWS_JS) or {}).get("count", 0))
        if count >= len(images):
            break
    return count


def _pending_count(sb: SB) -> int:
    return int((fb._run_js(sb, PENDING_COUNT_JS, {"pending": PENDING_POST_PATTERN}) or {}).get("count", 0))


def publish(sb: SB, group_url: str, auto: bool, with_images: bool = False) -> str:
    """"posted", "pending", "skipped", "not_published" ; StopRun sur blocage Facebook ou arrêt demandé."""
    _, baseline = _notices(sb)
    # « En attente d'approbation » s'affiche dans la publication elle-même, pas dans une alerte : compté avant / après
    pending_before = _pending_count(sb)
    if not auto:
        answer = _ask("  Relisez la publication dans Chrome. ENTRÉE = publier, s = passer ce groupe, q = arrêter : ")
        if answer == "q":
            discard_composer(sb)
            raise StopRun("arrêt demandé")
        if answer == "s":
            discard_composer(sb)
            return "skipped"

    button = fb._run_js(sb, PUBLISH_JS, {"publish": PUBLISH_LABELS}) or {}
    if not button.get("open"):
        if auto:  # fermée par Facebook avant le clic : rien n'a été publié
            print("  [WARN] Fenêtre de rédaction fermée avant le clic sur « Publier »")
            return "not_published"
        # Fenêtre déjà fermée en mode assisté : publiée (ou annulée) à la main dans Chrome
        answer = _ask("  Fenêtre de rédaction fermée : l'avez-vous publiée vous-même ? [o/N] ")
        return "posted" if answer in ("o", "oui", "y") else "skipped"
    deadline = time.monotonic() + (IMAGE_UPLOAD_TIMEOUT if with_images else PUBLISH_ENABLED_TIMEOUT)
    while button.get("disabled") and time.monotonic() < deadline:
        sb.sleep(1)
        button = fb._run_js(sb, PUBLISH_JS, {"publish": PUBLISH_LABELS}) or {}
    if "x" not in button or button.get("disabled"):
        print("  [WARN] Bouton « Publier » introuvable ou désactivé (sujet obligatoire dans ce groupe ?)")
        save_diagnostics(sb, group_url, "not_published")
        discard_composer(sb)
        return "not_published"
    _click(sb, button["x"], button["y"])

    deadline = time.monotonic() + (PUBLISH_IMAGES_TIMEOUT if with_images else PUBLISH_TIMEOUT)
    while time.monotonic() < deadline:
        sb.sleep(1)
        still_open, notices = _notices(sb)
        fresh = [share._fold(n) for n in notices if n not in baseline]
        if blocked := next((n for n in fresh if BLOCK_RE.search(n)), None):
            discard_composer(sb)
            raise StopRun(f"Facebook bloque la publication : « {blocked[:150]} »")
        if not still_open:
            sb.sleep(NOTICE_WAIT)
            _, notices = _notices(sb)
            fresh = [share._fold(n) for n in notices if n not in baseline]
            if blocked := next((n for n in fresh if BLOCK_RE.search(n)), None):
                raise StopRun(f"Facebook bloque la publication : « {blocked[:150]} »")
            if any(PENDING_RE.search(n) for n in fresh) or _pending_count(sb) > pending_before:
                return "pending"
            return "posted"
    print("  [WARN] La fenêtre de rédaction ne s'est pas fermée après le clic sur « Publier »")
    save_diagnostics(sb, group_url, "not_published")
    discard_composer(sb)
    return "not_published"


def post_to_group(sb: SB, group_url: str, text: str, auto: bool, images: list[Path] | None = None) -> str:
    status = open_composer(sb, group_url)
    if status != "open":
        return status
    sb.sleep(random.uniform(1, 2))
    if not fill_composer(sb, text):
        print("  [WARN] Texte non inséré dans la fenêtre de rédaction")
        save_diagnostics(sb, group_url, "fill_failed")
        discard_composer(sb)
        return "fill_failed"
    if images:
        seen = attach_images(sb, images)
        if seen >= len(images):
            print(f"  [INFO] {seen} affiche(s) jointe(s)")
        else:
            # Publiée quand même : le texte porte toutes les offres ; vignettes peut-être dessinées autrement
            print(f"  [WARN] {seen} vignette(s) vue(s) sur {len(images)} affiche(s) - publication quand même")
            save_diagnostics(sb, group_url, "images")
    else:
        sb.sleep(LINK_PREVIEW_WAIT)
    return publish(sb, group_url, auto, with_images=bool(images))


# ---------------------------------------------------------------------------
# Lancement
# ---------------------------------------------------------------------------


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def order_offers(group_id: str, offers: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Ordre propre au groupe et au jour (pas 20 publications identiques), commun au texte et aux affiches."""
    ordered = list(offers)
    random.Random(f"{group_id}|{today}").shuffle(ordered)
    return ordered


def _offer_line(offer: dict[str, Any]) -> str:
    owner = share.owner_name(offer)
    return f"{share._clean(offer.get('name'))}{f' — {owner}' if owner else ''}"


def _cities(offers: list[dict[str, Any]]) -> tuple[list[str], bool]:
    """Villes les plus fréquentes (dernière partie du lieu : « Analamahitsy, Antananarivo ») ; True s'il y en a d'autres."""
    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for offer in offers:
        city = share._location(offer).rsplit(",", 1)[-1].strip()
        key = share._fold(city)
        # Chiffres = adresse (« Anjanahary 49 IIS »), pas une ville
        if key and len(city) <= MAX_CITY_CHARS and not re.search(r"\d", city) and not LINK_LIKE_RE.search(city):
            counts[key] += 1
            names.setdefault(key, city)
    return [names[key] for key, _ in counts.most_common(MAX_CITIES)], len(counts) > MAX_CITIES


def post_text(offers: list[dict[str, Any]], args: argparse.Namespace, today: date, rank: int) -> str:
    """
    Par défaut (demande de l'utilisateur, 20/10/2026) : les offres numérotées comme les affiches, chacune avec son
    lien wipwork.com/jobs/<slug> (share.digest_post). --single-link : UN SEUL lien (wipwork.com/jobs) + 6 exemples
    de postes, repli si Facebook bloque les publications à liens multiples (constaté le 19/10/2026) ; les exemples
    dont le texte ressemble à une adresse web (« xxx.mg ») y sont écartés : Facebook en ferait un second lien.
    """
    header = HEADERS[(rank + today.toordinal()) % len(HEADERS)].format(
        date=f"{today:%d/%m/%Y}", country=args.country, country_upper=args.country.upper(), count=len(offers)
    )
    if not args.single_link:
        return share.digest_post(offers, args.site_url, args.country, today, header)
    examples = [line for line in map(_offer_line, offers) if not LINK_LIKE_RE.search(line)][:EXAMPLE_OFFERS]
    lines = [header, "", "Quelques postes à pourvoir :", *(f"• {line}" for line in examples)]
    cities, more = _cities(offers)
    if cities:
        lines += ["", f"📍 {', '.join(cities)}{'…' if more else ''}"]
    lines += [
        "",
        f"👉 Les {len(offers)} offres et la candidature en ligne : {args.site_url.rstrip('/')}/jobs",
        "",
        share.hashtags(args.country, offers),
    ]
    return "\n".join(lines)


def _recently_posted(group: dict[str, Any], min_hours: float, now: datetime) -> bool:
    with suppress(KeyError, TypeError, ValueError):
        return now - datetime.fromisoformat(group["last_post_at"]) < timedelta(hours=min_hours)
    return False


def plan(targets: list[fb.GroupTarget], offers: list[dict[str, Any]], state: dict[str, dict[str, Any]],
         args: argparse.Namespace, today: date) -> list[Job]:
    excluded = share.load_excluded_owners(Path(args.excluded_owners_file))
    online = len(share.select_offers(offers, {}, excluded, args.min_days_left, today, False)[0])
    now = datetime.now().astimezone()
    jobs = []
    for rank, target in enumerate(targets):
        group = state.get(target.group_id, {})
        if _recently_posted(group, args.min_hours, now):
            print(f"[INFO] {target.group_id} : déjà publié il y a moins de {args.min_hours:g} h ({group['last_post_at']}) - ignoré")
            continue
        selected, _ = share.select_offers(offers, group.get("posted", {}), excluded, args.min_days_left, today, False)
        batch = selected[: args.per_post]
        if len(batch) < args.min_offers:
            print(f"[INFO] {target.group_id} : {len(batch)} offre(s) nouvelle(s) pour ce groupe (< {args.min_offers}) - ignoré")
            continue
        batch = order_offers(target.group_id, batch, today)
        jobs.append(Job(target, batch, post_text(batch, args, today, rank), online))
    if args.max_groups > 0:
        jobs = jobs[: args.max_groups]
    return jobs


def posters_for(job: Job, args: argparse.Namespace, today: date) -> list[Path]:
    """Affiches du groupe (visuals.py) ; aucune si --no-images ou rendu impossible (publication texte seul)."""
    if args.no_images:
        return []
    try:
        return visuals.render_posters(job.offers, job.target.file_slug, args.country, today, job.online, args.site_url)
    except visuals.VisualError as exc:
        print(f"  [WARN] Affiches non générées ({exc}) : publication sans image")
        return []


def record(state: dict[str, dict[str, Any]], job: Job, status: str, site_url: str, images: int = 0) -> None:
    """Historique du groupe ; "posted"/"pending" marquent les offres publiées (jamais republiées dans ce groupe)."""
    target = job.target
    group = state.setdefault(target.group_id, {"url": target.group_url, "posted": {}, "history": []})
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    group.setdefault("history", []).append(
        {"at": at, "status": status, "offers": [o["id"] for o in job.offers], "images": images}
    )
    if status in ("posted", "pending"):
        group["last_post_at"] = at
        for offer in job.offers:
            group.setdefault("posted", {})[offer["id"]] = {
                "key": share.offer_key(offer),
                "title": share._clean(offer.get("name")),
                "url": share.offer_url(offer, site_url),
                "posted_at": at,
            }


def run(sb: SB, jobs: list[Job], state: dict[str, dict[str, Any]], state_path: Path, args: argparse.Namespace,
        today: date, totals: Counter[str]) -> None:
    failures = 0
    for index, job in enumerate(jobs, 1):
        target, batch = job.target, job.offers
        if index > 1:
            pause = random.uniform(args.delay_min, args.delay_max)
            print(f"\n[INFO] Pause de {int(pause // 60)} min {int(pause % 60):02d} s avant le groupe suivant (Ctrl+C pour arrêter)")
            sb.sleep(pause)
        print(f"\n[{index}/{len(jobs)}] Groupe {target.group_id} - {len(batch)} offre(s)")
        images = posters_for(job, args, today)
        try:
            status = post_to_group(sb, target.group_url, job.text, args.auto, images)
        except StopRun:
            record(state, job, "stopped", args.site_url, len(images))
            share._write_json_atomic(state_path, state)
            raise
        record(state, job, status, args.site_url, len(images))
        share._write_json_atomic(state_path, state)
        totals[status] += 1
        print(f"  -> {status}")
        failures = 0 if status in ("posted", "pending", "skipped", "not_member") else failures + 1
        if failures >= MAX_FAILURES_IN_A_ROW:
            raise StopRun(f"{failures} échecs d'affilée : la page Facebook a peut-être changé, vérifiez avec --assisted")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publie les offres WipWork dans des groupes Facebook (récapitulatif par groupe)")
    parser.add_argument("--group-url", action="append", default=[], help="groupe ciblé (répétable) ; défaut : share_groups.txt")
    parser.add_argument("--groups-file", default=str(GROUPS_FILE))
    parser.add_argument("--per-post", type=int, default=DEFAULT_PER_POST, help=f"offres par publication (défaut {DEFAULT_PER_POST})")
    parser.add_argument("--min-offers", type=int, default=DEFAULT_MIN_OFFERS,
                        help=f"offres nouvelles minimum pour publier dans un groupe (défaut {DEFAULT_MIN_OFFERS})")
    parser.add_argument("--max-groups", type=int, default=0, help="publications au maximum ce lancement-ci (0 = tous les groupes)")
    parser.add_argument("--min-hours", type=float, default=DEFAULT_MIN_HOURS,
                        help=f"heures minimum entre deux publications dans un même groupe (défaut {DEFAULT_MIN_HOURS})")
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN,
                        help=f"pause minimum entre deux groupes, en secondes (défaut {DEFAULT_DELAY_MIN})")
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX,
                        help=f"pause maximum entre deux groupes, en secondes (défaut {DEFAULT_DELAY_MAX})")
    parser.add_argument("--min-days-left", type=int, default=1, help="jours de validité restants minimum d'une offre (défaut 1)")
    parser.add_argument("--assisted", action="store_true",
                        help="fenêtre visible, ENTRÉE dans le terminal avant chaque publication (défaut : automatique)")
    parser.add_argument("--visible", action="store_true", help="fenêtre Chrome visible, publication automatique (défaut : sans fenêtre)")
    parser.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)  # ancien nom, comportement par défaut
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)  # idem
    parser.add_argument("--preview", action="store_true",
                        help="texte et affiches prévus pour chaque groupe, sans navigateur Facebook ni publication")
    parser.add_argument("--no-images", action="store_true", help="publication texte seul, sans les affiches (visuals.py)")
    parser.add_argument("--single-link", action="store_true",
                        help="un seul lien (wipwork.com/jobs) au lieu d'un lien par offre, si Facebook bloque les publications")
    parser.add_argument("--login", action="store_true", help="forcer l'étape de connexion manuelle (fenêtre visible)")
    parser.add_argument("--profile", default=str(PROFILE_DIR), help=f"profil Chrome (défaut {PROFILE_DIR.name})")
    parser.add_argument("--country", default=share.DEFAULT_COUNTRY)
    parser.add_argument("--excluded-owners-file", default=str(share.EXCLUDED_OWNERS_FILE))
    parser.add_argument("--api-url", default=os.getenv("WIPWORK_OFFERS_API_URL", share.DEFAULT_OFFERS_API))
    parser.add_argument("--site-url", default=os.getenv("WIPWORK_SITE_URL", share.DEFAULT_SITE_URL))
    args = parser.parse_args()
    if args.assisted and (args.auto or args.headless):
        parser.error("--assisted se relit dans la fenêtre Chrome : incompatible avec --auto / --headless")
    args.auto = not args.assisted
    # Fenêtre visible pour relire (--assisted), sur demande (--visible) ou pour se connecter (--login)
    args.headless = not (args.assisted or args.visible or args.login)
    if args.delay_max < args.delay_min:
        parser.error("--delay-max doit être >= --delay-min")
    return args


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")  # emojis des publications sur une console cp1252

    args = parse_args()
    targets = fb.load_targets(args.group_url, Path(args.groups_file))
    if not targets:
        print(f"[ERREUR] Aucun groupe : passez --group-url ou ajoutez des liens dans {args.groups_file}")
        return
    today = date.today()
    state_path = share.OUTPUT_DIR / f"group_posts_{share._fold(args.country).replace(' ', '_')}.json"
    state = load_state(state_path)
    try:
        offers, announced = share.fetch_offers(args.api_url, args.country)
    except share.ShareError as exc:
        print(f"[ERREUR] API des offres ({args.api_url}) : {exc}")
        sys.exit(1)
    print(f"[INFO] {len(offers)} offres uniques ({announced} annoncées par l'API), {len(targets)} groupe(s)")

    jobs = plan(targets, offers, state, args, today)
    if not jobs:
        print("[INFO] Rien à publier.")
        return
    if args.preview:
        for job in jobs:
            print(f"\n{'-' * 60}\n{job.target.group_url} ({len(job.offers)} offres)\n{'-' * 60}\n{job.text}")
            for path in posters_for(job, args, today):
                print(f"[AFFICHE] {path}")
        print(f"\n[INFO] Aperçu : {len(jobs)} publication(s) prévue(s), rien n'est publié.")
        return

    profile_dir = Path(args.profile)
    fb.PROFILE_DIR = profile_dir  # nom du profil affiché par fb.login_and_wait
    profile_dir.mkdir(parents=True, exist_ok=True)
    fb.close_orphan_chromes(profile_dir)
    fb._reset_profile_exit_state(profile_dir)
    mode = ("automatique" if args.auto else "assisté") + (", sans fenêtre" if args.headless else ", fenêtre visible")
    print(f"[INFO] {len(jobs)} publication(s) prévue(s) - mode {mode}, profil {profile_dir.name}")

    totals: Counter[str] = Counter()
    with SB(uc=True, locale="fr", user_data_dir=str(profile_dir), headless=args.headless) as sb:
        fb._activate_cdp_mode(sb)
        try:
            logged_in = fb.is_logged_in(sb)
            if (args.headless or not sys.stdin.isatty()) and (args.login or not logged_in):
                print("[ERREUR] Profil Facebook non connecté : reconnectez-le dans un terminal avec "
                      "python facebook_script/post_groups.py --login")
                return
            if (args.login or not logged_in) and not fb.login_and_wait(sb):
                print("Abandon.")
                return
            run(sb, jobs, state, state_path, args, today, totals)
        except StopRun as exc:
            print(f"\n[STOP] {exc} - aucun autre groupe ce lancement-ci.")
        except KeyboardInterrupt:
            print("\n[INFO] Interruption (Ctrl+C) - les publications déjà faites sont enregistrées.")
    summary = ", ".join(f"{count} {status}" for status, count in totals.most_common()) or "aucune"
    print(f"\nTerminé - {summary}. Suivi : {state_path}")


if __name__ == "__main__":
    main()

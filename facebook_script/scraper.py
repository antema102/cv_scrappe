"""
facebook_script/scraper.py
==========================
Scrape les publications de groupes Facebook (fil <div role="feed"> du groupe).

Etape 1 - Connexion :
Une fenêtre Chrome s'ouvre sur facebook.com avec le profil persistant
"my_custom_profile_facebook" (racine du projet, ignoré par git). Si ce profil
n'est pas encore connecté, connectez-vous manuellement (identifiants, code de
vérification...), puis appuyez sur ENTRÉE dans le terminal. La session reste
enregistrée dans le profil : les lancements suivants la réutilisent sans rien
demander, tant que Facebook ne l'invalide pas.

Détection de session : cookie "c_user" présent (posé par Facebook uniquement
pour un compte connecté) ET aucune page de connexion/vérification affichée.

Etape 2 - Scraping :
Pour chaque groupe (facebook_script/groups.txt ou --url), le script ouvre le
fil du groupe (tri "nouvelles publications" par défaut) et scrolle à l'infini.
A chaque passage, les publications pas encore traitées sont :
  1. marquées (attribut data-scrape-key) pour ne jamais être relues,
  2. dépliées (clic sur "Voir plus"),
  3. survolées : Facebook ne met le vrai lien de la publication dans le lien
     horodatage ("?__cft__...#?bek" au départ) qu'au survol. Survol synthétique
     d'abord ; si aucun permalien n'apparaît, survol souris réel via CDP
     (désactivé automatiquement s'il échoue plusieurs fois d'affilée),
  4. lues via CDP DOM.getOuterHTML(includeShadowDOM) : Facebook cache
     l'horodatage ("1 h") dans un shadow root FERMÉ, invisible pour le
     JavaScript de la page (innerText/outerHTML) mais sérialisé par CDP en
     <template shadowrootmode="closed">,
  5. parsées avec BeautifulSoup : auteur, texte, emails, téléphones, liens,
     images (le texte alternatif auto-généré des images contient souvent le
     texte des affiches d'offres),
  6. leurs images téléchargées tout de suite (URL du CDN signées, expirées au
     bout de quelques jours) ; les images manquantes des publications déjà en
     cache sont rattrapées au début de chaque groupe.

Etape 3 - Fin de chaque groupe (finalize_posts) :
  - texte des images lu par OCR (RapidOCR, local : pip install rapidocr
    onnxruntime) ; emails/téléphones des affiches ajoutés à la publication,
  - détection des offres d'emploi (mots-clés français/anglais/malgache) avec
    intitulé, description (texte + images), missions/tâches et profil,
  - export des seules offres dans downloaded_files/facebook_jobs_{group_id}.json.
Ne traite que ce qui manque : un Ctrl+C est rattrapé au lancement suivant.

Arrêt d'un groupe : fin du fil (rien de nouveau après --max-idle scrolls),
--max-posts atteint, ou --stop-after-known publications déjà connues d'affilée.

Usage :
    python facebook_script/scraper.py                          # groupes de groups.txt
    python facebook_script/scraper.py --url https://www.facebook.com/groups/506924959474464/
    python facebook_script/scraper.py --max-posts 10            # test rapide
    python facebook_script/scraper.py --stop-after-known 20     # relance incrémentale
    python facebook_script/scraper.py --dump-html --max-posts 5  # HTML brut pour ajuster les sélecteurs
    python facebook_script/scraper.py --login                   # forcer l'étape de connexion
    python facebook_script/scraper.py --headless                # sans fenêtre (profil déjà connecté)

Sortie : downloaded_files/facebook_posts_{group_id}.json (clé = post_id) et
downloaded_files/facebook_images/{group_id}/{post_id}_{n}.jpg (chemin noté dans
images[i]["file"] ; --no-images pour ne garder que les URL) et
downloaded_files/facebook_jobs_{group_id}.json (offres d'emploi uniquement ; --no-ocr
pour ne pas lire les images).
Pas d'envoi au backend : aucune route /api ne reçoit de publications Facebook.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import re
import sys
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from bs4.element import Script, Stylesheet
from fb_dates import is_too_old
from mycdp import dom as cdp_dom  # mycdp est installé avec seleniumbase (pilote du mode CDP)
from mycdp import input_ as cdp_input
from mycdp import runtime as cdp_runtime
from seleniumbase import SB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

PROFILE_DIR = PROJECT_ROOT / "my_custom_profile_facebook"
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
GROUPS_FILE = SCRIPT_DIR / "groups.txt"

FACEBOOK_URL = "https://www.facebook.com/"

DEFAULT_SCROLL_DELAY = 2.5
DEFAULT_MAX_IDLE = 8
BATCH_SIZE = 25  # publications traitées au maximum entre deux scrolls
EXPAND_WAIT = 1.0  # laisse Facebook déplier "Voir plus" / réagir au survol synthétique
HOVER_WAIT = 0.8  # après un survol souris réel
HOVER_MAX_MISSES = 5  # permaliens introuvables d'affilée avant de couper le survol réel
FEED_TIMEOUT = 30

# Valeurs du paramètre d'URL ?sorting_setting= des groupes (ignoré par Facebook s'il n'est pas reconnu)
SORT_PARAMS = {
    "chrono": "CHRONOLOGICAL",
    "activity": "RECENT_ACTIVITY",
    "default": "",
}

SEE_MORE_LABELS = ("voir plus", "see more", "afficher plus", "afficher la suite", "en voir plus")
# Bouton affiché une fois le texte déplié : jamais cliqué (replierait le texte), seulement retiré du texte
SEE_LESS_LABELS = ("voir moins", "see less", "afficher moins")

IMAGES_DIRNAME = "facebook_images"  # sous OUTPUT_DIR
IMAGE_TIMEOUT = 20
IMAGE_EXTENSIONS = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Referer": "https://www.facebook.com/",
}

GROUP_URL_RE = re.compile(r"facebook\.com/groups/([^/?#\s]+)", re.I)
POST_ID_RES = (
    re.compile(r"/groups/[^/?#]+/(?:posts|permalink)/(\d+|pfbid\w+)"),
    re.compile(r"[?&](?:story_fbid|multi_permalinks)=(\d+|pfbid\w+)"),
    re.compile(r"facebook\.com/[^/?#]+/posts/(\d+|pfbid\w+)"),  # publication d'une page
    re.compile(r"[?&]set=(?:pcb|gm)\.(\d+)"),  # photos d'une publication de groupe/page
)
# Liens vers la visionneuse photo (toutes les images d'une publication, en pleine résolution)
PHOTO_LINK_RE = re.compile(r"facebook\.com/(?:photo/?\?|photo\.php\?|[^/?#]+/photos/)", re.I)
MORE_IMAGES_RE = re.compile(r"^\+\s?(\d{1,3})$")  # "+10" sur la dernière vignette
USER_ID_RES = (
    re.compile(r"/groups/[^/?#]+/user/(\d+)"),
    re.compile(r"profile\.php\?(?:[^#]*&)?id=(\d+)"),
    re.compile(r"/people/[^/?#]+/(\d+)"),
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|webp|svg)$", re.I)
PHONE_RE = re.compile(
    r"(?<![\d+])(?:"
    r"(?:\+|00)[1-9]\d{0,2}(?:[\s.\-]?\d){8,10}"  # international : +261 34 12 345 67
    r"|0\d(?:[\s.\-]?\d){8}"  # local 10 chiffres : 034 12 345 67
    r")(?!\d)"
)
INVISIBLE_CHARS_RE = re.compile(r"[​-‏⁠﻿]")

# Par défaut BeautifulSoup range le texte des <template> à part et get_text() l'ignore :
# or c'est là que CDP sérialise les shadow roots fermés (horodatage "1 h").
SOUP_STRING_CONTAINERS = {"script": Script, "style": Stylesheet}

# Premiers segments d'URL facebook.com qui ne sont pas des noms de profil
RESERVED_PATHS = {
    "events", "gaming", "groups", "hashtag", "l.php", "marketplace", "pages", "people",
    "permalink.php", "photo", "photo.php", "profile.php", "reel", "share", "sharer",
    "stories", "story.php", "watch",
}


# ---------------------------------------------------------------------------
# JavaScript exécuté dans la page (IIFE renvoyant du JSON ; __ARGS__ = paramètres)
# ---------------------------------------------------------------------------

# Repère les publications du fil pas encore traitées, les marque, clique sur
# "Voir plus" et simule un survol du lien horodatage. Ignore les squelettes de
# chargement et les publications virtualisées (contenu retiré par Facebook).
COLLECT_POSTS_JS = r"""
(() => {
  const args = __ARGS__;
  let feed = document.querySelector('div[role="feed"]');
  // Pages Facebook : les publications ne sont pas toujours dans un div[role="feed"]
  if (args.anyRoot && (!feed || !feed.querySelector('[aria-posinset]'))) {
    feed = document.querySelector('[role="main"]') || document.body;
  }
  if (!feed) return JSON.stringify({feed: false, posts: []});

  let items = Array.from(feed.querySelectorAll('[aria-posinset]'))
    .filter((el) => !el.parentElement.closest('[aria-posinset]'));
  if (!items.length) items = Array.from(feed.children);

  const seeMore = new Set(args.seeMore);
  const inComment = (post, el) => {
    const article = el.closest('[role="article"]');
    return !!article && article !== post && post.contains(article);
  };
  const isTimeLink = (a) => {
    const href = a.getAttribute('href') || '';
    return href.startsWith('?') || href.startsWith('#') || href.includes('#?')
      || !!a.querySelector('[aria-labelledby]');
  };

  window.__scrapeSeq = window.__scrapeSeq || 0;
  const posts = [];
  for (const post of items) {
    if (posts.length >= args.max) break;
    if (post.hasAttribute('data-scrape-key')) continue;
    if (post.querySelector('[data-virtualized="true"]')) continue;
    if (!post.querySelector('a[href]') || !(post.innerText || '').trim()) continue;

    const key = 'p' + (++window.__scrapeSeq);
    post.setAttribute('data-scrape-key', key);

    for (const button of post.querySelectorAll('[role="button"]')) {
      const label = (button.innerText || '').trim().toLowerCase().replace(/^[.…\s]+/, '');
      if (seeMore.has(label) && !inComment(post, button)) button.click();
    }

    for (const link of post.querySelectorAll('a[href]')) {
      if (!isTimeLink(link) || inComment(post, link)) continue;
      for (const type of ['pointerover', 'mouseover', 'mousemove', 'focusin']) {
        const Ctor = type === 'pointerover' ? PointerEvent : type === 'focusin' ? FocusEvent : MouseEvent;
        link.dispatchEvent(new Ctor(type, {bubbles: true, cancelable: true, view: window}));
      }
    }
    posts.push({key});
  }
  return JSON.stringify({feed: true, posts});
})()
"""

# Centre du lien horodatage d'une publication (ramené au milieu de l'écran),
# pour un survol souris réel via CDP Input.dispatchMouseEvent.
TIME_LINK_POINT_JS = r"""
(() => {
  const args = __ARGS__;
  const post = document.querySelector('[data-scrape-key="' + args.key + '"]');
  if (!post) return JSON.stringify(null);
  const link = Array.from(post.querySelectorAll('a[href]')).find((a) => {
    const href = a.getAttribute('href') || '';
    return !!a.querySelector('[aria-labelledby]') || href.startsWith('?') || href.startsWith('#') || href.includes('#?');
  });
  if (!link) return JSON.stringify(null);
  link.scrollIntoView({block: 'center', behavior: 'instant'});
  const rect = link.getBoundingClientRect();
  if (!rect.width || !rect.height) return JSON.stringify(null);
  return JSON.stringify({x: rect.left + rect.width / 2, y: rect.top + rect.height / 2});
})()
"""

# Texte de l'infobulle affichée au survol réel du lien horodatage ("samedi 12 septembre 2026 à 14:32")
DATE_TOOLTIP_JS = r"""
(() => {
  const texts = Array.from(document.querySelectorAll('[role="tooltip"]'))
    .map((el) => (el.innerText || '').trim())
    .filter(Boolean);
  return JSON.stringify(texts.length ? texts[texts.length - 1] : '');
})()
"""

PAGE_STATE_JS = r"""
(() => JSON.stringify({
  url: location.href,
  title: document.title || '',
  feed: !!document.querySelector('div[role="feed"]'),
  posts: document.querySelectorAll('[aria-posinset]').length,
  password: !!document.querySelector('input[name="pass"], input[type="password"]'),
  height: document.documentElement.scrollHeight,
}))()
"""

SCROLL_TO_BOTTOM_JS = r"""
(() => {
  const height = document.documentElement.scrollHeight;
  window.scrollTo(0, height);
  return JSON.stringify({height});
})()
"""

# Remonte un peu : relance le chargement quand le fil semble bloqué
NUDGE_UP_JS = r"""
(() => {
  window.scrollBy(0, -Math.round(window.innerHeight * 0.7));
  return JSON.stringify(true);
})()
"""


# ---------------------------------------------------------------------------
# Cache JSON local
# ---------------------------------------------------------------------------


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


class PostStore:
    """
    Cache JSON d'un groupe (clé = post_id). Contrairement au JsonStore de
    senegal_script (réécriture complète du fichier à chaque offre), la
    sauvegarde se fait par lot : un groupe peut compter des milliers de
    publications. Ecriture atomique (fichier temporaire + os.replace) pour ne
    pas corrompre le cache sur un Ctrl+C.
    """

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()
        self._dirty = False

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.file_path.exists():
            return {}
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Jamais d'écrasement silencieux d'un cache illisible : on le met de côté
            backup = self.file_path.with_name(f"{self.file_path.stem}.corrupt-{int(time.time())}.json")
            with suppress(OSError):
                self.file_path.rename(backup)
                print(f"[WARN] Cache illisible déplacé vers {backup.name}")
            return {}
        return {str(k): dict(v) for k, v in payload.items() if isinstance(v, dict)}

    def has(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def posts(self) -> list[dict[str, Any]]:
        return list(self._data.values())

    def add(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        _write_json_atomic(self.file_path, self._data)
        self._dirty = False

    def count(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# Groupes à scraper
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GroupTarget:
    group_id: str  # id numérique ou nom personnalisé du groupe (/groups/<id>/)
    group_url: str  # https://www.facebook.com/groups/<id>/

    def feed_url(self, sort: str) -> str:
        param = SORT_PARAMS.get(sort, "")
        return f"{self.group_url}?sorting_setting={param}" if param else self.group_url

    @property
    def file_slug(self) -> str:
        return re.sub(r"[^\w.\-]", "_", self.group_id)

    @property
    def output_file(self) -> Path:
        return OUTPUT_DIR / f"facebook_posts_{self.file_slug}.json"

    # Interface commune avec pages.PageTarget, utilisée par parse_post
    def post_url(self, post_id: str, href: str) -> str:
        return f"{self.group_url}posts/{post_id}/"

    def source_fields(self, name: str) -> dict[str, str]:
        return {"group_id": self.group_id, "group_name": name, "group_url": self.group_url}


def parse_group_url(url: str) -> GroupTarget | None:
    match = GROUP_URL_RE.search(url.strip())
    if not match:
        return None
    group_id = unquote(match.group(1))
    return GroupTarget(group_id=group_id, group_url=f"https://www.facebook.com/groups/{group_id}/")


def load_targets(urls: list[str], groups_file: Path) -> list[GroupTarget]:
    lines = list(urls)
    if not lines and groups_file.exists():
        lines = groups_file.read_text(encoding="utf-8-sig").splitlines()  # -sig : BOM du Bloc-notes Windows

    targets: list[GroupTarget] = []
    seen: set[str] = set()
    for line in (raw.strip() for raw in lines):
        if not line or line.startswith("#"):
            continue
        target = parse_group_url(line)
        if target is None:
            print(f"[WARN] Lien ignoré (pas un groupe Facebook) : {line}")
            continue
        if target.group_id not in seen:
            seen.add(target.group_id)
            targets.append(target)
    return targets


# ---------------------------------------------------------------------------
# Helpers texte
# ---------------------------------------------------------------------------


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", INVISIBLE_CHARS_RE.sub("", value)).strip()


def _clean_lines(text: str) -> str:
    lines = (_normalize_text(part) for part in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _contact_text(text: str) -> str:
    """Chiffres/lettres stylisés des publications (𝟎𝟑𝟒, 0️⃣3️⃣4️⃣) ramenés en ASCII avant les regex."""
    return unicodedata.normalize("NFKC", text).replace("️", "").replace("⃣", "")


def extract_emails(text: str) -> list[str]:
    emails: list[str] = []
    for raw in EMAIL_RE.findall(_contact_text(text)):
        email = raw.lower().rstrip(".")
        if not IMAGE_EXT_RE.search(email) and email not in emails:
            emails.append(email)
    return emails


def extract_phones(text: str) -> list[str]:
    phones: list[str] = []
    for raw in PHONE_RE.findall(_contact_text(text)):
        phone = re.sub(r"[\s.\-]", "", raw)
        if phone not in phones:
            phones.append(phone)
    return phones


def _clean_group_title(title: str) -> str:
    title = re.sub(r"^\(\d+\+?\)\s*", "", title)  # "(3) " = notifications non lues
    title = re.sub(r"\s*\|\s*Facebook\s*$", "", title)
    return _normalize_text(title)


# ---------------------------------------------------------------------------
# Parsing d'une publication (HTML renvoyé par CDP, shadow roots compris)
# ---------------------------------------------------------------------------


def _post_root(soup: BeautifulSoup) -> Tag | None:
    root = soup.find(True)
    if not isinstance(root, Tag):
        return None
    if root.has_attr("aria-posinset") or root.get("role") == "article":
        return root
    # Repli (fil sans aria-posinset) : on a reçu l'enveloppe, la publication est dedans
    return root.select_one("[aria-posinset], [role='article']") or root


def _find_message_container(root: Tag) -> Tag | None:
    return (
        root.select_one("[data-ad-rendering-role='story_message']")
        or root.select_one("[data-ad-preview='message']")
        or root.select_one("[data-ad-comet-preview='message']")
    )


def _message_text(container: Tag | None) -> str:
    """
    Texte de la publication, emojis compris (<img alt="📢">). Chaque <div dir="auto">
    feuille est une ligne ; les lignes d'un même bloc parent forment un paragraphe.
    """
    if container is None:
        return ""
    node = copy.copy(container)  # le HTML d'origine sert encore aux liens/identifiants
    for hidden in node.select("[aria-hidden='true']"):
        hidden.decompose()
    for button in node.select("[role='button']"):
        if _normalize_text(button.get_text(" ")).lower().lstrip(".… ") in SEE_MORE_LABELS + SEE_LESS_LABELS:
            button.decompose()
    for img in node.find_all("img"):
        img.replace_with(img.get("alt", ""))
    for br in node.find_all("br"):
        br.replace_with("\n")

    lines = [
        div for div in node.find_all("div", attrs={"dir": "auto"})
        if div.find("div", attrs={"dir": "auto"}) is None
    ]
    if not lines:
        for block in node.find_all(["div", "p", "li"]):
            block.insert_before("\n")
            block.insert_after("\n")
        return _clean_lines(node.get_text(""))

    paragraphs: list[list[str]] = []
    previous_parent = None
    for line in lines:
        text = _clean_lines(line.get_text(""))
        if not text:
            continue
        if line.parent is not previous_parent or not paragraphs:
            paragraphs.append([])
        previous_parent = line.parent
        paragraphs[-1].append(text)
    return "\n\n".join("\n".join(paragraph) for paragraph in paragraphs)


def _parse_author(root: Tag) -> tuple[str, str]:
    """(nom, href) de l'auteur ; href vide pour un membre anonyme."""
    container = root.select_one("[data-ad-rendering-role='profile_name']")
    if container is not None:
        anchors = container.select("a[href]")
    else:
        anchors = root.select("h2 a[href], h3 a[href], h4 a[href], strong a[href]")
    for anchor in anchors:
        name = _normalize_text(anchor.get_text(" "))
        if name:
            return name, anchor.get("href", "")
    if container is not None:
        return _normalize_text(container.get_text(" ")), ""
    return "", ""


def _author_identity(href: str) -> tuple[str, str]:
    """(author_id, author_url) canoniques, sans les paramètres de tracking __cft__/__tn__."""
    if not href:
        return "", ""
    for pattern in USER_ID_RES:
        match = pattern.search(href)
        if match:
            return match.group(1), f"https://www.facebook.com/profile.php?id={match.group(1)}"
    parsed = urlparse(urljoin(FACEBOOK_URL, href))
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc.endswith("facebook.com") and len(segments) == 1 and segments[0].lower() not in RESERVED_PATHS:
        return segments[0], f"https://www.facebook.com/{segments[0]}"
    return "", ""


def _parse_avatar(root: Tag) -> str:
    image = root.select_one("svg image")
    if image is None:
        return ""
    return image.get("xlink:href") or image.get("href") or ""


def _find_time_anchor(root: Tag) -> Tag | None:
    """
    Lien horodatage : contient le shadow root fermé de l'heure (<template>) ou un
    span aria-labelledby ; avant survol son href est factice ("?__cft__...#?bek").
    """
    profile = root.select_one("[data-ad-rendering-role='profile_name']")
    anchors = [
        anchor for anchor in root.select("a[href]")
        if not any(parent is profile for parent in anchor.parents)
        and "/user/" not in anchor.get("href", "")
        and "profile.php" not in anchor.get("href", "")
    ]
    for anchor in anchors:
        if anchor.find("template") is not None or anchor.select_one("[aria-labelledby]") is not None:
            return anchor
    for anchor in anchors:
        href = anchor.get("href", "")
        if href.startswith(("?", "#")) or "#?" in href:
            return anchor
    return None


def _parse_time_text(anchor: Tag | None) -> str:
    if anchor is None:
        return ""
    text = _normalize_text(anchor.get_text(" "))
    return text if len(text) <= 60 else ""


def _parse_post_id(root: Tag, time_anchor: Tag | None, message: Tag | None) -> tuple[str, str]:
    """
    (id réel, lien où il a été trouvé) : lien horodatage d'abord (permalien après
    survol), puis les autres liens de la publication. Les liens écrits DANS le
    texte sont exclus : ils peuvent pointer vers une autre publication.
    """
    message_links = {id(anchor) for anchor in message.select("a[href]")} if message is not None else set()
    hrefs = [time_anchor.get("href", "")] if time_anchor is not None else []
    hrefs += [anchor.get("href", "") for anchor in root.select("a[href]") if id(anchor) not in message_links]
    for pattern in POST_ID_RES:
        for href in hrefs:
            match = pattern.search(href)
            if match:
                return match.group(1), href
    return "", ""


def _parse_photo_links(root: Tag) -> tuple[list[str], int]:
    """(liens visionneuse des vignettes, nombre d'images masquées derrière "+N")."""
    links: list[str] = []
    more = 0
    for anchor in root.select("a[href]"):
        href = urljoin(FACEBOOK_URL, anchor.get("href", ""))
        if not PHOTO_LINK_RE.search(href):
            continue
        if href not in links:
            links.append(href)
        for node in anchor.find_all(string=True):
            match = MORE_IMAGES_RE.match(_normalize_text(node))
            if match:
                more = max(more, int(match.group(1)))
    return links, more


def _fallback_post_id(author_key: str, text: str, images: list[dict[str, str]]) -> str:
    """
    Sans permalien : id stable dérivé du contenu. Début du texte uniquement (le même
    que "Voir plus" ait été déplié ou non) et chemin de la 1re image sans ses
    paramètres signés (oh=/oe= changent à chaque session).
    """
    text_start = " ".join(text.split())[:100]
    image_path = urlparse(images[0]["url"]).path if images else ""
    digest = hashlib.sha1(f"{author_key}|{text_start}|{image_path}".encode("utf-8")).hexdigest()
    return f"hash-{digest[:20]}"


def _parse_images(root: Tag) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for img in root.select("img[src]"):
        src = img.get("src", "")
        if "scontent" not in urlparse(src).netloc or src in seen:
            continue  # emojis/icônes (static.xx.fbcdn.net) exclus
        with suppress(ValueError):
            if 0 < int(img.get("width", "0")) <= 60:
                continue  # miniatures de profil
        seen.add(src)
        images.append({"url": src, "alt": _normalize_text(img.get("alt"))})
    return images


def _unwrap_facebook_link(href: str) -> str:
    """https://l.facebook.com/l.php?u=<cible>&h=... -> <cible>, sans fbclid."""
    parsed = urlparse(href)
    if parsed.netloc.lower().endswith("facebook.com") and parsed.path == "/l.php":
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target:
            parsed = urlparse(target)
    if "fbclid" in parsed.query:
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "fbclid"]
        parsed = parsed._replace(query=urlencode(query))
    return urlunparse(parsed)


def _parse_links(root: Tag) -> list[str]:
    """Liens externes de la publication (sites, formulaires, wa.me...), hors facebook.com."""
    links: list[str] = []
    for anchor in root.select("a[href]"):
        url = _unwrap_facebook_link(anchor.get("href", ""))
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if parsed.scheme not in ("http", "https"):
            continue
        if host in ("facebook.com", "fb.com", "fb.me") or host.endswith((".facebook.com", ".fb.com", ".fbcdn.net")):
            continue
        if url not in links:
            links.append(url)
    return links


def parse_post(html: str, target: Any, group_name: str) -> dict[str, Any] | None:
    """target : GroupTarget ou pages.PageTarget (post_url() et source_fields())."""
    soup = BeautifulSoup(html, "html.parser", string_containers=SOUP_STRING_CONTAINERS)
    root = _post_root(soup)
    if root is None:
        return None
    position = root.get("aria-posinset", "")

    # Commentaires affichés sous la publication : ni leur texte ni leurs liens n'en font partie
    for comment in root.select("[role='article']"):
        comment.decompose()

    author_name, author_href = _parse_author(root)
    author_id, author_url = _author_identity(author_href)
    message = _find_message_container(root)
    text = _message_text(message)
    images = _parse_images(root)
    if not (author_name or text or images):
        return None  # squelette de chargement, bloc de suggestions...

    time_anchor = _find_time_anchor(root)
    post_id, post_href = _parse_post_id(root, time_anchor, message)
    post_url = target.post_url(post_id, post_href) if post_id else ""
    if not post_id:
        post_id = _fallback_post_id(author_id or author_name, text, images)

    mailto = [anchor.get("href", "")[len("mailto:"):] for anchor in root.select("a[href^='mailto:']")]
    searchable = "\n".join([text, *(image["alt"] for image in images), *mailto])
    photo_links, more_images = _parse_photo_links(root)

    return {
        "post_id": post_id,
        "post_url": post_url,
        **target.source_fields(group_name),
        "author_name": author_name,
        "author_id": author_id,
        "author_url": author_url,
        "author_avatar": _parse_avatar(root),
        "time_text": _parse_time_text(time_anchor),
        "text": text,
        "emails": extract_emails(searchable),
        "phones": extract_phones(searchable),
        "links": _parse_links(root),
        "images": images,
        "photo_links": photo_links,
        "more_images": more_images,
        "feed_position": position,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Images : téléchargées dès le scraping, les URL du CDN Facebook sont signées et
# expirent en quelques jours (paramètre oe=, timestamp hexadécimal)
# ---------------------------------------------------------------------------

_http = requests.Session()
_http.headers.update(HTTP_HEADERS)


class ImageDownloadError(Exception):
    def __init__(self, message: str, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent  # URL expirée/refusée : inutile de réessayer au prochain lancement


def _download_image(url: str, dest_stem: Path) -> Path:
    for extension in set(IMAGE_EXTENSIONS.values()):
        if dest_stem.with_suffix(extension).exists():
            return dest_stem.with_suffix(extension)  # déjà téléchargée (cache JSON supprimé entre-temps)
    try:
        response = _http.get(url, timeout=IMAGE_TIMEOUT)
    except requests.RequestException as exc:
        raise ImageDownloadError(type(exc).__name__, permanent=False) from exc
    if response.status_code != 200:
        raise ImageDownloadError(f"HTTP {response.status_code}", permanent=response.status_code in (400, 401, 403, 404, 410))
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or not response.content:
        raise ImageDownloadError(f"contenu inattendu ({content_type or 'vide'})", permanent=True)

    path = dest_stem.with_suffix(IMAGE_EXTENSIONS.get(content_type) or Path(urlparse(url).path).suffix or ".jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".part")
    tmp_path.write_bytes(response.content)
    os.replace(tmp_path, path)
    return path


def download_post_images(post: dict[str, Any], target: GroupTarget) -> tuple[int, int]:
    """
    Enregistre les images d'une publication dans downloaded_files/facebook_images/<groupe>/
    (<post_id>_<n>.<ext>) et note le chemin, relatif à downloaded_files, dans
    images[i]["file"]. Renvoie (images enregistrées, échecs).
    """
    folder = OUTPUT_DIR / IMAGES_DIRNAME / target.file_slug
    saved = failed = 0
    for index, image in enumerate(post.get("images", []), 1):
        if image.get("file") or image.get("download_error"):
            continue
        try:
            path = _download_image(image["url"], folder / f"{post['post_id']}_{index}")
        except ImageDownloadError as exc:
            failed += 1
            if exc.permanent:
                image["download_error"] = str(exc)
            continue
        image["file"] = path.relative_to(OUTPUT_DIR).as_posix()
        saved += 1
    return saved, failed


def download_missing_images(store: PostStore, target: GroupTarget) -> None:
    """Rattrapage des publications déjà en cache dont les images ne sont pas encore enregistrées."""
    pending = [
        post for post in store.posts()
        if any(not (image.get("file") or image.get("download_error")) for image in post.get("images", []))
    ]
    if not pending:
        return
    print(f"Images à enregistrer pour {len(pending)} publication(s) déjà en cache...")
    saved = failed = 0
    for post in pending:
        post_saved, post_failed = download_post_images(post, target)
        saved += post_saved
        failed += post_failed
        store.add(post["post_id"], post)
    store.save()
    print(f"  -> {saved} image(s) enregistrée(s), {failed} échec(s)")


# ---------------------------------------------------------------------------
# Texte des images - OCR local et gratuit (pip install rapidocr onnxruntime).
# Les affiches d'offres portent souvent des emails/téléphones absents du texte de
# la publication, et le texte alternatif de Facebook en saute (vérifié sur une
# affiche à deux adresses dont l'alt n'en citait qu'une).
# ---------------------------------------------------------------------------

OCR_MIN_SCORE = 0.6  # en dessous : bruit sur les photos de produits ("mte" à 0.58)

# "Peut être une image de texte qui dit 'NOUS RECRUTONS ...'" -> texte cité
FB_ALT_TEXT_RE = re.compile(r"^(?:peut[\s-]être|may be)\b.*?(?:qui dit|that says)\s*", re.I)

_ocr_engine: Any = None
_ocr_missing = False


def _get_ocr_engine() -> Any:
    """RapidOCR chargé à la première utilisation (onnxruntime + modèles) ; None s'il n'est pas installé."""
    global _ocr_engine, _ocr_missing
    if _ocr_engine is None and not _ocr_missing:
        try:
            from rapidocr import RapidOCR
        except ImportError:
            _ocr_missing = True
            print("[WARN] OCR indisponible (pip install rapidocr onnxruntime) : emails/téléphones des images non lus")
            return None
        # "error" : ni journal de chargement des modèles ni "The text detection result is empty" par photo sans texte
        _ocr_engine = RapidOCR(params={"Global.log_level": "error"})
    return _ocr_engine


def ocr_post_images(engine: Any, post: dict[str, Any]) -> None:
    """Remplit images[i]["ocr_text"] (lignes lues, bruit filtré) pour les images téléchargées."""
    for image in post.get("images", []):
        if not image.get("file") or "ocr_text" in image:
            continue
        try:
            result = engine(str(OUTPUT_DIR / image["file"]))
        except Exception as exc:  # fichier supprimé/corrompu : noté vide pour ne pas réessayer en boucle
            print(f"    [OCR] {image['file']} : {str(exc)[:120]}")
            image["ocr_text"] = ""
            continue
        image["ocr_text"] = "\n".join(
            text.strip()
            for text, score in zip(result.txts or (), result.scores or ())
            if score >= OCR_MIN_SCORE and sum(char.isalnum() for char in text) >= 2
        )


def _image_text(image: dict[str, Any]) -> str:
    """Texte d'une image : OCR, sinon texte alternatif de Facebook quand il cite le texte de l'image."""
    if image.get("ocr_text"):
        return image["ocr_text"]
    alt = image.get("alt", "")
    match = FB_ALT_TEXT_RE.match(alt)
    return alt[match.end():].strip(" ’‘'\"«»") if match else ""


def _merge_unique(existing: list[str], extra: list[str]) -> list[str]:
    return existing + [item for item in dict.fromkeys(extra) if item not in existing]


# ---------------------------------------------------------------------------
# Offres d'emploi : détection par mots-clés (français, anglais, malgache) et
# découpage best-effort des rubriques "missions" et "profil", sur le texte de la
# publication + le texte de ses images
# ---------------------------------------------------------------------------

# Appliquées au texte "plié" (_fold : minuscules, sans accents, apostrophes droites)
JOB_OFFER_RE = re.compile(
    r"offres?\s+d'?\s*emplois?|\brecrut(?:e|es|ent|ons|ement|ements)\b|\bnous\s+recherchons\b|\bon\s+recherche\b"
    r"|\bpostes?\s+a\s+pourvoir\b|\bappel\s+a\s+candidatures?\b|\blettre\s+de\s+motivation\b"
    r"|\bdossier\s+de\s+candidature\b|\bcv\s+et\s+(?:lm|lettre)\b"
    r"|\b(?:envoye[rz]|deposer|transmettre)\s+(?:votre|vos|un|le|les)\s+(?:cv|candidatures?|dossiers?)\b"
    r"|\bwe\s*(?:'re|\s+are)\s+hiring\b|\bjob\s+(?:offer|opening|vacancy)\b|\bvacanc(?:y|ies)\b"
    r"|\btolotr'?\s*asa\b|\b(?:mitady|mila)\s+mpiasa\b|\bmisy\s+asa\b"
)
TASKS_HEADING_RE = re.compile(
    r"\b(?:missions?|taches?|responsabilites?|activites?|attributions?|fonctions?|roles?|descriptions?\s+du\s+poste"
    r"|asa\s+atao|andraikitra|responsibilities|duties|tasks?)\b"
)
PROFILE_HEADING_RE = re.compile(
    r"\b(?:profils?|competences?|qualifications?|exigences?|requis|criteres?|fepetra|requirements?|skills?)\b"
)
OTHER_HEADING_RE = re.compile(
    r"\b(?:avantages?|conditions?|dossiers?|candidatures?|postuler|contacts?|lieu|localisation|salaires?"
    r"|remunerations?|horaires?|date\s+limite|cv|envoye[rz]|karama|adresses?|tel|telephone|e-?mail)\b"
)
# Appliquées au texte d'origine (normalisé NFKC)
JOB_TITLE_RES = (
    # "OFFRE D'EMPLOI : GÉRANT(E)", "📢 RECRUTEMENT – VENDEUSE EN LIGNE", "Poste : Commercial"
    re.compile(
        r"(?:offres?\s+d[’']\s*emplois?|recrutement|nous\s+recrutons|on\s+recrute|intitul[ée]\s+du\s+poste|^\W*poste)"
        r"\s*[:–—|-]\s*(\S.{2,119})",
        re.I,
    ),
    # "recrute un(e) comptable", "recherche une secrétaire"
    re.compile(r"\b(?:recrute|recrutons|recherche|recherchons)\s+(?:un|une|des|une?\s*\(e\))\s+([^.,;!\n]{3,80})", re.I),
)
BULLET_RE = re.compile(r"^(?:[•·●○◦▪■□►▶➢➤✓✔*\-–—>]+|\d{1,2}\s*[.)°]|\d️?⃣)\s*")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.replace("’", "'").replace("‘", "'"))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _strip_leading_symbols(line: str) -> str:
    """Retire les emojis de début de ligne ("📱 Missions :" -> "Missions :")."""
    index = 0
    while index < len(line) and (line[index].isspace() or unicodedata.category(line[index]) in ("So", "Sk", "Mn", "Me", "Cf", "Cn")):
        index += 1
    return line[index:]


def _heading(line: str) -> tuple[str, str] | None:
    """(rubrique, contenu après ':') si la ligne est un intitulé ("Missions :", "PROFIL RECHERCHÉ")."""
    if BULLET_RE.match(line):
        return None
    label, colon, inline = line.partition(":")
    if not colon and line.endswith((";", ",", ".")):
        return None  # fin de phrase : élément de liste ("... sens des responsabilités;"), pas un intitulé
    folded = _fold(label)
    if len(folded.split()) > (5 if colon else 8):
        return None
    for kind, pattern in (("tasks", TASKS_HEADING_RE), ("profile", PROFILE_HEADING_RE), ("other", OTHER_HEADING_RE)):
        if pattern.search(folded):
            return kind, inline.strip()
    return None


def _job_sections(lines: list[str]) -> dict[str, list[str]]:
    """Lignes sous les intitulés "missions/tâches" et "profil", jusqu'à l'intitulé ou au paragraphe suivant."""
    sections: dict[str, list[str]] = {"tasks": [], "profile": []}
    current: str | None = None
    previous_marker = ""
    for raw_line in lines:
        raw = raw_line.strip()
        line = _strip_leading_symbols(raw).strip()
        if not line:
            if current and sections[current]:
                current = None
            continue
        heading = _heading(line)
        if heading is not None:
            kind, inline = heading
            current = kind if kind in sections else None
            previous_marker = ""
            if current and inline:
                sections[current].append(inline)
            continue
        if current is None:
            continue

        items = sections[current]
        marker = raw[0] if unicodedata.category(raw[0]) == "So" else ""
        if marker and items and marker != previous_marker:
            current = None  # "🕐 Lundi – Samedi" après une liste : autre bloc
            continue
        is_bullet = bool(marker or BULLET_RE.match(line))
        item = BULLET_RE.sub("", line).strip()
        if not item:
            continue
        if not is_bullet and items and (item[0].islower() or item[0] in "(&") and not items[-1].endswith((".", ";", ":", "!", "?")):
            items[-1] = f"{items[-1]} {item}"  # ligne coupée en deux par l'OCR
        else:
            items.append(item)
        previous_marker = marker
    return {kind: [item.rstrip(" ;,") for item in items] for kind, items in sections.items()}


def _job_title(lines: list[str]) -> str:
    normalized = [unicodedata.normalize("NFKC", line) for line in lines]  # 𝐑𝐄𝐂𝐑𝐔𝐓𝐄𝐌𝐄𝐍𝐓 -> RECRUTEMENT
    for line in normalized:
        for pattern in JOB_TITLE_RES:
            match = pattern.search(line)
            if match:
                return _normalize_text(match.group(1)).strip(" :–—|-!")[:120]
    # Affiche : "NOUS RECRUTONS !" puis l'intitulé quelques lignes plus bas
    for index, line in enumerate(normalized):
        if re.search(r"\brecrut", _fold(line)):
            for following in normalized[index + 1:index + 4]:
                candidate = _normalize_text(_strip_leading_symbols(following))
                if sum(char.isalpha() for char in candidate) >= 8 and not JOB_OFFER_RE.search(_fold(candidate)):
                    return candidate[:120]
    return ""


def analyze_job(post: dict[str, Any]) -> dict[str, Any]:
    image_texts = [text for text in (_image_text(image) for image in post.get("images", [])) if text]
    description = "\n\n".join([post.get("text", ""), *image_texts]).strip()
    if not JOB_OFFER_RE.search(_fold(description)):
        return {"is_offer": False}
    lines = description.split("\n")
    sections = _job_sections(lines)
    return {
        "is_offer": True,
        "title": _job_title(lines),
        "description": description,
        "tasks": sections["tasks"],
        "profile": sections["profile"],
    }


def write_jobs_export(store: PostStore, target: GroupTarget) -> tuple[Path, int]:
    """downloaded_files/facebook_jobs_<groupe>.json : uniquement les offres d'emploi, champs utiles."""
    offers: dict[str, dict[str, Any]] = {}
    for post in store.posts():
        job = post.get("job") or {}
        if not job.get("is_offer"):
            continue
        offers[post["post_id"]] = {
            "post_url": post["post_url"],
            "title": job["title"],
            "description": job["description"],
            "tasks": job["tasks"],
            "profile": job["profile"],
            "emails": post["emails"],
            "phones": post["phones"],
            "author_name": post["author_name"],
            "author_url": post["author_url"],
            "group_name": post["group_name"],
            "time_text": post["time_text"],
            "scraped_at": post["scraped_at"],
            "images": [image["file"] for image in post["images"] if image.get("file")],
        }
    path = OUTPUT_DIR / f"facebook_jobs_{target.file_slug}.json"
    _write_json_atomic(path, offers)
    return path, len(offers)


# ---------------------------------------------------------------------------
# Navigateur (mode CDP)
# ---------------------------------------------------------------------------


def _reset_profile_exit_state(profile_dir: Path) -> None:
    """
    Même correctif que senegal_script : Chrome marque exit_type="Crashed" après
    une fermeture pilotée par Selenium, puis affiche au lancement suivant un
    bandeau "Restaurer les pages ?" qui bloque le rendu en headless.
    """
    prefs_path = profile_dir / "Default" / "Preferences"
    if not prefs_path.exists():
        return
    try:
        data = json.loads(prefs_path.read_text(encoding="utf-8"))
        profile = data.setdefault("profile", {})
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        prefs_path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _activate_cdp_mode(sb: SB) -> None:
    """
    sb.activate_cdp_mode() lit l'URL courante du driver, parfois encore None juste
    après le lancement de Chrome (TypeError dans SeleniumBase, observé sur un profil
    neuf) : on laisse quelques secondes à Chrome avant de réessayer.
    """
    for _ in range(2):
        try:
            sb.activate_cdp_mode()
            return
        except TypeError:
            sb.sleep(3)
    sb.activate_cdp_mode()


def _cdp_send(sb: SB, command: Any) -> Any:
    return sb.cdp.loop.run_until_complete(sb.cdp.page.send(command))


def _run_js(sb: SB, script: str, args: Any = None) -> Any:
    """Exécute un des scripts *_JS ci-dessus et décode son JSON (None en cas d'erreur)."""
    code = script.replace("__ARGS__", json.dumps(args if args is not None else {}))
    try:
        raw = sb.cdp.evaluate(code)
    except Exception as exc:
        print(f"    [JS] {str(exc)[:200]}")
        return None
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _page_state(sb: SB) -> dict[str, Any]:
    return _run_js(sb, PAGE_STATE_JS) or {}


def _is_auth_wall(state: dict[str, Any]) -> bool:
    url = state.get("url", "")
    return bool(state.get("password")) or any(part in url for part in ("/login", "/checkpoint", "/two_step_verification"))


def _outer_html(sb: SB, key: str) -> str:
    """
    outerHTML d'une publication marquée, shadow roots FERMÉS compris (horodatage).
    Runtime.evaluate -> objectId -> DOM.getOuterHTML : pas de DOM.getDocument
    complet (sb.cdp.find_element le refait à chaque appel, très lent sur Facebook).
    """
    selector = json.dumps(f'[data-scrape-key="{key}"]')
    try:
        remote, exception = _cdp_send(
            sb, cdp_runtime.evaluate(expression=f"document.querySelector({selector})", return_by_value=False)
        )
    except Exception:
        return ""
    object_id = getattr(remote, "object_id", None)
    if exception is not None or object_id is None:
        return ""  # publication retirée du DOM entre-temps
    try:
        return _cdp_send(sb, cdp_dom.get_outer_html(object_id=object_id, include_shadow_dom=True)) or ""
    except Exception:
        return ""
    finally:
        with suppress(Exception):
            _cdp_send(sb, cdp_runtime.release_object(object_id))


def _real_hover_time_link(sb: SB, key: str) -> bool:
    point = _run_js(sb, TIME_LINK_POINT_JS, {"key": key})
    if not point:
        return False
    try:
        _cdp_send(sb, cdp_input.dispatch_mouse_event(type_="mouseMoved", x=point["x"], y=point["y"]))
    except Exception:
        return False
    return True


def read_date_tooltip(sb: SB, key: str, timeout: float = 2.5) -> str:
    """
    Date complète d'une publication : Facebook n'affiche que "3 j" mais montre
    "samedi 12 septembre 2026 à 14:32" dans une infobulle au survol réel du lien
    horodatage. Chaîne vide si l'infobulle n'apparaît pas.
    """
    if not _real_hover_time_link(sb, key):
        return ""
    text = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = _run_js(sb, DATE_TOOLTIP_JS) or ""
        if text:
            break
        sb.sleep(0.3)
    with suppress(Exception):  # souris hors du lien : l'infobulle ne masque pas la publication suivante
        _cdp_send(sb, cdp_input.dispatch_mouse_event(type_="mouseMoved", x=1, y=1))
    return _normalize_text(text)


def _session_active(sb: SB) -> bool:
    """Vérifie la session sur la page courante, sans naviguer."""
    try:
        cookies = sb.cdp.get_all_cookies()
    except Exception:
        return False
    if not any(getattr(cookie, "name", "") == "c_user" for cookie in cookies):
        return False
    return not _is_auth_wall(_page_state(sb))


def is_logged_in(sb: SB) -> bool:
    try:
        sb.goto(FACEBOOK_URL)
        sb.sleep(4)
    except Exception:
        return False
    return _session_active(sb)


def login_and_wait(sb: SB) -> bool:
    print("\n" + "=" * 60)
    print("  CONNEXION FACEBOOK")
    print("=" * 60)
    print(f"\nUne fenêtre Chrome est ouverte sur {FACEBOOK_URL}")
    print("Connectez-vous (email/téléphone, mot de passe, code de vérification si demandé).")
    while True:
        answer = input("\nUne fois connecté, appuyez sur ENTRÉE ici (q + ENTRÉE pour quitter)... ")
        if answer.strip().lower() == "q":
            return False
        # Pas de navigation ici : ne pas casser une vérification en deux étapes en cours
        if _session_active(sb):
            print(f"[OK] Connecté - session sauvegardée dans le profil {PROFILE_DIR.name} (réutilisée aux prochains lancements).")
            return True
        print("[WARN] Pas encore connecté (cookie de session absent ou page de connexion/vérification affichée).")


# ---------------------------------------------------------------------------
# Scroll infini d'un groupe
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScrapeOptions:
    sort: str
    max_posts: int
    stop_after_known: int
    max_idle: int
    scroll_delay: float
    dump_html: bool
    download_images: bool = True
    ocr: bool = True
    any_root: bool = False  # pages : publications hors div[role="feed"]
    exact_dates: bool = False  # survol réel du lien horodatage pour lire la date complète (infobulle)
    max_age_days: int = 0  # 15 = seulement les publications des 15 derniers jours (aujourd'hui compris) ; 0 = toutes


@dataclass(slots=True)
class GroupStats:
    new: int = 0
    known: int = 0
    without_permalink: int = 0
    too_old: int = 0


OLD_STREAK_STOP = 5  # publications trop anciennes d'affilée avant d'arrêter le fil (une vieille épinglée en tête ne l'arrête pas)


def post_too_old(post: dict[str, Any], max_age_days: int) -> bool:
    return is_too_old(post.get("date_tooltip", ""), post.get("time_text", ""), post.get("scraped_at", ""), max_age_days)


def open_group_feed(sb: SB, target: GroupTarget, sort: str) -> str | None:
    """Ouvre le fil du groupe ; renvoie le nom du groupe, ou None si le fil est introuvable."""
    url = target.feed_url(sort)
    print(f"Ouverture de {url}")
    for attempt in range(1, 4):
        try:
            if attempt == 1:
                sb.goto(url)
            else:
                sb.refresh()
        except Exception as exc:
            print(f"  [RETRY] {attempt}/3 - {exc}")
            sb.sleep(3)
            continue
        deadline = time.monotonic() + FEED_TIMEOUT
        while time.monotonic() < deadline:
            state = _page_state(sb)
            if state.get("feed"):
                return _clean_group_title(state.get("title", ""))
            if _is_auth_wall(state):
                print("  [ERREUR] Facebook demande une connexion/vérification - relancez avec --login")
                return None
            sb.sleep(1)
        print(f"  [RETRY] {attempt}/3 - fil du groupe introuvable")
    print('  [ERREUR] Aucun <div role="feed"> : groupe privé non rejoint, lien invalide ou page modifiée par Facebook ?')
    return None


def _read_post(
    sb: SB, key: str, target: GroupTarget, group_name: str, real_hover: bool
) -> tuple[dict[str, Any] | None, str]:
    html = _outer_html(sb, key)
    post = parse_post(html, target, group_name) if html else None
    if post is not None and not post["post_url"] and real_hover and _real_hover_time_link(sb, key):
        sb.sleep(HOVER_WAIT)
        retry_html = _outer_html(sb, key)
        retried = parse_post(retry_html, target, group_name) if retry_html else None
        if retried is not None and retried["post_url"]:
            return retried, retry_html
    return post, html


def _dump_html(target: GroupTarget, post_id: str, html: str) -> None:
    folder = OUTPUT_DIR / "facebook_html" / target.file_slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{post_id}.html").write_text(html, encoding="utf-8")


def _print_post(index: int, post: dict[str, Any]) -> None:
    preview = _normalize_text(post["text"])[:80] or "(sans texte)"
    print(f"  + #{index} {post['author_name'] or '?'} ({post['time_text'] or '?'}) : {preview}")
    details = []
    if post["phones"]:
        details.append("tél : " + ", ".join(post["phones"]))
    if post["emails"]:
        details.append("email : " + ", ".join(post["emails"]))
    if post["images"]:
        saved = sum(1 for image in post["images"] if image.get("file"))
        details.append(f"images : {saved}/{len(post['images'])} enregistrée(s)" if saved else f"{len(post['images'])} image(s)")
    if details:
        print("      " + " | ".join(details))


def scrape_group(sb: SB, target: GroupTarget, opts: ScrapeOptions) -> GroupStats:
    stats = GroupStats()
    store = PostStore(target.output_file)
    if opts.download_images:
        download_missing_images(store, target)
    group_name = open_group_feed(sb, target, opts.sort)
    if group_name is not None:
        print(f"Groupe : {group_name or target.group_id} - {store.count()} publication(s) déjà en cache")
        try:
            _scroll_feed(sb, target, group_name, store, opts, stats)
        finally:
            store.save()
    # Hors du finally : sauté sur Ctrl+C, rattrapé au lancement suivant (ne traite que ce qui manque)
    finalize_posts(store, target, opts)
    return stats


def _scroll_feed(
    sb: SB, target: GroupTarget, group_name: str, store: PostStore, opts: ScrapeOptions, stats: GroupStats
) -> None:
    real_hover = True
    permalink_misses = 0
    known_streak = 0
    old_streak = 0
    idle = 0
    while True:
        batch = _run_js(
            sb, COLLECT_POSTS_JS, {"max": BATCH_SIZE, "seeMore": list(SEE_MORE_LABELS), "anyRoot": opts.any_root}
        )
        if not batch or not batch.get("feed"):
            reason = "connexion demandée" if _is_auth_wall(_page_state(sb)) else "page changée"
            print(f"  [WARN] Fil du groupe perdu ({reason}) - arrêt du groupe")
            return

        items = batch["posts"]
        if items:
            sb.sleep(EXPAND_WAIT)
        for item in items:
            post, html = _read_post(sb, item["key"], target, group_name, real_hover)
            if post is None:
                continue
            # Pages : Facebook peut changer l'id (pfbid) d'une même publication entre deux sessions
            canonical_post_id = getattr(target, "canonical_post_id", None)
            if canonical_post_id is not None:
                post["post_id"] = canonical_post_id(post, store)
            if opts.dump_html:
                _dump_html(target, post["post_id"], html)

            if post["post_url"]:
                permalink_misses = 0
            else:
                stats.without_permalink += 1
                permalink_misses += 1
                if real_hover and permalink_misses >= HOVER_MAX_MISSES:
                    real_hover = False
                    print("  [INFO] Le survol ne révèle pas les permaliens - ids dérivés du contenu pour la suite")

            known = store.get(post["post_id"])
            if known is None and opts.exact_dates:
                post["date_tooltip"] = read_date_tooltip(sb, item["key"])
            if opts.max_age_days:
                # Publication déjà connue : sa date enregistrée (infobulle + date de scraping d'origine)
                if post_too_old(known or post, opts.max_age_days):
                    stats.too_old += 1
                    old_streak += 1
                    if old_streak >= OLD_STREAK_STOP:
                        print(f"  {old_streak} publication(s) de plus de {opts.max_age_days} jours d'affilée - fin des publications récentes")
                        return
                    continue
                old_streak = 0

            if known is not None:
                stats.known += 1
                known_streak += 1
                if opts.stop_after_known and known_streak >= opts.stop_after_known:
                    print(f"  {known_streak} publication(s) déjà connue(s) d'affilée - arrêt du groupe")
                    return
                continue

            known_streak = 0
            if opts.download_images:
                download_post_images(post, target)
            store.add(post["post_id"], post)
            stats.new += 1
            _print_post(stats.new, post)
            if opts.max_posts and stats.new >= opts.max_posts:
                print(f"  Limite de {opts.max_posts} nouvelle(s) publication(s) atteinte")
                return
        store.save()
        if len(items) >= BATCH_SIZE:
            continue  # d'autres publications chargées attendent : pas de scroll qui les ferait virtualiser

        height_before = (_run_js(sb, SCROLL_TO_BOTTOM_JS) or {}).get("height", 0)
        sb.sleep(opts.scroll_delay * random.uniform(0.7, 1.3))
        if items or _page_state(sb).get("height", 0) > height_before:
            idle = 0
            continue
        idle += 1
        if idle >= opts.max_idle:
            print(f"  Fin du fil : rien de nouveau après {idle} scroll(s)")
            return
        _run_js(sb, NUDGE_UP_JS)
        sb.sleep(1)


def finalize_posts(store: PostStore, target: GroupTarget, opts: ScrapeOptions) -> None:
    """
    Après le scroll : OCR des images pas encore lues, emails/téléphones trouvés sur
    les images ajoutés à la publication, analyse "offre d'emploi" recalculée pour
    tout le cache (les règles s'appliquent aussi aux anciennes publications), puis
    export des offres.
    """
    pending = [
        post for post in store.posts()
        if any(image.get("file") and "ocr_text" not in image for image in post.get("images", []))
    ]
    engine = _get_ocr_engine() if opts.ocr and pending else None
    if engine is not None:
        print(f"Lecture du texte des images (OCR) : {len(pending)} publication(s)...")
        for index, post in enumerate(pending, 1):
            ocr_post_images(engine, post)
            store.add(post["post_id"], post)
            if index % 25 == 0:
                store.save()
                print(f"  ... {index}/{len(pending)}")

    for post in store.posts():
        image_text = "\n".join(_image_text(image) for image in post.get("images", []))
        emails = _merge_unique(post["emails"], extract_emails(image_text))
        phones = _merge_unique(post["phones"], extract_phones(image_text))
        found = emails[len(post["emails"]):] + phones[len(post["phones"]):]
        if found:
            print(f"  [image] {post['author_name'] or post['post_id']} : {', '.join(found)}")
        job = analyze_job(post)
        # images_done : lu par pages.py (analyse IA) - toutes les images ont un fichier ou une erreur définitive
        done = all(image.get("file") or image.get("download_error") for image in post.get("images", []))
        if found or job != post.get("job") or done != post.get("images_done"):
            post.update(emails=emails, phones=phones, job=job, images_done=done)
            store.add(post["post_id"], post)
    store.save()

    jobs_file, count = write_jobs_export(store, target)
    print(f"Offres d'emploi : {count} -> {jobs_file}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape les publications de groupes Facebook (scroll infini du fil)")
    parser.add_argument(
        "--url", action="append", default=[], metavar="URL",
        help="Lien d'un groupe Facebook (répétable). Par défaut : liens de --groups-file",
    )
    parser.add_argument(
        "--groups-file", default=str(GROUPS_FILE), metavar="FICHIER",
        help="Fichier texte, un lien de groupe par ligne (défaut : facebook_script/groups.txt)",
    )
    parser.add_argument(
        "--sort", choices=list(SORT_PARAMS), default="chrono",
        help="Tri du fil : chrono = nouvelles publications (défaut), activity = activité récente, default = tri Facebook",
    )
    parser.add_argument(
        "--max-posts", type=int, default=0, metavar="N",
        help="Nombre max de nouvelles publications par groupe (0 = illimité)",
    )
    parser.add_argument(
        "--stop-after-known", type=int, default=0, metavar="N",
        help="Arrête un groupe après N publications déjà en cache d'affilée (0 = jamais) - relances avec --sort chrono",
    )
    parser.add_argument(
        "--max-age-days", type=int, default=15, metavar="N",
        help="Seulement les publications des N derniers jours, aujourd'hui compris (défaut 15 ; 0 = toutes)",
    )
    parser.add_argument(
        "--max-idle", type=int, default=DEFAULT_MAX_IDLE, metavar="N",
        help=f"Scrolls sans nouveau contenu avant de conclure à la fin du fil (défaut {DEFAULT_MAX_IDLE})",
    )
    parser.add_argument(
        "--scroll-delay", type=float, default=DEFAULT_SCROLL_DELAY, metavar="SECONDES",
        help=f"Pause moyenne après chaque scroll (défaut {DEFAULT_SCROLL_DELAY}s, variation aléatoire +/-30 pourcent)",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Ne télécharge pas les images (seules leurs URL, qui expirent en quelques jours, sont gardées)",
    )
    parser.add_argument(
        "--no-ocr", action="store_true",
        help="Ne lit pas le texte des images (emails/téléphones des affiches non récupérés)",
    )
    parser.add_argument(
        "--dump-html", action="store_true",
        help="Sauvegarde le HTML brut de chaque publication dans downloaded_files/facebook_html/",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="Propose l'étape de connexion manuelle même si une session est détectée",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Mode sans fenêtre (le profil doit déjà être connecté)",
    )
    return parser.parse_args()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")  # emojis des publications sur une console cp1252

    args = parse_args()
    targets = load_targets(args.url, Path(args.groups_file))
    if not targets:
        print(f"[ERREUR] Aucun groupe à scraper : passez --url ou ajoutez des liens dans {args.groups_file}")
        return

    opts = ScrapeOptions(
        sort=args.sort,
        max_posts=args.max_posts,
        stop_after_known=args.stop_after_known,
        max_idle=args.max_idle,
        scroll_delay=args.scroll_delay,
        dump_html=args.dump_html,
        download_images=not args.no_images,
        ocr=not args.no_ocr,
        max_age_days=args.max_age_days,
    )
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _reset_profile_exit_state(PROFILE_DIR)

    totals = GroupStats()
    with SB(uc=True, locale="fr", user_data_dir=str(PROFILE_DIR), headless=args.headless) as sb:
        _activate_cdp_mode(sb)
        try:
            logged_in = is_logged_in(sb)
            if args.headless and (args.login or not logged_in):
                print("[ERREUR] Profil Facebook non connecté : relancez sans --headless pour vous connecter.")
                return
            if args.login or not logged_in:
                if not login_and_wait(sb):
                    print("Abandon.")
                    return
            else:
                print(f"[INFO] Session Facebook déjà active dans {PROFILE_DIR.name} - pas besoin de se reconnecter.")

            for index, target in enumerate(targets, 1):
                print(f"\n[{index}/{len(targets)}] Groupe {target.group_id}")
                stats = scrape_group(sb, target, opts)
                totals.new += stats.new
                totals.known += stats.known
                totals.without_permalink += stats.without_permalink
                totals.too_old += stats.too_old
                print(
                    f"  -> {stats.new} nouvelle(s), {stats.known} déjà connue(s), {stats.too_old} trop ancienne(s), "
                    f"{stats.without_permalink} sans permalien - {target.output_file}"
                )
        except KeyboardInterrupt:
            print("\n[INFO] Interruption (Ctrl+C) - les publications déjà lues sont sauvegardées.")

    print(
        f"\nTerminé - {totals.new} nouvelle(s) publication(s), {totals.known} déjà connue(s), "
        f"{totals.too_old} trop ancienne(s) ignorée(s), {totals.without_permalink} sans permalien (id dérivé du contenu)."
    )


if __name__ == "__main__":
    main()

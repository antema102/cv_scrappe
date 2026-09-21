"""
facebook_script/pages.py
========================
Scraping de pages Facebook (multi-pays via --country, voir countries.py) +
analyse IA OpenAI de chaque publication + JSON au format exact du backend,
SANS envoi au backend (fichiers à vérifier d'abord).

Un pays = une entrée dans COUNTRIES (countries.py, indicatif téléphonique +
longueur du numéro national) + un dossier countries/<pays>/ contenant
pages.txt et groups.txt (un lien par ligne, "#" = commentaire ; --pages-file /
--groups-file pour un autre chemin). Ajouter un pays = ajouter son
CountryProfile puis créer countries/<pays>/pages.txt + groups.txt (voir
countries/maroc/ comme modèle vide).

Pipeline, chaque étape relançable et ne refaisant que ce qui manque :
  1. Scraping (navigateur, profil my_custom_profile_facebook partagé avec scraper.py) :
     infos publiques de la page (onglet "À propos"), publications du fil (même
     parsing que les groupes : texte, auteur, permalien, horodatage, date complète
     lue dans l'infobulle), puis TOUTES les images de chaque publication via la
     visionneuse photo (le fil n'en montre que 5 + "+N") en pleine résolution,
     téléchargées et lues par OCR.
  2. Analyse IA (ai_extractor.py) : texte + contexte de la page + OCR + toutes les
     images, pour chaque publication, avec ou sans image. Mise en cache.
  3. Consolidation (company_registry.py) : dédoublonnage des entreprises de toutes
     les pages du pays, company_id stables, documents backend.

Sorties (downloaded_files/facebook_pages/) :
  companies_<pays>.json         -> companies_scrappe        (clé company_id)
  jobs_<pays>.json              -> jobs_scrappe             (clé job_id)
  job_publications_<pays>.json  -> job_publications_scrappe (clé job_id : published_at)
  analysis_<pays>.json          -> revue humaine : réponses IA, sources, fusions, adresse,
                                   produits/services, catégories (absents du backend)
  identity_registry_<pays>.json -> clé normalisée -> company_id (stabilité des ids)
  pages_<pays>.json, posts_<page>.json, images/, ai_cache/ -> données brutes et cache

Usage :
    python facebook_script/pages.py                                  # countries/madagascar/{pages,groups}.txt : scraping + IA + JSON
    python facebook_script/pages.py --country maroc                  # countries/maroc/{pages,groups}.txt
    python facebook_script/pages.py --url https://www.facebook.com/profile.php?id=61559428572369
    python facebook_script/pages.py --max-posts 5                     # test rapide
    python facebook_script/pages.py --max-age-days 30                 # publications des 30 derniers jours (défaut 15)
    python facebook_script/pages.py --no-ai                           # scraping seul (aucun appel OpenAI)
    python facebook_script/pages.py --skip-scrape                     # IA + JSON sur les publications déjà scrapées
    python facebook_script/pages.py --skip-scrape --reanalyze         # ré-analyse ce qui a été analysé avec un ancien prompt/modèle
    python facebook_script/pages.py --max-ai-tokens 500000            # plafond de tokens IA pour ce lancement
    python facebook_script/pages.py --push-only                       # envoie au backend les JSON déjà validés
    python facebook_script/pages.py --skip-scrape --push              # IA + JSON puis envoi au backend
    python facebook_script/pages.py --push-only --no-clean            # envoi sans supprimer les doublons déjà envoyés
    python facebook_script/pages.py --no-groups                       # pages seulement (défaut : countries/<pays>/pages.txt + groups.txt)
    python facebook_script/pages.py --no-pages                        # groupes seulement (countries/<pays>/groups.txt)
    python facebook_script/pages.py --keep-images                     # garde les images (défaut : supprimées après analyse)
    python facebook_script/pages.py --group-url https://www.facebook.com/groups/2367943963509691 --push

OPENAI_API_KEY / OPENAI_MODEL : environnement ou .env (racine, backend/.env).
Envoi au backend (backend_push.py) uniquement avec --push / --push-only : SCRAPER_API_URL (défaut
http://localhost:3500), incrémental (push_state_<pays>.json) ; les documents déjà envoyés puis regroupés
avec un autre sont supprimés du backend (stale_replacement_finder), sauf --no-clean.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import scraper as fb
from ai_extractor import (
    COMPANY_TYPES, DEFAULT_BASE_URL, DEFAULT_MODEL, OFFER_TYPES, PROMPT_VERSION, AiError, AiExtractor, load_dotenv, mask_secret,
)
from backend_push import DEFAULT_API_URL, REMOVE, ReplacementFinder, push_country
from company_registry import (
    CompanyRegistry, build_candidate, company_document, fold, names_similar, normalize_name, review_entry, website_domain,
)
from countries import COUNTRIES, CountryProfile
from fb_dates import published_at
from seleniumbase import SB

DEFAULT_MAX_PHOTOS = 60
# Filtre des images envoyées à l'IA (coût) : contact lu par l'OCR, ou texte suffisant (affiche, offre)
AI_MIN_TEXT_CHARS = 40
WEBSITE_RE = re.compile(r"(?:https?://|www\.)\S+|\b[a-z0-9][a-z0-9\-]*\.(?:mg|com|fr|org|net|io|co)\b", re.I)
VIEWER_TIMEOUT = 12
NEXT_PHOTO_LABELS = ("photo suivante", "next photo", "suivante", "suivant", "next")
# Plus grand côté minimal d'une photo de la visionneuse : pendant un changement de photo, la grande image n'est pas
# encore là et une miniature de la page (vu : 100x100, cdn t1.15752-9) était prise à la place, puis le parcours s'arrêtait
VIEWER_MIN_SIDE = 250
VIEWER_NEXT_RETRIES = 3  # clic « suivante » retenté : le bouton apparaît parfois après la photo
VIEWER_MAX_ATTEMPTS = 3  # lancements où une publication incomplète (ex. 12 photos sur 31) est reprise

PAGE_INFO_JS = r"""
(() => {
  const main = document.querySelector('[role="main"]') || document.body;
  let logo = '';
  let best = 0;
  for (const el of document.querySelectorAll('svg image, img')) {
    const src = el.getAttribute('xlink:href') || el.getAttribute('href') || el.getAttribute('src') || '';
    if (!src.includes('scontent')) continue;
    const r = el.getBoundingClientRect();
    // Photo de profil de la page : carrée, dans le haut de l'écran
    if (r.top > 700 || r.width < 80 || r.width > 400 || Math.abs(r.width - r.height) > 4) continue;
    if (r.width > best) { best = r.width; logo = src; }
  }
  return JSON.stringify({title: document.title || '', about: (main.innerText || '').trim(), logo});
})()
"""

VIEWER_STATE_JS = r"""
(() => {
  const args = __ARGS__;
  const href = location.href;
  const m = href.match(/[?&]fbid=(\d+)/) || href.match(/\/photos\/[^/]+\/(\d+)/) || href.match(/\/photo\/(\d+)/);
  const bigEnough = (el) => Math.max(el.naturalWidth, el.naturalHeight) >= args.minSide;
  let img = Array.from(document.querySelectorAll('img[data-visualcompletion="media-vc-image"]')).find(bigEnough) || null;
  if (!img) {
    let best = 0;
    for (const el of document.querySelectorAll('img')) {
      if (!(el.currentSrc || el.src || '').includes('scontent') || !bigEnough(el)) continue;
      const area = el.naturalWidth * el.naturalHeight;
      if (area > best) { best = area; img = el; }
    }
  }
  const labels = new Set(args.nextLabels);
  const next = Array.from(document.querySelectorAll('[aria-label]')).find((el) =>
    labels.has((el.getAttribute('aria-label') || '').trim().toLowerCase()) && el.getBoundingClientRect().width > 0);
  if (args.click) {
    if (next) next.click();
    return JSON.stringify({clicked: !!next});
  }
  // Image réellement chargée : pendant un changement de photo, src peut valoir l'URL de la page
  const loaded = !!img && img.complete && img.naturalWidth > 0;
  return JSON.stringify({
    fbid: m ? m[1] : '',
    src: loaded ? (img.currentSrc || img.src || '') : '',
    alt: img ? (img.alt || '') : '',
    hasNext: !!next,
  });
})()
"""


# ---------------------------------------------------------------------------
# Pages à scraper
# ---------------------------------------------------------------------------


def pages_dir() -> Path:
    return fb.OUTPUT_DIR / "facebook_pages"


def _clean_facebook_url(href: str) -> str:
    """URL absolue sans les paramètres de tracking (__cft__, __tn__...)."""
    parsed = urlparse(urljoin(fb.FACEBOOK_URL, href))
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.startswith("__")]
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


GROUP_ID_PREFIX = "group-"  # id de source d'un groupe dans pages_<pays>.json : "group-<id du groupe>"


@dataclass(frozen=True, slots=True)
class PageTarget:
    page_id: str  # id numérique (profile.php?id=) ou nom personnalisé ; "group-<id>" pour un groupe
    page_url: str
    about_url: str
    kind: str = "page"  # "group" : publications collectées par scraper.py (groupes), analysées par le même pipeline
    posts_path: str = ""  # fichier de publications relatif à downloaded_files (groupes) ; vide = facebook_pages/posts_<slug>.json

    @property
    def file_slug(self) -> str:
        return re.sub(r"[^\w.\-]", "_", self.page_id)

    @property
    def posts_file(self) -> Path:
        return fb.OUTPUT_DIR / self.posts_path if self.posts_path else pages_dir() / f"posts_{self.file_slug}.json"

    # Interface attendue par scraper.parse_post
    def post_url(self, post_id: str, href: str) -> str:
        if self.kind == "group":
            return f"https://www.facebook.com/groups/{self.page_id.removeprefix(GROUP_ID_PREFIX)}/posts/{post_id}/"
        if "/posts/" in href or "story_fbid=" in href:
            return _clean_facebook_url(href)
        return f"https://www.facebook.com/{self.page_id}/posts/{post_id}"  # id trouvé via un lien photo (set=pcb.)

    def source_fields(self, name: str) -> dict[str, str]:
        return {"page_id": self.page_id, "page_name": name, "page_url": self.page_url}

    def canonical_post_id(self, post: dict[str, Any], store: fb.PostStore) -> str:
        """Id déjà connu de cette publication (même photo ou même texte seul), sinon le sien."""
        index = getattr(store, "_known_posts", None)
        if index is None or index.size != store.count():  # reconstruit seulement si le cache a changé
            index = KnownPosts(store.posts())
            store._known_posts = index
        return index.find(post) or post["post_id"]


PHOTO_FBID_RE = re.compile(r"[?&]fbid=(\d+)|/photos/[^/?#]+/(\d+)")
KNOWN_TEXT_MIN_CHARS = 20


def _post_photo_ids(post: dict[str, Any]) -> set[str]:
    ids = {image["fbid"] for image in post.get("images", []) if image.get("fbid")}
    for link in post.get("photo_links", []):
        match = PHOTO_FBID_RE.search(link)
        if match:
            ids.add(match.group(1) or match.group(2))
    return ids


class KnownPosts:
    """
    Index des publications déjà enregistrées : Facebook ne garde pas toujours le même pfbid
    d'une session à l'autre (vu : même publication, mêmes 30 photos, deux pfbid). Même photo
    (fbid, stable) = même publication ; sans photo, même texte (>= KNOWN_TEXT_MIN_CHARS).
    Index plutôt que comparaison deux à deux : milliers de publications par page.
    """

    def __init__(self, posts: list[dict[str, Any]] = ()) -> None:
        self.ids: set[str] = set()
        self.by_photo: dict[str, set[str]] = {}
        self.photo_count: dict[str, int] = {}
        self.order: dict[str, int] = {}
        self.by_text: dict[str, str] = {}
        self.size = 0
        for post in posts:
            self.add(post)

    @staticmethod
    def _text(post: dict[str, Any]) -> str:
        return " ".join(fold(post.get("text", "")).split())

    def add(self, post: dict[str, Any]) -> None:
        if post["post_id"] not in self.photo_count:
            self.order[post["post_id"]] = len(self.order)  # à égalité, la 1re publication enregistrée gagne
        self.ids.add(post["post_id"])
        self.size += 1
        photos = _post_photo_ids(post)
        self.photo_count[post["post_id"]] = len(photos)
        for photo in photos:
            self.by_photo.setdefault(photo, set()).add(post["post_id"])
        text = self._text(post)
        if not photos and len(text) >= KNOWN_TEXT_MIN_CHARS:
            self.by_text.setdefault(text, post["post_id"])

    def find(self, post: dict[str, Any]) -> str | None:
        if post["post_id"] in self.ids:
            return post["post_id"]
        photos = _post_photo_ids(post)
        overlaps: dict[str, int] = {}
        for photo in photos:
            for other_id in self.by_photo.get(photo, ()):
                overlaps[other_id] = overlaps.get(other_id, 0) + 1
        for other_id, common in sorted(overlaps.items(), key=lambda item: (-item[1], self.order[item[0]])):
            # Au moins la moitié des photos en commun (comparées aux vignettes du fil : 5 max) : une même photo
            # réutilisée (logo, bannière) dans des publications différentes ne les confond pas
            if common * 2 >= min(len(photos), self.photo_count[other_id]):
                return other_id
        text = self._text(post)
        if not photos and len(text) >= KNOWN_TEXT_MIN_CHARS:
            return self.by_text.get(text)
        return None


def find_known_post(post: dict[str, Any], posts: list[dict[str, Any]]) -> str | None:
    """Id de la publication déjà connue parmi `posts` (hors `post` lui-même), sinon None."""
    return KnownPosts([other for other in posts if other is not post]).find(post)


def canonical_posts(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """(publications uniques, doublon post_id -> post_id gardé) ; la 1re scrapée est gardée."""
    index = KnownPosts()
    kept: list[dict[str, Any]] = []
    duplicates: dict[str, str] = {}
    for post in sorted(posts, key=lambda p: p.get("scraped_at", "")):
        known = index.find(post)
        if known is not None and known != post["post_id"]:
            duplicates[post["post_id"]] = known
            continue
        index.add(post)
        kept.append(post)
    return kept, duplicates


def parse_page_url(url: str) -> PageTarget | None:
    raw = url.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if "facebook.com" not in parsed.netloc.lower():
        return None
    path = parsed.path.rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    page_id = ""
    if path == "/profile.php":
        page_id = parse_qs(parsed.query).get("id", [""])[0]
    elif segments[:1] == ["people"] and len(segments) >= 3:
        page_id = segments[2]
    if page_id:
        if not page_id.isdigit():
            return None
        page_url = f"https://www.facebook.com/profile.php?id={page_id}"
        return PageTarget(page_id, page_url, f"{page_url}&sk=about")
    if segments and segments[0].lower() not in fb.RESERVED_PATHS:
        page_url = f"https://www.facebook.com/{segments[0]}"
        return PageTarget(segments[0], page_url, f"{page_url}/about")
    return None


def load_page_targets(urls: list[str], pages_file: Path) -> list[PageTarget]:
    lines = list(urls)
    if not lines and pages_file.exists():
        lines = pages_file.read_text(encoding="utf-8-sig").splitlines()
    targets: list[PageTarget] = []
    for line in (raw.strip() for raw in lines):
        if not line or line.startswith("#"):
            continue
        target = parse_page_url(line)
        if target is None:
            print(f"[WARN] Lien ignoré (pas une page Facebook) : {line}")
        elif all(t.page_id != target.page_id for t in targets):
            targets.append(target)
    return targets


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scraping d'une page
# ---------------------------------------------------------------------------


def fetch_page_info(sb: SB, target: PageTarget) -> dict[str, Any]:
    """Nom, photo de profil et texte public de l'onglet "À propos" (contacts, adresse, catégorie de la page)."""
    info: dict[str, Any] = {"page_id": target.page_id, "page_url": target.page_url, "name": "", "about_text": "", "logo_url": ""}
    try:
        sb.goto(target.about_url)
        sb.sleep(4)
    except Exception as exc:
        print(f"  [WARN] Onglet À propos inaccessible : {exc}")
        return info
    state = fb._run_js(sb, PAGE_INFO_JS) or {}
    lines = list(dict.fromkeys(fb._normalize_text(line) for line in state.get("about", "").split("\n")))
    info.update(
        name=fb._clean_group_title(state.get("title", "")),
        about_text="\n".join(line for line in lines if line),
        logo_url=state.get("logo", ""),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return info


def open_page_feed(sb: SB, target: PageTarget) -> str | None:
    print(f"Ouverture de {target.page_url}")
    try:
        sb.goto(target.page_url)
    except Exception as exc:
        print(f"  [ERREUR] {exc}")
        return None
    deadline = time.monotonic() + fb.FEED_TIMEOUT
    while time.monotonic() < deadline:
        state = fb._page_state(sb)
        if state.get("posts"):
            return fb._clean_group_title(state.get("title", ""))
        if fb._is_auth_wall(state):
            print("  [ERREUR] Facebook demande une connexion/vérification - relancez avec --login")
            return None
        fb._run_js(sb, "(() => { window.scrollBy(0, 800); return JSON.stringify(true); })()")  # publications sous l'intro
        sb.sleep(1.5)
    print("  [ERREUR] Aucune publication trouvée (page vide, restreinte ou modifiée par Facebook ?)")
    return None


def _viewer_js(sb: SB, click: bool) -> dict[str, Any]:
    args = {"nextLabels": list(NEXT_PHOTO_LABELS), "click": click, "minSide": VIEWER_MIN_SIDE}
    return fb._run_js(sb, VIEWER_STATE_JS, args) or {}


def _viewer_state(sb: SB, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attend qu'une photo (différente de la précédente, assez grande) soit affichée dans la visionneuse."""
    deadline = time.monotonic() + VIEWER_TIMEOUT
    while time.monotonic() < deadline:
        state = _viewer_js(sb, click=False)
        if state.get("src"):
            state["ident"] = state.get("fbid") or urlparse(state["src"]).path
            if previous is None or (state["ident"] != previous["ident"] and state["src"] != previous["src"]):
                return state
        sb.sleep(0.5)
    return None


def _next_viewer_photo(sb: SB, current: dict[str, Any]) -> dict[str, Any] | None:
    """
    Photo suivante. Le bouton « suivante » et la grande image arrivent parfois après l'adresse de la photo :
    ne jamais conclure « fin de l'album » au premier essai (vu : arrêt à 12 photos sur 31). Si l'adresse a déjà
    changé, on attend l'image sans recliquer, sinon une photo serait sautée.
    """
    for _ in range(VIEWER_NEXT_RETRIES):
        probe = _viewer_js(sb, click=False)
        moved = bool(probe.get("fbid")) and probe.get("fbid") != current.get("fbid")
        if not moved and not _viewer_js(sb, click=True).get("clicked"):
            sb.sleep(1.5)  # bouton pas encore affiché
            continue
        state = _viewer_state(sb, current)
        if state is not None:
            return state
    return None


class ViewerPhotos(list):
    """Photos lues ; `album_complete` = la visionneuse est revenue à une photo déjà vue : tout l'album a défilé."""

    album_complete = False


def collect_viewer_photos(sb: SB, url: str, limit: int) -> ViewerPhotos:
    """Parcourt la visionneuse depuis la 1re photo : "suivante" jusqu'à `limit` photos ou retour au début."""
    photos = ViewerPhotos()
    try:
        sb.goto(url)
    except Exception:
        return photos
    seen: set[str] = set()
    state = _viewer_state(sb, None)
    while state is not None and len(photos) < limit:
        if state["ident"] in seen:
            photos.album_complete = True  # plus fiable que « vignettes + N » (souvent décalé d'une photo)
            break
        seen.add(state["ident"])
        photos.append({"fbid": state.get("fbid", ""), "url": state["src"], "alt": fb._normalize_text(state.get("alt"))})
        if len(photos) >= limit:
            break
        state = _next_viewer_photo(sb, state)
    return photos


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_post_images(sb: SB, store: fb.PostStore, target: PageTarget, opts: argparse.Namespace) -> None:
    """
    Toutes les images de chaque publication : visionneuse (pleine résolution, au-delà
    des 5 vignettes du fil) sinon vignettes du fil ; téléchargées avec sha256.
    Attendu = vignettes + "+N" (_expected_photos). Publication incomplète : images_done laissé à faux et reprise
    au lancement suivant (VIEWER_MAX_ATTEMPTS essais) ; une publication déjà lue n'est jamais remplacée par moins
    de photos, et elle est ré-analysée si la reprise en trouve davantage.
    """
    folder = pages_dir() / "images" / target.file_slug
    viewer_failures = 0
    for post in store.posts():
        if fb.post_too_old(post, opts.max_age_days):
            continue  # publication trop ancienne déjà en cache : ni visionneuse ni téléchargement
        previous = post.get("images", [])
        expected = _expected_photos(post, opts.max_photos)
        viewer = bool(previous and post.get("photo_links") and not opts.no_viewer)
        if post.get("images_done"):
            # Terminée mais incomplète (ex. 12 photos sur 31, avant la correction de la visionneuse) : reprise
            incomplete = len(previous) < expected and not post.get("album_complete")
            if not (viewer and incomplete and post.get("viewer_attempts", 0) < VIEWER_MAX_ATTEMPTS):
                continue
            if post.get("viewer_attempts"):
                print(f"  [REPRISE] {post['post_id']} : {len(previous)}/{expected} photos, nouvel essai de la visionneuse")
            else:  # groupe : vignettes du fil seulement, jamais passé par la visionneuse
                print(f"  [VISIONNEUSE] {post['post_id']} : {len(previous)}/{expected} photos, récupération des autres")
        # Déjà lue (OCR fait ou fichiers supprimés) : ne jamais remplacer par moins bien
        processed = any("ocr_text" in image or image.get("file_deleted") for image in previous)
        feed_images = previous
        photos: list[dict[str, str]] = []
        if viewer:
            photos = collect_viewer_photos(sb, post["photo_links"][0], expected)
            if not photos:
                if fb._is_auth_wall(fb._page_state(sb)):
                    # Sans ce contrôle : 12 s d'attente par publication puis vignettes basse résolution gravées "terminé"
                    print("  [ERREUR] Facebook demande une reconnexion : récupération des images interrompue (relancez avec --login)")
                    store.save()
                    return  # essai non compté : la visionneuse n'a pas pu s'ouvrir
            post["viewer_attempts"] = post.get("viewer_attempts", 0) + 1
            if not photos:
                viewer_failures += 1
                if viewer_failures == 3:
                    print("  [WARN] Visionneuse : aucune photo lue sur 3 publications d'affilée (Facebook a changé ?) - vignettes du fil utilisées")
            else:
                viewer_failures = 0
        if getattr(photos, "album_complete", False):
            post["album_complete"] = True  # mémorisé : jamais reprise ensuite, même si « vignettes + N » annonçait plus
        retry = viewer and len(photos) < expected and not post.get("album_complete") and post["viewer_attempts"] < VIEWER_MAX_ATTEMPTS
        if processed and len(photos) <= len(previous):
            # Rien de mieux que la dernière fois : images et analyse gardées telles quelles
            post["images_done"] = not retry
            store.add(post["post_id"], post)
            store.save()
            if retry:
                print(f"  [INCOMPLET] {post['post_id']} : {len(photos)}/{expected} photos lues, réessai au prochain lancement "
                      f"({post['viewer_attempts']}/{VIEWER_MAX_ATTEMPTS})")
            continue
        if photos and len(photos) >= len(feed_images):
            records = [{"url": p["url"], "alt": p["alt"], "fbid": p["fbid"], "source": "viewer"} for p in photos]
        else:
            records = [{**image, "source": "feed"} for image in feed_images]

        complete = True
        for index, image in enumerate(records, 1):
            # _download_image réutilise un fichier existant : nom par fbid (stable d'un essai à l'autre, jamais la photo
            # d'une autre position) ; préfixe distinct pour les vignettes du fil, jamais prises pour une pleine résolution
            photo_key = image.get("fbid") or str(index)
            stem = f"{post['post_id']}_{photo_key}" if image["source"] == "viewer" else f"{post['post_id']}_feed_{index}"
            try:
                path = fb._download_image(image["url"], folder / stem)
            except fb.ImageDownloadError as exc:
                image["download_error"] = str(exc)
                complete = complete and exc.permanent
                continue
            image["file"] = path.relative_to(fb.OUTPUT_DIR).as_posix()
            image["sha256"] = _sha256(path)
        post["images"] = records
        post["images_done"] = complete and not retry
        if processed and ("analysis" in post or post.get("analysis_skipped")):
            # Plus de photos qu'à la précédente analyse : nouvelles affiches = nouvelles offres, publication ré-analysée
            # (OCR refait, texte seul envoyé à l'IA ; job_id des offres déjà connues inchangés, registre par contenu)
            for key in ("analysis", "analysis_skipped", "analysis_skipped_by", "analysis_error", "analysis_error_permanent"):
                post.pop(key, None)
            print(f"  [COMPLÉTÉE] {post['post_id']} : {len(previous)} -> {len(records)} photos, publication ré-analysée")
        store.add(post["post_id"], post)
        if records:
            print(f"  [images] {post['post_id']} : {sum(1 for r in records if r.get('file'))}/{len(records)} ({records[0]['source']})"
                  + (f" - incomplet ({expected} attendues), réessai au prochain lancement" if retry else ""))
        store.save()


def _expected_photos(post: dict[str, Any], max_photos: int) -> int:
    """Vignettes du fil + « +N » (photos cachées), plafonné : une photo seule d'un album ne fait pas parcourir tout l'album."""
    thumbnails = max(len(post.get("photo_links", [])), 1)
    return min(max_photos, thumbnails + int(post.get("more_images") or 0))


def ocr_page_posts(store: fb.PostStore, max_age_days: int = 0) -> None:
    pending = [
        p for p in store.posts()
        if any(fb.image_on_disk(i) and "ocr_text" not in i for i in p.get("images", [])) and not fb.post_too_old(p, max_age_days)
    ]
    if pending:
        print("  Chargement du moteur OCR (RapidOCR / onnxruntime)...", flush=True)
    engine = fb._get_ocr_engine() if pending else None
    if engine is None:
        return
    total = sum(1 for p in pending for i in p["images"] if fb.image_on_disk(i) and "ocr_text" not in i)
    print(f"  OCR des images : {total} image(s) dans {len(pending)} publication(s) (~0,5 à 2 s par image)...")
    done = 0
    for index, post in enumerate(pending, 1):
        count = sum(1 for i in post["images"] if fb.image_on_disk(i) and "ocr_text" not in i)
        start = time.monotonic()
        fb.ocr_post_images(engine, post)
        done += count
        store.add(post["post_id"], post)
        store.save()  # progression conservée sur Ctrl+C
        print(f"    [{index}/{len(pending)}] {count} image(s) en {time.monotonic() - start:.0f}s - total {done}/{total}")


def scrape_page(sb: SB, target: PageTarget, opts: argparse.Namespace, country: CountryProfile) -> fb.GroupStats:
    stats = fb.GroupStats()
    registry_path = pages_dir() / f"pages_{country.code}.json"
    pages = _read_json(registry_path)
    info = fetch_page_info(sb, target)
    pages[target.page_id] = {**pages.get(target.page_id, {}), **{k: v for k, v in info.items() if v}, "country": country.code}
    fb._write_json_atomic(registry_path, pages)
    print(f"Page : {info['name'] or target.page_id} ({len(info['about_text'])} caractères dans À propos)")

    store = fb.PostStore(target.posts_file)
    name = open_page_feed(sb, target)
    if name is not None:
        scroll_opts = fb.ScrapeOptions(
            sort="default", max_posts=opts.max_posts, stop_after_known=opts.stop_after_known, max_idle=opts.max_idle,
            scroll_delay=opts.scroll_delay, dump_html=opts.dump_html, download_images=False, ocr=False,
            any_root=True, exact_dates=not opts.no_exact_dates, max_age_days=opts.max_age_days,
        )
        try:
            fb._scroll_feed(sb, target, info["name"] or name, store, scroll_opts, stats)
        finally:
            store.save()
    fetch_post_images(sb, store, target, opts)
    if not opts.no_ocr:
        ocr_page_posts(store, opts.max_age_days)
    return stats


# ---------------------------------------------------------------------------
# Analyse IA
# ---------------------------------------------------------------------------


OCR_AT_RE = re.compile(r"\s*(?:@|\(at\)|\[at\])\s*", re.I)
OCR_DOT_RE = re.compile(r"(?<=[a-z0-9])\s*\.\s*(?=(?:com|mg|fr|org|net|info|biz|co|io)\b)", re.I)


def ocr_emails(text: str) -> list[str]:
    """Emails d'un texte OCR, tolérant les espaces que RapidOCR insère autour de « @ » et du point ("rh @ boa .mg")."""
    return fb.extract_emails(OCR_DOT_RE.sub(".", OCR_AT_RE.sub("@", fb._contact_text(text))))


def select_images_for_ai(post: dict[str, Any], send_all_images: bool = False, email_filter: bool = False) -> list[dict[str, Any]]:
    """
    Mode d'envoi de chaque image téléchargée (option coût "mixte", ~29 000 tokens par image jointe en haute
    résolution avec gpt-4o-mini) ; noté dans images[i]["ai_mode"] (+ "ai_reason") :
    - "ocr_text" : l'OCR a lu un contact (email, téléphone, site) ou >= AI_MIN_TEXT_CHARS caractères -> seul ce
      texte part (affiches d'offres : lues de façon fiable par RapidOCR) ;
    - "image"    : l'OCR a lu un peu de texte (logo, nom stylisé) ou l'OCR n'a pas tourné -> image jointe ;
    - "skipped"  : aucun texte lu (photo, décor) -> rien n'est envoyé ; si RIEN d'autre ne part pour la
      publication, la 1re image est jointe quand même (logo / nom de l'entreprise).
    email_filter : si le texte de la publication ne contient pas d'email, une image dont l'OCR ne lit aucun
      email est écartée (et plus de 1re image jointe par défaut) — les offres sans email ne sont pas récupérées.
    send_all_images : toutes les images jointes (ancien comportement, coûteux ; ignore email_filter).
    Renvoie [{"number", "path" (None = texte OCR seul), "ocr"}] pour AiExtractor.analyze_post.
    """
    items: list[dict[str, Any]] = []
    downloaded = [(number, image) for number, image in enumerate(post.get("images", []), 1) if image.get("file")]
    filter_images = email_filter and not send_all_images and not fb.extract_emails(post.get("text", ""))
    for number, image in downloaded:
        text = image.get("ocr_text", "")
        chars = sum(char.isalnum() for char in text)
        has_contact = bool(fb.extract_emails(text) or fb.extract_phones(text) or WEBSITE_RE.search(text))
        if send_all_images or "ocr_text" not in image:
            mode, reason = "image", "toutes les images jointes" if send_all_images else "OCR non disponible"
        elif filter_images and not ocr_emails(text):
            mode, reason = "skipped", "aucun email lu par l'OCR"
        elif has_contact or chars >= AI_MIN_TEXT_CHARS:
            mode, reason = "ocr_text", f"texte lu par l'OCR ({chars} caractères{', contact' if has_contact else ''})"
        elif chars:
            mode, reason = "image", f"peu de texte lu ({chars} caractères) : logo ou visuel"
        else:
            mode, reason = "skipped", "aucun texte lu (photo, décor)"
        if mode == "image" and not (fb.OUTPUT_DIR / image["file"]).exists():
            mode, reason = ("ocr_text", "fichier image absent : texte OCR seul") if text else ("skipped", "fichier image absent")
        image["ai_mode"], image["ai_reason"] = mode, reason
        image.pop("ai_sent", None)
        image.pop("ai_skip_reason", None)
        if mode != "skipped":
            items.append({"number": number, "path": fb.OUTPUT_DIR / image["file"] if mode == "image" else None, "ocr": text})
    existing = [(number, image) for number, image in downloaded if (fb.OUTPUT_DIR / image["file"]).exists()]
    if existing and not items and not filter_images:
        number, image = existing[0]
        image["ai_mode"], image["ai_reason"] = "image", "aucune image lisible : 1re image jointe quand même"
        items.append({"number": number, "path": fb.OUTPUT_DIR / image["file"], "ocr": image.get("ocr_text", "")})
    return items


def country_page_ids(country: CountryProfile) -> list[str]:
    pages = _read_json(pages_dir() / f"pages_{country.code}.json")
    return [page_id for page_id, page in pages.items() if page.get("country") == country.code]


def source_target(page_id: str, entry: dict[str, Any]) -> PageTarget:
    """Cible d'une entrée du registre `pages_<pays>.json` : page Facebook, ou groupe scrapé par scraper.py."""
    return PageTarget(page_id, entry.get("page_url", ""), "", entry.get("kind", "page"), entry.get("posts_file", ""))


def register_group(target: fb.GroupTarget, country: CountryProfile) -> str:
    """
    Inscrit un groupe déjà scrapé (scraper.py) dans `pages_<pays>.json` pour que l'analyse IA, la
    consolidation et l'envoi au backend le traitent comme une page. Renvoie l'id de source.
    """
    page_id = f"{GROUP_ID_PREFIX}{target.group_id}"
    posts = fb.PostStore(target.output_file).posts()
    name = next((post.get("group_name") for post in reversed(posts) if post.get("group_name")), "")
    registry_path = pages_dir() / f"pages_{country.code}.json"
    registry = _read_json(registry_path)
    entry = {
        **registry.get(page_id, {}),
        "page_id": page_id, "kind": "group", "page_url": target.group_url, "country": country.code,
        "posts_file": str(target.output_file.relative_to(fb.OUTPUT_DIR)),
    }
    if name:
        entry["name"] = name
    registry[page_id] = entry
    fb._write_json_atomic(registry_path, registry)
    return page_id


def _needs_analysis(
    post: dict[str, Any], extractor: AiExtractor, reanalyze: bool, active_filters: set[str] = frozenset()
) -> bool:
    """
    Jamais analysée ; ou, avec --reanalyze, analysée avec un autre modèle / une ancienne version du prompt.
    Publication écartée par un filtre (`analysis_skipped_by`) : reprise dès que ce filtre est désactivé.
    """
    meta = post.get("analysis", {}).get("meta")
    if meta is None:
        if post.get("analysis_skipped_by", "email_filter") in active_filters and post.get("analysis_skipped"):
            return False
        return not post.get("analysis_error_permanent") or reanalyze  # erreur définitive : pas retentée à chaque run
    return reanalyze and (meta.get("prompt_version") != PROMPT_VERSION or meta.get("model") != extractor.model)


def analyze_posts(
    country: CountryProfile, extractor: AiExtractor, reanalyze: bool, ocr: bool = True, send_all_images: bool = False,
    max_tokens: int = 0, max_age_days: int = 0, email_filter: bool = False, job_filter: bool = False,
) -> None:
    """
    N'appelle l'IA que pour ce qui manque : publications jamais analysées (doublons de pfbid exclus), ou
    analysées avec un autre modèle / prompt si --reanalyze ; le cache IA reste utilisé dans tous les cas.
    max_tokens : arrêt quand les nouveaux appels de ce lancement ont consommé ce budget (0 = sans limite).
    email_filter : images sans email lu par l'OCR écartées ; publication sans aucun email (texte ni images)
    jamais envoyée, marquée analysis_skipped (voir select_images_for_ai).
    job_filter : publication de GROUPE sans mot-clé d'offre (scraper.analyze_job) jamais envoyée — un groupe
    contient beaucoup de publications hors sujet ; sans effet sur les pages.
    """
    pages = _read_json(pages_dir() / f"pages_{country.code}.json")
    tokens_used = 0
    for page_id in country_page_ids(country):
        target = source_target(page_id, pages[page_id])
        is_group = target.kind == "group"
        store = fb.PostStore(target.posts_file)
        if ocr:
            ocr_page_posts(store, max_age_days)  # rattrape un OCR interrompu (Ctrl+C, --skip-scrape) avant l'IA
        unique, duplicates = canonical_posts(store.posts())
        active_filters = set() if send_all_images else (
            ({"email_filter"} if email_filter else set()) | ({"job_filter"} if job_filter and is_group else set())
        )
        candidates = [p for p in unique if _needs_analysis(p, extractor, reanalyze, active_filters)]
        todo = [p for p in candidates if not fb.post_too_old(p, max_age_days)]
        if len(todo) < len(candidates):
            print(f"\n[ANCIENNES] {len(candidates) - len(todo)} publication(s) de plus de {max_age_days} jours non analysée(s)")
        if duplicates:
            print(f"\n[DOUBLON] {len(duplicates)} publication(s) déjà connue(s) sous un autre pfbid : jamais envoyée(s) à l'IA")
        if not todo:
            continue
        print(f"\nAnalyse IA - {pages[page_id].get('name') or page_id} : {len(todo)} publication(s)")
        waiting_ocr = without_email = not_offer = 0
        for post in todo:
            if max_tokens and tokens_used >= max_tokens:
                print(f"  [BUDGET] {tokens_used} tokens consommés (limite --max-ai-tokens {max_tokens}) : analyse arrêtée, relancez pour continuer")
                store.save()
                return
            if is_group and not post.get("images_done"):  # groupes scrapés avant l'ajout du champ (scraper.finalize_posts)
                post["images_done"] = all(i.get("file") or i.get("download_error") for i in post.get("images", []))
            if not post.get("images_done"):
                print(f"  [ATTENTE] {post['post_id'][:30]}… : images incomplètes, analyse reportée au prochain scraping")
                continue
            if not send_all_images and any(fb.image_on_disk(i) and "ocr_text" not in i for i in post.get("images", [])):
                waiting_ocr += 1  # sans OCR, chaque image partirait en haute résolution (~29 000 tokens chacune)
                continue
            # analyze_job recalculé : les photos complètes de la visionneuse ont pu remplacer les 5 vignettes du fil
            if is_group and job_filter and not send_all_images and not fb.analyze_job(post)["is_offer"]:
                post["analysis_skipped"] = "publication de groupe sans mot-clé d'offre d'emploi"
                post["analysis_skipped_by"] = "job_filter"
                store.add(post["post_id"], post)
                not_offer += 1
                continue
            images = select_images_for_ai(post, send_all_images, email_filter)
            if email_filter and not send_all_images and not images and not fb.extract_emails(post.get("text", "")):
                post["analysis_skipped"] = "aucun email dans le texte ni dans les images (OCR)"
                post["analysis_skipped_by"] = "email_filter"
                store.add(post["post_id"], post)
                without_email += 1
                continue
            modes = {mode: sum(1 for i in post.get("images", []) if i.get("ai_mode") == mode) for mode in ("image", "ocr_text", "skipped")}
            try:
                analysis = extractor.analyze_post(post, pages[page_id], images, country.name)
            except AiError as exc:
                post["analysis_error"] = str(exc)
                post["analysis_error_permanent"] = exc.permanent
                store.add(post["post_id"], post)
                store.save()
                print(f"  [ERREUR IA] {post['post_id'][:30]}… : {exc}")
                continue
            if not analysis["meta"].get("cached"):
                tokens_used += sum(int(v) for v in analysis["meta"].get("usage", {}).values())
            post["analysis"] = analysis
            post.pop("analysis_skipped", None)
            post.pop("analysis_skipped_by", None)
            post.pop("analysis_error", None)
            post.pop("analysis_error_permanent", None)
            store.add(post["post_id"], post)
            store.save()
            result = analysis["result"]
            names = ", ".join(c["name"] for c in result["companies"] if c["name"]) or "-"
            print(
                f"  + {post['post_id'][:30]}… [{result['post_kind']}] images jointes {modes['image']}, texte OCR seul "
                f"{modes['ocr_text']}, écartées {modes['skipped']} | tokens {analysis['meta'].get('usage', {}).get('prompt_tokens', '?')} -> "
                f"entreprises : {names} | offres : {len(result['job_offers'])}"
            )
        if not_offer:
            print(f"  [HORS SUJET] {not_offer} publication(s) de groupe sans mot-clé d'offre : pas envoyée(s) à l'IA, "
                  f"0 token (--no-job-filter pour les analyser quand même)")
        if without_email:
            print(f"  [SANS EMAIL] {without_email} publication(s) sans aucun email (texte ni OCR des images) : pas envoyée(s) "
                  f"à l'IA, 0 token (--no-email-filter pour les analyser quand même)")
        if waiting_ocr:
            print(f"  [ATTENTE] {waiting_ocr} publication(s) sans OCR (pip install rapidocr onnxruntime, ou relancez sans "
                  f"--no-ocr) : non envoyée(s) pour ne pas joindre chaque image en haute résolution (--send-all-images pour forcer)")
        store.save()
    if tokens_used:
        print(f"\nIA : {tokens_used} tokens consommés par les nouveaux appels de ce lancement")


# ---------------------------------------------------------------------------
# Consolidation -> JSON au format backend
# ---------------------------------------------------------------------------


def _offer_company_id(offer: dict[str, Any], refs: list[tuple[dict[str, Any], str]]) -> str:
    """Entreprise de l'offre : même nom dans la publication, sinon l'unique entreprise, sinon la page elle-même."""
    wanted = normalize_name(offer.get("company_name", ""))
    if wanted:
        for company, company_id in refs:
            name = normalize_name(company.get("name", ""))
            if name and names_similar(wanted, name):
                return company_id
    if len(refs) == 1:
        return refs[0][1]
    return next((company_id for company, company_id in refs if company.get("is_page_owner")), "")


JOB_REPOST_WINDOW_DAYS = 60  # au-delà : nouvelle campagne de recrutement, offre distincte
DMY_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b")


def _title_key(title: str) -> str:
    """'Assistant(e) comptable H/F' -> 'assistant comptable'."""
    title = re.sub(r"\(\s*e\s*\)|\b[hf]\s*/\s*[hf]\b", " ", title, flags=re.I)
    return " ".join(re.findall(r"[a-z0-9]+", fold(title)))


def _deadline_key(deadline: str) -> str:
    match = DMY_RE.search(deadline)
    if match:
        day, month, year = (int(part) for part in match.groups())
        return f"{year + 2000 if year < 100 else year:04d}-{month:02d}-{day:02d}"
    return " ".join(re.findall(r"[a-z0-9]+", fold(deadline)))


def _occurrence_day(occurrence: dict[str, Any]) -> date:
    return date.fromisoformat((occurrence["published"] or occurrence["post"]["scraped_at"])[:10])


def dedupe_offers(occurrences: list[dict[str, Any]], registry_path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    """
    Regroupe les republications d'une même offre : même entreprise (company_id, sinon nom
    normalisé), même intitulé normalisé, même date limite, publications à moins de
    JOB_REPOST_WINDOW_DAYS jours d'intervalle. Sans intitulé ou sans entreprise : jamais
    regroupée. job_id stable : job_registry_<pays>.json retient le job_id de CHAQUE
    publication d'offre ; une campagne reprend celui déjà attribué à l'une de ses
    publications (même si une publication plus ancienne est découverte ensuite), sinon
    l'id de sa première publication.
    Renvoie [(job_id, occurrences triées de la plus ancienne à la plus récente)].
    """
    registry = _read_json(registry_path)
    known: dict[str, str] = registry.setdefault("occurrences", {})
    groups: dict[str, list[dict[str, Any]]] = {}
    repeats: dict[str, int] = {}
    for occurrence in occurrences:
        offer = occurrence["offer"]
        title = _title_key(offer.get("title", ""))
        name = normalize_name(offer.get("company_name", ""))
        owner = occurrence["company_id"] or (f"name:{name}" if name else "")
        key = f"{owner}|{title}|{_deadline_key(offer.get('deadline', ''))}" if title and owner else f"post:{occurrence['local_id']}"
        # Clé de registre par CONTENU (publication + offre) et non par rang : une ré-analyse peut renvoyer
        # les offres dans un autre ordre, "fb-post-X-3" désignerait alors une autre offre.
        # Même clé plusieurs fois dans une publication (offre répétée par l'IA, même poste dans deux villes) :
        # "#2", "#3" dans l'ordre des offres, sinon elles se partagent une entrée et échangent leur job_id à chaque lancement
        registry_key = f"{occurrence['post']['post_id']}|{key}"
        repeats[registry_key] = repeats.get(registry_key, 0) + 1
        occurrence["registry_key"] = registry_key if repeats[registry_key] == 1 else f"{registry_key}#{repeats[registry_key]}"
        groups.setdefault(key, []).append(occurrence)

    all_campaigns: list[list[dict[str, Any]]] = []
    for key, group in groups.items():
        group.sort(key=lambda occ: (_occurrence_day(occ), occ["post"]["scraped_at"], occ["local_id"]))
        campaigns: list[list[dict[str, Any]]] = []
        for occurrence in group:
            # Une campagne = republications dans des publications DIFFÉRENTES, à <= JOB_REPOST_WINDOW_DAYS jours :
            # deux offres identiques dans une même publication (ex. 2 postes, 2 villes) restent distinctes
            target = next((
                campaign for campaign in campaigns
                if (_occurrence_day(occurrence) - _occurrence_day(campaign[-1])).days <= JOB_REPOST_WINDOW_DAYS
                and all(occ["post"]["post_id"] != occurrence["post"]["post_id"] for occ in campaign)
            ), None)
            if target is None:
                campaigns.append([occurrence])
            else:
                target.append(occurrence)
        all_campaigns += campaigns

    # 1re passe : les campagnes déjà connues reprennent leur job_id ; 2e passe : ids neufs, jamais déjà pris
    ids: dict[int, str] = {}
    used: set[str] = set()
    for position, campaign in enumerate(all_campaigns):
        previous = [known[occ["registry_key"]] for occ in campaign if occ["registry_key"] in known]
        previous = [job_id for job_id in previous if job_id not in used]
        if previous:
            ids[position] = max(previous, key=previous.count)
            used.add(ids[position])
    for position, campaign in enumerate(all_campaigns):
        if position in ids:
            continue
        job_id = next((occ["local_id"] for occ in campaign if occ["local_id"] not in used), None)
        suffix = 2
        while job_id is None or job_id in used:
            job_id, suffix = f"{campaign[0]['local_id']}-r{suffix}", suffix + 1
        ids[position] = job_id
        used.add(job_id)

    assigned: list[tuple[str, list[dict[str, Any]]]] = []
    for position, campaign in enumerate(all_campaigns):
        for occurrence in campaign:
            known[occurrence["registry_key"]] = ids[position]
        assigned.append((ids[position], campaign))
    fb._write_json_atomic(registry_path, registry)
    return assigned


def _merged_offer(campaign: list[dict[str, Any]]) -> dict[str, Any]:
    """Version la plus complète de l'offre, avec les contacts de toutes ses republications."""
    def completeness(offer: dict[str, Any]) -> int:
        lists = sum(len(offer.get(field, [])) for field in ("tasks", "qualifications", "skills"))
        fields = sum(1 for field in ("location", "contract_type", "salary", "deadline", "how_to_apply") if offer.get(field))
        return len(offer.get("description", "")) + 50 * lists + 20 * fields

    offers = [occurrence["offer"] for occurrence in campaign]
    merged = dict(max(offers, key=completeness))
    for field in ("emails", "phone_numbers"):
        merged[field] = list(dict.fromkeys(item for offer in offers for item in offer.get(field, [])))
    # Une seule republication jugée suspecte suffit (la version "la plus complète" n'est pas forcément celle-là)
    if any(offer.get("suspicious") for offer in offers):
        merged["suspicious"] = True
    return merged


def job_document(
    job_id: str, offer: dict[str, Any], post: dict[str, Any], page: dict[str, Any], company: dict[str, Any],
    company_id: str, published: str | None,
) -> dict[str, Any]:
    """Uniquement les champs du modèle Job (backend/src/models/job.model.ts)."""
    job_url = post.get("post_url") or page.get("page_url", "")
    criteria = {
        "Lieu": offer.get("location", ""),
        "Secteur": offer.get("sector", ""),
        "Type de contrat": offer.get("contract_type", ""),
        "Salaire": offer.get("salary", ""),
        "Date limite": offer.get("deadline", ""),
        "Comment postuler": offer.get("how_to_apply", ""),
        "Emails": ", ".join(offer.get("emails", [])),
        "Téléphones": ", ".join(offer.get("phone_numbers", [])),
        "Publié le": published or "",
        "Source": f"Facebook - {page.get('name') or page.get('page_url', '')}",
    }
    tasks = offer.get("tasks", [])
    return {
        "job_id": job_id,
        "title": offer.get("title", ""),
        "job_url": job_url,
        "company_name": company.get("name") or offer.get("company_name", ""),
        "company_url": company.get("company_url", ""),
        "company_id": company_id,
        "detail": {
            "job_url": job_url,
            "headline": "",
            "description": offer.get("description") or post.get("text", ""),
            "qualifications": offer.get("qualifications", []),
            "criteria": {key: value for key, value in criteria.items() if value},
            "skills": offer.get("skills", []),
            "sections": [{
                "heading": "Missions",
                "description": "\n".join(f"• {task}" for task in tasks),
                "qualifications": [], "criteria": {}, "skills": [],
            }] if tasks else [],
        },
        "company_profile": company,
    }


def release_images(country: CountryProfile, max_age_days: int = 0) -> None:
    """
    Supprime du disque les images dont le pipeline n'a plus besoin, pour ne pas saturer le disque : publication
    analysée, écartée par un filtre, doublon de pfbid, ou trop ancienne pour être analysée. Tout ce qui en a été
    tiré reste dans le JSON (url, fbid, sha256, texte OCR, chemin + file_deleted) : rien n'est retéléchargé
    (images_done / champ file conservés), le cache IA et la consolidation n'utilisent pas les fichiers.
    Une publication en attente (téléchargement, OCR, analyse, erreur IA à retenter) garde ses images.
    Prix : --reanalyze ne peut plus joindre ces images, il repart du texte OCR.
    """
    pages = _read_json(pages_dir() / f"pages_{country.code}.json")
    files = size = 0
    for page_id in country_page_ids(country):
        store = fb.PostStore(source_target(page_id, pages[page_id]).posts_file)
        _, duplicates = canonical_posts(store.posts())
        changed = False
        for post in store.posts():
            finished = (
                "analysis" in post or post.get("analysis_skipped") or post["post_id"] in duplicates
                or fb.post_too_old(post, max_age_days)
            )
            if not finished:
                continue
            for image in post.get("images", []):
                if not fb.image_on_disk(image):
                    continue
                path = fb.OUTPUT_DIR / image["file"]
                try:
                    file_size = path.stat().st_size
                    path.unlink()
                except FileNotFoundError:
                    file_size = 0  # déjà supprimé à la main
                except OSError as exc:  # fichier ouvert ailleurs (visionneuse Windows...) : réessayé au prochain lancement
                    print(f"  [WARN] image non supprimée {image['file']} : {exc}")
                    continue
                image["file_deleted"] = True
                files += 1 if file_size else 0
                size += file_size
                changed = True
                with suppress(OSError):
                    path.parent.rmdir()  # dossier de la page vide : retiré aussi
            if changed:
                store.add(post["post_id"], post)
        if changed:
            store.save()
    if files:
        print(f"\nImages : {files} fichier(s) supprimé(s) du disque ({f'{size / 1_048_576:.1f} Mo' if size >= 1_048_576 else f'{size // 1024} Ko'} libérés), analyse terminée "
              f"(--keep-images pour les garder)")


def _without_page_owner(post: dict[str, Any]) -> dict[str, Any]:
    """Copie de la publication où aucune entreprise n'est « la page qui publie » (groupes : le groupe n'emploie pas)."""
    result = post["analysis"]["result"]
    companies = [{**company, "is_page_owner": False} for company in result["companies"]]
    return {**post, "analysis": {**post["analysis"], "result": {**result, "companies": companies}}}


EXCLUDED_COMPANIES_FILE = fb.SCRIPT_DIR / "excluded_companies.txt"


def _name_words(text: str) -> list[str]:
    """Mots pliés, pluriel simple retiré des mots longs : « Ministères » -> ["ministere"], « EUROP'ALU » -> ["europ", "alu"]."""
    return [word[:-1] if len(word) >= 5 and word.endswith("s") else word for word in re.findall(r"[a-z0-9]+", fold(text))]


def load_excluded_companies(path: Path) -> list[str]:
    """Liste d'exclusion (une entreprise par ligne, # = commentaire) sous forme compacte : « NP AKADIN » -> "npakadin"."""
    if not path.exists():
        return []
    entries = ("".join(_name_words(line)) for line in path.read_text(encoding="utf-8-sig").splitlines() if not line.strip().startswith("#"))
    return list(dict.fromkeys(entry for entry in entries if entry))


def excluded_company_match(blocklist: list[str], names: list[str], domains: list[str] = ()) -> str:
    """
    Entrée de la liste d'exclusion qui correspond, sinon "" : suite de mots ENTIERS d'un nom (collés ou non :
    « Europalu », « Europ Alu ») — jamais un morceau de mot (« YAS » ne touche pas « Yasmine ») — ou libellé
    d'un domaine d'email / de site (« europ-alu » dans recrutement@europ-alu.com).
    """
    for target in blocklist:
        for name in names:
            words = _name_words(name)
            for start in range(len(words)):
                joined = ""
                for word in words[start:]:
                    joined += word
                    if joined == target:
                        return target
                    if len(joined) >= len(target):
                        break
        if any("".join(_name_words(label)) == target for domain in domains for label in domain.split(".")[:-1]):
            return target
    return ""


NON_EMPLOYER_TYPES = tuple(t for t in COMPANY_TYPES if t != "entreprise")  # ("portail_offres", "ong_ou_institution")


def ai_company_type(cluster: list[Any]) -> str:
    """
    Classification IA (company_type) la plus restrictive vue dans le cluster (une seule publication qui identifie
    un portail/une ONG suffit, même si une autre l'a laissé par défaut à "entreprise") ; "entreprise" sinon.
    """
    types = {c.data.get("company_type", "entreprise") for c in cluster}
    return next((kind for kind in NON_EMPLOYER_TYPES if kind in types), "entreprise")


def _cluster_domains(cluster: list[Any]) -> list[str]:
    """Domaines propres à une entreprise (site, emails), hors messageries et réseaux (gmail.com, facebook.com...)."""
    domains = [website_domain(candidate.data.get("website", "")) for candidate in cluster]
    domains += [website_domain(email.split("@")[-1]) for candidate in cluster for email in candidate.data.get("emails", []) if "@" in email]
    return [domain for domain in domains if domain]


DEFAULT_OFFER_TYPES = ("emploi", "stage")  # le reste (concours, formation, bourse, appel d'offres...) n'est pas publié
# Filet de sécurité sur le DÉBUT de l'intitulé (plié) : sans ambiguïté, appliqué même si l'IA a dit "emploi", et seule
# source pour les analyses d'avant offer_type (prompt < v5). Jamais un mot isolé : "Formateur", "Chef d'atelier",
# "Gestionnaire fournisseurs" sont de vrais emplois (vérifié sur les vraies offres).
NON_JOB_TITLE_RES = (
    ("concours", re.compile(r"^\W*concours\b|\bconcours (?:d.entree|de recrutement|administratif|direct|professionnel)")),
    ("formation", re.compile(
        r"^\W*(?:formations?|session de formation|programme de formation|offre de formation|cours (?:de|d.|en)|"
        r"seminaire|webinaire|masterclass|bootcamp|certification)\b"
    )),
    ("bourse", re.compile(r"^\W*(?:bourses?|programme de bourses?|fellowship|scholarship)\b")),
    ("appel_offres", re.compile(
        r"^\W*(?:avis d.)?(?:appel d.offres?|appel a manifestation|manifestation d.interet|consultation ouverte|"
        r"demande de (?:cotation|prix|proposition)s?)\b"
    )),
)
STAGE_TITLE_RE = re.compile(r"^\W*(?:stages?|stagiaires?|offre de stage)\b")


def offer_type(offer: dict[str, Any]) -> str:
    """Nature d'une offre : règle sur l'intitulé si elle s'applique, sinon offer_type de l'IA, sinon emploi/stage."""
    title = fold(offer.get("title", ""))
    for kind, pattern in NON_JOB_TITLE_RES:
        if pattern.search(title):
            return kind
    if offer.get("offer_type") in OFFER_TYPES:
        return offer["offer_type"]
    return "stage" if STAGE_TITLE_RE.search(title) else "emploi"


def consolidate(
    country: CountryProfile, offer_types: tuple[str, ...] | list[str] = DEFAULT_OFFER_TYPES, require_contact: bool = False,
    excluded_companies: list[str] | None = None, exclude_relay_companies: bool = True, exclude_suspicious: bool = True,
) -> None:
    """
    require_contact : une offre dont l'entreprise n'a ni email ni téléphone (company_profile sans contact, ou pas
    d'entreprise du tout) n'est pas publiée — aucun moyen de contacter le recruteur (défaut CLI, --allow-no-contact).
    excluded_companies : liste d'exclusion nommée (load_excluded_companies) — ni la fiche entreprise ni ses offres
    ne sont publiées.
    exclude_relay_companies : entreprise classée par l'IA (company_type) comme portail d'offres (MADAJOB, Asako,
    agence qui relaie pour le compte de tiers...) ou ONG/organisation internationale/institution publique (Banque
    Mondiale, agences UN, ministères...) écartée comme excluded_companies, sans avoir à la lister nommément
    (défaut CLI, --allow-relay-companies pour les garder).
    exclude_suspicious : offre dont l'IA a détecté des signes d'arnaque (job_offers[].suspicious) jamais publiée
    (défaut CLI, --no-suspicious-filter pour les garder).
    """
    blocklist = excluded_companies or []
    out = pages_dir()
    pages = _read_json(out / f"pages_{country.code}.json")
    posts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    duplicate_posts: dict[str, str] = {}
    for page_id in country_page_ids(country):
        entry = pages[page_id]
        store = fb.PostStore(source_target(page_id, entry).posts_file)
        kept, duplicates = canonical_posts([post for post in store.posts() if "analysis" in post])  # doublons de pfbid exclus
        if entry.get("kind") == "group":
            kept = [_without_page_owner(post) for post in kept]  # un groupe n'est jamais l'employeur des offres relayées
        posts += [(post, entry) for post in kept]
        duplicate_posts.update(duplicates)
    if not posts:
        print("\nConsolidation : aucune publication analysée pour l'instant.")
        return

    candidates = []
    for post, page in posts:
        for index, company in enumerate(post["analysis"]["result"]["companies"]):
            if company.get("name") or company.get("emails") or company.get("phone_numbers") or company.get("website"):
                candidates.append(build_candidate(post, index, company, page, country))
    registry = CompanyRegistry(out / f"identity_registry_{country.code}.json")
    clusters, shared = registry.cluster(candidates)
    assigned = registry.assign_ids(clusters, shared)
    registry.save()

    company_by_ref = {candidate.ref: company_id for company_id, cluster in assigned for candidate in cluster}
    companies = {company_id: company_document(company_id, cluster, country, registry.generic_keys) for company_id, cluster in assigned}
    excluded_company_ids: dict[str, Any] = {}
    for company_id, cluster in assigned:
        # Tous les noms vus pour l'entreprise (variantes de l'IA regroupées) + domaines de ses emails et de son site
        matched = excluded_company_match(blocklist, [c.data.get("name", "") for c in cluster], _cluster_domains(cluster))
        ai_type = ai_company_type(cluster) if exclude_relay_companies else "entreprise"
        if matched or ai_type != "entreprise":
            excluded_company_ids[company_id] = {
                "name": companies.pop(company_id)["name"], "matched": matched,
                "reason": "liste_exclusion" if matched else ai_type,
            }
    jobs: dict[str, Any] = {}
    publications: dict[str, Any] = {}
    review_posts: dict[str, Any] = {}

    occurrences: list[dict[str, Any]] = []
    for post, page in posts:
        result = post["analysis"]["result"]
        refs = [
            (company, company_by_ref[f"{post['post_id']}#{index}"])
            for index, company in enumerate(result["companies"]) if f"{post['post_id']}#{index}" in company_by_ref
        ]
        published, precision = published_at(post.get("date_tooltip", ""), post.get("time_text", ""), post["scraped_at"])
        offers = result["job_offers"]
        local_ids = []
        for number, offer in enumerate(offers, 1):
            if not offer.get("title", "").strip():
                continue  # offre sans intitulé : inutilisable côté backend, visible dans ai_result de la revue
            local_id = f"fb-post-{post['post_id']}" + (f"-{number}" if len(offers) > 1 else "")
            occurrences.append({
                "local_id": local_id, "post": post, "page": page, "offer": offer,
                "company_id": _offer_company_id(offer, refs), "published": published,
            })
            local_ids.append(local_id)
        review_posts[post["post_id"]] = {
            "post_url": post.get("post_url", ""),
            "page_id": page["page_id"],
            "page_name": page.get("name", ""),
            "time_text": post.get("time_text", ""),
            "date_tooltip": post.get("date_tooltip", ""),
            "published_at": published,
            "published_at_precision": precision,
            "post_kind": result["post_kind"],
            "text": post.get("text", ""),
            "images": [
                {k: image.get(k) for k in ("file", "sha256", "source", "ai_mode", "ai_reason", "ocr_text", "download_error")
                 if image.get(k) not in (None, "")}
                for image in post.get("images", [])
            ],
            "company_ids": [company_id for _, company_id in refs],
            "job_ids": local_ids,  # remplacés par les job_id dédoublonnés ci-dessous
            "ai_result": result,
            "ai_meta": post["analysis"]["meta"],
            "notes": result.get("notes", ""),
        }

    # Même offre republiée dans plusieurs publications -> un seul job_id, première date de publication
    job_id_by_local: dict[str, str] = {}
    review_jobs: dict[str, Any] = {}
    excluded_offers: dict[str, Any] = {}
    for job_id, campaign in dedupe_offers(occurrences, out / f"job_registry_{country.code}.json"):
        first = campaign[0]
        company_id = next((occ["company_id"] for occ in campaign if occ["company_id"]), "")
        offer = _merged_offer(campaign)
        for occ in campaign:
            job_id_by_local[occ["local_id"]] = job_id
        occurrences_review = [{"post_id": occ["post"]["post_id"], "published_at": occ["published"]} for occ in campaign]
        kind = offer_type(offer)
        company = companies.get(company_id, {})
        offer_names = [occ["offer"].get("company_name", "") for occ in campaign]
        reason = (
            "entreprise_exclue" if company_id in excluded_company_ids or excluded_company_match(blocklist, offer_names)
            else "type" if kind not in offer_types
            else "offre_suspecte" if exclude_suspicious and offer.get("suspicious")
            else "sans_contact" if require_contact and not (company.get("emails") or company.get("phone_numbers"))
            else ""
        )
        if reason:
            # Id attribué quand même (registre stable) : le nettoyage du backend retire l'annonce si elle y est déjà ;
            # elle revient d'elle-même si son entreprise gagne un contact ou si le filtre change
            excluded_offers[job_id] = {
                "title": offer.get("title", ""), "company_name": offer.get("company_name", ""), "offer_type": kind,
                "reason": reason, "occurrences": occurrences_review,
            }
            continue
        jobs[job_id] = job_document(job_id, offer, first["post"], first["page"], company, company_id, first["published"])
        publications[job_id] = {"job_id": job_id, "published_at": first["published"]}
        review_jobs[job_id] = {"title": offer.get("title", ""), "offer_type": kind, "occurrences": occurrences_review}
    for review in review_posts.values():
        review["job_ids"] = list(dict.fromkeys(job_id_by_local[local_id] for local_id in review["job_ids"]))

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fb._write_json_atomic(out / f"companies_{country.code}.json", companies)
    fb._write_json_atomic(out / f"jobs_{country.code}.json", jobs)
    fb._write_json_atomic(out / f"job_publications_{country.code}.json", publications)
    fb._write_json_atomic(out / f"analysis_{country.code}.json", {
        "generated_at": generated_at,
        "country": {"code": country.code, "name": country.name, "iso3": country.iso3},
        "companies": {company_id: review_entry(company_id, cluster, shared) for company_id, cluster in assigned},
        "jobs": review_jobs,
        "posts": review_posts,
        "duplicate_posts": duplicate_posts,  # doublon de pfbid -> publication gardée (nettoyage du backend)
        "excluded_offers": excluded_offers,  # pas des offres d'emploi (--offer-types) : jamais publiées, retirées du backend
        "excluded_companies": excluded_company_ids,  # liste d'exclusion : fiche jamais publiée, retirée du backend
    })
    merged = sum(1 for _, cluster in assigned if len(cluster) > 1)
    reposted = len(occurrences) - len(jobs) - len(excluded_offers)
    by_type: dict[str, int] = {}
    for entry in excluded_offers.values():
        if entry["reason"] == "type":
            by_type[entry["offer_type"]] = by_type.get(entry["offer_type"], 0) + 1
    excluded_text = ", ".join(f"{count} {kind}" for kind, count in sorted(by_type.items()))
    without_contact = sum(1 for entry in excluded_offers.values() if entry["reason"] == "sans_contact")
    suspicious_offers = sum(1 for entry in excluded_offers.values() if entry["reason"] == "offre_suspecte")
    blocked_offers = sum(1 for entry in excluded_offers.values() if entry["reason"] == "entreprise_exclue")
    print(
        f"\nConsolidation {country.name} : {len(posts)} publication(s), {len(candidates)} mention(s) d'entreprise "
        f"-> {len(companies)} entreprise(s) ({merged} regroupée(s)), {len(jobs)} offre(s) "
        f"({reposted} republication(s) regroupée(s)), "
        f"{len(shared)} contact(s) partagé(s) ignoré(s)\n  -> {out}"
    )
    if excluded_company_ids or blocked_offers:
        named = sorted({e["name"] for e in excluded_company_ids.values() if e["reason"] == "liste_exclusion"})
        relay = sorted({e["name"] for e in excluded_company_ids.values() if e["reason"] == "portail_offres"})
        ngo = sorted({e["name"] for e in excluded_company_ids.values() if e["reason"] == "ong_ou_institution"})
        print(f"  liste d'exclusion / IA : {len(excluded_company_ids)} entreprise(s) et {blocked_offers} offre(s) écartée(s)")
        if named:
            print(f"    liste nommée : {', '.join(named)}")
        if relay:
            print(f"    portails/agences relais (IA) : {', '.join(relay)}")
        if ngo:
            print(f"    ONG/organisations internationales/institutions (IA) : {', '.join(ngo)}")
    if by_type:
        print(f"  {sum(by_type.values())} annonce(s) écartée(s), hors types gardés ({','.join(offer_types)}) : {excluded_text}")
    if without_contact:
        print(f"  {without_contact} offre(s) écartée(s) : entreprise sans email ni téléphone (--allow-no-contact pour les garder)")
    if suspicious_offers:
        print(f"  {suspicious_offers} offre(s) écartée(s) : signes d'arnaque détectés par l'IA (--no-suspicious-filter pour les garder)")
    if excluded_offers:
        print(f"  détail -> analysis_{country.code}.json, clé excluded_offers")


REF_CODE_RE = re.compile(r"[(\[]\s*r[ée]f[^)\]]*[)\]]|\br[ée]f(?:[ée]rence)?\s*[.:°#]\s*[\w/-]+", re.I)
INCLUSIVE_SUFFIX_RE = re.compile(r"\(\s*(?:e|es|s|ère|ere|ne|se|le|trice|euse)\s*\)|\.(?:ve|ère|ere|trice|euse)\b", re.I)
JOB_ID_SUFFIX_RE = re.compile(r"(?:-\d+)?(?:-r\d+)?")


def _offer_signature(title: str) -> str:
    """Intitulé comparable d'une analyse IA à l'autre : 'Conseiller(ère) de Vente (Réf CV)' -> 'conseiller de vente'."""
    return _title_key(INCLUSIVE_SUFFIX_RE.sub("", REF_CODE_RE.sub(" ", title)))


def stale_replacement_finder(country: CountryProfile) -> ReplacementFinder:
    """
    Nettoyage du backend (backend_push.clean_stale) : pour un document envoyé autrefois puis disparu des JSON,
    id du document actuel qui le remplace, sinon None (document gardé en base).
    - entreprise : fusionnée dans une autre (alias du registre d'identité) ;
    - offre : même intitulé (hors « (Réf …) », « (ère) », « H/F ») ET même entreprise (alias résolus), ou même
      publication d'origine (doublon de pfbid ramené à la publication gardée, republication regroupée).
    Une offre que la dernière analyse IA n'a pas retrouvée n'a pas d'équivalent : jamais supprimée.
    """
    out = pages_dir()
    companies = _read_json(out / f"companies_{country.code}.json")
    jobs = _read_json(out / f"jobs_{country.code}.json")
    analysis = _read_json(out / f"analysis_{country.code}.json")
    aliases: dict[str, str] = _read_json(out / f"identity_registry_{country.code}.json").get("aliases", {})
    duplicates: dict[str, str] = analysis.get("duplicate_posts", {})
    known_posts = sorted({*analysis.get("posts", {}), *duplicates}, key=len, reverse=True)  # plus long d'abord : ids préfixes

    def resolve(company_id: str) -> str:
        seen = {company_id}
        while company_id in aliases and aliases[company_id] not in seen:
            company_id = aliases[company_id]
            seen.add(company_id)
        return company_id

    def source_post(job_id: str) -> str:
        """'fb-post-<post_id>-3' -> publication gardée (celle d'origine si <post_id> est un doublon de pfbid)."""
        for post_id in known_posts:
            prefix = f"fb-post-{post_id}"
            if job_id.startswith(prefix) and JOB_ID_SUFFIX_RE.fullmatch(job_id[len(prefix):]):
                return duplicates.get(post_id, post_id)
        return ""

    current: dict[str, list[tuple[str, str, str, set[str]]]] = {}
    for job_id, job in jobs.items():
        occurrences = analysis.get("jobs", {}).get(job_id, {}).get("occurrences", [])
        current.setdefault(_offer_signature(job.get("title", "")), []).append((
            job_id, job.get("company_id", ""), normalize_name(job.get("company_name", "")),
            {occurrence["post_id"] for occurrence in occurrences},
        ))

    excluded_offers = analysis.get("excluded_offers", {})
    excluded_companies = analysis.get("excluded_companies", {})

    def find(kind: str, doc_id: str, stored: dict[str, Any]) -> str | None:
        if kind == "jobs" and doc_id in excluded_offers:
            return REMOVE  # concours, formation, sans contact, entreprise exclue : retiré du site sans remplaçant
        if kind == "companies" and doc_id in excluded_companies:
            return REMOVE  # liste d'exclusion (excluded_companies.txt)
        if kind == "companies":
            company_id = resolve(doc_id)
            return company_id if company_id != doc_id and company_id in companies else None
        signature = _offer_signature(stored.get("title") or "")
        if not signature:
            return None
        company_id = resolve(stored.get("company_id") or "")
        name = normalize_name(stored.get("company_name") or "")
        post_id = source_post(doc_id)
        for job_id, current_company, current_name, posts in current.get(signature, []):
            same_company = company_id == current_company if company_id and current_company else bool(name) and name == current_name
            if same_company or (post_id and post_id in posts):
                return job_id
        return None

    return find


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _offer_types_arg(value: str) -> list[str]:
    types = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [part for part in types if part not in OFFER_TYPES]
    if not types or unknown:
        raise argparse.ArgumentTypeError(f"types inconnus {unknown or value!r} : choisir parmi {', '.join(OFFER_TYPES)}")
    return types


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pages Facebook -> analyse IA -> JSON au format backend")
    parser.add_argument("--url", action="append", default=[], metavar="URL", help="Page Facebook (répétable). Défaut : --pages-file")
    parser.add_argument(
        "--pages-file", default=None, metavar="FICHIER",
        help="Un lien de page par ligne (défaut : facebook_script/countries/<pays>/pages.txt)",
    )
    parser.add_argument("--group-url", action="append", default=[], metavar="URL", help="Groupe Facebook (répétable). Défaut : --groups-file")
    parser.add_argument(
        "--groups-file", default=None, metavar="FICHIER",
        help="Un lien de groupe par ligne (défaut : facebook_script/countries/<pays>/groups.txt)",
    )
    parser.add_argument("--no-groups", action="store_true", help="Ignore les groupes (pages seulement)")
    parser.add_argument("--no-pages", action="store_true", help="Ignore les pages (groupes seulement)")
    parser.add_argument("--country", choices=sorted(COUNTRIES), default="madagascar", help="Pays des pages (défaut : madagascar)")
    parser.add_argument("--max-posts", type=int, default=0, metavar="N", help="Nouvelles publications max par page (0 = illimité)")
    parser.add_argument("--stop-after-known", type=int, default=0, metavar="N", help="Arrête une page après N publications déjà en cache d'affilée")
    parser.add_argument(
        "--max-age-days", type=int, default=15, metavar="N",
        help="Seulement les publications des N derniers jours, aujourd'hui compris (défaut 15 : le 15 on garde du 1er au 15 ; 0 = toutes)",
    )
    parser.add_argument("--max-idle", type=int, default=fb.DEFAULT_MAX_IDLE, metavar="N", help="Scrolls sans nouveauté avant fin du fil")
    parser.add_argument("--scroll-delay", type=float, default=fb.DEFAULT_SCROLL_DELAY, metavar="SECONDES", help="Pause moyenne après chaque scroll")
    parser.add_argument("--max-photos", type=int, default=DEFAULT_MAX_PHOTOS, metavar="N", help=f"Images max par publication (défaut {DEFAULT_MAX_PHOTOS})")
    parser.add_argument("--no-viewer", action="store_true", help="Vignettes du fil seulement (5 max, taille réduite) au lieu de la visionneuse")
    parser.add_argument("--no-exact-dates", action="store_true", help="Pas de survol pour lire la date complète (date estimée depuis '3 j')")
    parser.add_argument("--no-ocr", action="store_true", help="Pas d'OCR des images (l'IA lit quand même les images)")
    parser.add_argument("--no-ai", action="store_true", help="Scraping seul, aucun appel OpenAI")
    parser.add_argument(
        "--send-all-images", action="store_true",
        help="Joint toutes les images à l'IA en haute résolution (coûteux ; par défaut les images lues par l'OCR partent en texte)",
    )
    parser.add_argument(
        "--no-email-filter", action="store_true",
        help="Envoie aussi à l'IA les images sans email lu par l'OCR (offres sans email : candidature par téléphone...)",
    )
    parser.add_argument(
        "--no-job-filter", action="store_true",
        help="Envoie aussi à l'IA les publications de groupe sans mot-clé d'offre d'emploi (plus cher)",
    )
    parser.add_argument(
        "--excluded-companies-file", default=str(EXCLUDED_COMPANIES_FILE), metavar="FICHIER",
        help="Entreprises jamais publiées, ni elles ni leurs offres (défaut : facebook_script/excluded_companies.txt)",
    )
    parser.add_argument(
        "--allow-no-contact", action="store_true",
        help="Publie aussi les offres dont l'entreprise n'a ni email ni téléphone (par défaut écartées : aucun moyen de la contacter)",
    )
    parser.add_argument(
        "--allow-relay-companies", action="store_true",
        help="Publie aussi les entreprises que l'IA classe comme portail d'offres (MADAJOB, Asako...) ou ONG/organisation "
             "internationale/institution publique (Banque Mondiale, agences UN, ministères...) — par défaut écartées, "
             "voir excluded_companies.txt pour les entreprises exclues nommément",
    )
    parser.add_argument(
        "--no-suspicious-filter", action="store_true",
        help="Publie aussi les offres où l'IA a détecté des signes d'arnaque (paiement demandé, MLM...) — par défaut écartées",
    )
    parser.add_argument(
        "--offer-types", type=_offer_types_arg, default=list(DEFAULT_OFFER_TYPES), metavar="TYPES",
        help=f"Types d'annonces gardés, séparés par des virgules parmi {', '.join(OFFER_TYPES)} "
             f"(défaut : {','.join(DEFAULT_OFFER_TYPES)} ; ex. --offer-types emploi pour écarter aussi les stages)",
    )
    parser.add_argument(
        "--keep-images", action="store_true",
        help="Garde les images sur le disque (par défaut supprimées une fois la publication analysée, écartée ou trop ancienne)",
    )
    parser.add_argument("--skip-scrape", action="store_true", help="Pas de navigateur : analyse IA + consolidation des publications en cache")
    parser.add_argument(
        "--reanalyze", action="store_true",
        help="Ré-analyse les publications analysées avec un autre modèle ou une ancienne version du prompt (cache IA utilisé)",
    )
    parser.add_argument(
        "--max-ai-tokens", type=int, default=0, metavar="N",
        help="Arrête l'analyse IA quand les nouveaux appels de ce lancement ont consommé N tokens (0 = sans limite)",
    )
    parser.add_argument("--push", action="store_true", help="Après la consolidation, envoie les JSON au backend (SCRAPER_API_URL)")
    parser.add_argument("--push-only", action="store_true", help="Envoie seulement les JSON déjà générés au backend (ni navigateur, ni IA)")
    parser.add_argument("--force-push", action="store_true", help="Renvoie tous les documents, même inchangés depuis le dernier envoi")
    parser.add_argument(
        "--no-clean", action="store_true",
        help="Ne supprime pas du backend les documents déjà envoyés puis regroupés avec un autre (entreprise fusionnée, offre republiée)",
    )
    parser.add_argument("--dump-html", action="store_true", help="HTML brut de chaque publication dans downloaded_files/facebook_html/")
    parser.add_argument("--login", action="store_true", help="Propose la connexion manuelle même si une session est détectée")
    parser.add_argument("--headless", action="store_true", help="Sans fenêtre (profil déjà connecté)")
    args = parser.parse_args()
    if args.pages_file is None:
        args.pages_file = str(fb.SCRIPT_DIR / "countries" / args.country / "pages.txt")
    if args.groups_file is None:
        args.groups_file = str(fb.SCRIPT_DIR / "countries" / args.country / "groups.txt")
    return args


def _print_stats(stats: fb.GroupStats, max_age_days: int) -> None:
    print(
        f"  -> {stats.new} nouvelle(s), {stats.known} déjà connue(s), {stats.too_old} trop ancienne(s) "
        f"(> {max_age_days} j) ignorée(s), {stats.without_permalink} sans permalien"
    )


def group_scrape_options(args: argparse.Namespace) -> fb.ScrapeOptions:
    """Options du scroll d'un groupe (scraper.py) : tri chronologique, images + OCR nécessaires à l'analyse IA."""
    return fb.ScrapeOptions(
        sort="chrono", max_posts=args.max_posts, stop_after_known=args.stop_after_known, max_idle=args.max_idle,
        scroll_delay=args.scroll_delay, dump_html=args.dump_html, download_images=True, ocr=not args.no_ocr,
        exact_dates=not args.no_exact_dates, max_age_days=args.max_age_days,
    )


def run_scraping(
    targets: list[PageTarget], groups: list[fb.GroupTarget], args: argparse.Namespace, country: CountryProfile
) -> None:
    fb.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    fb._reset_profile_exit_state(fb.PROFILE_DIR)
    with SB(uc=True, locale="fr", user_data_dir=str(fb.PROFILE_DIR), headless=args.headless) as sb:
        fb._activate_cdp_mode(sb)
        logged_in = fb.is_logged_in(sb)
        if args.headless and (args.login or not logged_in):
            print("[ERREUR] Profil Facebook non connecté : relancez sans --headless pour vous connecter.")
            return
        if (args.login or not logged_in) and not fb.login_and_wait(sb):
            return
        total = len(targets) + len(groups)
        for index, target in enumerate(targets, 1):
            print(f"\n[{index}/{total}] Page {target.page_id}")
            _print_stats(scrape_page(sb, target, args, country), args.max_age_days)
        for index, group in enumerate(groups, len(targets) + 1):
            print(f"\n[{index}/{total}] Groupe {group.group_id}")
            stats = fb.scrape_group(sb, group, group_scrape_options(args))  # cache et export propres au groupe
            page_id = register_group(group, country)  # puis analysé, consolidé et envoyé comme une page
            fetch_group_images(sb, group, page_id, args)
            _print_stats(stats, args.max_age_days)


def fetch_group_images(sb: SB, group: fb.GroupTarget, page_id: str, args: argparse.Namespace) -> None:
    """
    Le fil d'un groupe ne montre que 5 vignettes ; les publications « +N » passent ensuite par la même visionneuse
    que les pages (fetch_post_images : photos pleine résolution, reprise si incomplet). Les vignettes déjà lues par
    scraper.finalize_posts sont remplacées par les photos complètes ; l'OCR des nouvelles images est rattrapé
    avant l'analyse IA (analyze_posts).
    """
    if args.no_viewer:
        return
    store = fb.PostStore(group.output_file)  # relu : scrape_group vient de l'enregistrer
    entry = {"page_url": group.group_url, "kind": "group", "posts_file": str(group.output_file.relative_to(fb.OUTPUT_DIR))}
    fetch_post_images(sb, store, source_target(page_id, entry), args)


class RunAlreadyActive(Exception):
    pass


@contextmanager
def run_lock(path: Path):
    """Verrou exclusif sur un fichier, libéré par le système même si le processus plante."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RunAlreadyActive() from None
    try:
        yield
    finally:
        with suppress(OSError):
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def _push(args: argparse.Namespace, country: CountryProfile, api_url: str) -> None:
    if api_url:
        finder = None if args.no_clean else stale_replacement_finder(country)
        push_country(pages_dir(), country.code, api_url, force=args.force_push, find_replacement=finder)
    else:
        print("[ERREUR] SCRAPER_API_URL est vide : envoi au backend désactivé.")


def _run(args: argparse.Namespace, country: CountryProfile, api_url: str) -> None:
    if args.push_only:
        _push(args, country, api_url)
        return

    if args.skip_scrape and not args.no_groups:  # groupes déjà collectés par scraper.py : les faire connaître au pipeline
        for group in fb.load_targets(args.group_url, Path(args.groups_file)):
            if group.output_file.exists():
                register_group(group, country)

    if not args.skip_scrape:
        targets = [] if args.no_pages else load_page_targets(args.url, Path(args.pages_file))
        groups = [] if args.no_groups else fb.load_targets(args.group_url, Path(args.groups_file))
        if not targets and not groups:
            print(f"[ERREUR] Rien à scraper : passez --url / --group-url ou ajoutez des liens dans "
                  f"{args.pages_file} ou {args.groups_file}")
            return
        run_scraping(targets, groups, args, country)

    if not args.no_ai:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            print("\n[ERREUR] OPENAI_API_KEY absente (environnement ou .env) : analyse IA sautée.")
        else:
            model = os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
            print(f"\nIA : modèle {model}, clé {mask_secret(api_key)}")
            extractor = AiExtractor(api_key, model, pages_dir() / "ai_cache", os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL)
            analyze_posts(
                country, extractor, reanalyze=args.reanalyze, ocr=not args.no_ocr,
                send_all_images=args.send_all_images, max_tokens=args.max_ai_tokens, max_age_days=args.max_age_days,
                email_filter=not args.no_email_filter, job_filter=not args.no_job_filter,
            )
    if not args.keep_images:
        release_images(country, args.max_age_days)
    consolidate(country, args.offer_types, require_contact=not args.allow_no_contact,
                excluded_companies=load_excluded_companies(Path(args.excluded_companies_file)),
                exclude_relay_companies=not args.allow_relay_companies, exclude_suspicious=not args.no_suspicious_filter)
    if args.push:
        _push(args, country, api_url)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")
    load_dotenv(fb.PROJECT_ROOT / ".env", fb.PROJECT_ROOT / "backend" / ".env")
    args = parse_args()
    country = COUNTRIES[args.country]
    api_url = os.getenv("SCRAPER_API_URL", DEFAULT_API_URL)
    try:
        with run_lock(pages_dir() / f".run_{country.code}.lock"):
            _run(args, country, api_url)
    except RunAlreadyActive:
        print(f"[ERREUR] Un autre lancement de pages.py ({country.code}) est déjà en cours : attendez sa fin "
              f"(deux lancements simultanés écraseraient les mêmes fichiers).")
    except KeyboardInterrupt:
        print("\n[INFO] Interruption (Ctrl+C) - tout ce qui a été lu/analysé est sauvegardé ; relancez pour continuer.")


if __name__ == "__main__":
    main()

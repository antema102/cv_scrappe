"""
facebook_script/post_groups.py
==============================
Publie les offres WipWork dans des groupes Facebook : dans chaque groupe de
facebook_script/pays/<code>/share_groups.txt, les --per-post (40) offres les plus récentes jamais encore
publiées dans CE groupe : ~40 nouvelles offres par jour, ce sont donc celles du jour.

La publication (post_text) liste les offres numérotées, chacune avec son lien
wipwork.com/jobs/<slug>, + les affiches jointes (visuals.py : une par tranche de 10 offres,
même numérotation ; --no-images pour le texte seul). --single-link : un seul lien
(wipwork.com/jobs) + 6 exemples de postes, repli si Facebook bloque les publications à liens
multiples (constaté le 19/10/2026). Pas de commentaire : sous une publication en attente
d'approbation, Facebook n'ouvre pas les commentaires.

Pas d'API possible : Meta a supprimé l'API Groups (publish_to_groups) en avril 2024. Le
script pilote Chrome (SeleniumBase CDP, profil persistant connecté à la main) comme une
personne : ouvre le groupe, clique sur « Exprimez-vous… », active « Publier de manière
anonyme » puis « OK » sur l'explication qui s'affiche (--no-anonymous : sous le nom du
compte ; interrupteur absent = groupe sans
publication anonyme, publiée sous le nom du compte), colle le texte (événement paste lu par
l'éditeur Lexical de Facebook ; repli : frappe ligne par ligne via CDP), joint les affiches,
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
  - pause aléatoire de --delay-min à --delay-max secondes (55 à 65 s, ~1 min) entre deux groupes ;
  - au plus une publication par groupe toutes les --min-hours heures (20) ;
  - en-tête et ordre des offres différents d'un groupe à l'autre ;
  - groupe où le compte est suspendu (« Votre profil a été suspendu dans ce groupe … jusqu'au … ») ou a atteint la
    limite de publications en attente d'approbation : sauté jusqu'à la fin de la suspension, ou 3 jours
    (--retry-blocked pour réessayer quand même) au lieu d'être retenté chaque jour ;
  - limite anti-spam du compte (« Nous limitons le nombre de fois que vous pouvez publier… », écrite dans la
    fenêtre de rédaction) : arrêt, puis aucune publication pendant 24 h (account_block.json) ;
  - arrêt de tout le lancement sur un message de blocage / limite / spam de Facebook,
    sur une page de connexion, ou après MAX_FAILURES_IN_A_ROW échecs d'affilée.

Le compte doit être membre des groupes (sinon groupe ignoré : « not_member »). Publication
soumise à l'approbation des admins = « pending », comptée comme publiée.

Plusieurs pays (les 54 pays d'Afrique de countries.py, un dossier chacun) : un pays publie dès
que facebook_script/pays/<code>/share_groups.txt contient des groupes. Les pays passent
L'UN APRÈS L'AUTRE :
offres de l'API pour ce pays (paramètre pays, + filtre targetCountries / companyCountry :
une offre d'un autre pays n'est jamais publiée dans ses groupes), ses groupes, son suivi,
son profil Chrome, puis Chrome fermé et pays suivant. Textes et affiches dans la langue du
pays (fr / en / pt). Réglages par pays dans facebook_script/share_countries.json : profil
(au plus un compte par pays ; plusieurs pays peuvent partager le même), offres par
publication, minimum d'offres, quota quotidien. Un arrêt (blocage, déconnexion, échecs
d'affilée) arrête les pays de CE profil ; les autres profils continuent.

Quota (max_groups_per_day, --max-groups-per-day ; 0 = aucun) : au plus N publications par
jour et par profil, tous pays confondus ; les groupes jamais publiés passent d'abord, puis
ceux dont la dernière publication est la plus ancienne (chaque groupe servi tous les
quelques jours au lieu de tous les groupes chaque jour).

Anti-doublons, en plus du suivi des offres par groupe (offer_key) :
  - même groupe sous deux adresses (nom personnalisé / id numérique) : id numérique lu à
    l'ouverture du groupe (GROUP_ID_JS, numeric_id du suivi), la deuxième adresse est
    ignorée (« duplicate_group ») ;
  - même groupe dans les listes de deux pays : ignoré dans les deux, [ERREUR] à chaque
    lancement tant que les listes ne sont pas corrigées ;
  - publication interrompue (plantage, Ctrl+C, Chrome figé) entre le clic sur « Publier »
    et l'écriture du suivi : marqueur « publishing » écrit AVANT le clic ; au lancement
    suivant il devient « unknown », compté comme publié (offres marquées, --min-hours) :
    mieux vaut rater une publication que la doubler ;
  - un seul post_groups.py à la fois (verrou downloaded_files/facebook_share/.post_groups.lock).

Usage :
    python facebook_script/post_groups.py --preview          # textes et affiches par groupe, sans publier
    python facebook_script/post_groups.py                    # automatique, sans fenêtre (= PM2)
    python facebook_script/post_groups.py --max-groups 5
    python facebook_script/post_groups.py --visible          # automatique, fenêtre Chrome visible
    python facebook_script/post_groups.py --assisted         # fenêtre visible, ENTRÉE pour publier chaque groupe
    python facebook_script/post_groups.py --no-anonymous     # publier sous le nom du compte, pas en anonyme
    python facebook_script/post_groups.py --group-url https://www.facebook.com/groups/... --per-post 5
    python facebook_script/post_groups.py --login            # (re)connecter le profil (fenêtre visible)
    python facebook_script/post_groups.py --country cameroun --preview
    python facebook_script/post_groups.py --login --country cameroun
    python facebook_script/post_groups.py --countries madagascar,cameroun
    python facebook_script/post_groups.py --all-countries    # tous les pays qui ont des groupes (= PM2)

Sans option de pays : Madagascar, comme avant le multi-pays. --auto et --headless (anciens
noms, comportement par défaut aujourd'hui) restent acceptés.

PM2 (ecosystem.config.js, app "fb-share-groups") : une fois par jour (cron_restart). Sans
terminal, aucune question n'est posée : session expirée = arrêt avec message, reconnecter
à la main avec --login.

Suivi : downloaded_files/facebook_share/group_posts_<code pays>.json (par groupe : offres
publiées, date de la dernière publication, historique des tentatives avec le profil,
numeric_id, marqueur publishing).
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
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout, suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import account_pool
import fb_dates
import scraper as fb
import share
import visuals
from countries import COUNTRIES, LEGACY_COUNTRY, CountryProfile, active_countries, find_country, list_file, parse_countries
from mycdp import dom as cdp_dom
from mycdp import input_ as cdp_input
from mycdp import page as cdp_page
from mycdp import runtime as cdp_runtime
from seleniumbase import SB

# Profil de Madagascar si share_countries.json n'en donne pas. Jamais my_custom_profile_facebook (scraping) : du 11 au
# 15/11/2026 un post_groups.py figé tenait ce profil partagé et pages.py n'a rien scrapé (voir CLAUDE.md)
PROFILE_DIR = fb.PROJECT_ROOT / "my_custom_profile_facebook_share"
SETTINGS_FILE = fb.SCRIPT_DIR / "share_countries.json"  # réglages par pays (profil, quotas)
LOCK_FILE = share.OUTPUT_DIR / ".post_groups.lock"
DEBUG_DIR = share.OUTPUT_DIR / "debug"  # capture + boutons visibles d'un groupe en échec

DEFAULT_PER_POST = 40  # ~40 nouvelles offres par jour sur WipWork Madagascar : celles du jour
DEFAULT_MIN_OFFERS = 40  # moins d'offres nouvelles pour un groupe : pas de publication
DEFAULT_MIN_HOURS = 20
DEFAULT_DELAY_MIN = 55  # ~1 min entre deux groupes (demande de l'utilisateur, 05/11/2026)
DEFAULT_DELAY_MAX = 65  # un peu de hasard : un rythme parfaitement régulier se repère
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
ANONYMOUS_TIMEOUT = 5  # interrupteur « Publier de manière anonyme » toujours absent : groupe sans publication anonyme
ANONYMOUS_CONFIRM_TIMEOUT = 12  # après le clic : explication « Publication anonyme », « OK », interrupteur activé
ANONYMOUS_CLICKS = 2
ANONYMOUS_WAIT = 1.5  # retour de la fenêtre de rédaction après le « OK » de l'explication
PANEL_CLICK_TRIES = 10  # bouton de l'explication relu toutes les 0,3 s jusqu'à ce qu'il ne bouge plus (fin du glissement)
MAX_FAILURES_IN_A_ROW = 100
DEFAULT_MAX_GROUPS_PER_DAY = 0  # quota de publications par jour et par profil (0 = aucun)
SETTING_KEYS = ("profile", "api_name", "per_post", "min_offers", "max_groups_per_day")
# Statuts qui comptent comme une publication faite (quota du jour)
POSTED_STATUSES = ("posted", "pending", "unknown", "publishing")

# Un seul lien dans la publication (Facebook bloque une publication de groupe qui en contient beaucoup)
EXAMPLE_OFFERS = 6  # postes cités dans la publication
MAX_CITIES = 4
MAX_CITY_CHARS = 30  # au-delà, c'est une adresse (« Immeuble Le Colisée Tsiadana Tana 101 »), pas une ville
# Message affiché DANS la publication tant qu'un admin ne l'a pas approuvée (texte plié), pas dans une alerte
# (vu le 19/10/2026 : « Votre publication est en attente d'approbation par un admin »)
PENDING_POST_PATTERN = r"publication est en attente|en attente d approbation|pending approval|awaiting approval"
# Texte que Facebook transforme en lien : les exemples de postes qui en contiennent sont écartés (un seul lien voulu)
AFRICAN_TLDS = (
    "dz|eg|ly|ma|tn|mr|sd|ss|bj|bf|cv|ci|gm|gh|gn|gw|lr|ml|ne|ng|sn|sl|tg|cm|cf|cg|cd|ga|gq|td|st|bi|rw|km|dj|er|et|"
    "ke|mg|mw|mu|mz|ug|sc|so|tz|zm|zw|za|ao|bw|sz|ls|na"
)
LINK_LIKE_RE = re.compile(rf"https?://|www\.|\b[\w-]+\.(?:com|fr|org|net|io|co|info|biz|africa|{AFRICAN_TLDS})\b", re.I)

# En-têtes selon la langue du pays (countries.CountryProfile.lang), un par groupe et par jour
HEADERS = {
    "fr": (
        "📢 OFFRES D'EMPLOI DU {date} — {country_upper}",
        "💼 {count} offres d'emploi à pourvoir — {country}, {date}",
        "🔎 Vous cherchez un emploi ? Voici {count} offres du {date}",
        "📌 Ils recrutent {in_country} — sélection du {date}",
    ),
    "en": (
        "📢 JOB OFFERS — {date} — {country_upper}",
        "💼 {count} job openings — {country}, {date}",
        "🔎 Looking for a job? Here are {count} offers from {date}",
        "📌 Now hiring {in_country} — selection of {date}",
    ),
    "pt": (
        "📢 OFERTAS DE EMPREGO DE {date} — {country_upper}",
        "💼 {count} vagas de emprego — {country}, {date}",
        "🔎 Procura emprego? Aqui estão {count} ofertas de {date}",
        "📌 Estão a recrutar {in_country} — seleção de {date}",
    ),
}
SINGLE_LINK_TEXTS = {
    "fr": {"examples": "Quelques postes à pourvoir :", "apply": "👉 Les {count} offres et la candidature en ligne : {url}"},
    "en": {"examples": "Some of the open positions:", "apply": "👉 All {count} offers and online application: {url}"},
    "pt": {"examples": "Algumas vagas:", "apply": "👉 As {count} ofertas e a candidatura online: {url}"},
}

COMPOSER_LABELS = (
    r"exprimez-vous|[ée]crivez quelque chose|write something|cr[ée]er une publication|create a public post|"
    r"vous voulez dire quelque chose|quoi de neuf|what'?s on your mind"
)
# « Envoyer » / « Submit » : libellé possible du bouton pour une publication anonyme (toujours soumise aux admins)
PUBLISH_LABELS = r"^(?:publier|post|publish|envoyer|soumettre|submit)$"
ANONYMOUS_LABELS = r"anonym"  # « Publier de manière anonyme », « Post anonymously », « Publication anonyme »
ANONYMOUS_OK_LABELS = r"^ok$"
ANONYMOUS_BACK_LABELS = r"^(?:retour|back)$"
PHOTO_LABELS = r"^(?:photo/vid[ée]o|photo/video|photos?/vid[ée]os?|photos?)$"
JOIN_LABELS = r"^(?:rejoindre le groupe|join group|rejoindre|join)$"
# Sur le texte plié (share._fold) des boîtes de dialogue / alertes apparues après le clic sur « Publier »
BLOCK_RE = re.compile(
    r"vous ne pouvez pas|you can t|temporairement|temporarily|bloque|blocked|limit|spam|standards de la communaute|"
    r"community standards|reessayez plus tard|try again later|nous avons supprime|we removed"
)
PENDING_RE = re.compile(r"en attente|pending|approbation|approval|examin|review")
# Limite anti-spam du COMPTE (texte plié), écrite en rouge sous « Publier » dans la fenêtre de rédaction (04/11/2026) :
# « Nous limitons le nombre de fois que vous pouvez publier, commenter ou faire autre chose dans un laps de temps donné
# afin de protéger la communauté contre le spam. Vous pouvez réessayer plus tard. » Avant : jamais vue (NOTICES_JS
# ignorait la fenêtre de rédaction), « not_published » dans chaque groupe (32 le 03/11) - insister prolonge la limite.
ACCOUNT_LIMIT_RE = re.compile(
    r"nous limitons le nombre de fois|contre le spam|we limit how often|(?:from|against) spam"
)
ACCOUNT_BLOCK_HOURS = 24  # aucun lancement pendant 24 h : celui du lendemain 8 h est sauté (repris le surlendemain)
ACCOUNT_BLOCK_FILE = share.OUTPUT_DIR / "account_block.json"  # {profil: {until, since, message}}
ACCOUNT_IDS_FILE = share.OUTPUT_DIR / "share_account_ids.json"  # {profil: identifiant Facebook (cookie c_user)}
# Bandeaux qui remplacent « Exprimez-vous… » (texte plié), vus sur les captures du 04/11/2026 : 6 groupes sur 8 en
# « no_composer » chaque jour depuis le 24/10 avaient 5 publications jamais approuvées, les 2 autres une suspension.
# Retenter chaque jour n'y change rien : le groupe est sauté jusqu'à la fin de la suspension, ou RESTRICTION_RETRY_DAYS.
RESTRICTIONS = (
    # « Votre profil a été suspendu dans ce groupe … jusqu'au 5 nov 2026, 06:47 »
    ("suspended", re.compile(r"profil a ete suspendu|avez ete suspendu|suspendu dans ce groupe|been suspended|suspended (?:from|in) this group")),
    # « Vous avez atteint la limite de contenus en attente dans ce groupe » (5 publications en attente d'approbation)
    ("pending_limit", re.compile(r"limite de (?:contenus?|publications?) en attente|limit (?:of|on|for) pending")),
)
RESTRICTION_LABELS = {
    "suspended": "compte suspendu dans ce groupe par ses admins",
    "pending_limit": "limite de publications en attente d'approbation atteinte (les admins n'approuvent pas)",
}
RESTRICTION_RETRY_DAYS = 3  # limite d'attente, ou suspension sans date lisible : groupe regardé de nouveau 3 jours plus tard
MONTH_RE = "|".join(sorted(fb_dates.MONTHS, key=len, reverse=True))

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

# Volet caché (aria-hidden) de la fenêtre modale : la fenêtre de rédaction et l'explication « Publication anonyme »
# glissent l'une à la place de l'autre dans le même conteneur, celle qui n'est pas affichée porte aria-hidden="true"
HIDDEN_IN_MODAL_JS = r"""
const hiddenInModal = (el) => {
  for (let n = el; n && n.getAttribute; n = n.parentElement) {
    if (n.getAttribute('aria-hidden') === 'true') return true;
    if (n.getAttribute('aria-modal') === 'true') return false;
  }
  return false;
};
"""

# Interrupteur « Publier de manière anonyme » de la fenêtre de rédaction (input role="switch", libellé via
# aria-labelledby) : point à cliquer, état actuel ; {} = pas d'interrupteur, ou fenêtre de rédaction cachée sous
# l'explication. Seul aria-checked compte : el.checked bascule dès le clic, avant le « OK » de l'explication.
# Pas de scrollIntoView : il ferait défiler le conteneur (overflow hidden) où glissent les deux volets.
ANONYMOUS_JS = r"""
(() => {
  const ARGS = __ARGS__;
  __HIDDEN_IN_MODAL__
  const label = new RegExp(ARGS.anonymous, 'i');
  const box = document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]');
  if (!box) return JSON.stringify({});
  for (const el of box.closest('div[role="dialog"]').querySelectorAll('[role="switch"], input[type="checkbox"]')) {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const text = [el.getAttribute('aria-label'), ...ids.map((id) => document.getElementById(id)?.textContent)].join(' ');
    if (!label.test(text) || hiddenInModal(el)) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2, checked: el.getAttribute('aria-checked') === 'true'});
  }
  return JSON.stringify({});
})()
""".replace("__HIDDEN_IN_MODAL__", HIDDEN_IN_MODAL_JS)

# Bouton (« OK », « Retour ») de l'explication « Publication anonyme » affichée après le clic sur l'interrupteur : point
# à cliquer, hit = c'est bien lui sous ce point (pas pendant le glissement du volet) ; {} = pas d'explication à l'écran.
# Le role="dialog" « Publication anonyme » n'entoure que l'en-tête (titre + « Retour », 60 px de haut, vu le 04/11/2026) :
# « OK » est plus bas dans le même volet, cherché en remontant les parents jusqu'au volet (sans la fenêtre de rédaction).
ANONYMOUS_PANEL_JS = r"""
(() => {
  const ARGS = __ARGS__;
  __HIDDEN_IN_MODAL__
  const anonymous = new RegExp(ARGS.anonymous, 'i');
  const label = new RegExp(ARGS.button, 'i');
  const textbox = 'div[contenteditable="true"][role="textbox"]';
  for (const dialog of document.querySelectorAll('[role="dialog"]')) {
    if (dialog.querySelector(textbox) || hiddenInModal(dialog)) continue;
    if (!anonymous.test([dialog.getAttribute('aria-label'), dialog.querySelector('h2')?.textContent].join(' '))) continue;
    for (let scope = dialog; scope && scope !== document.body && !scope.querySelector(textbox); scope = scope.parentElement) {
      for (const el of scope.querySelectorAll('[role="button"]')) {
        if (!label.test((el.getAttribute('aria-label') || el.innerText || '').trim())) continue;
        const r = el.getBoundingClientRect();
        if (!r.width || !r.height) continue;
        const x = r.left + r.width / 2, y = r.top + r.height / 2;
        const top = document.elementFromPoint(x, y);
        return JSON.stringify({x, y, hit: !!top && el.contains(top)});
      }
    }
  }
  return JSON.stringify({});
})()
""".replace("__HIDDEN_IN_MODAL__", HIDDEN_IN_MODAL_JS)

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

# Textes des boîtes de dialogue / alertes (erreurs, « en attente d'approbation ») et messages de la fenêtre de
# rédaction hors texte de la publication (limite anti-spam du compte)
NOTICES_JS = r"""
(() => {
  const notices = new Set();
  for (const el of document.querySelectorAll('[role="dialog"], [role="alertdialog"], [role="alert"], [role="status"]')) {
    const box = el.querySelector('div[contenteditable="true"][role="textbox"]');
    if (box) {
      // Fenêtre de rédaction : ses messages (« Nous limitons le nombre de fois… » sous « Publier »), jamais le texte
      // de la publication (« Date limite » ferait un faux blocage)
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent.trim();
        if (text.length >= 20 && !box.contains(node)) notices.add(text.slice(0, 300));
      }
      continue;
    }
    const text = (el.innerText || '').trim();
    if (text) notices.add(text.slice(0, 300));
  }
  return JSON.stringify({open: !!document.querySelector('div[role="dialog"] div[contenteditable="true"][role="textbox"]'), notices: [...notices]});
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

# Id numérique du groupe ouvert (un même groupe a souvent un nom personnalisé ET un id) : balises al: de l'appli
# mobile, sinon la valeur "groupID" la plus fréquente des données de la page (celle du groupe affiché ; les groupes
# suggérés n'y apparaissent qu'une ou deux fois). Non vérifié sur le vrai Facebook au moment de l'écrire : si aucun
# id n'est trouvé, rien n'est bloqué (seule la comparaison d'adresses reste).
GROUP_ID_JS = r"""
(() => {
  for (const prop of ['al:android:url', 'al:ios:url']) {
    const meta = document.querySelector(`meta[property="${prop}"]`);
    const found = /fb:\/\/group\/(?:\?id=)?(\d+)/.exec((meta && meta.content) || '');
    if (found) return JSON.stringify({id: found[1], source: 'meta'});
  }
  const counts = {};
  for (const found of document.documentElement.innerHTML.matchAll(/"groupID":"(\d+)"/g)) {
    counts[found[1]] = (counts[found[1]] || 0) + 1;
  }
  let best = '', count = 0;
  for (const [id, n] of Object.entries(counts)) if (n > count) { best = id; count = n; }
  return JSON.stringify(best ? {id: best, source: 'groupID', count} : {});
})()
"""

# Bandeau qui interdit de publier dans le groupe (lu seulement si « Exprimez-vous… » est absent) : la ligne visible
# dont le texte plié (comme share._fold) correspond à ARGS.pattern, et les deux suivantes (date de fin de suspension)
RESTRICTION_JS = r"""
(() => {
  const ARGS = __ARGS__;
  const pattern = new RegExp(ARGS.pattern);
  const fold = (s) => (s || '').normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const lines = (document.body.innerText || '').split('\n').map((s) => s.trim()).filter(Boolean);
  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(fold(lines[i]))) return JSON.stringify({text: lines.slice(i, i + 3).join(' ')});
  }
  return JSON.stringify({});
})()
"""


class StopRun(Exception):
    """
    Blocage Facebook, page de connexion ou échecs d'affilée : aucun autre groupe pour ce profil ce lancement-ci
    (scope "profile" : les pays des autres profils continuent) ; arrêt demandé : scope "all".
    """

    def __init__(self, message: str, scope: str = "profile") -> None:
        super().__init__(message)
        self.scope = scope


class AccountLimited(StopRun):
    """Limite anti-spam de Facebook sur le compte (ACCOUNT_LIMIT_RE) : arrêt et pause de ACCOUNT_BLOCK_HOURS."""

    def __init__(self, message: str) -> None:
        super().__init__(f"Facebook limite les publications du compte (anti-spam) : « {message[:200]} »")
        self.message = message


class GroupRestricted(Exception):
    """Bandeau de Facebook dans le groupe (open_composer) : publication impossible, groupe sauté jusqu'à `until`."""

    def __init__(self, reason: str, until: datetime, message: str) -> None:
        super().__init__(reason)
        self.reason, self.until, self.message = reason, until, message


@dataclass(slots=True)
class Job:
    target: fb.GroupTarget
    offers: list[dict[str, Any]]  # dans l'ordre des affiches
    text: str  # publication : un seul lien (wipwork.com/jobs)
    online: int  # offres partageables en tout (« N offres en ligne » des affiches)
    country: CountryProfile
    last_post_at: str = ""  # dernière publication dans ce groupe ("" = jamais) : ordre de rotation du quota


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


def _retry_day(now: datetime, days: int) -> datetime:
    """Minuit, `days` jours plus tard : le lancement quotidien de ce jour-là regarde de nouveau le groupe."""
    return (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def restriction_end(text: str, now: datetime) -> datetime | None:
    """
    Fin d'une suspension : « … jusqu'au 5 nov 2026, 06:47 » / « … until November 5, 2026 at 6:47 AM » (heure locale
    du navigateur = celle de la machine). None si la date est illisible.
    """
    folded = share._fold(text)  # « jusqu’au » -> « jusquau », « jusqu'au » -> « jusqu au »
    if match := re.search(rf"jusqu ?au (\d{{1,2}}) ({MONTH_RE})(?: (\d{{4}}))?(?: (?:a )?(\d{{1,2}}) (\d{{2}}))?\b", folded):
        day, month, year, hour, minute, ampm = match[1], match[2], match[3], match[4], match[5], None
    elif match := re.search(
        rf"until ({MONTH_RE}) (\d{{1,2}})(?: (\d{{4}}))?(?: (?:at )?(\d{{1,2}}) (\d{{2}})(?: (am|pm))?)?\b", folded
    ):
        month, day, year, hour, minute, ampm = match[1], match[2], match[3], match[4], match[5], match[6]
    else:
        return None
    hours = int(hour) if hour else 23
    if ampm:
        hours = hours % 12 + (12 if ampm == "pm" else 0)
    try:
        end = datetime(int(year or now.year), fb_dates.MONTHS[month], int(day), hours, int(minute or 59)).astimezone()
    except ValueError:
        return None
    if not year and end < now - timedelta(days=1):
        end = end.replace(year=end.year + 1)  # « jusqu'au 2 janv » lu fin décembre
    return end


def detect_restriction(sb: SB) -> GroupRestricted | None:
    """Bandeau « suspendu » / « limite de contenus en attente » de la page du groupe (RESTRICTIONS), sinon None."""
    pattern = "|".join(regex.pattern for _, regex in RESTRICTIONS)
    text = share._clean((fb._run_js(sb, RESTRICTION_JS, {"pattern": pattern}) or {}).get("text"))
    if not text:
        return None
    now = datetime.now().astimezone()
    for reason, regex in RESTRICTIONS:
        if regex.search(share._fold(text)):
            until = restriction_end(text, now) if reason == "suspended" else None
            return GroupRestricted(reason, until or _retry_day(now, RESTRICTION_RETRY_DAYS), text[:300])
    return None


def open_composer(sb: SB, group_url: str, on_group_id: Any = None) -> str:
    """
    "open", "not_member", "no_composer", "duplicate_group" ; StopRun sur une page de connexion ; GroupRestricted sur
    un bandeau de suspension ou de limite de publications en attente (cherché seulement tant que « Exprimez-vous… »
    est absent). Le bouton « Exprimez-vous… » est cherché jusqu'au bout du délai : « not_member » seulement s'il n'est
    jamais apparu ET qu'un « Rejoindre » était visible (un « Rejoindre » seul est souvent celui d'un groupe suggéré).
    on_group_id(id, source) -> bool : id numérique du groupe lu dans la page ; False = groupe déjà publié sous une
    autre adresse, rien n'est ouvert.
    """
    sb.goto(group_url)
    sb.sleep(PAGE_LOAD_WAIT + random.uniform(0, 3))
    if fb._is_auth_wall(fb._page_state(sb)):
        raise StopRun("page de connexion Facebook : relancez avec --login")
    if on_group_id is not None:
        found_id = fb._run_js(sb, GROUP_ID_JS) or {}
        if found_id.get("id") and not on_group_id(str(found_id["id"]), str(found_id.get("source", ""))):
            return "duplicate_group"
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
        elif not composer_seen and (restriction := detect_restriction(sb)):
            raise restriction
        sb.sleep(1)
    status = "not_member" if join_seen and not composer_seen else "no_composer"
    save_diagnostics(sb, group_url, status)
    return status


def _anonymous_switch(sb: SB) -> dict[str, Any]:
    return fb._run_js(sb, ANONYMOUS_JS, {"anonymous": ANONYMOUS_LABELS}) or {}


def _click_panel_button(sb: SB, labels: str) -> bool:
    """
    Clic souris sur un bouton de l'explication « Publication anonyme », une fois le volet immobile et le bouton sous le
    point : le volet glisse par-dessus la fenêtre de rédaction, un clic pendant le glissement tombe à côté, et Facebook
    ignore les clics JS (element.click()). False = pas de bouton à l'écran (volet reparti, resté dans la page hors
    du cadre), ou volet jamais immobile.
    """
    previous: dict[str, Any] = {}
    for _ in range(PANEL_CLICK_TRIES):
        button = fb._run_js(sb, ANONYMOUS_PANEL_JS, {"anonymous": ANONYMOUS_LABELS, "button": labels}) or {}
        if "x" not in button:
            return False
        steady = "x" in previous and abs(button["x"] - previous["x"]) < 1 and abs(button["y"] - previous["y"]) < 1
        if steady and button.get("hit"):
            _click(sb, button["x"], button["y"])
            return True
        previous = button
        sb.sleep(0.3)
    return False


def set_anonymous(sb: SB) -> str:
    """
    Active « Publier de manière anonyme » avant le texte et les affiches : "on", "absent" (pas d'interrupteur :
    publication anonyme désactivée par les admins du groupe) ou "failed" (clic ou « OK » sans effet ; « Retour » à la
    fenêtre de rédaction, publiée sous le nom du compte).
    """
    switch: dict[str, Any] = {}
    deadline = time.monotonic() + ANONYMOUS_TIMEOUT
    while time.monotonic() < deadline:
        switch = _anonymous_switch(sb)
        if "x" in switch:
            break
        sb.sleep(1)
    if "x" not in switch:
        return "absent"
    ok_clicks = 0
    for _ in range(ANONYMOUS_CLICKS):
        switch = _anonymous_switch(sb)  # relu juste avant : sur un interrupteur déjà activé, un clic le désactiverait
        if switch.get("checked"):
            return "on"
        if "x" not in switch:
            break
        sb.sleep(random.uniform(0.4, 0.9))
        _click(sb, switch["x"], switch["y"])
        clicked_ok = False
        deadline = time.monotonic() + ANONYMOUS_CONFIRM_TIMEOUT
        while time.monotonic() < deadline:
            sb.sleep(1)
            switch = _anonymous_switch(sb)
            if switch.get("checked"):
                return "on"
            if clicked_ok and "x" in switch:
                sb.sleep(ANONYMOUS_WAIT)  # fenêtre de rédaction revenue : délai pour l'interrupteur avant de le relire
                break  # puis, s'il est toujours désactivé, nouveau clic
            # L'explication « Publication anonyme » glisse par-dessus la fenêtre de rédaction un peu après le clic :
            # l'interrupteur ne passe à aria-checked="true" qu'après son « OK »
            if _click_panel_button(sb, ANONYMOUS_OK_LABELS):
                clicked_ok = True
                ok_clicks += 1
                sb.sleep(ANONYMOUS_WAIT)
    # Explication restée à l'écran : retour à la fenêtre de rédaction (sinon le texte serait collé dans un volet caché)
    back = _click_panel_button(sb, ANONYMOUS_BACK_LABELS)
    if back:
        sb.sleep(ANONYMOUS_WAIT)
    print(f"  [DIAG] Publication anonyme : {ok_clicks} clic(s) sur « OK »{', puis « Retour »' if back else ''}")
    return "failed"


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
    """(fenêtre de rédaction ouverte, messages : boîtes de dialogue / alertes + messages de la fenêtre de rédaction)."""
    state = fb._run_js(sb, NOTICES_JS) or {}
    return bool(state.get("open")), list(state.get("notices") or [])


def _check_account_limit(sb: SB, notices: list[str], group_url: str) -> None:
    """AccountLimited si un message est la limite anti-spam du compte (rien n'est publié : fenêtre vidée et fermée)."""
    if message := next((n for n in notices if ACCOUNT_LIMIT_RE.search(share._fold(n))), None):
        print(f"  [WARN] {share._clean(message)[:300]}")
        save_diagnostics(sb, group_url, "account_limited")
        discard_composer(sb)
        raise AccountLimited(share._clean(message))


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


def publish(sb: SB, group_url: str, auto: bool, with_images: bool = False, on_publishing: Any = None) -> str:
    """
    "posted", "pending", "skipped", "not_published" ; StopRun sur blocage Facebook ou arrêt demandé, AccountLimited
    sur la limite anti-spam du compte (déjà affichée avant le clic, ou apparue après).
    on_publishing() est appelé juste AVANT le clic sur « Publier » (marqueur « publishing » du suivi).
    """
    _, baseline = _notices(sb)
    _check_account_limit(sb, baseline, group_url)
    # « En attente d'approbation » s'affiche dans la publication elle-même, pas dans une alerte : compté avant / après
    pending_before = _pending_count(sb)
    if not auto:
        answer = _ask("  Relisez la publication dans Chrome. ENTRÉE = publier, s = passer ce groupe, q = arrêter : ")
        if answer == "q":
            discard_composer(sb)
            raise StopRun("arrêt demandé", scope="all")
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
    if on_publishing is not None:
        on_publishing()
    _click(sb, button["x"], button["y"])

    deadline = time.monotonic() + (PUBLISH_IMAGES_TIMEOUT if with_images else PUBLISH_TIMEOUT)
    while time.monotonic() < deadline:
        sb.sleep(1)
        still_open, notices = _notices(sb)
        _check_account_limit(sb, notices, group_url)
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


def post_to_group(sb: SB, group_url: str, text: str, auto: bool, images: list[Path] | None = None,
                  anonymous: bool = True, on_group_id: Any = None, on_publishing: Any = None) -> str:
    status = open_composer(sb, group_url, on_group_id)
    if status != "open":
        return status
    sb.sleep(random.uniform(1, 2))
    if anonymous:
        mode = set_anonymous(sb)
        if mode == "on":
            print("  [INFO] Publication anonyme activée")
        elif mode == "absent":
            print("  [WARN] Pas de « Publier de manière anonyme » dans ce groupe : publication sous le nom du compte")
        else:
            print("  [WARN] « Publier de manière anonyme » non activé : publication sous le nom du compte")
            save_diagnostics(sb, group_url, "anonymous")
        sb.sleep(random.uniform(1, 2))  # l'éditeur se redessine après le changement de mode
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
    return publish(sb, group_url, auto, with_images=bool(images), on_publishing=on_publishing)


# ---------------------------------------------------------------------------
# Lancement
# ---------------------------------------------------------------------------


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ---------------------------------------------------------------------------
# Réglages par pays (share_countries.json)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CountrySettings:
    country: CountryProfile
    profile: Path
    api_name: str  # paramètre pays de l'API WipWork
    per_post: int
    min_offers: int
    max_groups_per_day: int  # 0 = aucun quota
    groups_file: Path
    state_path: Path


def default_profile(country: CountryProfile) -> Path:
    """Madagascar : PROFILE_DIR (profil historique) ; autre pays : un profil à lui, à connecter avec --login."""
    if country.code == LEGACY_COUNTRY:
        return PROFILE_DIR
    return fb.PROJECT_ROOT / f"my_custom_profile_facebook_share_{country.code}"


def load_settings_file(path: Path) -> dict[str, Any]:
    """{"defaults": {...}, "countries": {code: {...}}} ; clés commençant par « _ » = commentaires."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[ERREUR] {path} illisible : {exc}") from None
    problems = []
    for code, values in (data.get("countries") or {}).items():
        if code.startswith("_"):
            continue
        if code not in COUNTRIES:
            problems.append(f"pays inconnu « {code} » (codes : python facebook_script/countries.py)")
        problems += [f"{code} : réglage inconnu « {key} »" for key in values if not key.startswith("_") and key not in SETTING_KEYS]
    problems += [f"defaults : réglage inconnu « {key} »" for key in (data.get("defaults") or {})
                 if not key.startswith("_") and key not in SETTING_KEYS]
    if problems:
        raise SystemExit(f"[ERREUR] {path.name} : " + " ; ".join(problems))
    return data


def country_settings(country: CountryProfile, args: argparse.Namespace, config: dict[str, Any], use_cli: bool) -> CountrySettings:
    """Options de la ligne de commande (pays de ce lancement), puis share_countries.json, puis constantes."""
    defaults = config.get("defaults") or {}
    own = (config.get("countries") or {}).get(country.code) or {}

    def pick(name: str, fallback: Any) -> Any:
        cli = getattr(args, name) if use_cli else None
        return cli if cli is not None else own.get(name, defaults.get(name, fallback))

    profile = Path((args.profile if use_cli else None) or own.get("profile") or default_profile(country))
    if not profile.is_absolute():
        profile = fb.PROJECT_ROOT / profile
    groups_file = Path(args.groups_file) if use_cli and args.groups_file else list_file(country, "share_groups")
    return CountrySettings(
        country=country, profile=profile, api_name=own.get("api_name") or country.offers_api_name,
        per_post=int(pick("per_post", DEFAULT_PER_POST)), min_offers=int(pick("min_offers", DEFAULT_MIN_OFFERS)),
        max_groups_per_day=int(pick("max_groups_per_day", DEFAULT_MAX_GROUPS_PER_DAY)), groups_file=groups_file,
        state_path=share.OUTPUT_DIR / f"group_posts_{country.code}.json",
    )


# ---------------------------------------------------------------------------
# Textes
# ---------------------------------------------------------------------------


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


def post_text(offers: list[dict[str, Any]], country: CountryProfile, args: argparse.Namespace, today: date, rank: int) -> str:
    """
    Par défaut (demande de l'utilisateur, 20/10/2026) : les offres numérotées comme les affiches, chacune avec son
    lien wipwork.com/jobs/<slug> (share.digest_post). --single-link : UN SEUL lien (wipwork.com/jobs) + 6 exemples
    de postes, repli si Facebook bloque les publications à liens multiples (constaté le 19/10/2026) ; les exemples
    dont le texte ressemble à une adresse web (« xxx.mg ») y sont écartés : Facebook en ferait un second lien.
    Langue et nom du pays : ceux du pays (countries.py).
    """
    lang = country.lang if country.lang in HEADERS else "fr"
    headers = HEADERS[lang]
    header = headers[(rank + today.toordinal()) % len(headers)].format(
        date=f"{today:%d/%m/%Y}", country=country.label, country_upper=country.label.upper(), count=len(offers),
        in_country=country.in_phrase,
    )
    if not args.single_link:
        return share.digest_post(offers, args.site_url, country.label, today, header, lang)
    texts = SINGLE_LINK_TEXTS[lang]
    examples = [line for line in map(_offer_line, offers) if not LINK_LIKE_RE.search(line)][:EXAMPLE_OFFERS]
    lines = [header, "", texts["examples"], *(f"• {line}" for line in examples)]
    cities, more = _cities(offers)
    if cities:
        lines += ["", f"📍 {', '.join(cities)}{'…' if more else ''}"]
    lines += [
        "",
        texts["apply"].format(count=len(offers), url=f"{args.site_url.rstrip('/')}/jobs"),
        "",
        share.hashtags(country.label, offers, lang),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Doublons de groupes, quota, marqueur de publication
# ---------------------------------------------------------------------------


def group_identity(target: fb.GroupTarget, group: dict[str, Any]) -> str:
    """Id numérique du groupe s'il est connu (adresse numérique ou lu à une visite précédente), sinon son nom."""
    if target.group_id.isdigit():
        return target.group_id
    return str(group.get("numeric_id") or f"name:{target.group_id.lower()}")


Owner = tuple[str, str]  # (code pays, group_id de la ligne gardée)


def build_claims(targets: dict[str, list[fb.GroupTarget]], states: dict[str, dict[str, Any]]) -> tuple[dict[str, Owner], set[Owner]]:
    """
    (groupe -> ligne qui le publie, lignes exclues). Même groupe dans DEUX pays : exclu des deux (on ne sait pas
    lequel a raison) ; deux fois dans le même pays : la première ligne seulement (les autres sont ignorées par plan).
    """
    owners: dict[str, list[Owner]] = {}
    for code, country_targets in targets.items():
        for target in country_targets:
            identity = group_identity(target, states.get(code, {}).get(target.group_id, {}))
            owners.setdefault(identity, []).append((code, target.group_id))
    claims: dict[str, Owner] = {}
    excluded: set[Owner] = set()
    for identity, lines in owners.items():
        if len({code for code, _ in lines}) > 1:
            excluded.update(lines)
            listed = ", ".join(f"{group_id} ({code})" for code, group_id in lines)
            print(f"[ERREUR] Même groupe listé pour plusieurs pays : {listed} - ignoré partout, corrigez les fichiers "
                  f"pays/<code>/share_groups.txt")
            continue
        claims[identity] = lines[0]
    return claims, excluded


def _entry_day(value: Any) -> date | None:
    with suppress(TypeError, ValueError):
        return datetime.fromisoformat(str(value)).date()
    return None


def posts_today(all_settings: dict[str, CountrySettings], profile: Path, today: date) -> int:
    """Publications du jour faites avec ce profil, tous pays confondus (quota partagé)."""
    count = 0
    for settings in all_settings.values():
        if not settings.state_path.exists():
            continue
        for group in load_state(settings.state_path).values():
            entries = list(group.get("history", []))
            if group.get("publishing"):
                entries.append({**group["publishing"], "status": "publishing"})
            for entry in entries:
                if entry.get("status") in POSTED_STATUSES and _entry_day(entry.get("at")) == today:
                    count += (entry.get("profile") or settings.profile.name) == profile.name
    return count


def _posted_entry(offer: dict[str, Any], site_url: str, at: str) -> dict[str, Any]:
    return {"key": share.offer_key(offer), "title": share._clean(offer.get("name")),
            "url": share.offer_url(offer, site_url), "posted_at": at}


def mark_publishing(state: dict[str, dict[str, Any]], job: Job, site_url: str, images: int, profile: str) -> None:
    """Juste avant le clic sur « Publier » : si le lancement meurt avant record(), la publication n'est pas refaite."""
    target = job.target
    group = state.setdefault(target.group_id, {"url": target.group_url, "posted": {}, "history": []})
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    group["publishing"] = {
        "at": at, "offers": [o["id"] for o in job.offers], "images": images, "profile": profile,
        "posted": {o["id"]: _posted_entry(o, site_url, at) for o in job.offers},
    }


def recover_interrupted(state: dict[str, dict[str, Any]]) -> int:
    """
    Marqueur « publishing » resté d'un lancement interrompu après le clic sur « Publier » : résultat inconnu, compté
    comme publié (offres marquées, dernière publication = clic) - mieux vaut rater une publication que la doubler.
    """
    recovered = 0
    for group_id, group in state.items():
        marker = group.pop("publishing", None)
        if not marker:
            continue
        recovered += 1
        at = marker.get("at", "")
        print(f"[WARN] {group_id} : publication interrompue après le clic sur « Publier » ({at}) - résultat inconnu, "
              f"comptée comme publiée (vérifiez le groupe)")
        group.setdefault("history", []).append(
            {"at": at, "status": "unknown", "offers": marker.get("offers", []), "images": marker.get("images", 0),
             "profile": marker.get("profile")}
        )
        group["last_post_at"] = at
        for offer_id, entry in (marker.get("posted") or {}).items():
            group.setdefault("posted", {}).setdefault(offer_id, entry)
    return recovered


# ---------------------------------------------------------------------------
# Lancement
# ---------------------------------------------------------------------------


def _recently_posted(group: dict[str, Any], min_hours: float, now: datetime) -> bool:
    with suppress(KeyError, TypeError, ValueError):
        return now - datetime.fromisoformat(group["last_post_at"]) < timedelta(hours=min_hours)
    return False


def _account_block_until(profile: str) -> datetime | None:
    """Fin de la pause du compte (limite anti-spam vue par un lancement précédent), si elle n'est pas passée."""
    with suppress(OSError, ValueError, KeyError, TypeError, AttributeError):
        until = datetime.fromisoformat(json.loads(ACCOUNT_BLOCK_FILE.read_text(encoding="utf-8"))[profile]["until"])
        return until if until > datetime.now().astimezone() else None
    return None


def _save_account_block(profile: str, message: str) -> datetime:
    """Pause de ACCOUNT_BLOCK_HOURS pour ce profil (ACCOUNT_BLOCK_FILE), lue par _account_block_until au lancement suivant."""
    now = datetime.now().astimezone()
    until = now + timedelta(hours=ACCOUNT_BLOCK_HOURS)
    blocks: dict[str, Any] = {}
    with suppress(OSError, ValueError):
        blocks = json.loads(ACCOUNT_BLOCK_FILE.read_text(encoding="utf-8"))
    history = (blocks.get(profile) or {}).get("history", [])
    blocks[profile] = {
        "until": until.isoformat(timespec="seconds"),
        "since": now.isoformat(timespec="seconds"),
        "message": message[:500],
        "history": [*history, now.isoformat(timespec="seconds")][-30:],  # dates des limites vues
    }
    share._write_json_atomic(ACCOUNT_BLOCK_FILE, blocks)
    return until


def _blocked_until(group: dict[str, Any]) -> datetime | None:
    """Fin du blocage enregistré par record() (suspension, limite de publications en attente), sinon None."""
    with suppress(KeyError, TypeError, ValueError):
        return datetime.fromisoformat(group["blocked"]["until"])
    return None


def plan(targets: list[fb.GroupTarget], offers: list[dict[str, Any]], state: dict[str, dict[str, Any]],
         settings: CountrySettings, args: argparse.Namespace, today: date, claims: dict[str, Owner],
         quota: int | None = None) -> list[Job]:
    """quota : publications encore permises aujourd'hui à ce profil (None = aucun quota)."""
    country = settings.country
    excluded = share.load_excluded_owners(Path(args.excluded_owners_file))
    online = len(share.select_offers(offers, {}, excluded, args.min_days_left, today, False)[0])
    now = datetime.now().astimezone()
    jobs = []
    for rank, target in enumerate(targets):
        group = state.get(target.group_id, {})
        owner = claims.get(group_identity(target, group))
        if owner is not None and owner != (country.code, target.group_id):
            print(f"[WARN] {target.group_id} : même groupe que {owner[1]} ({owner[0]}) - ignoré")
            continue
        until = _blocked_until(group)
        if until and until > now and not args.retry_blocked:
            reason = RESTRICTION_LABELS.get(group["blocked"].get("reason"), group["blocked"].get("reason"))
            print(f"[INFO] {target.group_id} : {reason} - ignoré jusqu'au {until:%d/%m/%Y %H:%M} (--retry-blocked pour réessayer)")
            continue
        if _recently_posted(group, args.min_hours, now):
            print(f"[INFO] {target.group_id} : déjà publié il y a moins de {args.min_hours:g} h ({group['last_post_at']}) - ignoré")
            continue
        selected, _ = share.select_offers(offers, group.get("posted", {}), excluded, args.min_days_left, today, False)
        batch = selected[: settings.per_post]
        if len(batch) < settings.min_offers:
            print(f"[INFO] {target.group_id} : {len(batch)} offre(s) nouvelle(s) pour ce groupe (< {settings.min_offers}) - ignoré")
            continue
        batch = order_offers(target.group_id, batch, today)
        jobs.append(Job(target, batch, post_text(batch, country, args, today, rank), online, country,
                        str(group.get("last_post_at") or "")))
    if quota is not None:
        jobs.sort(key=lambda job: job.last_post_at)  # rotation : jamais publiés d'abord, puis les plus anciens
        if len(jobs) > quota:
            print(f"[INFO] Quota du jour : {len(jobs) - quota} groupe(s) reporté(s) (ceux publiés le plus récemment)")
            jobs = jobs[:quota]
    if args.max_groups > 0:
        jobs = jobs[: args.max_groups]
    return jobs


def posters_for(job: Job, args: argparse.Namespace, today: date) -> list[Path]:
    """Affiches du groupe (visuals.py) ; aucune si --no-images ou rendu impossible (publication texte seul)."""
    if args.no_images:
        return []
    try:
        return visuals.render_posters(job.offers, job.target.file_slug, job.country.label, today, job.online,
                                      args.site_url, job.country.lang)
    except visuals.VisualError as exc:
        print(f"  [WARN] Affiches non générées ({exc}) : publication sans image")
        return []


def record(state: dict[str, dict[str, Any]], job: Job, status: str, site_url: str, images: int = 0,
           restriction: GroupRestricted | None = None, profile: str | None = None) -> None:
    """
    Historique du groupe (avec le profil qui a publié) ; "posted"/"pending" marquent les offres publiées (jamais
    republiées dans ce groupe). `restriction` -> group["blocked"] (sauté par plan() jusqu'à `until`) ; effacé dès que
    le groupe n'est plus bloqué.
    """
    target = job.target
    group = state.setdefault(target.group_id, {"url": target.group_url, "posted": {}, "history": []})
    group.pop("publishing", None)  # résultat connu : le marqueur d'avant le clic n'a plus lieu d'être
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    group.setdefault("history", []).append(
        {"at": at, "status": status, "offers": [o["id"] for o in job.offers], "images": images, "profile": profile}
    )
    if restriction:
        previous = group.get("blocked") or {}
        group["blocked"] = {
            "reason": restriction.reason,
            "until": restriction.until.isoformat(timespec="seconds"),
            "since": previous.get("since", at) if previous.get("reason") == restriction.reason else at,
            "message": restriction.message,
        }
    elif status not in ("stopped", "account_limited"):
        group.pop("blocked", None)
    if status in ("posted", "pending"):
        group["last_post_at"] = at
        for offer in job.offers:
            group.setdefault("posted", {})[offer["id"]] = _posted_entry(offer, site_url, at)


def run(sb: SB, jobs: list[Job], state: dict[str, dict[str, Any]], settings: CountrySettings, args: argparse.Namespace,
        today: date, totals: Counter[str], claims: dict[str, Owner]) -> None:
    failures = 0
    profile = settings.profile.name
    for index, job in enumerate(jobs, 1):
        target, batch = job.target, job.offers
        line: Owner = (settings.country.code, target.group_id)
        if index > 1:
            pause = random.uniform(args.delay_min, args.delay_max)
            print(f"\n[INFO] Pause de {int(pause // 60)} min {int(pause % 60):02d} s avant le groupe suivant (Ctrl+C pour arrêter)")
            sb.sleep(pause)
        print(f"\n[{index}/{len(jobs)}] Groupe {target.group_id} - {len(batch)} offre(s)")
        images = posters_for(job, args, today)

        def on_group_id(numeric_id: str, source: str, target: fb.GroupTarget = target, line: Owner = line) -> bool:
            group = state.setdefault(target.group_id, {"url": target.group_url, "posted": {}, "history": []})
            group["numeric_id"], group["numeric_id_source"] = numeric_id, source
            owner = claims.setdefault(numeric_id, line)
            if owner != line:
                print(f"  [WARN] Même groupe (id {numeric_id}) que {owner[1]} ({owner[0]}) : ignoré")
                return False
            return True

        def on_publishing(job: Job = job, count: int = len(images)) -> None:
            mark_publishing(state, job, args.site_url, count, profile)
            share._write_json_atomic(settings.state_path, state)

        restriction = None
        try:
            status = post_to_group(sb, target.group_url, job.text, args.auto, images, anonymous=not args.no_anonymous,
                                   on_group_id=on_group_id, on_publishing=on_publishing)
        except GroupRestricted as exc:
            restriction, status = exc, exc.reason
            print(f"  [INFO] « {exc.message} »")
            print(f"  [INFO] {RESTRICTION_LABELS[exc.reason]} : groupe ignoré jusqu'au {exc.until:%d/%m/%Y %H:%M}")
        except StopRun as exc:
            stopped = "account_limited" if isinstance(exc, AccountLimited) else "stopped"
            record(state, job, stopped, args.site_url, len(images), profile=profile)
            share._write_json_atomic(settings.state_path, state)
            raise
        record(state, job, status, args.site_url, len(images), restriction, profile=profile)
        share._write_json_atomic(settings.state_path, state)
        totals[status] += 1
        print(f"  -> {status}")
        ok_statuses = ("posted", "pending", "skipped", "not_member", "duplicate_group", *RESTRICTION_LABELS)
        failures = 0 if status in ok_statuses else failures + 1
        if failures >= MAX_FAILURES_IN_A_ROW:
            raise StopRun(f"{failures} échecs d'affilée : la page Facebook a peut-être changé, vérifiez avec --assisted")


def _country_arg(value: str) -> CountryProfile:
    try:
        return find_country(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _countries_arg(value: str) -> list[CountryProfile]:
    try:
        return parse_countries(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publie les offres WipWork dans des groupes Facebook (récapitulatif par groupe)")
    parser.add_argument("--country", type=_country_arg, default=None,
                        help="pays : code, nom ou ISO3 (cameroun, « Côte d'Ivoire », CMR...) ; défaut : madagascar")
    parser.add_argument("--countries", type=_countries_arg, default=None, metavar="PAYS,PAYS",
                        help="plusieurs pays, l'un après l'autre (ex. madagascar,cameroun)")
    parser.add_argument("--all-countries", action="store_true",
                        help="tous les pays dont pays/<code>/share_groups.txt contient des groupes (Madagascar en premier)")
    parser.add_argument("--group-url", action="append", default=[], help="groupe ciblé (répétable, un seul pays) ; défaut : pays/<pays>/share_groups.txt")
    parser.add_argument("--groups-file", default=None, help="un seul pays ; défaut : pays/<pays>/share_groups.txt")
    parser.add_argument("--settings-file", default=str(SETTINGS_FILE), help=f"réglages par pays (défaut {SETTINGS_FILE.name})")
    parser.add_argument("--per-post", type=int, default=None,
                        help=f"offres par publication (défaut : share_countries.json, sinon {DEFAULT_PER_POST})")
    parser.add_argument("--min-offers", type=int, default=None,
                        help=f"offres nouvelles minimum pour publier dans un groupe (défaut : share_countries.json, sinon {DEFAULT_MIN_OFFERS})")
    parser.add_argument("--max-groups-per-day", type=int, default=None,
                        help="publications au plus par jour et par profil, tous pays confondus (défaut : share_countries.json, "
                             f"sinon {DEFAULT_MAX_GROUPS_PER_DAY} = aucun quota)")
    parser.add_argument("--max-groups", type=int, default=0, help="publications au maximum par pays ce lancement-ci (0 = tous les groupes)")
    parser.add_argument("--min-hours", type=float, default=DEFAULT_MIN_HOURS,
                        help=f"heures minimum entre deux publications dans un même groupe (défaut {DEFAULT_MIN_HOURS})")
    parser.add_argument("--delay-min", type=float, default=DEFAULT_DELAY_MIN,
                        help=f"pause minimum entre deux groupes, en secondes (défaut {DEFAULT_DELAY_MIN})")
    parser.add_argument("--delay-max", type=float, default=DEFAULT_DELAY_MAX,
                        help=f"pause maximum entre deux groupes, en secondes (défaut {DEFAULT_DELAY_MAX})")
    parser.add_argument("--retry-blocked", action="store_true",
                        help="réessayer aussi les groupes où le compte est suspendu ou a trop de publications en attente "
                             "(défaut : ignorés jusqu'à la fin de la suspension, ou 3 jours), et publier malgré la pause "
                             f"du compte après une limite anti-spam de Facebook (défaut : {ACCOUNT_BLOCK_HOURS} h sans publier)")
    parser.add_argument("--min-days-left", type=int, default=1, help="jours de validité restants minimum d'une offre (défaut 1)")
    parser.add_argument("--assisted", action="store_true",
                        help="fenêtre visible, ENTRÉE dans le terminal avant chaque publication (défaut : automatique)")
    parser.add_argument("--visible", action="store_true", help="fenêtre Chrome visible, publication automatique (défaut : sans fenêtre)")
    parser.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)  # ancien nom, comportement par défaut
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)  # idem
    parser.add_argument("--preview", action="store_true",
                        help="texte et affiches prévus pour chaque groupe, sans navigateur Facebook ni publication")
    parser.add_argument("--no-images", action="store_true", help="publication texte seul, sans les affiches (visuals.py)")
    parser.add_argument("--no-anonymous", action="store_true",
                        help="publier sous le nom du compte (défaut : « Publier de manière anonyme » activé)")
    parser.add_argument("--single-link", action="store_true",
                        help="un seul lien (wipwork.com/jobs) au lieu d'un lien par offre, si Facebook bloque les publications")
    parser.add_argument("--login", action="store_true", help="(re)connecter le profil du ou des pays choisis (fenêtre visible), puis quitter")
    parser.add_argument("--profile", default=None,
                        help=f"profil Chrome, un seul pays (défaut : share_countries.json, sinon {PROFILE_DIR.name} pour "
                             "Madagascar et my_custom_profile_facebook_share_<pays> ailleurs)")
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
    if sum(bool(x) for x in (args.country, args.countries, args.all_countries)) > 1:
        parser.error("--country, --countries et --all-countries s'excluent")
    if args.all_countries:
        args.country_list = active_countries("share_groups")
        if not args.country_list:
            parser.error("aucun pays : ajoutez des groupes dans facebook_script/pays/<code>/share_groups.txt")
    else:
        args.country_list = args.countries or [args.country or COUNTRIES[LEGACY_COUNTRY]]
    if len(args.country_list) > 1 and (args.group_url or args.groups_file or args.profile):
        parser.error("--group-url / --groups-file / --profile : un seul pays à la fois")
    return args


def open_profile(profile_dir: Path) -> Path:
    fb.PROFILE_DIR = profile_dir  # nom du profil affiché par fb.login_and_wait
    profile_dir.mkdir(parents=True, exist_ok=True)
    fb.close_orphan_chromes(profile_dir)
    fb._reset_profile_exit_state(profile_dir)
    return profile_dir


class ProfileBusy(StopRun):
    """Profil Chrome déjà ouvert par un autre lancement (publication ou vérification) : ce compte est sauté."""

    def __init__(self, profile: str) -> None:
        super().__init__(f"profil {profile} occupé (publication ou vérification en cours) : sauté ce lancement-ci")


def profile_lock_path(profile_dir: Path) -> Path:
    """Verrou d'un profil de partage, lu à l'appel (share.OUTPUT_DIR peut être redirigé)."""
    return share.OUTPUT_DIR / f".profile_{profile_dir.name}.lock"


@contextmanager
def profile_session(profile_dir: Path, headless: bool) -> Iterator[SB]:
    """
    Chrome sur un profil de partage, sous verrou (spec §5.7) : verrou pris AVANT toute action sur le profil (fermeture
    des Chrome orphelins comprise), tenu pendant toute l'utilisation, relâché APRÈS la fermeture de Chrome — aussi sur
    exception. Profil déjà tenu (autre publication, vérification, --login) -> ProfileBusy, jamais d'attente.
    """
    with ExitStack() as stack:
        try:
            stack.enter_context(fb.run_lock(profile_lock_path(profile_dir)))
        except fb.RunAlreadyActive:
            raise ProfileBusy(profile_dir.name) from None
        open_profile(profile_dir)
        sb = stack.enter_context(SB(uc=True, locale="fr", user_data_dir=str(profile_dir), headless=headless))
        fb._activate_cdp_mode(sb)
        yield sb


def facebook_user_id(sb: SB) -> str:
    """Identifiant du compte Facebook connecté (cookie c_user, lu comme fb._session_active), "" si absent."""
    try:
        cookies = sb.cdp.get_all_cookies()
    except Exception:
        return ""
    return next((str(getattr(c, "value", "")) for c in cookies if getattr(c, "name", "") == "c_user"), "")


def check_account_identity(profile: str, sb: SB) -> None:
    """
    Un compte Facebook = un seul profil du pool : un dossier de profil copié garderait la session du compte d'origine
    (quota et limite anti-spam faussés). Conflit -> StopRun pour ce profil ; sinon identifiant enregistré.
    """
    user_id = facebook_user_id(sb)
    if not user_id:
        return
    ids: dict[str, str] = {}
    if ACCOUNT_IDS_FILE.exists():
        try:
            ids = json.loads(ACCOUNT_IDS_FILE.read_text(encoding="utf-8"))
        except ValueError:
            raise StopRun(f"{ACCOUNT_IDS_FILE} illisible : corrigez-le ou supprimez-le") from None
    other = account_pool.account_id_conflict(ids, profile, user_id)
    if other:
        raise StopRun(f"même compte Facebook que le profil {other} : un compte Facebook = un seul profil (dossier de "
                      f"profil copié ?) - profil {profile} arrêté")
    if ids.get(profile) != user_id:
        ids[profile] = user_id
        share._write_json_atomic(ACCOUNT_IDS_FILE, ids)


def login_only(selected: list[CountrySettings]) -> None:
    """
    --login : ouvre seulement Chrome sur le profil de chaque pays choisi (une fois par profil) pour (re)connecter le
    compte, puis s'arrête. Ni lecture de l'API des offres (toutes les pages, plusieurs minutes avec les 502), ni
    affiches, ni publication.
    """
    if not sys.stdin.isatty():
        print("[ERREUR] --login demande un terminal (connexion manuelle puis ENTRÉE).")
        return
    done: set[str] = set()
    for settings in selected:
        if settings.profile.name in done:
            continue
        done.add(settings.profile.name)
        countries = ", ".join(s.country.name for s in selected if s.profile.name == settings.profile.name)
        print(f"\n[INFO] Profil {settings.profile.name} ({countries})")
        try:
            with profile_session(settings.profile, headless=False) as sb:
                if fb.is_logged_in(sb):
                    print(f"[OK] Profil {settings.profile.name} déjà connecté.")
                    continue
                if not fb.login_and_wait(sb):
                    print("Abandon.")
                    return
        except ProfileBusy as exc:
            print(f"[WARN] {exc}")


def load_targets(selected: list[CountrySettings], all_settings: dict[str, CountrySettings],
                 args: argparse.Namespace) -> tuple[dict[str, list[fb.GroupTarget]], dict[str, Owner]]:
    """Groupes de chaque pays (ceux des autres pays aussi : doublons entre pays), sans les lignes exclues."""
    targets: dict[str, list[fb.GroupTarget]] = {}
    states: dict[str, dict[str, Any]] = {}
    selected_codes = {settings.country.code for settings in selected}
    for code, settings in all_settings.items():
        if code in selected_codes:
            country_targets = fb.load_targets(args.group_url, settings.groups_file)
        elif settings.groups_file.exists():
            with redirect_stdout(StringIO()):  # liens invalides des autres pays : signalés à leur propre lancement
                country_targets = fb.load_targets([], settings.groups_file)
        else:
            continue
        targets[code] = country_targets
        states[code] = load_state(settings.state_path)
    claims, excluded = build_claims(targets, states)
    kept = {code: [t for t in country_targets if (code, t.group_id) not in excluded] for code, country_targets in targets.items()}
    return kept, claims


def publish_country(settings: CountrySettings, jobs: list[Job], state: dict[str, dict[str, Any]], args: argparse.Namespace,
                    today: date, claims: dict[str, Owner]) -> Counter[str]:
    """Publie les groupes d'un pays avec son profil ; StopRun remonte (arrêt de ce profil ou de tout le lancement)."""
    totals: Counter[str] = Counter()
    profile_dir = settings.profile
    mode = ("automatique" if args.auto else "assisté") + (", sans fenêtre" if args.headless else ", fenêtre visible")
    print(f"[INFO] {len(jobs)} publication(s) prévue(s) - mode {mode}, profil {profile_dir.name}")
    with profile_session(profile_dir, args.headless) as sb:  # ProfileBusy (StopRun) si le profil est déjà ouvert
        try:
            logged_in = fb.is_logged_in(sb)
            if (args.headless or not sys.stdin.isatty()) and not logged_in:
                raise StopRun(f"profil Facebook {profile_dir.name} non connecté : reconnectez-le dans un terminal avec "
                              f"python facebook_script/post_groups.py --login --country {settings.country.code}")
            if not logged_in and not fb.login_and_wait(sb):
                raise StopRun("connexion abandonnée")
            run(sb, jobs, state, settings, args, today, totals, claims)
        finally:
            summary = ", ".join(f"{count} {status}" for status, count in totals.most_common()) or "aucune"
            print(f"\nTerminé - {summary}. Suivi : {settings.state_path}")
    return totals


def run_countries(selected: list[CountrySettings], all_settings: dict[str, CountrySettings], args: argparse.Namespace) -> int:
    """Pays l'un après l'autre ; code de sortie (1 si l'API des offres d'un pays est restée illisible)."""
    today = date.today()
    many = len(selected) > 1
    targets_by_country, claims = load_targets(selected, all_settings, args)
    stopped: dict[str, str] = {}  # profil -> raison de l'arrêt
    planned: Counter[str] = Counter()  # --preview : publications prévues par profil (rien n'est écrit dans les suivis)
    results: dict[str, str] = {}
    exit_code = 0
    for index, settings in enumerate(selected, 1):
        country = settings.country
        if many:
            print(f"\n{'=' * 60}\n  [{index}/{len(selected)}] {country.name} ({country.code}) - profil {settings.profile.name}\n{'=' * 60}")
        targets = targets_by_country.get(country.code, [])
        if not targets:
            print(f"[ERREUR] Aucun groupe : passez --group-url ou ajoutez des liens dans {settings.groups_file}")
            results[country.code] = "aucun groupe"
            continue
        if settings.profile.name in stopped:
            print(f"[STOP] Profil {settings.profile.name} arrêté plus tôt ({stopped[settings.profile.name]}) : {country.name} ignoré")
            results[country.code] = "profil arrêté"
            continue
        try:
            offers, announced = share.fetch_offers(args.api_url, settings.api_name)
        except share.ShareError as exc:
            print(f"[ERREUR] API des offres ({args.api_url}, pays={settings.api_name}) : {exc}")
            results[country.code] = "API illisible"
            exit_code = 1
            continue
        offers, foreign = share.offers_for_country(offers, [settings.api_name, country.name, country.label])
        print(f"[INFO] {len(offers)} offres uniques ({announced} annoncées par l'API), {len(targets)} groupe(s)")
        if foreign:
            print(f"[WARN] {foreign} offre(s) d'un autre pays renvoyée(s) par l'API pour pays={settings.api_name} : écartée(s)")
        if not offers and not announced:
            print(f"[INFO] Aucune offre WipWork pour pays={settings.api_name} (nom attendu par l'API ? « api_name » "
                  f"dans {Path(args.settings_file).name})")

        state = load_state(settings.state_path)
        if recover_interrupted(state) and not args.preview:
            share._write_json_atomic(settings.state_path, state)
        quota = None
        if settings.max_groups_per_day > 0:
            used = posts_today(all_settings, settings.profile, today) + planned[settings.profile.name]
            quota = max(0, settings.max_groups_per_day - used)
            print(f"[INFO] Profil {settings.profile.name} : {used} publication(s) aujourd'hui sur "
                  f"{settings.max_groups_per_day} permises")
        jobs = plan(targets, offers, state, settings, args, today, claims, quota)
        if not jobs:
            print("[INFO] Rien à publier.")
            results[country.code] = "rien à publier"
            continue
        if args.preview:
            for job in jobs:
                print(f"\n{'-' * 60}\n{job.target.group_url} ({len(job.offers)} offres)\n{'-' * 60}\n{job.text}")
                for path in posters_for(job, args, today):
                    print(f"[AFFICHE] {path}")
            print(f"\n[INFO] Aperçu : {len(jobs)} publication(s) prévue(s), rien n'est publié.")
            planned[settings.profile.name] += len(jobs)
            results[country.code] = f"{len(jobs)} prévue(s)"
            continue
        paused = _account_block_until(settings.profile.name)
        if paused and not args.retry_blocked:
            print(f"[STOP] Facebook a limité les publications du profil {settings.profile.name} (anti-spam) : aucune "
                  f"publication avant le {paused:%d/%m/%Y %H:%M} ({ACCOUNT_BLOCK_FILE}, --retry-blocked pour réessayer)")
            results[country.code] = f"profil en pause jusqu'au {paused:%d/%m/%Y %H:%M}"
            stopped[settings.profile.name] = "limite anti-spam du compte"
            continue
        try:
            totals = publish_country(settings, jobs, state, args, today, claims)
            results[country.code] = ", ".join(f"{n} {status}" for status, n in totals.most_common()) or "aucune"
        except AccountLimited as exc:
            until = _save_account_block(settings.profile.name, exc.message)
            print(f"\n[STOP] {exc}\n[STOP] Aucun autre groupe pour le profil {settings.profile.name}, et aucune publication "
                  f"avant le {until:%d/%m/%Y %H:%M} (insister prolonge la limite ; --retry-blocked pour réessayer quand même).")
            results[country.code] = f"limite anti-spam : pause jusqu'au {until:%d/%m/%Y %H:%M}"
            stopped[settings.profile.name] = "limite anti-spam du compte"
        except StopRun as exc:
            print(f"\n[STOP] {exc} - aucun autre groupe pour le profil {settings.profile.name} ce lancement-ci.")
            results[country.code] = f"arrêt : {exc}"
            if exc.scope == "all":
                break
            stopped[settings.profile.name] = str(exc)
        except KeyboardInterrupt:
            print("\n[INFO] Interruption (Ctrl+C) - les publications déjà faites sont enregistrées.")
            results[country.code] = "interrompu"
            break
    if many:
        print(f"\n{'=' * 60}\nRécapitulatif par pays :")
        for settings in selected:
            print(f"  {settings.country.name:<24} {results.get(settings.country.code, 'non traité')}")
    return exit_code


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")  # emojis des publications sur une console cp1252

    args = parse_args()
    config = load_settings_file(Path(args.settings_file))
    all_settings = {code: country_settings(country, args, config, use_cli=False) for code, country in COUNTRIES.items()}
    selected = [country_settings(country, args, config, use_cli=True) for country in args.country_list]
    all_settings.update({settings.country.code: settings for settings in selected})
    if args.login:
        login_only(selected)
        return
    try:
        with nullcontext() if args.preview else fb.run_lock(LOCK_FILE):
            exit_code = run_countries(selected, all_settings, args)
    except fb.RunAlreadyActive:
        print("[ERREUR] post_groups.py tourne déjà (autre terminal ou PM2) : attendez sa fin - deux lancements "
              "publieraient deux fois dans les mêmes groupes.")
        exit_code = 1
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()

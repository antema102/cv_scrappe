"""
facebook_script/profile_inventory.py
====================================
Inventaire des profils Chrome / comptes Facebook du projet, pour donner à chaque pays SON compte de scraping sans
conflit avec post_groups.py (publication). À lancer sur la machine dont on veut l'inventaire (le serveur).

    python facebook_script/profile_inventory.py
    python facebook_script/profile_inventory.py --propose cameroun=my_custom_profile_facebook,madagascar=my_custom_profile_facebook_share_3
    python facebook_script/profile_inventory.py --propose ... --live                 # + session, compte, groupes
    python facebook_script/profile_inventory.py --propose ... --live --live-accounts # + compte de tous les profils
    python facebook_script/profile_inventory.py --propose ... --live --json inventaire.json

Sans --live : AUCUN Chrome ouvert, AUCUN fichier du projet écrit. Lu : dossiers my_custom_profile*, réglages
(scrape_profiles.json, share_accounts.json, share_countries.json), suivis de post_groups.py (share_account_ids.json,
share_membership.json, account_block.json), scrape_accounts.json, et une COPIE temporaire de la base de cookies de
chaque profil (présence et date d'expiration des cookies de session Facebook c_user / xs ; aucune valeur déchiffrée ;
copie supprimée aussitôt).

--live : ouvre Chrome sans fenêtre sur chaque profil proposé (comme un lancement de scraping, sans la remise en état
du profil : ni fermeture de Chrome, ni Preferences modifiées), lit la session et le compte (cookie c_user), puis visite
chaque groupe de groups.txt du pays : membre (« Exprimez-vous… » présent), lisible sans être membre (groupe public),
non lisible (privé), à vérifier. Aucun clic, aucune saisie, aucune connexion ni déconnexion, aucun « Rejoindre ».
Seul fichier du projet écrit : le verrou downloaded_files/facebook_share/.profile_<nom>.lock, comme pages.py et
post_groups.py (Chrome, lui, met à jour son cache et ses cookies comme à chaque ouverture). Profil déjà ouvert (Chrome lancé dessus, ou verrou de pages.py / post_groups.py) : sauté, jamais attendu. À lancer hors
des heures de run_daily.py et de post_groups.py : l'ancien pages.py (avant scrape_profiles) ne prend pas le verrou et
échouerait s'il démarrait pendant la vérification de son profil.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import textwrap
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import scrape_profiles
from countries import CountryProfile, active_countries, find_country, list_file, list_lines

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SHARE_DIR = PROJECT_ROOT / "downloaded_files" / "facebook_share"
FILES = {
    "scrape_profiles": scrape_profiles.CONFIG_FILE,
    "share_accounts": SCRIPT_DIR / "share_accounts.json",
    "share_countries": SCRIPT_DIR / "share_countries.json",
    "share_account_ids": SHARE_DIR / "share_account_ids.json",
    "share_membership": SHARE_DIR / "share_membership.json",
    "account_block": SHARE_DIR / "account_block.json",
    "scrape_accounts": scrape_profiles.ACCOUNTS_FILE,
}
OTHER_SCRIPTS = {  # profils Chrome des autres scripts du dépôt : jamais candidats
    "my_custom_profile": "index.py (sites emploi.xx)",
    "my_custom_profile_2": "téléchargement des CV",
    "my_custom_profile_3": "email_extractor (Google)",
    "my_custom_profile_senegal": "senegal_script (senjob.com)",
}
GROUP_RE = re.compile(r"/groups/([^/?#\s]+)", re.I)
OK, TODO, BLOCK, INFO = "OK", "À FAIRE", "BLOQUANT", "INFO"
FEED_JS = r"""
(() => {
  const feed = document.querySelector('div[role="feed"]');
  return JSON.stringify({feed: !!feed, posts: feed ? feed.querySelectorAll('[aria-posinset]').length : 0});
})()
"""


def _fold(name: str) -> str:
    return name.strip().casefold()


def read_json(path: Path) -> tuple[Any, str]:
    """(contenu, "") ; (None, "absent") ; (None, "illisible (...)") — jamais d'exception, jamais réécrit."""
    if not path.exists():
        return None, "absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:
        return None, f"illisible ({exc})"


def _chrome_time(value: int) -> datetime | None:
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value) if value else None


def cookie_session(profile: Path, tmp: Path, now: datetime | None = None) -> str:
    """État de la session Facebook d'après les cookies enregistrés (copie lue puis supprimée ; rien déchiffré)."""
    source = next((p for p in (profile / "Default" / "Network" / "Cookies", profile / "Default" / "Cookies")
                   if p.exists()), None)
    if source is None:
        return "aucune session (pas de base de cookies)"
    copy = tmp / f"{profile.name}.sqlite"
    try:
        shutil.copyfile(source, copy)  # l'original n'est qu'ouvert en lecture
    except OSError:
        return "à vérifier (cookies illisibles : Chrome ouvert ?)"
    try:
        connection = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT name, expires_utc FROM cookies WHERE host_key LIKE '%facebook.com' AND name IN ('c_user', 'xs')"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return f"à vérifier (cookies illisibles : {exc})"
    finally:
        with suppress(OSError):
            copy.unlink()
    found = dict(rows)
    if not {"c_user", "xs"} <= set(found):
        return "déconnectée (aucun cookie de session)"
    expires = [t for t in (_chrome_time(found["c_user"]), _chrome_time(found["xs"])) if t]
    if expires and min(expires) < (now or datetime.now(timezone.utc)):
        return f"déconnectée (cookie expiré le {min(expires):%Y-%m-%d})"
    return "cookie valide (à confirmer avec --live)"


def chrome_profiles_open() -> set[str]:
    """Dossiers de profil (pliés) sur lesquels un Chrome tourne en ce moment."""
    try:
        import psutil
    except ImportError:
        return set()
    found = set()
    for proc in psutil.process_iter(["name", "cmdline"]):
        with suppress(psutil.Error, OSError):
            if (proc.info["name"] or "").lower() not in ("chrome.exe", "chrome"):
                continue
            for arg in proc.info["cmdline"] or ():
                if arg.startswith("--user-data-dir="):
                    found.add(os.path.normcase(os.path.abspath(arg.split("=", 1)[1].strip('"'))))
    return found


def lock_held(path: Path) -> bool:
    """Verrou de pages.py / post_groups.py tenu en ce moment (sondé sans créer le fichier)."""
    if not path.exists():
        return False
    try:
        handle = open(path, "r+")
    except OSError:
        return False
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def group_ids(country: CountryProfile) -> list[str]:
    """Groupes à scraper du pays (groups.txt), id ou nom personnalisé en minuscules."""
    ids = []
    for line in list_lines(list_file(country, "groups")):
        match = GROUP_RE.search(line)
        ids.append((match.group(1) if match else line.strip().strip("/")).lower())
    return list(dict.fromkeys(ids))


def memberships(data: Any) -> dict[str, dict[str, set[str]]]:
    """share_membership.json -> {profil plié: {pays: ids des groupes dont le compte est membre}}."""
    result: dict[str, dict[str, set[str]]] = {}
    for profile, entries in (data or {}).items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if not isinstance(entry, dict) or entry.get("status") != "member" or "/" not in key:
                continue
            country, group = key.split("/", 1)
            ids = result.setdefault(_fold(profile), {}).setdefault(country, set())
            ids.add(group.lower())
            if entry.get("numeric_id"):
                ids.add(str(entry["numeric_id"]).lower())
    return result


@dataclass
class ProfileRow:
    name: str
    exists: bool
    roles: list[str] = field(default_factory=list)
    scrape_countries: list[str] = field(default_factory=list)
    pool: str = ""  # "actif", "inactif", ""
    account_ids: dict[str, str] = field(default_factory=dict)  # source -> c_user
    session: str = ""
    busy: str = ""
    blocked_until: str = ""
    live: dict[str, Any] = field(default_factory=dict)

    @property
    def is_facebook(self) -> bool:
        return _fold(self.name).startswith("my_custom_profile_facebook") or bool(self.pool) or bool(
            self.scrape_countries) or any("publication" in r for r in self.roles)


def parse_proposal(value: str) -> dict[str, str]:
    """« cameroun=profil_a,madagascar=profil_b » -> {code: profil} ; ValueError si illisible."""
    proposal: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in value.split(","))):
        country, sep, profile = item.partition("=")
        if not sep or not profile.strip():
            raise ValueError(f"« {item} » : pays=profil attendu")
        code = find_country(country.strip()).code
        profile = profile.strip()
        if "/" in profile or "\\" in profile or ":" in profile or profile in (".", ".."):
            raise ValueError(f"« {profile} » : nom de dossier seul attendu (à la racine du projet)")
        if code in proposal:
            raise ValueError(f"pays {code} proposé deux fois")
        proposal[code] = profile
    return proposal


def collect(proposal: dict[str, str], root: Path | None = None, files: dict[str, Path] | None = None,
            read_cookies: bool = True) -> dict[str, Any]:
    """Inventaire hors ligne (aucun Chrome) : profils, rôles, comptes connus, sessions, adhésions, avertissements."""
    root = root or PROJECT_ROOT
    files = files or FILES
    data: dict[str, Any] = {}
    problems: list[str] = []
    for key, path in files.items():
        data[key], error = read_json(path)
        if error and error != "absent":
            problems.append(f"{path.name} {error}")
    countries = active_countries("pages", "groups")
    try:
        mapping = scrape_profiles.load_mapping(files["scrape_profiles"])
    except scrape_profiles.ProfileConfigError as exc:
        mapping = {}
        problems.append(str(exc))
    accounts = [a for a in ((data["share_accounts"] or {}).get("accounts") or []) if isinstance(a, dict)]
    pool = {_fold(str(a.get("profile", ""))): ("actif" if a.get("active") else "inactif") for a in accounts}
    share_cfg: dict[str, list[str]] = {}
    for code, section in ((data["share_countries"] or {}).get("countries") or {}).items():
        if isinstance(section, dict) and section.get("profile"):
            share_cfg.setdefault(_fold(section["profile"]), []).append(code)

    names: dict[str, str] = {}
    for directory in sorted(root.glob("my_custom_profile*")):
        if directory.is_dir():
            names[_fold(directory.name)] = directory.name
    for name in [*(str(a.get("profile", "")) for a in accounts), *mapping.values(), scrape_profiles.DEFAULT_PROFILE,
                 *proposal.values(), *share_cfg]:
        if name:
            names.setdefault(_fold(Path(name).name), Path(name).name)

    open_dirs = chrome_profiles_open()
    scrape_ids = {_fold(k): (v or {}).get("c_user", "") for k, v in ((data["scrape_accounts"] or {}).get("profiles")
                                                                      or {}).items() if isinstance(v, dict)}
    share_ids = {_fold(k): str(v) for k, v in (data["share_account_ids"] or {}).items()}
    blocks = {_fold(k): v for k, v in (data["account_block"] or {}).items() if isinstance(v, dict)}
    now = datetime.now(timezone.utc)
    rows: list[ProfileRow] = []
    with tempfile.TemporaryDirectory(prefix="inventaire_profils_") as tmp:
        for folded, name in sorted(names.items()):
            path = root / name
            row = ProfileRow(name, path.is_dir())
            row.scrape_countries = [c.code for c in countries if _fold(scrape_profiles.profile_for(c, mapping).name)
                                    == folded]
            if row.scrape_countries:
                row.roles.append("scraping")
            if folded in pool:
                row.pool = pool[folded]
                row.roles.append(f"publication (pool, {pool[folded]})")
            if folded in share_cfg:
                row.roles.append(f"publication (share_countries.json : {', '.join(share_cfg[folded])})")
            if folded.startswith(scrape_profiles.SHARE_PREFIX) and not (folded in pool or folded in share_cfg):
                row.roles.append("ancien profil de publication (hors réglages)")
            if folded in OTHER_SCRIPTS:
                row.roles.append(f"autre script : {OTHER_SCRIPTS[folded]}")
            if not row.roles:
                row.roles.append("non utilisé")
            if scrape_ids.get(folded):
                row.account_ids["scrape_accounts.json"] = scrape_ids[folded]
            if share_ids.get(folded):
                row.account_ids["share_account_ids.json"] = share_ids[folded]
            block = blocks.get(folded)
            with suppress(KeyError, TypeError, ValueError):
                if datetime.fromisoformat(block["until"]) > now:
                    row.blocked_until = block["until"]
            if row.exists:
                if os.path.normcase(os.path.abspath(path)) in open_dirs:
                    row.busy = "Chrome ouvert"
                elif lock_held(scrape_profiles.profile_lock_path(path, files["share_membership"].parent)):
                    row.busy = "verrou tenu"
                row.session = cookie_session(path, Path(tmp)) if read_cookies else "non lue"
            else:
                row.session = "profil absent"
            rows.append(row)

    member = memberships(data["share_membership"])
    coverage: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        for country in countries:
            wanted = group_ids(country)
            got = len(set(wanted) & member.get(_fold(row.name), {}).get(country.code, set()))
            if got:
                coverage.setdefault(row.name, {})[country.code] = [got, len(wanted)]
    return {"rows": rows, "coverage": coverage, "countries": [c.code for c in countries], "mapping": mapping,
            "problems": problems, "groups": {c.code: group_ids(c) for c in countries},
            "pages": {c.code: len(list_lines(list_file(c, "pages"))) for c in countries}}


def known_ids(row: ProfileRow) -> set[str]:
    ids = {v for v in row.account_ids.values() if v}
    if row.live.get("c_user"):
        ids.add(row.live["c_user"])
    return ids


def account_conflicts(rows: list[ProfileRow]) -> dict[str, list[str]]:
    """{c_user: profils} pour un même compte Facebook vu dans plusieurs profils."""
    owners: dict[str, list[str]] = {}
    for row in rows:
        for user_id in known_ids(row):
            owners.setdefault(user_id, []).append(row.name)
    return {uid: names for uid, names in owners.items() if len(names) > 1}


def evaluate_proposal(proposal: dict[str, str], inventory: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    """Pour chaque pays proposé : vérifications (niveau, message). Rien n'est jamais associé automatiquement."""
    rows = {_fold(r.name): r for r in inventory["rows"]}
    conflicts = account_conflicts(inventory["rows"])
    used: dict[str, list[str]] = {}
    for code, profile in proposal.items():
        used.setdefault(_fold(profile), []).append(code)
    verdicts: dict[str, list[tuple[str, str]]] = {}
    for code, profile in proposal.items():
        row = rows.get(_fold(profile))
        checks: list[tuple[str, str]] = []
        if row is None or not row.exists:
            checks.append((BLOCK, f"profil {profile} absent de cette machine : à créer et connecter à la main "
                                  f"(rien ne sera associé automatiquement)"))
            verdicts[code] = checks
            continue
        if len(used[_fold(profile)]) > 1:
            checks.append((BLOCK, f"même profil proposé pour {', '.join(used[_fold(profile)])}"))
        if row.pool == "actif":
            checks.append((TODO, "compte actif dans le pool de publication (share_accounts.json) : à retirer avant "
                                 "de scraper (un compte ne publie et ne scrape jamais en même temps)"))
        elif row.pool == "inactif":
            checks.append((INFO, "déjà inactif dans le pool de publication : à retirer de share_accounts.json"))
        in_settings = bool(row.pool) or any("share_countries.json" in role for role in row.roles)
        if any("share_countries.json" in role for role in row.roles):
            checks.append((TODO, "profil de publication d'un pays dans share_countries.json : à remplacer"))
        if not in_settings:  # déclaré dans les réglages : déjà signalé ci-dessus ; reste le nom pris d'office
            try:
                scrape_profiles.check_profile_name(profile, share=set())
            except scrape_profiles.ProfileConfigError as exc:
                checks.append((BLOCK, str(exc)))
        if row.blocked_until:
            checks.append((INFO, f"limité anti-spam par Facebook jusqu'au {row.blocked_until}"))
        live = row.live
        if live.get("skipped"):
            checks.append((TODO, f"--live : {live['skipped']}"))
        if live.get("error"):
            checks.append((TODO, f"--live en échec : {live['error']}"))
        if live.get("session"):
            level = OK if live["session"] == "connectée" else TODO
            checks.append((level, f"session : {live['session']} (vérifiée --live)"))
        elif row.session.startswith("cookie valide"):
            checks.append((OK, f"session : {row.session}"))
        elif in_settings:
            checks.append((TODO, f"session : {row.session} - reconnexion manuelle une fois le profil retiré des "
                                 f"réglages de publication (pages.py le refuse avant)"))
        else:
            checks.append((TODO, f"session : {row.session} - reconnecter : python facebook_script/pages.py "
                                 f"--login-only --country {code} --profile {profile}"))
        ids = known_ids(row)
        if not ids:
            checks.append((TODO, "compte Facebook inconnu : l'identifier avec --live"))
        elif len(ids) > 1:
            checks.append((BLOCK, f"plusieurs comptes vus pour ce profil : {', '.join(sorted(ids))}"))
        else:
            checks.append((OK, f"compte Facebook {next(iter(ids))}"))
        for user_id, owners in conflicts.items():
            if row.name in owners:
                others = [o for o in owners if o != row.name]
                checks.append((BLOCK, f"compte {user_id} aussi connecté dans {', '.join(others)}"))
        if row.busy:
            checks.append((INFO, f"{row.busy} en ce moment sur ce profil"))
        total = len(inventory["groups"].get(code, []))
        if live.get("groups"):
            readable = [g for g in live["groups"] if g["access"].startswith(("membre", "lisible"))]
            blocked = [g["id"] for g in live["groups"] if g["access"].startswith("non lisible")]
            unsure = [g["id"] for g in live["groups"] if g["access"].startswith("à vérifier")]
            checked = len(live["groups"])
            level = OK if len(readable) == total else TODO
            checks.append((level, f"groupes lisibles (--live) : {len(readable)}/{total}"
                                  + (f" ({checked} vérifiés)" if checked < total else "")))
            if blocked:
                checks.append((TODO, f"{len(blocked)} groupe(s) privé(s) à rejoindre À LA MAIN : {', '.join(blocked)}"))
            if unsure:
                checks.append((TODO, f"{len(unsure)} groupe(s) à vérifier : {', '.join(unsure)}"))
        elif total:
            got = inventory["coverage"].get(row.name, {}).get(code, [0, total])[0]
            level = OK if got == total else INFO
            checks.append((level, f"membre enregistré (share_membership.json) de {got}/{total} groupes scrapés - "
                                  f"accès réel : --live (un groupe public se lit sans être membre)"))
        verdicts[code] = checks
    return verdicts


def classify_access(member: bool, restricted: bool, posts: int, join_seen: bool) -> str:
    if member:
        return "membre (restreint par les admins)" if restricted else "membre"
    if posts:
        return "lisible sans être membre (groupe public)"
    if join_seen:
        return "non lisible (groupe privé, non membre)"
    return "à vérifier (ni fil ni bouton Rejoindre)"


class SessionLost(Exception):
    pass


def group_access(sb: Any, url: str) -> str:
    """Lecture seule : ni clic ni saisie ; seul mouvement, le défilement de COMPOSER_JS vers le bouton."""
    import post_groups as pg
    import scraper as fb

    try:
        sb.goto(url)
    except Exception:
        return "à vérifier (page non chargée)"
    sb.sleep(pg.PAGE_LOAD_WAIT + random.uniform(0, 2))
    if fb._is_auth_wall(fb._page_state(sb)):
        raise SessionLost()
    member = restricted = join_seen = False
    deadline = time.monotonic() + pg.COMPOSER_TIMEOUT
    while True:
        found = fb._run_js(sb, pg.COMPOSER_JS, {"composer": pg.COMPOSER_LABELS, "join": pg.JOIN_LABELS}) or {}
        if found.get("open") or "x" in found:
            member = True
            break
        join_seen = join_seen or bool(found.get("join"))
        if pg.detect_restriction(sb):
            member = restricted = True
            break
        if time.monotonic() >= deadline:
            break
        sb.sleep(1)
    feed = fb._run_js(sb, FEED_JS) or {}
    return classify_access(member, restricted, int(feed.get("posts") or 0), join_seen)


def live_check(row: ProfileRow, root: Path, groups: list[str], max_groups: int, pause: tuple[float, float]) -> None:
    """Session, compte et accès aux groupes d'un profil : row.live. Profil occupé ou absent : sauté."""
    if not row.exists:
        row.live = {"skipped": "profil absent"}
        return
    if row.busy:
        row.live = {"skipped": f"{row.busy} : profil sauté (relancer hors scraping / publication)"}
        return
    # Uniquement des fonctions déjà présentes avant scrape_profiles : l'inventaire tourne sur le serveur avec
    # seulement scrape_profiles.py et ce fichier copiés, sans rien changer d'autre
    import post_groups as pg
    import scraper as fb
    from seleniumbase import SB

    profile = root / row.name
    if os.path.normcase(os.path.abspath(profile)) in chrome_profiles_open():
        row.live = {"skipped": "Chrome ouvert : profil sauté"}
        return
    try:
        lock = fb.run_lock(scrape_profiles.profile_lock_path(profile, SHARE_DIR))
        lock.__enter__()
    except fb.RunAlreadyActive:
        row.live = {"skipped": "verrou tenu : profil sauté"}
        return
    try:
        print(f"\n[live] {row.name}")
        with SB(uc=True, locale="fr", user_data_dir=str(profile), headless=True) as sb:
            fb._activate_cdp_mode(sb)
            logged_in = fb.is_logged_in(sb)
            row.live = {"session": "connectée" if logged_in else "déconnectée (page de connexion)",
                        "c_user": pg.facebook_user_id(sb) if logged_in else ""}
            print(f"  session {row.live['session']}" + (f", compte {row.live['c_user']}" if row.live["c_user"] else ""))
            if not logged_in or not groups:
                return
            results = []
            todo = groups[:max_groups] if max_groups else groups
            for index, group_id in enumerate(todo, 1):
                try:
                    access = group_access(sb, f"https://www.facebook.com/groups/{group_id}/")
                except SessionLost:
                    row.live["session"] = "déconnectée pendant la vérification"
                    break
                results.append({"id": group_id, "access": access})
                print(f"  [{index}/{len(todo)}] {group_id} : {access}")
                if index < len(todo):
                    time.sleep(random.uniform(*pause))
            row.live["groups"] = results
    except Exception as exc:  # un profil en échec n'empêche pas les autres
        row.live = {**row.live, "error": str(exc)[:300]}
        print(f"  [ERREUR] {exc}")
    finally:
        lock.__exit__(None, None, None)


def print_report(inventory: dict[str, Any], proposal: dict[str, str], verdicts: dict[str, list[tuple[str, str]]],
                 machine: str) -> None:
    rows: list[ProfileRow] = inventory["rows"]
    print(f"Inventaire des profils Chrome - {machine} ({PROJECT_ROOT})")
    for problem in inventory["problems"]:
        print(f"[ATTENTION] {problem}")
    print("\nProfil                                     Rôle / pays                                   "
          "Compte Facebook        Session")
    for row in rows:
        role = "; ".join(row.roles)
        if row.scrape_countries:
            role += f" : {', '.join(row.scrape_countries)}"
        ids = ", ".join(sorted(known_ids(row))) or "inconnu"
        session = row.live.get("session") and f"{row.live['session']} (--live)" or row.session
        extra = [x for x in (row.busy, row.blocked_until and f"limité jusqu'au {row.blocked_until}") if x]
        lines = textwrap.wrap(role, 45) or [""]
        print(f"{row.name:<42} {lines[0]:<45} {ids:<22} {session}" + (f" [{'; '.join(extra)}]" if extra else ""))
        for line in lines[1:]:
            print(f"{'':<42} {line}")
    for user_id, owners in account_conflicts(rows).items():
        print(f"[CONFLIT] compte Facebook {user_id} connecté dans plusieurs profils : {', '.join(owners)}")
    countries = inventory["countries"]
    print("\nGroupes scrapés dont le compte est membre enregistré (share_membership.json ; groupes publics lisibles "
          "sans être membre : vérifier avec --live)")
    print(f"{'Profil':<42} " + " ".join(f"{c[:11]:>11}" for c in countries))
    print(f"{'(à scraper : groupes g + pages p)':<42} " + " ".join(
        f"{len(inventory['groups'][c])}g+{inventory['pages'][c]}p".rjust(11) for c in countries))
    for name, cover in inventory["coverage"].items():
        print(f"{name:<42} " + " ".join(f"{'/'.join(map(str, cover[c])) if c in cover else '-':>11}" for c in countries))
    if not proposal:
        print("\n(--propose pays=profil,... pour vérifier une répartition)")
        return
    print("\nRépartition proposée (rien n'est associé ni modifié par cette commande)")
    for code, profile in proposal.items():
        checks = verdicts[code]
        worst = BLOCK if any(l == BLOCK for l, _ in checks) else TODO if any(l == TODO for l, _ in checks) else OK
        print(f"\n  {code} -> {profile} : {worst}")
        for level, message in checks:
            print(f"    [{level}] {message}")
    missing = [c for c in countries if c not in proposal]
    if missing:
        print(f"\n  Pays actifs sans profil proposé : {', '.join(missing)}")


def _pause(value: str) -> tuple[float, float]:
    low, _, high = value.partition("-")
    low_f, high_f = float(low), float(high or low)
    if low_f < 0 or high_f < low_f:
        raise argparse.ArgumentTypeError("MIN-MAX attendu, ex. 8-15")
    return low_f, high_f


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Inventaire des profils Chrome / comptes Facebook (lecture seule)")
    parser.add_argument("--propose", default="", metavar="PAYS=PROFIL,...",
                        help="répartition à vérifier, ex. cameroun=my_custom_profile_facebook,maurice=..._share_12")
    parser.add_argument("--live", action="store_true",
                        help="ouvre Chrome sans fenêtre sur chaque profil proposé : session, compte, groupes du pays")
    parser.add_argument("--live-accounts", action="store_true",
                        help="avec --live : lit aussi session et compte de tous les autres profils Facebook (sans groupes)")
    parser.add_argument("--max-groups", type=int, default=0, metavar="N", help="--live : N groupes par pays au plus")
    parser.add_argument("--pause", type=_pause, default=(8.0, 15.0), metavar="MIN-MAX",
                        help="--live : pause entre deux groupes, en secondes (défaut 8-15)")
    parser.add_argument("--json", metavar="FICHIER", help="écrit aussi l'inventaire complet dans ce fichier JSON")
    args = parser.parse_args()
    try:
        proposal = parse_proposal(args.propose)
    except ValueError as exc:
        parser.error(f"--propose : {exc}")
    if args.live and not proposal:
        parser.error("--live vérifie les profils de --propose")

    inventory = collect(proposal)
    if args.live:
        rows = {_fold(r.name): r for r in inventory["rows"]}
        for code, profile in proposal.items():
            live_check(rows[_fold(profile)], PROJECT_ROOT, inventory["groups"].get(code, []), args.max_groups, args.pause)
        if args.live_accounts:
            proposed = {_fold(p) for p in proposal.values()}
            for row in inventory["rows"]:
                if row.is_facebook and _fold(row.name) not in proposed:
                    live_check(row, PROJECT_ROOT, [], 0, args.pause)
        print()
    verdicts = evaluate_proposal(proposal, inventory)
    machine = f"{platform.node()} / {os.environ.get('USERNAME') or os.environ.get('USER') or '?'}"
    print_report(inventory, proposal, verdicts, machine)
    if args.json:
        report = {**inventory, "rows": [asdict(r) for r in inventory["rows"]], "proposal": proposal,
                  "verdicts": verdicts, "machine": machine, "at": datetime.now().astimezone().isoformat()}
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nInventaire complet : {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

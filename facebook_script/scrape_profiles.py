"""
facebook_script/scrape_profiles.py
==================================
Profil Chrome (donc compte Facebook) du SCRAPING de chaque pays : pages.py et run_daily.py.

facebook_script/scrape_profiles.json :
    {"countries": {"madagascar": "my_custom_profile_facebook", "cameroun": "my_custom_profile_facebook_cameroun"}}

- Pays absent du fichier -> DEFAULT_PROFILE (my_custom_profile_facebook, profil historique de tous les pays).
- Deux pays sur le même profil = même compte Facebook : jamais en même temps (run_daily les fait passer l'un après
  l'autre ; pages.py tient le verrou du profil pendant que Chrome est ouvert).
- Jamais un profil de post_groups.py : déclaré dans share_accounts.json (actif ou non) ou share_countries.json, ou un
  nom par défaut de post_groups.py (my_custom_profile_facebook_share, my_custom_profile_facebook_share_<code pays>) :
  un compte bloqué pour spam casserait le scraping, et deux Chrome ne s'ouvrent pas sur le même profil. Un ancien
  profil de publication (ex. my_custom_profile_facebook_share_10) devient utilisable une fois retiré de ces réglages.
- Dans l'autre sens, post_groups.py n'ouvre jamais un profil de scraping (is_scraping_profile) ni un compte enregistré
  pour le scraping (scraping_account_owner).
- Rien n'est jamais créé, copié, vidé ni supprimé ici : seulement lu.

Compte Facebook de chaque profil (cookie c_user, lu par pages.py une fois connecté) :
downloaded_files/facebook_pages/scrape_accounts.json = {"profiles": {profil: {"c_user", "countries", "updated_at"}}}.
Même compte dans deux profils de scraping, ou compte d'un profil changé depuis le dernier lancement -> le pays s'arrête
(AccountMismatch) au lieu de mélanger les sessions ; pages.py --accept-account accepte un changement voulu.

Ce module n'importe ni Selenium ni scraper.py (lu aussi par run_daily.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from countries import COUNTRIES, CountryProfile, find_country

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONFIG_FILE = SCRIPT_DIR / "scrape_profiles.json"
DEFAULT_PROFILE = "my_custom_profile_facebook"
SHARE_PREFIX = "my_custom_profile_facebook_share"
SHARE_CONFIG_FILES = (SCRIPT_DIR / "share_accounts.json", SCRIPT_DIR / "share_countries.json")
ACCOUNTS_FILE_NAME = "scrape_accounts.json"  # dans downloaded_files/facebook_pages/
ACCOUNTS_FILE = PROJECT_ROOT / "downloaded_files" / "facebook_pages" / ACCOUNTS_FILE_NAME
SHARE_ACCOUNT_IDS_FILE = PROJECT_ROOT / "downloaded_files" / "facebook_share" / "share_account_ids.json"
# Même dossier et même nom de verrou que post_groups.profile_lock_path : un profil ouvert par l'un est vu par l'autre
LOCK_DIR = PROJECT_ROOT / "downloaded_files" / "facebook_share"


class ProfileConfigError(ValueError):
    """scrape_profiles.json illisible ou profil interdit : aucun pays ne doit partir sur un profil incertain."""


class AccountMismatch(Exception):
    """Le compte Facebook connecté dans le profil ne correspond pas à ce qui a été enregistré."""


def _fold(name: str) -> str:
    return name.strip().casefold()  # dossiers Windows insensibles à la casse


def share_profiles(files: tuple[Path, ...] | None = None) -> set[str]:
    """Profils de post_groups.py déclarés dans ses fichiers de réglages (noms pliés), actifs ou non."""
    names: set[str] = set()
    for path in SHARE_CONFIG_FILES if files is None else files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # illisible : post_groups.py le signale lui-même ; le préfixe _share reste interdit
        for account in data.get("accounts") or []:
            if isinstance(account, dict) and isinstance(account.get("profile"), str):
                names.add(_fold(Path(account["profile"]).name))
        for section in (data.get("countries") or {}).values():
            if isinstance(section, dict) and isinstance(section.get("profile"), str):
                names.add(_fold(Path(section["profile"]).name))
        defaults = data.get("defaults") or {}
        if isinstance(defaults, dict) and isinstance(defaults.get("profile"), str):
            names.add(_fold(Path(defaults["profile"]).name))
    return names


def post_groups_default_profiles() -> set[str]:
    """Noms que post_groups.py prend d'office sans réglage (default_profile) : réservés à la publication."""
    return {_fold(SHARE_PREFIX)} | {_fold(f"{SHARE_PREFIX}_{code}") for code in COUNTRIES}


def check_profile_name(name: str, share: set[str] | None = None) -> str:
    """Nom de dossier de profil (à la racine du projet) utilisable pour le scraping ; ProfileConfigError sinon."""
    if not isinstance(name, str) or not name.strip():
        raise ProfileConfigError("profil vide")
    name = name.strip()
    if name in (".", "..") or "/" in name or "\\" in name or ":" in name:
        raise ProfileConfigError(f"profil « {name} » : nom de dossier seul attendu (à la racine du projet)")
    share = share_profiles() if share is None else share
    if _fold(name) in share:
        raise ProfileConfigError(
            f"profil « {name} » : profil de publication (déclaré dans share_accounts.json ou share_countries.json), "
            f"jamais utilisé pour le scraping - retirez-le d'abord de ces réglages"
        )
    if _fold(name) in post_groups_default_profiles():
        raise ProfileConfigError(
            f"profil « {name} » : nom pris d'office par post_groups.py (publication), jamais utilisé pour le scraping"
        )
    return name


def load_mapping(path: Path = CONFIG_FILE, share: set[str] | None = None) -> dict[str, str]:
    """{code pays: nom du profil} ; fichier absent = {} (tous les pays sur DEFAULT_PROFILE) ; ProfileConfigError."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileConfigError(f"{path.name} illisible ({exc}) : corrigez-le, il n'est jamais réécrit") from None
    countries = data.get("countries", {}) if isinstance(data, dict) else None
    if not isinstance(countries, dict):
        raise ProfileConfigError(f"{path.name} : objet \"countries\" attendu ({{\"code pays\": \"profil\"}})")
    share = share_profiles() if share is None else share
    mapping: dict[str, str] = {}
    for key, value in countries.items():
        try:
            code = find_country(key).code
        except ValueError as exc:
            raise ProfileConfigError(f"{path.name} : {exc}") from None
        if code in mapping:
            raise ProfileConfigError(f"{path.name} : pays {code} déclaré deux fois")
        try:
            mapping[code] = check_profile_name(value, share)
        except ProfileConfigError as exc:
            raise ProfileConfigError(f"{path.name}, pays {code} : {exc}") from None
    return mapping


def profile_dir(name: str) -> Path:
    return PROJECT_ROOT / name


def profile_for(country: CountryProfile, mapping: dict[str, str]) -> Path:
    return profile_dir(mapping.get(country.code, DEFAULT_PROFILE))


def shared_profiles(profiles: dict[str, Path]) -> dict[str, list[str]]:
    """{nom du profil: codes pays} pour les profils utilisés par plusieurs pays (passent l'un après l'autre)."""
    users: dict[str, list[str]] = {}
    for code, path in profiles.items():
        users.setdefault(path.name, []).append(code)
    return {name: codes for name, codes in users.items() if len(codes) > 1}


def profile_lock_path(profile: Path, lock_dir: Path = LOCK_DIR) -> Path:
    """Verrou « profil ouvert », identique à celui de post_groups.py (.profile_<nom>.lock)."""
    return lock_dir / f".profile_{profile.name}.lock"


def bind_account(
    data: dict[str, Any], profile: str, country_code: str, user_id: str, accept_change: bool, stamp: str
) -> dict[str, Any]:
    """
    Nouvel état de scrape_accounts.json après la connexion de `user_id` dans `profile` pour `country_code`.
    AccountMismatch si ce compte est déjà celui d'un AUTRE profil de scraping (sessions mélangées), ou si le profil
    avait un autre compte au lancement précédent (sauf accept_change). `data` n'est jamais modifié.
    """
    profiles = dict(data.get("profiles") or {})
    for other, entry in profiles.items():
        if _fold(other) != _fold(profile) and isinstance(entry, dict) and entry.get("c_user") == user_id:
            raise AccountMismatch(
                f"le compte Facebook {user_id} du profil {profile} est déjà celui du profil {other} "
                f"(pays {', '.join(entry.get('countries') or []) or '?'}) : un compte = un seul profil de scraping, "
                f"sinon deux pays scrapent en même temps avec le même compte. Connectez le bon compte dans {profile}, "
                f"ou retirez l'entrée {other} de {ACCOUNTS_FILE_NAME} si ce profil n'est plus utilisé"
            )
    previous = next((entry for name, entry in profiles.items() if _fold(name) == _fold(profile)), None)
    previous = previous if isinstance(previous, dict) else {}
    known = previous.get("c_user")
    if known and known != user_id and not accept_change:
        raise AccountMismatch(
            f"le compte Facebook connecté dans le profil {profile} a changé ({known} -> {user_id}) depuis le dernier "
            f"lancement : vérifiez qu'il s'agit bien du compte de ce pays, puis relancez avec --accept-account"
        )
    countries = sorted({*(previous.get("countries") or []), country_code}) if known == user_id else [country_code]
    profiles = {name: entry for name, entry in profiles.items() if _fold(name) != _fold(profile)}
    profiles[profile] = {"c_user": user_id, "countries": countries, "updated_at": stamp}
    return {**data, "profiles": profiles}


def share_account_owners(share_ids: dict[str, str], user_id: str) -> list[str]:
    """Profils de post_groups.py vus connectés à ce compte (share_account_ids.json, historique compris)."""
    return [name for name, known in share_ids.items() if user_id and known == user_id]


# --- Côté publication (post_groups.py) : lu sans jamais lever d'exception -------------------------------------------


def scraping_profiles(path: Path | None = None) -> set[str]:
    """
    Profils du scraping (noms pliés) : DEFAULT_PROFILE + valeurs de scrape_profiles.json, lues SANS validation : côté
    publication, un fichier de scraping invalide ne doit ni arrêter la publication ni cacher un profil de scraping.
    """
    names = {_fold(DEFAULT_PROFILE)}
    try:
        data = json.loads((path or CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return names
    countries = data.get("countries") if isinstance(data, dict) else None
    for value in countries.values() if isinstance(countries, dict) else ():
        if isinstance(value, str) and value.strip():
            names.add(_fold(Path(value.strip()).name))
    return names


def is_scraping_profile(profile: str | Path, path: Path | None = None) -> bool:
    return _fold(Path(profile).name) in scraping_profiles(path)


def scraping_account_owner(user_id: str, profile: str | Path, accounts_file: Path | None = None) -> str | None:
    """Autre profil enregistré pour le scraping (scrape_accounts.json) avec ce compte Facebook, sinon None."""
    if not user_id:
        return None
    try:
        data = json.loads((accounts_file or ACCOUNTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = data.get("profiles") if isinstance(data, dict) else None
    for name, entry in entries.items() if isinstance(entries, dict) else ():
        if isinstance(entry, dict) and entry.get("c_user") == user_id and _fold(name) != _fold(Path(profile).name):
            return name
    return None

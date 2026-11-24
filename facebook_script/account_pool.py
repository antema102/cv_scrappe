"""
Pool de comptes Facebook de post_groups.py : logique de décision, sans navigateur ni réseau.

Spec : docs/superpowers/specs/2026-11-24-repartition-comptes-facebook-design.md (révision 4).
- comptes et quotas (share_accounts.json) ;
- adhésions (member / not_member / unknown), restrictions de compte ou de groupe ;
- priorité des groupes, attribution globale (§5.4) et équilibrage de la charge (§5.5) ;
- textes du tableau de répartition et du résumé des adhésions.

Aucun import de scraper.py (SeleniumBase) : module testable seul (test_account_pool.py, test_pool_optimality.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRAPING_PROFILE = "my_custom_profile_facebook"  # profil du scraping (pages.py, scraper.py) : jamais dans le pool
ACCOUNT_KEYS = ("profile", "label", "active", "max_groups_per_day")
DEFAULT_KEYS = ("max_groups_per_day",)
TOP_KEYS = ("defaults", "accounts")


class AccountsError(ValueError):
    """share_accounts.json invalide : message en français qui nomme le fichier."""


@dataclass(frozen=True, slots=True)
class Account:
    profile: str  # nom du dossier = identité dans history[].profile, posts_today, account_block.json
    path: Path  # chemin absolu du profil Chrome
    label: str
    active: bool
    quota: int  # publications par jour, tous pays confondus ; 0 = illimité (priorité déjà appliquée)


def _quota(value: Any, where: str, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AccountsError(f"{name} : {where} max_groups_per_day invalide « {value!r} » (entier >= 0 ; 0 = illimité)")
    return value


def _unknown_keys(values: dict[str, Any], allowed: tuple[str, ...], where: str, name: str) -> None:
    unknown = [key for key in values if not key.startswith("_") and key not in allowed]
    if unknown:
        raise AccountsError(f"{name} : {where} clé inconnue « {unknown[0]} » (clés permises : {', '.join(allowed)})")


def load_accounts(path: Path, project_root: Path, cli_quota: int | None = None) -> list[Account]:
    """
    Comptes de share_accounts.json, dans l'ordre du fichier ([] si le fichier n'existe pas).
    Quota : cli_quota (--max-groups-per-day, pour CHAQUE compte) > max_groups_per_day du compte > defaults > 0.
    Fichier illisible ou invalide -> AccountsError (jamais de repli silencieux sur l'ancien fonctionnement).
    """
    name = path.name
    if cli_quota is not None:
        _quota(cli_quota, "--max-groups-per-day :", name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AccountsError(f"{name} illisible (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}") from None
    if not isinstance(data, dict):
        raise AccountsError(f"{name} : un objet JSON est attendu")
    _unknown_keys(data, TOP_KEYS, "", name)
    defaults = data.get("defaults") or {}
    entries = data.get("accounts") or []
    if not isinstance(defaults, dict):
        raise AccountsError(f"{name} : « defaults » doit être un objet")
    if not isinstance(entries, list):
        raise AccountsError(f"{name} : « accounts » doit être une liste")
    _unknown_keys(defaults, DEFAULT_KEYS, "defaults :", name)
    default_quota = _quota(defaults.get("max_groups_per_day", 0), "defaults :", name)

    accounts: list[Account] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, 1):
        where = f"compte n° {index} :"
        if not isinstance(entry, dict):
            raise AccountsError(f"{name} : {where} un objet est attendu")
        _unknown_keys(entry, ACCOUNT_KEYS, where, name)
        raw = entry.get("profile")
        if not isinstance(raw, str) or not raw.strip():
            raise AccountsError(f"{name} : {where} « profile » manquant (dossier du profil Chrome)")
        profile_path = Path(raw.strip())
        if not profile_path.is_absolute():
            profile_path = project_root / profile_path
        profile = profile_path.name
        if profile == SCRAPING_PROFILE:
            raise AccountsError(f"{name} : {where} le profil {SCRAPING_PROFILE} (scraping) est interdit dans le pool")
        if profile in seen:
            raise AccountsError(f"{name} : {where} profil « {profile} » en double")
        seen.add(profile)
        active = entry.get("active", False)
        if not isinstance(active, bool):
            raise AccountsError(f"{name} : {where} « active » doit valoir true ou false")
        label = entry.get("label", profile)
        if not isinstance(label, str):
            raise AccountsError(f"{name} : {where} « label » doit être un texte")
        quota = _quota(entry["max_groups_per_day"], where, name) if "max_groups_per_day" in entry else default_quota
        accounts.append(Account(profile, profile_path, label or profile, active, quota if cli_quota is None else cli_quota))
    return accounts


def pool_enabled(accounts: list[Account]) -> bool:
    """Mode pool : au moins un compte actif (sinon fonctionnement actuel de post_groups.py)."""
    return any(account.active for account in accounts)


def remaining_cap(quota: int, posted_today: int) -> int | None:
    """Publications encore permises aujourd'hui ; None = illimité (quota 0)."""
    if quota == 0:
        return None
    return max(0, quota - posted_today)

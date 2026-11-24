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
from datetime import datetime, timedelta
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


# ---------------------------------------------------------------------------
# Adhésions (§5.2) et restrictions (§5.2.1)
# ---------------------------------------------------------------------------

MEMBER, NOT_MEMBER, UNKNOWN = "member", "not_member", "unknown"
# Statuts de publication qui prouvent l'adhésion : le compte a ouvert la fenêtre de rédaction ou vu un bandeau du groupe
MEMBER_HISTORY_STATUSES = ("posted", "pending", "unknown", "suspended", "pending_limit")
GROUP_RESTRICTIONS = ("suspended", "pending_limit")  # modération du groupe : valent pour TOUS les comptes du pool
MEMBERSHIP_RECHECK_DAYS = 7  # « not_member » revérifié après 7 jours (le compte a pu rejoindre le groupe)


def group_key(code: str, group_id: str) -> str:
    """Identité d'une ligne de pays/<code>/share_groups.txt : « madagascar/506924959474464 »."""
    return f"{code}/{group_id}"


def _parse_dt(value: Any) -> datetime | None:
    """Date ISO avec fuseau ; une date sans fuseau est lue à l'heure locale (jamais de comparaison impossible)."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


@dataclass(frozen=True, slots=True)
class Evidence:
    status: str  # MEMBER | NOT_MEMBER
    at: datetime
    source: str  # "history" (publication) | "check" (vérification)


class MembershipFileError(ValueError):
    """share_membership.json illisible : jamais réécrit."""


def load_membership(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """{profil: {group_key: entrée}} ; {} si absent ; MembershipFileError si illisible."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise MembershipFileError(f"{path.name} illisible (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg} - "
                                  "corrigez-le ou supprimez-le (les adhésions seront revérifiées)") from None
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise MembershipFileError(f"{path.name} : format inattendu ({{profil: {{groupe: entrée}}}} attendu)")
    return data


def merge_account_membership(data: dict[str, dict[str, dict[str, Any]]], profile: str,
                             entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Copie de `data` où seules les entrées `entries` du profil `profile` sont remplacées."""
    merged = dict(data)
    own = dict(data.get(profile) or {})
    own.update(entries)
    merged[profile] = own
    return merged


class Memberships:
    """État d'adhésion (compte, groupe) : preuve la plus récente entre publications et vérifications."""

    def __init__(self, states: dict[str, dict[str, dict[str, Any]]], fallback_profiles: dict[str, str],
                 membership: dict[str, dict[str, dict[str, Any]]]) -> None:
        self._history: dict[tuple[str, str], Evidence] = {}
        self._check: dict[tuple[str, str], Evidence] = {}
        self._checked_at: dict[str, datetime] = {}
        for code, groups in states.items():
            for group_id, group in groups.items():
                key = group_key(code, group_id)
                for entry in group.get("history") or []:
                    status = entry.get("status")
                    found = MEMBER if status in MEMBER_HISTORY_STATUSES else NOT_MEMBER if status == NOT_MEMBER else None
                    profile = entry.get("profile") or fallback_profiles.get(code)
                    when = _parse_dt(entry.get("at"))
                    if found and profile and when:
                        self._keep(self._history, (profile, key), Evidence(found, when, "history"))
        for profile, entries in membership.items():
            for key, entry in entries.items():
                when = _parse_dt(entry.get("checked_at"))
                if when is None:
                    continue
                if profile not in self._checked_at or when > self._checked_at[profile]:
                    self._checked_at[profile] = when
                if entry.get("status") in (MEMBER, NOT_MEMBER):
                    self._keep(self._check, (profile, key), Evidence(entry["status"], when, "check"))

    @staticmethod
    def _keep(index: dict[tuple[str, str], Evidence], slot: tuple[str, str], evidence: Evidence) -> None:
        if slot not in index or evidence.at >= index[slot].at:
            index[slot] = evidence

    def evidence(self, profile: str, key: str) -> Evidence | None:
        history, check = self._history.get((profile, key)), self._check.get((profile, key))
        if history and check:
            return history if history.at > check.at else check  # même instant : la vérification gagne
        return history or check

    def status(self, profile: str, key: str) -> str:
        found = self.evidence(profile, key)
        return found.status if found else UNKNOWN

    def needs_check(self, profile: str, key: str, now: datetime, recheck: bool = False) -> bool:
        """À visiter par --check-membership : inconnu, ou non membre depuis plus de MEMBERSHIP_RECHECK_DAYS jours."""
        found = self.evidence(profile, key)
        if recheck or found is None:
            return True
        if found.status == MEMBER:
            return False
        return now - found.at > timedelta(days=MEMBERSHIP_RECHECK_DAYS)

    def last_check(self, profile: str) -> datetime | None:
        return self._checked_at.get(profile)


@dataclass(frozen=True, slots=True)
class Restriction:
    key: str
    reason: str  # "suspended" | "pending_limit"
    until: datetime
    seen_by: str  # profil qui a vu le bandeau ("" si inconnu)
    source: str  # "publication" (group_posts « blocked ») | "check" (share_membership.json)


def group_restrictions(states: dict[str, dict[str, dict[str, Any]]], membership: dict[str, dict[str, dict[str, Any]]],
                       now: datetime) -> dict[str, Restriction]:
    """Restrictions DE GROUPE encore actives (§5.2.1) : le groupe n'est donné à aucun compte jusqu'à `until`."""
    found: dict[str, Restriction] = {}

    def keep(restriction: Restriction) -> None:
        if restriction.key not in found or restriction.until > found[restriction.key].until:
            found[restriction.key] = restriction

    for code, groups in states.items():
        for group_id, group in groups.items():
            blocked = group.get("blocked") or {}
            until = _parse_dt(blocked.get("until"))
            if blocked.get("reason") in GROUP_RESTRICTIONS and until and until > now:
                seen_by = next((e.get("profile") or "" for e in reversed(group.get("history") or [])
                                if e.get("status") == blocked["reason"]), "")
                keep(Restriction(group_key(code, group_id), blocked["reason"], until, seen_by, "publication"))
    for profile, entries in membership.items():
        for key, entry in entries.items():
            restriction = entry.get("restriction") or {}
            until = _parse_dt(restriction.get("until"))
            if restriction.get("reason") in GROUP_RESTRICTIONS and until and until > now:
                keep(Restriction(key, restriction["reason"], until, profile, "check"))
    return found


def account_id_conflict(ids: dict[str, str], profile: str, user_id: str) -> str | None:
    """Autre profil déjà connecté au même compte Facebook (cookie c_user), sinon None."""
    if not user_id:
        return None
    return next((other for other, known in ids.items() if other != profile and known == user_id), None)

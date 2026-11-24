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
from collections import deque
from collections.abc import Mapping, Sequence
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

    def has_history(self, profile: str) -> bool:
        """Au moins une preuve tirée des publications (affichée « (publications) » sans vérification)."""
        return any(slot[0] == profile for slot in self._history)


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


# ---------------------------------------------------------------------------
# Priorité des groupes (§5.3), attribution (§5.4), équilibrage (§5.5)
# ---------------------------------------------------------------------------

FIRST_COUNTRY = "madagascar"  # = countries.LEGACY_COUNTRY : à égalité, Madagascar d'abord (comme --all-countries)


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str  # group_key
    country: str  # code pays
    rank: int  # rang dans share_groups.txt
    last_post_at: datetime | None  # dernière publication dans ce groupe (None = jamais)
    country_last: datetime | None  # dernière publication dans le pays (None = pays jamais servi)


def country_last_post(state: dict[str, dict[str, Any]]) -> datetime | None:
    """Dernière publication dans un pays : plus grand last_post_at de son suivi group_posts_<code>.json."""
    dates = [d for d in (_parse_dt(group.get("last_post_at")) for group in state.values() if group.get("last_post_at")) if d]
    return max(dates) if dates else None


def priority_key(candidate: Candidate) -> tuple[Any, ...]:
    """
    Ordre total (§5.3) : jamais publié d'abord, puis last_post_at le plus ancien, puis le pays qui attend depuis le plus
    longtemps (jamais servi d'abord), Madagascar, code du pays, rang. Comparaison sur timestamp() : jamais
    datetime.min, une date avec fuseau et une date sans fuseau ne se comparent pas.
    """
    def ts(value: datetime | None) -> float:
        return value.timestamp() if value else 0.0

    return (candidate.last_post_at is not None, ts(candidate.last_post_at),
            candidate.country_last is not None, ts(candidate.country_last),
            candidate.country != FIRST_COUNTRY, candidate.country, candidate.rank)


@dataclass(slots=True)
class Allocation:
    owner: dict[str, str]  # groupe servi -> profil
    unserved: list[str]  # groupes servables non servis (membres au quota), ordre de priorité
    loads: dict[str, int]  # profil -> groupes attribués, pour chaque profil de `order`


class _Pool:
    """État d'une attribution : charges, groupes tenus par chaque compte, membres dans l'ordre du fichier."""

    def __init__(self, groups: Sequence[str], members: Mapping[str, Sequence[str]], caps: Mapping[str, int | None],
                 order: Sequence[str], owner: dict[str, str | None] | None = None) -> None:
        self.rank = {profile: i for i, profile in enumerate(order)}
        self.pos = {group: i for i, group in enumerate(groups)}
        self.order = list(order)
        self.members = {g: sorted((a for a in members.get(g, ()) if a in self.rank), key=self.rank.__getitem__)
                        for g in groups}
        self.caps = {a: (float("inf") if caps.get(a) is None else caps[a]) for a in order}
        self.owner: dict[str, str | None] = dict(owner) if owner else {g: None for g in groups}
        self.load = {a: 0 for a in order}
        self.held: dict[str, set[str]] = {a: set() for a in order}
        for group, profile in self.owner.items():
            if profile is not None:
                self.load[profile] += 1
                self.held[profile].add(group)

    def held_least_priority_first(self, profile: str) -> list[str]:
        return sorted(self.held[profile], key=self.pos.__getitem__, reverse=True)

    def search(self, starts: list[str], accept: Any) -> tuple[str, dict[str, tuple[str, str] | None]] | None:
        """
        Recherche en largeur : compte -> groupe qu'il tient (le moins prioritaire d'abord) -> autre membre de ce groupe…
        jusqu'à un compte que `accept` retient. (compte trouvé, parents) ou None.
        """
        parent: dict[str, tuple[str, str] | None] = {a: None for a in starts}
        queue = deque(starts)
        while queue:
            current = queue.popleft()
            for group in self.held_least_priority_first(current):
                for other in self.members[group]:
                    if other in parent:
                        continue
                    parent[other] = (current, group)
                    if accept(other):
                        return other, parent
                    queue.append(other)
        return None

    def shift(self, end: str, parent: dict[str, tuple[str, str] | None]) -> str:
        """Déplace les groupes le long du chemin trouvé (chacun vers le compte suivant) ; renvoie le compte de départ."""
        current = end
        while parent[current] is not None:
            previous, group = parent[current]
            self.owner[group] = current
            self.held[previous].discard(group)
            self.held[current].add(group)
            current = previous
        return current

    def give(self, group: str, profile: str) -> None:
        self.owner[group] = profile
        self.held[profile].add(group)
        self.load[profile] += 1


def serve(groups: Sequence[str], members: Mapping[str, Sequence[str]], caps: Mapping[str, int | None],
          order: Sequence[str], *, use_paths: bool = True) -> dict[str, str | None]:
    """
    §5.4 : groupes pris par ordre de priorité ; membre libre le moins chargé (égalité : ordre du fichier), sinon
    chemin libérant (un membre au quota cède un groupe à un autre de ses membres qui a du quota, en cascade).
    `use_paths=False` : variante sans chemin (tests seulement, pour prouver que les tests détectent l'erreur).
    """
    pool = _Pool(groups, members, caps, order)
    for group in groups:
        candidates = pool.members[group]
        free = [a for a in candidates if pool.load[a] < pool.caps[a]]
        if free:
            pool.give(group, min(free, key=lambda a: (pool.load[a], pool.rank[a])))
            continue
        if not use_paths or not candidates:
            continue
        found = pool.search(candidates, lambda a: pool.load[a] < pool.caps[a])
        if found is None:
            continue
        end, parent = found
        start = pool.shift(end, parent)
        pool.owner[group] = start
        pool.held[start].add(group)
        pool.load[end] += 1  # les comptes intermédiaires cèdent un groupe et en reçoivent un
    return pool.owner


def balance(groups: Sequence[str], owner: dict[str, str | None], members: Mapping[str, Sequence[str]],
            caps: Mapping[str, int | None], order: Sequence[str]) -> dict[str, str | None]:
    """
    §5.5 : tant qu'un compte peut céder un groupe, directement ou en cascade, à un compte d'au moins 2 groupes de
    moins qui a du quota, on déplace. L'ensemble servi ne change pas.
    """
    pool = _Pool(groups, members, caps, order, owner)
    while True:
        for top in sorted(pool.order, key=lambda a: (-pool.load[a], pool.rank[a])):
            limit = pool.load[top] - 2
            found = pool.search([top], lambda a: pool.load[a] <= limit and pool.load[a] < pool.caps[a])
            if found is None:
                continue
            end, parent = found
            pool.shift(end, parent)
            pool.load[top] -= 1
            pool.load[end] += 1
            break
        else:
            return pool.owner


def allocate(groups: Sequence[str], members: Mapping[str, Sequence[str]], caps: Mapping[str, int | None],
             order: Sequence[str]) -> Allocation:
    """Attribution globale (serve) puis équilibrage (balance). `groups` : déjà triés par priorité."""
    owner = balance(groups, serve(groups, members, caps, order), members, caps, order)
    loads = {profile: 0 for profile in order}
    for profile in owner.values():
        if profile is not None:
            loads[profile] += 1
    return Allocation({g: p for g, p in owner.items() if p is not None},
                      [g for g in groups if owner[g] is None], loads)


# ---------------------------------------------------------------------------
# Signalements et textes (§5.6, §5.9)
# ---------------------------------------------------------------------------

RESTRICTION_TEXTS = {"suspended": "suspendu", "pending_limit": "limite de publications en attente"}
MAX_LISTED = 5  # groupes cités par ligne, puis « … »


def under_used(loads: Mapping[str, int]) -> list[str]:
    """Comptes dont la charge est nulle ou inférieure à la moitié de la moyenne (rien à répartir : aucun)."""
    total = sum(loads.values())
    if not loads or total == 0:
        return []
    half_mean = total / len(loads) / 2
    return [profile for profile, load in loads.items() if load == 0 or load < half_mean]


def _when(value: datetime) -> str:
    return f"{value:%d/%m/%Y %H:%M}"


def _listed(keys: Sequence[str]) -> str:
    shown = ", ".join(keys[:MAX_LISTED])
    return f"{shown}, …" if len(keys) > MAX_LISTED else shown


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = []
    for row in [header, *rows]:
        cells = [row[0].ljust(widths[0])] + [cell.rjust(widths[i]) for i, cell in enumerate(row) if i]
        lines.append("  ".join(cells).rstrip())
    return lines


def _quota_text(value: int | None) -> str:
    return "∞" if value is None or value == 0 else str(value)


def allocation_table(allocation: Allocation, accounts: Sequence[Account], caps: Mapping[str, int | None],
                     country_of: Mapping[str, str], country_names: Mapping[str, str]) -> str:
    """Tableau de répartition : groupes par compte et par pays, total, quota restant ; groupes servis par pays."""
    countries = [code for code in country_names if code in set(country_of.values())]
    header = ["Compte", *(country_names[c] for c in countries), "Total", "Quota restant"]
    rows = []
    for acc in accounts:
        mine = [country_of[g] for g, p in allocation.owner.items() if p == acc.profile]
        rows.append([acc.profile, *(str(mine.count(c)) for c in countries), str(len(mine)), _quota_text(caps.get(acc.profile))])
    served = [country_of[g] for g in allocation.owner]
    total = list(country_of.values())
    summary = " · ".join(f"{country_names[c]} {served.count(c)}/{total.count(c)}" for c in countries)
    return "\n".join([*_table(header, rows), f"Pays (servis / à publier) : {summary}"])


def membership_summary_text(accounts: Sequence[Account], keys_by_country: Mapping[str, list[str]],
                            memberships: Memberships, restrictions: Mapping[str, Restriction],
                            country_names: Mapping[str, str]) -> str:
    """Résumé des adhésions (--membership-summary) : par compte et par pays, groupes sans membre, restrictions."""
    codes = [code for code in keys_by_country if keys_by_country[code]]
    header = ["Compte", "Actif", "Quota", *(f"{country_names.get(c, c)} ({len(keys_by_country[c])})" for c in codes),
              "Dernière vérification"]
    rows = []
    for acc in accounts:
        cells = []
        for code in codes:
            statuses = [memberships.status(acc.profile, key) for key in keys_by_country[code]]
            cells.append(f"{statuses.count(MEMBER)} membre · {statuses.count(NOT_MEMBER)} non membre · "
                         f"{statuses.count(UNKNOWN)} ?")
        last = memberships.last_check(acc.profile)
        seen = _when(last) if last else "(publications)" if memberships.has_history(acc.profile) else "jamais"
        rows.append([acc.profile, "oui" if acc.active else "non", _quota_text(acc.quota), *cells, seen])
    active = [acc.profile for acc in accounts if acc.active]
    orphans = [key for code in codes for key in keys_by_country[code]
               if not any(memberships.status(profile, key) == MEMBER for profile in active)]
    lines = _table(header, rows)
    lines.append(f"Groupes sans membre confirmé parmi les comptes actifs : "
                 f"{f'{len(orphans)} ({_listed(orphans)})' if orphans else 'aucun'}")
    current = [f"{r.key} ({RESTRICTION_TEXTS.get(r.reason, r.reason)} jusqu'au {_when(r.until)}"
               f"{f', vu par {r.seen_by}' if r.seen_by else ''})" for r in sorted(restrictions.values(), key=lambda r: r.key)]
    lines.append(f"Restrictions de groupe en cours : {' ; '.join(current) if current else 'aucune'}")
    return "\n".join(lines)


def signals_text(unserved: Mapping[str, str], inactive: Mapping[str, str], stopped: Mapping[str, tuple[str, int]],
                 loads: Mapping[str, int], country_last: Mapping[str, datetime | None],
                 country_names: Mapping[str, str]) -> str:
    """Signalements (§5.6) : groupes non servis par raison, pays en attente, comptes inactifs / arrêtés / sous-utilisés."""
    by_reason: dict[str, list[str]] = {}
    by_country: dict[str, int] = {}
    for key, reason in unserved.items():
        by_reason.setdefault(reason, []).append(key)
        code = key.split("/", 1)[0]
        by_country[code] = by_country.get(code, 0) + 1
    waiting = []
    for code, count in by_country.items():
        last = country_last.get(code)
        since = f"en attente depuis le {last:%d/%m/%Y}" if last else "jamais servi"
        waiting.append(f"{country_names.get(code, code)} ({count} groupe(s), {since})")
    lines = ["Non servis : " + (" ; ".join(f"{reason} : {_listed(keys)}" for reason, keys in by_reason.items()) or "—"),
             "En attente : " + (" ; ".join(waiting) or "—"),
             "Comptes inactifs : " + (" ; ".join(f"{p} ({reason})" for p, reason in inactive.items()) or "—")]
    if stopped:
        lines.append("Arrêtés en cours de cycle : " + " ; ".join(
            f"{p} ({reason}) - {left} groupe(s) laissé(s) pour le cycle suivant" for p, (reason, left) in stopped.items()))
    lines.append("Sous-utilisés : " + (", ".join(f"{p} ({loads[p]})" for p in under_used(loads)) or "—"))
    return "\n".join(lines)


def account_id_conflict(ids: dict[str, str], profile: str, user_id: str) -> str | None:
    """Autre profil déjà connecté au même compte Facebook (cookie c_user), sinon None."""
    if not user_id:
        return None
    return next((other for other, known in ids.items() if other != profile and known == user_id), None)

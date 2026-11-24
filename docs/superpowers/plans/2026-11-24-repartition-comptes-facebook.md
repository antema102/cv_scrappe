# Pool de comptes Facebook pour `post_groups.py` — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** répartir chaque jour tous les groupes à publier de tous les pays entre un pool de comptes Facebook
(membres confirmés seulement, quota global par compte, charge équilibrée), avec une vérification des adhésions
séparée et en lecture seule, sans rien changer au fonctionnement actuel tant que le pool est désactivé.

**Architecture :** toute la logique de décision (comptes, quotas, adhésions, restrictions, priorité,
attribution, équilibrage, textes de rapport) va dans un module **pur** `facebook_script/account_pool.py`
(aucun Chrome, aucun import de `scraper`). `post_groups.py` garde son chemin actuel intact et gagne un verrou
de profil commun (`profile_session`), la vérification (`membership_status`, `check_memberships`) et
l'orchestration du pool (`run_pool`, `publish_account`) qui réutilise `plan()` et `run()` sans les modifier.

**Tech Stack :** Python 3.14 (stdlib : `dataclasses`, `json`, `unittest`, `itertools`), SeleniumBase 4.54
en mode CDP (existant). Aucune nouvelle dépendance.

**Spec :** `docs/superpowers/specs/2026-11-24-repartition-comptes-facebook-design.md` (révision 4,
validée le 24/11/2026). Les numéros §x.y renvoient à ce document ; les algorithmes 5.4 et 5.5 y sont décrits
pas à pas et ne sont pas recopiés ici.

## Global Constraints

- Mode pool ⇔ `facebook_script/share_accounts.json` contient au moins un compte `"active": true` **et**
  `--profile` n'est pas donné. Sinon : comportement actuel **identique au caractère près** (Task 1).
- Quota : `0` = **illimité** à tous les niveaux ; priorité `--max-groups-per-day` > `max_groups_per_day` du
  compte > `defaults.max_groups_per_day` > `0` ; quota **par compte**, tous pays confondus, jamais un total
  du pool ; négatif, non entier ou booléen → erreur au chargement.
- Adhésion : états `member`, `not_member`, `unknown` ; preuves « member » dans `history` : `posted`,
  `pending`, `unknown`, `suspended`, `pending_limit` ; preuve « not_member » : `not_member` ; preuve la plus
  récente gagnante (à égalité : la vérification) ; `unknown` et `not_member` ne reçoivent jamais de groupe.
- Restrictions **de groupe** (tout le pool) : `suspended`, `pending_limit` uniquement. Toutes les autres
  sont **de compte** (§5.2.1).
- `MEMBERSHIP_RECHECK_DAYS = 7` ; pause de vérification aléatoire 20 à 40 s (`CHECK_DELAY_MIN = 20`,
  `CHECK_DELAY_MAX = 40`) ; publication inchangée (55 à 65 s).
- Fichiers d'état (non suivis par git), dans `downloaded_files/facebook_share/` : `share_membership.json`,
  `share_account_ids.json` (ajout de ce plan, Review Focus 2), `.profile_<nom du profil>.lock`,
  `.share_membership.lock`.
- Profil interdit dans le pool : `my_custom_profile_facebook` (scraping).
- Inchangés : `share.py`, `visuals.py`, `scraper.py`, `pages.py`, `countries.py`, `ecosystem.config.js` ;
  dans `post_groups.py` : `plan`, `run`, `record`, `posts_today`, `post_to_group`, `open_composer`,
  `mark_publishing`, `recover_interrupted`, formats de `group_posts_<code>.json` et `account_block.json`.
- Code, commentaires, messages et logs **en français**, style du dépôt (`from __future__ import annotations`,
  dataclasses `slots=True`, annotations de types).
- Tests : `unittest` de la bibliothèque standard, **aucun** accès réseau ni Facebook, fichiers temporaires
  uniquement (`tempfile.TemporaryDirectory`) ; tout chemin d'état de `post_groups` patché vers ce dossier.
- Branche de travail `feature/pool-comptes-facebook` ; un commit par tâche, message en français terminé par
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Commandes de test

Depuis la racine du dépôt :

```bash
# Tous les tests du pool (~40 s, dont ~25 s de comparaisons exhaustives)
python -m unittest discover -s facebook_script -p "test_*pool*.py" -v
# Un fichier
python -m unittest discover -s facebook_script -p "test_account_pool.py" -v
# Un test
python -m unittest discover -s facebook_script -p "test_account_pool.py" -k test_quota_priority -v
```

Réussite = dernière ligne `OK` (aucun `FAIL` / `ERROR`).

## Review Focus

1. `share_accounts.json` cassé à la main (virgule en trop, clé mal orthographiée) sous PM2 → arrêt avec un
   message qui nomme le fichier et la ligne, code de sortie 1, **jamais** de repli silencieux sur l'ancien
   mode → test `test_invalid_json_names_file_and_line` (Task 2).
2. Même compte Facebook connecté dans deux dossiers de profil (dossier de profil copié pour « créer » un
   compte) → le second profil est refusé avec « même compte Facebook que … » ; sinon le quota et la
   protection anti-spam sont faussés → test `test_same_facebook_account_in_two_profiles_refused` (Task 7).
3. `share_membership.json` illisible (modifié à la main) → vérification et pool s'arrêtent avec message, le
   fichier n'est **jamais** réécrit → `test_load_membership_invalid_raises` (Task 3),
   `test_check_stops_on_invalid_membership_file` (Task 8), `test_pool_stops_on_invalid_membership_file`
   (Task 9).
4. Ctrl+C ou plantage au milieu d'une vérification → ce qui a été vérifié reste enregistré, JSON valide →
   `test_ctrl_c_keeps_checked_groups` (Task 8).
5. API des offres en échec pour un pays en mode pool → les autres pays sont publiés, code de sortie 1, le
   pays en échec garde sa priorité → `test_offers_api_failure_one_country` (Task 9).

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `facebook_script/account_pool.py` | **créé** — logique pure : comptes et quotas (Task 2), adhésions, restrictions, fichier d'adhésions, identité Facebook (Task 3), priorité, attribution, équilibrage (Task 4), signalements et textes (Task 6) |
| `facebook_script/share_accounts.json` | **créé** — `"accounts": []` + aide (Task 2) |
| `facebook_script/post_groups.py` | **modifié** — `profile_session` + identité du compte, utilisés aussi par `publish_country` et `login_only` (Task 7) ; options et vérification (Task 8) ; mode pool (Task 9) |
| `facebook_script/share_countries.json` | **modifié** — aide seulement (Task 10) |
| `facebook_script/test_pool_legacy_compat.py` | **créé** — non-régression du mode actuel, sorties de référence (Task 1) |
| `facebook_script/test_data/pool_legacy/` | **créé** — offres fictives, listes de groupes, réglages, sorties de référence (Task 1) |
| `facebook_script/test_account_pool.py` | **créé** — tests de la logique pure (Tasks 2, 3, 4, 6) |
| `facebook_script/test_pool_optimality.py` | **créé** — recherche exhaustive et flot de référence (Task 5) |
| `facebook_script/test_post_groups_pool.py` | **créé** — faux navigateur : verrous, vérification, orchestration (Tasks 7, 8, 9) |
| `CLAUDE.md` | **modifié** — section `post_groups.py` (Task 10) |
| `docs/superpowers/specs/…-design.md` | statut « validée » (Task 10) |

Note : la spec citait deux fichiers de test ; ils sont scindés en quatre (même motif `test_*pool*.py`) pour
isoler les tests lents (Task 5) et la non-régression (Task 1).

## Ordre et dépendances

```
Task 1 (non-régression, code actuel intact)
  └─ Task 2 (comptes, quotas) ─ Task 3 (adhésions, restrictions) ─ Task 4 (attribution) ─┬─ Task 5 (optimalité)
                                                                                         └─ Task 6 (textes)
Task 7 (verrou de profil)  ← dépend de Task 1 (doit garder la référence) et Task 3 (identités)
Task 8 (vérification)      ← Tasks 2, 3, 6, 7
Task 9 (mode pool)         ← Tasks 2 à 8
Task 10 (documentation)    ← Task 9
Task 11 (validation réelle sur Facebook, manuelle) ← Task 10 ; condition d'activation du pool
```

Task 1 **doit** être faite et commitée avant toute modification de `post_groups.py` : ses sorties de
référence sont produites par le code actuel.

---

### Task 1 : filet de non-régression du mode actuel

**Files :**
- Create : `facebook_script/test_pool_legacy_compat.py`
- Create : `facebook_script/test_data/pool_legacy/offers.json`, `groups_madagascar.txt`,
  `groups_cameroun.txt`, `groups_ghana.txt`, `settings.json`, `excluded_owners.txt` (vide)
- Create (générés) : `facebook_script/test_data/pool_legacy/expected_preview_madagascar.txt`,
  `expected_preview_all.txt`, `expected_run_madagascar.json`

**Interfaces :**
- Consumes : `post_groups.main()` et le code **actuel**, sans modification.
- Produces : `LegacyHarness` (dans le fichier de test) réutilisé par Task 9 :
  `run_main(argv: list[str], accounts_file: Path | None = None) -> tuple[str, dict]` (sortie console,
  état normalisé), et les trois fichiers `expected_*`.

- [ ] **Step 1 : créer la branche**

```bash
git checkout -b feature/pool-comptes-facebook
```

- [ ] **Step 2 : données fictives** (jamais de vraies offres ni de vrais groupes)

`offers.json` : 60 offres fictives, 45 Madagascar (`companyCountry: "Madagascar"`), 10 Cameroun, 5 Ghana ;
champs lus par `share.py` : `id`, `name`, `slug`, `status: "active"`, `isPublic: true`,
`moderationStatus: "approve"`, `createdAt` (ISO, distincts), `expiresAt: "2027-06-30T00:00:00Z"`,
`proprietaire: {"name": "Entreprise Fictive N"}`, `lieu`, `type`, `companyCountry`. Listes de groupes :
4 liens `https://www.facebook.com/groups/test<pays><n>/` par pays. `settings.json` :
`{"defaults": {}, "countries": {"cameroun": {"min_offers": 5}, "ghana": {"min_offers": 3}}}`.

- [ ] **Step 3 : écrire le harnais et les trois tests**

Le harnais patche (via `unittest.mock.patch`) : `share.fetch_offers` (renvoie les offres du fichier dont
`companyCountry` vaut le nom demandé, et leur nombre), `share.OUTPUT_DIR`, `post_groups.LOCK_FILE`,
`post_groups.ACCOUNT_BLOCK_FILE`, `post_groups.DEBUG_DIR` → dossier temporaire ; `post_groups.date` → une
sous-classe de `date` dont `today()` renvoie `date(2026, 12, 1)` ; `post_groups.list_file` → fichiers de
`test_data` (dossier temporaire inexistant pour les autres pays) ; `post_groups.active_countries` →
`[madagascar, cameroun, ghana]` ; `post_groups.ACCOUNTS_FILE` → `accounts_file` ou un chemin inexistant
(`create=True` : l'attribut n'existe pas encore) ; `sys.argv`. Sortie console capturée par
`redirect_stdout`. Pour l'exécution réelle : `post_groups.SB` → faux contexte, `fb._activate_cdp_mode`,
`fb.close_orphan_chromes`, `fb._reset_profile_exit_state` → sans effet, `fb.is_logged_in` → `True`,
`post_groups.post_to_group` → faux qui enregistre `(profil, url, sha1(texte), nb d'affiches)` et renvoie
dans l'ordre `posted`, `pending`, `not_member`, `posted` ; `random.seed(12345)` avant `main()`. État
normalisé = contenu des `group_posts_*.json` avec toute valeur de date (`at`, `posted_at`, `last_post_at`)
remplacée par `"<date>"`.

```python
class LegacyCompatTest(unittest.TestCase):
    def test_preview_madagascar_identical(self):
        out, _ = run_main(["--country", "madagascar", "--groups-file", G_MDG, "--settings-file", SETTINGS,
                           "--excluded-owners-file", EXCLUDED, "--preview", "--no-images"])
        self.assertEqual(out, read("expected_preview_madagascar.txt"))

    def test_preview_all_countries_identical(self):   # commande PM2 en aperçu
        out, _ = run_main(["--all-countries", "--settings-file", SETTINGS, "--excluded-owners-file", EXCLUDED,
                           "--preview", "--no-images"])
        self.assertEqual(out, read("expected_preview_all.txt"))

    def test_run_madagascar_identical(self):          # publication réelle avec faux navigateur
        out, state = run_main(["--country", "madagascar", "--groups-file", G_MDG, "--settings-file", SETTINGS,
                               "--excluded-owners-file", EXCLUDED, "--no-images"])
        self.assertEqual({"stdout": out, "calls": CALLS, "state": state}, read_json("expected_run_madagascar.json"))
```

Si la variable d'environnement `UPDATE_GOLDEN=1` est définie, chaque test **écrit** son fichier `expected_*`
au lieu de comparer.

- [ ] **Step 4 : produire les références avec le code actuel**

Run : `UPDATE_GOLDEN=1 python -m unittest discover -s facebook_script -p "test_pool_legacy_compat.py" -v`
Expected : `OK`, trois fichiers `expected_*` créés ; les relire : aucune vraie donnée, sortie lisible.

- [ ] **Step 5 : vérifier qu'ils passent sans régénération**

Run : `python -m unittest discover -s facebook_script -p "test_pool_legacy_compat.py" -v`
Expected : `OK` (3 tests). À partir d'ici, **interdit** de relancer avec `UPDATE_GOLDEN=1` : un écart est une
régression à corriger dans le code, pas dans la référence.

- [ ] **Step 6 : commit** (avec la spec et ce plan)

```bash
git add docs/superpowers facebook_script/test_pool_legacy_compat.py facebook_script/test_data/pool_legacy
git commit -m "post_groups : référence de non-régression du mode actuel avant le pool de comptes"
```

---

### Task 2 : comptes et quotas

**Files :**
- Create : `facebook_script/account_pool.py`, `facebook_script/share_accounts.json`,
  `facebook_script/test_account_pool.py`

**Interfaces :**
- Produces :

```python
SCRAPING_PROFILE = "my_custom_profile_facebook"
ACCOUNT_KEYS = ("profile", "label", "active", "max_groups_per_day")

class AccountsError(ValueError): ...           # message en français, nomme le fichier

@dataclass(frozen=True, slots=True)
class Account:
    profile: str    # nom du dossier = identité dans history[].profile, posts_today, account_block.json
    path: Path      # chemin absolu (relatif -> relatif à project_root)
    label: str      # défaut : profile
    active: bool
    quota: int      # priorité déjà appliquée ; 0 = illimité

def load_accounts(path: Path, project_root: Path, cli_quota: int | None = None) -> list[Account]
def pool_enabled(accounts: list[Account]) -> bool
def remaining_cap(quota: int, posted_today: int) -> int | None   # None = illimité
```

- [ ] **Step 1 : tests (`class AccountsTest`)**

```python
def test_missing_file_gives_empty_list(self): assertEqual(load_accounts(tmp / "absent.json", root), [])
def test_shipped_file_is_empty_and_pool_off(self):
    accounts = load_accounts(SCRIPT_DIR / "share_accounts.json", root); assertEqual(accounts, []); assertFalse(pool_enabled(accounts))
def test_quota_priority(self):   # defaults 10 ; A : 15 ; B : rien
    assertEqual([a.quota for a in load_accounts(f, root)], [15, 10])
    assertEqual([a.quota for a in load_accounts(f, root, cli_quota=4)], [4, 4])   # 4 chacun, pas 4 au total
    assertEqual([a.quota for a in load_accounts(f, root, cli_quota=0)], [0, 0])
def test_quota_zero_means_unlimited(self): assertIsNone(remaining_cap(0, 50))
def test_remaining_cap(self): assertEqual(remaining_cap(5, 3), 2); assertEqual(remaining_cap(5, 7), 0)
def test_pool_enabled_only_with_active_account(self)   # tous inactifs -> False ; un actif -> True
def test_relative_profile_resolved_against_project_root(self)
def test_bom_accepted(self)                             # fichier écrit en utf-8-sig
def test_errors(self):   # chacun -> AccountsError dont le message contient l'élément fautif
    # clé inconnue "quota" ; même dossier deux fois ("p" et "./p") ; "my_custom_profile_facebook" ;
    # max_groups_per_day = -1, "5", 2.5, True ; cli_quota=-1
def test_invalid_json_names_file_and_line(self):        # Review Focus 1
    f.write_text('{"accounts": [,]}'); with assertRaises(AccountsError) as ctx: load_accounts(f, root)
    assertIn("share_accounts.json", str(ctx.exception)); assertIn("ligne 1", str(ctx.exception))
```

- [ ] **Step 2 :** Run `python -m unittest discover -s facebook_script -p "test_account_pool.py" -v` →
  Expected : `ModuleNotFoundError: account_pool` / échecs.
- [ ] **Step 3 : implémenter** `load_accounts`, `pool_enabled`, `remaining_cap` dans `account_pool.py`
  (lecture `utf-8-sig` ; `json.JSONDecodeError.lineno` pour le message ; clés commençant par `_` ignorées
  comme dans `share_countries.json`) et créer `share_accounts.json` :

```json
{
  "_aide": [
    "Pool de comptes de post_groups.py : actif dès qu'un compte a \"active\": true (sinon fonctionnement actuel).",
    "profile : dossier du profil Chrome (relatif à la racine du projet) ; jamais my_custom_profile_facebook (scraping).",
    "max_groups_per_day : publications par jour et par compte, tous pays confondus ; 0 = illimité.",
    "Pour arrêter un compte : \"active\": false (jamais 0). --max-groups-per-day l'emporte pour tous les comptes.",
    "Connexion : python facebook_script/post_groups.py --login --account <profil>",
    "Adhésions : python facebook_script/post_groups.py --check-membership --account <profil>"
  ],
  "defaults": {"max_groups_per_day": 0},
  "accounts": []
}
```

- [ ] **Step 4 :** relancer → `OK`. Relancer aussi Task 1 → `OK`.
- [ ] **Step 5 : commit** — `post_groups : comptes du pool et quotas (share_accounts.json)`

---

### Task 3 : adhésions, restrictions, fichier d'adhésions, identité Facebook

**Files :** Modify `facebook_script/account_pool.py` ; Test `facebook_script/test_account_pool.py`

**Interfaces :**
- Consumes : `Account` (Task 2).
- Produces :

```python
MEMBER, NOT_MEMBER, UNKNOWN = "member", "not_member", "unknown"
MEMBER_HISTORY_STATUSES = ("posted", "pending", "unknown", "suspended", "pending_limit")
GROUP_RESTRICTIONS = ("suspended", "pending_limit")
MEMBERSHIP_RECHECK_DAYS = 7

def group_key(code: str, group_id: str) -> str            # "madagascar/506924959474464"

@dataclass(frozen=True, slots=True)
class Evidence:
    status: str         # MEMBER | NOT_MEMBER
    at: datetime        # avec fuseau
    source: str         # "history" | "check"

class MembershipFileError(ValueError): ...
def load_membership(path: Path) -> dict[str, dict[str, dict[str, Any]]]   # absent -> {} ; illisible -> erreur
def merge_account_membership(data: dict, profile: str, entries: dict[str, dict[str, Any]]) -> dict
    # copie de data où seules les entrées `entries` de `profile` sont remplacées

class Memberships:
    def __init__(self, states: dict[str, dict[str, dict[str, Any]]],     # code pays -> group_posts_<code>.json
                 fallback_profiles: dict[str, str],                      # code pays -> profil des entrées sans profil
                 membership: dict[str, dict[str, dict[str, Any]]]) -> None
    def evidence(self, profile: str, key: str) -> Evidence | None
    def status(self, profile: str, key: str) -> str
    def needs_check(self, profile: str, key: str, now: datetime, recheck: bool = False) -> bool
    def last_check(self, profile: str) -> datetime | None

@dataclass(frozen=True, slots=True)
class Restriction:
    key: str; reason: str; until: datetime; seen_by: str; source: str     # source : "publication" | "check"

def group_restrictions(states: dict[str, dict[str, dict[str, Any]]], membership: dict, now: datetime) -> dict[str, Restriction]
    # restrictions DE GROUPE actives seulement : group["blocked"] (raison dans GROUP_RESTRICTIONS, until > now)
    # et entrées de vérification avec "restriction" non expirée, quel que soit le profil

def account_id_conflict(ids: dict[str, str], profile: str, user_id: str) -> str | None
    # profil déjà enregistré avec le même identifiant Facebook (autre que `profile`), sinon None
```

- [ ] **Step 1 : tests (`class MembershipsTest`, `class RestrictionsTest`)** — dates avec fuseau
  `+03:00` ; `G = group_key("madagascar", "g1")`.

```python
def test_history_posted_is_member(self)                    # history [{status posted, profile P}] -> MEMBER
def test_history_entry_without_profile_uses_country_profile(self)  # fallback_profiles={"madagascar": "H"} -> status("H", G) == MEMBER
def test_check_unknown_is_not_evidence(self)               # membership {P: {G: {status unknown}}} -> UNKNOWN, evidence None
def test_no_composer_or_fill_failed_is_not_evidence(self)  # history no_composer / fill_failed / account_limited -> UNKNOWN
def test_most_recent_evidence_wins(self):
    # check not_member 25/11 10:00 puis history posted 26/11 -> MEMBER
    # history posted 24/11 puis check not_member 25/11 -> NOT_MEMBER
    # même instant -> la vérification gagne
def test_needs_check(self):   # now = 01/12
    # UNKNOWN -> True ; MEMBER -> False ; NOT_MEMBER du 28/11 -> False ; du 23/11 -> True ; recheck=True -> True
def test_last_check(self)                                  # plus grand checked_at du profil, None sans vérification
def test_group_restrictions(self):
    # blocked suspended until futur -> Restriction(source="publication") ; until passé -> absent
    # check restriction pending_limit vue par P -> Restriction(seen_by=P, source="check") ; expirée -> absent
def test_account_level_statuses_never_restrict_group(self) # history not_member / account_limited de P -> {}
def test_merge_account_membership_keeps_other_profiles(self)
def test_load_membership_invalid_raises(self)              # Review Focus 3 : '{"P": ' -> MembershipFileError, fichier inchangé
def test_account_id_conflict(self):
    assertEqual(account_id_conflict({"A": "100"}, "B", "100"), "A"); assertIsNone(account_id_conflict({"A": "100"}, "A", "100"))
```

- [ ] **Step 2 :** Run → échecs attendus (`ImportError` sur les nouveaux noms).
- [ ] **Step 3 : implémenter** (dates : `datetime.fromisoformat`, entrées illisibles ignorées ;
  `history` d'un groupe = `states[code][group_id]["history"]`).
- [ ] **Step 4 :** Run `test_account_pool.py` → `OK`.
- [ ] **Step 5 : commit** — `post_groups : adhésions (trois états), restrictions de groupe, fichier d'adhésions`

---

### Task 4 : priorité, attribution (§5.4), équilibrage (§5.5)

**Files :** Modify `facebook_script/account_pool.py` ; Test `facebook_script/test_account_pool.py`

**Interfaces :**
- Produces :

```python
@dataclass(frozen=True, slots=True)
class Candidate:
    key: str                      # group_key
    country: str                  # code pays
    rank: int                     # rang dans share_groups.txt
    last_post_at: datetime | None
    country_last: datetime | None # dernière publication dans le pays

def country_last_post(state: dict[str, dict[str, Any]]) -> datetime | None   # max des last_post_at du suivi
def priority_key(c: Candidate) -> tuple
    # (last_post_at is not None, ts(last_post_at), country_last is not None, ts(country_last),
    #  c.country != "madagascar", c.country, c.rank)  avec ts(x) = x.timestamp() ou 0.0
    # (jamais datetime.min : comparer une date avec fuseau et une date sans fuseau lève TypeError)

@dataclass(slots=True)
class Allocation:
    owner: dict[str, str]      # groupe servi -> profil
    unserved: list[str]        # groupes servables non servis (membres au quota), ordre de priorité
    loads: dict[str, int]      # profil -> groupes attribués, pour CHAQUE profil de `order`

def serve(groups: Sequence[str], members: Mapping[str, Sequence[str]], caps: Mapping[str, int | None],
          order: Sequence[str], *, use_paths: bool = True) -> dict[str, str | None]      # §5.4
def balance(groups: Sequence[str], owner: dict[str, str | None], members: Mapping[str, Sequence[str]],
            caps: Mapping[str, int | None], order: Sequence[str]) -> dict[str, str | None]  # §5.5
def allocate(groups: Sequence[str], members: Mapping[str, Sequence[str]], caps: Mapping[str, int | None],
             order: Sequence[str]) -> Allocation                                         # serve puis balance
```

`groups` est déjà trié par priorité ; `caps[p] is None` = illimité ; `members[g]` peut être dans n'importe
quel ordre (parcouru dans l'ordre de `order`). Règles de parcours de la spec : comptes de départ et membres
dans l'ordre de `order` ; groupes d'un compte du **moins** prioritaire au **plus** prioritaire (dans `serve`
comme dans `balance`) ; `balance` : comptes triés par charge décroissante puis ordre de `order`, on
recommence depuis le début après chaque déplacement.

- [ ] **Step 1 : tests (`class AllocationTest`)** — résultats exacts calculés par la simulation de
  vérification de la spec (§5.5.1, §8). Notation : groupes `"<pays>/<n>"`, comptes `"A"`, `"B"`, …

```python
def test_three_accounts_three_countries(self):   # M30 S20 C10, tous membres, sans quota
    al = allocate(...); assertEqual(al.loads, {"A": 20, "B": 20, "C": 20})
    assertEqual(per_country(al), {"A": {"madagascar": 10, "senegal": 7, "cameroun": 3},
                                  "B": {"madagascar": 10, "senegal": 7, "cameroun": 3},
                                  "C": {"madagascar": 10, "senegal": 6, "cameroun": 4}})
def test_different_memberships_per_country(self):   # A : Madagascar ; B : Sénégal + Cameroun ; C : tout
    assertEqual(per_country(al), {"A": {"madagascar": 20}, "B": {"senegal": 18, "cameroun": 2},
                                  "C": {"madagascar": 10, "senegal": 2, "cameroun": 8}})
def test_ten_accounts_twenty_countries(self):   # 5 groupes par pays, tous membres
    # sans quota : 100 servis, loads tous à 10 ; quota 5 : servis = les 50 groupes des 10 premiers pays
    # dans l'ordre de priorité, unserved = les 50 autres, loads tous à 5
def test_one_account_ten_countries(self):        # 3 groupes par pays ; sans quota -> 30 ; cap 15 -> 15 premiers
def test_global_quota_across_countries(self):    # A cap 5 membre de 3 pays -> loads["A"] <= 5 ; cap 2 -> 2
def test_freeing_path(self):                     # A cap 1 {g1,g2}, B cap 1 {g2}, ordre g2, g1
    assertEqual(allocate(["g2", "g1"], {"g2": ["A", "B"], "g1": ["A"]}, {"A": 1, "B": 1}, ["A", "B"]).owner,
                {"g2": "B", "g1": "A"})
def test_no_ceiling_historic_alone(self):        # H membre des 67, 8 comptes sans adhésion
    assertEqual(al.loads["H"], 67); assertTrue(all(al.loads[p] == 0 for p in others))
def test_partial_members(self):                  # H 67, B 10, C 3 (parmi les 10) -> 57 / 7 / 3
def test_unknown_never_assigned_even_at_quota(self)   # membres au quota -> unserved, jamais un non-membre
def test_priority_key_order(self):               # jamais publié < publié ; ancien < récent ; pays jamais servi d'abord ;
                                                 # Madagascar puis code ; rang ; dates avec fuseau sans TypeError
def test_deterministic(self):                    # 20 appels identiques -> même owner
```

`class RotationTest.test_three_cycles` : scénario §8 de la spec, simulé dans le test (filtre 20 h,
`priority_key`, `allocate`, publications à 8 h 00, 8 h 05… dans l'ordre d'exécution §5.7 : comptes dans
l'ordre, dans un compte pays par priorité de leur premier groupe puis groupes par priorité ; T1 sans membre
exclu). Assertions exactes :

```python
EXPECTED = [  # (ordre de priorité, A, B, non servis)
    ("S1 S2 S3 M3 M4 M1 M2 C1 C2", ["S1", "M3", "M4"], ["S2", "S3"], ["M1", "M2", "C1", "C2"]),
    ("M1 M2 C1 C2 S1 M3 M4 S2 S3", ["M1", "M2", "S1"], ["C1", "C2"], ["M3", "M4", "S2", "S3"]),
    ("M3 M4 S2 S3 M1 M2 S1 C1 C2", ["M3", "M4", "M1"], ["S2", "S3"], ["M2", "S1", "C1", "C2"]),
]
# + les 9 groupes servis au moins une fois sur les cycles 1-2 ; T1 signalé « sans membre » à chaque cycle ;
#   aucun groupe de Madagascar pour B ; loads A <= 3, B <= 2
```

- [ ] **Step 2 :** Run → échecs attendus.
- [ ] **Step 3 : implémenter** `country_last_post`, `priority_key`, `serve`, `balance`, `allocate` selon
  §5.4 et §5.5 (recherche en largeur, file `collections.deque`).
- [ ] **Step 4 :** Run `test_account_pool.py` → `OK`.
- [ ] **Step 5 : commit** — `post_groups : attribution globale des groupes et équilibrage de la charge`

---

### Task 5 : optimalité contre recherche exhaustive et flot de référence

**Files :** Create `facebook_script/test_pool_optimality.py`

**Interfaces :** Consumes `serve`, `balance`, `allocate` (Task 4). Les références ci-dessous sont écrites
**dans le test**, indépendamment de `account_pool` (ne rien en importer d'autre que les trois fonctions).

- [ ] **Step 1 : écrire les références et générateurs**
  - `exhaustive(n_acc, members, caps)` : `itertools.product` sur `[None] + membres` de chaque groupe, quotas
    respectés ; renvoie (masque de l'ensemble servi le meilleur par priorité — groupe 0 = bit de poids fort —,
    charge max minimale, vecteur des charges trié décroissant minimal, somme des carrés minimale pour ce
    masque, nombre maximal de groupes servis).
  - `min_square_flow(n_acc, members, caps, served)` : flot à coût minimum (plus courts chemins successifs,
    Bellman-Ford / SPFA) source → groupe (1) → membre (1) → puits avec, par compte, des arcs unitaires de
    coût 1, 3, 5, … (k-ième groupe : 2k − 1) jusqu'au quota ; renvoie (flot, coût = somme des carrés) ;
    `min_max_flow` : plus petite borne T telle que tout `served` passe avec des quotas `min(cap, T)`.
  - `random_case(rng)` : 1 à 4 comptes, 1 à 7 groupes, probabilité d'adhésion dans {0,2 ; 0,4 ; 0,6 ; 0,8 ;
    1,0}, groupe sans membre → un membre tiré au hasard, quotas dans `[None, 0, 1, 1, 2, 2, 3, 4]`.
  - `country_case(rng, max_acc=5, max_groups=8)` : 2 à `max_acc` comptes, 1 à 3 pays, 2 à `max_groups`
    groupes triés par pays, adhésion par pays avec probabilité 0,6, compte 0 membre de tout une fois sur
    deux, trou par groupe avec probabilité 0,15, quotas dans `[None, None, 0, 1, 2, 3, 5]`.
- [ ] **Step 2 : tests**

```python
def test_random_cases_match_exhaustive(self):      # graines 1, 2, 3 ; 3 000 cas chacune
    # pour chaque cas : masque servi == ref ; nb servi == ref ; max(loads) == ref ; sorted(loads, reverse) == ref ;
    # somme des carrés == ref ; aucun quota dépassé ; aucun groupe à un non-membre
def test_country_cases_match_exhaustive(self)      # graine 7 ; 1 500 cas country_case(rng)
def test_large_cases_match_flow(self)              # suite de la graine 7 ; 400 cas country_case(rng, 8, 40)
    # nb servi == flot max de tous les groupes ; max(loads) == min_max_flow ; somme des carrés == min_square_flow
def test_degraded_variants_are_caught(self):       # le test prouve qu'il détecte une erreur
    # sur les cas des graines 1-3 : serve(..., use_paths=False) donne un mauvais ensemble servi au moins une fois ;
    # serve(...) sans balance donne une charge max trop haute au moins une fois
```

- [ ] **Step 3 :** Run `python -m unittest discover -s facebook_script -p "test_pool_optimality.py" -v`
  Expected : `OK` (4 tests, ~25 s). Mesuré lors de la vérification de la spec : 0 écart ; variantes dégradées
  en faute dans 486 et 222 cas.
- [ ] **Step 4 : commit** — `post_groups : tests d'optimalité de la répartition (exhaustif et flot)`

---

### Task 6 : signalements et textes

**Files :** Modify `facebook_script/account_pool.py` ; Test `facebook_script/test_account_pool.py`

**Interfaces :**
- Consumes : `Account`, `Memberships`, `Restriction`, `Allocation`.
- Produces :

```python
def under_used(loads: Mapping[str, int]) -> list[str]
    # profils dont la charge < moitié de la moyenne des charges de `loads`, ou nulle ; ordre de `loads`
def allocation_table(allocation: Allocation, accounts: Sequence[Account], caps: Mapping[str, int | None],
                     country_of: Mapping[str, str], country_names: Mapping[str, str]) -> str
def membership_summary_text(accounts: Sequence[Account], keys_by_country: Mapping[str, list[str]],
                            memberships: Memberships, restrictions: Mapping[str, Restriction],
                            country_names: Mapping[str, str]) -> str
def signals_text(unserved: Mapping[str, str], inactive: Mapping[str, str], stopped: Mapping[str, tuple[str, int]],
                 loads: Mapping[str, int], country_last: Mapping[str, datetime | None],
                 country_names: Mapping[str, str]) -> str
    # unserved : groupe -> raison (« aucun compte membre confirmé », « membres au quota »,
    #   « restriction de groupe jusqu'au jj/mm/aaaa hh:mm », « API du pays illisible ») ;
    # inactive : profil -> raison (5.1) ; stopped : profil -> (raison, groupes laissés pour le cycle suivant) ;
    # pays partiellement servis avec « en attente depuis le jj/mm/aaaa » (country_last) ; sous-utilisés (under_used)
```

Formats : ceux de §5.9 (colonnes alignées ; quota `∞` pour 0 / illimité ; cellule d'adhésion
`"<n> membre · <n> non membre · <n> ?"` ; dernière vérification `jj/mm/aaaa hh:mm`, `(publications)` si
seules des preuves de publication, `jamais` sinon ; lignes « Groupes sans membre confirmé parmi les comptes
actifs : … » et « Restrictions de groupe en cours : … »).

- [ ] **Step 1 : tests (`class ReportsTest`)**

```python
def test_under_used(self):
    assertEqual(under_used({"H": 57, "B": 7, "C": 3}), ["B", "C"]); assertEqual(under_used({"A": 20, "B": 20}), [])
    assertEqual(under_used({"A": 5, "B": 0}), ["B"])
def test_allocation_table(self)          # 3 comptes / 3 pays (Task 4) : lignes "10 7 3 20 ∞", ligne « Pays : Madagascar 30/30 servis · … »
def test_membership_summary_text(self)   # "65 membre · 0 non membre · 2 ?", "(publications)", "jamais", restriction affichée avec sa date
def test_signals_text(self)              # chaque raison de groupe non servi, compte inactif, compte arrêté « 4 groupe(s) laissé(s)
                                         # pour le cycle suivant », pays « en attente depuis le 29/11/2026 », sous-utilisés
```

- [ ] **Step 2 :** Run → échecs ; **Step 3 :** implémenter ; **Step 4 :** Run → `OK`.
- [ ] **Step 5 : commit** — `post_groups : tableau de répartition et résumé des adhésions`

---

### Task 7 : verrou de profil et identité du compte (`post_groups.py`)

**Files :** Modify `facebook_script/post_groups.py` (`publish_country` l. 1511-1531, `login_only` l. 1463-1487,
nouvelles fonctions près de `open_profile` l. 1455) ; Create `facebook_script/test_post_groups_pool.py`

**Interfaces :**
- Consumes : `fb.run_lock`, `fb.RunAlreadyActive`, `open_profile`, `account_pool.account_id_conflict`.
- Produces :

```python
ACCOUNT_IDS_FILE = share.OUTPUT_DIR / "share_account_ids.json"       # {profil: identifiant c_user}

class ProfileBusy(StopRun):            # scope "profile" ; message « profil <nom> occupé (publication ou vérification en cours) »
    ...
def profile_lock_path(profile_dir: Path) -> Path        # share.OUTPUT_DIR / f".profile_{profile_dir.name}.lock" (lu à l'appel)
@contextmanager
def profile_session(profile_dir: Path, headless: bool) -> Iterator[SB]
    # 1. fb.run_lock(profile_lock_path(...)) — RunAlreadyActive -> ProfileBusy
    # 2. open_profile(profile_dir)  3. with SB(uc=True, locale="fr", user_data_dir=..., headless=headless) as sb
    # 4. fb._activate_cdp_mode(sb) ; yield sb  — sortie : Chrome fermé PUIS verrou relâché, aussi sur exception
def facebook_user_id(sb: SB) -> str                     # valeur du cookie c_user (lu comme fb._session_active), "" si absent
def check_account_identity(profile: str, sb: SB) -> None
    # lit ACCOUNT_IDS_FILE ; conflit -> StopRun(f"même compte Facebook que le profil {autre} : ...") ;
    # sinon enregistre {profile: id} (écriture atomique) ; id vide -> rien
```

`publish_country` : même affichage qu'aujourd'hui, `open_profile` + `with SB(...)` + `_activate_cdp_mode`
remplacés par `with profile_session(settings.profile, args.headless) as sb:` (ProfileBusy est un StopRun :
la gestion existante de `run_countries` s'applique). `login_only` : `profile_session(profile, headless=False)`,
ProfileBusy → message et profil suivant. `check_account_identity` n'est **pas** appelé dans le mode actuel
(seulement pool, vérification, `--login --account`, Tasks 8-9).

- [ ] **Step 1 : tests (`class ProfileSessionTest`)** — faux `SB` (contexte qui journalise `sb_enter` /
  `sb_exit` et, à `sb_exit`, essaie de prendre le verrou du profil et journalise le résultat), patchs de
  `post_groups.SB`, `fb._activate_cdp_mode`, `fb.close_orphan_chromes`, `fb._reset_profile_exit_state`,
  `share.OUTPUT_DIR` → dossier temporaire.

```python
def test_lock_taken_before_open_and_released_after_close(self):
    # journal == ["open_profile", "sb_enter", "inside", "sb_exit(verrou encore tenu)"], puis verrou libre
def test_lock_held_inside_session(self)          # dans le with : fb.run_lock(path) lève RunAlreadyActive
def test_busy_profile_raises_profile_busy(self)  # verrou pris à l'avance -> ProfileBusy, SB jamais ouvert
def test_lock_released_after_exception(self)     # AccountLimited levé dedans -> Chrome fermé puis verrou libre
def test_publish_country_busy_profile(self)      # verrou pris -> StopRun, aucun post_to_group
def test_login_only_skips_busy_profile(self)     # message « occupé », profil suivant traité
def test_same_facebook_account_in_two_profiles_refused(self):   # Review Focus 2
    # ACCOUNT_IDS_FILE {"A": "100"} ; profil B lit c_user "100" -> StopRun contenant "même compte Facebook que le profil A"
def test_identity_recorded(self)                 # profil A, c_user "100", fichier vide -> {"A": "100"}
```

- [ ] **Step 2 :** Run `test_post_groups_pool.py` → échecs.
- [ ] **Step 3 : implémenter.**
- [ ] **Step 4 :** Run `test_post_groups_pool.py` → `OK` ; **Run Task 1** → `OK` (non-régression : la
  sortie du mode actuel ne change pas).
- [ ] **Step 5 : commit** — `post_groups : verrou par profil Chrome tenu jusqu'à la fermeture de Chrome`

---

### Task 8 : options, vérification des adhésions, résumé

**Files :** Modify `facebook_script/post_groups.py` (`parse_args` l. 1385-1452, `main` l. 1627-1648,
nouvelles fonctions) ; Test `facebook_script/test_post_groups_pool.py`

**Interfaces :**
- Consumes : Tasks 2, 3, 6, 7.
- Produces :

```python
ACCOUNTS_FILE = fb.SCRIPT_DIR / "share_accounts.json"
MEMBERSHIP_FILE = share.OUTPUT_DIR / "share_membership.json"
MEMBERSHIP_LOCK = share.OUTPUT_DIR / ".share_membership.lock"
MEMBERSHIP_LOCK_WAIT = 5          # s : essais toutes les 0,2 s puis erreur
CHECK_DELAY_MIN, CHECK_DELAY_MAX = 20, 40

@dataclass(slots=True)
class MembershipResult:
    status: str                   # account_pool.MEMBER | NOT_MEMBER | UNKNOWN
    detail: str                   # "composer" | "restriction" | "join_button" | "no_composer" | "load_error"
    numeric_id: str = ""
    restriction: GroupRestricted | None = None

def membership_status(sb: SB, group_url: str) -> MembershipResult     # §5.8, lecture seule
def save_membership(profile: str, entries: dict[str, dict[str, Any]]) -> None
    # verrou court MEMBERSHIP_LOCK, load_membership, merge_account_membership, share._write_json_atomic
def check_memberships(accounts: list[Account], targets: dict[str, list[fb.GroupTarget]],
                      all_settings: dict[str, CountrySettings], args: argparse.Namespace) -> int
def print_membership_summary(accounts: list[Account], targets: dict[str, list[fb.GroupTarget]],
                             all_settings: dict[str, CountrySettings], args: argparse.Namespace) -> None
```

Options (`parse_args`) :
- `--accounts-file` (défaut `ACCOUNTS_FILE`), `--account PROFIL` (répétable), `--check-membership`,
  `--recheck`, `--membership-summary`.
- `--delay-min` / `--delay-max` : défaut `None`, résolus après lecture : vérification → 20 / 40, sinon
  55 / 65 (valeurs actuelles) ; contrôle `delay_max >= delay_min` après résolution (message inchangé).
- Exclusions (`parser.error`) : `--check-membership`, `--membership-summary`, `--login`, `--preview` deux à
  deux ; `--account` avec `--profile`.
- `--check-membership` / `--membership-summary` sans option de pays → `active_countries("share_groups")` ;
  `--group-url` accepté avec un seul pays (pour la validation réelle, Task 11).
- `main()` : `account_pool.load_accounts(Path(args.accounts_file), fb.PROJECT_ROOT, args.max_groups_per_day)`
  (`AccountsError` → `[ERREUR] …` et code 1) ; `--account` inconnu → erreur ; puis résumé / vérification
  avant le reste ; `--login` avec `--account` → connexion de ces comptes (avec `check_account_identity`) ;
  le reste de `main()` inchangé dans cette tâche.

`membership_status` : `sb.goto`, attente `PAGE_LOAD_WAIT`, page de connexion → `StopRun` ; `GROUP_ID_JS` ;
boucle jusqu'à `COMPOSER_TIMEOUT` sur `COMPOSER_JS` **sans jamais appeler `_click`** (bouton vu → `member` /
`composer`) ; tant que le bouton est absent, `detect_restriction` → `member` / `restriction` ; à la fin,
« Rejoindre » vu → `not_member` / `join_button`, sinon `unknown` / `no_composer` + `save_diagnostics` ;
exception de chargement → `unknown` / `load_error`.

`check_memberships` : comptes = `--account` ou tous les comptes listés (actifs ou non) ; groupes à visiter
= `Memberships.needs_check(...)`, au plus `--max-groups` par compte ; `profile_session` +
`fb.is_logged_in` (non connecté → message `--login --account`, compte suivant) + `check_account_identity` ;
pause 20-40 s entre deux visites ; `save_membership` **après chaque groupe** ; `ProfileBusy` → compte suivant ;
`MembershipFileError` → arrêt, code 1, fichier non réécrit ; fin : `membership_summary_text` du compte.
Aucun verrou `.post_groups.lock`. N'écrit jamais `group_posts_<code>.json`.

- [ ] **Step 1 : tests (`class MembershipStatusTest`, `class CheckMembershipsTest`, `class CliTest`)** —
  faux `sb` qui n'a **que** `goto` et `sleep` (tout autre attribut lève `AttributeError`), et
  `post_groups._click`, `_press`, `_type_lines`, `fill_composer` patchés pour lever `AssertionError("clic
  interdit")` ; `fb._run_js`, `fb._is_auth_wall`, `fb._page_state` scriptés.

```python
def test_composer_is_member(self)            # COMPOSER_JS -> {"join": False, "x": 1, "y": 1} -> MEMBER/"composer"
def test_join_only_is_not_member(self)       # {"join": True} jusqu'au délai -> NOT_MEMBER/"join_button"
def test_nothing_is_unknown(self)            # {} -> UNKNOWN/"no_composer", save_diagnostics appelé
def test_banner_is_member_with_restriction(self)   # RESTRICTION_JS « suspendu … jusqu'au 5 nov 2026, 06:47 » -> MEMBER, restriction.reason "suspended"
def test_auth_wall_stops(self)               # -> StopRun
def test_load_error_is_unknown(self)         # goto lève -> UNKNOWN/"load_error"
def test_numeric_id_recorded(self)           # GROUP_ID_JS {"id": "506", "source": "meta"} -> numeric_id "506"
# (COMPOSER_TIMEOUT patché à 0,05 s, sb.sleep sans effet)

def test_visits_only_needed_groups(self)     # member : pas visité ; not_member du 28/11 : non ; du 20/11 : oui ; unknown : oui
def test_recheck_and_max_groups(self)        # --recheck : tous ; --max-groups 2 : 2 visites
def test_inactive_account_can_be_checked(self)
def test_group_posts_untouched(self)         # sha256 des group_posts_*.json identique avant / après
def test_busy_profile_skipped(self)          # verrou du profil tenu -> message, compte suivant vérifié
def test_ctrl_c_keeps_checked_groups(self)   # Review Focus 4 : KeyboardInterrupt à la 3e visite -> 2 entrées, JSON valide
def test_check_stops_on_invalid_membership_file(self)   # Review Focus 3 : code 1, fichier identique octet pour octet
def test_save_membership_keeps_other_profiles(self)
def test_delay_defaults(self)                # parse_args : vérification 20/40, publication 55/65, valeurs explicites gardées
def test_option_conflicts(self)              # --check-membership --preview -> SystemExit ; --account X --profile Y -> SystemExit
def test_unknown_account_rejected(self)
def test_summary_prints_table(self)          # --membership-summary : aucun Chrome (SB patché pour lever), tableau imprimé
```

- [ ] **Step 2 :** Run → échecs ; **Step 3 :** implémenter.
- [ ] **Step 4 :** Run `test_post_groups_pool.py` → `OK` ; **Run Task 1** → `OK`.
- [ ] **Step 5 : commit** — `post_groups : vérification des adhésions en lecture seule et résumé`

---

### Task 9 : publication en mode pool

**Files :** Modify `facebook_script/post_groups.py` (nouvelles fonctions après `run_countries` l. 1534 ;
`main`) ; Test `facebook_script/test_post_groups_pool.py`, `facebook_script/test_pool_legacy_compat.py`

**Interfaces :**
- Consumes : tout ce qui précède ; `plan`, `run`, `posts_today`, `recover_interrupted`, `load_targets`,
  `_account_block_until`, `_save_account_block`, `posters_for`, `CountrySettings` (inchangés).
- Produces :

```python
@dataclass(slots=True)
class PoolJob:
    job: Job
    key: str                   # group_key
    settings: CountrySettings  # réglages du pays (profil remplacé par celui du compte à l'exécution)

def active_accounts(accounts: list[Account], all_settings: dict[str, CountrySettings],
                    args: argparse.Namespace, today: date) -> tuple[list[Account], dict[str, int | None], dict[str, str]]
    # (comptes actifs dans l'ordre du fichier, cap par profil, raison par profil inactif)
    # raisons : "désactivé", "non choisi (--account)", "jamais connecté", "pause jusqu'au jj/mm/aaaa hh:mm", "quota atteint"
def publish_account(account: Account, batches: list[tuple[CountrySettings, list[Job], dict[str, dict[str, Any]]]],
                    args: argparse.Namespace, today: date, claims: dict[str, Owner], totals: Counter[str]) -> None
def run_pool(accounts: list[Account], selected: list[CountrySettings], all_settings: dict[str, CountrySettings],
             args: argparse.Namespace) -> int
```

Déroulement de `run_pool` (§5.3 à §5.7) :
1. Avertissements : profil historique d'un pays choisi absent du pool ; `profile` / `max_groups_per_day`
   renseignés dans `share_countries.json` (ignorés).
2. `active_accounts` ; aucun actif utile → message, code 0.
3. `load_targets` (claims) ; suivi de chaque pays chargé **une seule fois**, `recover_interrupted` (écrit
   sauf `--preview`) ; `load_membership` (`MembershipFileError` → arrêt, code 1) ; `Memberships` avec
   `fallback_profiles[code] = all_settings[code].profile.name` ; `group_restrictions`.
4. Pour chaque pays : offres (mêmes messages que `run_countries`, échec → pays ignoré, code 1) ;
   `plan(targets, offers, state, settings, args_sans_max_groups, today, claims, quota=None)` où
   `args_sans_max_groups = argparse.Namespace(**{**vars(args), "max_groups": 0})` ; `Candidate` par Job ;
   tri `priority_key` ; troncature `--max-groups` par pays dans cet ordre.
5. Exclusion des groupes à restriction de vérification active (sauf `--retry-blocked`) et des groupes sans
   membre confirmé actif (signalés) ; `members[key]` = actifs `MEMBER`.
6. `allocate(groups triés, members, caps, ordre des comptes)` ; `allocation_table` + `signals_text` (§5.6).
7. `--preview` : textes et affiches par groupe (comme `run_countries`), **rien écrit**, code 0.
8. Exécution sous `fb.run_lock(LOCK_FILE)` (déjà pris par `main`) : comptes dans l'ordre du fichier, ceux
   qui ont des groupes ; pour chacun, `batches` = pays par priorité de leur premier groupe, Jobs par
   priorité ; pause habituelle entre deux comptes ; `publish_account`.
9. `AccountLimited` → `_save_account_block`, compte arrêté ; `ProfileBusy` / `StopRun(scope="profile")` →
   compte arrêté ; `StopRun(scope="all")` / Ctrl+C → fin ; **jamais** de réattribution des groupes restants.
10. Récapitulatif par compte et par pays, puis `signals_text` avec les comptes arrêtés en cours de cycle
    (raison, nombre de groupes laissés) ; code de sortie.

`publish_account` : `with profile_session(account.path, args.headless) as sb` (§5.7, verrou tenu jusqu'à la
fermeture de Chrome) ; connexion (même règle que `publish_country` : sans terminal ou sans fenêtre → StopRun
« reconnectez avec `--login --account` ») ; `check_account_identity` ; puis pour chaque lot
`run(sb, jobs, state, dataclasses.replace(settings, profile=account.path), args, today, totals, claims)`,
avec la pause habituelle avant chaque lot sauf le premier.

`main()` : si `pool_enabled(accounts)` et pas `--profile` → `run_pool` (sous `.post_groups.lock` sauf
`--preview`), sinon chemin actuel inchangé.

- [ ] **Step 1 : tests (`class PoolRunTest`)** — faux `SB` qui compte les ouvertures par profil, faux
  `post_to_group` scriptable par (profil, groupe), offres fictives de Task 1, tous les chemins d'état patchés.

```python
def test_limited_account_groups_not_transferred(self):
    # A, B, C membres ; A lève AccountLimited à son 3e groupe (Sénégal, après 2 groupes de Madagascar)
    # -> groupes restants de A absents de tous les history ; B et C publient exactement leurs groupes prévus ;
    #    account_block.json contient A ; au cycle suivant A inactif et ses groupes non tentés passent en tête
def test_one_chrome_per_account_multi_country(self):
    # A a des groupes à Madagascar, au Cameroun et au Ghana -> 1 ouverture de SB pour A ;
    # chaque entrée d'history est dans le suivi de son pays avec profile "A" ; texte du Ghana en anglais
def test_lock_held_between_run_calls(self)          # depuis le faux post_to_group du 2e et du 3e pays : verrou de A occupé
def test_busy_profile_skipped_not_redistributed(self)   # verrou de B tenu -> B sauté ; ses groupes publiés par personne
def test_not_member_no_retry_same_cycle(self)       # not_member pour A dans g -> 1 seule tentative ; au cycle suivant g -> B
def test_suspended_blocks_group_for_all(self)       # suspended pour A dans g -> blocked écrit ; cycle suivant g non attribué à B
def test_crash_then_restart(self)                   # marqueur publishing laissé -> relance : groupe compté publié, non refait
def test_quota_counts_all_countries(self)           # A quota 3, 2 publications du jour au Sénégal -> 1 groupe au plus
def test_offers_api_failure_one_country(self)       # Review Focus 5 : Cameroun en échec -> code 1, Madagascar publié, Cameroun en tête au cycle suivant
def test_pool_stops_on_invalid_membership_file(self)    # Review Focus 3 : code 1, aucun Chrome, fichier inchangé
def test_preview_writes_nothing(self)               # sha256 de tous les fichiers d'état identiques avant / après
def test_max_groups_by_priority(self)               # --max-groups 1 -> le groupe le plus prioritaire de chaque pays
def test_check_restriction_respected(self)          # restriction de vérification active -> groupe exclu ; --retry-blocked -> inclus
def test_profile_option_forces_legacy(self)         # comptes actifs + --profile -> run_countries appelé, pas run_pool
def test_warns_historic_profile_missing(self)
```

Dans `test_pool_legacy_compat.py`, ajouter :

```python
def test_inactive_accounts_keep_legacy_output(self):   # comptes listés, tous "active": false
    for argv, expected in GOLDEN_CASES:                # les 3 scénarios de Task 1
        out, state = run_main(argv, accounts_file=INACTIVE_ACCOUNTS); assertEqual(..., expected)
```

- [ ] **Step 2 :** Run → échecs ; **Step 3 :** implémenter.
- [ ] **Step 4 :** Run `python -m unittest discover -s facebook_script -p "test_*pool*.py" -v` → `OK`
  (tous les fichiers, dont la non-régression).
- [ ] **Step 5 : commit** — `post_groups : publication en mode pool (répartition globale, un Chrome par compte)`

---

### Task 10 : documentation et vérification finale

**Files :** Modify `CLAUDE.md`, `facebook_script/share_countries.json` (`_aide`), spec (statut).

- [ ] **Step 1 : `CLAUDE.md`**, section `post_groups.py` : remplacer « **au plus un compte par pays** (pas de
  rotation de comptes pour passer sous les limites de Facebook : refusé) » par le mode pool (activation,
  répartition globale, adhésions confirmées, restrictions de compte / de groupe, aucun transfert pendant le
  cycle, verrou de profil, quotas `0` = illimité) ; commandes `--check-membership`, `--membership-summary`,
  `--account`, `--login --account` ; fichiers d'état ; commande de test ; ce qui reste non vérifié sur le
  vrai Facebook (jusqu'à Task 11).
- [ ] **Step 2 : `share_countries.json`** : ajouter à `_aide` « En mode pool (share_accounts.json avec un
  compte actif), profile et max_groups_per_day sont ignorés : profil et quota viennent du compte. »
- [ ] **Step 3 : spec** : statut « validée (révision 4), implémentée ».
- [ ] **Step 4 : vérification complète**
  Run : `python -m unittest discover -s facebook_script -p "test_*pool*.py" -v` → `OK`.
  Run : `python facebook_script/post_groups.py --membership-summary` → tableau (aucun compte listé : message
  « aucun compte dans share_accounts.json »), aucun Chrome.
  Run : `python facebook_script/post_groups.py --preview --no-images --all-countries` → même forme de sortie
  qu'avant la branche (mode actuel, fichier de comptes vide) ; aucun fichier d'état modifié
  (`git status` propre hors `downloaded_files`).
- [ ] **Step 5 : commit** — `post_groups : documentation du pool de comptes`

---

### Task 11 : validation réelle de la détection des adhésions (manuelle, avant activation)

Condition **obligatoire** avant de passer un compte du pool à `"active": true`. Se fait avec l'accord
explicite de l'utilisateur, sur son poste, fenêtre visible. Aucune publication possible : la vérification
ne clique jamais (Task 8).

- [ ] **Step 1 : préparer l'échantillon** (avec l'utilisateur) : un nouveau compte `X`, connecté par
  `python facebook_script/post_groups.py --login --account <X>` ; 6 groupes de `share_groups.txt` :
  2 dont X est membre, 2 groupes **publics** dont X n'est pas membre, 2 groupes privés dont X n'est pas
  membre. L'utilisateur note la vérité terrain en ouvrant chaque groupe à la main dans le profil X.
- [ ] **Step 2 : lancer la vérification sur l'échantillon**

```bash
python facebook_script/post_groups.py --check-membership --account <X> --country madagascar \
  --group-url <g1> --group-url <g2> --group-url <g3> --group-url <g4> --group-url <g5> --group-url <g6> \
  --recheck --visible
```

  Expected : 2 `member`, 4 `not_member` ; en particulier les 2 groupes publics **ne** donnent **pas**
  `member` (hypothèse non vérifiée de la spec §9 : un non-membre ne voit pas « Exprimez-vous… »).
- [ ] **Step 3 : même chose avec le compte historique** sur 3 groupes où il a déjà publié → 3 `member`.
- [ ] **Step 4 : décision**
  - 100 % conforme → noter dans `CLAUDE.md` « détection des adhésions vérifiée sur le vrai Facebook le
    jj/mm/aaaa (6 + 3 groupes) » ; commit `post_groups : détection des adhésions vérifiée sur Facebook`.
  - Un écart → **ne pas activer le pool** ; captures `downloaded_files/facebook_share/debug/` analysées,
    correction de `membership_status` (nouvelle tâche, avec test reproduisant le cas), puis on refait Task 11.

---

## Non-régression quand le pool est désactivé (demande 4)

- **Référence figée avant toute modification** (Task 1) : sortie console de `--preview` (un pays et
  `--all-countries`, commande PM2) et exécution complète avec faux navigateur (appels de publication +
  suivis normalisés), produites par le code actuel et commitées.
- **Rejouée à chaque tâche** qui touche `post_groups.py` (Tasks 7, 8, 9, 10) : égalité **au caractère
  près**, interdiction de régénérer la référence.
- **Trois états « pool désactivé » couverts** : fichier de comptes absent, fichier livré vide
  (`"accounts": []`), comptes listés mais tous inactifs (Task 9) ; plus `--profile` qui force l'ancien mode.
- **Seul changement assumé du chemin actuel** : le verrou de profil dans `publish_country` / `login_only` ;
  sans conflit de profil la sortie est identique (référence), avec conflit le profil est sauté au lieu de
  faire planter Chrome (`test_publish_country_busy_profile`).
- **En production** (phase 0 ci-dessous) : comparer les journaux PM2 `logs/fb-share-groups.out.log` d'un
  jour avant et d'un jour après le déploiement (mêmes lignes `[INFO] … publication(s) prévue(s)`, même
  récapitulatif par pays).

## Précautions (demande 5)

**Publications en double**
- `.post_groups.lock` inchangé : un seul lancement de publication à la fois (pool ou non).
- Un groupe = un seul compte par cycle : invariant de `allocate` (Tasks 4, 5) ; suivi de chaque pays chargé
  **une fois** et le même objet passé aux comptes successifs (pas de relecture qui écraserait une
  publication du compte précédent) ; délai de 20 h, offres déjà publiées et `claims` (même groupe sous deux
  adresses / dans deux pays) appliqués par `plan()` inchangé, tous comptes confondus.
- `mark_publishing` avant le clic et `recover_interrupted` au chargement, aussi en mode pool
  (`test_crash_then_restart`).
- Même compte Facebook dans deux profils refusé (Review Focus 2).

**Conflits de profils**
- Verrou de profil pris avant toute action, tenu jusqu'à la fermeture de Chrome, pour publication,
  vérification et connexion ; jamais d'attente (aucun blocage mutuel) ; fermeture des Chrome orphelins
  seulement après prise du verrou.
- Profil de scraping interdit dans le pool ; profils en double refusés au chargement.

**Pertes de données**
- Toutes les écritures par `share._write_json_atomic` (fichier temporaire puis remplacement).
- `share_membership.json` : écrit par la seule vérification, sous verrou court, relu puis fusionné (seules
  les entrées du compte vérifié changent) ; la publication n'y touche pas ; la vérification n'écrit jamais
  `group_posts_<code>.json`.
- Fichier illisible (`share_accounts.json`, `share_membership.json`) → arrêt avec message, **jamais
  réécrit** (Review Focus 1 et 3).
- Écriture après chaque groupe (vérification et publication) : Ctrl+C ou plantage ne perd que le groupe en
  cours (Review Focus 4).
- Formats de `group_posts_<code>.json` et `account_block.json` inchangés : l'ancien code relit tout.

## Mise en service progressive et retour arrière (demande 6)

| Phase | Action | Critère pour passer à la suite |
|---|---|---|
| 0. Déploiement neutre | Fusion de la branche ; `share_accounts.json` livré vide ; PM2 inchangé | 2 cycles de 8 h : journaux identiques à ceux d'avant (section ci-dessus) |
| 1. Comptes | Créer chaque nouveau profil **vide** (jamais en copiant un dossier de profil connecté) ; `--login --account <profil>` ; lister avec `"active": false` | `--membership-summary` liste les comptes, pool toujours éteint |
| 2. Validation réelle | Task 11 | 100 % conforme |
| 3. Adhésions | Rejoindre les groupes à la main ; `--check-membership --account <profil>` pour chaque compte, y compris l'historique (ajouté en `"active": false`) | `--membership-summary` : adhésions attendues, aucun « même compte Facebook » |
| 4. Activation | Hors du créneau de 8 h : tous les comptes `"active": true`, puis `--preview --all-countries` | Tableau de répartition conforme ; sinon remettre `"active": false` |
| 5. Premier cycle pool | Cycle de 8 h suivant ; lire le récapitulatif par compte et par pays | Aucun `account_limited` inattendu, aucun groupe publié deux fois |
| 6. Régime normal | `--membership-summary` après chaque ajout de groupe ou de compte ; `--check-membership` pour les nouveaux groupes | — |

Option prudente pour la phase 5 (choix de l'utilisateur, non imposé par la spec) : quota modeste par nouveau
compte les premiers jours (`max_groups_per_day`), relevé ensuite.

**Retour arrière**, du plus léger au plus lourd :
1. **Immédiat** : tous les comptes à `"active": false` (ou `"accounts": []`) → le cycle suivant reprend
   exactement l'ancien fonctionnement (couvert par les tests de Task 9).
2. **Pour un lancement** : `--profile my_custom_profile_facebook_share` force l'ancien mode.
3. **Un seul compte** : `"active": false` sur ce compte ; ses groupes passent aux autres membres au cycle
   suivant.
4. **Code** : `git revert` de la fusion ; aucune migration — les suivis gardent leur format (entrées
   `history[].profile` déjà lues par l'ancien code), `share_membership.json` et `share_account_ids.json`
   sont simplement ignorés.

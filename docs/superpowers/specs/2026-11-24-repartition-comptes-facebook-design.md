# Répartition des comptes Facebook entre les pays (`post_groups.py`)

Date : 24/11/2026 — statut : **à valider** (aucun code modifié).

- Révision 2 : adhésions vérifiées sans publier (option A) ; plus de plafond ⌈groupes / comptes⌉.
- Révision 3 : un compte peut publier dans plusieurs pays pendant le même cycle ; répartition globale des
  groupes de tous les pays ; plus de limite du nombre de pays au nombre de comptes ; rotation déduite de
  l'ancienneté des groupes ; un Chrome par compte pour tous ses pays.
- Révision 4 :
  - §5.4 / §5.5 : garanties **démontrées** et **vérifiées** contre une recherche exhaustive (§5.5.1),
    ordre de déplacement fixé pour le déterminisme ;
  - §5.2 : portée des restrictions — **compte** ou **groupe** — distinguée (§5.2.1) ;
  - §5.3 : rotation vérifiée sur 3 cycles, effet connu du délai de 20 h ;
  - §5.7 : verrou de profil tenu de l'ouverture à la fermeture de Chrome, entre les appels à `run()` ;
  - §5.1 / §6 : quotas — `0` = illimité, priorité entre ligne de commande, compte et valeur par défaut ;
  - §8 : tests correspondants.

## 1. Intention

Aujourd'hui `post_groups.py` publie avec **un compte par pays**, fixé dans `share_countries.json`
(`profile`). L'utilisateur veut un **pool de comptes Facebook** qui se partage, à chaque cycle, **tous les
groupes à publier de tous les pays** ayant un `pays/<code>/share_groups.txt`, pour que **chaque compte ait à
peu près le même nombre de groupes, tous pays confondus**, en ne publiant que là où il est membre confirmé
et dans la limite de son quota. Un compte peut recevoir des groupes de Madagascar, du Sénégal et du
Cameroun le même jour. Le pays d'origine d'un compte n'est pas une restriction.

## 2. Objectifs, non-objectifs, anti-objectifs

**Objectifs**
- **Répartition globale** : chaque groupe à publier, quel que soit son pays, va à **un seul** compte ; la
  charge totale des comptes (tous pays confondus) est équilibrée autant que les adhésions et les quotas le
  permettent.
- Un compte ne reçoit **que des groupes dont il est membre confirmé** ; une adhésion inconnue n'est jamais
  supposée.
- **Quota global par compte** (publications du jour dans tous les pays) ; `0` = illimité.
- Le nombre de pays servis n'est **pas limité** par le nombre de comptes : 10 comptes membres des groupes de
  20 pays couvrent les 20 pays dans le même cycle si les quotas le permettent.
- **Rotation** quand tout ne peut pas être servi (aucun membre, quotas épuisés) : les groupes et les pays
  qui attendent depuis le plus longtemps passent en premier au cycle suivant.
- Adhésions vérifiées **sans publier**, par une commande séparée, résultats gardés dans un JSON persistant.
- Le compte historique continue de publier normalement pendant que les nouveaux comptes sont vérifiés.
- Comptes sous-utilisés, comptes inactifs, groupes et pays non servis **signalés**.

**Non-objectifs**
- Rejoindre des groupes automatiquement (reste manuel, à la main dans Chrome).
- Publier avec plusieurs comptes en parallèle (un Chrome de publication à la fois, comme aujourd'hui).
- Changer les textes, affiches, la publication anonyme, la détection des blocages, `share.py`, `visuals.py`.
- Ajouter des pays hors d'Afrique (l'exemple « France » de l'utilisateur n'est pas dans `countries.py`, qui
  ne connaît que les 54 pays d'Afrique : les exemples et tests utilisent des codes existants).
- Quota total pour l'ensemble du pool (non demandé ; seuls existent le quota par compte et `--max-groups`
  par pays).

**Anti-objectifs (règles de l'utilisateur, 24/11/2026)**
- Un compte limité par Facebook **ne voit pas ses groupes restants passer à un autre compte** pendant le
  cycle, dans aucun pays ; les autres comptes continuent.
- Une limitation **individuelle** d'un compte ne bloque pas les autres comptes dans un groupe (§5.2.1).
- Un compte qui ne peut pas accéder à un groupe est signalé ; rien n'est recalculé avant le cycle suivant.
- La vérification ne publie jamais, ne demande jamais d'adhésion, ne contourne aucune restriction.
- Aucun plafond artificiel ⌈groupes / comptes⌉ quand les autres comptes ne sont pas membres.
- Aucune protection existante affaiblie : quota par compte, pause de 24 h, 20 h entre deux publications
  dans un groupe (tous comptes confondus), offres jamais republiées dans un groupe, même groupe sous deux
  adresses / dans deux pays, verrou d'un seul lancement de publication.

## 3. Contraintes constatées dans le code

- Un compte = un **profil Chrome** connecté à la main (`--login`). L'identité d'un compte dans les suivis est
  déjà le nom du dossier de profil (`history[].profile`, `posts_today`, `account_block.json`).
- `posts_today(profil)` compte déjà les publications du jour (`posted`, `pending`, `unknown`, `publishing`,
  date locale du jour) **dans les suivis de tous les pays** : le quota global par compte existe déjà.
- « share_groupe » = fichiers `pays/<code>/share_groups.txt` (pas de table). Le 24/11/2026 : seul Madagascar
  a des groupes (67) et l'API WipWork ne renvoie que des offres de Madagascar.
- Le suivi `group_posts_<code>.json` est **par groupe**, indépendant du compte : le délai de 20 h, le
  dédoublonnage des offres et le blocage d'un groupe (`blocked`) s'appliquent déjà à tous les comptes. Son
  `history` n'est jamais tronqué et enregistre le profil qui a publié (entrées anciennes sans profil =
  profil du pays).
- `run()` publie une liste de groupes d'**un** pays (texte, langue, fichier de suivi du pays) avec un Chrome
  déjà ouvert : on peut l'appeler plusieurs fois, un pays après l'autre, dans le même Chrome.
- `open_composer` **clique** sur « Exprimez-vous… » : la vérification ne peut pas le réutiliser tel quel.
  `COMPOSER_JS` (lecture du bouton, de « Rejoindre »), `RESTRICTION_JS`, `GROUP_ID_JS` et
  `fb._is_auth_wall` se lisent sans clic.
- `not_member` n'est déduit que si « Exprimez-vous… » est absent ET « Rejoindre » visible (un « Rejoindre »
  seul est souvent celui d'un groupe suggéré).
- `fb.run_lock(path)` : verrou exclusif de fichier (msvcrt / fcntl), non bloquant (`RunAlreadyActive` si
  déjà pris), libéré par le système même si le processus plante.

## 4. Approches envisagées

| Approche | Principe | Écartée / retenue |
|---|---|---|
| Autant de comptes par pays | ⌊comptes / pays⌋ par pays | Écartée : 5 comptes pour 3 groupes au Sénégal |
| Comptes → pays (un pays par compte), à la charge | révision 2 | Remplacée : un compte ne pouvait pas couvrir plusieurs pays ; 10 comptes ne servaient que 10 pays |
| **Répartition globale des groupes, membres confirmés** | chaque groupe de chaque pays → un membre confirmé, charge totale équilibrée, quotas globaux | **Retenue par l'utilisateur (révision 3)** |
| Attribution au fil de l'eau (sans chemins) | chaque groupe au membre libre le moins chargé, sans échange | Écartée : mesuré, se trompe sur l'ensemble servi dans ~5 % des cas aléatoires et sur la charge max dans ~2,5 % (§5.5.1) |
| Adhésion apprise en publiant | essayer, noter `not_member` | Écartée : groupes non servis pendant des jours, compte historique bridé |
| Adhésion déclarée à la main | fichier compte → groupes | Écartée : fastidieux (67 groupes par compte) |
| Rotation de comptes sur limite | basculer sur un autre compte quand l'un est limité | Refusée : contourne l'anti-spam de Facebook |

## 5. Algorithme

Un **cycle** = un lancement de publication (`--all-countries` sous PM2, chaque jour à 8 h).

### 5.1 Comptes actifs et quotas

Liste lue dans `facebook_script/share_accounts.json`. Un compte est **actif pour ce cycle** si :
`active: true`, dossier de profil existant, pas en pause dans `account_block.json`, quota du jour non
épuisé. Les autres sont signalés avec leur raison (« désactivé », « jamais connecté », « pause jusqu'au … »,
« quota atteint »).

**Quota d'un compte** (`max_groups_per_day`, publications par jour **et par compte**, tous pays confondus) :

| Source (de la plus forte à la plus faible) | Effet |
|---|---|
| `--max-groups-per-day N` en ligne de commande | remplace le quota de **chaque** compte pour ce lancement ; `N = 0` → illimité pour tous |
| `max_groups_per_day` du compte dans `share_accounts.json` | quota de ce compte |
| `defaults.max_groups_per_day` de `share_accounts.json` | comptes sans valeur propre |
| constante `DEFAULT_MAX_GROUPS_PER_DAY` | `0` |

- **`0` = quota illimité**, à tous les niveaux (même sens qu'aujourd'hui dans `share_countries.json`). Pour
  empêcher un compte de publier : `"active": false`, jamais `0`.
- Valeur négative ou non entière → arrêt au démarrage avec message.
- Le quota est **par compte**, jamais un total pour le pool, et jamais par pays.
- `cap(a)` = quota restant aujourd'hui : ∞ si le quota vaut 0 ; sinon quota − `posts_today(profil)`
  (publications du jour dans **tous** les pays, date locale de la machine). `cap(a) ≤ 0` → compte inactif
  (« quota atteint »).
- En mode pool, `max_groups_per_day` de `share_countries.json` est ignoré (avertissement s'il est
  renseigné). Sans mode pool, rien ne change : quota par profil lu dans `share_countries.json`.
- `--max-groups N` reste « au plus N groupes **par pays** et par lancement » (0 = tous).

### 5.2 Adhésions : trois états

Pour un compte `a` et un groupe `g` (ligne `<code pays>/<group_id>` de `share_groups.txt`), l'état est donné
par la **preuve la plus récente** parmi deux sources, chacune écrite par un seul programme :

| Source | Écrite par | Preuve « member » | Preuve « not_member » |
|---|---|---|---|
| `share_membership.json` | la vérification (5.8) uniquement | bouton « Exprimez-vous… » vu, ou bandeau de restriction | « Rejoindre » vu sans « Exprimez-vous… » |
| `history` de `group_posts_<code>.json` | la publication, inchangée | `posted`, `pending`, `unknown`, `suspended`, `pending_limit` | `not_member` |

- Pas de preuve, ou dernière vérification non concluante (`no_composer`, page non chargée) → **`unknown`**.
- `unknown` et `not_member` ne sont **jamais** traités comme membres.
- Le compte historique a déjà ses preuves dans `history` (toutes ses publications réussies) : il est
  membre confirmé de ces groupes sans aucune vérification.
- `membres(g)` = comptes actifs membres confirmés de `g`, moins ceux qu'une restriction **de compte**
  écarte (§5.2.1) ; vide si une restriction **de groupe** est active.

#### 5.2.1 Portée des restrictions : compte ou groupe

| Situation | Détectée par | Portée | Effet |
|---|---|---|---|
| Compte non membre (`not_member`) | vérification, publication | **compte** | ce compte ne reçoit plus ce groupe ; les autres membres oui |
| Limite anti-spam du compte (`AccountLimited`, « Nous limitons le nombre de fois que vous pouvez publier… ») | publication | **compte** (tous ses groupes) | arrêt du compte ; pause de 24 h (`account_block.json`) ; ses groupes restants **non transférés pendant le cycle**, répartis normalement entre les comptes actifs au cycle suivant |
| Déconnexion, page de connexion, vérification de sécurité | publication, vérification | **compte** | arrêt du compte pour ce lancement ; groupes non transférés ; `--login --account` |
| Quota atteint, profil occupé | 5.1, 5.7 | **compte** | compte sauté ; groupes non transférés |
| Échec technique (`no_composer`, `fill_failed`, `not_published`) | publication | aucune restriction | le groupe reste à publier ; réattribué au cycle suivant (éventuellement au même compte) |
| **Suspension dans le groupe** (« Votre profil a été suspendu dans ce groupe … jusqu'au … », `suspended`) | publication, vérification | **groupe** (tout le pool) | groupe donné à aucun compte jusqu'à `until` |
| **Limite de publications en attente dans le groupe** (`pending_limit`) | publication, vérification | **groupe** (tout le pool) | groupe donné à aucun compte jusqu'à `until` (3 jours) |
| Même groupe sous deux adresses / dans deux pays | publication, chargement | **groupe** | règles existantes inchangées |

Pourquoi `suspended` et `pending_limit` restent au niveau du groupe, bien que le bandeau nomme un seul
profil : ce sont des décisions de **modération du groupe** à l'encontre de nos publications (les admins ont
suspendu l'auteur, ou n'approuvent pas nos publications au point de saturer la file d'attente). Publier le
même récapitulatif avec un autre compte du pool serait contourner cette modération — exclu par la règle de
l'utilisateur « sans contourner les restrictions ». Ce n'est pas une limitation individuelle au sens
technique (adhésion, quota, anti-spam du compte, session) : celles-là n'arrêtent que le compte concerné.

Le blocage de groupe vient soit de la publication (`blocked` du groupe dans `group_posts_<code>.json`,
écrit comme aujourd'hui par `record()`), soit de la vérification (`restriction` non expirée dans
`share_membership.json`, quel que soit le compte qui l'a vue). `--retry-blocked` lève les deux, comme
aujourd'hui.

### 5.3 Groupes à servir, tous pays confondus, et rotation

Le nombre de pays n'est plus limité au nombre de comptes : **tous** les pays candidats sont lus.

```
candidats = pays choisis (--all-countries : share_groups.txt non vide ; --country / --countries : ceux-là)
pour c dans candidats :
    offres = API WipWork(c)                   # échec : c ignoré ce cycle, code de sortie 1
    G_c = plan(c)                             # filtres existants : 20 h, bloqué, offres nouvelles, doublons
    tronquer G_c à --max-groups (par pays, ordre de priorité ci-dessous)
S = ⋃ G_c, sans les groupes où membres(g) est vide (signalés « aucun compte membre confirmé »)
```

**Priorité d'un groupe** (ordre total, sans égalité possible) :

```
priorité(g) = ( g jamais publié d'abord,
                last_post_at(g) le plus ancien d'abord,
                pays de g : dernière publication la plus ancienne d'abord (pays jamais servi d'abord),
                Madagascar d'abord, puis code du pays,
                rang de g dans share_groups.txt )
```

`last_post_at` et la dernière publication d'un pays (plus grand `last_post_at` de ses groupes) sont lus
dans `group_posts_<code>.json` : pas de fichier de rotation. `last_post_at` n'avance que sur `posted`,
`pending` ou `unknown` : un groupe non servi, en échec ou attribué à un compte arrêté **garde son ancienneté**
et passe devant au cycle suivant.

Propriétés vérifiées (simulation §8, test « rotation sur 3 cycles ») :
- un pays jamais servi passe avant les groupes jamais publiés d'un pays déjà servi ;
- un groupe non servi passe, au cycle suivant, devant tous les groupes servis au cycle courant ;
- un groupe sans membre confirmé ne consomme aucune place et ne bloque aucun autre groupe ;
- avec des quotas insuffisants, chaque groupe servable est servi au moins une fois en ⌈servables / places⌉
  cycles (exemple §8 : 9 groupes, 5 places par cycle → tous servis en 2 cycles, rotation continue au 3e).

Effet connu, **inchangé** : un groupe publié tard dans un long lancement (après 12 h) peut n'avoir pas
atteint 20 h au lancement de 8 h le lendemain ; il est alors absent de `S` ce jour-là (pas « non servi »)
et repasse le surlendemain avec son ancienneté. Plus il y a de groupes, plus le lancement est long.

Coût : les offres de **chaque** pays candidat sont lues à chaque cycle (aujourd'hui Madagascar seul).

### 5.4 Attribution globale des groupes (qui est servi)

Chaque groupe de `S` va à **un seul** compte, membre confirmé, sans dépasser `cap(a)`. Les groupes sont pris
par ordre de priorité ; quand les quotas ne suffisent pas, ce sont les moins prioritaires qui attendent.

```
charge(a) = 0 pour chaque compte actif          # charge = groupes attribués ce cycle, tous pays confondus
pour g dans S trié par priorité :
    libres = [a dans membres(g) si charge(a) < cap(a)]
    si libres non vide :
        a = compte de libres de plus petite charge (égalité : ordre du fichier) ; g -> a ; charge(a) += 1
    sinon si chemin_libérant(g) existe :
        appliquer le chemin
    sinon :
        g non servi ce cycle (« membres au quota »), signalé
```

`chemin_libérant(g)` : tous les membres de `g` sont au quota, mais l'un d'eux peut céder un groupe déjà
attribué à un autre de ses membres qui a encore du quota, éventuellement en cascade. Recherche en largeur :
membre `a1` de `g` → groupe `h1` attribué à `a1` → autre membre `a2` de `h1` → … → compte `ak` avec
`charge(ak) < cap(ak)`. Si trouvé : `h(k-1)` passe à `ak`, …, `h1` passe à `a2`, `g` va à `a1` ; seule la charge
de `ak` augmente (les comptes intermédiaires cèdent un groupe et en reçoivent un). Déplacements uniquement
entre membres confirmés, aucun quota dépassé.

Ordre de parcours (déterminisme) : comptes de départ dans l'ordre du fichier ; groupes d'un compte du
**moins prioritaire au plus prioritaire** ; membres d'un groupe dans l'ordre du fichier.

Exemple : A (quota 1) membre de g1 et g2, B (quota 1) membre de g2 seulement, g2 plus prioritaire que g1.
g2 → A ; g1 : A au quota, B pas membre → chemin : g2 passe à B, g1 va à A. Les deux groupes sont servis.

**Garantie de 5.4 (démontrée)** : les ensembles de groupes qui peuvent être servis ensemble (chaque groupe
par un membre, quotas respectés) forment un matroïde (matroïde transversal, chaque compte compté `cap(a)`
fois). 5.4 est l'algorithme glouton sur ce matroïde dans l'ordre de priorité, le test d'ajout se faisant par
chemin libérant (théorème de Berge sur les chemins augmentants). Donc l'ensemble servi est :
- de **taille maximale** (aucune autre attribution ne sert plus de groupes) ;
- **le meilleur dans l'ordre de priorité** : pour tout k, il contient autant de groupes parmi les k plus
  prioritaires que n'importe quelle autre attribution possible ; un groupe moins prioritaire n'empêche
  jamais un plus prioritaire d'être servi.

### 5.5 Équilibrage de la charge entre les comptes (tous pays confondus)

L'ensemble des groupes servis ne change plus ; on déplace seulement des groupes entre comptes.

```
répéter :
    pour a_haut dans les comptes triés par charge décroissante (égalité : ordre du fichier) :
        chercher (en largeur) un chemin a_haut → h1 → b1 → h2 → b2 … → bk où :
            h1 est attribué à a_haut, b1 est membre confirmé de h1,
            h2 est attribué à b1, b2 est membre confirmé de h2, … ,
            charge(bk) <= charge(a_haut) - 2  et  charge(bk) < cap(bk)
        trouvé : h1 passe à b1, h2 passe à b2, …, hk passe à bk ; recommencer depuis le début
    aucun chemin depuis aucun compte : fin
```

Même ordre de parcours qu'en 5.4 (groupes d'un compte du moins prioritaire au plus prioritaire, membres
dans l'ordre du fichier). Les comptes intermédiaires peuvent être au quota (ils cèdent un groupe et en
reçoivent un) ; seul `bk` doit avoir du quota. Fin assurée : chaque déplacement fait baisser la somme des
carrés des charges d'au moins 2.

#### 5.5.1 Garantie de l'équilibrage

**Réponse à la question « la condition `charge(bk) <= charge(a_haut) - 2` suffit-elle ? » : oui**, à trois
conditions, toutes trois dans l'algorithme ci-dessus :
1. la recherche part de **chaque** compte, pas seulement du plus chargé (si le plus chargé n'a aucun
   chemin, on essaie le suivant) ;
2. la recherche explore **tout** ce qui est atteignable en cascade, pas seulement les voisins directs ;
3. seul le compte d'arrivée doit avoir du quota.

**Garantie démontrée** : pour l'ensemble servi fixé en 5.4, l'attribution finale
- minimise la **charge maximale** ;
- plus fort : le **vecteur des charges trié par ordre décroissant est le plus petit possible**
  (lexicographiquement) — la plus grosse charge est minimale, puis la deuxième, etc. ;
- minimise la **somme des carrés** des charges.

Justification : une attribution est un flot (source → groupes → comptes → puits, capacité `cap(a)` vers le
puits) ; coût du k-ième groupe d'un compte = 2k − 1, d'où un coût total = somme des carrés des charges.
Un flot de coût convexe est optimal si et seulement si son graphe résiduel n'a aucun cycle de coût négatif.
Un tel cycle correspond exactement à un chemin d'un compte `s` vers un compte `t` respectant le quota de
`t`, de coût 2·(charge(t) − charge(s) + 1), négatif si et seulement si `charge(t) <= charge(s) − 2`. Donc :
plus aucun chemin ⇔ somme des carrés minimale. Sur ce type de structure (ensemble M-convexe de vecteurs de
charges), le minimum de la somme des carrés coïncide avec le vecteur trié minimal et donc avec la charge
maximale minimale (résultat de « minimisation décroissante » de Frank et Murota ; cas sans quota :
semi-couplages optimaux de Harvey, Ladner, Lovász et Tamir, 2003).

**Vérification indépendante** (scripts hors projet, 24/11/2026, repris dans les tests §8) :

| Jeu de cas | Méthode de référence | Écarts de l'algorithme |
|---|---|---|
| 9 000 cas aléatoires : 1 à 4 comptes, 1 à 7 groupes, adhésions 20 à 100 %, quotas 0 à 4 ou illimités | recherche **exhaustive** de toutes les attributions | **0** (ensemble servi, nombre servi, charge max, vecteur trié, somme des carrés, quotas, adhésions) |
| 1 500 cas « par pays » : 2 à 5 comptes, 1 à 3 pays, adhésions par pays avec trous, compte historique membre de tout dans la moitié des cas | recherche **exhaustive** | **0** |
| 400 grands cas : jusqu'à 8 comptes, 40 groupes, 3 pays | flot à coût minimum et flot maximum (méthode indépendante) | **0** (nombre servi, charge max, somme des carrés) |
| Variantes dégradées, mêmes 9 000 cas | recherche exhaustive | sans chemin libérant : ensemble servi faux dans 486 cas ; sans équilibrage : charge max trop haute dans 222 cas |

La dernière ligne montre que les tests détectent bien une erreur : chaque étape est nécessaire.

**Limite de la garantie, formulée précisément** : l'optimalité de l'équilibrage est démontrée **pour
l'ensemble servi choisi par la priorité** (5.4). Quand les quotas obligent à laisser des groupes de côté,
la priorité passe avant l'équilibre : on ne sert jamais un groupe moins prioritaire à la place d'un plus
prioritaire pour mieux équilibrer. Sur 10 500 cas tirés au hasard (9 000 + 1 500), **aucun** autre ensemble
servi de même taille n'aurait permis une charge maximale plus basse — constaté, non démontré en général.
Sans quota, tous les groupes servables sont servis et la garantie est globale.

Exemples (calculés par le script de vérification) :
- 3 comptes membres de tout, Madagascar 30 + Sénégal 20 + Cameroun 10, sans quota → 20 / 20 / 20 ; chaque
  compte a des groupes **dans les 3 pays** (10 / 7 / 3, 10 / 7 / 3, 10 / 6 / 4).
- 10 comptes membres de tout, 20 pays de 5 groupes, sans quota → 10 groupes par compte, les 20 pays servis ;
  quota 5 → 50 groupes servis (les 10 premiers pays par priorité), les 10 autres pays passent d'abord au
  cycle suivant.
- 9 comptes membres des 67 groupes de Madagascar → 7 ou 8 groupes chacun.
- Compte historique membre des 67, 8 nouveaux comptes `unknown` partout → historique : 67 ; les 8 autres :
  « sans groupe : 0 membre confirmé, 67 inconnus → --check-membership --account … ».
- Historique membre des 67, B membre de 10, C membre de 3 (parmi les 10 de B) → 57 / 7 / 3 ; B et C
  signalés sous-utilisés.
- A membre de Madagascar seulement, B du Sénégal et du Cameroun, C de tout ; Madagascar 30, Sénégal 20,
  Cameroun 10, sans quota → A 20 (Madagascar), B 20 (Sénégal 18, Cameroun 2), C 20 (Madagascar 10,
  Sénégal 2, Cameroun 8).

### 5.6 Signalements

- **Compte sous-utilisé** : compte actif dont la charge est inférieure à la moitié de la charge moyenne des
  comptes actifs, ou nulle. Raison affichée par pays : `n` membre / `n` non membre / `n` inconnu, quota
  restant, ou « ses groupes sont tous mieux répartis ailleurs ». Commande suggérée :
  `--check-membership --account <profil>` s'il a des groupes `unknown`.
- **Groupe non servi** : aucun compte membre confirmé actif / membres au quota / restriction de groupe
  active (avec sa date de fin) / API du pays illisible.
- **Pays non servi ou partiellement servi** : groupes à publier, groupes servis, groupes en attente (avec la
  raison), dernière publication dans le pays (« en attente depuis le … »).
- **Comptes inactifs** : raison (5.1) ; **comptes arrêtés en cours de cycle** : raison et nombre de groupes
  laissés pour le cycle suivant (non transférés).

### 5.7 Exécution et verrou de profil

- Comptes l'un après l'autre (ordre du fichier) ; **un seul Chrome par compte**, ouvert une fois pour tous
  ses groupes, tous pays confondus ; un seul Chrome de publication à la fois.
- Dans un compte : pays dans l'ordre de priorité de leur premier groupe, puis groupes par priorité ; pour
  chaque pays, `run(sb, ses Job de ce pays, état du pays, réglages du pays avec profile = compte)` —
  fonction existante, inchangée (texte et affiches dans la langue du pays, suivi du pays, historique avec le
  profil qui a publié). Pause habituelle (`--delay-min` / `--delay-max`) entre deux groupes, **y compris**
  au changement de pays et de compte.
- Suivi de chaque pays chargé une fois (avec `recover_interrupted` comme aujourd'hui), partagé par les
  comptes successifs, écrit après chaque groupe.

**Verrou de profil** — `downloaded_files/facebook_share/.profile_<nom du profil>.lock`, par `fb.run_lock`
(non bloquant, libéré par le système en cas de plantage) :

```
publish_account(compte, groupes par pays) :
    with fb.run_lock(.profile_<compte>.lock) :     # 1. pris AVANT toute action sur le profil
        open_profile(compte)                        #    (fermeture des Chrome orphelins comprise)
        with SB(user_data_dir = profil) as sb :     # 2. Chrome ouvert une seule fois
            vérifier la connexion
            pour chaque pays du compte :
                run(sb, Job du pays, …)              # 3. appels successifs : même sb, même verrou,
                pause entre deux groupes             #    rien n'est relâché entre deux run()
        # 4. sortie du with SB : Chrome fermé
    # 5. verrou relâché seulement après la fermeture de Chrome
```

- **Tenu sans interruption** de l'étape 1 à l'étape 5, pendant **tous** les pays du compte : aucune
  vérification ni autre publication ne peut ouvrir ce profil entre deux `run()`.
- Un `StopRun` / `AccountLimited` / Ctrl+C dans un `run()` sort des deux `with` dans l'ordre : Chrome fermé
  **puis** verrou relâché ; aucun autre pays de ce compte n'est publié.
- **Pris par tout ce qui ouvre un profil de partage** : publication en mode pool (`publish_account`),
  publication actuelle (`publish_country`), vérification (`check_memberships`, un compte à la fois),
  `--login`. Jamais par `--membership-summary` ni `--preview` (aucun Chrome).
- **Déjà pris** (`RunAlreadyActive`) : publication → compte sauté ce cycle (« profil occupé »), ses
  groupes ne sont pas redistribués ; vérification → compte sauté avec message (« profil en cours de
  publication, relancez plus tard ») ; `--login` → refus avec message. Jamais d'attente : pas de blocage
  mutuel possible.
- Ordre des verrous : la publication tient `.post_groups.lock` (un seul lancement de publication) puis un
  verrou de profil à la fois ; la vérification ne tient que le verrou de profil, donc elle peut tourner
  pendant une publication sur un **autre** profil.

Arrêts en cours de cycle :
- `StopRun` / `AccountLimited` / déconnexion d'un compte : **arrêt de ce compte seulement**, ses groupes
  restants **dans tous les pays** attendent le cycle suivant ; `AccountLimited` → pause de 24 h comme
  aujourd'hui. Les autres comptes continuent avec leurs propres groupes, sans en recevoir de nouveaux.
  Ctrl+C / « q » : arrêt de tout.
- `not_member` à la publication (compte retiré du groupe depuis) : écrit dans `history` comme aujourd'hui →
  preuve `not_member` plus récente → plus de groupe pour ce compte au cycle suivant. Aucun réessai dans le
  même cycle.
- Restriction de groupe vue à la publication : `blocked` écrit par `record()` comme aujourd'hui → groupe
  donné à aucun compte jusqu'à sa fin.

### 5.8 Vérification des adhésions (`--check-membership`)

```
python facebook_script/post_groups.py --check-membership --account my_custom_profile_facebook_share_2
python facebook_script/post_groups.py --check-membership                 # tous les comptes listés, l'un après l'autre
python facebook_script/post_groups.py --check-membership --account X --country senegal
python facebook_script/post_groups.py --check-membership --account X --recheck   # revisite aussi les groupes déjà confirmés
python facebook_script/post_groups.py --check-membership --account X --max-groups 30
```

- **Indépendante de la publication** : ne prend pas le verrou de publication (`.post_groups.lock`), donc
  peut tourner pendant que le compte historique publie ; prend le verrou du profil vérifié (§5.7). N'écrit
  que `share_membership.json`, jamais `group_posts_<code>.json`.
- **Comptes** : tout compte listé dans `share_accounts.json`, **actif ou non** (on vérifie un nouveau compte
  avant de l'activer). Profil `my_custom_profile_facebook` (scraping) refusé.
- **Groupes visités** : ceux de **tous** les pays ayant un `share_groups.txt` non vide (ou `--country` /
  `--countries`), pour lesquels l'état du compte (5.2) est `unknown`, ou `not_member` depuis plus de
  `MEMBERSHIP_RECHECK_DAYS` (7) jours (il a pu rejoindre le groupe). `member` n'est pas revisité (la
  publication détecte un compte retiré). `--recheck` : tous. `--max-groups N` : au plus N visites.
- **Une visite, en lecture seule** : ouverture de l'adresse du groupe, attente du chargement, puis lecture
  répétée jusqu'à `COMPOSER_TIMEOUT` :
  - page de connexion → arrêt de la vérification de ce compte (« reconnectez avec `--login --account` ») ;
  - id numérique du groupe lu (`GROUP_ID_JS`), enregistré ;
  - « Exprimez-vous… » vu → `member` ;
  - bandeau de suspension / limite de publications en attente → `member` + `restriction` (`reason`, `until`)
    — restriction **de groupe** (§5.2.1) ;
  - fin du délai : « Rejoindre » vu sans « Exprimez-vous… » → `not_member` ; sinon → `unknown`
    (`detail` : `no_composer`, `load_error`) + capture de diagnostic comme aujourd'hui.
- **Jamais** : clic, saisie, ouverture de la fenêtre de rédaction, « Rejoindre », publication, commentaire.
  Seul mouvement : le défilement fait par `COMPOSER_JS` pour amener le bouton à l'écran.
- Pause aléatoire de 20 à 40 s entre deux groupes (`--delay-min` / `--delay-max` s'ils sont donnés).
- Écriture après chaque groupe (Ctrl+C garde ce qui est vérifié), sous un verrou court
  (`.share_membership.lock`, attente de quelques secondes au plus) : relire le fichier, remplacer les
  entrées **de ce compte**, écriture atomique.
- Fin : résumé du compte (comme 5.9).

Format de `downloaded_files/facebook_share/share_membership.json` :

```json
{
  "my_custom_profile_facebook_share_2": {
    "madagascar/506924959474464": {"status": "member", "checked_at": "2026-11-25T09:12:04+03:00",
                                   "detail": "composer", "numeric_id": "506924959474464", "url": "https://..."},
    "madagascar/emploimada":      {"status": "not_member", "checked_at": "2026-11-25T09:13:01+03:00",
                                   "detail": "join_button", "url": "https://..."},
    "madagascar/1234":            {"status": "member", "checked_at": "...", "detail": "restriction",
                                   "restriction": {"reason": "suspended", "until": "2026-11-27T06:47:00+03:00"}}
  }
}
```

### 5.9 Résumé des adhésions (`--membership-summary`) et affichage

```
python facebook_script/post_groups.py --membership-summary               # tous les comptes, tous les pays
python facebook_script/post_groups.py --membership-summary --account X --country madagascar
```

Sans navigateur ni verrou. Exemple :

```
Compte                               Actif  Quota  Madagascar (67)                  Sénégal (3)          Dernière vérification
my_custom_profile_facebook_share     oui    ∞      65 membre · 0 non membre · 2 ?   0 · 0 · 3 ?          (publications)
my_custom_profile_facebook_share_2   non    15     40 membre · 20 non membre · 7 ?  3 membre · 0 · 0 ?   25/11/2026 10:42
my_custom_profile_facebook_share_3   non    ∞       0 membre · 0 non membre · 67 ?  0 · 0 · 3 ?          jamais
Groupes sans membre confirmé parmi les comptes actifs : 5 (madagascar/123…, madagascar/abc…, senegal/…)
Restrictions de groupe en cours : madagascar/1234 (suspendu jusqu'au 27/11/2026 06:47, vu par share_2)
```

Publication, au début — tableau de répartition (exemple réel du script, 3 comptes, 3 pays) :

```
Compte                               Madagascar  Sénégal  Cameroun  Total  Quota restant
my_custom_profile_facebook_share         10         7        3        20        ∞
my_custom_profile_facebook_share_2       10         7        3        20        ∞
my_custom_profile_facebook_share_3       10         6        4        20        ∞
Pays : Madagascar 30/30 servis · Sénégal 20/20 · Cameroun 10/10
En attente : —   Comptes inactifs : —   Sous-utilisés : —
```

À la fin, récapitulatif par compte et par pays. `--preview` en mode pool : même tableau + textes et
affiches par groupe, **rien écrit**.

## 6. Activation, compatibilité, mise en route

- **Mode pool** si `share_accounts.json` contient au moins un compte **actif** ; sinon comportement actuel
  **identique** (fichier livré avec `"accounts": []`). `--profile` force le mode actuel pour ce lancement.
- En mode pool, `profile` et `max_groups_per_day` de `share_countries.json` ne s'appliquent pas
  (avertissement s'ils sont renseignés) : quota du compte selon §5.1. `api_name`, `per_post`, `min_offers`
  restent par pays.
- Avertissement au démarrage du mode pool si le profil historique d'un pays (`share_countries.json` ou
  défaut) n'est pas listé : il ne publierait plus.
- `--login --account <profil>` : connecte ce compte (actif ou non) ; `--login` en mode pool : chaque compte
  listé non connecté, l'un après l'autre.
- Commande PM2 inchangée (`--all-countries`).

**Mise en route conseillée** (le compte historique publie normalement à chaque étape) :
1. Lister les nouveaux comptes avec `"active": false` → le mode pool reste éteint, PM2 publie comme
   aujourd'hui avec le compte historique.
2. `--login --account <profil>` puis `--check-membership --account <profil>` pour chaque nouveau compte
   (rejoindre les groupes à la main avant si besoin) ; suivre avec `--membership-summary`.
3. Ajouter le compte historique, lui aussi avec `"active": false`, et lancer `--check-membership --account
   my_custom_profile_facebook_share` : seuls ses groupes sans publication réussie sont visités (refusée si
   ce profil est en train de publier : la relancer après).
4. Passer tous les comptes (historique compris) à `"active": true` en dehors du créneau de 8 h, puis
   `--preview` pour voir la répartition ; si elle ne convient pas, remettre `"active": false` avant le
   cycle suivant.

Format de `share_accounts.json` :

```json
{
  "_aide": ["max_groups_per_day : publications par jour et par compte, tous pays confondus ; 0 = illimité ;",
            "pour arrêter un compte : \"active\": false (jamais 0)."],
  "defaults": {"max_groups_per_day": 0},
  "accounts": [
    {"profile": "my_custom_profile_facebook_share", "label": "Compte historique", "active": true},
    {"profile": "my_custom_profile_facebook_share_2", "label": "Compte 2", "active": false, "max_groups_per_day": 15}
  ]
}
```

Validation au démarrage (arrêt avec message) : clés inconnues, profil en double (même nom de dossier),
profil `my_custom_profile_facebook` (scraping) interdit, `max_groups_per_day` négatif ou non entier,
`--account` inconnu. Profil relatif = relatif à la racine du projet.

## 7. Fichiers concernés

| Fichier | Changement |
|---|---|
| `facebook_script/account_pool.py` | **nouveau** — logique pure, sans Chrome : comptes et quotas, adhésions (deux sources, preuve la plus récente), portée des restrictions, lecture/écriture de `share_membership.json`, priorité des groupes, attribution globale (5.4), équilibrage (5.5), signalements, textes du résumé et du tableau |
| `facebook_script/post_groups.py` | options `--accounts-file`, `--account`, `--check-membership`, `--recheck`, `--membership-summary` ; `membership_status()` (visite en lecture seule) ; `check_memberships()` ; `run_pool()` appelé par `main()` en mode pool ; `publish_account()` (verrou de profil + un Chrome par compte, `run()` pays après pays) ; `--login` par compte ; **verrou de profil dans `publish_country` et `login_only`** (seul changement au chemin actuel : un profil occupé est sauté au lieu de faire échouer Chrome). `plan`, `run`, `record`, `posts_today`, pause de compte, `post_to_group`, `open_composer` inchangés |
| `facebook_script/share_accounts.json` | **nouveau** — `"accounts": []` + aide |
| `facebook_script/share_countries.json` | aide seulement : `profile` / `max_groups_per_day` ignorés en mode pool |
| `facebook_script/test_account_pool.py` | **nouveau** — tests `unittest` de la logique pure (§8) |
| `facebook_script/test_post_groups_pool.py` | **nouveau** — tests avec faux navigateur : vérification en lecture seule, verrous, orchestration du mode pool (§8) |
| `CLAUDE.md` | section `post_groups.py` : mode pool, vérification, résumé, verrous, quotas ; remplace « au plus un compte par pays » |
| `downloaded_files/facebook_share/` | **nouveaux fichiers d'état** (non suivis par git) : `share_membership.json`, `.profile_<nom>.lock`, `.share_membership.lock` |

Inchangés : `share.py`, `visuals.py`, `scraper.py`, `pages.py`, `countries.py`, `ecosystem.config.js`, format
de `group_posts_<code>.json` et de `account_block.json`.

## 8. Tests

`python -m unittest discover facebook_script -p "test_*pool*.py"` — aucun accès réseau ni Facebook. Codes
pays réels de `countries.py` (madagascar, senegal, cameroun, togo…) ; « tous membres » = preuve `member`
pour chaque compte et chaque groupe. Durée attendue : ~30 s, dont ~25 s de comparaisons exhaustives (mesuré
avec les scripts de vérification).

**`test_account_pool.py` (logique pure)**

Cas demandés par l'utilisateur :
- **10 comptes, 20 pays** (5 groupes chacun), tous membres, sans quota → les 100 groupes servis, les 20 pays
  couverts, 10 groupes par compte ; avec quota 5 par compte → 50 groupes servis (les 10 premiers pays par
  priorité), puis les 10 autres pays servis en premier au cycle suivant.
- **3 comptes, 3 pays** (Madagascar 30, Sénégal 20, Cameroun 10), tous membres, sans quota → 20 / 20 / 20,
  chaque compte a des groupes dans les 3 pays (10/7/3, 10/7/3, 10/6/4), chaque groupe attribué une fois.
- **1 compte, 10 pays** (3 groupes chacun), membre de tout : sans quota → les 30 groupes ; quota 15 → les
  15 groupes des 5 premiers pays, les 15 autres au cycle 2, retour aux premiers au cycle 3.
- **Adhésions différentes selon les pays** : A Madagascar seulement, B Sénégal + Cameroun, C tout →
  A 20 (Madagascar), B 20 (Sénégal 18, Cameroun 2), C 20 (Madagascar 10, Sénégal 2, Cameroun 8) ; aucun
  groupe donné hors adhésion confirmée.
- **Quotas individuels globaux** : A quota 5, membre de 3 pays → au plus 5 groupes tous pays confondus ;
  A ayant déjà 3 publications aujourd'hui au Sénégal → au plus 2 groupes, quel que soit le pays.
- **Compte limité au milieu de ses publications** : voir `test_post_groups_pool.py`.

Optimalité (§5.5.1) — mêmes jeux de cas que la vérification, graines fixes :
- 9 000 cas aléatoires (1 à 4 comptes, 1 à 7 groupes) contre la **recherche exhaustive** : ensemble servi
  (taille et priorité), charge max, vecteur des charges trié, somme des carrés, quotas, adhésions → 0 écart.
- 1 500 cas « par pays » (2 à 5 comptes, 1 à 8 groupes, 1 à 3 pays) contre la recherche exhaustive → 0 écart.
- 400 grands cas (jusqu'à 8 comptes, 40 groupes) contre un **flot à coût minimum** écrit dans le test,
  indépendant de l'algorithme → 0 écart.
- **Contrôle du test lui-même** : la variante sans chemin libérant et la variante sans équilibrage doivent
  échouer sur au moins un cas de ces jeux (sinon le test ne prouverait rien).
- Exemple du chemin libérant : A (quota 1) membre de g1 + g2, B (quota 1) membre de g2, g2 prioritaire →
  g2 → B, g1 → A.
- Pas de plafond ⌈groupes / comptes⌉ : historique 67, 8 comptes `unknown` → historique 67 ; membres
  partiels 67 / 10 / 3 → 57 / 7 / 3, B et C sous-utilisés.
- Déterminisme : même résultat sur 20 lancements avec les mêmes données.

**Rotation sur 3 cycles** (résultats calculés par la simulation de l'algorithme) :

Données — cycles le 01/12, 02/12 et 03/12 à 8 h ; publications simulées à 8 h, 8 h 05, 8 h 10… dans l'ordre
d'exécution (§5.7) ; délai de 20 h appliqué :

| Groupe | Pays | État avant le cycle 1 |
|---|---|---|
| M1, M2 | Madagascar | publiés le 29/11 à 9 h 00 et 9 h 05 |
| M3, M4 | Madagascar | jamais publiés |
| S1, S2, S3 | Sénégal | jamais publiés (pays jamais servi) |
| C1, C2 | Cameroun | publiés le 30/11 à 10 h 00 et 10 h 05 |
| T1 | Togo | jamais publié, **aucun compte membre** |

Comptes : A quota 3, membre de Madagascar, Sénégal, Cameroun ; B quota 2, membre de Sénégal et Cameroun
(5 places par cycle pour 9 groupes servables : quotas insuffisants).

| Cycle | Ordre de priorité | A publie | B publie | Non servis (quota) | Sans membre |
|---|---|---|---|---|---|
| 1 | S1 S2 S3 M3 M4 M1 M2 C1 C2 | S1, M3, M4 | S2, S3 | M1, M2, C1, C2 | T1 |
| 2 | M1 M2 C1 C2 S1 M3 M4 S2 S3 | M1, M2, S1 | C1, C2 | M3, M4, S2, S3 | T1 |
| 3 | M3 M4 S2 S3 M1 M2 S1 C1 C2 | M3, M4, M1 | S2, S3 | M2, S1, C1, C2 | T1 |

Vérifié par le test : ordre de priorité exact à chaque cycle ; le Sénégal (jamais servi) passe avant les
groupes jamais publiés de Madagascar ; chaque groupe non servi au cycle n passe devant tous ceux servis au
cycle n ; les 9 groupes servis au moins une fois sur les cycles 1-2 ; T1 signalé à chaque cycle sans
prendre de place ; aucun quota dépassé ; aucun groupe donné à B à Madagascar.

Priorité : jamais publié avant déjà publié ; `last_post_at` le plus ancien d'abord ; à égalité, pays jamais
servi puis pays servi le moins récemment ; puis Madagascar, code, rang dans le fichier ; groupe en échec
(`fill_failed`) ou attribué à un compte arrêté : ancienneté conservée.

Restrictions (§5.2.1) :
- `not_member` de A dans g → B (membre) reçoit g ; A ne le reçoit plus.
- A en pause anti-spam (`account_block.json`) → A inactif ; ses groupes répartis entre les autres comptes
  au cycle suivant ; aucun blocage de groupe.
- `suspended` ou `pending_limit` vu par A dans g (publication ou vérification) → g donné à aucun compte,
  B compris, jusqu'à `until` ; de nouveau attribuable après ; `--retry-blocked` le rend attribuable.

Quotas (§5.1) :
- `max_groups_per_day: 0` (compte, `defaults`, ligne de commande) → illimité.
- Priorité : `--max-groups-per-day` > compte > `defaults` > 0 ; `--max-groups-per-day 0` rend tous les
  comptes illimités ; `--max-groups-per-day 4` impose 4 à chacun, pas 4 au total.
- Négatif ou non entier → erreur au chargement.
- `cap(a)` = quota − publications du jour de ce profil dans **tous** les pays ; `cap ≤ 0` → inactif
  « quota atteint ».

Adhésions et fichiers :
- Preuve la plus récente entre vérification et publication (vérif. `not_member` puis publication `posted` →
  `member` ; publication `posted` puis vérif. `not_member` → `not_member`) ; entrée d'historique sans profil
  = profil du pays ; `no_composer` = aucune preuve ; jamais vérifié = `unknown`.
- `unknown` et `not_member` ne reçoivent jamais de groupe, même quand tous les membres sont au quota.
- Fichier de comptes : clés inconnues, profils en double, profil de scraping → erreur ; liste vide ou
  aucun compte actif → mode pool éteint.
- `share_membership.json` : écriture d'un compte sans toucher aux entrées des autres comptes.

**`test_post_groups_pool.py` (faux navigateur, faux `post_to_group`, vrais fichiers de verrou dans un
dossier temporaire)**
- **Compte limité au milieu de ses publications** (A limité à son 3e groupe, qui est au Sénégal après
  2 groupes à Madagascar) → aucun de ses groupes restants (Sénégal et Cameroun) donné à B ou C ; B et C
  publient exactement les groupes prévus pour eux ; pause de 24 h écrite pour A ; les groupes de A non
  tentés sont les premiers du cycle suivant.
- **Verrou de profil** :
  - pendant la publication de A dans 3 pays, le verrou de A est tenu **entre** chaque appel à `run()`
    (une tentative de prise du verrou depuis le faux `run()` du 2e et du 3e pays échoue) ;
  - ordre observé : verrou pris → `open_profile` → ouverture de Chrome → … → fermeture de Chrome →
    verrou relâché ; idem après un `AccountLimited` et après une exception inattendue ;
  - verrou de B déjà tenu (vérification simulée) → B sauté ce cycle, ses groupes non redistribués ;
  - `check_memberships` sur un profil en cours de publication → compte sauté avec message ;
  - vérification de C pendant la publication de A → autorisée (profils différents) ;
  - `publish_country` (mode actuel) et `--login` prennent aussi le verrou.
- Un compte dans plusieurs pays : un seul Chrome ouvert pour ses 3 pays ; chaque publication écrite dans le
  suivi de son pays avec le profil du compte ; texte dans la langue du pays.
- Quota global : `posts_today` relu de tous les pays avant l'attribution.
- `not_member` à la publication → pas de nouvel essai dans le cycle, plus de groupe au cycle suivant.
- `suspended` à la publication par A → `blocked` écrit → groupe non attribué à B au cycle suivant.
- Plantage puis relance (marqueur `publishing`) : la publication interrompue n'est pas refaite.
- `membership_status` : « Exprimez-vous… » → `member` ; « Rejoindre » seul → `not_member` ; rien →
  `unknown` ; bandeau → `member` + `restriction` ; page de connexion → arrêt du compte ; **aucun clic ni
  saisie** envoyé au navigateur (le faux `sb` échoue sur tout `Input.dispatch*` / `insertText` / clic).
- `check_memberships` : `member` non revisité, `not_member` récent non revisité, `not_member` de plus de
  7 jours revisité, `--recheck`, `--max-groups`, compte inactif vérifiable, `group_posts_<code>.json`
  inchangé.
- Compatibilité : sans `share_accounts.json` (ou sans compte actif), sortie `--preview` identique au
  caractère près à celle d'avant.

Hors tests automatiques : premier `--check-membership --visible` sur le vrai Facebook avec accord de
l'utilisateur, pour confirmer qu'un non-membre ne voit pas « Exprimez-vous… » dans un groupe public.

## 9. Décisions prises par Claude (à vérifier)

- `suspended` et `pending_limit` restent des restrictions **de groupe** (décision de modération du groupe
  envers nos publications) ; toutes les autres limitations sont **de compte** (§5.2.1).
- Ordre de déplacement fixé (groupe le moins prioritaire d'abord) pour un résultat déterministe et des
  tests aux valeurs exactes.
- Rotation déduite de l'ancienneté de chaque groupe (puis du pays) dans `group_posts_<code>.json` : pas de
  fichier `share_rotation.json`.
- Quand les quotas manquent, la priorité passe avant l'équilibre (§5.5.1, limite de la garantie).
- Verrou de profil non bloquant : un profil occupé est sauté, jamais attendu.
- Un Chrome par compte pour tous ses pays ; comptes dans l'ordre du fichier ; dans un compte, pays puis
  groupes par priorité.
- Preuve la plus récente entre vérification et publication (la publication n'écrit pas
  `share_membership.json`, elle reste inchangée).
- `member` jamais revisité par défaut ; `not_member` revisité après 7 jours ; `unknown` revisité à chaque
  vérification.
- « Exprimez-vous… » visible = `member`, même si un « Rejoindre » est aussi visible (groupes suggérés) —
  **non vérifié sur le vrai Facebook** pour un groupe public vu par un non-membre.
- Vérification par défaut sur tous les pays qui ont des groupes ; pause de 20 à 40 s entre deux visites.
- Sous-utilisé = moins de la moitié de la charge moyenne des comptes actifs, ou aucun groupe.
- Mode pool seulement avec au moins un compte actif : nouveaux comptes vérifiés avant activation.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ce fichier documente le projet en français (langue du code et des logs) pour rester cohérent avec le reste du repo.

## Vue d'ensemble

Trois sous-systèmes indépendants qui communiquent via une API REST :

1. **Scraper Python** (racine + `scrapers/`) — scrape des offres d'emploi et profils d'entreprises sur 35 sites africains de type "emploi.xx" (plateforme Drupal générique), via SeleniumBase en mode CDP (contourne Cloudflare/captcha).
2. **Backend Node.js/Express** (`backend/`) — API REST + MongoDB, reçoit les données du scraper et les sert au frontend.
3. **Frontend React** (`frontend/`) — dashboard de visualisation (Vite + Tailwind v4).
4. **Script Sénégal** (`senegal_script/`) — scraper indépendant dédié à senjob.com (site PHP, pas Drupal — distinct du pays `"senegal"` de `scrapers/countries.py` qui cible emploisenegal.com). Voir section dédiée plus bas.
5. **Script Facebook** (`facebook_script/`) — scraper indépendant des publications de groupes Facebook (`scraper.py`) et de pages Facebook analysées par l'IA OpenAI vers des JSON au format backend (`pages.py`), compte connecté manuellement. Voir sections dédiées plus bas.

Le scraper ne dépend jamais du frontend ; il pousse ses données au backend via HTTP (`SCRAPER_API_URL`) et/ou les persiste en local dans `downloaded_files/*.json`.

## Commandes

### Scraper (Python)
```bash
python index.py                              # tous les pays
python index.py --country burkina            # un seul pays (voir scrapers/countries.py pour les codes)
python index.py --list                       # liste les codes pays disponibles

python index.py --download-cvs                                        # CV emploi.ma, session anonyme
python index.py --download-cvs --country maroc --cv-cookie "k=v; k2=v2"  # avec cookie de session
python -m scrapers.email_extractor --country senegal --limit 20       # extraction d'emails via Google AI Mode
python setup_profil.py --profil 1            # ouvre Chrome pour connecter manuellement un profil persistant
```
Dépendances : `pip install seleniumbase beautifulsoup4 requests`. Pas de suite de tests automatisés dans ce dépôt — la validation se fait en exécutant le scraper sur un pays et en inspectant les fichiers `downloaded_files/`.

Variable d'environnement clé : `SCRAPER_API_URL` (défaut `http://localhost:3500`). La mettre à vide désactive l'envoi vers le backend et bascule sur cache JSON local uniquement.

### Envoi des CV vers l'IA WipWork (`scrapers/ia_cv_uploader.py`)
```bash
python -m scrapers.ia_cv_uploader --dry-run                  # liste ce qui partirait, aucun envoi
python -m scrapers.ia_cv_uploader --limit 5 --use-test        # test réel sur 5 CV, collections de test de l'IA
python -m scrapers.ia_cv_uploader --country south_africa
python -m scrapers.ia_cv_uploader --cv-root /srv/cv_scrappe/downloaded_files/cv_files
python -m scrapers.ia_cv_uploader --max-attempts 3            # ignore les CV ayant déjà échoué 3 fois
```
Pousse les CV déjà scrapés vers `POST {IA_API_URL}/parse/resume` (défaut `https://www.wipwork.com/bot`).

- **Sélection** : appliquée côté backend par `GET /api/cvs/ia/pending` — `commercial_email_wave = 2` (`--wave`, `all` pour ignorer), `commercial_email_unsubscribed` absent, et `ia_sent != true`. Un CV envoyé quitte donc le filtre : le script relit la page courante en boucle, aucun doublon même en cas de reprise.
- **Métadonnées** : `enterprise_ids=WipWork` + `enterprise_sources={"WipWork":"import"}` (champ **obligatoire** de l'API, `--source` pour `apply`/`save_from_search`), `country_ids` = alpha-3 déduit du pays via `scrapers/common/country_iso.py` (`south_africa` → `ZAF`), `visibility=visible`, `is_active=false` (défaut de l'API — passer `--is-active` pour activer).
- **Fichier** : lu depuis `filepath` ; si le chemin vient d'une autre machine (chemins Windows `C:\Users\user\...` en base), repli sur `<--cv-root|$CV_FILES_ROOT>/<country>/<filename>` puis `downloaded_files/cv_files/<country>/<filename>`. Seuls `.pdf`, `.docx`, `.doc` sont acceptés.
- **Marquage** : `PATCH /api/cvs/{id}/ia` écrit `ia_sent`, `ia_sent_at`, `ia_point_id`, `ia_user_id`, `ia_operation_type`, `ia_storage_path`, `ia_attempts`, `ia_last_error`, `ia_skip_reason`. Un « skip » (fichier introuvable, pays inconnu) n'incrémente pas `ia_attempts` et laisse le CV éligible ; un échec API l'incrémente. Journal local de secours : `downloaded_files/ia_uploads.json`.
- **Retry** : backoff exponentiel + jitter sur 408/429/5xx uniquement ; 400/415/422 = échec définitif (le CV est marqué `failed`, la boucle continue).

- **Authentification** : l'API IA exige un en-tête `X-API-Key`, envoyé depuis `IA_API_KEY` (ou `--api-key`). `IA_API_TOKEN` ajoute un `Authorization: Bearer` si un jour nécessaire. Les secrets ne sont jamais loggés en clair (`bf80…ddbe`).

Variables d'environnement : `SCRAPER_API_URL`, `IA_API_URL`, `IA_API_KEY`, `IA_API_TOKEN` (Bearer, optionnel), `CV_FILES_ROOT`. Le script les lit dans l'environnement, puis à défaut dans `.env` (racine) et `backend/.env` — tous deux git-ignorés, c'est là que vit la clé d'API ; ne jamais la remettre dans un fichier suivi par git. `_load_dotenv()` n'écrase jamais une variable déjà exportée dans le shell.

Vérifier la couverture du mapping pays → alpha-3 : `python -m scrapers.common.country_iso`.

### Script Sénégal — senjob.com (`senegal_script/`)
```bash
python senegal_script/scraper.py                 # interactif : login manuel puis scrape toutes les pages
python senegal_script/scraper.py --limit 5        # test rapide, 5 nouvelles offres
python senegal_script/scraper.py --max-pages 2
python senegal_script/scraper.py --headless       # sans fenêtre, suppose la session déjà connectée
```
Au premier lancement (ou si la session a expiré), une fenêtre Chrome s'ouvre sur l'espace candidat senjob.com et attend une connexion manuelle (ENTRÉE dans le terminal pour continuer) — session sauvegardée dans le profil persistant `my_custom_profile_senegal` (racine du projet). Sans connexion, les offres natives senjob (non relayées depuis un flux ONG externe) n'exposent ni email de contact recruteur ni description complète — voir "Pièges" ci-dessous.

`is_logged_in()` vérifie avant toute chose si le profil est déjà connecté (absence du champ mot de passe sur la page de connexion) — si la session est encore valide, aucune saisie n'est demandée, passage direct au scraping.

Une offre sans `company_id` identifiable (ni logo, ni nom d'entreprise) n'est jamais écrite dans `jobs_senjob.json` ni poussée à `jobs_scrappe` (destinées à une campagne d'emailing par entreprise) — trace uniquement dans `downloaded_files/jobs_senjob_skipped.json` pour éviter de la re-scraper à chaque lancement.

Fichier unique et autonome (n'importe pas `scrapers/`, dépendances dupliquées localement : `JsonStore`/`ApiClient`) — sortie dans `downloaded_files/jobs_senjob.json`, push vers `SCRAPER_API_URL` si définie (même convention que le reste du scraper).

### Script Facebook — groupes (`facebook_script/`)
```bash
python facebook_script/scraper.py                             # groupes listés dans facebook_script/groups.txt
python facebook_script/scraper.py --url https://www.facebook.com/groups/506924959474464/
python facebook_script/scraper.py --max-posts 10               # test rapide
python facebook_script/scraper.py --stop-after-known 20        # relance incrémentale (tri chrono par défaut)
python facebook_script/scraper.py --dump-html --max-posts 5    # HTML brut par publication -> downloaded_files/facebook_html/
python facebook_script/scraper.py --login                      # forcer l'étape de connexion manuelle
python facebook_script/scraper.py --no-ocr                     # sans lecture du texte des images
python facebook_script/scraper.py --max-age-days 0             # toutes les publications (défaut : 15 derniers jours)
```
Dépendances en plus du reste du dépôt : `pip install rapidocr onnxruntime` (OCR des images, optionnel).
Premier lancement : Chrome s'ouvre sur facebook.com avec le profil persistant `my_custom_profile_facebook`, connexion manuelle puis ENTRÉE dans le terminal. Session détectée par le cookie `c_user` + absence de page login/checkpoint (`_session_active`, sans navigation pour ne pas casser une 2FA en cours).

Boucle par groupe (`scrape_group`) : `COLLECT_POSTS_JS` marque les `[aria-posinset]` du `div[role="feed"]` pas encore traités (`data-scrape-key`), clique « Voir plus » et simule un survol du lien horodatage ; chaque publication est ensuite lue par `_outer_html` puis parsée en BS4 (`parse_post`). Scroll en bas de page tant que du contenu arrive ; fin du fil après `--max-idle` scrolls sans nouveauté. Sortie : `downloaded_files/facebook_posts_{group_id}.json` (clé `post_id`), écrite par lot (`PostStore`, écriture atomique). Pas de push backend (aucune route pour ces données).

Images : téléchargées avec `requests` dès qu'une publication est nouvelle (`download_post_images`), dans `downloaded_files/facebook_images/{group_id}/{post_id}_{n}.jpg` ; chemin relatif à `downloaded_files` dans `images[i]["file"]`. Les URL du CDN Facebook sont signées et expirent en quelques jours (`oe=`) : au début de chaque groupe, `download_missing_images` rattrape les publications en cache sans fichier. Erreur définitive (404, 403, 410, contenu non image) → `images[i]["download_error"]`, plus retentée ; erreur réseau → retentée au lancement suivant. `--no-images` pour ne garder que les URL. Seules les vignettes présentes dans le fil sont récupérées (taille du fil, ~600-700 px ; au-delà de 5 photos, le « +N » n'est pas dans le DOM).

Fin de chaque groupe (`finalize_posts`, hors `finally` : sauté sur Ctrl+C, rattrapé au lancement suivant car ne traite que ce qui manque) :
- **OCR** des images téléchargées sans `ocr_text` via RapidOCR (`pip install rapidocr onnxruntime`, local et gratuit, import paresseux : sans le paquet, simple avertissement). Lignes filtrées (score ≥ `OCR_MIN_SCORE`, ≥ 2 caractères alphanumériques) dans `images[i]["ocr_text"]`. Emails/téléphones trouvés sur les images fusionnés dans `emails`/`phones`. `--no-ocr` pour désactiver. Sans OCR, `_image_text` se rabat sur le texte alternatif de Facebook (« … qui dit '…' »), incomplet (a sauté un des deux emails d'une affiche réelle).
- **Offres d'emploi** : `analyze_job` recalculé pour tout le cache à chaque lancement → `post["job"]` = `{is_offer, title, description, tasks, profile}` (description = texte + texte des images). Détection par mots-clés FR/EN/malgache sur texte « plié » (`_fold` : minuscules, sans accents, NFKD), rubriques découpées sous les intitulés « Missions/Tâches… » et « Profil/Compétences… » (`_job_sections`, best-effort, fusion des lignes coupées par l'OCR).
- **Export** `downloaded_files/facebook_jobs_{group_id}.json` : uniquement les offres, champs utiles (intitulé, description, missions, profil, emails, téléphones, lien, images).

Fichier autonome (n'importe pas `scrapers/`). Validation hors Facebook : fausse page de groupe locale reproduisant les pièges ci-dessous, pilotée en headless via `scrape_group` avec `OUTPUT_DIR` redirigé.

### Pages Facebook → IA OpenAI → JSON backend (`facebook_script/pages.py`)
```bash
python facebook_script/pages.py                                   # pages.txt : scraping + analyse IA + JSON
python facebook_script/pages.py --url https://www.facebook.com/profile.php?id=61559428572369 --max-posts 5
python facebook_script/pages.py --no-ai                           # scraping seul, aucun appel OpenAI
python facebook_script/pages.py --skip-scrape                     # sans navigateur : IA + consolidation du cache
python facebook_script/pages.py --skip-scrape --reanalyze         # refait les analyses d'un ancien prompt/modèle (cache utilisé)
python facebook_script/pages.py --max-ai-tokens 500000            # plafond de tokens IA pour ce lancement
python facebook_script/pages.py --no-email-filter                 # envoie aussi les images/publications sans email lu
python facebook_script/pages.py --max-age-days 30                 # fenêtre de publication (défaut 15 jours, 0 = tout)
python facebook_script/pages.py --push                            # + envoi au backend et nettoyage des doublons déjà envoyés
python facebook_script/pages.py --push-only                       # envoi des JSON déjà générés (ni navigateur ni IA)
python facebook_script/pages.py --push-only --no-clean            # envoi sans aucune suppression dans le backend
```
`OPENAI_API_KEY` (obligatoire), `OPENAI_MODEL` (défaut `gpt-4o-mini` dans `ai_extractor.py`), `OPENAI_BASE_URL` : environnement puis `.env` / `backend/.env`. Sans `--push` / `--push-only`, **aucun envoi au backend** : fichiers JSON à valider d'abord. Même profil Chrome que les groupes (`my_custom_profile_facebook`).

Modules (importent `scraper.py` : `parse_post`, `_scroll_feed`, CDP, OCR, téléchargement) :
- `pages.py` — `PageTarget` (même interface que `GroupTarget` pour `parse_post` : `post_url()`, `source_fields()`), infos de l'onglet « À propos », fil de la page (`ScrapeOptions(any_root=True)` : publications hors `div[role="feed"]`), date complète via l'infobulle du lien horodatage (`exact_dates`, survol réel), puis **toutes** les images via la visionneuse photo (`collect_viewer_photos` : bouton « Photo suivante » jusqu'à `vignettes + "+N"` photos — la limite évite de parcourir tout un album pour une photo seule), téléchargées avec sha256 et OCR. Une publication n'est analysée que si `images_done`. Coût IA — mode mixte (`select_images_for_ai`, `images[i].ai_mode` / `ai_reason` dans `analysis_*`) : une image jointe en haute résolution coûte ~29 000 tokens avec gpt-4o-mini (mesuré : ~850 000 tokens pour 30 affiches). L'OCR lit un contact ou ≥ `AI_MIN_TEXT_CHARS` caractères → **texte OCR seul** (`ocr_text`, image non jointe) ; un peu de texte (logo) ou pas d'OCR → image jointe (`image`) ; rien lu → écartée (`skipped`), sauf si rien d'autre ne part (1re image jointe). `--send-all-images` = tout joindre. **Filtre email** (par défaut en CLI, `--no-email-filter` pour le retirer ; ignoré avec `--send-all-images`) : si le texte de la publication n'a pas d'email, une image dont l'OCR ne lit aucun email (`ocr_emails`, tolère « rh @ boa .mg ») est écartée (`ai_reason` « aucun email lu par l'OCR ») ; publication sans aucun email (texte ni images) jamais envoyée, marquée `analysis_skipped` et plus retentée tant que le filtre est actif. Mesuré sur les vraies analyses : 22 images sur 88 écartées, 37 emails sur 38 trouvés par l'IA étaient lus par l'OCR (le 38e n'était pas un email valide) ; prix : les offres sans email (16 sur 84, candidature par téléphone…) ne sont plus récupérées. Mesuré sur une vraie publication de 24 affiches : 649 662 → 11 535 tokens, mêmes entreprises et emails, plus d'offres. L'OCR part lui aussi **par lots** de `MAX_IMAGES_PER_CALL` : en un seul appel (27 000 caractères) le modèle sautait les offres des dernières affiches. L'OCR manquant est rattrapé avant l'analyse (aussi avec `--skip-scrape`). Publication déjà connue sous un autre pfbid (`KnownPosts` : au moins la moitié des photos `fbid` en commun — une photo réutilisée seule ne suffit pas — ou même texte sans photo) : ni re-scrapée ni ré-analysée (`canonical_posts`) ; ignorée à la consolidation.

Fenêtre de fraîcheur (`--max-age-days`, défaut **15** pour les pages ET les groupes, 0 = désactivé) : seules les publications des N derniers jours, aujourd'hui compris, sont récupérées — le 15 septembre, du 1er au 15 ; le 31 août est trop ancien (`fb_dates.is_too_old`, date = infobulle sinon texte affiché ; date illisible = gardée). Dans `_scroll_feed` (`ScrapeOptions.max_age_days`) : publication trop ancienne ni enregistrée ni téléchargée ; `OLD_STREAK_STOP` (5) trop anciennes d'affilée arrêtent le fil (une vieille publication épinglée en tête ne l'arrête pas). Une publication déjà connue est jugée sur sa date enregistrée. Pages : les publications en cache trop anciennes ne passent plus ni par la visionneuse, ni par l'OCR, ni par l'IA ; celles déjà analysées restent dans la consolidation (déjà en base, sinon signalées à tort comme à supprimer).

Garde-fous production (pages) :
- **Coût IA** : jamais d'appel pour une publication déjà analysée ou doublon ; `--reanalyze` ne refait que les analyses d'un autre modèle / `PROMPT_VERSION` (cache utilisé) ; erreur IA définitive non retentée sauf `--reanalyze` ; publication dont l'OCR manque = pas d'envoi (sinon chaque image partirait en haute résolution) sauf `--send-all-images` ; `--max-ai-tokens N` arrête l'analyse au budget. Cache par lot (`ai_cache/batches/`) : un lot en échec ne fait pas refacturer les lots déjà reçus. Réponse coupée (`finish_reason: length`) ou JSON illisible → lot scindé en deux (`_OutputTruncated`) ; 429 → `Retry-After` respecté ; image corrompue ou absente → pas de plantage.
- **Offres** : clé de registre par contenu (`post_id|entreprise|intitulé|date limite`), pas par rang — une ré-analyse qui renvoie les offres dans un autre ordre garde les mêmes `job_id` ; attribution en deux passes (ids connus puis ids neufs, jamais en double) ; deux offres identiques d'une MÊME publication ne sont jamais regroupées, et leur clé de registre reçoit `#2`, `#3` dans l'ordre des offres (sans ce suffixe elles partageaient une entrée et échangeaient leur `job_id` à chaque consolidation — vu sur de vraies données : offre répétée par l'IA « (H/F) » / « H/F ») ; offre sans intitulé écartée ; `merge_results` distingue les homonymes par entreprise / date limite / lieu.
- **Entreprises** : contact vu avec ≥ 2 noms dissemblables = pas une preuve pour fusionner ; vu avec ≥ `GENERIC_MIN_COMPANIES` (3) entreprises = agence / page relais, retiré aussi des fiches (`registry.generic_keys`). `names_similar` tolère les variantes de l'IA (préfixe commun ≥ 5 lettres, noms collés). Pays normalisé sur `CountryProfile.name` (filtre exact du frontend). Alias de fusion résolus en chaîne.
- **Visionneuse** : page de connexion détectée → phase images interrompue sans marquer `images_done` ; vignettes du fil enregistrées sous `<post_id>_feed_<n>` (jamais réutilisées à la place d'une photo pleine résolution) ; avertissement après 3 échecs d'affilée.
- **Verrou** `.run_<pays>.lock` (msvcrt / fcntl, libéré même en cas de plantage) : deux lancements simultanés du même pays refusés.
- `ai_extractor.py` — Chat Completions + Structured Outputs (`RESULT_SCHEMA` strict) : texte, contexte de page, OCR et images (base64, ≤ 1600 px, `detail: high`) par lots de `MAX_IMAGES_PER_CALL`, fusionnés par `merge_results`. Publications sans image envoyées aussi. Cache `ai_cache/<sha256>.json` (modèle + `PROMPT_VERSION` + contenu) : changer le prompt → incrémenter `PROMPT_VERSION`.
- `company_registry.py` — dédoublonnage multi-pages : clés email / domaine web (hors messageries et réseaux, `GENERIC_HOSTS`) / page Facebook / téléphone E.164 / nom normalisé (sans formes juridiques) + ville ; union-find ; jamais de fusion si sites ou pages Facebook différents ; contact associé à des noms dissemblables = « partagé » (page relais, agence) et ignoré. `company_id` = `fb-<sha1>` stable via `identity_registry_<pays>.json` (fusion de deux ids connus → alias).
- `countries.py` — `CountryProfile` (nom, ISO3, indicatif, longueur du numéro national) ; `fb_dates.py` — infobulle ou « 3 j » → `published_at` + précision.

Sorties `downloaded_files/facebook_pages/` : `companies_<pays>.json` (champs **exactement** ceux de `company.model.ts`), `jobs_<pays>.json` (`job.model.ts` : missions dans `detail.sections[{heading:"Missions",…}]`, lieu/contrat/salaire/date limite/source dans `detail.criteria`, `company_profile` = document entreprise), `job_publications_<pays>.json` (`job_publication.model.ts` : `{job_id, published_at}`), `analysis_<pays>.json` (revue : réponses IA, sources, fusions, et ce que le backend ne stocke pas — adresse, produits/services, catégories). Téléphones au format local (`0341234567`) dans les documents, E.164 pour le dédoublonnage.

Offres republiées (`dedupe_offers`, pages relais qui republient la même offre plusieurs jours) : même entreprise (`company_id`, sinon nom normalisé), même intitulé normalisé (`_title_key` : sans accents, sans « (e) » / « H/F »), même date limite (`_deadline_key`), publications à ≤ `JOB_REPOST_WINDOW_DAYS` jours d'écart → **un seul job_id**, `published_at` = 1re publication, contenu = version la plus complète (`_merged_offer`) avec les contacts de toutes les republications. Offre sans intitulé ni entreprise : jamais regroupée. Une offre sans nom d'entreprise est rattachée à l'unique entreprise de sa publication (`_offer_company_id`). `job_id` stable : `job_registry_<pays>.json` retient le job_id de chaque publication d'offre (ne pas le reconstruire depuis la date de 1re publication : elle change quand une publication plus ancienne est découverte). Détail des regroupements : `analysis_<pays>.json` → `jobs[job_id].occurrences`.

Envoi au backend (`backend_push.py`), **uniquement** avec `--push` (après consolidation) ou `--push-only` (JSON déjà générés, ni navigateur ni IA) : `GET /health`, puis `POST /api/companies` → `POST /api/jobs` → `POST /api/jobs/:job_id/publication` (entreprises d'abord). Incrémental : `push_state_<pays>.json` = sha256 de chaque document envoyé avec succès, un document inchangé n'est pas renvoyé (`--force-push` pour tout renvoyer) ; 4xx = refusé et non marqué, réseau/5xx = retenté. `SCRAPER_API_URL` (défaut `http://localhost:3500`, vide = désactivé).

Nettoyage des doublons déjà envoyés (`backend_push.clean_stale`, après l'envoi, désactivable avec `--no-clean`) : un id présent dans `push_state` mais plus dans les JSON (entreprise fusionnée, offre republiée regroupée, offre d'un doublon de pfbid) est supprimé par `DELETE /api/jobs/:job_id` (supprime aussi sa date de publication) puis `DELETE /api/companies/:company_id`, **seulement** si `pages.stale_replacement_finder` lui trouve un remplaçant déjà envoyé avec son contenu actuel :
- offre : même intitulé comparable (`_offer_signature` : sans « (Réf …) », « (ère) », « .ve », « H/F ») et même entreprise (alias du registre d'identité résolus), ou même publication d'origine (`analysis_<pays>.json` → `duplicate_posts` ramène un doublon de pfbid à la publication gardée) ;
- entreprise : alias vers une entreprise des JSON, et plus aucune offre en base qui la référence (offres nettoyées d'abord).
Sans remplaçant (ex. offre trouvée seulement dans l'analyse d'un doublon, absente de celle de la publication gardée) : gardé en base, signalé à chaque envoi. Remplaçant refusé par le backend : suppression reportée. Document déjà supprimé à la main : retiré de `push_state`. Routes DELETE absentes (backend lancé avant leur ajout, `npm run dev` ne recharge pas) : 404 → message « redémarrez le backend », rien supprimé, réessayé au prochain envoi.

### Backend (`backend/`)
```bash
npm run dev     # ts-node src/app.ts — API sur :3500
npm run build   # tsc
npm start       # node dist/app.js
```

### Frontend (`frontend/`)
```bash
npm run dev       # Vite dev server, proxy /api -> http://localhost:3500
npm run build      # tsc -b && vite build
npm run lint       # eslint .
npm run preview
```

## Architecture du scraper

```
scrapers/
├── countries.py                 # dict COUNTRIES: code -> CountryConfig (35 pays)
├── cv_downloader.py             # téléchargement PDF des CV via CDP Network.loadNetworkResource
├── email_extractor.py           # extraction d'emails/téléphones via Google AI Mode (shadow DOM)
├── emploi_scraper.py            # JsonStore + ApiClient (utilitaires partagés, réutilisés par base_scraper)
├── ia_cv_uploader.py            # envoi des CV vers l'IA WipWork (POST /parse/resume)
└── common/
    ├── country_config.py        # CountryConfig (frozen dataclass) — toutes les URLs + CvDownloadPattern
    ├── country_iso.py            # code pays interne -> ISO 3166-1 alpha-3 (country_ids de l'API IA)
    ├── models.py                 # JobListing, CompanyProfile (dataclasses slots=True)
    ├── helpers.py                # normalize_text, extract_job_id, html_text, first_text
    ├── parsers.py                 # fonctions BS4 pures — reçoivent (soup, config)
    ├── browser_helpers.py        # maybe_solve_captcha(sb)
    ├── cv_parser.py               # extraire_infos_cv(path) — extraction texte/emails/téléphones depuis un PDF
    └── base_scraper.py           # BaseJobScraper(config, api_url) — orchestration Selenium principale
```

`scrapers/emploi_scraper.py` définit `JsonStore` (cache JSON par `job_id`/`company_id`) et `ApiClient` (POST vers le backend) ; `base_scraper.py` les importe (`from scrapers.emploi_scraper import ApiClient, JsonStore`). Ce fichier contient aussi une classe `EmploiMaScraper` historique câblée en dur sur emploi.cg — code legacy, ne pas l'utiliser comme référence pour du nouveau pays ; passer par `CountryConfig` + `BaseJobScraper`.

### Flux principal (`BaseJobScraper.scrape_all`)
1. `_scrape_recruiter_list` : parcourt les pages de `recruiters_url`, collecte les entreprises (id + nom).
2. Pour chaque entreprise : `_get_or_scrape_company` (cache-first) puis, si `has_jobs`, `_scrape_company_jobs` qui pagine les offres et scrape chaque détail via `_scrape_job_detail`.
3. Chaque offre/entreprise nouvellement scrapée est écrite dans le `JsonStore` local **et** poussée au backend si `api_client` est configuré.
4. `CloudflareBlockError` déclenche une pause de 5 min puis relance toute la session Selenium (pas juste la page).

### Patterns importants
- **Cache** : `JsonStore` — clé = `job_id` ou `company_id` ; `job_already_scraped` interroge le backend en priorité (`api_client.job_exists`), sinon le cache local.
- **Skip** : offre déjà connue → ignorée ; entreprise sans offres (`has_jobs=False`, détecté via présence d'un lien `is_recruiter_nid`) → pas de scraping de ses jobs.
- **Retry** : 3 tentatives sur pages détail/entreprise, 5 sur pages listing, `refresh()` + `maybe_solve_captcha` entre chaque tentative.
- **CDP mode** : `sb.activate_cdp_mode()` requis dès l'ouverture — ne jamais lire `sb.driver.page_source`, toujours `sb.get_page_source()`.
- **Pagination** : `?page=N`, `get_total_pages()` cherche `li.pager-item a[title='...']` (titre FR/EN selon le pays).
- **Sortie fichiers** : `downloaded_files/jobs_{code}.json`, `companies_{code}.json`, `cv_files/{code}/`, `company_emails.json`.
- **Profils Chrome persistants** : `my_custom_profile*` (créés/connectés via `setup_profil.py`), utilisés pour garder des sessions authentifiées entre lancements (ex. Google AI Mode pour `email_extractor.py`).

### CountryConfig — champs clés
```python
CountryConfig(
    code="burkina",                       # clé dans COUNTRIES, préfixe des fichiers de sortie
    base_url="https://www.emploiburkina.com",
    jobs_search_path="/recherche-jobs-burkina-faso",
    recruiters_path="/employer",          # "/recruteurs" (FR) ou "/recruiters" (EN)
    recruiter_path_prefix="/recruiter/",  # "/recruteur/" (FR) ou "/recruiter/" (EN)
    cv_storage_path="/sites/default/files/private/cv/",  # optionnel, pour cv_downloader
    cv_patterns=(...),                    # optionnel, motifs de nommage des CV PDF
)
```
Ajouter un pays = ajouter une entrée dans `scrapers/countries.py` ; tout le reste (URLs, extraction d'ID, pagination) suit automatiquement via les propriétés/méthodes de `CountryConfig`.

## Backend (`backend/src/`)

- `app.ts` — Express, CORS, middleware de log de chaque requête, monte `/api/jobs`, `/api/companies`, `/api/cvs`, `/health`. Port via `process.env.PORT` (défaut 3500).
- `models/` — Mongoose : `job.model.ts`, `company.model.ts`, `job_publication.model.ts` (historique de publication d'une offre), `cv.model.ts`.
- `routes/` — `jobs.routes.ts`, `companies.routes.ts`, `cv.routes.ts` ; c'est l'API que consomment à la fois le scraper Python (upserts) et le frontend (lecture). `DELETE /api/jobs/:job_id` (offre + sa date de publication) et `DELETE /api/companies/:company_id` servent au nettoyage des doublons du script Facebook ; ils répondent 200 avec `deleted: false` si le document n'existe plus, pour qu'un 404 signale une route absente.
- Routes dédiées à l'envoi IA (dans `cv.routes.ts`) : `GET /api/cvs/ia/pending` (sélection `commercial_email_wave` + non désinscrits + non envoyés — déclarée **avant** `GET /api/cvs/:id`, sinon Express l'intercepte), `GET /api/cvs/ia/stats` (avancement), `PATCH /api/cvs/:id/ia` (marquage `sent`/`failed`/`skipped`). Le filtre est centralisé dans `buildIaSelectionFilter()`.
- `commercial_email_wave` / `commercial_email_unsubscribed` sont écrits par l'outil d'emailing (écriture Mongo directe, hors backend) ; ils sont déclarés dans `cv.model.ts` uniquement pour documenter la sélection IA.
- `config/db.ts` — connexion MongoDB.

## Frontend (`frontend/src/`)

- **Proxy dev** : `/api` → `http://localhost:3500` (dans `vite.config.ts`) ; en prod, `API_BASE_URL` vient de `VITE_API_URL`.
- **Tailwind v4** : un seul `@import "tailwindcss"` dans `index.css`, plugin `@tailwindcss/vite` (pas de `tailwind.config.js` classique).
- Couches : `api/` (fetch bruts) → `services/` (logique métier, ex. `filterCompanies()` côté client dans `companiesService.ts`) → `hooks/` (`useCompanies`, `useJobs`, `useStats`) → `components/` (`ui/` génériques, `layout/`, `dashboard/`) → `pages/` (`Dashboard.tsx`, `CompanyDetail.tsx`).
- Pas de pagination serveur pour l'instant : le dashboard fetch avec une grosse limite (ex. 500 entreprises) et filtre/pagine côté client.
- Icônes : `lucide-react`.

## À ne pas faire

- Ne pas utiliser `sb.driver.page_source` en mode CDP → toujours `sb.get_page_source()`.
- Ne pas appeler `sb` en dehors du bloc `with SB(...) as sb:`.
- Ne pas dupliquer la logique d'URL/extraction d'ID pays — tout passe par `CountryConfig`.
- Ne pas prendre `scrapers/emploi_scraper.py::EmploiMaScraper` comme modèle pour un nouveau scraper — c'est du code legacy spécifique à un site ; le chemin courant est `BaseJobScraper` + `CountryConfig`.
- Ne pas envoyer un CV à l'IA sans `country_ids` : les téléphones stockés sont au format local (`0786004809`), pas E.164 — l'API ne peut rien déduire et répond 400. Si un pays n'est pas dans `country_iso.py`, le CV est volontairement skippé (jamais envoyé avec un pays approximatif).
- `senegal_script/scraper.py` (senjob.com) : ne jamais parser le bloc `<script type="application/ld+json">` avec `json.loads()` — le champ `description` contient des retours à la ligne bruts qui font planter le parsing JSON strict ; extraire les champs par regex ciblée (voir `_parse_json_ld`). Les emails de recruteur n'apparaissent dans le HTML que pour une session candidate connectée (jamais en anonyme) — toujours passer par le profil persistant `my_custom_profile_senegal`.
- `facebook_script/scraper.py` : ne pas lire les publications avec `innerText`/`outerHTML` côté page ni `sb.cdp.get_element_html()` — l'horodatage (« 1 h ») est dans un shadow root **fermé**, seul `DOM.getOuterHTML(includeShadowDOM)` le sérialise (`<template shadowrootmode="closed">`). Parser ce HTML avec `string_containers=SOUP_STRING_CONTAINERS` : avec les réglages par défaut, BeautifulSoup exclut le texte des `<template>` de `get_text()`. Ne pas passer par `sb.cdp.find_element()` par publication (refait un `DOM.getDocument` complet, très lent sur Facebook) — `_outer_html` passe par `Runtime.evaluate` → `objectId`.
- `facebook_script/scraper.py` : le vrai permalien n'est dans le lien horodatage qu'après survol (href factice `?__cft__...#?bek` avant) ; sans lui, `post_id` = `hash-…` dérivé du contenu. Les blocs `aria-hidden="true"` répétant « Facebook » sont des leurres anti-scraping — toujours les exclure du texte. Les `[role="article"]` imbriqués dans une publication sont des commentaires (retirés avant parsing). Une fois « Voir plus » cliqué, Facebook insère un bouton « Voir moins » dans la dernière ligne du texte : ne jamais l'ajouter à `SEE_MORE_LABELS` (le JS le cliquerait et replierait le texte) — il est dans `SEE_LESS_LABELS`, uniquement retiré du texte côté Python.
- `facebook_script/scraper.py` : ne pas régler le niveau de log de RapidOCR via `logging.getLogger("RapidOCR")` — le constructeur le réinitialise ; passer `params={"Global.log_level": "error"}`. Les regex d'emails/téléphones s'appliquent après `_contact_text` (NFKC + retrait des emojis « keycap ») : les publications écrivent souvent les numéros en chiffres stylisés (`𝟎𝟑𝟒`, `0️⃣3️⃣4️⃣`). Dans `_heading`, une ligne sans « : » qui finit par `;` `,` `.` n'est jamais un intitulé (sinon « … sens des responsabilités; » ouvrirait une rubrique « tâches »).
- `facebook_script/pages.py` : dans la visionneuse, ne lire une photo que si l'image est chargée (`complete && naturalWidth > 0`) — pendant un changement de photo `img.src` peut valoir l'URL de la page, téléchargée alors comme « image » HTML. Ne pas considérer la page qui publie comme l'entreprise des offres qu'elle relaie : seul `is_page_owner` (décidé par l'IA) lui rattache l'id de la page et son logo. Ne pas ajouter de champ hors modèle Mongoose dans `companies_*`/`jobs_*` (supprimé silencieusement à l'upsert) : les informations sans champ backend vont dans `analysis_*`.

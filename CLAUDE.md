# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ce fichier documente le projet en français (langue du code et des logs) pour rester cohérent avec le reste du repo.

## Vue d'ensemble

Trois sous-systèmes indépendants qui communiquent via une API REST :

1. **Scraper Python** (racine + `scrapers/`) — scrape des offres d'emploi et profils d'entreprises sur 35 sites africains de type "emploi.xx" (plateforme Drupal générique), via SeleniumBase en mode CDP (contourne Cloudflare/captcha).
2. **Backend Node.js/Express** (`backend/`) — API REST + MongoDB, reçoit les données du scraper et les sert au frontend.
3. **Frontend React** (`frontend/`) — dashboard de visualisation (Vite + Tailwind v4).
4. **Script Sénégal** (`senegal_script/`) — scraper indépendant dédié à senjob.com (site PHP, pas Drupal — distinct du pays `"senegal"` de `scrapers/countries.py` qui cible emploisenegal.com). Voir section dédiée plus bas.

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
└── common/
    ├── country_config.py        # CountryConfig (frozen dataclass) — toutes les URLs + CvDownloadPattern
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
- `routes/` — `jobs.routes.ts`, `companies.routes.ts`, `cv.routes.ts` ; c'est l'API que consomment à la fois le scraper Python (upserts) et le frontend (lecture).
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
- `senegal_script/scraper.py` (senjob.com) : ne jamais parser le bloc `<script type="application/ld+json">` avec `json.loads()` — le champ `description` contient des retours à la ligne bruts qui font planter le parsing JSON strict ; extraire les champs par regex ciblée (voir `_parse_json_ld`). Les emails de recruteur n'apparaissent dans le HTML que pour une session candidate connectée (jamais en anonyme) — toujours passer par le profil persistant `my_custom_profile_senegal`.

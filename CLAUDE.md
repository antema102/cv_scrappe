# CLAUDE.md — Contexte projet pour l'IA

Ce fichier permet à l'IA de comprendre le projet sans lire tous les fichiers.

## Architecture en un coup d'œil

```
cv_scrappe/
├── index.py                        # CLI : python index.py [--country CODE] [--list]
├── emploi_scraper.py               # JsonStore + ApiClient (partagés)
├── scrapers/
│   ├── countries.py                # COUNTRIES dict — 35 pays configurés
│   └── common/
│       ├── country_config.py       # CountryConfig dataclass (frozen) — toute la logique URL
│       ├── models.py               # JobListing, CompanyProfile (dataclasses slots=True)
│       ├── helpers.py              # normalize_text, extract_job_id, html_text, first_text
│       ├── parsers.py              # Fonctions BS4 pures — reçoivent (soup, config)
│       └── base_scraper.py        # BaseJobScraper(config, api_url) — toute la logique Selenium
├── backend/src/
│   ├── app.ts                      # Express + MongoDB :3000
│   ├── models/ job.model.ts, company.model.ts
│   └── routes/ jobs.routes.ts, companies.routes.ts
└── frontend/src/
    ├── api/          config.ts, companies.ts, jobs.ts
    ├── types/        company.ts, job.ts, api.ts
    ├── services/     companiesService.ts, jobsService.ts
    ├── hooks/        useCompanies.ts, useJobs.ts, useStats.ts
    ├── utils/        formatDate.ts
    ├── components/
    │   ├── ui/       Badge, Card, Input, Select, Button, Skeleton
    │   ├── layout/   Header
    │   └── dashboard/ StatsCard, CompanyCard, JobCard, SearchFilters, CompanyGrid, RecentJobs
    └── pages/        Dashboard.tsx
```

## Responsabilités clés

| Fichier | Rôle | Modifier si… |
|---|---|---|
| `scrapers/countries.py` | Config des 35 pays | Ajouter/modifier un pays |
| `common/country_config.py` | URLs + extract_id | Changer la structure d'URL de la plateforme |
| `common/parsers.py` | Sélecteurs CSS HTML | Le site change son HTML |
| `common/base_scraper.py` | Flux de scraping | Changer la logique de navigation |
| `emploi_scraper.py` | JsonStore, ApiClient | Changer le stockage ou l'API |

## Patterns importants

- **Cache** : `JsonStore` (JSON fichier) — clé = `job_id` ou `company_id`
- **Skip** : offre déjà en cache → ignorée ; entreprise sans offres (`has_jobs=False`) → pas de navigation
- **Retry** : 3 tentatives sur pages détail/entreprise, 5 sur pages listing
- **Captcha** : `sb.solve_captcha()` appelé silencieusement à chaque retry
- **CDP mode** : `sb.activate_cdp_mode()` requis — ne jamais utiliser `sb.driver.page_source`
- **Pagination** : `?page=N` — `get_total_pages()` cherche `li.pager-item a[title='...']` (FR + EN)
- **Sortie** : `downloaded_files/jobs_{code}.json` et `companies_{code}.json` par pays

## CountryConfig — champs

```python
CountryConfig(
    code="burkina",                          # clé dans COUNTRIES, préfixe fichiers
    base_url="https://www.emploiburkina.com",
    jobs_search_path="/recherche-jobs-burkina-faso",
    recruiters_path="/employer",           # ou "/recruiters" (EN)
    recruiter_path_prefix="/recruiter/",     # ou "/recruiter/" (EN)
)
```

## Commandes utiles

```bash
python index.py                      # tous les pays
python index.py --country burkina    # un seul pays
python index.py --list               # liste les codes

cd backend && npm run dev            # API Node.js sur :3000
cd frontend && npm run dev           # Dashboard React sur :5173
```

## Frontend — points clés

- **Proxy Vite** : `/api` → `http://localhost:3000` (configuré dans `vite.config.ts`)
- **Tailwind v4** : import unique `@import "tailwindcss"` dans `index.css`, plugin `@tailwindcss/vite`
- **Filtres** : côté client — `filterCompanies()` dans `companiesService.ts`
- **Stats** : agrégées depuis les réponses API (`total` + `getUniqueCountries/Sectors`)
- **Pagination** : non gérée côté UI pour l'instant — fetch limit=500 entreprises
- **Icônes** : `lucide-react`

## Ce qu'il ne faut PAS faire

- Ne pas utiliser `sb.driver.page_source` en mode CDP → utiliser `sb.get_page_source()`
- Ne pas appeler `sb` hors du bloc `with SB(...) as sb:`
- Ne pas modifier `emploi_scraper.py` pour la logique métier — c'est uniquement utilitaire
- Ne pas dupliquer la logique URL — tout passe par `CountryConfig`

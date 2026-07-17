# cv_scrappe

Scraper d'offres d'emploi africaines basé sur SeleniumBase.
Supporte **35 pays** via une architecture générique configurable.

---

## Prérequis

```bash
pip install seleniumbase beautifulsoup4 requests
```

Backend Node.js (optionnel) :
```bash
cd backend
npm install
npm run dev
```

---

## Utilisation

### Scraper tous les pays
```bash
python index.py
```

### Scraper un pays spécifique
```bash
python index.py --country burkina
python index.py --country maroc
python index.py --country ghana
```

### Lister tous les codes pays disponibles
```bash
python index.py --list
```

### Télécharger des CV (emploi.ma)
```bash
# Essai anonyme
python index.py --download-cvs

# Avec cookie de session (CV privés)
python index.py --download-cvs --cv-cookie "nom_cookie=valeur; autre_cookie=valeur"

# Ou via variable d'environnement
set EMPLOI_MA_COOKIE=nom_cookie=valeur; autre_cookie=valeur
python index.py --download-cvs
```

### Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `SCRAPER_API_URL` | `http://localhost:3000` | URL du backend Node.js. Mettre à vide pour désactiver l'envoi API |

```bash
# Désactiver l'envoi vers le backend
SCRAPER_API_URL= python index.py --country burkina
```

---

## Pays supportés

| Code | Pays | Site |
|---|---|---|
| `maroc` | Maroc | emploi.ma |
| `cote_ivoire` | Côte d'Ivoire | emploi.ci |
| `senegal` | Sénégal | emploisenegal.com |
| `burkina` | Burkina Faso | emploiburkina.com |
| `cameroun` | Cameroun | emploi.cm |
| `congo` | Congo | emploi.cg |
| `congo_rdc` | Congo RDC | emploi.cd |
| `guinee` | Guinée | emploiguinee.com |
| `togo` | Togo | emploi.tg |
| `gabon` | Gabon | emploi.ga |
| `mali` | Mali | emploimali.com |
| `benin` | Bénin | emploibenin.com |
| `niger` | Niger | nigerjob.net |
| `tchad` | Tchad | emploi.td |
| `algerie` | Algérie | algeriejob.com |
| `tunisie` | Tunisie | emploitunisie.com |
| `mauritanie` | Mauritanie | emploimauritanie.com |
| `burundi` | Burundi | emploi.bi |
| `centrafrique` | Centrafrique | emploi.cf |
| `ghana` | Ghana | ghanajob.com |
| `nigeria` | Nigeria | nigeriajob.com |
| `kenya` | Kenya | kenyajob.com |
| `south_africa` | Afrique du Sud | zajob.com |
| *(+ 12 autres)* | … | … |

---

## Données produites

Chaque pays génère deux fichiers dans `downloaded_files/` :

```
downloaded_files/
  jobs_burkina.json
  companies_burkina.json
  jobs_maroc.json
  companies_maroc.json
  …
```

Structure d'une offre (`jobs_{code}.json`) :
```json
{
  "job_id": "312345",
  "title": "Développeur Python",
  "job_url": "https://…",
  "company_name": "ACME",
  "company_id": "10836",
  "detail": {
    "headline": "…",
    "description": "…",
    "qualifications": [],
    "criteria": {},
    "skills": [],
    "sections": []
  },
  "company_profile": { … }
}
```

---

## Architecture

```
cv_scrappe/
├── index.py                    # Point d'entrée CLI
├── emploi_scraper.py           # Utilitaires partagés (JsonStore, ApiClient)
├── scrapers/
│   ├── countries.py            # Config de tous les pays
│   └── common/
│       ├── country_config.py   # CountryConfig (URLs, extract_id…)
│       ├── models.py           # JobListing, CompanyProfile
│       ├── helpers.py          # Utilitaires texte
│       ├── parsers.py          # Parsing HTML (BS4)
│       └── base_scraper.py     # BaseJobScraper (logique principale)
└── backend/                    # API REST Node.js + MongoDB
    └── src/
        ├── app.ts
        ├── models/
        └── routes/
```

## Ajouter un nouveau pays

Ouvrir `scrapers/countries.py` et ajouter une entrée :

```python
"tanzanie": CountryConfig(
    code="tanzanie",
    base_url="https://www.tanzaniajob.com",
    jobs_search_path="/job-vacancies-search-tanzania",
    recruiters_path="/recruiters",       # /recruteurs (FR) ou /recruiters (EN)
    recruiter_path_prefix="/recruiter/", # /recruteur/ (FR) ou /recruiter/ (EN)
),
```

C'est tout — le scraper tourne automatiquement avec la config.

"""
senegal_script/scraper.py
==========================
Scrape les offres d'emploi de senjob.com (Senegal).

Etape 1 - Connexion :
Une fenetre Chrome s'ouvre sur l'espace candidat de senjob.com. Connectez-vous
manuellement avec vos identifiants, puis appuyez sur ENTREE dans le terminal
pour lancer le scraping. La session est sauvegardee dans le profil Chrome
persistant "my_custom_profile_senegal" (a la racine du projet) : les lancements
suivants reutilisent cette session tant qu'elle reste valide.

Detection automatique : a chaque lancement (hors --headless), le script
verifie d'abord si ce profil est deja connecte (absence du champ mot de passe
sur la page de connexion) avant de demander quoi que ce soit. Si la session
est encore valide, aucune saisie n'est demandee - passage direct au scraping.

Etape 2 - Scraping :
Le script parcourt https://senjob.com/offres-d-emploi.php (toutes les pages
de resultats), recupere le lien de chaque offre puis visite sa page de detail
pour en extraire titre, reference, localisation, expiration, categories,
description et coordonnees du recruteur (email si disponible). Le nom de
l'entreprise (extrait du bloc JSON-LD quand present) est egalement enregistre
dans un cache/table entreprises separe (downloaded_files/companies_senjob.json,
table companies_scrappe cote backend) — un identifiant d'entreprise est derive
du nom de fichier du logo (senjob n'a pas de page profil entreprise publique
comme emploi.ma), avec repli sur un slug du nom si aucun logo n'est trouve.

Certaines offres publiees directement sur senjob.com (par opposition aux
offres relayees depuis des flux d'ONG externes) n'affichent leur contenu
complet qu'aux candidats connectes, voire abonnes. Le scraper detecte ce cas
en interne et enregistre les champs disponibles depuis le listing sans jamais
planter.

Modele de donnees unifie : le detail enregistre/pousse (build_unified_detail)
utilise EXACTEMENT la meme forme que les 35 sites emploi.xx -
{job_url, headline, description, qualifications, criteria, skills, sections}
(voir backend/src/models/job.model.ts) - aucun champ specifique a senjob a la
racine du detail. Les donnees propres a senjob sans equivalent direct
(reference, localisation, expiration, contact recruteur, verrouillage...) sont
rangees dans `criteria` (deja un champ libre cle/valeur pour les autres sites).

Filtrage entreprise : une offre pour laquelle aucun company_id n'a pu etre
derive (ni logo, ni nom d'entreprise trouve) n'est JAMAIS enregistree dans
jobs_senjob.json ni poussee au backend (jobs_scrappe) - ces offres serviront
a une campagne d'emailing par entreprise, une offre sans entreprise identifiee
n'y a pas sa place. Elle est tout de meme tracee dans un cache local separe
(downloaded_files/jobs_senjob_skipped.json) pour ne pas la re-scraper a
chaque lancement.

Usage :
    python senegal_script/scraper.py                  # interactif, toutes les pages
    python senegal_script/scraper.py --limit 5         # test rapide, 5 nouvelles offres
    python senegal_script/scraper.py --max-pages 2      # limite le nombre de pages de listing
    python senegal_script/scraper.py --headless         # sans fenetre (session deja connectee)

Sortie : downloaded_files/jobs_senjob.json (cle = job_id),
downloaded_files/companies_senjob.json (cle = company_id) et
downloaded_files/jobs_senjob_skipped.json (offres sans entreprise identifiee).
Variable d'environnement SCRAPER_API_URL (defaut http://localhost:3500) :
vide = pas de push backend, cache JSON local uniquement.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from seleniumbase import SB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

PROFILE_DIR = PROJECT_ROOT / "my_custom_profile_senegal"
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
JOBS_FILE = OUTPUT_DIR / "jobs_senjob.json"
COMPANIES_FILE = OUTPUT_DIR / "companies_senjob.json"
SKIPPED_FILE = OUTPUT_DIR / "jobs_senjob_skipped.json"

BASE_URL = "https://senjob.com"
LOGIN_URL = f"{BASE_URL}/jobseekers/index.php"
LISTING_URL = f"{BASE_URL}/offres-d-emploi.php"

BACKEND_URL: str = os.getenv("SCRAPER_API_URL", "http://localhost:3500")
DEFAULT_DELAY = 1.5

ID_PREFIX = "senjob-"  # evite toute collision avec les job_id/company_id des 35 sites emploi.xx (memes collections MongoDB, cle unique)

JOB_ID_RE = re.compile(r"_e_(\d+)\.html")
PAGE_NUM_RE = re.compile(r"[?&]page=(\d+)")
LD_JSON_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LOGO_ID_RE = re.compile(r"/(\d+)\.\w+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_REFERENCE_LABELS = {"Référence", "Localisation", "Expiration"}


# ---------------------------------------------------------------------------
# Cache JSON local + client backend (memes interfaces que scrapers/emploi_scraper.py,
# dupliquees ici pour garder ce dossier independant du package `scrapers`)
# ---------------------------------------------------------------------------


class JsonStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.file_path.exists():
            return {}
        try:
            payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return {str(k): dict(v) for k, v in payload.items() if isinstance(v, dict)}

    def save(self) -> None:
        self.file_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def has(self, key: str) -> bool:
        return key in self._data

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value
        self.save()

    def count(self) -> int:
        return len(self._data)


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def job_exists(self, job_id: str) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/jobs/{job_id}", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def upsert_job(self, job: dict[str, Any]) -> None:
        try:
            requests.post(f"{self.base_url}/api/jobs", json=job, timeout=10)
        except Exception as exc:
            print(f"  [API] echec envoi offre {job.get('job_id')} : {exc}")

    def upsert_job_publication(self, job_id: str, published_at: str | None) -> None:
        try:
            requests.post(
                f"{self.base_url}/api/jobs/{job_id}/publication",
                json={"published_at": published_at},
                timeout=10,
            )
        except Exception:
            pass

    def upsert_company(self, company: dict[str, Any]) -> None:
        try:
            requests.post(f"{self.base_url}/api/companies", json=company, timeout=10)
        except Exception as exc:
            print(f"  [API] echec envoi entreprise {company.get('company_id')} : {exc}")


# ---------------------------------------------------------------------------
# Helpers texte (equivalents locaux de scrapers/common/helpers.py)
# ---------------------------------------------------------------------------


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _break_preserving_text(element: Any) -> str:
    """Convertit un element BS4 en texte en gardant un retour a la ligne par <br>."""
    if element is None:
        return ""
    for br in element.find_all("br"):
        br.replace_with("\n")
    lines = [line.strip() for line in element.get_text().split("\n")]
    return "\n".join(line for line in lines if line)


def _maybe_solve_captcha(sb: SB) -> None:
    try:
        sb.solve_captcha()
    except Exception:
        pass


def _reset_profile_exit_state(profile_dir: Path) -> None:
    """
    Chrome marque profile.exit_type="Crashed" apres une fermeture pilotee par
    Selenium (meme quand driver.quit() s'est bien passe cote script). Au
    lancement suivant sur ce meme profil, Chrome tente d'afficher un bandeau
    "Restaurer les pages ?" qui, en mode headless, bloque le rendu de la page
    et fait echouer wait_for_element (observe : la page listing ne charge
    plus jamais des le 2e lancement). On force donc une sortie "propre" dans
    les preferences du profil avant chaque lancement.
    """
    prefs_path = profile_dir / "Default" / "Preferences"
    if not prefs_path.exists():
        return
    try:
        data = json.loads(prefs_path.read_text(encoding="utf-8"))
        profile = data.setdefault("profile", {})
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        prefs_path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Parsing - page de listing (https://senjob.com/offres-d-emploi.php)
# ---------------------------------------------------------------------------


def get_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("div.resultsOffre a[href]")
    for a in anchors:
        if _normalize_text(a.get_text()) == ">|":
            match = PAGE_NUM_RE.search(a["href"])
            if match:
                return int(match.group(1))
    pages = []
    for a in anchors:
        match = PAGE_NUM_RE.search(a.get("href", ""))
        if match:
            pages.append(int(match.group(1)))
    return max(pages) if pages else 1


def parse_listing_page(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for tr in soup.select("table#offresenjobs tr"):
        link = tr.select_one("td:nth-child(3) > div > a")
        if link is None:
            continue
        href = link.get("href", "")
        match = JOB_ID_RE.search(href)
        if not match:
            continue

        job_id = f"{ID_PREFIX}{match.group(1)}"
        title = _normalize_text(link.get_text(" ", strip=True))

        location_short = ""
        loc_span = tr.select_one("td:nth-child(4) span.green_text_normal")
        if loc_span is not None:
            location_short = _normalize_text(loc_span.get_text(" ", strip=True))

        published_at = ""
        pub_hidden = tr.select_one("td:nth-child(5) span[style*='display:none']")
        if pub_hidden is not None:
            published_at = _normalize_text(pub_hidden.get_text())

        expire_at = ""
        exp_hidden = tr.select_one("td:nth-child(6) span[style*='display:none']")
        if exp_hidden is not None:
            expire_at = _normalize_text(exp_hidden.get_text())

        rows.append(
            {
                "job_id": job_id,
                "title": title,
                "job_url": href,
                "location_short": location_short,
                "published_at": published_at,
                "expire_at": expire_at,
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Parsing - page de detail (https://senjob.com/jobseekers/{slug}_e_{id}.html)
# ---------------------------------------------------------------------------


def _parse_json_ld(html: str) -> dict[str, str]:
    """
    Extrait les champs utiles du bloc JSON-LD JobPosting (present uniquement sur
    les offres publiques/syndiquees). Ce bloc N'EST PAS du JSON strict (le champ
    "description" contient des retours a la ligne bruts) -> json.loads() plante
    dessus. On extrait donc chaque champ par regex ciblee plutot que de parser
    l'ensemble.
    """
    match = LD_JSON_RE.search(html)
    if not match:
        return {}
    raw = match.group(1)
    fields: dict[str, str] = {}

    for key in ("title", "datePosted", "validThrough", "employmentType"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', raw)
        if m:
            fields[key] = m.group(1).strip()

    org_match = re.search(
        r'"hiringOrganization"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]*)"', raw, re.S
    )
    if org_match:
        fields["company_name"] = org_match.group(1).strip()

    loc_match = re.search(
        r'"addressLocality"\s*:\s*"([^"]*)"[^}]*?"addressCountry"\s*:\s*"([^"]*)"',
        raw,
        re.S,
    )
    if loc_match:
        fields["location_full"] = f"{loc_match.group(1).strip()} / {loc_match.group(2).strip()}"

    salary_match = re.search(
        r'"currency"\s*:\s*"([^"]*)"[^}]*?"value"\s*:\s*([\d.]+)[^}]*?"unitText"\s*:\s*"([^"]*)"',
        raw,
        re.S,
    )
    if salary_match and salary_match.group(2) not in ("0", "0.0"):
        fields["salary"] = f"{salary_match.group(2)} {salary_match.group(1)} / {salary_match.group(3)}"

    return fields


def _label_values(soup: BeautifulSoup) -> dict[str, str]:
    """Lit le tableau Reference/Localisation/Expiration (label dans un <td>, valeur dans le <td> suivant)."""
    result: dict[str, str] = {}
    for td in soup.find_all("td"):
        label = _normalize_text(td.get_text())
        if label in _REFERENCE_LABELS and label not in result:
            value_td = td.find_next("td")
            if value_td is not None:
                result[label] = _normalize_text(value_td.get_text(" ", strip=True))
    return result


def _parse_categories(soup: BeautifulSoup) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = []
    for tag_div in soup.select(".tagcompt"):
        label = _normalize_text(tag_div.get_text())
        if not label:
            continue
        anchor = tag_div.find_parent("a")
        cat_type, cat_id = "", ""
        if anchor is not None:
            href = anchor.get("href", "")
            m = re.search(r"[?&](Category|Secteur)=(\d+)", href)
            if m:
                cat_type, cat_id = m.group(1), m.group(2)
        categories.append({"label": label, "type": cat_type, "id": cat_id})
    return categories


def _parse_apply_info(soup: BeautifulSoup) -> tuple[str, str]:
    """Cherche le fieldset "Coordonnees du recruteur" -> (email, texte complet)."""
    for legend in soup.find_all("legend"):
        if "recruteur" not in _normalize_text(legend.get_text()).lower():
            continue
        fieldset = legend.find_parent("fieldset")
        if fieldset is None:
            continue

        email = ""
        mail_link = fieldset.select_one("a[href^='mailto:']")
        if mail_link is not None:
            email = mail_link.get("href", "").replace("mailto:", "").split("?")[0].strip()

        text = _break_preserving_text(fieldset)
        if not email:
            found = EMAIL_RE.search(text)
            if found:
                email = found.group(0)

        return email, text

    return "", ""


def _parse_title(soup: BeautifulSoup, fallback: str) -> str:
    for font in soup.find_all("font"):
        if "32px" in font.get("style", ""):
            text = _normalize_text(font.get_text())
            if text:
                return text
    return fallback


def _parse_logo(soup: BeautifulSoup) -> str:
    img = soup.select_one("img[src*='employers/images/']")
    if img is not None and img.get("src"):
        return urljoin(BASE_URL, img["src"])
    return ""


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug


def _derive_company_id(logo_url: str, name: str) -> str:
    """
    senjob.com n'a pas de page profil entreprise publique (contrairement a
    emploi.ma) -> pas d'id stable fourni par le site. Le nom de fichier du logo
    (ex: .../employers/images/1593619322.jpg) est en pratique l'id interne du
    compte recruteur cote senjob (verifie : deux offres du meme employeur
    partagent le meme logo) -> source la plus fiable. A defaut, on retombe sur
    un slug du nom (moins fiable : variations de casse/espaces possibles entre
    offres d'un meme employeur).
    """
    if logo_url:
        match = LOGO_ID_RE.search(logo_url)
        if match:
            return f"{ID_PREFIX}{match.group(1)}"
    if name:
        slug = _slugify(name)
        if slug:
            return f"{ID_PREFIX}{slug}"
    return ""


def _split_location(location_full: str) -> tuple[str, str]:
    """'Kolda, Sedhiou, Ziguinchor / Senegal' -> ('Kolda, Sedhiou, Ziguinchor', 'Senegal')."""
    if not location_full:
        return "", "Sénégal"
    if "/" in location_full:
        city, _, country = location_full.rpartition("/")
        city = _normalize_text(city)
        country = _normalize_text(country) or "Sénégal"
        return city, country
    return _normalize_text(location_full), "Sénégal"


def build_company_payload(
    company_id: str, name: str, logo_url: str, location_full: str, email: str
) -> dict[str, Any]:
    city, country = _split_location(location_full)
    return {
        "company_id": company_id,
        "company_url": "",  # pas de page profil entreprise publique sur senjob.com
        "name": name,
        "city": city,
        "country": country,
        "sector": "",  # senjob n'expose pas de secteur par entreprise (seulement des categories par offre)
        "website": "",  # pas de site web externe affiche sur senjob.com
        "description": "",
        "logo_url": logo_url,
        "emails": [email] if email else [],
    }


def parse_job_detail(html: str, job_url: str, fallback_title: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    ld_fields = _parse_json_ld(html)
    labels = _label_values(soup)
    description = _break_preserving_text(soup.find("div", id="articlebi"))
    apply_email, apply_text = _parse_apply_info(soup)
    categories = _parse_categories(soup)

    # Le bloc "Connexion candidat" (formulaire #formConnSenjob) n'apparait que
    # sur les offres a contenu partiel/tronque -> signal fiable de verrouillage,
    # contrairement a une description vide (qui peut juste etre un resume court
    # legitime). Absent sur les offres publiques completes (verifie sur offres
    # syndiquees depuis des flux ONG externes).
    gated = soup.find(id="formConnSenjob") is not None

    detail: dict[str, Any] = {
        "job_url": job_url,
        "reference": labels.get("Référence", ""),
        "location_full": ld_fields.get("location_full", "") or labels.get("Localisation", ""),
        "expire_text": labels.get("Expiration", ""),
        "description": description,
        "logo_url": _parse_logo(soup),
        "categories": categories,
        "apply_email": apply_email,
        "apply_text": apply_text,
        "employment_type": ld_fields.get("employmentType", ""),
        "date_posted": ld_fields.get("datePosted", ""),
        "valid_through": ld_fields.get("validThrough", ""),
        "salary": ld_fields.get("salary", ""),
        "gated": gated,
    }

    return {
        "title": _parse_title(soup, fallback_title),
        "company_name": ld_fields.get("company_name", ""),
        "detail": detail,
    }


_QUALIF_HEADING_RE = re.compile(r"^qualifications?\s*:?$", re.IGNORECASE)


def _extract_qualifications(description: str) -> list[str]:
    """
    Best-effort : recupere les lignes a puce qui suivent un intitule
    "Qualifications" dans la description (frequent sur les offres senjob
    syndiquees depuis des flux ONG, ex. magasinier_e_163127). Liste vide si
    aucun intitule de ce type n'est trouve - pas d'invention de donnees.
    """
    if not description:
        return []
    lines = description.split("\n")
    qualifications: list[str] = []
    in_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not in_section:
            if _QUALIF_HEADING_RE.match(_normalize_text(line)):
                in_section = True
            continue
        if not line:
            continue
        if line.startswith(("•", "-", "*")):
            qualifications.append(line.lstrip("•-* ").strip())
        elif qualifications:
            break  # ligne sans puce -> fin de la section Qualifications
    return qualifications


def build_unified_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """
    Convertit le detail riche propre a senjob.com dans la MEME forme que celle
    utilisee par les 35 sites emploi.xx (scrapers/common/parsers.py::build_job_detail)
    -> {job_url, headline, description, qualifications, criteria, skills, sections}.
    Uniformise le modele de donnees entre scrapers (une seule forme cote backend
    et frontend, cf. backend/src/models/job.model.ts) plutot que de faire
    diverger le schema par site. Les champs propres a senjob sans equivalent
    direct (reference, localisation, expiration, contact recruteur, ...) vont
    dans `criteria`, deja un champ libre cle/valeur pour les autres sites.
    """
    criteria: dict[str, str] = {}

    if detail.get("reference"):
        criteria["Référence"] = detail["reference"]

    location = detail.get("location_full") or detail.get("location_short") or ""
    if location:
        criteria["Localisation"] = location

    expire = detail.get("expire_text") or detail.get("expire_at") or ""
    if expire:
        criteria["Expiration"] = expire

    if detail.get("published_at"):
        criteria["Publié le"] = detail["published_at"]

    if detail.get("employment_type"):
        criteria["Type de contrat"] = detail["employment_type"]

    if detail.get("salary"):
        criteria["Salaire"] = detail["salary"]

    categories = detail.get("categories") or []
    if categories:
        labels = [c["label"] for c in categories if c.get("label")]
        if labels:
            criteria["Catégories"] = ", ".join(labels)

    if detail.get("apply_email"):
        criteria["Email recruteur"] = detail["apply_email"]

    if detail.get("apply_text"):
        criteria["Comment postuler"] = detail["apply_text"]

    criteria["Connexion requise"] = "Oui" if detail.get("gated") else "Non"

    return {
        "job_url": detail.get("job_url", ""),
        "headline": "",
        "description": detail.get("description", ""),
        "qualifications": _extract_qualifications(detail.get("description", "")),
        "criteria": criteria,
        "skills": [],
        "sections": [],
    }


# ---------------------------------------------------------------------------
# Navigation Selenium
# ---------------------------------------------------------------------------


def _goto_with_retry(
    sb: SB, url: str, wait_selector: str | None, retries: int = 3, delay: float = DEFAULT_DELAY
) -> bool:
    for attempt in range(retries):
        try:
            if attempt == 0:
                sb.goto(url)
            else:
                sb.refresh()
            if wait_selector:
                sb.wait_for_element(wait_selector, timeout=20)
            else:
                sb.wait_for_ready_state_complete()
            return True
        except Exception:
            print(f"    [RETRY] {attempt + 1}/{retries} - {url}")
            _maybe_solve_captcha(sb)
            sb.sleep(delay * 2)
    return False


def is_logged_in(sb: SB) -> bool:
    """
    Verifie si la session du profil persistant (my_custom_profile_senegal) est
    deja authentifiee, en regardant si la page de connexion candidat affiche
    encore un champ mot de passe (absent une fois connecte, la page redirige
    generalement vers l'espace candidat). En cas de doute (page non chargee,
    structure inattendue), on considere prudemment que non -> le login manuel
    reste propose plutot que de risquer de scraper une session expiree.
    """
    if not _goto_with_retry(sb, LOGIN_URL, None, retries=2, delay=2):
        return False
    try:
        sb.sleep(2)
        soup = BeautifulSoup(sb.get_page_source(), "html.parser")
        return soup.select_one("input[type='password']") is None
    except Exception:
        return False


def login_and_wait(sb: SB, already_on_login_page: bool = False) -> None:
    print("\n" + "=" * 60)
    print("  CONNEXION SENJOB.COM")
    print("=" * 60)
    if not already_on_login_page and not _goto_with_retry(sb, LOGIN_URL, None, retries=2, delay=2):
        print("[WARN] Impossible de charger la page de connexion - verifiez la connexion internet")
    sb.sleep(3)
    _maybe_solve_captcha(sb)
    print(f"\nUne fenetre Chrome est ouverte sur {LOGIN_URL}")
    print("Connectez-vous avec votre compte candidat senjob.com (email / mot de passe).")
    input("\nUne fois connecte, appuyez sur ENTREE ici pour lancer le scraping...\n")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _job_already_handled(
    job_id: str, job_store: JsonStore, skipped_store: JsonStore, api_client: ApiClient | None
) -> bool:
    # skipped_store d'abord : une offre sans company_id n'est jamais poussee au
    # backend (voir run_scrape), donc api_client.job_exists() y renverrait
    # toujours False et la ferait re-scraper a chaque lancement sans ce check.
    if skipped_store.has(job_id):
        return True
    if api_client is not None:
        return api_client.job_exists(job_id)
    return job_store.has(job_id)


def _print_summary(new_count: int, seen_count: int, skipped_count: int) -> None:
    print(
        f"\nTermine - {new_count} nouvelle(s) offre(s) enregistree(s), "
        f"{seen_count} deja connue(s), {skipped_count} ignoree(s) (pas d'entreprise identifiee)."
    )


def run_scrape(
    sb: SB,
    job_store: JsonStore,
    company_store: JsonStore,
    skipped_store: JsonStore,
    api_client: ApiClient | None,
    max_pages: int,
    limit: int,
    delay: float,
) -> None:
    print(f"\nNavigation vers {LISTING_URL} ...")
    if not _goto_with_retry(sb, LISTING_URL, "table#offresenjobs", delay=delay):
        print("[ERREUR] Impossible de charger la liste des offres.")
        return

    html = sb.get_page_source()
    total_pages = get_total_pages(html)
    if max_pages > 0:
        total_pages = min(total_pages, max_pages)
    print(f"Pages a parcourir : {total_pages}")

    new_count = 0
    seen_count = 0
    skipped_count = 0

    for page in range(1, total_pages + 1):
        if page > 1:
            sb.sleep(delay)
            page_url = f"{LISTING_URL}?page={page}"
            if not _goto_with_retry(sb, page_url, "table#offresenjobs", delay=delay):
                print(f"[WARN] Page listing {page} inaccessible - passage a la suivante")
                continue
            html = sb.get_page_source()

        rows = parse_listing_page(html)
        print(f"[Page {page}/{total_pages}] {len(rows)} offre(s) trouvee(s)")

        for row in rows:
            job_id = row["job_id"]

            if _job_already_handled(job_id, job_store, skipped_store, api_client):
                seen_count += 1
                continue

            if limit > 0 and new_count >= limit:
                print(f"Limite de {limit} nouvelle(s) offre(s) atteinte - arret")
                _print_summary(new_count, seen_count, skipped_count)
                return

            sb.sleep(delay)
            print(f"  -> Detail offre {job_id} : {row['title']}")

            if not _goto_with_retry(sb, row["job_url"], None, retries=3, delay=delay):
                print(f"  [WARN] Detail offre {job_id} introuvable apres 3 tentatives - ignoree")
                continue

            detail_html = sb.get_page_source()
            parsed = parse_job_detail(detail_html, row["job_url"], fallback_title=row["title"])

            parsed["detail"]["location_short"] = row["location_short"]
            parsed["detail"]["published_at"] = row["published_at"]
            if row["expire_at"]:
                parsed["detail"]["expire_at"] = row["expire_at"]

            company_name = parsed["company_name"]
            logo_url = parsed["detail"]["logo_url"]
            company_id = _derive_company_id(logo_url, company_name)

            if not company_id:
                # Pas d'entreprise identifiable -> ne va jamais dans jobs_scrappe
                # (ces offres serviront a une campagne d'emailing par entreprise,
                # une offre sans entreprise n'y a pas sa place). Marquee dans un
                # cache local separe pour ne pas la re-scraper a chaque lancement.
                skipped_store.set(
                    job_id,
                    {"job_id": job_id, "title": row["title"], "job_url": row["job_url"]},
                )
                print(f"  [SKIP] Offre {job_id} ignoree - aucune entreprise identifiable")
                skipped_count += 1
                continue

            if not company_store.has(company_id):
                company_payload = build_company_payload(
                    company_id=company_id,
                    name=company_name,
                    logo_url=logo_url,
                    location_full=parsed["detail"]["location_full"] or row["location_short"],
                    email=parsed["detail"]["apply_email"],
                )
                company_store.set(company_id, company_payload)
                if api_client is not None:
                    api_client.upsert_company(company_payload)
                print(f"     (nouvelle entreprise : {company_name or company_id})")

            gated = parsed["detail"]["gated"]

            payload = {
                "job_id": job_id,
                "title": parsed["title"] or row["title"],
                "job_url": row["job_url"],
                "company_name": company_name,
                "company_url": "",
                "company_id": company_id,
                "detail": build_unified_detail(parsed["detail"]),
            }

            job_store.set(job_id, payload)
            if api_client is not None:
                api_client.upsert_job(payload)
                api_client.upsert_job_publication(job_id, row["published_at"] or None)

            if gated:
                print(f"  [INFO] Offre {job_id} verrouillee (connexion/abonnement requis) - champs partiels enregistres")

            new_count += 1

    _print_summary(new_count, seen_count, skipped_count)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape les offres d'emploi de senjob.com")
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Nombre max de nouvelles offres a detailler (0 = illimite)",
    )
    parser.add_argument(
        "--max-pages", type=int, default=0, metavar="N",
        help="Nombre max de pages de listing a parcourir (0 = toutes)",
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY, metavar="SECONDES",
        help=f"Delai entre requetes (defaut {DEFAULT_DELAY}s)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Mode sans fenetre (suppose le profil deja connecte, saute l'etape de login)",
    )
    args = parser.parse_args()

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _reset_profile_exit_state(PROFILE_DIR)
    job_store = JsonStore(JOBS_FILE)
    company_store = JsonStore(COMPANIES_FILE)
    skipped_store = JsonStore(SKIPPED_FILE)
    api_client = ApiClient(BACKEND_URL) if BACKEND_URL else None

    if api_client is None:
        print("[INFO] SCRAPER_API_URL non defini - cache JSON local uniquement (pas de push backend)")

    with SB(
        uc=True,
        locale="fr",
        user_data_dir=str(PROFILE_DIR),
        disable_js=False,
        headless=args.headless,
    ) as sb:
        sb.activate_cdp_mode()

        if args.headless:
            if not is_logged_in(sb):
                print(
                    "[WARN] Session non authentifiee sur ce profil - les offres necessitant "
                    "une connexion resteront partielles (gated). Relancez sans --headless pour vous connecter."
                )
        elif is_logged_in(sb):
            print("[INFO] Session deja active sur ce profil - pas besoin de se reconnecter.")
        else:
            login_and_wait(sb, already_on_login_page=True)

        run_scrape(
            sb,
            job_store,
            company_store,
            skipped_store,
            api_client,
            max_pages=args.max_pages,
            limit=args.limit,
            delay=args.delay,
        )

    print(f"\nCache local : {JOBS_FILE} ({job_store.count()} offre(s) au total)")
    print(f"Cache local : {COMPANIES_FILE} ({company_store.count()} entreprise(s) au total)")
    print(f"Cache local : {SKIPPED_FILE} ({skipped_store.count()} offre(s) ignoree(s) au total)")


if __name__ == "__main__":
    main()

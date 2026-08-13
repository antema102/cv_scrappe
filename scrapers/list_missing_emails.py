"""
list_missing_emails.py
=======================
Liste les entreprises qui n'ont pas encore d'adresse email connue, mais dont
le site web est exploitable (renseigné, non vide, pas "non disponible"/"n/a"...).

Ne fait aucun scraping : interroge uniquement le backend (filtre ?scrape=1,
déjà utilisé en interne par email_extractor.py / emails_website.py) et
exporte le résultat en JSON (et en CSV en option) dans downloaded_files/.

Utilisation :
    python -m scrapers.list_missing_emails                        # toutes les entreprises
    python -m scrapers.list_missing_emails --country senegal      # un pays
    python -m scrapers.list_missing_emails --limit 50             # 50 premières
    python -m scrapers.list_missing_emails --csv                  # export CSV en plus du JSON
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from scrapers.email_extractor import FETCH_PAGE_SIZE, fetch_companies_from_backend

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "downloaded_files"
JSON_FILE = OUTPUT_DIR / "companies_missing_email.json"
CSV_FILE = OUTPUT_DIR / "companies_missing_email.csv"

CSV_FIELDS: tuple[str, ...] = (
    "company_id", "name", "country", "city", "sector", "website",
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def write_json(companies: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(companies: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for company in companies:
            writer.writerow(company)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Liste les entreprises sans email connu mais avec un site web "
            "exploitable (renseigné, non vide, pas 'non disponible')"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python -m scrapers.list_missing_emails
  python -m scrapers.list_missing_emails --country senegal
  python -m scrapers.list_missing_emails --limit 50 --csv
        """,
    )
    parser.add_argument(
        "--country",
        metavar="NOM",
        help="Filtrer par nom de pays tel que stocké en base (ex: 'Algérie', 'Sénégal') — tous si omis",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Limiter le nombre d'entreprises récupérées (0 = toutes)",
    )
    parser.add_argument(
        "--output",
        metavar="FICHIER",
        default=str(JSON_FILE),
        help=f"Fichier JSON de sortie (défaut : {JSON_FILE})",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help=f"Exporte aussi un CSV (défaut : {CSV_FILE})",
    )
    args = parser.parse_args()

    companies = fetch_companies_from_backend(
        country=args.country,
        limit=args.limit,
        page_size=FETCH_PAGE_SIZE,
    )

    json_path = Path(args.output)
    write_json(companies, json_path)
    log.info("JSON écrit : %s (%d entreprise(s))", json_path, len(companies))

    if args.csv:
        write_csv(companies, CSV_FILE)
        log.info("CSV écrit : %s (%d entreprise(s))", CSV_FILE, len(companies))

    if not companies:
        log.info("Aucune entreprise sans email trouvée (avec site exploitable).")
        return

    per_country: dict[str, int] = {}
    for company in companies:
        country = str(company.get("country") or "?")
        per_country[country] = per_country.get(country, 0) + 1

    log.info("Répartition par pays :")
    for country, count in sorted(per_country.items(), key=lambda item: -item[1]):
        log.info("  %-25s %d", country, count)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import os

from scrapers import BaseJobScraper, COUNTRIES, download_country_cvs

API_URL: str | None = os.getenv("SCRAPER_API_URL", "http://localhost:3000")


def run_country(code: str) -> None:
    config = COUNTRIES[code]
    print(f"\n{'=' * 55}")
    print(f"  Pays : {code}  ({config.base_url})")
    print(f"{'=' * 55}")

    scraper = BaseJobScraper(config, api_url=API_URL)
    result = scraper.scrape_all()

    print(f"  Offres traitées      : {len(result['jobs'])}")
    print(f"  Nouvelles entreprises: {len(result['new_companies'])}")
    print(f"  Cache entreprises    : {scraper.company_store.count()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper emplois africains")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--list",
        action="store_true",
        help="Afficher tous les codes pays disponibles",
    )
    group.add_argument(
        "--download-cvs",
        action="store_true",
        help="Télécharger les CV PDF du pays indiqué par --country",
    )
    parser.add_argument(
        "--country",
        choices=list(COUNTRIES.keys()),
        metavar="CODE",
        help=f"Pays cible. Choix : {', '.join(COUNTRIES)}",
    )
    parser.add_argument(
        "--cv-cookie",
        default="",
        help="Cookie HTTP de session pour accéder aux CV privés",
    )
    args = parser.parse_args()

    if args.list:
        for code, cfg in COUNTRIES.items():
            print(f"  {code:<15} {cfg.base_url}")
        return

    if args.download_cvs:
        targets = [args.country] if args.country else list(COUNTRIES.keys())

        for code in targets:
            print(f"\n=== Téléchargement CV : {code} ===")
            download_country_cvs(
                COUNTRIES[code],
                cookie_header=args.cv_cookie.strip() or None,
            )
        return

    targets = [args.country] if args.country else list(COUNTRIES.keys())

    for code in targets:
        run_country(code)


if __name__ == "__main__":
    main()
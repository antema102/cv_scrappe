import os

from emploi_ma_scraper import EmploiMaScraper

# URL du backend Node.js — laisser vide pour désactiver l'envoi API
API_URL: str | None = os.getenv("SCRAPER_API_URL", "http://localhost:3000")


def main() -> None:
    scraper = EmploiMaScraper(api_url=API_URL)
    result = scraper.scrape_all()

    print(f"Offres traitées     : {len(result['jobs'])}")
    print(f"Entreprises nouvelles: {len(result['new_companies'])}")
    print(f"Cache entreprises   : {scraper.company_store.count()}")


if __name__ == "__main__":
    main()
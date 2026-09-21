"""
facebook_script/run_all_countries.py
=====================================
Lance `pages.py` successivement pour tous les pays de `countries.py` (COUNTRIES).

Séquentiel et obligatoire : toutes les pages/groupes, quel que soit le pays,
partagent le même profil Chrome persistant (`my_custom_profile_facebook`) —
deux `--country` en parallèle feraient s'affronter deux Chrome sur le même
profil (conflit de verrou du profil). Le verrou `.run_<pays>.lock` de pages.py
protège seulement contre deux lancements du MÊME pays, pas contre ça.

Usage :
    python facebook_script/run_all_countries.py                        # tous les pays, pipeline par défaut (pas de push)
    python facebook_script/run_all_countries.py --push                 # + envoi au backend pour chaque pays
    python facebook_script/run_all_countries.py --skip-scrape --reanalyze --push
    python facebook_script/run_all_countries.py --country maroc --country cameroun --push  # sous-ensemble

Tout argument autre que --country est transmis tel quel à pages.py, pour CHAQUE
pays (ex. --push, --skip-scrape, --max-age-days 30). --country est consommé
ici pour choisir la liste des pays à traiter (répétable ; par défaut : tous).
Un pays en échec (code de sortie non nul) n'empêche pas les suivants ; résumé
affiché à la fin.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from countries import COUNTRIES

SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> tuple[list[str], list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--country", action="append", default=[], choices=sorted(COUNTRIES), metavar="PAYS")
    parser.add_argument("-h", "--help", action="store_true")
    args, extra = parser.parse_known_args()
    if args.help:
        print(__doc__)
        print(f"Pays disponibles : {', '.join(sorted(COUNTRIES))}")
        sys.exit(0)
    return (args.country or sorted(COUNTRIES)), extra


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with suppress(Exception):
            stream.reconfigure(errors="replace")
    countries, extra = parse_args()
    failures: list[str] = []
    for index, code in enumerate(countries, 1):
        print(f"\n{'=' * 70}\n[{index}/{len(countries)}] {COUNTRIES[code].name} (--country {code})\n{'=' * 70}")
        result = subprocess.call([sys.executable, str(SCRIPT_DIR / "pages.py"), "--country", code, *extra])
        if result != 0:
            failures.append(code)
            print(f"[ERREUR] {code} a échoué (code {result}) — passage au pays suivant")
    print(f"\n{'=' * 70}")
    if failures:
        print(f"{len(failures)}/{len(countries)} pays en échec : {', '.join(failures)}")
    else:
        print(f"{len(countries)} pays traités avec succès : {', '.join(countries)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

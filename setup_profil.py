"""
setup_profil.py
---------------
Script utilitaire pour configurer un profil Chrome avec SeleniumBase.

Usage:
  python setup_profil.py            -> configure le Profil 1 (defaut)
  python setup_profil.py --profil 2 -> configure le Profil 2
  python setup_profil.py --profil 3 -> configure le Profil 3

Etapes:
  1. Ouvre Chrome avec le profil selectionne
  2. Navigue vers l'URL du profil
  3. Vous laisse vous connecter manuellement
  4. Appuyez sur ENTREE pour fermer et sauvegarder

A executer une seule fois par profil.
"""

import argparse
from pathlib import Path

from seleniumbase import SB


_PROJECT_ROOT = Path(__file__).resolve().parent

PROFILS = {
    1: {
        "dossier": str(_PROJECT_ROOT / "my_custom_profile"),
        "url": "https://www.emploi.ma",
        "port": 9222,
    },
    2: {
        "dossier": str(_PROJECT_ROOT / "my_custom_profile_2"),
        "url": "https://gemini.google.com/app/1b19647f725d9f8a",
        "port": 9223,
    },
    3: {
        "dossier": str(_PROJECT_ROOT / "my_custom_profile_3"),
        "url": "https://gemini.google.com/u/3/app/c4d641f4ba2e879f",
        "port": 9224,
    },
    4: {
        "dossier": str(_PROJECT_ROOT / "my_custom_profile_4"),
        "url": "https://gemini.google.com/u/5/app/d4a39a45c6b81b27",
        "port": 9225,
    },
    5: {
        "dossier": str(_PROJECT_ROOT / "my_custom_profile_5"),
        "url": "https://gemini.google.com/",
        "port": 9226,
    },
    6: {
        "dossier": str(_PROJECT_ROOT / "my_custom_profile_6"),
        "url": "https://gemini.google.com/",
        "port": 9227,
    },
}


def parse_args() -> int:
    parser = argparse.ArgumentParser(
        description="Configure un profil Chrome pour une utilisation future."
    )
    parser.add_argument(
        "--profil",
        "-p",
        type=int,
        default=1,
        choices=list(PROFILS.keys()),
        help=(
            f"Numero du profil a configurer (choix: {list(PROFILS.keys())}, "
            "defaut: 1)"
        ),
    )
    return parser.parse_args().profil


def main() -> None:
    numero = parse_args()
    config = PROFILS[numero]

    dossier = Path(config["dossier"])
    dossier.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"  CONFIGURATION DU PROFIL {numero}")
    print("=" * 60)
    print(f"\nProfil  : {dossier}")
    print(f"URL     : {config['url']}")
    print(f"Port    : {config['port']}")
    print("\nOuverture de Chrome avec SeleniumBase...")

    with SB(
        uc=True,
        headless=False,
        user_data_dir=str(dossier),
        chromium_arg=f"--remote-debugging-port={config['port']}"
    ) as sb:
        try:
            print("\nNavigation vers l'URL du profil...")
            sb.activate_cdp_mode()
            sb.goto(config["url"])
            sb.sleep(10)
            sb.solve_captcha()


            print("\n" + "=" * 60)
            print("  ACTION REQUISE DANS LE NAVIGATEUR:")
            print(f"  1. Connectez-vous avec le Profil {numero}")
            print("  2. Acceptez les conditions si demande")
            print("  3. Verifiez que la page cible s'affiche correctement")
            print("=" * 60)
            input(
                "\nQuand vous etes connecte et que tout fonctionne,\n"
                "appuyez sur ENTREE pour fermer et sauvegarder le profil...\n"
            )
        except Exception as e:
            print(f"Erreur lors de la navigation vers l'URL: {e}")
        print("Fermeture du navigateur - profil sauvegarde.")

    print(f"\nProfil {numero} configure avec succes !")
    print(f"Dossier: {dossier}")


if __name__ == "__main__":
    main()

"""
facebook_script/countries.py
============================
Les 54 pays d'Afrique pour les scripts Facebook (pages.py, post_groups.py) : nom et code ISO
écrits dans les JSON, normalisation des téléphones (clé de dédoublonnage en E.164, format local
dans les documents backend comme le reste de la base : "0341234567"), langue et nom du pays dans
les publications, fichiers de liens propres à chaque pays.

Un dossier par pays, facebook_script/pays/<code>/ : le pays d'une page ou d'un groupe est
celui du dossier où son lien est écrit. Un pays est actif dès qu'un de ses fichiers contient
au moins un lien :
    pays/<code>/pages.txt          pages scrapées (pages.py)
    pays/<code>/groups.txt         groupes scrapés (pages.py, scraper.py pour Madagascar)
    pays/<code>/share_groups.txt   groupes où post_groups.py publie les offres WipWork
Dossiers et fichiers modèles manquants : python facebook_script/countries.py --create
(ne touche jamais un fichier existant). Repli pour Madagascar : anciens fichiers
facebook_script/pages.txt / groups.txt / share_groups.txt s'ils existent encore et que le
fichier du dossier n'existe pas.

Codes : ceux de scrapers/common/country_iso.py quand le pays y est (cameroun, cote_ivoire,
south_africa…), sinon un code du même style. find_country() accepte aussi le nom, l'ISO3 et des
variantes (« Cameroun », « cameroon », « CMR », « Côte d'Ivoire »).

Téléphones : longueurs du numéro national (sans le préfixe national) d'après les plans de
numérotation publiés ; à vérifier sur de vraies données avant de s'y fier pour un nouveau pays.
`trunk_prefix=""` : pas de « 0 » national (Cameroun, Sénégal…) ; au Bénin, en Côte d'Ivoire et au
Congo le 0 initial fait partie du numéro (gardé en international).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COUNTRY_DIR = SCRIPT_DIR / "pays"  # un dossier par pays : pays/<code>/pages.txt, groups.txt, share_groups.txt
LEGACY_COUNTRY = "madagascar"  # seul pays qui existait avant le multi-pays : anciens fichiers à la racine
LIST_KINDS = ("pages", "groups", "share_groups")
# En-tête des fichiers modèles créés par create_country_folders ({name} = nom du pays)
LIST_HEADERS = {
    "pages": (
        "# {name} — pages Facebook à scraper (pages.py) : un lien par ligne.",
        "# Les lignes vides et celles qui commencent par # sont ignorées.",
        "# Le pays de ces pages est celui de ce dossier : une page ne doit être listée que pour un seul pays.",
    ),
    "groups": (
        "# {name} — groupes Facebook à SCRAPER (pages.py) : un lien par ligne.",
        "# Les lignes vides et celles qui commencent par # sont ignorées.",
        "# Distinct de share_groups.txt (groupes où l'on PUBLIE). Un groupe ne doit être listé que pour un seul pays.",
    ),
    "share_groups": (
        "# {name} — groupes Facebook où post_groups.py PUBLIE les offres WipWork : un lien par ligne.",
        "# Les lignes vides et celles qui commencent par # sont ignorées.",
        "# Le compte du profil de ce pays (facebook_script/share_countries.json) doit être membre de chaque groupe.",
        "# Distinct de groups.txt (groupes SCRAPÉS). Un groupe ne doit être listé que pour un seul pays.",
    ),
}


@dataclass(frozen=True, slots=True)
class CountryProfile:
    code: str  # suffixe des fichiers de sortie (companies_<code>.json)
    name: str  # valeur du champ "country" des documents backend (nom français)
    iso3: str  # ISO 3166-1 alpha-3 (même référentiel que scrapers/common/country_iso.py)
    phone_country_code: str  # indicatif sans "+"
    national_number_length: int  # chiffres du numéro national sans le préfixe national
    trunk_prefix: str = "0"
    other_lengths: tuple[int, ...] = ()  # autres longueurs valides (fixes, anciens numéros)
    lang: str = "fr"  # langue des publications : fr, en, pt
    display_name: str = ""  # nom du pays dans les publications (dans la langue lang) ; défaut : name
    in_phrase: str = ""  # « à Madagascar », « au Cameroun », « in Nigeria »
    api_name: str = ""  # paramètre pays de l'API WipWork ; défaut : name
    aliases: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def offers_api_name(self) -> str:
        return self.api_name or self.name

    @property
    def lengths(self) -> tuple[int, ...]:
        return (self.national_number_length, *self.other_lengths)

    def to_e164(self, raw: str) -> str:
        """'034 12 345 67' / '+261 34 12 345 67' / '0026134…' -> '+261341234567' ; '' si invalide."""
        raw = raw.strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return ""
        international = raw.startswith("+") or digits.startswith("00")
        if digits.startswith("00"):
            digits = digits[2:]
        cc, lengths = self.phone_country_code, self.lengths
        if digits.startswith(cc) and len(digits) - len(cc) in lengths:
            return f"+{digits}"
        if not international:
            trunk = self.trunk_prefix
            if trunk and digits.startswith(trunk) and len(digits) - len(trunk) in lengths:
                return f"+{cc}{digits[len(trunk):]}"
            if len(digits) in lengths:
                return f"+{cc}{digits}"
            return ""
        return f"+{digits}" if 8 <= len(digits) <= 15 else ""  # numéro étranger gardé tel quel

    def to_local(self, e164: str) -> str:
        prefix = f"+{self.phone_country_code}"
        if e164.startswith(prefix):
            return self.trunk_prefix + e164[len(prefix):]
        return e164


def _country(code: str, name: str, iso3: str, cc: str, length: int, trunk: str, in_phrase: str, **extra) -> CountryProfile:
    return CountryProfile(code=code, name=name, iso3=iso3, phone_country_code=cc, national_number_length=length,
                          trunk_prefix=trunk, in_phrase=in_phrase, **extra)


_EN = {"lang": "en"}
_PT = {"lang": "pt"}

_ALL: tuple[CountryProfile, ...] = (
    # Afrique du Nord
    _country("algerie", "Algérie", "DZA", "213", 9, "0", "en Algérie", other_lengths=(8,), aliases=("algeria",)),
    _country("egypt", "Égypte", "EGY", "20", 10, "0", "in Egypt", other_lengths=(9, 8), display_name="Egypt",
             aliases=("egypte",), **_EN),
    _country("libye", "Libye", "LBY", "218", 9, "0", "in Libya", other_lengths=(8,), display_name="Libya",
             aliases=("libya",), **_EN),
    _country("maroc", "Maroc", "MAR", "212", 9, "0", "au Maroc", aliases=("morocco",)),
    _country("tunisie", "Tunisie", "TUN", "216", 8, "", "en Tunisie", aliases=("tunisia",)),
    _country("mauritanie", "Mauritanie", "MRT", "222", 8, "", "en Mauritanie", aliases=("mauritania",)),
    _country("sudan", "Soudan", "SDN", "249", 9, "0", "in Sudan", display_name="Sudan", aliases=("soudan",), **_EN),
    _country("south_sudan", "Soudan du Sud", "SSD", "211", 9, "0", "in South Sudan", display_name="South Sudan",
             aliases=("soudan_du_sud",), **_EN),
    # Afrique de l'Ouest
    _country("benin", "Bénin", "BEN", "229", 10, "", "au Bénin", other_lengths=(8,)),
    _country("burkina", "Burkina Faso", "BFA", "226", 8, "", "au Burkina Faso", aliases=("burkina_faso",)),
    _country("cap_vert", "Cap-Vert", "CPV", "238", 7, "", "em Cabo Verde", display_name="Cabo Verde",
             aliases=("cape_verde", "cabo_verde"), **_PT),
    _country("cote_ivoire", "Côte d'Ivoire", "CIV", "225", 10, "", "en Côte d'Ivoire",
             aliases=("ivory_coast", "cote_divoire")),
    _country("gambia", "Gambie", "GMB", "220", 7, "", "in The Gambia", display_name="The Gambia",
             aliases=("gambie", "the_gambia"), **_EN),
    _country("ghana", "Ghana", "GHA", "233", 9, "0", "in Ghana", **_EN),
    _country("guinee", "Guinée", "GIN", "224", 9, "", "en Guinée", aliases=("guinea", "guinee_conakry")),
    _country("guinee_bissau", "Guinée-Bissau", "GNB", "245", 9, "", "na Guiné-Bissau", display_name="Guiné-Bissau",
             aliases=("guinea_bissau",), **_PT),
    _country("liberia", "Liberia", "LBR", "231", 9, "0", "in Liberia", other_lengths=(8, 7), **_EN),
    _country("mali", "Mali", "MLI", "223", 8, "", "au Mali"),
    _country("niger", "Niger", "NER", "227", 8, "", "au Niger"),
    _country("nigeria", "Nigeria", "NGA", "234", 10, "0", "in Nigeria", other_lengths=(8,), **_EN),
    _country("senegal", "Sénégal", "SEN", "221", 9, "", "au Sénégal"),
    _country("sierra_leone", "Sierra Leone", "SLE", "232", 8, "0", "in Sierra Leone", **_EN),
    _country("togo", "Togo", "TGO", "228", 8, "", "au Togo"),
    # Afrique centrale
    _country("cameroun", "Cameroun", "CMR", "237", 9, "", "au Cameroun", aliases=("cameroon",)),
    _country("centrafrique", "Centrafrique", "CAF", "236", 8, "", "en Centrafrique",
             aliases=("republique_centrafricaine", "central_african_republic", "rca")),
    _country("congo", "Congo", "COG", "242", 9, "", "au Congo",
             aliases=("congo_brazzaville", "republique_du_congo")),
    _country("congo_rdc", "RD Congo", "COD", "243", 9, "0", "en RD Congo",
             aliases=("rdc", "drc", "congo_kinshasa", "republique_democratique_du_congo")),
    _country("gabon", "Gabon", "GAB", "241", 8, "", "au Gabon", other_lengths=(7,)),
    _country("guinee_equatoriale", "Guinée équatoriale", "GNQ", "240", 9, "", "en Guinée équatoriale",
             aliases=("equatorial_guinea",)),
    _country("tchad", "Tchad", "TCD", "235", 8, "", "au Tchad", aliases=("chad",)),
    _country("sao_tome", "Sao Tomé-et-Principe", "STP", "239", 7, "", "em São Tomé e Príncipe",
             display_name="São Tomé e Príncipe", aliases=("sao_tome_et_principe", "sao_tome_and_principe"), **_PT),
    _country("burundi", "Burundi", "BDI", "257", 8, "", "au Burundi"),
    _country("rwanda", "Rwanda", "RWA", "250", 9, "0", "in Rwanda", **_EN),
    # Afrique de l'Est et océan Indien
    _country("comores", "Comores", "COM", "269", 7, "", "aux Comores", aliases=("comoros",)),
    _country("djibouti", "Djibouti", "DJI", "253", 8, "", "à Djibouti"),
    _country("eritrea", "Érythrée", "ERI", "291", 7, "0", "in Eritrea", display_name="Eritrea",
             aliases=("erythree",), **_EN),
    _country("ethiopia", "Éthiopie", "ETH", "251", 9, "0", "in Ethiopia", display_name="Ethiopia",
             aliases=("ethiopie",), **_EN),
    _country("kenya", "Kenya", "KEN", "254", 9, "0", "in Kenya", **_EN),
    _country("madagascar", "Madagascar", "MDG", "261", 9, "0", "à Madagascar"),  # 34 12 345 67
    _country("malawi", "Malawi", "MWI", "265", 9, "0", "in Malawi", other_lengths=(7,), **_EN),
    _country("maurice", "Maurice", "MUS", "230", 8, "", "à Maurice", other_lengths=(7,),
             aliases=("mauritius", "ile_maurice")),
    _country("mozambique", "Mozambique", "MOZ", "258", 9, "", "em Moçambique", other_lengths=(8,),
             display_name="Moçambique", aliases=("mocambique",), **_PT),
    _country("uganda", "Ouganda", "UGA", "256", 9, "0", "in Uganda", display_name="Uganda", aliases=("ouganda",), **_EN),
    _country("seychelles", "Seychelles", "SYC", "248", 7, "", "in Seychelles", **_EN),
    _country("somalia", "Somalie", "SOM", "252", 9, "0", "in Somalia", other_lengths=(8, 7), display_name="Somalia",
             aliases=("somalie",), **_EN),
    _country("tanzania", "Tanzanie", "TZA", "255", 9, "0", "in Tanzania", display_name="Tanzania",
             aliases=("tanzanie",), **_EN),
    _country("zambia", "Zambie", "ZMB", "260", 9, "0", "in Zambia", display_name="Zambia", aliases=("zambie",), **_EN),
    _country("zimbabwe", "Zimbabwe", "ZWE", "263", 9, "0", "in Zimbabwe", **_EN),
    # Afrique australe
    _country("south_africa", "Afrique du Sud", "ZAF", "27", 9, "0", "in South Africa", display_name="South Africa",
             aliases=("afrique_du_sud",), **_EN),
    _country("angola", "Angola", "AGO", "244", 9, "", "em Angola", **_PT),
    _country("botswana", "Botswana", "BWA", "267", 8, "", "in Botswana", other_lengths=(7,), **_EN),
    _country("eswatini", "Eswatini", "SWZ", "268", 8, "", "in Eswatini", aliases=("swaziland",), **_EN),
    _country("lesotho", "Lesotho", "LSO", "266", 8, "", "in Lesotho", **_EN),
    _country("namibia", "Namibie", "NAM", "264", 9, "0", "in Namibia", other_lengths=(8,), display_name="Namibia",
             aliases=("namibie",), **_EN),
)

COUNTRIES: dict[str, CountryProfile] = {country.code: country for country in _ALL}


def _key(value: str) -> str:
    """« Côte d'Ivoire » -> « cote_d_ivoire » : comparaison sans accents, casse ni ponctuation."""
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char)).lower()
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def _lookup() -> dict[str, str]:
    table: dict[str, str] = {}
    for country in _ALL:
        for value in (country.code, country.name, country.label, country.iso3, *country.aliases):
            key = _key(value)
            if table.setdefault(key, country.code) != country.code:
                raise ValueError(f"variante de pays ambiguë : {value!r}")
        table.setdefault(_key(country.name).replace("_", ""), country.code)  # « cotedivoire »
    return table


_LOOKUP = _lookup()


def find_country(value: str) -> CountryProfile:
    """Code, nom (français ou dans la langue des publications), ISO3 ou variante ; ValueError si inconnu."""
    key = _key(value)
    code = _LOOKUP.get(key) or _LOOKUP.get(key.replace("_", ""))
    if code is None:
        raise ValueError(f"pays inconnu : {value!r} (codes : {', '.join(sorted(COUNTRIES))})")
    return COUNTRIES[code]


def parse_countries(value: str) -> list[CountryProfile]:
    """« cameroun,senegal » -> profils, dans l'ordre, sans doublon ; ValueError si un pays est inconnu."""
    found: dict[str, CountryProfile] = {}
    for part in value.split(","):
        if part.strip():
            country = find_country(part.strip())
            found.setdefault(country.code, country)
    if not found:
        raise ValueError("aucun pays")
    return list(found.values())


def country_dir(country: CountryProfile) -> Path:
    return COUNTRY_DIR / country.code


def list_file(country: CountryProfile, kind: str) -> Path:
    """pays/<code>/<kind>.txt (kind : pages, groups, share_groups) ; Madagascar : ancien fichier s'il reste seul."""
    if kind not in LIST_KINDS:
        raise ValueError(kind)
    path = country_dir(country) / f"{kind}.txt"
    legacy = SCRIPT_DIR / f"{kind}.txt"
    if country.code == LEGACY_COUNTRY and not path.exists() and legacy.exists():
        return legacy
    return path


def create_country_folders() -> list[Path]:
    """Dossier et fichiers modèles (en-tête seul, aucun lien) de chaque pays ; jamais un fichier existant écrasé."""
    created = []
    for country in _ALL:
        for kind in LIST_KINDS:
            path = country_dir(country) / f"{kind}.txt"
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            header = "\n".join(line.format(name=country.name) for line in LIST_HEADERS[kind])
            path.write_text(header + "\n\n", encoding="utf-8")
            created.append(path)
    return created


def list_lines(path: Path) -> list[str]:
    """Liens d'un fichier (lignes vides et commentaires # ignorés) ; [] si le fichier n'existe pas."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()  # -sig : BOM du Bloc-notes Windows
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def active_countries(*kinds: str) -> list[CountryProfile]:
    """Pays dont au moins un des fichiers de liens donnés contient un lien, Madagascar en premier."""
    found = [country for country in _ALL if any(list_lines(list_file(country, kind)) for kind in kinds)]
    return sorted(found, key=lambda country: (country.code != LEGACY_COUNTRY, country.code))


if __name__ == "__main__":
    import sys

    if "--create" in sys.argv[1:]:
        new_files = create_country_folders()
        print(f"{len(new_files)} fichier(s) modèle(s) créé(s) dans {COUNTRY_DIR}")
    for country in sorted(_ALL, key=lambda c: c.code):
        links = {kind: len(list_lines(list_file(country, kind))) for kind in LIST_KINDS}
        summary = ", ".join(f"{kind} {count}" for kind, count in links.items() if count) or "-"
        print(f"{country.code:<20} {country.name:<22} {country.iso3} +{country.phone_country_code:<4} "
              f"{country.lang}  {country.in_phrase:<26} {summary}")
    print(f"\n{len(_ALL)} pays (liens par fichier de pays/<code>/ ; --create : dossiers et fichiers modèles manquants)")

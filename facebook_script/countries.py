"""
facebook_script/countries.py
============================
Profils pays du scraping de pages Facebook : nom et code ISO écrits dans les
JSON, normalisation des téléphones (clé de dédoublonnage en E.164, format local
dans les documents backend comme le reste de la base : "0341234567").

Ajouter un pays = ajouter une entrée dans COUNTRIES.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CountryProfile:
    code: str  # suffixe des fichiers de sortie (companies_<code>.json)
    name: str  # valeur du champ "country" des documents backend
    iso3: str  # ISO 3166-1 alpha-3 (même référentiel que scrapers/common/country_iso.py)
    phone_country_code: str  # indicatif sans "+"
    national_number_length: int  # chiffres du numéro national sans le préfixe "0"
    trunk_prefix: str = "0"

    def to_e164(self, raw: str) -> str:
        """'034 12 345 67' / '+261 34 12 345 67' / '0026134…' -> '+261341234567' ; '' si invalide."""
        raw = raw.strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return ""
        international = raw.startswith("+") or digits.startswith("00")
        if digits.startswith("00"):
            digits = digits[2:]
        cc, length = self.phone_country_code, self.national_number_length
        if digits.startswith(cc) and len(digits) == len(cc) + length:
            return f"+{digits}"
        if not international:
            if digits.startswith(self.trunk_prefix) and len(digits) == length + len(self.trunk_prefix):
                return f"+{cc}{digits[len(self.trunk_prefix):]}"
            if len(digits) == length:
                return f"+{cc}{digits}"
            return ""
        return f"+{digits}" if 8 <= len(digits) <= 15 else ""  # numéro étranger gardé tel quel

    def to_local(self, e164: str) -> str:
        prefix = f"+{self.phone_country_code}"
        if e164.startswith(prefix):
            return self.trunk_prefix + e164[len(prefix):]
        return e164


COUNTRIES: dict[str, CountryProfile] = {
    "madagascar": CountryProfile(
        code="madagascar",
        name="Madagascar",
        iso3="MDG",
        phone_country_code="261",
        national_number_length=9,  # 34 12 345 67
    ),
    "maroc": CountryProfile(
        code="maroc",
        name="Maroc",
        iso3="MAR",
        phone_country_code="212",
        national_number_length=9,  # 06/07 XX XX XX XX (mobile), 05 XX XX XX XX (fixe)
    ),
    "cameroun": CountryProfile(
        code="cameroun",
        name="Cameroun",
        iso3="CMR",
        phone_country_code="237",
        national_number_length=9,  # 6XX XXX XXX (mobile), 2XX XXX XXX (fixe) — pas de préfixe "0"
        trunk_prefix="",
    ),
    # ---------------------------------------------------------------------------
    # Mêmes 35 pays que scrapers/countries.py (codes identiques, alpha-3 repris de
    # scrapers/common/country_iso.py) — indicatif et longueur du numéro vérifiés
    # par pays (ITU/Wikipédia), mobile en priorité quand fixe et mobile diffèrent.
    # ---------------------------------------------------------------------------
    "cote_ivoire": CountryProfile(
        code="cote_ivoire",
        name="Côte d'Ivoire",
        iso3="CIV",
        phone_country_code="225",
        national_number_length=10,  # réforme 2021 : préfixe 01/05/07 + 8 chiffres, fait partie du numéro
        trunk_prefix="",
    ),
    "congo": CountryProfile(
        code="congo",
        name="Congo-Brazzaville",
        iso3="COG",
        phone_country_code="242",
        national_number_length=9,  # le "0" initial (04/05/06 mobile, 22 fixe) fait partie des 9 chiffres
        trunk_prefix="",
    ),
    "senegal": CountryProfile(
        code="senegal",
        name="Sénégal",
        iso3="SEN",
        phone_country_code="221",
        national_number_length=9,  # 7X XXX XX XX — pas de préfixe "0" à l'international
        trunk_prefix="",
    ),
    "burkina": CountryProfile(
        code="burkina",
        name="Burkina Faso",
        iso3="BFA",
        phone_country_code="226",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "guinee": CountryProfile(
        code="guinee",
        name="Guinée",
        iso3="GIN",
        phone_country_code="224",
        national_number_length=9,  # 6XX XXX XXX — même forme en local et à l'international, pas de "0"
        trunk_prefix="",
    ),
    "togo": CountryProfile(
        code="togo",
        name="Togo",
        iso3="TGO",
        phone_country_code="228",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "gabon": CountryProfile(
        code="gabon",
        name="Gabon",
        iso3="GAB",
        phone_country_code="241",
        national_number_length=8,  # 9 chiffres en local (0 + 8) depuis la migration terminée en avril 2024
    ),
    "mauritanie": CountryProfile(
        code="mauritanie",
        name="Mauritanie",
        iso3="MRT",
        phone_country_code="222",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "benin": CountryProfile(
        code="benin",
        name="Bénin",
        iso3="BEN",
        phone_country_code="229",
        # Réforme du 30/11/2024 : préfixe "01" désormais obligatoire et permanent (0ZXXXXXXXX,
        # 10 chiffres), gardé tel quel à l'international — ce n'est plus un préfixe de tronc.
        national_number_length=10,
        trunk_prefix="",
    ),
    "mali": CountryProfile(
        code="mali",
        name="Mali",
        iso3="MLI",
        phone_country_code="223",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "congo_rdc": CountryProfile(
        code="congo_rdc",
        name="Congo-Kinshasa",
        iso3="COD",
        phone_country_code="243",
        national_number_length=9,  # 08X/09X XXX XXXX en local (0 + 9)
    ),
    "algerie": CountryProfile(
        code="algerie",
        name="Algérie",
        iso3="DZA",
        phone_country_code="213",
        national_number_length=9,  # mobile (5/6/7 + 8 chiffres) ; le fixe (8 chiffres) n'est pas couvert
    ),
    "tunisie": CountryProfile(
        code="tunisie",
        name="Tunisie",
        iso3="TUN",
        phone_country_code="216",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "niger": CountryProfile(
        code="niger",
        name="Niger",
        iso3="NER",
        phone_country_code="227",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "tchad": CountryProfile(
        code="tchad",
        name="Tchad",
        iso3="TCD",
        phone_country_code="235",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "burundi": CountryProfile(
        code="burundi",
        name="Burundi",
        iso3="BDI",
        phone_country_code="257",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    "centrafrique": CountryProfile(
        code="centrafrique",
        name="Centrafrique",
        iso3="CAF",
        phone_country_code="236",
        national_number_length=8,  # pas de préfixe "0"
        trunk_prefix="",
    ),
    # ---------------------------------------------------------------------------
    # Afrique anglophone
    # ---------------------------------------------------------------------------
    "ghana": CountryProfile(
        code="ghana",
        name="Ghana",
        iso3="GHA",
        phone_country_code="233",
        national_number_length=9,  # 0XX XXX XXXX en local (0 + 9)
    ),
    "nigeria": CountryProfile(
        code="nigeria",
        name="Nigeria",
        iso3="NGA",
        phone_country_code="234",
        national_number_length=10,  # 0803 XXX XXXX en local (0 + 10)
    ),
    "egypt": CountryProfile(
        code="egypt",
        name="Égypte",
        iso3="EGY",
        phone_country_code="20",
        national_number_length=10,  # mobile (010/011/012/015 + 8 chiffres) ; le fixe varie, non couvert
    ),
    "ethiopia": CountryProfile(
        code="ethiopia",
        name="Éthiopie",
        iso3="ETH",
        phone_country_code="251",
        national_number_length=9,  # 09X XXX XXXX en local (0 + 9)
    ),
    "kenya": CountryProfile(
        code="kenya",
        name="Kenya",
        iso3="KEN",
        phone_country_code="254",
        national_number_length=9,  # 07XX XXX XXX en local (0 + 9)
    ),
    "uganda": CountryProfile(
        code="uganda",
        name="Ouganda",
        iso3="UGA",
        phone_country_code="256",
        national_number_length=9,  # 07XX XXX XXX en local (0 + 9)
    ),
    "rwanda": CountryProfile(
        code="rwanda",
        name="Rwanda",
        iso3="RWA",
        phone_country_code="250",
        national_number_length=9,  # 07XX XXX XXX en local (0 + 9)
    ),
    "sudan": CountryProfile(
        code="sudan",
        name="Soudan",
        iso3="SDN",
        phone_country_code="249",
        national_number_length=9,  # 09X XXX XXXX en local (0 + 9)
    ),
    "botswana": CountryProfile(
        code="botswana",
        name="Botswana",
        iso3="BWA",
        phone_country_code="267",
        national_number_length=8,  # mobile (7X XXX XXX) ; le fixe (7 chiffres) n'est pas couvert ; pas de "0"
        trunk_prefix="",
    ),
    "malawi": CountryProfile(
        code="malawi",
        name="Malawi",
        iso3="MWI",
        phone_country_code="265",
        national_number_length=9,  # 0XXX XX XXXX en local (0 + 9)
    ),
    "namibia": CountryProfile(
        code="namibia",
        name="Namibie",
        iso3="NAM",
        phone_country_code="264",
        national_number_length=9,  # 08X XXX XXXX en local (0 + 9)
    ),
    "zambia": CountryProfile(
        code="zambia",
        name="Zambie",
        iso3="ZMB",
        phone_country_code="260",
        national_number_length=9,  # 09XX XXX XXX en local (0 + 9)
    ),
    "zimbabwe": CountryProfile(
        code="zimbabwe",
        name="Zimbabwe",
        iso3="ZWE",
        phone_country_code="263",
        national_number_length=9,  # mobile (07X XXX XXXX) ; le fixe varie selon la zone, non couvert
    ),
    "liberia": CountryProfile(
        code="liberia",
        name="Libéria",
        iso3="LBR",
        phone_country_code="231",
        national_number_length=9,  # mobile (770 XXX XXX) ; le fixe (8 chiffres) n'est pas couvert
    ),
    "sierra_leone": CountryProfile(
        code="sierra_leone",
        name="Sierra Leone",
        iso3="SLE",
        phone_country_code="232",
        national_number_length=8,  # 0XX XXXXXX en local (0 + 8)
    ),
    "south_africa": CountryProfile(
        code="south_africa",
        name="Afrique du Sud",
        iso3="ZAF",
        phone_country_code="27",
        national_number_length=9,  # 08X XXX XXXX en local (0 + 9)
    ),
}

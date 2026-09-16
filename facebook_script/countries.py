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
}

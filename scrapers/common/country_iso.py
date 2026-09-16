"""
country_iso.py
==============
Mapping code pays interne (clé de `scrapers/countries.py`) -> ISO 3166-1 alpha-3.

L'API IA WipWork (`POST /parse/resume`) attend `country_ids` en alpha-3
(max 2 entrées). Les CV stockés en base portent le code interne du scraper
(`country: "south_africa"`), d'où cette table de correspondance.

Vérifier la couverture des 35 pays de `COUNTRIES` :
    python -m scrapers.common.country_iso
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Code interne -> alpha-3
# ---------------------------------------------------------------------------
COUNTRY_ALPHA3: dict[str, str] = {
    # Afrique francophone
    "maroc": "MAR",
    "cote_ivoire": "CIV",
    "congo": "COG",
    "cameroun": "CMR",
    "senegal": "SEN",
    "burkina": "BFA",
    "guinee": "GIN",
    "togo": "TGO",
    "gabon": "GAB",
    "mauritanie": "MRT",
    "benin": "BEN",
    "mali": "MLI",
    "congo_rdc": "COD",
    "algerie": "DZA",
    "tunisie": "TUN",
    "niger": "NER",
    "tchad": "TCD",
    "burundi": "BDI",
    "centrafrique": "CAF",
    # Afrique anglophone
    "ghana": "GHA",
    "nigeria": "NGA",
    "egypt": "EGY",
    "ethiopia": "ETH",
    "kenya": "KEN",
    "uganda": "UGA",
    "rwanda": "RWA",
    "sudan": "SDN",
    "botswana": "BWA",
    "malawi": "MWI",
    "namibia": "NAM",
    "zambia": "ZMB",
    "zimbabwe": "ZWE",
    "liberia": "LBR",
    "sierra_leone": "SLE",
    "south_africa": "ZAF",
}

# Variantes rencontrées dans les données (anciens exports, script Sénégal,
# libellés FR/EN) — normalisées vers le même alpha-3.
_ALIASES: dict[str, str] = {
    "senegal_senjob": "SEN",
    "senjob": "SEN",
    "afrique_du_sud": "ZAF",
    "south_africa_za": "ZAF",
    "morocco": "MAR",
    "ivory_coast": "CIV",
    "cote_divoire": "CIV",
    "congo_brazzaville": "COG",
    "congo_kinshasa": "COD",
    "rdc": "COD",
    "drc": "COD",
    "cameroon": "CMR",
    "guinea": "GIN",
    "mauritania": "MRT",
    "algeria": "DZA",
    "tunisia": "TUN",
    "chad": "TCD",
    "egypte": "EGY",
    "ethiopie": "ETH",
    "soudan": "SDN",
    "ouganda": "UGA",
    "zambie": "ZMB",
    "zimbabwe_zw": "ZWE",
    "sierraleone": "SLE",
    "burkina_faso": "BFA",
    "central_african_republic": "CAF",
}

_ALPHA3_VALUES = frozenset(COUNTRY_ALPHA3.values())


def _normalize(value: str) -> str:
    """"South Africa" / "south-africa" / " SOUTH_AFRICA " -> "south_africa"."""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def alpha3_for(country: str | None) -> str | None:
    """Retourne le code alpha-3 d'un pays, ou None si inconnu.

    Accepte le code interne du scraper, quelques variantes usuelles, ou
    directement un alpha-3 (`"ZAF"` -> `"ZAF"`).
    """
    if not country:
        return None

    raw = country.strip()
    if len(raw) == 3 and raw.upper() in _ALPHA3_VALUES:
        return raw.upper()

    key = _normalize(raw)
    return COUNTRY_ALPHA3.get(key) or _ALIASES.get(key)


def missing_alpha3_codes() -> list[str]:
    """Codes de `COUNTRIES` sans correspondance alpha-3 (doit rester vide)."""
    from scrapers.countries import COUNTRIES

    return sorted(code for code in COUNTRIES if alpha3_for(code) is None)


if __name__ == "__main__":
    manquants = missing_alpha3_codes()
    if manquants:
        print(f"❌ {len(manquants)} pays sans alpha-3 : {', '.join(manquants)}")
    else:
        print(f"✅ {len(COUNTRY_ALPHA3)} pays mappés, aucun code manquant.")

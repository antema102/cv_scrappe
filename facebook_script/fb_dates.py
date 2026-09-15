"""
facebook_script/fb_dates.py
===========================
Date de publication d'une publication Facebook, pour job_publications_scrappe
(published_at). Deux sources, par ordre de fiabilité :
  1. l'infobulle du lien horodatage ("samedi 12 septembre 2026 à 14:32"),
  2. le texte affiché ("3 j", "Hier à 14:05", "12 septembre"), relatif à la
     date de scraping.
Renvoie aussi la précision, pour savoir ce que vaut la date en revue.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta

MONTHS = {
    "janvier": 1, "janv": 1, "january": 1, "jan": 1,
    "fevrier": 2, "fevr": 2, "fev": 2, "february": 2, "feb": 2,
    "mars": 3, "march": 3, "mar": 3,
    "avril": 4, "avr": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6, "jun": 6,
    "juillet": 7, "juil": 7, "july": 7, "jul": 7,
    "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "sept": 9, "september": 9, "sep": 9,
    "octobre": 10, "oct": 10, "october": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "decembre": 12, "dec": 12, "december": 12,
}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

RELATIVE_RE = re.compile(
    r"^(\d{1,3})\s*(min|mn|m|minutes?|h|hr|hrs|heures?|hours?|j|jours?|d|days?|sem|semaines?|w|wk|weeks?|an|ans|y|yr|years?)\.?$"
)
TIME_RE = re.compile(r"(?:\ba\b|\bat\b|,)?\s*(\d{1,2})\s*[:h]\s*(\d{2})\s*(am|pm)?")
DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:er)?\s+({_MONTH})\.?(?:\s+(\d{{4}}))?")
MONTH_DAY_RE = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in decomposed if not unicodedata.combining(char)).lower()
    return re.sub(r"\s+", " ", folded).strip()


def _apply_time(day: datetime, text: str) -> tuple[datetime, bool]:
    match = TIME_RE.search(text)
    if not match:
        return day, False
    hour, minute = int(match.group(1)), int(match.group(2))
    if match.group(3) == "pm" and hour < 12:
        hour += 12
    elif match.group(3) == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return day, False
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0), True


def parse_facebook_date(text: str, now: datetime) -> tuple[datetime, str] | None:
    """
    (date, précision "minute" | "hour" | "day" | "week" | "year") ou None.
    `now` doit être dans le fuseau d'affichage de Facebook (celui du navigateur).
    """
    folded = _fold(text)
    if not folded:
        return None
    if folded in ("a l'instant", "a l’instant", "just now", "maintenant"):
        return now.replace(second=0, microsecond=0), "minute"

    match = RELATIVE_RE.match(folded)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        if unit.startswith(("min", "mn")) or unit == "m":
            return now - timedelta(minutes=amount), "minute"
        if unit.startswith(("h", "hr")):
            return now - timedelta(hours=amount), "hour"
        if unit.startswith(("j", "d")):
            return now - timedelta(days=amount), "day"
        if unit.startswith(("sem", "w")):
            return now - timedelta(weeks=amount), "week"
        try:
            return now.replace(year=now.year - amount), "year"
        except ValueError:  # 29 février -> année non bissextile
            return now.replace(year=now.year - amount, day=28), "year"

    if folded.startswith(("hier", "yesterday")):
        day, has_time = _apply_time(now - timedelta(days=1), folded)
        return day, "minute" if has_time else "day"

    match = DAY_MONTH_RE.search(folded)
    order = "dm"
    if not match:
        match = MONTH_DAY_RE.search(folded)
        order = "md"
    if not match:
        return None
    if order == "dm":
        day_number, month, year = int(match.group(1)), MONTHS[match.group(2)], match.group(3)
    else:
        month, day_number, year = MONTHS[match.group(1)], int(match.group(2)), match.group(3)
    try:
        candidate = now.replace(
            year=int(year) if year else now.year, month=month, day=day_number, hour=0, minute=0, second=0, microsecond=0
        )
    except ValueError:
        return None
    if not year and candidate > now + timedelta(days=1):
        candidate = candidate.replace(year=candidate.year - 1)  # "28 décembre" lu en janvier
    candidate, has_time = _apply_time(candidate, folded[match.end():])
    return candidate, "minute" if has_time else "day"


def published_at(tooltip: str, time_text: str, scraped_at: str) -> tuple[str | None, str]:
    """
    (published_at pour job_publications_scrappe, précision). Date seule
    ("2026-09-12", comme les autres scrapers) si l'heure n'est pas connue, sinon
    ISO complet avec fuseau. (None, "") si aucune date n'est lisible.
    """
    try:
        now = datetime.fromisoformat(scraped_at).astimezone()  # fuseau local = celui affiché par Facebook
    except ValueError:
        now = datetime.now().astimezone()
    for source, value in (("tooltip", tooltip), ("time_text", time_text)):
        parsed = parse_facebook_date(value or "", now)
        if parsed is None:
            continue
        moment, precision = parsed
        if precision in ("minute", "hour"):
            return moment.isoformat(timespec="minutes"), f"{precision} ({source})"
        return moment.date().isoformat(), f"{precision} ({source})"
    return None, ""


def is_too_old(tooltip: str, time_text: str, scraped_at: str, max_age_days: int, today: date | None = None) -> bool:
    """
    True si la publication date d'avant les `max_age_days` derniers jours, aujourd'hui compris :
    avec 15, le 15 septembre on garde du 1er au 15 septembre, le 31 août est trop ancien.
    False si la date est illisible (dans le doute on garde) ou si max_age_days <= 0 (filtre désactivé).
    """
    if max_age_days <= 0:
        return False
    published, _ = published_at(tooltip, time_text, scraped_at)
    if not published:
        return False
    today = today or datetime.now().astimezone().date()
    return date.fromisoformat(published[:10]) < today - timedelta(days=max_age_days - 1)

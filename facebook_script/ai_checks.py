"""
facebook_script/ai_checks.py
============================
Vérifie les réponses de l'IA (ai_extractor.py) contre ce qui est réellement écrit dans la publication, à chaque
consolidation (pages.py) : l'IA « corrige » parfois un email, invente un numéro, recopie le contact d'une autre
publication ou propose une offre absente de l'affiche. Aucun appel à l'IA ici : comparaison de textes, gratuite,
refaite à chaque lancement (les analyses en cache ne sont jamais modifiées).

Source d'une publication = son texte + le texte OCR de chaque image + ses liens + les champs texte de la page
(nom, adresse, « À propos »…).

  - emails : gardés s'ils sont dans la source (même avec les espaces de l'OCR « rh @ boa .mg »), ou si leur partie
    avant « @ » y est (l'IA a le droit de corriger « gmail.corn ») ; sinon retirés ;
  - téléphones : gardés si leurs 8 derniers chiffres sont dans la source (chiffres stylisés « 𝟎𝟑𝟒 » compris) ;
  - site web : gardé si son domaine est dans la source ;
  - salaire : gardé si ses montants sont dans la source ; date limite : gardée si son jour y est ;
  - offre : intitulé d'origine (title_original, sinon title) retrouvé dans la source — au moins la moitié de ses
    mots significatifs —, sinon offre « non_verifiee » (jamais publiée, sauf pages.py --keep-unverified).
Exception : une publication dont au moins une image a été JOINTE à l'IA (ai_mode "image") : l'IA a pu y lire ce que
l'OCR n'a pas lu ; rien n'est retiré, l'élément est seulement signalé « non vérifiable ». Même chose pour l'intitulé
d'une offre traduite (prompt d'avant v8, sans title_original) d'une publication en écriture non latine.

Langue : intitulé encore en écriture arabe / hébraïque / éthiopienne / tifinagh après l'analyse = traduction
manquante, offre « non_traduite » (jamais publiée) tant que pages.py --retranslate ne l'a pas refaite ; description
ou élément de liste dans ces écritures : remplacé (description assemblée) ou retiré.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from company_registry import fold, website_domain

# Écritures qu'une offre publiée ne doit plus contenir : hébreu, arabe (et formes de présentation), syriaque, thâna,
# n'ko, éthiopien, tifinagh
FOREIGN_SCRIPT_RE = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿ሀ-᎟ⴰ-⵿]")
EMAIL_RE = re.compile(r"[a-z0-9][a-z0-9._%+\-]*@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+")
PHONE_RUN_RE = re.compile(r"\+?\d[\d\s.\-()/]{5,}\d")
NUMBER_RE = re.compile(r"\d+")
PHONE_TAIL = 8  # chiffres comparés : assez pour un numéro, insensible à l'indicatif écrit ou non
TITLE_PREFIX = 5  # « commerciaux » retrouve « commercial », « assistante » retrouve « assistant(e) »
STOPWORDS = {
    "les", "des", "une", "pour", "avec", "dans", "sur", "par", "aux", "chez", "son", "ses", "est", "qui", "que",
    "and", "the", "for", "with", "from", "our", "your", "offre", "offres", "emploi", "poste", "postes", "recrutement",
    "recrute", "recherche", "urgent",
}
ATTACHED = "image"  # images[i].ai_mode d'une image jointe à l'IA (pages.select_images_for_ai)
# Intitulé qui n'est pas un poste (texte plié, ponctuation retirée) : « Offre d'emploi » d'une affiche qui annonce
# seulement une nouvelle adresse de candidature (PROMETAL, 24/11/2026), « Recrutement », « Nous recrutons »...
GENERIC_TITLE_RE = re.compile(
    r"(?:(?:urgent|avis de|nouvelle?s?|grande?) )*"
    r"(?:offres? d'? ?emplois?|offres? de stages?|offres?|annonces?|recrutements?|recrutement en cours|nous recrutons|"
    r"on recrute|.{0,40} recrute|emplois?|jobs?|postes?|postes? a pourvoir|postes? vacants?|opportunites? d'? ?emplois?|"
    r"vacanc(?:y|ies)|job (?:offer|opening|vacancy)|we are hiring|we re hiring|hiring|now hiring|candidature spontanee)"
    r"(?: (?:urgent|disponibles?|du jour))*"
)
# Phrase d'une description GÉNÉRÉE (publication sans description) qui parle du candidat ou des conditions : inventée
REQUIREMENT_RE = re.compile(
    r"\b(?:candidat|candidate|experience|experiment|diplom|qualifi|profil|competen|exig|requis|salaire|remuner|"
    r"avantage|horaire|contrat|cdi|cdd|bac\b|licence|master|ans d|years? of|must have|required|salary)"
)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.findall(text or "") if s.strip()]


def has_foreign_script(text: str) -> bool:
    return bool(FOREIGN_SCRIPT_RE.search(text or ""))


def _plain(text: str) -> str:
    """NFKC (chiffres stylisés 𝟎𝟑𝟒 -> 034), sans emojis « keycap », minuscules."""
    return unicodedata.normalize("NFKC", text or "").replace("️", "").replace("⃣", "").lower()


@dataclass(slots=True)
class PostSource:
    """Ce qui est vraiment écrit (texte, OCR, liens, page), normalisé pour les comparaisons."""
    folded: str  # sans accents, minuscules (intitulés, noms)
    compact: str  # minuscules, sans espaces autour de « @ » et des points (emails de l'OCR)
    emails: set[str]
    phone_digits: list[str]  # chiffres de chaque suite qui ressemble à un numéro
    numbers: set[str]  # nombres écrits (montants regroupés : « 500 000 » -> « 500000 »)
    foreign: bool  # la source contient une écriture non latine (annonce à traduire)
    attached_images: bool  # au moins une image jointe à l'IA : contenu non vérifiable par l'OCR


def post_source(post: dict[str, Any], page: dict[str, Any]) -> PostSource:
    parts = [post.get("text", ""), *(str(link) for link in post.get("links", []) or [])]
    parts += [image.get("ocr_text", "") for image in post.get("images", []) or []]
    parts += [value for value in page.values() if isinstance(value, str)]
    raw = "\n".join(part for part in parts if part)
    plain = _plain(raw)
    compact = re.sub(r"\s*(?:@|\(at\)|\[at\])\s*", "@", plain)
    compact = re.sub(r"(?<=[a-z0-9])\s*\.\s*(?=[a-z]{2,6}\b)", ".", compact)
    grouped = re.sub(r"(?<=\d)[\s.,  ](?=\d{3}\b)", "", plain)
    return PostSource(
        folded=fold(raw), compact=compact, emails=set(EMAIL_RE.findall(compact)),
        phone_digits=[re.sub(r"\D", "", run) for run in PHONE_RUN_RE.findall(plain)],
        numbers=set(NUMBER_RE.findall(grouped)), foreign=has_foreign_script(raw),
        attached_images=any(image.get("ai_mode") == ATTACHED for image in post.get("images", []) or []),
    )


# ---------------------------------------------------------------------------
# Éléments un par un
# ---------------------------------------------------------------------------


def email_in_source(email: str, source: PostSource) -> bool:
    email = _plain(email).strip().rstrip(".")
    if email in source.emails or email in source.compact:
        return True
    local = email.split("@", 1)[0]
    return len(local) >= 3 and f"{local}@" in source.compact  # domaine corrigé par l'IA (« gmail.corn »)


def phone_in_source(phone: str, source: PostSource) -> bool:
    digits = re.sub(r"\D", "", _plain(phone))
    if len(digits) < 6:
        return False
    tail = digits[-PHONE_TAIL:]
    return any(tail in run for run in source.phone_digits)


def website_in_source(website: str, source: PostSource) -> bool:
    domain = website_domain(website)
    return not domain or domain in source.compact  # facebook.com & co : pas un site d'entreprise, rien à vérifier


def _amounts(text: str) -> list[str]:
    grouped = re.sub(r"(?<=\d)[\s.,  ](?=\d{3}\b)", "", _plain(text))
    return [number for number in NUMBER_RE.findall(grouped) if len(number) >= 3]


def salary_in_source(salary: str, source: PostSource) -> bool:
    return all(amount in source.numbers for amount in _amounts(salary))  # « À négocier » : rien à vérifier


def deadline_in_source(deadline: str, source: PostSource) -> bool:
    day = next((n for n in NUMBER_RE.findall(_plain(deadline)) if len(n) <= 2), None)
    return day is None or day.lstrip("0") in {n.lstrip("0") for n in source.numbers}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", fold(text)) if len(w) >= 3 and not w.isdigit() and w not in STOPWORDS]


def title_in_source(title: str, source: PostSource) -> bool:
    words = _words(title)
    if not words:
        return True  # « H/F », sigle court : rien de significatif à vérifier
    found = sum(1 for word in words if word[:TITLE_PREFIX] in source.folded)
    return found * 2 >= len(words)


# ---------------------------------------------------------------------------
# Une réponse de l'IA
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CheckReport:
    notes: list[str] = field(default_factory=list)
    removed: dict[str, int] = field(default_factory=dict)  # "emails" -> nombre retiré

    def remove(self, kind: str, note: str) -> None:
        self.removed[kind] = self.removed.get(kind, 0) + 1
        self.notes.append(note)


def _check_list(values: list[str], present: Any, source: PostSource, kind: str, label: str, report: CheckReport) -> list[str]:
    kept = []
    for value in values:
        if present(value, source):
            kept.append(value)
        elif source.attached_images:
            kept.append(value)
            report.notes.append(f"{label} non vérifiable (image jointe à l'IA) : {value}")
        else:
            report.remove(kind, f"{label} retiré, absent de la publication : {value}")
    return kept


def check_company(company: dict[str, Any], source: PostSource, report: CheckReport) -> dict[str, Any]:
    checked = dict(company)
    name = company.get("name", "")
    checked["emails"] = _check_list(company.get("emails", []), email_in_source, source, "emails", f"email ({name})", report)
    checked["phone_numbers"] = _check_list(
        company.get("phone_numbers", []), phone_in_source, source, "phones", f"téléphone ({name})", report)
    website = company.get("website", "")
    if website and not website_in_source(website, source):
        if source.attached_images:
            report.notes.append(f"site non vérifiable (image jointe à l'IA) : {website}")
        else:
            checked["website"] = ""
            report.remove("websites", f"site retiré, absent de la publication : {website}")
    return checked


def check_offer(offer: dict[str, Any], source: PostSource, report: CheckReport) -> dict[str, Any]:
    """
    Offre nettoyée + "_check" ("ok", "non_verifiee", "non_traduite"), "_translated", "_notes" : champs de travail de
    pages.py, jamais recopiés tels quels dans un document backend.
    """
    checked = dict(offer)
    first_note = len(report.notes)
    title = offer.get("title", "")
    label = f"« {title[:60]} »"
    checked["emails"] = _check_list(offer.get("emails", []), email_in_source, source, "emails", f"email de {label}", report)
    checked["phone_numbers"] = _check_list(
        offer.get("phone_numbers", []), phone_in_source, source, "phones", f"téléphone de {label}", report)
    for field_name, present, kind in (("salary", salary_in_source, "salaries"), ("deadline", deadline_in_source, "deadlines")):
        value = offer.get(field_name, "")
        if value and not present(value, source):
            if source.attached_images:
                report.notes.append(f"{field_name} non vérifiable (image jointe à l'IA) de {label} : {value}")
            else:
                checked[field_name] = ""
                report.remove(kind, f"{field_name} retiré de {label}, absent de la publication : {value}")
    for field_name in ("tasks", "qualifications", "skills"):
        items = offer.get(field_name, [])
        checked[field_name] = [item for item in items if not has_foreign_script(item)]
        if len(checked[field_name]) < len(items):
            report.remove("untranslated", f"{len(items) - len(checked[field_name])} élément(s) non traduit(s) retiré(s) de {field_name} de {label}")
    if has_foreign_script(offer.get("description", "")):
        checked["description"] = ""  # remplacée par une description assemblée (offer_description)
        report.remove("untranslated", f"description non traduite de {label} remplacée")
    elif offer.get("description_generated"):
        # Description écrite par l'IA faute de description dans la publication : elle ne doit présenter que le métier.
        # Vu le 24/11/2026 : « Les candidats doivent avoir une expérience pertinente dans le domaine. » (inventé)
        kept = [s for s in _sentences(offer.get("description", "")) if not REQUIREMENT_RE.search(fold(s))]
        if len(kept) < len(_sentences(offer.get("description", ""))):
            checked["description"] = " ".join(kept)
            report.remove("invented", f"phrase sur le profil / les conditions retirée de la description générée de {label}")

    status = "ok"
    if has_foreign_script(title):
        status = "non_traduite"
        report.notes.append(f"offre {label} non traduite : jamais publiée (pages.py --retranslate)")
    elif GENERIC_TITLE_RE.fullmatch(" ".join(re.findall(r"[a-z0-9']+", fold(title).replace("’", "'")))):
        status = "intitule_generique"
        report.notes.append(f"offre {label} sans poste précis : jamais publiée")
    else:
        original = offer.get("title_original", "")
        translated = bool(original) and fold(original) != fold(title)
        if original:
            grounded = title_in_source(original, source)
        elif source.foreign:
            grounded = True  # analyse d'avant v8 d'une annonce traduite : intitulé d'origine inconnu
            report.notes.append(f"intitulé {label} non vérifiable (annonce traduite, analyse d'avant v8)")
        else:
            grounded = title_in_source(title, source)
        if not grounded:
            if source.attached_images:
                report.notes.append(f"intitulé {label} non vérifiable (image jointe à l'IA)")
            else:
                status = "non_verifiee"
                report.notes.append(f"offre {label} introuvable dans la publication : jamais publiée (--keep-unverified)")
        checked["_translated"] = translated or offer.get("source_language", "") not in ("", "fr", "en")
    checked["_check"] = status
    checked["_notes"] = report.notes[first_note:]  # remarques propres à cette offre (quality.checks du document)
    return checked


def check_result(result: dict[str, Any], source: PostSource) -> tuple[dict[str, Any], CheckReport]:
    """Copie nettoyée de la réponse de l'IA (l'analyse en cache n'est jamais modifiée) + rapport."""
    report = CheckReport()
    checked = dict(result)
    checked["companies"] = [check_company(company, source, report) for company in result.get("companies", [])]
    checked["job_offers"] = [check_offer(offer, source, report) for offer in result.get("job_offers", [])]
    return checked, report


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


MIN_RAW_DESCRIPTION_CHARS = 80


def assembled_description(offer: dict[str, Any], company_name: str) -> str:
    """Description faite uniquement des champs extraits (rien d'inventé) quand l'IA n'en a pas donné."""
    head = offer.get("title", "").strip()
    if company_name:
        head += f" chez {company_name}"
    if offer.get("location"):
        head += f" ({offer['location']})"
    parts = [head + "."]
    if offer.get("contract_type"):
        parts.append(f"Contrat : {offer['contract_type']}.")
    if offer.get("tasks"):
        parts.append("Missions : " + " ; ".join(offer["tasks"][:6]) + ".")
    if offer.get("qualifications"):
        parts.append("Profil : " + " ; ".join(offer["qualifications"][:4]) + ".")
    if offer.get("how_to_apply"):
        parts.append(f"Candidature : {offer['how_to_apply']}")
    return " ".join(part.replace("..", ".") for part in parts)


def offer_description(offer: dict[str, Any], post_text: str, single_offer: bool, company_name: str) -> tuple[str, str]:
    """
    (description, origine) : "publication" (rédigée par l'IA d'après la publication), "ia_generee" (publication
    sans description : l'IA a écrit une présentation générale du métier, prompt v8), "publication_brute" (texte de
    la publication, seulement si elle ne contient qu'une offre), "assemblee" (champs extraits mis bout à bout).
    """
    description = (offer.get("description") or "").strip()
    if description and not has_foreign_script(description):
        return description, "ia_generee" if offer.get("description_generated") else "publication"
    text = (post_text or "").strip()
    if single_offer and len(text) >= MIN_RAW_DESCRIPTION_CHARS and not has_foreign_script(text):
        return text, "publication_brute"
    return assembled_description(offer, company_name), "assemblee"

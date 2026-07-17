"""
cv_parser.py — extraction d'informations depuis un CV PDF.

Stratégie :
1. Extraction texte directe via pdfplumber.
2. OCR (Tesseract) uniquement si le texte direct est insuffisant.

Retourne un dict contenant : nom, emails, telephones, texte, methode.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TypedDict


pytesseract = None  # chargement paresseux pour ne pas bloquer si non installé


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class CvInfo(TypedDict):
    nom: str
    emails: list[str]
    telephones: list[str]
    texte: str
    methode: str


# ---------------------------------------------------------------------------
# Extraction de texte
# ---------------------------------------------------------------------------

def _texte_via_pdfplumber(pdf_path: str) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages).strip()
    except Exception:
        return ""


def _texte_via_ocr(pdf_path: str) -> str:
    global pytesseract
    try:
        if pytesseract is None:
            import pytesseract as _pyt
            _pyt.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            pytesseract = _pyt

        from pdf2image import convert_from_path

        texte_ocr_pages = []
        pages_en_images = convert_from_path(pdf_path, dpi=300)

        for image in pages_en_images:
            texte_ocr = pytesseract.image_to_string(image)
            texte_ocr_pages.append(texte_ocr)

        return "\n".join(texte_ocr_pages).strip()

        # pages = [pytesseract.image_to_string(img) for img in images]

        # return "\n".join(pages).strip()
    except Exception as e:
        print(f"Debug: OCR extraction failed: {e}")
        return ""


def _est_suffisant(texte: str) -> bool:
    """Texte jugé suffisant si > 80 caractères non-espaces."""
    return len(texte.replace(" ", "").replace("\n", "")) > 80


# ---------------------------------------------------------------------------
# Extraction des champs
# ---------------------------------------------------------------------------
def _chercher_emails(texte: str) -> list[str]:
    if not texte:
        return []

    emails: list[str] = []
    seen: set[str] = set()

    # Email avec espaces autour de @ ou .
    pattern_espace_tolerant = (
        r'([\w.%+-]+)\s*@\s*([\w.-]+)\s*\.\s*([\w]{2,})'
    )

    for nom, domaine, extension in re.findall(pattern_espace_tolerant, texte):
        email = f"{nom.strip()}@{domaine.strip()}.{extension.strip()}".lower()
        if email not in seen:
            seen.add(email)
            emails.append(email)

    # Email classique
    pattern_classique = r'[\w.%+-]+@[\w.-]+\.[\w]{2,}'

    for email in re.findall(pattern_classique, texte):
        email = email.lower()
        if email not in seen:
            seen.add(email)
            emails.append(email)    

    return emails


def _chercher_telephones(texte: str) -> list[str]:
    # Nettoie les artefacts OCR courants sur les préfixes "Tél" / "Tel"
    texte_propre = re.sub(r"(?i)tél[\s.:]*", "", texte)
    texte_propre = re.sub(r"(?i)tel[\s.:]*", "", texte_propre)

    pattern = r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?0[\s.\-]?\d(?:[\s.\-]?\d){8}"
    bruts = re.findall(pattern, texte_propre)
    normalises = [re.sub(r"[\s.\-]", "", t) for t in bruts]
    seen: set[str] = set()
    result: list[str] = []
    for t in normalises:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _extraire_nom(texte: str) -> str:
    if not texte:
        return "Nom non détecté"

    lignes = [l.strip() for l in texte.split("\n") if l.strip()]
    for ligne in lignes[:5]:
        if "@" in ligne:
            continue
        if any(c.isdigit() for c in ligne):
            continue
        mots = ligne.split()
        if 2 <= len(mots) <= 3:
            if all(mot[0].isupper() or mot.isupper() for mot in mots if mot.isalpha()):
                return ligne

    return "Nom non détecté"


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

def extraire_infos_cv(pdf_path: str) -> CvInfo:
    """
    Analyse un PDF et retourne les informations extraites.

    Stratégie en deux passes :
    1. Extraction texte directe (pdfplumber).
    2. OCR (pdf2image + Tesseract) si le texte est insuffisant OU si des
       champs importants sont manquants (nom, emails, téléphones).
       Les champs manquants sont complétés par l'OCR sans écraser ceux
       déjà trouvés.
    """
    if not os.path.exists(pdf_path):
        return CvInfo(
            nom="",
            emails=[],
            telephones=[],
            texte="",
            methode="erreur: fichier introuvable",
        )

    # --- Passe 1 : texte direct ---
    texte_direct = _texte_via_pdfplumber(pdf_path)
    nom_direct = _extraire_nom(texte_direct)
    emails_direct = _chercher_emails(texte_direct)
    phones_direct = _chercher_telephones(texte_direct)

    # Des champs sont-ils manquants ?
    nom_manquant = nom_direct == "Nom non détecté"
    emails_manquant = len(emails_direct) == 0
    phones_manquant = len(phones_direct) == 0
    texte_insuffisant = not _est_suffisant(texte_direct)

    besoin_ocr = texte_insuffisant or nom_manquant or emails_manquant or phones_manquant

    if not besoin_ocr:
        return CvInfo(
            nom=nom_direct,
            emails=emails_direct,
            telephones=phones_direct,
            texte=texte_direct,
            methode="Texte direct",
        )

    # --- Passe 2 : OCR pour compléter ---
    texte_ocr = _texte_via_ocr(pdf_path)

    if not texte_ocr:
        # OCR n'a rien donné, on retourne ce qu'on a
        return CvInfo(
            nom=nom_direct,
            emails=emails_direct,
            telephones=phones_direct,
            texte=texte_direct,
            methode="Texte direct (OCR indisponible)",
        )

    nom_ocr = _extraire_nom(texte_ocr)
    emails_ocr = _chercher_emails(texte_ocr)
    phones_ocr = _chercher_telephones(texte_ocr)

    # Fusion : on garde le résultat direct si non vide, sinon on prend l'OCR
    nom_final = nom_direct if not nom_manquant else nom_ocr
    emails_final = emails_direct if not emails_manquant else emails_ocr
    phones_final = phones_direct if not phones_manquant else phones_ocr

    # Texte final : celui qui contient le plus d'information
    texte_final = texte_ocr if texte_insuffisant else texte_direct

    methode = "Texte direct + OCR" if not texte_insuffisant else "OCR (Tesseract)"

    return CvInfo(
        nom=nom_final,
        emails=emails_final,
        telephones=phones_final,
        texte=texte_final,
        methode=methode,
    )

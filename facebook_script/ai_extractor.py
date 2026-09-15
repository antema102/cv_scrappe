"""
facebook_script/ai_extractor.py
===============================
Analyse d'une publication de page Facebook par l'IA OpenAI (Chat Completions,
sortie "Structured Outputs" : JSON schema strict) : entreprises citées, offres
d'emploi, contacts, produits/services, catégories.

Entrée envoyée au modèle, pour CHAQUE publication (avec ou sans image) :
  - contexte de la page (nom, URL, texte "À propos"),
  - texte de la publication, date affichée,
  - texte OCR de chaque image (indice, fiabilise emails/numéros),
  - toutes les images, redimensionnées (IMAGE_MAX_SIDE) et encodées en base64,
    par lots de MAX_IMAGES_PER_CALL : une publication de 15 images = 2 appels,
    résultats fusionnés ensuite (merge_results).

Cache : downloaded_files/facebook_pages/ai_cache/<sha256>.json, clé = modèle +
version du prompt + texte + empreintes des images (jamais le lien ni la date
relative, qui changent d'une session à l'autre). Chaque lot est aussi mis en
cache (ai_cache/batches/) dès sa réponse : si le 3e lot échoue (429, coupure),
les lots 1-2 déjà payés ne sont pas refacturés au lancement suivant.

Configuration (environnement, puis .env racine / backend/.env, jamais écrasé) :
OPENAI_API_KEY (obligatoire), OPENAI_MODEL (défaut DEFAULT_MODEL),
OPENAI_BASE_URL (défaut https://api.openai.com/v1).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import time
from pathlib import Path
from typing import Any

import requests

PROMPT_VERSION = "fb-pages-v3"  # v2 : images lues par OCR envoyées en texte seul ; v3 : OCR aussi par lots
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_IMAGES_PER_CALL = 8
IMAGE_MAX_SIDE = 1600  # assez pour lire le petit texte des affiches
REQUEST_TIMEOUT = 180
MAX_ATTEMPTS = 4
ABOUT_MAX_CHARS = 4000

POST_KINDS = ["job_offer", "company_promotion", "product_sale", "service_offer", "event", "news", "other"]

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    # Structured Outputs strict : tous les champs requis, aucun champ en plus
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


RESULT_SCHEMA = _object({
    "post_kind": {"type": "string", "enum": POST_KINDS},
    "companies": {
        "type": "array",
        "items": _object({
            "name": _STR,
            "description": _STR,
            "sector": _STR,
            "address": _STR,
            "city": _STR,
            "country": _STR,
            "emails": _STR_LIST,
            "phone_numbers": _STR_LIST,
            "website": _STR,
            "facebook_url": _STR,
            "products_services": _STR_LIST,
            "categories": _STR_LIST,
            "is_page_owner": {"type": "boolean"},
            "evidence": {
                "type": "array",
                "items": _object({"field": _STR, "value": _STR, "source": _STR}),
            },
        }),
    },
    "job_offers": {
        "type": "array",
        "items": _object({
            "title": _STR,
            "company_name": _STR,
            "description": _STR,
            "tasks": _STR_LIST,
            "qualifications": _STR_LIST,
            "skills": _STR_LIST,
            "location": _STR,
            "contract_type": _STR,
            "salary": _STR,
            "deadline": _STR,
            "how_to_apply": _STR,
            "emails": _STR_LIST,
            "phone_numbers": _STR_LIST,
        }),
    },
    "notes": _STR,
})

SYSTEM_PROMPT = """Tu extrais des données structurées de publications de pages Facebook ({country}).
Tu reçois : le contexte de la page, le texte de la publication, le texte OCR des images (peut contenir des erreurs de lecture) et une partie des images elles-mêmes (lot {batch} sur {batches}).
Pour limiter le coût, une image dont l'OCR a lu le texte n'est PAS jointe ("jointe": false) : son texte OCR est alors la seule source, traite-le comme le contenu de l'image. Les images jointes sont surtout des logos ou visuels peu lisibles par l'OCR.

Règles :
- N'invente rien. Un champ absent reste "" ou []. Recopie emails, téléphones et sites exactement comme écrits (corrige seulement une erreur OCR évidente : image jointe, ou forme manifestement cassée comme "gmail.corn").
- companies : chaque entreprise/organisation réellement identifiable (nom, contact ou site). Plusieurs images montrant le même nom/email/téléphone = UNE seule entreprise, informations regroupées.
- is_page_owner = true uniquement si l'entreprise est la page elle-même qui publie. Une page qui relaie des offres ou annonces d'autres entreprises n'est pas ces entreprises.
- description : 1 à 3 phrases factuelles sur l'activité de l'entreprise. sector : secteur d'activité court en français (ex. "Hôtellerie", "Commerce", "BTP").
- address : adresse physique telle qu'écrite ; city : ville seule ; country : pays si indiqué ou évident ({country} par défaut pour une entreprise locale).
- products_services : produits ou services proposés. categories : types de métiers/tâches/services concernés, en français, courts.
- job_offers : une entrée par poste proposé, pour TOUTES les images de ce lot sans exception (stages, alternances et plusieurs postes sur une même affiche compris) ; ne t'arrête pas aux premières. tasks = missions ; qualifications = profil/diplômes/expérience demandés ; skills = compétences ; how_to_apply = modalités de candidature ; deadline = date limite telle qu'écrite.
- evidence : pour chaque email, téléphone, site, adresse et nom d'entreprise retenu, la source ("texte", "page", "image 3"...).
- post_kind : nature principale de la publication."""


class AiError(Exception):
    def __init__(self, message: str, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


class _OutputTruncated(Exception):
    """Réponse coupée (finish_reason "length") ou JSON illisible : le lot est trop gros, on le scinde."""


def load_dotenv(*paths: Path) -> None:
    """Même principe que scrapers/ia_cv_uploader.py : KEY=VALUE, sans écraser l'environnement réel."""
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def mask_secret(secret: str) -> str:
    return f"{secret[:4]}…{secret[-4:]}" if len(secret) > 8 else "***"


def _image_data_url(path: Path) -> str:
    """Image redimensionnée en JPEG (Pillow, installé avec rapidocr) ; fichier brut si Pillow est absent."""
    try:
        from PIL import Image
    except ImportError:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode()}"


class AiExtractor:
    def __init__(self, api_key: str, model: str, cache_dir: Path, base_url: str = DEFAULT_BASE_URL) -> None:
        self.api_key = api_key
        self.model = model
        self.cache_dir = cache_dir
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.session = requests.Session()

    # -- API publique -------------------------------------------------------

    def analyze_post(
        self, post: dict[str, Any], page: dict[str, Any], images: list[dict[str, Any]], country_name: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        images : [{"number": n dans la publication, "path": fichier à joindre ou None (texte OCR seul), "ocr": texte}].
        Renvoie {"result": <RESULT_SCHEMA fusionné>, "meta": {...}} (depuis le cache si possible).
        """
        attached = [image for image in images if image["path"] is not None]
        # Clé sur le contenu stable uniquement : Facebook change le lien (pfbid) et la date relative ("5 j" -> "6 j")
        # d'une même publication entre deux sessions ; même texte + mêmes images = même analyse, pas de nouvel appel
        cache_key = self._key({"page": page.get("page_id", ""), "text": post.get("text", ""), "images": _image_refs(images)}, country_name)
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists() and not force:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            cached["meta"] = {**cached["meta"], "cached": True}  # aucun token consommé par ce lancement
            return cached

        # Lots de MAX_IMAGES_PER_CALL images (jointes OU texte OCR seul) : vérifié sur une publication de 24 affiches,
        # tout l'OCR en un seul appel fait sauter au modèle les offres des dernières affiches (7 offres sur 15)
        batches = [images[i:i + MAX_IMAGES_PER_CALL] for i in range(0, len(images), MAX_IMAGES_PER_CALL)] or [[]]
        results, usage, calls = [], {"prompt_tokens": 0, "completion_tokens": 0}, 0
        for index, batch in enumerate(batches):
            result, batch_usage, batch_calls = self._analyze_batch(
                post, page, batch, index + 1, len(batches), len(images), country_name, force
            )
            results.append(result)
            calls += batch_calls
            for key in usage:
                usage[key] += int(batch_usage.get(key, 0))

        analysis = {
            "result": merge_results(results),
            "meta": {
                "model": self.model, "prompt_version": PROMPT_VERSION, "calls": calls,
                "images": len(attached), "ocr_text_only_images": len(images) - len(attached),
                "usage": usage, "cache_key": cache_key,
            },
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        return analysis

    # -- Détails -------------------------------------------------------------

    def _key(self, content: dict[str, Any], country_name: str) -> str:
        payload = {"model": self.model, "prompt": PROMPT_VERSION, "country": country_name, **content}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _analyze_batch(
        self, post: dict[str, Any], page: dict[str, Any], batch: list[dict[str, Any]], batch_no: int, batches: int,
        total_images: int, country_name: str, force: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        """(résultat, usage, appels réels). Mis en cache dès la réponse ; lot scindé en deux si la réponse est coupée."""
        key = self._key({
            "page": page.get("page_id", ""), "text": post.get("text", ""), "batch": [batch_no, batches],
            "images": _image_refs(batch),
        }, country_name)
        cache_file = self.cache_dir / "batches" / f"{key}.json"
        if cache_file.exists() and not force:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached["result"], {}, 0
        try:
            result, usage = self._call(self._context(post, page, batch, total_images), batch, batch_no, batches, country_name)
            calls = 1
        except _OutputTruncated:
            if len(batch) <= 1:
                raise AiError("réponse coupée ou JSON illisible même pour une seule image", permanent=True) from None
            half = len(batch) // 2
            first = self._analyze_batch(post, page, batch[:half], batch_no, batches, total_images, country_name, force)
            second = self._analyze_batch(post, page, batch[half:], batch_no, batches, total_images, country_name, force)
            result = merge_results([first[0], second[0]])
            usage = {k: int(first[1].get(k, 0)) + int(second[1].get(k, 0)) for k in ("prompt_tokens", "completion_tokens")}
            calls = first[2] + second[2]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"result": result, "usage": usage}, ensure_ascii=False), encoding="utf-8")
        return result, usage, calls

    @staticmethod
    def _context(post: dict[str, Any], page: dict[str, Any], images: list[dict[str, Any]], total_images: int) -> dict[str, Any]:
        return {
            "page": {
                "name": page.get("name", ""),
                "url": page.get("page_url", ""),
                "about": (page.get("about_text", "") or "")[:ABOUT_MAX_CHARS],
            },
            "post": {
                "url": post.get("post_url", ""),
                "date_affichee": post.get("date_tooltip") or post.get("time_text", ""),
                "texte": post.get("text", ""),
                "liens": post.get("links", []),
                "nombre_images": total_images,
            },
            "ocr_images": [
                {"image": image["number"], "jointe": image["path"] is not None, "texte": image["ocr"]}
                for image in images if image["ocr"]
            ],
        }

    def _call(
        self, context: dict[str, Any], batch: list[dict[str, Any]], batch_no: int, batches: int, country_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False, indent=1)}]
        for image in batch:
            if image["path"] is None:
                continue  # texte OCR seul, déjà dans le contexte
            try:
                data_url = _image_data_url(image["path"])
            except Exception:  # fichier supprimé ou corrompu : l'OCR éventuel reste dans le contexte
                content.append({"type": "text", "text": f"image {image['number']} : fichier illisible, non jointe"})
                continue
            content.append({"type": "text", "text": f"image {image['number']}"})
            content.append({"type": "image_url", "image_url": {"url": data_url, "detail": "high"}})
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(country=country_name, batch=batch_no, batches=batches)},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "facebook_post_extraction", "strict": True, "schema": RESULT_SCHEMA},
            },
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            response: requests.Response | None = None
            try:
                response = self.session.post(self.endpoint, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                error = AiError(f"{type(exc).__name__}", permanent=False)
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                        choice = body["choices"][0]
                        message = choice["message"]
                    except (ValueError, KeyError, IndexError):
                        error = AiError(f"réponse inattendue : {response.text[:200]}", permanent=False)
                    else:
                        if message.get("refusal"):
                            raise AiError(f"refus du modèle : {message['refusal'][:200]}", permanent=True)
                        if choice.get("finish_reason") == "length":
                            raise _OutputTruncated()
                        try:
                            return json.loads(message.get("content") or ""), body.get("usage", {})
                        except json.JSONDecodeError:
                            raise _OutputTruncated() from None
                else:
                    retryable = response.status_code in (408, 409, 429) or response.status_code >= 500
                    error = AiError(f"HTTP {response.status_code} : {response.text[:300]}", permanent=not retryable)
            if error.permanent or attempt == MAX_ATTEMPTS:
                raise error
            time.sleep(_retry_delay(response, attempt))
        raise AiError("tentatives épuisées", permanent=False)


def _retry_delay(response: requests.Response | None, attempt: int) -> float:
    """Délai demandé par OpenAI (Retry-After sur 429) sinon backoff exponentiel, plafonné à 2 minutes."""
    backoff = min(60, 2 ** attempt) + random.uniform(0, 1)
    if response is not None:
        try:
            return min(120.0, max(backoff, float(response.headers.get("retry-after", "0"))))
        except ValueError:
            pass
    return backoff


def _image_refs(images: list[dict[str, Any]]) -> list[list[Any]]:
    """Empreinte stable des images d'un lot : sha256 du fichier joint, sinon le texte OCR envoyé."""
    refs: list[list[Any]] = []
    for image in images:
        if image["path"] is None:
            refs.append([image["number"], "ocr", image["ocr"]])
            continue
        try:
            refs.append([image["number"], "file", _file_sha256(image["path"])])
        except OSError:
            refs.append([image["number"], "missing", image["ocr"]])
    return refs


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


def _company_keys(company: dict[str, Any]) -> set[str]:
    """Nom, emails et 9 derniers chiffres des téléphones : suffisant pour regrouper les lots d'une publication."""
    keys = {f"name:{_norm(company['name'])}"} | {f"email:{_norm(email)}" for email in company["emails"]}
    keys |= {f"tel:{''.join(ch for ch in phone if ch.isdigit())[-9:]}" for phone in company["phone_numbers"]}
    return {key for key in keys if not key.endswith(":")}


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Fusionne les réponses des lots d'images d'une même publication (entreprises par nom/contact commun)."""
    if len(results) == 1:
        return results[0]
    merged: dict[str, Any] = {"post_kind": "other", "companies": [], "job_offers": [], "notes": ""}
    for result in results:
        if merged["post_kind"] == "other":
            merged["post_kind"] = result.get("post_kind", "other")
        merged["notes"] = " ".join(filter(None, [merged["notes"], result.get("notes", "")]))
        for company in result.get("companies", []):
            keys = _company_keys(company)
            existing = next((c for c in merged["companies"] if keys & _company_keys(c)), None)
            if existing is None:
                merged["companies"].append(company)
                continue
            for field in ("name", "description", "sector", "address", "city", "country", "website", "facebook_url"):
                if len(company[field]) > len(existing[field]):
                    existing[field] = company[field]
            for field in ("emails", "phone_numbers", "products_services", "categories", "evidence"):
                existing[field] += [item for item in company[field] if item not in existing[field]]
            existing["is_page_owner"] = existing["is_page_owner"] or company["is_page_owner"]
        for offer in result.get("job_offers", []):
            # Même intitulé chez deux entreprises différentes ("Commercial") = deux offres distinctes
            key = (_norm(offer["title"]), _norm(offer["company_name"]), _norm(offer["deadline"]), _norm(offer["location"]))
            if not any(key == (_norm(o["title"]), _norm(o["company_name"]), _norm(o["deadline"]), _norm(o["location"]))
                       for o in merged["job_offers"]):
                merged["job_offers"].append(offer)
    return merged

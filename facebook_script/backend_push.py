"""
facebook_script/backend_push.py
===============================
Envoi des JSON consolidés des pages Facebook (pages.py) vers le backend, par
les routes existantes (backend/src/routes) :
  1. companies_<pays>.json         -> POST /api/companies              (upsert par company_id)
  2. jobs_<pays>.json              -> POST /api/jobs                   (upsert par job_id)
  3. job_publications_<pays>.json  -> POST /api/jobs/:job_id/publication {published_at}

Entreprises d'abord : une offre référence son company_id. Envoi incrémental :
push_state_<pays>.json garde l'empreinte (sha256) de chaque document envoyé avec
succès ; un document inchangé n'est pas renvoyé (--force-push pour tout renvoyer).

Nettoyage : un document envoyé autrefois puis disparu des JSON (entreprise fusionnée,
offre republiée ou issue d'un doublon de publication) est supprimé du backend
(DELETE /api/jobs/:job_id, DELETE /api/companies/:company_id) seulement si son
remplaçant est connu (find_replacement) et déjà envoyé ; sinon il est gardé en base.

Backend : SCRAPER_API_URL (défaut http://localhost:3500), même convention que le
reste du scraper ; vide = envoi désactivé.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_API_URL = "http://localhost:3500"
REQUEST_TIMEOUT = 30
MAX_ATTEMPTS = 3
SAVE_EVERY = 20

# (type "companies" | "jobs", id disparu des JSON, document lu dans le backend) -> id du document qui le remplace, ou None
ReplacementFinder = Callable[[str, str, dict[str, Any]], str | None]


class BackendError(Exception):
    pass


class RouteMissing(BackendError):
    """404 sur une route qui répond toujours autrement : backend lancé avec un ancien code."""


class BackendClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def health(self) -> bool:
        try:
            return self.session.get(f"{self.base_url}/health", timeout=10).status_code == 200
        except requests.RequestException:
            return False

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Nouvelles tentatives sur erreur réseau / 5xx ; 4xx rendu à l'appelant."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.session.request(method, f"{self.base_url}{path}", timeout=REQUEST_TIMEOUT, **kwargs)
            except requests.RequestException as exc:
                error = f"{type(exc).__name__}"
            else:
                if response.status_code < 500:
                    return response
                error = f"HTTP {response.status_code} : {response.text[:200]}"
            if attempt == MAX_ATTEMPTS:
                raise BackendError(error)
            time.sleep(2 * attempt)
        raise AssertionError("unreachable")

    def post(self, path: str, payload: dict[str, Any]) -> None:
        response = self._request("POST", path, json=payload)
        if response.status_code >= 300:  # 4xx = erreur définitive (document refusé)
            raise BackendError(f"HTTP {response.status_code} : {response.text[:200]}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Document JSON, ou None s'il n'existe pas (404)."""
        response = self._request("GET", path, params=params)
        if response.status_code == 404:
            return None
        if response.status_code >= 300:
            raise BackendError(f"HTTP {response.status_code} : {response.text[:200]}")
        return response.json()

    def delete(self, path: str) -> None:
        response = self._request("DELETE", path)
        if response.status_code == 404:  # nos routes DELETE répondent 200 même si le document n'existe plus
            raise RouteMissing(f"DELETE {path} : route absente (HTTP 404)")
        if response.status_code >= 300:
            raise BackendError(f"HTTP {response.status_code} : {response.text[:200]}")


def _fingerprint(document: Any) -> str:
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _short(doc_ids: list[str], limit: int = 5) -> str:
    return ", ".join(doc_ids[:limit]) + (f" … (+{len(doc_ids) - limit})" if len(doc_ids) > limit else "")


def clean_stale(
    client: BackendClient, state: dict[str, dict[str, str]], state_path: Path,
    documents: dict[str, dict[str, Any]], find_replacement: ReplacementFinder,
) -> None:
    """
    Supprime du backend les documents envoyés autrefois puis disparus des JSON, uniquement quand le
    document qui les remplace est déjà en base avec son contenu actuel. Offres d'abord : une entreprise
    n'est supprimée que si plus aucune offre en base ne la référence (ex. offre gardée faute d'équivalent).
    """
    def sent(kind: str, doc_id: str | None) -> bool:
        return doc_id in documents[kind] and state[kind].get(doc_id) == _fingerprint(documents[kind][doc_id])

    kept_reasons = {
        "jobs": "aucune offre équivalente envoyée (ex. offre que la dernière analyse IA n'a pas retrouvée)",
        "companies": "pas fusionnée dans une entreprise envoyée, ou encore liée à une offre gardée",
    }
    for kind, label in (("jobs", "offres"), ("companies", "entreprises")):
        stale = sorted(doc_id for doc_id in state[kind] if doc_id not in documents[kind])
        if not stale:
            continue
        deleted: list[str] = []
        already_gone: list[str] = []
        kept: list[str] = []
        for doc_id in stale:
            path = f"/api/{kind}/{quote(doc_id, safe='')}"
            try:
                stored = client.get(path)
                if stored is None:
                    if kind == "jobs":
                        client.delete(path)  # supprimée à la main : retire aussi sa date de publication
                    already_gone.append(doc_id)
                else:
                    replacement = find_replacement(kind, doc_id, stored)
                    if kind == "jobs":
                        replaced = sent("jobs", replacement) and (replacement not in documents["publications"] or sent("publications", replacement))
                    else:
                        replaced = sent("companies", replacement) and not (client.get("/api/jobs", {"company_id": doc_id, "limit": 1}) or {}).get("total")
                    if not replaced:
                        kept.append(doc_id)
                        continue
                    client.delete(path)
                    deleted.append(doc_id)
            except RouteMissing:
                _write(state_path, state)
                print("  [ERREUR] Le backend n'a pas les routes DELETE : redémarrez-le (Ctrl+C puis `npm run dev` dans backend/) "
                      "- nettoyage des doublons reporté au prochain envoi.")
                return
            except BackendError as exc:
                print(f"  [ERREUR] nettoyage {label} {doc_id} : {exc}")
                continue
            del state[kind][doc_id]
            if kind == "jobs":
                state["publications"].pop(doc_id, None)
        _write(state_path, state)
        if deleted or already_gone:
            print(f"  doublons {label} : {len(deleted)} supprimé(s) du backend (remplacés par la version regroupée)"
                  + (f", {len(already_gone)} déjà absent(s) de la base" if already_gone else ""))
        if kept:
            print(f"  [INFO] {len(kept)} {label} gardée(s) en base, plus dans les JSON mais {kept_reasons[kind]} : {_short(kept)}")


def push_country(
    output_dir: Path, country_code: str, api_url: str, force: bool = False, find_replacement: ReplacementFinder | None = None,
) -> bool:
    """
    Envoie les documents nouveaux ou modifiés, puis (find_replacement fourni) supprime du backend les doublons
    déjà envoyés. Renvoie False si rien à envoyer ou backend injoignable.
    """
    if not (output_dir / f"companies_{country_code}.json").exists():
        print(f"[ERREUR] Aucun JSON à envoyer dans {output_dir} : lancez d'abord pages.py (scraping + IA).")
        return False
    client = BackendClient(api_url)
    if not client.health():
        print(f"[ERREUR] Backend injoignable ({api_url}/health) : lancez `npm run dev` dans backend/ ou vérifiez SCRAPER_API_URL.")
        return False

    state_path = output_dir / f"push_state_{country_code}.json"
    state: dict[str, dict[str, str]] = {"companies": {}, "jobs": {}, "publications": {}, **_read(state_path)}
    batches = (
        ("companies", "entreprises", _read(output_dir / f"companies_{country_code}.json"), lambda doc_id: "/api/companies"),
        ("jobs", "offres", _read(output_dir / f"jobs_{country_code}.json"), lambda doc_id: "/api/jobs"),
        ("publications", "dates de publication", _read(output_dir / f"job_publications_{country_code}.json"),
         lambda doc_id: f"/api/jobs/{quote(doc_id, safe='')}/publication"),
    )
    print(f"\nEnvoi au backend {api_url}")
    for kind, label, documents, route in batches:
        sent = unchanged = failed = 0
        for doc_id, document in documents.items():
            fingerprint = _fingerprint(document)
            if not force and state[kind].get(doc_id) == fingerprint:
                unchanged += 1
                continue
            payload = {"published_at": document.get("published_at")} if kind == "publications" else document
            try:
                client.post(route(doc_id), payload)
            except BackendError as exc:
                failed += 1
                print(f"  [ERREUR] {label} {doc_id} : {exc}")
                continue
            state[kind][doc_id] = fingerprint
            sent += 1
            if sent % SAVE_EVERY == 0:
                _write(state_path, state)
        _write(state_path, state)
        print(f"  {label} : {sent} envoyée(s), {unchanged} inchangée(s), {failed} en échec")

    documents = {kind: docs for kind, _, docs, _ in batches}
    if find_replacement is not None:
        clean_stale(client, state, state_path, documents, find_replacement)
    else:
        for kind, label in (("companies", "entreprises"), ("jobs", "offres")):
            stale = sorted(doc_id for doc_id in state[kind] if doc_id not in documents[kind])
            if stale:
                print(f"  [INFO] {len(stale)} {label} déjà envoyée(s) puis disparue(s) des JSON, laissée(s) en base "
                      f"(nettoyage désactivé) : {_short(stale)}")
    return True

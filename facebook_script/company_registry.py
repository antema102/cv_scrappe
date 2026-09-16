"""
facebook_script/company_registry.py
===================================
Identification et dédoublonnage des entreprises extraites par l'IA, toutes
publications et toutes pages d'un pays confondues.

Identifiants normalisés d'une entreprise candidate :
  - forts  : email, domaine du site web, page Facebook (id ou nom personnalisé)
  - moyen  : téléphone (E.164 via CountryProfile)
  - faible : nom normalisé (+ ville)

Règles de regroupement (union-find) :
  1. un identifiant fort OU un téléphone en commun -> même entreprise ;
  2. même nom normalisé, villes identiques ou inconnues -> même entreprise ;
  3. jamais de fusion entre deux groupes aux domaines web ou pages Facebook
     différents (conflit = entreprises distinctes) ;
  4. un contact "partagé" (même email/téléphone/site associé à des noms qui ne
     se ressemblent pas : page relais, agence de recrutement) n'est pas utilisé
     pour fusionner.

company_id stable : registre identity_registry_<pays>.json (clé normalisée ->
company_id). Un groupe reprend l'id déjà attribué à l'une de ses clés (le plus
ancien en cas de fusion de deux groupes connus, l'autre devient un alias) ;
sinon "fb-" + sha1 de sa meilleure clé.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from countries import CountryProfile

# Hébergeurs de messagerie / réseaux : jamais un "site web" identifiant une entreprise
GENERIC_HOSTS = {
    "facebook.com", "fb.com", "fb.me", "m.me", "instagram.com", "wa.me", "whatsapp.com", "api.whatsapp.com",
    "tiktok.com", "youtube.com", "youtu.be", "linkedin.com", "twitter.com", "x.com", "t.me", "telegram.me",
    "gmail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "hotmail.fr", "outlook.com", "live.com", "icloud.com",
    "google.com", "forms.gle", "docs.google.com", "bit.ly", "linktr.ee",
}
LEGAL_FORMS = {
    "sarl", "sarlu", "sa", "sas", "sasu", "eurl", "suarl", "snc", "ets", "etablissement", "etablissements",
    "ste", "societe", "company", "co", "ltd", "llc", "inc", "group", "groupe", "madagascar",
}  # "mada" gardé : "Loi Mada" doit rester comparable à "Loimada"
KEY_PRIORITY = ("fb", "web", "email", "tel", "name")
GENERIC_MIN_COMPANIES = 3  # contact vu avec au moins 3 entreprises différentes = agence / page relais
NAME_SIMILARITY = 0.72


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def normalize_name(name: str) -> str:
    """'SHOP LIANTSO SARL' / 'Shop-Liantso' -> 'shop liantso'."""
    tokens = re.findall(r"[a-z0-9]+", fold(name))
    kept = [token for token in tokens if token not in LEGAL_FORMS]
    return " ".join(kept or tokens)


def names_similar(a: str, b: str) -> bool:
    if not a or not b:
        return True  # un nom inconnu ne contredit rien
    if a == b or a in b or b in a:
        return True
    compact_a, compact_b = a.replace(" ", ""), b.replace(" ", "")
    if min(len(compact_a), len(compact_b)) >= 4 and (compact_a in compact_b or compact_b in compact_a):
        return True  # "loi mada" / "loimada"
    first_a, first_b = a.split()[0], b.split()[0]
    shorter, longer = sorted((first_a, first_b), key=len)
    if len(shorter) >= 5 and longer.startswith(shorter):
        return True  # "dookan cash carry" / "dookani" : variantes d'un même nom écrites par l'IA
    tokens_a, tokens_b = set(a.split()), set(b.split())
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return max(jaccard, SequenceMatcher(None, a, b).ratio()) >= NAME_SIMILARITY


def normalize_email(email: str) -> str:
    email = email.strip().lower().rstrip(".")
    return email if re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", email) else ""


def website_domain(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.netloc.lower().split(":")[0].removeprefix("www.").removeprefix("m.")
    if not host or "." not in host or host in GENERIC_HOSTS or any(host.endswith("." + g) for g in GENERIC_HOSTS):
        return ""
    return host


def facebook_page_key(url: str) -> str:
    """'https://www.facebook.com/profile.php?id=615…' -> '615…' ; '/ShopLiantso/' -> 'shopliantso'."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if "facebook.com" not in parsed.netloc.lower():
        return ""
    if parsed.path.rstrip("/") == "/profile.php":
        return parse_qs(parsed.query).get("id", [""])[0]
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments[:1] == ["people"] and len(segments) >= 3:
        return segments[2]
    if segments and segments[0].lower() not in {"groups", "photo", "photo.php", "permalink.php", "story.php", "share", "watch", "events", "hashtag", "reel"}:
        return segments[0].lower()
    return ""


@dataclass(slots=True)
class Candidate:
    """Une entreprise telle qu'extraite d'UNE publication."""
    ref: str  # "<post_id>#<index>"
    post_id: str
    page_id: str
    name: str
    data: dict[str, Any]  # entrée "companies" de la réponse IA
    page: dict[str, Any]  # page Facebook qui a publié
    keys: dict[str, set[str]] = field(default_factory=dict)  # famille -> valeurs normalisées

    def all_keys(self) -> set[str]:
        return {f"{family}:{value}" for family, values in self.keys.items() for value in values}


def build_candidate(post: dict[str, Any], index: int, company: dict[str, Any], page: dict[str, Any], country: CountryProfile) -> Candidate:
    name = normalize_name(company.get("name", ""))
    fb_keys = {facebook_page_key(company.get("facebook_url", ""))} - {""}
    if company.get("is_page_owner"):
        fb_keys.add(page.get("page_id", "").lower())
    city = fold(company.get("city", "")).strip()
    candidate = Candidate(
        ref=f"{post['post_id']}#{index}", post_id=post["post_id"], page_id=page.get("page_id", ""),
        name=name, data=company, page=page,
    )
    candidate.keys = {
        "fb": fb_keys - {""},
        "web": {website_domain(company.get("website", ""))} - {""},
        "email": {normalize_email(email) for email in company.get("emails", [])} - {""},
        "tel": {country.to_e164(phone) for phone in company.get("phone_numbers", [])} - {""},
        "name": {f"{name}|{city}"} if len(name) >= 3 else set(),
    }
    return candidate


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item


class CompanyRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"keys": {}, "companies": {}, "aliases": {}}
        self.generic_keys: set[str] = set()
        if path.exists():
            self.data.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- regroupement ------------------------------------------------------------

    @staticmethod
    def _name_groups_by_key(candidates: list[Candidate]) -> dict[str, int]:
        """Pour chaque contact : nombre de noms d'entreprise dissemblables associés."""
        names_by_key: dict[str, set[str]] = {}
        for candidate in candidates:
            for family in ("email", "tel", "web", "fb"):
                for value in candidate.keys[family]:
                    names_by_key.setdefault(f"{family}:{value}", set()).add(candidate.name)
        groups_by_key: dict[str, int] = {}
        for key, names in names_by_key.items():
            groups: list[str] = []
            for name in sorted(name for name in names if name):
                if not any(names_similar(name, other) for other in groups):
                    groups.append(name)
            groups_by_key[key] = len(groups)
        return groups_by_key

    def cluster(self, candidates: list[Candidate]) -> tuple[list[list[Candidate]], set[str]]:
        """
        shared (renvoyé) : contact vu avec >= 2 noms dissemblables -> jamais utilisé pour fusionner (groupe et
        filiale, erreur OCR sur un nom...). self.generic_keys : vu avec >= GENERIC_MIN_COMPANIES entreprises
        dissemblables (agence de recrutement, page relais) -> retiré aussi des fiches entreprises.
        """
        groups_by_key = self._name_groups_by_key(candidates)
        shared = {key for key, count in groups_by_key.items() if count >= 2}
        self.generic_keys = {key for key, count in groups_by_key.items() if count >= GENERIC_MIN_COMPANIES}
        uf = _UnionFind(len(candidates))
        groups: dict[int, dict[str, set[str]]] = {
            i: {"web": set(c.keys["web"]), "fb": set(c.keys["fb"]), "names": {c.name} - {""}} for i, c in enumerate(candidates)
        }

        def union(a: int, b: int) -> None:
            root_a, root_b = uf.find(a), uf.find(b)
            if root_a == root_b:
                return
            ga, gb = groups[root_a], groups[root_b]
            for family in ("web", "fb"):
                if ga[family] and gb[family] and not ga[family] & gb[family]:
                    return  # deux sites / deux pages Facebook différents : entreprises distinctes
            if not any(names_similar(x, y) for x in ga["names"] for y in gb["names"]) and ga["names"] and gb["names"]:
                return  # aucun nom compatible
            uf.parent[root_b] = root_a
            for family in ("web", "fb", "names"):
                ga[family] |= gb[family]

        first_by_key: dict[str, int] = {}
        for index, candidate in enumerate(candidates):
            for family in ("fb", "web", "email", "tel"):
                for value in candidate.keys[family]:
                    key = f"{family}:{value}"
                    if key in shared:
                        continue
                    if key in first_by_key:
                        union(first_by_key[key], index)
                    else:
                        first_by_key[key] = index
        # Nom identique : ville égale, ou inconnue d'un côté
        by_name: dict[str, list[int]] = {}
        for index, candidate in enumerate(candidates):
            if len(candidate.name) >= 3:
                by_name.setdefault(candidate.name, []).append(index)
        for indexes in by_name.values():
            for i in indexes[1:]:
                city_a = fold(candidates[indexes[0]].data.get("city", "")).strip()
                city_b = fold(candidates[i].data.get("city", "")).strip()
                if not city_a or not city_b or city_a == city_b:
                    union(indexes[0], i)

        clusters: dict[int, list[Candidate]] = {}
        for index, candidate in enumerate(candidates):
            clusters.setdefault(uf.find(index), []).append(candidate)
        return list(clusters.values()), shared

    # -- identifiants stables -----------------------------------------------------

    def assign_ids(self, clusters: list[list[Candidate]], shared: set[str]) -> list[tuple[str, list[Candidate]]]:
        keys_registry: dict[str, str] = self.data["keys"]
        companies: dict[str, Any] = self.data["companies"]
        aliases: dict[str, str] = self.data["aliases"]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        assigned: list[tuple[str, list[Candidate]]] = []
        used_ids: set[str] = set()

        def resolve(company_id: str) -> str:
            seen = {company_id}
            while company_id in aliases and aliases[company_id] not in seen:  # A -> B -> C : chaîne de fusions
                company_id = aliases[company_id]
                seen.add(company_id)
            return company_id

        for cluster in clusters:
            keys = sorted(set().union(*(c.all_keys() for c in cluster)) - shared)
            known = {resolve(keys_registry[k]) for k in keys if k in keys_registry} - used_ids
            if known:
                company_id = min(known, key=lambda cid: companies.get(cid, {}).get("created_at", now))
                for other in known - {company_id}:
                    aliases[other] = company_id  # deux entreprises connues reconnues identiques
            else:
                best = min(keys, key=lambda k: (KEY_PRIORITY.index(k.split(":", 1)[0]), k)) if keys else cluster[0].ref
                base_id = company_id = "fb-" + hashlib.sha1(best.encode("utf-8")).hexdigest()[:12]
                suffix = 2
                while company_id in used_ids:  # groupe scindé depuis le dernier lancement
                    company_id, suffix = f"{base_id}-{suffix}", suffix + 1
            used_ids.add(company_id)
            entry = companies.setdefault(company_id, {"created_at": now, "keys": []})
            entry["keys"] = sorted(set(entry.get("keys", [])) | set(keys))
            entry["updated_at"] = now
            for key in keys:
                keys_registry[key] = company_id
            assigned.append((company_id, cluster))
        return assigned


# ---------------------------------------------------------------------------
# Documents au format du backend (backend/src/models/company.model.ts)
# ---------------------------------------------------------------------------


def _most_common(values: list[str]) -> str:
    values = [value.strip() for value in values if value and value.strip()]
    if not values:
        return ""
    counts = Counter(fold(value) for value in values)
    best = max(counts, key=lambda folded: (counts[folded], len(folded)))
    return next(value for value in values if fold(value) == best)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def company_document(
    company_id: str, cluster: list[Candidate], country: CountryProfile, generic: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
    """
    Uniquement les champs du modèle Company (Mongoose supprime tout le reste). Les emails/téléphones
    génériques (vus avec >= GENERIC_MIN_COMPANIES entreprises : agence de recrutement, page relais) ne
    sont pas attribués à l'entreprise — sinon l'emailing écrirait à l'agence au nom de chaque entreprise.
    """
    owners = [c for c in cluster if c.data.get("is_page_owner")]
    datas = [c.data for c in cluster]
    website = next((d["website"].strip() for d in datas if website_domain(d.get("website", ""))), "")
    if website and "://" not in website:
        website = f"https://{website}"
    company_url = owners[0].page.get("page_url", "") if owners else next(
        (d["facebook_url"] for d in datas if facebook_page_key(d.get("facebook_url", ""))), ""
    )
    phones = [country.to_e164(p) for d in datas for p in d.get("phone_numbers", [])]
    emails = [normalize_email(e) for d in datas for e in d.get("emails", [])]
    country_name = _most_common([d.get("country", "") for d in datas])
    if not country_name or fold(country_name).strip() in (fold(country.name), country.iso3.lower()):
        country_name = country.name  # "madagascar", "MADAGASCAR" -> "Madagascar" (filtre exact du frontend)
    return {
        "company_id": company_id,
        "company_url": company_url,
        "name": _most_common([d.get("name", "") for d in datas]) or (owners[0].page.get("name", "") if owners else ""),
        "city": _most_common([d.get("city", "") for d in datas]),
        "country": country_name,
        "sector": _most_common([d.get("sector", "") for d in datas]),
        "website": website,
        "description": max((d.get("description", "").strip() for d in datas), key=len, default=""),
        "logo_url": owners[0].page.get("logo_url", "") if owners else "",
        "emails": _unique([e for e in emails if e and f"email:{e}" not in generic]),
        "phone_numbers": _unique([country.to_local(p) for p in phones if p and f"tel:{p}" not in generic]),
    }


def review_entry(company_id: str, cluster: list[Candidate], shared: set[str]) -> dict[str, Any]:
    """Ce que le backend ne stocke pas (adresse, produits/services, catégories) + traçabilité des fusions."""
    datas = [c.data for c in cluster]
    keys = set().union(*(c.all_keys() for c in cluster))
    return {
        "company_id": company_id,
        "addresses": _unique([d.get("address", "").strip() for d in datas]),
        "products_services": _unique([p.strip() for d in datas for p in d.get("products_services", [])]),
        "categories": _unique([p.strip() for d in datas for p in d.get("categories", [])]),
        "names_seen": _unique([d.get("name", "").strip() for d in datas]),
        "identity_keys": sorted(keys - shared),
        "shared_keys_ignored": sorted(keys & shared),
        "sources": [{"post_id": c.post_id, "page_id": c.page_id, "is_page_owner": bool(c.data.get("is_page_owner"))} for c in cluster],
        "evidence": [e for d in datas for e in d.get("evidence", [])],
    }

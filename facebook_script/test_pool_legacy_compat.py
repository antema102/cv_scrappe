"""
Non-régression du fonctionnement actuel de post_groups.py (pool de comptes désactivé).

Les sorties de référence (test_data/pool_legacy/expected_*) ont été produites par le code d'AVANT le pool de
comptes (UPDATE_GOLDEN=1, une seule fois) : un écart est une régression à corriger dans le code, jamais dans la
référence. Aucune donnée réelle : offres et groupes fictifs, aucun accès réseau ni Facebook.

    python -m unittest discover -s facebook_script -p "test_pool_legacy_compat.py" -v
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

import post_groups
import scraper as fb
import share
from countries import COUNTRIES

DATA = Path(__file__).resolve().parent / "test_data" / "pool_legacy"
G_MDG = str(DATA / "groups_madagascar.txt")
SETTINGS = str(DATA / "settings.json")
EXCLUDED = str(DATA / "excluded_owners.txt")
RUN_SETTINGS = "@RUN_SETTINGS@"  # remplacé par des réglages écrits à l'exécution (profil dans le dossier temporaire)
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
TEST_COUNTRIES = ("madagascar", "cameroun", "ghana")
DATE_KEYS = {"at", "posted_at", "last_post_at", "since", "until"}
FAKE_STATUSES = ("posted", "pending", "not_member", "posted")

CALLS: list[list[Any]] = []  # appels au faux post_to_group du dernier run_main


class FixedDate(date):
    @classmethod
    def today(cls) -> FixedDate:
        return cls(2026, 12, 1)


class FakeSB:
    """Navigateur factice : ouvert / fermé, pauses instantanées."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> FakeSB:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def sleep(self, seconds: float) -> None:
        pass


def _fake_fetch_offers(api_url: str, country: str) -> tuple[list[dict[str, Any]], int]:
    offers = [o for o in json.loads((DATA / "offers.json").read_text(encoding="utf-8")) if o["companyCountry"] == country]
    return offers, len(offers)


def _fake_post_to_group(sb: Any, group_url: str, text: str, auto: bool, images: Any = None, anonymous: bool = True,
                        on_group_id: Any = None, on_publishing: Any = None) -> str:
    status = FAKE_STATUSES[len(CALLS) % len(FAKE_STATUSES)]
    if status in ("posted", "pending") and on_publishing is not None:
        on_publishing()  # comme le vrai : marqueur « publishing » juste avant le clic
    CALLS.append([fb.PROFILE_DIR.name, group_url, hashlib.sha1(text.encode("utf-8")).hexdigest()[:12], len(images or [])])
    return status


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<date>" if k in DATE_KEYS and v else _normalize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def run_main(argv: list[str], accounts_file: Path | None = None) -> tuple[str, dict[str, Any]]:
    """post_groups.main() isolé : (sortie console, suivis group_posts_*.json normalisés)."""
    CALLS.clear()
    with tempfile.TemporaryDirectory() as tmp_name, ExitStack() as stack:
        tmp = Path(tmp_name)
        out_dir = tmp / "facebook_share"
        out_dir.mkdir()
        run_settings = tmp / "settings_run.json"
        settings = json.loads(Path(SETTINGS).read_text(encoding="utf-8"))
        settings["countries"].setdefault("madagascar", {})["profile"] = str(tmp / "profil_test_madagascar")
        run_settings.write_text(json.dumps(settings), encoding="utf-8")
        files = {code: DATA / f"groups_{code}.txt" for code in TEST_COUNTRIES}

        def fake_list_file(country: Any, kind: str) -> Path:
            return files.get(country.code, tmp / f"absent_{country.code}_{kind}.txt")

        patches = [
            mock.patch.object(share, "fetch_offers", _fake_fetch_offers),
            mock.patch.object(share, "OUTPUT_DIR", out_dir),
            mock.patch.object(post_groups, "LOCK_FILE", out_dir / ".post_groups.lock"),
            mock.patch.object(post_groups, "ACCOUNT_BLOCK_FILE", out_dir / "account_block.json"),
            mock.patch.object(post_groups, "DEBUG_DIR", out_dir / "debug"),
            mock.patch.object(post_groups, "date", FixedDate),
            mock.patch.object(post_groups, "list_file", fake_list_file),
            mock.patch.object(post_groups, "active_countries", lambda *kinds: [COUNTRIES[c] for c in TEST_COUNTRIES]),
            mock.patch.object(post_groups, "ACCOUNTS_FILE", accounts_file or tmp / "absent_accounts.json", create=True),
            mock.patch.object(post_groups, "SB", FakeSB),
            mock.patch.object(post_groups, "post_to_group", _fake_post_to_group),
            mock.patch.object(fb, "_activate_cdp_mode", lambda sb: None),
            mock.patch.object(fb, "close_orphan_chromes", lambda profile_dir: None),
            mock.patch.object(fb, "_reset_profile_exit_state", lambda profile_dir: None),
            mock.patch.object(fb, "is_logged_in", lambda sb: True),
            mock.patch.object(fb, "PROFILE_DIR", fb.PROFILE_DIR),  # remis en place après le test
            mock.patch.object(sys, "argv", ["post_groups.py", *[str(run_settings) if a == RUN_SETTINGS else a for a in argv]]),
        ]
        for patch in patches:
            stack.enter_context(patch)
        random.seed(12345)
        buffer = StringIO()
        with redirect_stdout(buffer):
            try:
                post_groups.main()
            except SystemExit as exc:
                print(f"<exit {exc.code}>")
        out = buffer.getvalue().replace(str(tmp), "<tmp>")
        out = re.sub(r"<tmp>\S*", lambda m: m.group(0).replace("\\", "/"), out)  # même référence Windows / Linux
        state = {path.name: _normalize(json.loads(path.read_text(encoding="utf-8")))
                 for path in sorted(out_dir.glob("group_posts_*.json"))}
    return out, state


def _check(test: unittest.TestCase, name: str, actual: Any) -> None:
    path = DATA / name
    if UPDATE:
        text = actual if isinstance(actual, str) else json.dumps(actual, ensure_ascii=False, indent=1) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    expected_text = path.read_text(encoding="utf-8")
    expected = expected_text if isinstance(actual, str) else json.loads(expected_text)
    test.assertEqual(actual, expected)


PREVIEW_MDG = ["--country", "madagascar", "--groups-file", G_MDG, "--settings-file", SETTINGS,
               "--excluded-owners-file", EXCLUDED, "--preview", "--no-images"]
PREVIEW_ALL = ["--all-countries", "--settings-file", SETTINGS, "--excluded-owners-file", EXCLUDED,
               "--preview", "--no-images"]
RUN_MDG = ["--country", "madagascar", "--groups-file", G_MDG, "--settings-file", RUN_SETTINGS,
           "--excluded-owners-file", EXCLUDED, "--no-images"]
GOLDEN_CASES = (  # (arguments, fichier de référence, exécution réelle ?)
    (PREVIEW_MDG, "expected_preview_madagascar.txt", False),
    (PREVIEW_ALL, "expected_preview_all.txt", False),
    (RUN_MDG, "expected_run_madagascar.json", True),
)


def golden_value(argv: list[str], real_run: bool, accounts_file: Path | None = None) -> Any:
    out, state = run_main(argv, accounts_file)
    return {"stdout": out, "calls": [list(c) for c in CALLS], "state": state} if real_run else out


class LegacyCompatTest(unittest.TestCase):
    maxDiff = None

    def test_preview_madagascar_identical(self) -> None:
        _check(self, "expected_preview_madagascar.txt", golden_value(PREVIEW_MDG, False))

    def test_preview_all_countries_identical(self) -> None:  # commande PM2 en aperçu
        _check(self, "expected_preview_all.txt", golden_value(PREVIEW_ALL, False))

    def test_run_madagascar_identical(self) -> None:  # publication avec faux navigateur
        _check(self, "expected_run_madagascar.json", golden_value(RUN_MDG, True))

    def test_inactive_accounts_keep_legacy_output(self) -> None:
        """Pool désactivé (comptes listés, aucun actif) : sorties identiques à la référence."""
        if UPDATE:
            self.skipTest("référence produite par les trois tests précédents")
        with tempfile.TemporaryDirectory() as tmp:
            accounts = Path(tmp) / "share_accounts.json"
            accounts.write_text(json.dumps({"accounts": [{"profile": "compte_inactif_1", "active": False},
                                                         {"profile": "compte_inactif_2"}]}), encoding="utf-8")
            for argv, name, real_run in GOLDEN_CASES:
                with self.subTest(reference=name):
                    _check(self, name, golden_value(argv, real_run, accounts))

    def test_empty_shipped_accounts_file_keeps_legacy_output(self) -> None:
        """Fichier de comptes livré (« accounts »: []) : sorties identiques à la référence."""
        if UPDATE:
            self.skipTest("référence produite par les trois tests précédents")
        shipped = Path(__file__).resolve().parent / "share_accounts.json"
        for argv, name, real_run in GOLDEN_CASES:
            with self.subTest(reference=name):
                _check(self, name, golden_value(argv, real_run, shipped))


if __name__ == "__main__":
    unittest.main()

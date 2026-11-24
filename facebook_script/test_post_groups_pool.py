"""
Pool de comptes dans post_groups.py avec un faux navigateur : verrou de profil, identité du compte, vérification des
adhésions (lecture seule), publication en mode pool. Aucun accès réseau ni Facebook.

    python -m unittest discover -s facebook_script -p "test_post_groups_pool.py" -v
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest import mock

import post_groups as pg
import scraper as fb
import share
from countries import COUNTRIES

EVENTS: list[str] = []


class Cookie:
    def __init__(self, name: str, value: str) -> None:
        self.name, self.value = name, value


class FakeCdp:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    def get_all_cookies(self) -> list[Cookie]:
        return [Cookie("xs", "x"), Cookie("c_user", self.user_id)] if self.user_id else []


class FakeSB:
    """Faux SeleniumBase : journalise ouverture / fermeture ; à la fermeture, vérifie que le verrou est encore tenu."""

    user_id = ""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.cdp = FakeCdp(FakeSB.user_id)

    def __enter__(self) -> FakeSB:
        EVENTS.append(f"sb_enter:{Path(self.kwargs['user_data_dir']).name}")
        return self

    def __exit__(self, *exc: Any) -> bool:
        lock = pg.profile_lock_path(Path(self.kwargs["user_data_dir"]))
        try:
            with fb.run_lock(lock):
                EVENTS.append("sb_exit(verrou libre)")
        except fb.RunAlreadyActive:
            EVENTS.append("sb_exit(verrou tenu)")
        return False

    def sleep(self, seconds: float) -> None:
        pass


class PoolTestCase(unittest.TestCase):
    """Dossier temporaire ; chemins d'état de post_groups et de share redirigés ; faux Chrome."""

    def setUp(self) -> None:
        EVENTS.clear()
        FakeSB.user_id = ""
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "facebook_share"
        self.out.mkdir()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch.object(share, "OUTPUT_DIR", self.out),
            mock.patch.object(pg, "ACCOUNT_IDS_FILE", self.out / "share_account_ids.json"),
            mock.patch.object(pg, "ACCOUNT_BLOCK_FILE", self.out / "account_block.json"),
            mock.patch.object(pg, "LOCK_FILE", self.out / ".post_groups.lock"),
            mock.patch.object(pg, "DEBUG_DIR", self.out / "debug"),
            mock.patch.object(pg, "SB", FakeSB),
            mock.patch.object(fb, "_activate_cdp_mode", lambda sb: None),
            mock.patch.object(fb, "close_orphan_chromes", lambda profile_dir: EVENTS.append(f"open_profile:{profile_dir.name}")),
            mock.patch.object(fb, "_reset_profile_exit_state", lambda profile_dir: None),
            mock.patch.object(fb, "PROFILE_DIR", fb.PROFILE_DIR),
        ):
            stack.enter_context(patch)
        self.addCleanup(self._tmp.cleanup)

    def profile(self, name: str) -> Path:
        return self.tmp / name

    def lock_free(self, profile: Path) -> bool:
        try:
            with fb.run_lock(pg.profile_lock_path(profile)):
                return True
        except fb.RunAlreadyActive:
            return False

    def settings(self, code: str, profile: Path) -> pg.CountrySettings:
        country = COUNTRIES[code]
        return pg.CountrySettings(country=country, profile=profile, api_name=country.offers_api_name, per_post=40,
                                  min_offers=1, max_groups_per_day=0, groups_file=self.tmp / f"{code}.txt",
                                  state_path=self.out / f"group_posts_{code}.json")


class ProfileSessionTest(PoolTestCase):
    def test_lock_taken_before_open_and_released_after_close(self) -> None:
        profile = self.profile("prof_a")
        with pg.profile_session(profile, headless=True):
            EVENTS.append("inside")
        self.assertEqual(EVENTS, ["open_profile:prof_a", "sb_enter:prof_a", "inside", "sb_exit(verrou tenu)"])
        self.assertTrue(self.lock_free(profile))

    def test_lock_held_inside_session(self) -> None:
        profile = self.profile("prof_a")
        with pg.profile_session(profile, headless=True):
            self.assertFalse(self.lock_free(profile))

    def test_busy_profile_raises_profile_busy(self) -> None:
        profile = self.profile("prof_a")
        with fb.run_lock(pg.profile_lock_path(profile)):
            with self.assertRaises(pg.ProfileBusy) as ctx:
                with pg.profile_session(profile, headless=True):
                    EVENTS.append("inside")
        self.assertEqual(EVENTS, [])
        self.assertIn("prof_a", str(ctx.exception))
        self.assertEqual(ctx.exception.scope, "profile")

    def test_lock_released_after_exception(self) -> None:
        profile = self.profile("prof_a")
        with self.assertRaises(pg.AccountLimited):
            with pg.profile_session(profile, headless=True):
                raise pg.AccountLimited("Nous limitons le nombre de fois que vous pouvez publier")
        self.assertEqual(EVENTS[-1], "sb_exit(verrou tenu)")
        self.assertTrue(self.lock_free(profile))

    def test_publish_country_busy_profile(self) -> None:
        profile = self.profile("prof_a")
        args = argparse.Namespace(auto=True, headless=True)
        with mock.patch.object(pg, "post_to_group", side_effect=AssertionError("publication interdite")), \
                fb.run_lock(pg.profile_lock_path(profile)), redirect_stdout(StringIO()):
            with self.assertRaises(pg.StopRun):
                pg.publish_country(self.settings("madagascar", profile), [], {}, args, None, {})
        self.assertEqual(EVENTS, [])

    def test_login_only_skips_busy_profile(self) -> None:
        busy, free = self.profile("prof_busy"), self.profile("prof_free")
        out = StringIO()
        with mock.patch.object(sys.stdin, "isatty", lambda: True), mock.patch.object(fb, "is_logged_in", lambda sb: True), \
                fb.run_lock(pg.profile_lock_path(busy)), redirect_stdout(out):
            pg.login_only([self.settings("madagascar", busy), self.settings("cameroun", free)])
        self.assertIn("occupé", out.getvalue())
        self.assertIn("[OK] Profil prof_free déjà connecté.", out.getvalue())
        self.assertEqual([e for e in EVENTS if e.startswith("sb_enter")], ["sb_enter:prof_free"])

    def test_same_facebook_account_in_two_profiles_refused(self) -> None:  # Review Focus 2
        pg.ACCOUNT_IDS_FILE.write_text(json.dumps({"prof_a": "100"}), encoding="utf-8")
        FakeSB.user_id = "100"
        with self.assertRaises(pg.StopRun) as ctx:
            pg.check_account_identity("prof_b", FakeSB(user_data_dir=str(self.profile("prof_b"))))
        self.assertIn("même compte Facebook que le profil prof_a", str(ctx.exception))
        self.assertEqual(json.loads(pg.ACCOUNT_IDS_FILE.read_text(encoding="utf-8")), {"prof_a": "100"})

    def test_identity_recorded(self) -> None:
        FakeSB.user_id = "100"
        pg.check_account_identity("prof_a", FakeSB(user_data_dir=str(self.profile("prof_a"))))
        self.assertEqual(json.loads(pg.ACCOUNT_IDS_FILE.read_text(encoding="utf-8")), {"prof_a": "100"})
        FakeSB.user_id = ""
        pg.check_account_identity("prof_b", FakeSB(user_data_dir=str(self.profile("prof_b"))))  # pas de cookie : rien
        self.assertEqual(json.loads(pg.ACCOUNT_IDS_FILE.read_text(encoding="utf-8")), {"prof_a": "100"})


if __name__ == "__main__":
    unittest.main()

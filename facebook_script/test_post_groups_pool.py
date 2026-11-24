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
from contextlib import ExitStack, redirect_stderr, redirect_stdout
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


class ReadOnlySB:
    """Navigateur de vérification : seulement goto et sleep (tout autre attribut -> AttributeError : aucun clic possible)."""

    __slots__ = ("visited", "fail")

    def __init__(self, fail: bool = False) -> None:
        self.visited: list[str] = []
        self.fail = fail

    def goto(self, url: str) -> None:
        if self.fail:
            raise RuntimeError("page non chargée")
        self.visited.append(url)

    def sleep(self, seconds: float) -> None:
        pass


def forbid(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("clic ou saisie interdit pendant une vérification")


class MembershipStatusTest(PoolTestCase):
    def status(self, composer: dict, banner: str = "", group_id: dict | None = None, auth_wall: bool = False,
               fail: bool = False) -> tuple[pg.MembershipResult, list[str]]:
        diagnostics: list[str] = []

        def run_js(sb: Any, js: str, args: dict | None = None) -> Any:
            if js is pg.GROUP_ID_JS:
                return group_id or {}
            if js is pg.COMPOSER_JS:
                return composer
            if js is pg.RESTRICTION_JS:
                return {"text": banner}
            raise AssertionError("script inattendu")

        with mock.patch.object(fb, "_run_js", run_js), mock.patch.object(fb, "_page_state", lambda sb: {}), \
                mock.patch.object(fb, "_is_auth_wall", lambda state: auth_wall), \
                mock.patch.object(pg, "COMPOSER_TIMEOUT", 0.05), mock.patch.object(pg, "PAGE_LOAD_WAIT", 0), \
                mock.patch.object(pg, "save_diagnostics", lambda sb, url, reason: diagnostics.append(reason)), \
                mock.patch.object(pg, "_click", forbid), mock.patch.object(pg, "_press", forbid), \
                mock.patch.object(pg, "_type_lines", forbid), mock.patch.object(pg, "fill_composer", forbid):
            return pg.membership_status(ReadOnlySB(fail), "https://www.facebook.com/groups/g1/"), diagnostics

    def test_composer_is_member(self) -> None:
        result, _ = self.status({"join": True, "x": 10, "y": 20})
        self.assertEqual((result.status, result.detail), ("member", "composer"))

    def test_open_dialog_is_member(self) -> None:
        self.assertEqual(self.status({"open": True})[0].status, "member")

    def test_join_only_is_not_member(self) -> None:
        result, diagnostics = self.status({"join": True})
        self.assertEqual((result.status, result.detail), ("not_member", "join_button"))
        self.assertEqual(len(diagnostics), 1)

    def test_nothing_is_unknown(self) -> None:
        result, diagnostics = self.status({})
        self.assertEqual((result.status, result.detail), ("unknown", "no_composer"))
        self.assertEqual(len(diagnostics), 1)

    def test_banner_is_member_with_restriction(self) -> None:
        result, _ = self.status({}, banner="Votre profil a été suspendu dans ce groupe jusqu'au 5 nov 2026, 06:47")
        self.assertEqual((result.status, result.detail, result.restriction.reason), ("member", "restriction", "suspended"))

    def test_auth_wall_stops(self) -> None:
        with self.assertRaises(pg.StopRun):
            self.status({}, auth_wall=True)

    def test_load_error_is_unknown(self) -> None:
        result, _ = self.status({"x": 1, "y": 1}, fail=True)
        self.assertEqual((result.status, result.detail), ("unknown", "load_error"))

    def test_numeric_id_recorded(self) -> None:
        result, _ = self.status({"x": 1, "y": 1}, group_id={"id": "506", "source": "meta"})
        self.assertEqual(result.numeric_id, "506")


def ago(days: float) -> str:
    return (pg.datetime.now().astimezone() - pg.timedelta(days=days)).isoformat(timespec="seconds")


class CheckMembershipsTest(PoolTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.membership_file = self.out / "share_membership.json"
        for patch in (mock.patch.object(pg, "MEMBERSHIP_FILE", self.membership_file),
                      mock.patch.object(pg, "MEMBERSHIP_LOCK", self.out / ".share_membership.lock"),
                      mock.patch.object(fb, "is_logged_in", lambda sb: True)):
            patch.start()
            self.addCleanup(patch.stop)
        self.targets = {"madagascar": [fb.GroupTarget(f"g{i}", f"https://www.facebook.com/groups/g{i}/") for i in range(1, 5)]}
        self.all_settings = {"madagascar": self.settings("madagascar", self.profile("historique"))}
        self.visits: list[tuple[str, str]] = []

    def account(self, name: str, active: bool = True) -> Any:
        return pg.account_pool.Account(name, self.profile(name), name, active, 0)

    def fake_status(self, results: dict[str, pg.MembershipResult] | None = None, interrupt_at: int = 0) -> Any:
        def status(sb: Any, url: str) -> pg.MembershipResult:
            if interrupt_at and len(self.visits) + 1 == interrupt_at:
                raise KeyboardInterrupt
            self.visits.append((fb.PROFILE_DIR.name, url))
            return (results or {}).get(url, pg.MembershipResult("member", "composer", "123"))
        return mock.patch.object(pg, "membership_status", status)

    def args(self, **kw: Any) -> argparse.Namespace:
        return argparse.Namespace(**{"account": [], "recheck": False, "max_groups": 0, "headless": True,
                                     "delay_min": 0, "delay_max": 0, **kw})

    def check(self, accounts: list[Any], **kw: Any) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = pg.check_memberships(accounts, self.targets, self.all_settings, self.args(**kw))
        return code, out.getvalue()

    def test_visits_only_needed_groups(self) -> None:
        self.membership_file.write_text(json.dumps({"A": {
            "madagascar/g1": {"status": "member", "checked_at": ago(10)},
            "madagascar/g2": {"status": "not_member", "checked_at": ago(3)},
            "madagascar/g3": {"status": "not_member", "checked_at": ago(8)}}}), encoding="utf-8")
        with self.fake_status():
            code, _ = self.check([self.account("A")])
        self.assertEqual(code, 0)
        self.assertEqual([url for _, url in self.visits],
                         ["https://www.facebook.com/groups/g3/", "https://www.facebook.com/groups/g4/"])

    def test_recheck_and_max_groups(self) -> None:
        self.membership_file.write_text(json.dumps({"A": {"madagascar/g1": {"status": "member", "checked_at": ago(1)}}}),
                                        encoding="utf-8")
        with self.fake_status():
            self.check([self.account("A")], recheck=True)
        self.assertEqual(len(self.visits), 4)
        self.visits.clear()
        with self.fake_status():
            self.check([self.account("A")], recheck=True, max_groups=2)
        self.assertEqual(len(self.visits), 2)

    def test_entry_fields_written(self) -> None:
        restriction = pg.GroupRestricted("pending_limit", pg.datetime(2026, 12, 4, 8, 0).astimezone(), "limite")
        results = {"https://www.facebook.com/groups/g2/": pg.MembershipResult("member", "restriction", "77", restriction)}
        with self.fake_status(results):
            self.check([self.account("A")], max_groups=2)
        saved = json.loads(self.membership_file.read_text(encoding="utf-8"))["A"]
        self.assertEqual(saved["madagascar/g1"]["status"], "member")
        self.assertEqual(saved["madagascar/g1"]["numeric_id"], "123")
        self.assertEqual(saved["madagascar/g1"]["url"], "https://www.facebook.com/groups/g1/")
        self.assertIn("checked_at", saved["madagascar/g1"])
        self.assertEqual(saved["madagascar/g2"]["restriction"]["reason"], "pending_limit")
        self.assertTrue(saved["madagascar/g2"]["restriction"]["until"].startswith("2026-12-04T08:00"))

    def test_inactive_account_can_be_checked(self) -> None:
        with self.fake_status():
            self.check([self.account("A", active=False)])
        self.assertEqual(len(self.visits), 4)

    def test_only_chosen_accounts(self) -> None:
        with self.fake_status():
            self.check([self.account("A"), self.account("B")], account=["B"])
        self.assertEqual({profile for profile, _ in self.visits}, {"B"})

    def test_group_posts_untouched(self) -> None:
        state = self.all_settings["madagascar"].state_path
        state.write_text(json.dumps({"g1": {"history": [{"at": ago(1), "status": "posted", "profile": "A"}]}}),
                         encoding="utf-8")
        before = state.read_bytes()
        with self.fake_status():
            self.check([self.account("A"), self.account("B")])
        self.assertEqual(state.read_bytes(), before)
        self.assertNotIn(("A", "https://www.facebook.com/groups/g1/"), self.visits)  # membre prouvé par la publication

    def test_busy_profile_skipped(self) -> None:
        with self.fake_status(), fb.run_lock(pg.profile_lock_path(self.profile("A"))):
            _, out = self.check([self.account("A"), self.account("B")])
        self.assertIn("occupé", out)
        self.assertEqual({profile for profile, _ in self.visits}, {"B"})

    def test_ctrl_c_keeps_checked_groups(self) -> None:  # Review Focus 4
        with self.fake_status(interrupt_at=3), self.assertRaises(KeyboardInterrupt):
            self.check([self.account("A")])
        saved = json.loads(self.membership_file.read_text(encoding="utf-8"))
        self.assertEqual(sorted(saved["A"]), ["madagascar/g1", "madagascar/g2"])
        self.assertTrue(self.lock_free(self.profile("A")))

    def test_check_stops_on_invalid_membership_file(self) -> None:  # Review Focus 3
        self.membership_file.write_text('{"A": ', encoding="utf-8")
        with self.fake_status():
            code, out = self.check([self.account("A")])
        self.assertEqual(code, 1)
        self.assertIn("share_membership.json", out)
        self.assertEqual(self.membership_file.read_text(encoding="utf-8"), '{"A": ')
        self.assertEqual(EVENTS, [])  # aucun Chrome

    def test_save_membership_keeps_other_profiles(self) -> None:
        self.membership_file.write_text(json.dumps({"B": {"madagascar/g1": {"status": "member"}}}), encoding="utf-8")
        pg.save_membership("A", {"madagascar/g1": {"status": "not_member"}})
        self.assertEqual(json.loads(self.membership_file.read_text(encoding="utf-8")),
                         {"B": {"madagascar/g1": {"status": "member"}}, "A": {"madagascar/g1": {"status": "not_member"}}})


class CliTest(PoolTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.accounts_file = self.tmp / "share_accounts.json"
        self.groups = self.tmp / "groups_madagascar.txt"
        self.groups.write_text("https://www.facebook.com/groups/g1/\nhttps://www.facebook.com/groups/g2/\n", encoding="utf-8")
        for patch in (mock.patch.object(pg, "active_countries", lambda *kinds: [COUNTRIES["madagascar"]]),
                      mock.patch.object(pg, "list_file", lambda country, kind: self.groups if country.code == "madagascar"
                                        else self.tmp / f"absent_{country.code}.txt"),
                      mock.patch.object(pg, "ACCOUNTS_FILE", self.accounts_file),
                      mock.patch.object(pg, "MEMBERSHIP_FILE", self.out / "share_membership.json"),
                      mock.patch.object(pg, "MEMBERSHIP_LOCK", self.out / ".share_membership.lock")):
            patch.start()
            self.addCleanup(patch.stop)

    def parse(self, *argv: str) -> argparse.Namespace:
        with mock.patch.object(sys, "argv", ["post_groups.py", *argv]):
            return pg.parse_args()

    def main(self, *argv: str) -> tuple[int, str]:
        out, code = StringIO(), 0
        with mock.patch.object(sys, "argv", ["post_groups.py", *argv]), redirect_stdout(out), redirect_stderr(StringIO()):
            try:
                pg.main()
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue()

    def test_delay_defaults(self) -> None:
        args = self.parse("--check-membership")
        self.assertEqual((args.delay_min, args.delay_max), (20, 40))
        args = self.parse()
        self.assertEqual((args.delay_min, args.delay_max), (55, 65))
        args = self.parse("--check-membership", "--delay-min", "5", "--delay-max", "6")
        self.assertEqual((args.delay_min, args.delay_max), (5, 6))

    def test_check_and_summary_default_to_all_countries(self) -> None:
        self.assertEqual([c.code for c in self.parse("--check-membership").country_list], ["madagascar"])
        self.assertEqual([c.code for c in self.parse("--membership-summary").country_list], ["madagascar"])

    def test_option_conflicts(self) -> None:
        for argv in (["--check-membership", "--preview"], ["--membership-summary", "--login"],
                     ["--check-membership", "--membership-summary"], ["--account", "X", "--profile", "Y"]):
            with self.subTest(argv=argv), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                self.parse(*argv)

    def test_unknown_account_rejected(self) -> None:
        self.accounts_file.write_text(json.dumps({"accounts": [{"profile": "A"}]}), encoding="utf-8")
        code, out = self.main("--membership-summary", "--account", "ZZ")
        self.assertEqual(code, 1)
        self.assertIn("ZZ", out)

    def test_invalid_accounts_file_stops(self) -> None:  # Review Focus 1 (même pour l'ancien fonctionnement)
        self.accounts_file.write_text('{"accounts": [,]}', encoding="utf-8")
        code, out = self.main("--preview", "--no-images")
        self.assertEqual(code, 1)
        self.assertIn("share_accounts.json illisible (ligne 1", out)

    def test_summary_prints_table(self) -> None:
        self.accounts_file.write_text(json.dumps({"accounts": [{"profile": "A", "active": True}]}), encoding="utf-8")
        with mock.patch.object(pg, "SB", side_effect=AssertionError("aucun Chrome pour le résumé")):
            code, out = self.main("--membership-summary")
        self.assertEqual(code, 0)
        self.assertIn("Madagascar (2)", out)
        self.assertIn("0 membre · 0 non membre · 2 ?", out)

    def test_summary_without_accounts(self) -> None:
        code, out = self.main("--membership-summary")
        self.assertEqual(code, 0)
        self.assertIn("aucun compte", out)


if __name__ == "__main__":
    unittest.main()

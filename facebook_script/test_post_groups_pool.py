"""
Pool de comptes dans post_groups.py avec un faux navigateur : verrou de profil, identité du compte, vérification des
adhésions (lecture seule), publication en mode pool. Aucun accès réseau ni Facebook.

    python -m unittest discover -s facebook_script -p "test_post_groups_pool.py" -v
"""
from __future__ import annotations

import argparse
import json
import re
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


class JoinAssistTest(CheckMembershipsTest):
    """--join-assist : groupes non confirmés ouverts un par un ; l'utilisateur rejoint lui-même, puis ENTRÉE."""

    def assist(self, answers: list[str], accounts: list[Any] | None = None, tty: bool = True, **kw: Any) -> tuple[int, str]:
        self.opened: list[tuple[str, str]] = []
        replies = iter(answers)
        out = StringIO()
        with mock.patch.object(FakeSB, "goto", lambda sb, url: self.opened.append((fb.PROFILE_DIR.name, url)), create=True), \
                mock.patch.object(sys.stdin, "isatty", lambda: tty), \
                mock.patch("builtins.input", lambda prompt="": next(replies)), \
                mock.patch.object(pg, "_click", forbid), mock.patch.object(pg, "_press", forbid), \
                mock.patch.object(pg, "_type_lines", forbid), mock.patch.object(pg, "fill_composer", forbid), \
                mock.patch.object(fb, "login_and_wait", forbid), redirect_stdout(out):
            code = pg.join_assist(accounts or [self.account("A", active=False)], self.targets, self.all_settings,
                                  self.args(account=["A"], **kw))
        return code, out.getvalue()

    def test_opens_only_unconfirmed_groups(self) -> None:
        self.membership_file.write_text(json.dumps({"A": {
            "madagascar/g1": {"status": "member", "checked_at": ago(1)},
            "madagascar/g2": {"status": "not_member", "checked_at": ago(1)}}}), encoding="utf-8")
        with self.fake_status():
            self.assist(["", "", ""])
        self.assertEqual([url for _, url in self.opened], [f"https://www.facebook.com/groups/g{i}/" for i in (2, 3, 4)])

    def test_rechecks_and_saves_after_enter(self) -> None:
        with self.fake_status():
            self.assist(["", "", "", ""])
        saved = json.loads(self.membership_file.read_text(encoding="utf-8"))["A"]
        self.assertEqual(sorted(saved), [f"madagascar/g{i}" for i in range(1, 5)])
        self.assertEqual(saved["madagascar/g1"]["status"], "member")
        self.assertEqual(len(self.visits), 4)  # une revérification (lecture seule) par groupe

    def test_skip_and_quit(self) -> None:
        with self.fake_status():
            code, _ = self.assist(["s", "", "q"])
        self.assertEqual(code, 0)
        saved = json.loads(self.membership_file.read_text(encoding="utf-8"))["A"]
        self.assertEqual(sorted(saved), ["madagascar/g2"])  # g1 passé, g3 : arrêt
        self.assertEqual(len(self.opened), 3)  # g4 jamais ouvert
        self.assertTrue(self.lock_free(self.profile("A")))

    def test_requires_terminal(self) -> None:
        with self.fake_status():
            code, out = self.assist([], tty=False)
        self.assertEqual(code, 1)
        self.assertIn("terminal", out)
        self.assertEqual(EVENTS, [])

    def test_never_clicks(self) -> None:  # _click, _press, _type_lines, fill_composer, login_and_wait lèvent si appelés
        with self.fake_status():
            code, _ = self.assist(["", "", "", ""])
        self.assertEqual(code, 0)


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

    def test_join_assist_options(self) -> None:
        args = self.parse("--join-assist", "--account", "A")
        self.assertEqual([c.code for c in args.country_list], ["madagascar"])  # tous les pays qui ont des groupes
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            self.parse("--join-assist")  # --account obligatoire

    def test_option_conflicts(self) -> None:
        for argv in (["--check-membership", "--preview"], ["--membership-summary", "--login"],
                     ["--join-assist", "--account", "A", "--check-membership"],
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


UPLOAD_RED_TEXT = "Impossible de télécharger vos fichiers :"  # sous « Publier », dans la fenêtre de rédaction
UPLOAD_DIALOG = ("Impossible d’importer les photos\nDésolé, aucune nouvelle photo n’a pu être téléchargée. "
                 "Veuillez réessayer.\nFermer")  # boîte de dialogue vue le 25/11/2026


class PhotoUploadTest(unittest.TestCase):
    """Affiches refusées par Facebook (« Impossible de télécharger vos fichiers », 25/11/2026)."""

    def publish(self, before: list[str], after: list[str], open_after: bool = True) -> tuple[Any, list[str]]:
        events: list[str] = []

        def run_js(sb: Any, js: str, args: dict | None = None) -> Any:
            if js is pg.NOTICES_JS:
                clicked = "click" in events
                return {"open": open_after if clicked else True, "notices": after if clicked else before}
            if js is pg.PUBLISH_JS:
                return {"open": True, "x": 10, "y": 20}
            if js is pg.PENDING_COUNT_JS:
                return {"count": 0}
            raise AssertionError("script inattendu")

        with mock.patch.object(fb, "_run_js", run_js), \
                mock.patch.object(pg, "_click", lambda sb, x, y: events.append("click")), \
                mock.patch.object(pg, "save_diagnostics", lambda sb, url, reason: events.append(f"diag:{reason}")), \
                mock.patch.object(pg, "discard_composer", lambda sb: events.append("discard")), \
                mock.patch.object(pg, "NOTICE_WAIT", 0), mock.patch.object(pg, "PUBLISH_IMAGES_TIMEOUT", 0.2), \
                mock.patch.object(pg, "IMAGE_UPLOAD_TIMEOUT", 0.2):
            try:
                result: Any = pg.publish(FakeSB(), "https://www.facebook.com/groups/g1/", True, with_images=True,
                                         on_publishing=lambda: events.append("publishing"))
            except Exception as exc:  # noqa: BLE001 - le test regarde l'exception levée
                result = exc
        return result, events

    def test_upload_error_before_click_nothing_published(self) -> None:
        result, events = self.publish([UPLOAD_RED_TEXT], [])
        self.assertIsInstance(result, pg.PhotoUploadFailed)
        self.assertNotIn("click", events)  # jamais « Publier » avec des affiches refusées
        self.assertNotIn("publishing", events)

    def test_upload_error_after_click_composer_still_open(self) -> None:
        result, events = self.publish([], [UPLOAD_DIALOG, UPLOAD_RED_TEXT])
        self.assertIsInstance(result, pg.PhotoUploadFailed)
        self.assertEqual(events[:2], ["publishing", "click"])

    def test_upload_error_is_not_a_block(self) -> None:  # ni StopRun ni arrêt du profil
        result, _ = self.publish([], [UPLOAD_DIALOG])
        self.assertNotIsInstance(result, pg.StopRun)

    def test_block_after_click_captured_before_discard(self) -> None:
        result, events = self.publish([], ["Vous ne pouvez pas publier pour le moment. Réessayez plus tard."])
        self.assertIsInstance(result, pg.StopRun)
        self.assertLess(events.index("diag:block"), events.index("discard"))  # capture AVANT de vider la fenêtre

    def test_question_after_published_post_is_not_a_block(self) -> None:
        """
        Bug du 25/11/2026 (offresdemploimadagascar, 14 h 03 et 14 h 12) : publication faite (fenêtre de rédaction
        fermée), puis une question de Facebook contenant un mot de BLOCK_RE -> StopRun, publication jamais enregistrée,
        mêmes offres republiées au lancement suivant.
        """
        question = "Règles du groupe\nPas de spam ni de publicité. Acceptez-vous de respecter ces règles ?\nOui"
        result, events = self.publish([], [question], open_after=False)
        self.assertEqual(result, "posted")  # publiée : enregistrée, offres marquées
        self.assertIn("diag:block", events)  # message et capture gardés pour vérification
        self.assertNotIn("discard", events)

    def test_upload_error_patterns(self) -> None:
        for text in (UPLOAD_RED_TEXT, UPLOAD_DIALOG, "Couldn't upload your files", "Unable to upload photos"):
            self.assertTrue(pg.UPLOAD_ERROR_RE.search(share._fold(text)), text)
        for text in ("Date limite : 30/11", "Votre publication est en attente d'approbation"):
            self.assertFalse(pg.UPLOAD_ERROR_RE.search(share._fold(text)), text)

    def post(self, outcomes: list[Any]) -> tuple[str, list[Any]]:
        calls: list[Any] = []

        def fake_publish(sb: Any, url: str, auto: bool, with_images: bool = False, on_publishing: Any = None) -> str:
            calls.append(("publish", with_images))
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with mock.patch.object(pg, "open_composer", lambda sb, url, on_group_id=None: calls.append(("open", on_group_id)) or "open"), \
                mock.patch.object(pg, "set_anonymous", lambda sb: "on"), \
                mock.patch.object(pg, "fill_composer", lambda sb, text: True), \
                mock.patch.object(pg, "attach_images", lambda sb, images: calls.append(("attach", len(images))) or len(images)), \
                mock.patch.object(pg, "publish", fake_publish), \
                mock.patch.object(pg, "discard_composer", lambda sb: calls.append("discard")), \
                mock.patch.object(pg, "save_diagnostics", lambda sb, url, reason: calls.append(f"diag:{reason}")), \
                redirect_stdout(StringIO()):
            status = pg.post_to_group(FakeSB(), "https://www.facebook.com/groups/g1/", "texte", True,
                                      [Path("a.png"), Path("b.png")], on_group_id="id-callback")
        return status, calls

    def test_post_to_group_retries_once_without_images(self) -> None:
        status, calls = self.post([pg.PhotoUploadFailed("refusées"), "posted"])
        self.assertEqual(status, "posted")
        self.assertEqual([c for c in calls if isinstance(c, tuple)],
                         [("open", "id-callback"), ("attach", 2), ("publish", True), ("open", None), ("publish", False)])
        self.assertIn("discard", calls)
        self.assertIn("diag:photo_upload", calls)

    def test_retry_failure_is_reported_not_looped(self) -> None:
        status, calls = self.post([pg.PhotoUploadFailed("refusées"), pg.PhotoUploadFailed("encore")])
        self.assertEqual(status, "upload_failed")
        self.assertEqual(sum(1 for c in calls if c == ("open", None)), 1)  # un seul nouvel essai


class SameGroupTwoAddressesTest(unittest.TestCase):
    """
    Bug du 25/11/2026 : recrutement.emploi.madagascar (ligne 7) et 414326263961327 (ligne 8) sont le même groupe.
    L'adresse 8 publie à 13 h 42 ; au lancement suivant, la ligne 7 devient la ligne « gardée » (id numérique connu) et
    ses protections (20 h, offres déjà publiées, blocage) étaient lues sur SA ligne, vide : republication tentée à 14 h 07.
    """

    A, B = "recrutement.emploi.madagascar", "414326263961327"

    def plan(self, last_post_hours_ago: float, blocked_on_b: bool = False) -> tuple[list[Any], set[str]]:
        offers = json.loads((DATA_DIR / "offers.json").read_text(encoding="utf-8"))
        offers = [o for o in offers if o["companyCountry"] == "Madagascar"]
        today = pg.date.today()
        now = pg.datetime.now().astimezone()
        at = (now - pg.timedelta(hours=last_post_hours_ago)).isoformat(timespec="seconds")
        first_40 = share.select_offers(offers, {}, set(), 1, today, False)[0][:40]
        state: dict[str, Any] = {
            self.A: {"url": f"https://www.facebook.com/groups/{self.A}/", "posted": {}, "numeric_id": self.B,
                     "history": [{"at": at, "status": "duplicate_group"}]},
            self.B: {"url": f"https://www.facebook.com/groups/{self.B}/", "last_post_at": at, "numeric_id": self.B,
                     "posted": {o["id"]: pg._posted_entry(o, share.DEFAULT_SITE_URL, at) for o in first_40},
                     "history": [{"at": at, "status": "pending"}]},
        }
        if blocked_on_b:
            state[self.B]["blocked"] = {"reason": "suspended", "until": (now + pg.timedelta(days=2)).isoformat(),
                                        "since": at, "message": "suspendu"}
        targets = [fb.GroupTarget(self.A, f"https://www.facebook.com/groups/{self.A}/"),
                   fb.GroupTarget(self.B, f"https://www.facebook.com/groups/{self.B}/")]
        claims, _ = pg.build_claims({"madagascar": targets}, {"madagascar": state})
        country = COUNTRIES["madagascar"]
        settings = pg.CountrySettings(country=country, profile=Path("p"), api_name="Madagascar", per_post=40,
                                      min_offers=1, max_groups_per_day=0, groups_file=Path("g.txt"),
                                      state_path=Path("s.json"))
        args = argparse.Namespace(excluded_owners_file=str(DATA_DIR / "excluded_owners.txt"), min_days_left=1,
                                  min_hours=20, max_groups=0, retry_blocked=False, single_link=False,
                                  site_url=share.DEFAULT_SITE_URL)
        with redirect_stdout(StringIO()):
            jobs = pg.plan(targets, offers, state, settings, args, today, claims)
        return jobs, {o["id"] for o in first_40}

    def test_kept_line_flips_but_20h_rule_still_applies(self) -> None:
        jobs, _ = self.plan(last_post_hours_ago=1)
        self.assertEqual([job.target.group_id for job in jobs], [])  # publié il y a 1 h sous l'autre adresse

    def test_offers_posted_under_other_address_never_reposted(self) -> None:
        jobs, already = self.plan(last_post_hours_ago=30)
        self.assertEqual([job.target.group_id for job in jobs], [self.A])
        self.assertFalse({o["id"] for o in jobs[0].offers} & already)
        self.assertEqual(len(jobs[0].offers), 5)  # 45 offres - 40 déjà publiées dans ce groupe

    def test_block_on_other_address_applies(self) -> None:
        jobs, _ = self.plan(last_post_hours_ago=30, blocked_on_b=True)
        self.assertEqual(jobs, [])


DATA_DIR = Path(__file__).resolve().parent / "test_data" / "pool_legacy"
DATA = Path(__file__).resolve().parent / "test_data" / "pool_legacy"
POOL_COUNTRIES = ("madagascar", "cameroun", "ghana")
EN_HEADERS = ("JOB OFFERS", "job openings", "Looking for a job", "Now hiring")


class FixedDate(pg.date):
    @classmethod
    def today(cls) -> FixedDate:
        return cls.fromordinal(pg.datetime.now().date().toordinal())  # « aujourd'hui » réel : quotas du jour cohérents


class PoolRunTest(PoolTestCase):
    """Mode pool de bout en bout : vraies fonctions plan / run / record, faux navigateur et faux post_to_group."""

    def setUp(self) -> None:
        super().setUp()
        self.accounts_file = self.tmp / "share_accounts.json"
        self.membership_file = self.out / "share_membership.json"
        self.calls: list[dict[str, Any]] = []
        self.script: dict[tuple[str, str], Any] = {}  # (profil, groupe) -> statut ou exception
        self.limit_at: dict[str, int] = {}  # profil -> n° de son appel qui lève AccountLimited
        self.on_call: Any = None
        self.failing_countries: set[str] = set()
        files = {code: DATA / f"groups_{code}.txt" for code in POOL_COUNTRIES}
        for patch in (
            mock.patch.object(share, "fetch_offers", self.fake_fetch),
            mock.patch.object(pg, "post_to_group", self.fake_post),
            mock.patch.object(pg, "list_file", lambda country, kind: files.get(country.code, self.tmp / f"absent_{country.code}")),
            mock.patch.object(pg, "active_countries", lambda *kinds: [COUNTRIES[c] for c in POOL_COUNTRIES]),
            mock.patch.object(pg, "MEMBERSHIP_FILE", self.membership_file),
            mock.patch.object(pg, "MEMBERSHIP_LOCK", self.out / ".share_membership.lock"),
            mock.patch.object(pg, "date", FixedDate),
            mock.patch.object(fb, "is_logged_in", lambda sb: True),
            # profils par défaut des pays : mêmes noms, mais dans le dossier temporaire (jamais à la racine du projet)
            mock.patch.object(pg, "default_profile", lambda country: self.tmp / (
                pg.PROFILE_DIR.name if country.code == "madagascar" else f"{pg.PROFILE_DIR.name}_{country.code}")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    # -- faux services -----------------------------------------------------------------------------------------------
    def fake_fetch(self, api_url: str, country: str) -> tuple[list[dict[str, Any]], int]:
        if country in self.failing_countries:
            raise share.ShareError("502 Bad Gateway")
        offers = [o for o in json.loads((DATA / "offers.json").read_text(encoding="utf-8")) if o["companyCountry"] == country]
        return offers, len(offers)

    def fake_post(self, sb: Any, group_url: str, text: str, auto: bool, images: Any = None, anonymous: bool = True,
                  on_group_id: Any = None, on_publishing: Any = None) -> str:
        profile = fb.PROFILE_DIR.name
        group = group_url.rstrip("/").rsplit("/", 1)[-1]
        mine = sum(1 for c in self.calls if c["profile"] == profile) + 1
        self.calls.append({"profile": profile, "group": group, "text": text})
        if self.on_call:
            self.on_call(profile, group)
        if self.limit_at.get(profile) == mine:
            raise pg.AccountLimited("Nous limitons le nombre de fois que vous pouvez publier")
        outcome = self.script.get((profile, group), "posted")
        if isinstance(outcome, BaseException):
            if isinstance(outcome, RuntimeError) and on_publishing:
                on_publishing()  # plantage APRÈS le clic sur « Publier »
            raise outcome
        if outcome in ("posted", "pending") and on_publishing:
            on_publishing()
        return outcome

    # -- données ---------------------------------------------------------------------------------------------------
    def accounts(self, *names: str, quotas: dict[str, int] | None = None, inactive: tuple[str, ...] = ()) -> None:
        entries = []
        for name in names:
            self.profile(name).mkdir(exist_ok=True)
            entry: dict[str, Any] = {"profile": str(self.profile(name)), "active": name not in inactive}
            if quotas and name in quotas:
                entry["max_groups_per_day"] = quotas[name]
            entries.append(entry)
        self.accounts_file.write_text(json.dumps({"accounts": entries}), encoding="utf-8")

    def members(self, names: tuple[str, ...], codes: tuple[str, ...] = POOL_COUNTRIES, extra: dict | None = None) -> None:
        checked = (pg.datetime.now().astimezone() - pg.timedelta(days=1)).isoformat(timespec="seconds")
        data = {name: {f"{code}/test{code}{n}": {"status": "member", "checked_at": checked, "detail": "composer"}
                       for code in codes for n in range(1, 5)} for name in names}
        for name, entries in (extra or {}).items():
            data.setdefault(name, {}).update(entries)
        self.membership_file.write_text(json.dumps(data), encoding="utf-8")

    def main(self, *extra: str, countries: str | None = ",".join(POOL_COUNTRIES)) -> tuple[int, str]:
        argv = ["--settings-file", str(DATA / "settings.json"),
                "--excluded-owners-file", str(DATA / "excluded_owners.txt"), "--no-images",
                "--accounts-file", str(self.accounts_file), "--delay-min", "0", "--delay-max", "0", *extra]
        if countries:
            argv = ["--countries", countries, *argv]
        out, code = StringIO(), 0
        with mock.patch.object(sys, "argv", ["post_groups.py", *argv]), redirect_stdout(out):
            try:
                pg.main()
            except SystemExit as exc:
                code = exc.code or 0
        return code, out.getvalue()

    def state(self, code: str) -> dict[str, Any]:
        path = self.out / f"group_posts_{code}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def groups_of(self, profile: str) -> list[str]:
        return [c["group"] for c in self.calls if c["profile"] == profile]

    @staticmethod
    def preview_groups(out: str) -> dict[str, list[str]]:
        """Aperçu du pool : {profil: groupes prévus}, d'après les en-têtes « Compte … (profil X) » et les adresses."""
        found: dict[str, list[str]] = {}
        profile = None
        for line in out.splitlines():
            header = re.search(r"^  Compte .*\(profil (\w+)\)", line)
            if header:
                profile = header.group(1)
                found[profile] = []
            group = re.match(r"^https://www\.facebook\.com/groups/(\w+)/ \(", line)
            if group and profile:
                found[profile].append(group.group(1))
        return found

    # -- tests -----------------------------------------------------------------------------------------------------
    def test_limited_account_groups_not_transferred(self) -> None:
        self.accounts("A", "B", "C")
        self.members(("A", "B", "C"))
        self.limit_at["A"] = 3  # 3e groupe de A : au Cameroun, après 2 groupes de Madagascar
        code, out = self.main()
        self.assertEqual(self.groups_of("A"), ["testmadagascar1", "testmadagascar4", "testcameroun3"])
        self.assertEqual(sorted(self.groups_of("B")), sorted(["testmadagascar2", "testcameroun1", "testcameroun4", "testghana3"]))
        self.assertEqual(sorted(self.groups_of("C")), sorted(["testmadagascar3", "testcameroun2", "testghana1", "testghana4"]))
        self.assertNotIn("testghana2", [c["group"] for c in self.calls])  # groupe restant de A : jamais transféré
        self.assertNotIn("testghana2", self.state("ghana"))
        self.assertIn("A", json.loads(pg.ACCOUNT_BLOCK_FILE.read_text(encoding="utf-8")))
        self.assertIn("2 groupe(s) laissé(s) pour le cycle suivant", out)
        # cycle suivant : A en pause, ses groupes non publiés passent en tête et vont à B ou C
        self.calls.clear()
        code, out = self.main("--preview")
        self.assertIn("A (pause jusqu'au", out)
        planned = self.preview_groups(out)
        self.assertNotIn("A", planned)
        self.assertEqual(sorted(g for groups in planned.values() for g in groups), ["testcameroun3", "testghana2"])

    def test_one_chrome_per_account_multi_country(self) -> None:
        self.accounts("A", "B", "C")
        self.members(("A", "B", "C"))
        self.main()
        opened = [e for e in EVENTS if e.startswith("sb_enter")]
        self.assertEqual(sorted(opened), ["sb_enter:A", "sb_enter:B", "sb_enter:C"])
        for code in POOL_COUNTRIES:
            for group in self.state(code).values():
                self.assertIn(group["history"][-1]["profile"], ("A", "B", "C"))
        a_countries = {c["group"].removeprefix("test").rstrip("1234") for c in self.calls if c["profile"] == "A"}
        self.assertEqual(a_countries, set(POOL_COUNTRIES))
        ghana_text = next(c["text"] for c in self.calls if c["group"].startswith("testghana"))
        self.assertTrue(any(h in ghana_text.splitlines()[0] for h in EN_HEADERS))

    def test_lock_held_between_run_calls(self) -> None:
        self.accounts("A", "B", "C")
        self.members(("A", "B", "C"))
        seen: list[tuple[str, bool]] = []
        self.on_call = lambda profile, group: seen.append((group, self.lock_free(self.profile(profile))))
        self.main()
        self.assertTrue(seen)
        self.assertFalse(any(free for _, free in seen))
        self.assertTrue(all(self.lock_free(self.profile(p)) for p in "ABC"))

    def test_busy_profile_skipped_not_redistributed(self) -> None:
        self.accounts("A", "B", "C")
        self.members(("A", "B", "C"))
        with fb.run_lock(pg.profile_lock_path(self.profile("B"))):
            code, out = self.main()
        self.assertEqual(self.groups_of("B"), [])
        self.assertEqual(len(self.calls), 8)  # 4 pour A, 4 pour C : les 4 de B ne sont pas redistribués
        self.assertIn("occupé", out)

    def test_not_member_no_retry_same_cycle(self) -> None:
        self.accounts("A", "B")
        self.members(("A", "B"))
        self.script[("A", "testmadagascar1")] = "not_member"
        self.main()
        self.assertEqual([c["profile"] for c in self.calls if c["group"] == "testmadagascar1"], ["A"])
        code, out = self.main("--preview")
        self.assertEqual(self.preview_groups(out), {"B": ["testmadagascar1"]})  # cycle suivant : B, plus jamais A

    def test_suspended_blocks_group_for_all(self) -> None:
        self.accounts("A", "B")
        self.members(("A", "B"))
        until = pg.datetime.now().astimezone() + pg.timedelta(days=2)
        self.script[("A", "testmadagascar1")] = pg.GroupRestricted("suspended", until, "Votre profil a été suspendu")
        self.main()
        self.assertEqual(self.state("madagascar")["testmadagascar1"]["blocked"]["reason"], "suspended")
        self.calls.clear()
        code, out = self.main("--preview")
        self.assertNotIn("groups/testmadagascar1/", out)

    def test_crash_then_restart(self) -> None:
        self.accounts("A")
        self.members(("A",))
        self.script[("A", "testmadagascar1")] = RuntimeError("plantage de Chrome")
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertIn("erreur inattendue", out)
        self.assertIn("publishing", self.state("madagascar")["testmadagascar1"])
        self.calls.clear()
        self.script.clear()
        self.main()
        self.assertNotIn("testmadagascar1", [c["group"] for c in self.calls])
        self.assertEqual(self.state("madagascar")["testmadagascar1"]["history"][-1]["status"], "unknown")

    def test_unexpected_error_stops_only_that_account(self) -> None:
        self.accounts("A", "B", "C")
        self.members(("A", "B", "C"))
        self.script[("A", "testmadagascar1")] = TimeoutError("CDP sans réponse")  # comme fb.CdpTimeout
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertEqual(self.groups_of("A"), ["testmadagascar1"])  # A arrêté, ses autres groupes non transférés
        self.assertEqual(len(self.groups_of("B")), 4)
        self.assertEqual(len(self.groups_of("C")), 4)
        self.assertTrue(self.lock_free(self.profile("A")))
        self.assertIn("A (erreur inattendue", out)

    def test_account_option_without_pool_refused(self) -> None:
        self.accounts("A", inactive=("A",))
        for extra in ((), ("--preview",)):
            with self.subTest(extra=extra):
                code, out = self.main("--account", "A", *extra)
                self.assertEqual(code, 1)
                self.assertIn("pool désactivé", out)
        self.assertEqual((self.calls, EVENTS), ([], []))

    def test_quota_counts_all_countries(self) -> None:
        self.accounts("A", quotas={"A": 3})
        self.members(("A",))
        today = pg.datetime.now().astimezone().isoformat(timespec="seconds")
        (self.out / "group_posts_ghana.json").write_text(json.dumps({"autre": {"url": "u", "posted": {}, "history": [
            {"at": today, "status": "posted", "profile": "A"}, {"at": today, "status": "pending", "profile": "A"}]}}),
            encoding="utf-8")
        self.main()
        self.assertEqual(len(self.calls), 1)

    def test_offers_api_failure_one_country(self) -> None:  # Review Focus 5
        self.accounts("A", "B")
        self.members(("A", "B"))
        self.failing_countries.add("Cameroun")
        code, out = self.main()
        self.assertEqual(code, 1)
        countries = {c["group"].removeprefix("test").rstrip("1234") for c in self.calls}
        self.assertEqual(countries, {"madagascar", "ghana"})
        self.assertIn("API du pays illisible", out)

    def test_pool_stops_on_invalid_membership_file(self) -> None:  # Review Focus 3
        self.accounts("A")
        self.membership_file.write_text('{"A": ', encoding="utf-8")
        code, out = self.main()
        self.assertEqual(code, 1)
        self.assertEqual(self.membership_file.read_text(encoding="utf-8"), '{"A": ')
        self.assertEqual(EVENTS, [])
        self.assertEqual(self.calls, [])

    def test_preview_writes_nothing(self) -> None:
        self.accounts("A", "B")
        self.members(("A", "B"))
        before = {p.name: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        code, out = self.main("--preview")
        after = {p.name: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual((self.calls, EVENTS), ([], []))
        self.assertIn("Total", out)

    def test_max_groups_by_priority(self) -> None:
        self.accounts("A", "B")
        self.members(("A", "B"))
        self.main("--max-groups", "1")
        self.assertEqual(sorted(c["group"] for c in self.calls), ["testcameroun1", "testghana1", "testmadagascar1"])

    def test_check_restriction_respected(self) -> None:
        self.accounts("A", "B")
        until = (pg.datetime.now().astimezone() + pg.timedelta(days=2)).isoformat(timespec="seconds")
        self.members(("A", "B"), extra={"B": {"madagascar/testmadagascar1": {
            "status": "member", "checked_at": pg.datetime.now().astimezone().isoformat(timespec="seconds"),
            "detail": "restriction", "restriction": {"reason": "pending_limit", "until": until}}}})
        self.main()
        self.assertNotIn("testmadagascar1", [c["group"] for c in self.calls])
        self.calls.clear()
        for code in POOL_COUNTRIES:
            (self.out / f"group_posts_{code}.json").unlink(missing_ok=True)
        self.main("--retry-blocked")
        self.assertIn("testmadagascar1", [c["group"] for c in self.calls])

    def test_unknown_members_get_nothing(self) -> None:
        self.accounts("A", "B")
        self.members(("A",), codes=("madagascar",))
        code, out = self.main()
        self.assertEqual({c["profile"] for c in self.calls}, {"A"})
        self.assertEqual(len(self.calls), 4)
        self.assertIn("aucun compte membre confirmé", out)

    def test_profile_option_forces_legacy(self) -> None:
        self.accounts("A")
        self.members(("A",))
        with mock.patch.object(pg, "run_countries", return_value=0) as legacy, \
                mock.patch.object(pg, "run_pool", side_effect=AssertionError("pas de pool avec --profile")):
            self.main("--country", "madagascar", "--profile", str(self.profile("legacy")), countries=None)
        legacy.assert_called_once()

    def test_warns_historic_profile_missing(self) -> None:
        self.accounts("A")
        self.members(("A",))
        code, out = self.main("--preview")
        self.assertIn("[WARN] Profil historique my_custom_profile_facebook_share", out)


if __name__ == "__main__":
    unittest.main()

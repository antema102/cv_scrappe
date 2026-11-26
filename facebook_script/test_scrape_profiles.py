"""
Profils Chrome et comptes Facebook du scraping, un par pays (scrape_profiles.py + pages.py).

Aucun accès réseau ni Facebook, aucun vrai profil : Chrome est remplacé par un faux navigateur, tous les fichiers
sont dans un dossier temporaire.

    python -m unittest discover -s facebook_script -p "test_scrape_profiles.py" -v
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import pages
import post_groups
import scrape_profiles
import scraper as fb
from countries import COUNTRIES

MDG, CMR, TUN = COUNTRIES["madagascar"], COUNTRIES["cameroun"], COUNTRIES["tunisie"]


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


class MappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def load(self, data, share=frozenset()):
        return scrape_profiles.load_mapping(_write(self.tmp / "scrape_profiles.json", data), set(share))

    def test_missing_file_keeps_everyone_on_the_historical_profile(self) -> None:
        mapping = scrape_profiles.load_mapping(self.tmp / "absent.json", set())
        self.assertEqual(mapping, {})
        self.assertEqual(scrape_profiles.profile_for(CMR, mapping).name, scrape_profiles.DEFAULT_PROFILE)

    def test_codes_names_and_iso3_accepted(self) -> None:
        mapping = self.load({"countries": {"Cameroun": "prof_cmr", "TUN": "prof_tun", "madagascar": "prof_mdg"}})
        self.assertEqual(mapping, {"cameroun": "prof_cmr", "tunisie": "prof_tun", "madagascar": "prof_mdg"})
        self.assertEqual(scrape_profiles.profile_for(CMR, mapping), scrape_profiles.PROJECT_ROOT / "prof_cmr")

    def test_publication_profiles_refused(self) -> None:
        share = {"compte_pool", "my_custom_profile_facebook_share_3"}  # déclarés dans les réglages de post_groups.py
        for name in ("compte_pool", "My_Custom_Profile_Facebook_Share_3",
                     "my_custom_profile_facebook_share", "my_custom_profile_facebook_share_cameroun"):  # noms d'office
            with self.subTest(name=name), self.assertRaises(scrape_profiles.ProfileConfigError):
                self.load({"countries": {"cameroun": name}}, share=share)

    def test_former_publication_profile_accepted_once_out_of_settings(self) -> None:
        mapping = self.load({"countries": {"senegal": "my_custom_profile_facebook_share_10"}},
                            share={"my_custom_profile_facebook_share_2"})
        self.assertEqual(mapping, {"senegal": "my_custom_profile_facebook_share_10"})

    def test_invalid_files_refused_never_guessed(self) -> None:
        cases = {
            "json illisible": "{pas du json",
            "countries absent": {"countries": ["cameroun"]},
            "pays inconnu": {"countries": {"atlantide": "p"}},
            "pays en double": {"countries": {"cameroun": "a", "CMR": "b"}},
            "chemin": {"countries": {"cameroun": "../autre/profil"}},
            "vide": {"countries": {"cameroun": "  "}},
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(scrape_profiles.ProfileConfigError):
                self.load(data)

    def test_share_profiles_read_from_post_groups_settings(self) -> None:
        accounts = _write(self.tmp / "accounts.json", {"accounts": [{"profile": "Compte_A"}, {"profile": "compte_b"}]})
        countries = _write(self.tmp / "countries.json", {"countries": {"cameroun": {"profile": "compte_c"}}})
        self.assertEqual(scrape_profiles.share_profiles((accounts, countries, self.tmp / "absent.json")),
                         {"compte_a", "compte_b", "compte_c"})

    def test_repository_settings_are_valid(self) -> None:  # le fichier livré ne bloque aucun cycle
        scrape_profiles.load_mapping()

    def test_shared_profiles_listed(self) -> None:
        root = scrape_profiles.PROJECT_ROOT
        shared = scrape_profiles.shared_profiles({"madagascar": root / "p", "maurice": root / "p", "cameroun": root / "q"})
        self.assertEqual(shared, {"p": ["madagascar", "maurice"]})

    def test_same_lock_as_post_groups(self) -> None:  # un profil ouvert par l'un est vu occupé par l'autre
        profile = scrape_profiles.PROJECT_ROOT / "my_custom_profile_facebook"
        self.assertEqual(scrape_profiles.profile_lock_path(profile), post_groups.profile_lock_path(profile))


class BindAccountTest(unittest.TestCase):
    def bind(self, data, profile, code, user, accept=False):
        return scrape_profiles.bind_account(data, profile, code, user, accept, "2026-11-26T19:00:00+03:00")

    def test_first_run_records_account_and_country(self) -> None:
        data = self.bind({}, "prof_cmr", "cameroun", "111")
        self.assertEqual(data["profiles"]["prof_cmr"]["c_user"], "111")
        self.assertEqual(data["profiles"]["prof_cmr"]["countries"], ["cameroun"])

    def test_shared_profile_accumulates_countries(self) -> None:
        data = self.bind(self.bind({}, "prof", "madagascar", "111"), "prof", "maurice", "111")
        self.assertEqual(data["profiles"]["prof"]["countries"], ["madagascar", "maurice"])

    def test_same_account_in_two_profiles_refused(self) -> None:
        data = self.bind({}, "prof_mdg", "madagascar", "111")
        with self.assertRaisesRegex(scrape_profiles.AccountMismatch, "prof_mdg"):
            self.bind(data, "prof_cmr", "cameroun", "111")
        with self.assertRaises(scrape_profiles.AccountMismatch):  # --accept-account ne mélange jamais deux profils
            self.bind(data, "prof_cmr", "cameroun", "111", accept=True)

    def test_changed_account_refused_unless_accepted(self) -> None:
        data = self.bind({}, "prof_cmr", "cameroun", "111")
        with self.assertRaisesRegex(scrape_profiles.AccountMismatch, "111 -> 222"):
            self.bind(data, "prof_cmr", "cameroun", "222")
        accepted = self.bind(data, "prof_cmr", "cameroun", "222", accept=True)
        self.assertEqual(accepted["profiles"]["prof_cmr"], {"c_user": "222", "countries": ["cameroun"],
                                                             "updated_at": "2026-11-26T19:00:00+03:00"})
        self.assertEqual(data["profiles"]["prof_cmr"]["c_user"], "111")  # entrée non modifiée sur place

    def test_share_account_owners(self) -> None:
        ids = {"share_2": "111", "share_10": "111", "share_3": "333"}
        self.assertEqual(scrape_profiles.share_account_owners(ids, "111"), ["share_2", "share_10"])
        self.assertEqual(scrape_profiles.share_account_owners(ids, "999"), [])


class FakeSB:
    """Faux Chrome : enregistre le profil ouvert ; le compte connecté dépend du profil (ACCOUNTS)."""
    ACCOUNTS: dict[str, str] = {}
    OPENED: list[str] = []

    def __init__(self, **kwargs) -> None:
        self.profile = Path(kwargs["user_data_dir"]).name
        FakeSB.OPENED.append(kwargs["user_data_dir"])

    def __enter__(self) -> "FakeSB":
        return self

    def __exit__(self, *exc) -> None:
        return None


class PagesProfileTest(unittest.TestCase):
    """pages.run_scraping : chaque pays ouvre son profil, jamais un profil occupé, absent ou d'un autre compte."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        FakeSB.OPENED = []
        FakeSB.ACCOUNTS = {}
        patches = [
            mock.patch.object(fb, "OUTPUT_DIR", self.tmp / "out"),
            mock.patch.object(fb, "PROFILE_DIR", fb.PROFILE_DIR),
            mock.patch.object(scrape_profiles, "SHARE_ACCOUNT_IDS_FILE", self.tmp / "share_account_ids.json"),
            mock.patch.object(pages, "SB", FakeSB),
            mock.patch.object(fb, "close_orphan_chromes", lambda profile: None),
            mock.patch.object(fb, "_reset_profile_exit_state", lambda profile: None),
            mock.patch.object(fb, "_activate_cdp_mode", lambda sb: None),
            mock.patch.object(fb, "is_logged_in", lambda sb: True),
            mock.patch.object(fb, "facebook_user_id", lambda sb: FakeSB.ACCOUNTS.get(sb.profile, "")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def profile(self, name: str, account: str = "", create: bool = True) -> Path:
        path = self.tmp / name
        if create:
            path.mkdir(exist_ok=True)
        if account:
            FakeSB.ACCOUNTS[name] = account
        return path

    def scrape(self, country, profile: Path, accept: bool = False) -> tuple[bool, str]:
        fb.PROFILE_DIR = profile
        args = argparse.Namespace(headless=True, login=False, login_only=True, accept_account=accept)
        out = StringIO()
        with redirect_stdout(out):
            ok = pages.run_scraping([], [], args, country)
        return ok, out.getvalue()

    def accounts(self) -> dict:
        return json.loads((self.tmp / "out" / "facebook_pages" / "scrape_accounts.json").read_text(encoding="utf-8"))

    def test_each_country_opens_only_its_own_profile(self) -> None:
        self.assertTrue(self.scrape(CMR, self.profile("prof_cmr", "111"))[0])
        self.assertTrue(self.scrape(TUN, self.profile("prof_tun", "222"))[0])
        self.assertEqual([Path(p).name for p in FakeSB.OPENED], ["prof_cmr", "prof_tun"])
        profiles = self.accounts()["profiles"]
        self.assertEqual((profiles["prof_cmr"]["c_user"], profiles["prof_cmr"]["countries"]), ("111", ["cameroun"]))
        self.assertEqual((profiles["prof_tun"]["c_user"], profiles["prof_tun"]["countries"]), ("222", ["tunisie"]))

    def test_missing_profile_never_created_in_automatic_mode(self) -> None:
        missing = self.profile("prof_absent", create=False)
        ok, out = self.scrape(CMR, missing)
        self.assertFalse(ok)
        self.assertIn("introuvable", out)
        self.assertEqual(FakeSB.OPENED, [])
        self.assertFalse(missing.exists())

    def test_profile_already_open_is_skipped(self) -> None:
        profile = self.profile("prof_cmr", "111")
        with fb.run_lock(scrape_profiles.profile_lock_path(profile, self.tmp / "out" / "facebook_share")):
            ok, out = self.scrape(CMR, profile)
        self.assertFalse(ok)
        self.assertIn("déjà ouvert", out)
        self.assertEqual(FakeSB.OPENED, [])
        self.assertTrue(self.scrape(CMR, profile)[0])  # verrou relâché : le lancement suivant passe

    def test_same_account_in_two_profiles_stops_the_second_country(self) -> None:
        self.assertTrue(self.scrape(MDG, self.profile("prof_mdg", "111"))[0])
        ok, out = self.scrape(CMR, self.profile("prof_cmr", "111"))
        self.assertFalse(ok)
        self.assertIn("déjà celui du profil prof_mdg", out)
        self.assertNotIn("prof_cmr", self.accounts()["profiles"])

    def test_changed_account_needs_accept_account(self) -> None:
        profile = self.profile("prof_cmr", "111")
        self.assertTrue(self.scrape(CMR, profile)[0])
        FakeSB.ACCOUNTS["prof_cmr"] = "333"
        ok, out = self.scrape(CMR, profile)
        self.assertFalse(ok)
        self.assertIn("a changé", out)
        self.assertTrue(self.scrape(CMR, profile, accept=True)[0])
        self.assertEqual(self.accounts()["profiles"]["prof_cmr"]["c_user"], "333")

    def publication_settings(self, *profiles: str) -> None:
        accounts = _write(self.tmp / "share_accounts.json", {"accounts": [{"profile": p, "active": True} for p in profiles]})
        patch = mock.patch.object(scrape_profiles, "SHARE_CONFIG_FILES", (accounts, self.tmp / "absent.json"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_account_still_publishing_is_refused_and_not_recorded(self) -> None:
        self.publication_settings("my_custom_profile_facebook_share_2")
        _write(self.tmp / "share_account_ids.json", {"my_custom_profile_facebook_share_2": "111"})
        ok, out = self.scrape(CMR, self.profile("prof_cmr", "111"))
        self.assertFalse(ok)
        self.assertIn("publie encore", out)
        # rien enregistré : sinon post_groups.py refuserait ensuite ce compte pour publier
        self.assertFalse((self.tmp / "out" / "facebook_pages" / "scrape_accounts.json").exists())

    def test_former_publication_account_accepted(self) -> None:
        self.publication_settings("my_custom_profile_facebook_share_2")  # share_10 retiré des réglages
        _write(self.tmp / "share_account_ids.json", {"my_custom_profile_facebook_share_10": "777"})
        ok, out = self.scrape(MDG, self.profile("my_custom_profile_facebook_share_10", "777"))
        self.assertTrue(ok)
        self.assertIn("ancien compte de publication", out)
        self.assertEqual(self.accounts()["profiles"]["my_custom_profile_facebook_share_10"]["c_user"], "777")

    def test_profile_resolution(self) -> None:
        root = scrape_profiles.PROJECT_ROOT
        mapping = {"cameroun": "prof_cmr"}
        self.assertEqual(pages.scrape_profile(argparse.Namespace(profile=None), CMR, mapping), root / "prof_cmr")
        self.assertEqual(pages.scrape_profile(argparse.Namespace(profile=None), MDG, mapping),
                         root / scrape_profiles.DEFAULT_PROFILE)
        self.assertEqual(pages.scrape_profile(argparse.Namespace(profile="autre"), CMR, mapping), root / "autre")


class PublicationGuardTest(unittest.TestCase):
    """post_groups.py n'ouvre jamais un profil de scraping et ne publie jamais avec un compte de scraping."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        config = _write(self.tmp / "scrape_profiles.json",
                        {"countries": {"senegal": "my_custom_profile_facebook_share_10"}})
        patch = mock.patch.object(scrape_profiles, "CONFIG_FILE", config)
        patch.start()
        self.addCleanup(patch.stop)

    def test_scraping_profiles_read_without_ever_failing(self) -> None:
        self.assertEqual(scrape_profiles.scraping_profiles(),
                         {"my_custom_profile_facebook", "my_custom_profile_facebook_share_10"})
        broken = _write(self.tmp / "casse.json", "{pas du json")
        self.assertEqual(scrape_profiles.scraping_profiles(broken), {"my_custom_profile_facebook"})

    def test_profile_session_refuses_scraping_profiles_before_any_chrome(self) -> None:
        with mock.patch.object(post_groups, "SB", side_effect=AssertionError("Chrome ouvert")), \
                mock.patch.object(post_groups, "open_profile", side_effect=AssertionError("profil touché")):
            for name in ("my_custom_profile_facebook_share_10", "my_custom_profile_facebook"):
                with self.subTest(name=name), self.assertRaisesRegex(post_groups.StopRun, "réservé au scraping"):
                    with post_groups.profile_session(self.tmp / name, headless=True):
                        pass

    def test_pool_skips_scraping_profile_and_keeps_the_others(self) -> None:
        accounts = []
        for name in ("my_custom_profile_facebook_share_2", "my_custom_profile_facebook_share_10"):
            (self.tmp / name).mkdir()
            accounts.append(post_groups.account_pool.Account(name, self.tmp / name, name, True, 20))
        args = argparse.Namespace(account=[], retry_blocked=False)
        with mock.patch.object(post_groups, "posts_today", lambda *a: 0), \
                mock.patch.object(post_groups, "_account_block_until", lambda profile: None):
            active, caps, inactive = post_groups.active_accounts(accounts, {}, args, None)
        self.assertEqual([a.profile for a in active], ["my_custom_profile_facebook_share_2"])
        self.assertIn("scraping", inactive["my_custom_profile_facebook_share_10"])

    def test_publication_refuses_an_account_registered_for_scraping(self) -> None:
        scrape_accounts = _write(self.tmp / "scrape_accounts.json",
                                 {"profiles": {"my_custom_profile_facebook": {"c_user": "555"}}})
        with mock.patch.object(post_groups, "SCRAPE_ACCOUNTS_FILE", scrape_accounts), \
                mock.patch.object(post_groups, "ACCOUNT_IDS_FILE", self.tmp / "share_account_ids.json"), \
                mock.patch.object(post_groups, "facebook_user_id", lambda sb: "555"):
            with self.assertRaisesRegex(post_groups.StopRun, "enregistré pour le scraping"):
                post_groups.check_account_identity("my_custom_profile_facebook_share_4", object())
        self.assertFalse((self.tmp / "share_account_ids.json").exists())  # rien enregistré côté publication


class OcrThreadsTest(unittest.TestCase):
    def engine_params(self, env: dict[str, str]) -> dict:
        captured = {}
        fake = types.ModuleType("rapidocr")
        fake.RapidOCR = lambda params: captured.update(params) or object()
        with mock.patch.dict(sys.modules, {"rapidocr": fake}), mock.patch.dict("os.environ", env, clear=False), \
                mock.patch.object(fb, "_ocr_engine", None), mock.patch.object(fb, "_ocr_missing", False):
            fb._get_ocr_engine()
        return captured

    def test_limit_from_run_daily(self) -> None:
        params = self.engine_params({"FB_OCR_THREADS": "3"})
        self.assertEqual(params["EngineConfig.onnxruntime.intra_op_num_threads"], 3)
        self.assertEqual(params["EngineConfig.onnxruntime.inter_op_num_threads"], 1)

    def test_no_limit_by_default(self) -> None:  # lancement manuel : comportement d'avant (tous les cœurs)
        for value in ("", "0", "abc"):
            with self.subTest(value=value):
                self.assertEqual(self.engine_params({"FB_OCR_THREADS": value}), {"Global.log_level": "error"})


if __name__ == "__main__":
    unittest.main()

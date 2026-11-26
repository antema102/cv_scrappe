"""
Inventaire des profils (profile_inventory.py) : lecture seule, verdicts de la répartition proposée.
Aucun vrai profil ni Facebook : bases de cookies fabriquées, faux navigateur, dossiers temporaires.

    python -m unittest discover -s facebook_script -p "test_profile_inventory.py" -v
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import post_groups as pg
import profile_inventory as inv
import scraper as fb
from profile_inventory import BLOCK, INFO, OK, TODO, ProfileRow

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _chrome_us(moment: datetime) -> int:
    return int((moment - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000)


def _profile(root: Path, name: str, cookies: dict[str, datetime] | None) -> Path:
    path = root / name
    (path / "Default" / "Network").mkdir(parents=True)
    if cookies is not None:
        db = sqlite3.connect(path / "Default" / "Network" / "Cookies")
        db.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER, encrypted_value BLOB)")
        for cookie, expires in cookies.items():
            db.execute("INSERT INTO cookies VALUES ('.facebook.com', ?, ?, x'00')", (cookie, _chrome_us(expires)))
        db.commit()
        db.close()
    return path


class CookieSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.tmp = Path(tempfile.mkdtemp())

    def state(self, cookies) -> str:
        return inv.cookie_session(_profile(self.root, f"p{len(list(self.root.iterdir()))}", cookies), self.tmp, NOW)

    def test_states(self) -> None:
        later, earlier = NOW + timedelta(days=300), NOW - timedelta(days=1)
        self.assertTrue(self.state({"c_user": later, "xs": later}).startswith("cookie valide"))
        self.assertTrue(self.state({"c_user": later}).startswith("déconnectée (aucun cookie"))
        self.assertIn("expiré", self.state({"c_user": later, "xs": earlier}))
        self.assertTrue(self.state(None).startswith("aucune session"))

    def test_original_untouched_and_copy_removed(self) -> None:
        path = _profile(self.root, "p", {"c_user": NOW + timedelta(days=9), "xs": NOW + timedelta(days=9)})
        cookies = path / "Default" / "Network" / "Cookies"
        before = (cookies.stat().st_mtime_ns, cookies.read_bytes())
        inv.cookie_session(path, self.tmp, NOW)
        self.assertEqual((cookies.stat().st_mtime_ns, cookies.read_bytes()), before)
        self.assertEqual(list(self.tmp.iterdir()), [])


class LockProbeTest(unittest.TestCase):
    def test_probe_never_creates_and_sees_held_lock(self) -> None:
        path = Path(tempfile.mkdtemp()) / ".profile_x.lock"
        self.assertFalse(inv.lock_held(path))
        self.assertFalse(path.exists())  # sondé sans créer le fichier
        with fb.run_lock(path):
            self.assertTrue(inv.lock_held(path))
        self.assertFalse(inv.lock_held(path))
        with fb.run_lock(path):  # la sonde a bien relâché le verrou
            pass


class ProposalTest(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(inv.parse_proposal("Cameroun=a, MDG=b"), {"cameroun": "a", "madagascar": "b"})
        for bad in ("cameroun", "cameroun=", "atlantide=a", "cameroun=a,CMR=b", "cameroun=../a"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                inv.parse_proposal(bad)

    def inventory(self, *rows: ProfileRow, coverage=None) -> dict:
        return {"rows": list(rows), "coverage": coverage or {},
                "groups": {"cameroun": ["g1", "g2", "g3"], "maurice": ["m1"], "madagascar": ["a"]}}

    def levels(self, checks) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for level, message in checks:
            result.setdefault(level, []).append(message)
        return result

    def test_missing_profile_is_blocking_never_associated(self) -> None:
        verdicts = inv.evaluate_proposal({"tunisie": "my_custom_profile_facebook_tunisie"}, self.inventory())
        self.assertEqual(verdicts["tunisie"][0][0], BLOCK)

    def test_active_publication_account_must_leave_pool(self) -> None:
        row = ProfileRow("my_custom_profile_facebook_share_12", True, ["publication (pool, actif)"], pool="actif",
                         session="cookie valide (à confirmer avec --live)")
        found = self.levels(inv.evaluate_proposal({"maurice": row.name}, self.inventory(row))["maurice"])
        self.assertTrue(any("retirer" in m for m in found[TODO]))
        self.assertTrue(any("inconnu" in m for m in found[TODO]))
        self.assertNotIn(BLOCK, found)  # nom accepté une fois retiré du pool

    def test_post_groups_default_name_is_blocking(self) -> None:
        row = ProfileRow("my_custom_profile_facebook_share_maurice", True, session="cookie valide")
        found = self.levels(inv.evaluate_proposal({"maurice": row.name}, self.inventory(row))["maurice"])
        self.assertTrue(any("pris d'office" in m for m in found[BLOCK]))

    def test_same_account_in_two_profiles_is_blocking(self) -> None:
        a = ProfileRow("p_a", True, account_ids={"scrape_accounts.json": "111"}, session="cookie valide")
        b = ProfileRow("p_b", True, account_ids={"share_account_ids.json": "111"}, session="cookie valide")
        found = self.levels(inv.evaluate_proposal({"cameroun": "p_a"}, self.inventory(a, b))["cameroun"])
        self.assertTrue(any("p_b" in m for m in found[BLOCK]))

    def test_account_changed_live_is_blocking(self) -> None:
        row = ProfileRow("p", True, account_ids={"share_account_ids.json": "111"}, live={"session": "connectée",
                                                                                          "c_user": "222"})
        found = self.levels(inv.evaluate_proposal({"cameroun": "p"}, self.inventory(row))["cameroun"])
        self.assertTrue(any("plusieurs comptes" in m for m in found[BLOCK]))

    def test_same_profile_for_two_countries_is_blocking(self) -> None:
        row = ProfileRow("p", True, session="cookie valide")
        verdicts = inv.evaluate_proposal({"cameroun": "p", "maurice": "P"}, self.inventory(row))
        for code in ("cameroun", "maurice"):
            self.assertIn(BLOCK, [level for level, _ in verdicts[code]])

    def test_live_groups_list_private_ones_to_join_by_hand(self) -> None:
        groups = [{"id": "g1", "access": "membre"}, {"id": "g2", "access": "lisible sans être membre (groupe public)"},
                  {"id": "g3", "access": "non lisible (groupe privé, non membre)"}]
        row = ProfileRow("p", True, live={"session": "connectée", "c_user": "1", "groups": groups})
        found = self.levels(inv.evaluate_proposal({"cameroun": "p"}, self.inventory(row))["cameroun"])
        self.assertIn("groupes lisibles (--live) : 2/3", " ".join(found[TODO]))
        self.assertTrue(any("À LA MAIN : g3" in m for m in found[TODO]))
        self.assertIn("session : connectée (vérifiée --live)", found[OK])

    def test_skipped_live_reported(self) -> None:
        row = ProfileRow("p", True, busy="Chrome ouvert", live={"skipped": "Chrome ouvert : profil sauté"},
                         session="à vérifier")
        found = self.levels(inv.evaluate_proposal({"cameroun": "p"}, self.inventory(row))["cameroun"])
        self.assertTrue(any("sauté" in m for m in found[TODO]))
        self.assertTrue(any("Chrome ouvert" in m for m in found[INFO]))


class CollectTest(unittest.TestCase):
    def test_roles_accounts_and_coverage_from_files_only(self) -> None:
        root = Path(tempfile.mkdtemp())
        far = NOW + timedelta(days=3650)
        _profile(root, "my_custom_profile_facebook", {"c_user": far, "xs": far})
        _profile(root, "my_custom_profile_facebook_share_12", None)
        _profile(root, "my_custom_profile_3", None)
        cfg = root / "cfg"
        cfg.mkdir()
        files = {
            "scrape_profiles": cfg / "scrape_profiles.json",
            "share_accounts": cfg / "share_accounts.json",
            "share_countries": cfg / "share_countries.json",
            "share_account_ids": cfg / "share_account_ids.json",
            "share_membership": cfg / "share_membership.json",
            "account_block": cfg / "account_block.json",
            "scrape_accounts": cfg / "scrape_accounts.json",
        }
        files["share_accounts"].write_text(json.dumps({"accounts": [
            {"profile": "my_custom_profile_facebook_share_12", "active": True}]}), encoding="utf-8")
        files["share_account_ids"].write_text(json.dumps({"my_custom_profile_facebook_share_12": "555"}),
                                              encoding="utf-8")
        files["scrape_accounts"].write_text(json.dumps({"profiles": {"my_custom_profile_facebook": {"c_user": "444"}}}),
                                            encoding="utf-8")
        maurice = inv.group_ids(inv.find_country("maurice"))
        files["share_membership"].write_text(json.dumps({"my_custom_profile_facebook_share_12": {
            f"maurice/{g}": {"status": "member"} for g in maurice}}), encoding="utf-8")
        files["account_block"].write_text("{illisible", encoding="utf-8")
        result = inv.collect({"maurice": "my_custom_profile_facebook_share_12"}, root, files)
        rows = {r.name: r for r in result["rows"]}
        self.assertIn("scraping", rows["my_custom_profile_facebook"].roles)
        self.assertEqual(rows["my_custom_profile_facebook"].account_ids, {"scrape_accounts.json": "444"})
        self.assertTrue(rows["my_custom_profile_facebook"].session.startswith("cookie valide"))
        self.assertEqual(rows["my_custom_profile_facebook_share_12"].pool, "actif")
        self.assertEqual(rows["my_custom_profile_facebook_share_12"].account_ids, {"share_account_ids.json": "555"})
        self.assertIn("autre script", rows["my_custom_profile_3"].roles[0])
        self.assertEqual(result["coverage"]["my_custom_profile_facebook_share_12"]["maurice"],
                         [len(maurice), len(maurice)])
        self.assertTrue(any("account_block.json illisible" in p for p in result["problems"]))


class FakeSB:
    def goto(self, url: str) -> None:
        self.url = url

    def sleep(self, seconds: float) -> None:
        pass


class GroupAccessTest(unittest.TestCase):
    """Classement d'un groupe à partir de ce que la page montre ; jamais de clic (aucune méthode de clic appelée)."""

    def access(self, composer: dict, feed_posts: int, restriction=None) -> str:
        def run_js(sb, script, args=None):
            return {"posts": feed_posts, "feed": True} if script is inv.FEED_JS else composer

        with mock.patch.object(pg, "PAGE_LOAD_WAIT", 0), mock.patch.object(pg, "COMPOSER_TIMEOUT", 0), \
                mock.patch.object(fb, "_page_state", lambda sb: {"url": "https://www.facebook.com/groups/1/"}), \
                mock.patch.object(fb, "_run_js", run_js), mock.patch.object(pg, "detect_restriction",
                                                                            lambda sb: restriction):
            return inv.group_access(FakeSB(), "https://www.facebook.com/groups/1/")

    def test_classification(self) -> None:
        self.assertEqual(self.access({"x": 1, "y": 2}, 5), "membre")
        self.assertEqual(self.access({}, 4), "lisible sans être membre (groupe public)")
        self.assertEqual(self.access({"join": True}, 0), "non lisible (groupe privé, non membre)")
        self.assertTrue(self.access({}, 0).startswith("à vérifier"))
        self.assertEqual(self.access({}, 0, restriction=object()), "membre (restreint par les admins)")

    def test_login_page_stops_the_profile(self) -> None:
        with mock.patch.object(pg, "PAGE_LOAD_WAIT", 0), \
                mock.patch.object(fb, "_page_state", lambda sb: {"url": "https://www.facebook.com/login/"}), \
                self.assertRaises(inv.SessionLost):
            inv.group_access(FakeSB(), "https://www.facebook.com/groups/1/")

    def test_busy_or_missing_profile_skipped_without_chrome(self) -> None:
        with mock.patch("seleniumbase.SB", side_effect=AssertionError("Chrome ouvert")):
            busy = ProfileRow("p", True, busy="verrou tenu")
            inv.live_check(busy, Path(tempfile.mkdtemp()), ["g"], 0, (0, 0))
            self.assertIn("sauté", busy.live["skipped"])
            missing = ProfileRow("q", False)
            inv.live_check(missing, Path(tempfile.mkdtemp()), ["g"], 0, (0, 0))
            self.assertEqual(missing.live["skipped"], "profil absent")


if __name__ == "__main__":
    unittest.main()

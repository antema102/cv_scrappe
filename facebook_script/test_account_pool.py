"""
Tests de la logique pure du pool de comptes (account_pool.py) : aucun navigateur, aucun réseau.

    python -m unittest discover -s facebook_script -p "test_account_pool.py" -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import account_pool as ap

SCRIPT_DIR = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=3))


class AccountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.file = self.root / "share_accounts.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, data: object, encoding: str = "utf-8") -> Path:
        text = data if isinstance(data, str) else json.dumps(data)
        self.file.write_text(text, encoding=encoding)
        return self.file

    def test_missing_file_gives_empty_list(self) -> None:
        self.assertEqual(ap.load_accounts(self.root / "absent.json", self.root), [])

    def test_shipped_file_is_empty_and_pool_off(self) -> None:
        accounts = ap.load_accounts(SCRIPT_DIR / "share_accounts.json", self.root)
        self.assertEqual(accounts, [])
        self.assertFalse(ap.pool_enabled(accounts))

    def test_quota_priority(self) -> None:
        self.write({"defaults": {"max_groups_per_day": 10},
                    "accounts": [{"profile": "A", "active": True, "max_groups_per_day": 15},
                                 {"profile": "B", "active": True}]})
        self.assertEqual([a.quota for a in ap.load_accounts(self.file, self.root)], [15, 10])
        self.assertEqual([a.quota for a in ap.load_accounts(self.file, self.root, cli_quota=4)], [4, 4])
        self.assertEqual([a.quota for a in ap.load_accounts(self.file, self.root, cli_quota=0)], [0, 0])

    def test_default_quota_is_unlimited(self) -> None:
        self.write({"accounts": [{"profile": "A", "active": True}]})
        self.assertEqual(ap.load_accounts(self.file, self.root)[0].quota, 0)

    def test_quota_zero_means_unlimited(self) -> None:
        self.assertIsNone(ap.remaining_cap(0, 50))

    def test_remaining_cap(self) -> None:
        self.assertEqual(ap.remaining_cap(5, 3), 2)
        self.assertEqual(ap.remaining_cap(5, 7), 0)

    def test_fields(self) -> None:
        self.write({"accounts": [{"profile": "prof_a", "label": "Compte A", "active": False},
                                 {"profile": "prof_b"}]})
        a, b = ap.load_accounts(self.file, self.root)
        self.assertEqual((a.profile, a.label, a.active, a.path), ("prof_a", "Compte A", False, self.root / "prof_a"))
        self.assertEqual((b.label, b.active), ("prof_b", False))  # actif seulement si demandé

    def test_pool_enabled_only_with_active_account(self) -> None:
        self.write({"accounts": [{"profile": "A", "active": False}, {"profile": "B", "active": False}]})
        self.assertFalse(ap.pool_enabled(ap.load_accounts(self.file, self.root)))
        self.write({"accounts": [{"profile": "A", "active": False}, {"profile": "B", "active": True}]})
        self.assertTrue(ap.pool_enabled(ap.load_accounts(self.file, self.root)))

    def test_relative_profile_resolved_against_project_root(self) -> None:
        absolute = self.root / "ailleurs" / "prof_x"
        self.write({"accounts": [{"profile": "sub/prof_a"}, {"profile": str(absolute)}]})
        a, x = ap.load_accounts(self.file, self.root)
        self.assertEqual((a.path, a.profile), (self.root / "sub" / "prof_a", "prof_a"))
        self.assertEqual((x.path, x.profile), (absolute, "prof_x"))

    def test_bom_accepted(self) -> None:
        self.write({"accounts": [{"profile": "A"}]}, encoding="utf-8-sig")
        self.assertEqual(len(ap.load_accounts(self.file, self.root)), 1)

    def test_comment_keys_ignored(self) -> None:
        self.write({"_aide": ["..."], "accounts": [{"profile": "A", "_note": "x"}]})
        self.assertEqual(len(ap.load_accounts(self.file, self.root)), 1)

    def test_errors(self) -> None:
        cases = [
            ({"accounts": [{"profile": "A", "quota": 3}]}, "quota"),
            ({"defaults": {"max": 1}, "accounts": []}, "max"),
            ({"accounts": [{"profile": "p"}, {"profile": "./p"}]}, "p"),
            ({"accounts": [{"profile": "my_custom_profile_facebook"}]}, "my_custom_profile_facebook"),
            ({"accounts": [{"profile": "A", "max_groups_per_day": -1}]}, "-1"),
            ({"accounts": [{"profile": "A", "max_groups_per_day": "5"}]}, "5"),
            ({"accounts": [{"profile": "A", "max_groups_per_day": 2.5}]}, "2.5"),
            ({"accounts": [{"profile": "A", "max_groups_per_day": True}]}, "True"),
            ({"accounts": [{"label": "sans profil"}]}, "profile"),
            ({"accounts": [{"profile": "A", "active": "oui"}]}, "active"),
            ({"accounts": {"profile": "A"}}, "accounts"),
        ]
        for data, needle in cases:
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ap.AccountsError) as ctx:
                    ap.load_accounts(self.file, self.root)
                self.assertIn(needle, str(ctx.exception))
                self.assertIn("share_accounts.json", str(ctx.exception))
        self.write({"accounts": []})
        with self.assertRaises(ap.AccountsError):
            ap.load_accounts(self.file, self.root, cli_quota=-1)

    def test_invalid_json_names_file_and_line(self) -> None:  # Review Focus 1
        self.write('{"accounts": [,]}')
        with self.assertRaises(ap.AccountsError) as ctx:
            ap.load_accounts(self.file, self.root)
        self.assertIn("share_accounts.json", str(ctx.exception))
        self.assertIn("ligne 1", str(ctx.exception))


def at(day: int, hour: int = 10, month: int = 11) -> str:
    return datetime(2026, month, day, hour, 0, tzinfo=TZ).isoformat()


G = ap.group_key("madagascar", "g1")
NOW = datetime(2026, 12, 1, 8, 0, tzinfo=TZ)


def states_with(*entries: dict, blocked: dict | None = None) -> dict:
    group: dict = {"url": "https://www.facebook.com/groups/g1/", "posted": {}, "history": list(entries)}
    if blocked:
        group["blocked"] = blocked
    return {"madagascar": {"g1": group}}


class MembershipsTest(unittest.TestCase):
    def test_group_key(self) -> None:
        self.assertEqual(ap.group_key("madagascar", "506924959474464"), "madagascar/506924959474464")

    def test_history_posted_is_member(self) -> None:
        for status in ap.MEMBER_HISTORY_STATUSES:
            with self.subTest(status=status):
                m = ap.Memberships(states_with({"at": at(20), "status": status, "profile": "P"}), {}, {})
                self.assertEqual(m.status("P", G), ap.MEMBER)
                self.assertEqual(m.evidence("P", G).source, "history")

    def test_history_entry_without_profile_uses_country_profile(self) -> None:
        m = ap.Memberships(states_with({"at": at(20), "status": "posted"}), {"madagascar": "H"}, {})
        self.assertEqual(m.status("H", G), ap.MEMBER)
        self.assertEqual(m.status("P", G), ap.UNKNOWN)

    def test_check_unknown_is_not_evidence(self) -> None:
        m = ap.Memberships({}, {}, {"P": {G: {"status": "unknown", "checked_at": at(25), "detail": "no_composer"}}})
        self.assertEqual(m.status("P", G), ap.UNKNOWN)
        self.assertIsNone(m.evidence("P", G))

    def test_no_composer_or_fill_failed_is_not_evidence(self) -> None:
        for status in ("no_composer", "fill_failed", "not_published", "account_limited", "stopped", "skipped"):
            with self.subTest(status=status):
                m = ap.Memberships(states_with({"at": at(20), "status": status, "profile": "P"}), {}, {})
                self.assertEqual(m.status("P", G), ap.UNKNOWN)

    def test_history_not_member(self) -> None:
        m = ap.Memberships(states_with({"at": at(20), "status": "posted", "profile": "P"},
                                       {"at": at(22), "status": "not_member", "profile": "P"}), {}, {})
        self.assertEqual(m.status("P", G), ap.NOT_MEMBER)

    def test_most_recent_evidence_wins(self) -> None:
        check_not_member = {"P": {G: {"status": "not_member", "checked_at": at(25), "detail": "join_button"}}}
        m = ap.Memberships(states_with({"at": at(26), "status": "posted", "profile": "P"}), {}, check_not_member)
        self.assertEqual(m.status("P", G), ap.MEMBER)
        m = ap.Memberships(states_with({"at": at(24), "status": "posted", "profile": "P"}), {}, check_not_member)
        self.assertEqual(m.status("P", G), ap.NOT_MEMBER)
        m = ap.Memberships(states_with({"at": at(25), "status": "posted", "profile": "P"}), {}, check_not_member)
        self.assertEqual(m.status("P", G), ap.NOT_MEMBER)  # même instant : la vérification gagne

    def test_needs_check(self) -> None:
        def memberships(status: str, day: int) -> ap.Memberships:
            return ap.Memberships({}, {}, {"P": {G: {"status": status, "checked_at": at(day)}}})
        self.assertTrue(ap.Memberships({}, {}, {}).needs_check("P", G, NOW))
        self.assertFalse(memberships("member", 20).needs_check("P", G, NOW))
        self.assertFalse(memberships("not_member", 28).needs_check("P", G, NOW))
        self.assertTrue(memberships("not_member", 23).needs_check("P", G, NOW))
        self.assertTrue(memberships("unknown", 30).needs_check("P", G, NOW))
        self.assertTrue(memberships("member", 20).needs_check("P", G, NOW, recheck=True))

    def test_last_check(self) -> None:
        m = ap.Memberships({}, {}, {"P": {G: {"status": "unknown", "checked_at": at(25)},
                                          "madagascar/g2": {"status": "member", "checked_at": at(27)}}})
        self.assertEqual(m.last_check("P"), datetime.fromisoformat(at(27)))
        self.assertIsNone(m.last_check("Q"))

    def test_naive_dates_do_not_crash(self) -> None:
        m = ap.Memberships(states_with({"at": "2026-11-20T10:00:00", "status": "posted", "profile": "P"}), {},
                           {"P": {G: {"status": "not_member", "checked_at": at(25)}}})
        self.assertEqual(m.status("P", G), ap.NOT_MEMBER)


class RestrictionsTest(unittest.TestCase):
    def test_group_restrictions_from_publication(self) -> None:
        states = states_with({"at": at(28), "status": "suspended", "profile": "P"},
                             blocked={"reason": "suspended", "until": at(3, month=12), "since": at(28), "message": "x"})
        found = ap.group_restrictions(states, {}, NOW)
        self.assertEqual(list(found), [G])
        self.assertEqual((found[G].reason, found[G].source, found[G].seen_by), ("suspended", "publication", "P"))
        expired = states_with(blocked={"reason": "pending_limit", "until": at(30), "since": at(27), "message": "x"})
        self.assertEqual(ap.group_restrictions(expired, {}, NOW), {})

    def test_group_restrictions_from_check(self) -> None:
        membership = {"P": {G: {"status": "member", "checked_at": at(29), "detail": "restriction",
                                "restriction": {"reason": "pending_limit", "until": at(2, month=12)}}}}
        found = ap.group_restrictions({}, membership, NOW)
        self.assertEqual((found[G].reason, found[G].source, found[G].seen_by), ("pending_limit", "check", "P"))
        membership["P"][G]["restriction"]["until"] = at(30)
        self.assertEqual(ap.group_restrictions({}, membership, NOW), {})

    def test_account_level_statuses_never_restrict_group(self) -> None:
        states = states_with({"at": at(30), "status": "not_member", "profile": "P"},
                             {"at": at(30, 11), "status": "account_limited", "profile": "P"})
        self.assertEqual(ap.group_restrictions(states, {}, NOW), {})


class MembershipFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "share_membership.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(ap.load_membership(self.path), {})

    def test_load_membership_invalid_raises(self) -> None:  # Review Focus 3
        self.path.write_text('{"P": ', encoding="utf-8")
        with self.assertRaises(ap.MembershipFileError) as ctx:
            ap.load_membership(self.path)
        self.assertIn("share_membership.json", str(ctx.exception))
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"P": ')

    def test_merge_account_membership_keeps_other_profiles(self) -> None:
        data = {"P": {G: {"status": "member"}, "madagascar/g2": {"status": "unknown"}}, "Q": {G: {"status": "not_member"}}}
        merged = ap.merge_account_membership(data, "P", {"madagascar/g2": {"status": "member"}})
        self.assertEqual(merged["Q"], {G: {"status": "not_member"}})
        self.assertEqual(merged["P"], {G: {"status": "member"}, "madagascar/g2": {"status": "member"}})
        self.assertEqual(data["P"]["madagascar/g2"], {"status": "unknown"})  # original intact
        self.assertEqual(ap.merge_account_membership({}, "R", {G: {"status": "member"}}), {"R": {G: {"status": "member"}}})

    def test_account_id_conflict(self) -> None:
        self.assertEqual(ap.account_id_conflict({"A": "100"}, "B", "100"), "A")
        self.assertIsNone(ap.account_id_conflict({"A": "100"}, "A", "100"))
        self.assertIsNone(ap.account_id_conflict({"A": "100"}, "B", "200"))
        self.assertIsNone(ap.account_id_conflict({"A": ""}, "B", ""))


if __name__ == "__main__":
    unittest.main()

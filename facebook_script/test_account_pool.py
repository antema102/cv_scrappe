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


def groups_of(spec: list[tuple[str, int]]) -> list[str]:
    """[("madagascar", 30), ("senegal", 20)] -> ["madagascar/0", ..., "senegal/19"] dans cet ordre (= priorité)."""
    return [f"{code}/{n}" for code, count in spec for n in range(count)]


def per_country(allocation: ap.Allocation) -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = {}
    for key, profile in allocation.owner.items():
        country = key.split("/")[0]
        found.setdefault(profile, {}).setdefault(country, 0)
        found[profile][country] += 1
    return found


class AllocationTest(unittest.TestCase):
    def test_three_accounts_three_countries(self) -> None:
        groups = groups_of([("madagascar", 30), ("senegal", 20), ("cameroun", 10)])
        al = ap.allocate(groups, {g: ["A", "B", "C"] for g in groups}, {"A": None, "B": None, "C": None}, ["A", "B", "C"])
        self.assertEqual(al.loads, {"A": 20, "B": 20, "C": 20})
        self.assertEqual(per_country(al), {"A": {"madagascar": 10, "senegal": 7, "cameroun": 3},
                                           "B": {"madagascar": 10, "senegal": 7, "cameroun": 3},
                                           "C": {"madagascar": 10, "senegal": 6, "cameroun": 4}})
        self.assertEqual(sorted(al.owner), sorted(groups))  # chaque groupe attribué une fois
        self.assertEqual(al.unserved, [])

    def test_different_memberships_per_country(self) -> None:
        groups = groups_of([("madagascar", 30), ("senegal", 20), ("cameroun", 10)])
        members = {g: ["A", "C"] if g.startswith("madagascar") else ["B", "C"] for g in groups}
        al = ap.allocate(groups, members, {"A": None, "B": None, "C": None}, ["A", "B", "C"])
        self.assertEqual(per_country(al), {"A": {"madagascar": 20}, "B": {"senegal": 18, "cameroun": 2},
                                           "C": {"madagascar": 10, "senegal": 2, "cameroun": 8}})
        for key, profile in al.owner.items():
            self.assertIn(profile, members[key])

    def test_ten_accounts_twenty_countries(self) -> None:
        accounts = [f"a{i}" for i in range(10)]
        groups = groups_of([(f"pays{c:02d}", 5) for c in range(20)])
        members = {g: accounts for g in groups}
        al = ap.allocate(groups, members, {a: None for a in accounts}, accounts)
        self.assertEqual(len(al.owner), 100)
        self.assertEqual(set(al.loads.values()), {10})
        self.assertEqual({k.split("/")[0] for k in al.owner}, {f"pays{c:02d}" for c in range(20)})
        al = ap.allocate(groups, members, {a: 5 for a in accounts}, accounts)
        self.assertEqual(sorted(al.owner), sorted(groups[:50]))  # les 10 premiers pays
        self.assertEqual(al.unserved, groups[50:])
        self.assertEqual(set(al.loads.values()), {5})

    def test_one_account_ten_countries(self) -> None:
        groups = groups_of([(f"pays{c}", 3) for c in range(10)])
        members = {g: ["A"] for g in groups}
        self.assertEqual(len(ap.allocate(groups, members, {"A": None}, ["A"]).owner), 30)
        al = ap.allocate(groups, members, {"A": 15}, ["A"])
        self.assertEqual(sorted(al.owner), sorted(groups[:15]))
        self.assertEqual(al.unserved, groups[15:])

    def test_global_quota_across_countries(self) -> None:
        groups = groups_of([("madagascar", 10), ("senegal", 10), ("cameroun", 10)])
        members = {g: ["A"] for g in groups}
        self.assertEqual(ap.allocate(groups, members, {"A": 5}, ["A"]).loads, {"A": 5})
        self.assertEqual(ap.allocate(groups, members, {"A": 2}, ["A"]).loads, {"A": 2})

    def test_freeing_path(self) -> None:
        al = ap.allocate(["g2", "g1"], {"g2": ["A", "B"], "g1": ["A"]}, {"A": 1, "B": 1}, ["A", "B"])
        self.assertEqual(al.owner, {"g2": "B", "g1": "A"})
        self.assertEqual(al.unserved, [])

    def test_no_ceiling_historic_alone(self) -> None:
        groups = groups_of([("madagascar", 67)])
        others = [f"n{i}" for i in range(8)]
        al = ap.allocate(groups, {g: ["H"] for g in groups}, {p: None for p in ["H", *others]}, ["H", *others])
        self.assertEqual(al.loads["H"], 67)
        self.assertTrue(all(al.loads[p] == 0 for p in others))

    def test_partial_members(self) -> None:
        groups = groups_of([("madagascar", 67)])
        members = {g: ["H", "B", "C"] if i < 3 else ["H", "B"] if i < 10 else ["H"] for i, g in enumerate(groups)}
        al = ap.allocate(groups, members, {"H": None, "B": None, "C": None}, ["H", "B", "C"])
        self.assertEqual(al.loads, {"H": 57, "B": 7, "C": 3})

    def test_all_members_balanced(self) -> None:
        groups = groups_of([("madagascar", 67)])
        accounts = [f"a{i}" for i in range(9)]
        al = ap.allocate(groups, {g: accounts for g in groups}, {a: None for a in accounts}, accounts)
        self.assertTrue(set(al.loads.values()) <= {7, 8})
        self.assertEqual(sum(al.loads.values()), 67)

    def test_unknown_never_assigned_even_at_quota(self) -> None:
        al = ap.allocate(["g1", "g2", "g3"], {"g1": ["A"], "g2": ["A"], "g3": ["A"]}, {"A": 1, "B": None}, ["A", "B"])
        self.assertEqual(al.owner, {"g1": "A"})
        self.assertEqual(al.unserved, ["g2", "g3"])
        self.assertEqual(al.loads, {"A": 1, "B": 0})

    def test_deterministic(self) -> None:
        groups = groups_of([("madagascar", 30), ("senegal", 20), ("cameroun", 10)])
        members = {g: ["A", "B", "C"] if i % 3 else ["B", "C"] for i, g in enumerate(groups)}
        caps = {"A": 12, "B": None, "C": 25}
        first = ap.allocate(groups, members, caps, ["A", "B", "C"]).owner
        for _ in range(20):
            self.assertEqual(ap.allocate(groups, members, caps, ["A", "B", "C"]).owner, first)

    def test_priority_key_order(self) -> None:
        def cand(key: str, country: str, rank: int, last: str | None, country_last: str | None) -> ap.Candidate:
            parse = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
            return ap.Candidate(key, country, rank, parse(last), parse(country_last))
        items = [
            cand("posted_recent", "madagascar", 0, at(30), at(30)),
            cand("posted_old", "senegal", 0, at(20), at(30)),
            cand("never_mdg_served", "madagascar", 1, None, at(30)),
            cand("never_cmr_old", "cameroun", 0, None, at(25)),
            cand("never_tgo_never", "togo", 1, None, None),
            cand("never_sen_never", "senegal", 2, None, None),
            cand("never_sen_never_rank0", "senegal", 0, None, None),
            cand("never_mdg_never", "madagascar", 5, None, None),
            cand("naive", "ghana", 0, "2026-11-25T10:00:00", None),
        ]
        ordered = [c.key for c in sorted(items, key=ap.priority_key)]
        self.assertEqual(ordered, ["never_mdg_never", "never_sen_never_rank0", "never_sen_never", "never_tgo_never",
                                   "never_cmr_old", "never_mdg_served", "posted_old", "naive", "posted_recent"])

    def test_country_last_post(self) -> None:
        state = {"g1": {"last_post_at": at(20)}, "g2": {"last_post_at": at(25)}, "g3": {}}
        self.assertEqual(ap.country_last_post(state), datetime.fromisoformat(at(25)))
        self.assertIsNone(ap.country_last_post({"g1": {}}))


class RotationTest(unittest.TestCase):
    """Scénario §8 de la spec : 4 pays, quotas insuffisants (5 places pour 9 groupes), trois cycles."""

    EXPECTED = [  # (ordre de priorité, A, B, non servis)
        ("S1 S2 S3 M3 M4 M1 M2 C1 C2", ["S1", "M3", "M4"], ["S2", "S3"], ["M1", "M2", "C1", "C2"]),
        ("M1 M2 C1 C2 S1 M3 M4 S2 S3", ["M1", "M2", "S1"], ["C1", "C2"], ["M3", "M4", "S2", "S3"]),
        ("M3 M4 S2 S3 M1 M2 S1 C1 C2", ["M3", "M4", "M1"], ["S2", "S3"], ["M2", "S1", "C1", "C2"]),
    ]

    def test_three_cycles(self) -> None:
        d0 = datetime(2026, 12, 1, 8, 0, tzinfo=TZ)
        names = {"M": "madagascar", "S": "senegal", "C": "cameroun", "T": "togo"}
        last: dict[str, datetime | None] = {
            "M1": d0 - timedelta(days=2) + timedelta(hours=1), "M2": d0 - timedelta(days=2, minutes=-65),
            "M3": None, "M4": None, "S1": None, "S2": None, "S3": None,
            "C1": d0 - timedelta(days=1) + timedelta(hours=2), "C2": d0 - timedelta(days=1) + timedelta(hours=2, minutes=5),
            "T1": None,
        }
        member_of = {"A": {"madagascar", "senegal", "cameroun"}, "B": {"senegal", "cameroun"}}
        caps = {"A": 3, "B": 2}
        served_twice: set[str] = set()
        for cycle, (order, exp_a, exp_b, exp_unserved) in enumerate(self.EXPECTED):
            now = d0 + timedelta(days=cycle)

            def candidate(group: str) -> ap.Candidate:
                country = names[group[0]]
                dates = [v for k, v in last.items() if names[k[0]] == country and v]
                return ap.Candidate(group, country, int(group[1:]), last[group], max(dates) if dates else None)

            due = [g for g, v in last.items() if v is None or now - v >= timedelta(hours=20)]
            no_member = [g for g in due if not any(names[g[0]] in m for m in member_of.values())]
            self.assertEqual(no_member, ["T1"])
            servable = sorted((g for g in due if g not in no_member), key=lambda g: ap.priority_key(candidate(g)))
            self.assertEqual(" ".join(servable), order)
            members = {g: [a for a in ("A", "B") if names[g[0]] in member_of[a]] for g in servable}
            al = ap.allocate(servable, members, caps, ["A", "B"])
            mine = {a: [g for g in servable if al.owner.get(g) == a] for a in ("A", "B")}
            self.assertEqual((mine["A"], mine["B"], al.unserved), (exp_a, exp_b, exp_unserved))
            self.assertFalse(any(g.startswith("M") for g in mine["B"]))
            self.assertLessEqual(al.loads["A"], 3)
            self.assertLessEqual(al.loads["B"], 2)
            # exécution (§5.7) : ordre fixé avant de publier ; comptes dans l'ordre, pays par priorité de leur 1er groupe
            orders = {}
            for a in ("A", "B"):
                countries: list[str] = []
                for g in mine[a]:
                    countries.append(g[0]) if g[0] not in countries else None
                orders[a] = [g for c in countries for g in mine[a] if g[0] == c]
            when = now
            for a in ("A", "B"):
                for g in orders[a]:
                    last[g] = when
                    when += timedelta(minutes=5)
            if cycle < 2:
                served_twice |= set(al.owner)
        self.assertEqual(served_twice, {"M1", "M2", "M3", "M4", "S1", "S2", "S3", "C1", "C2"})  # tous servis en 2 cycles


NAMES = {"madagascar": "Madagascar", "senegal": "Sénégal", "cameroun": "Cameroun"}


def account(profile: str, active: bool = True, quota: int = 0) -> ap.Account:
    return ap.Account(profile, Path("/profils") / profile, profile, active, quota)


class ReportsTest(unittest.TestCase):
    def test_under_used(self) -> None:
        self.assertEqual(ap.under_used({"H": 57, "B": 7, "C": 3}), ["B", "C"])
        self.assertEqual(ap.under_used({"A": 20, "B": 20}), [])
        self.assertEqual(ap.under_used({"A": 5, "B": 0}), ["B"])
        self.assertEqual(ap.under_used({"A": 0, "B": 0}), [])  # rien à répartir : personne n'est sous-utilisé
        self.assertEqual(ap.under_used({}), [])

    def test_allocation_table(self) -> None:
        groups = groups_of([("madagascar", 30), ("senegal", 20), ("cameroun", 10), ("senegal", 0)])
        groups.append("senegal/extra")
        members = {g: ["A", "B", "C"] for g in groups}
        members["senegal/extra"] = ["A"]
        caps = {"A": 20, "B": None, "C": None}
        al = ap.allocate(groups, members, caps, ["A", "B", "C"])
        text = ap.allocation_table(al, [account("A", quota=20), account("B"), account("C")], caps,
                                   {g: g.split("/")[0] for g in groups}, NAMES)
        lines = text.splitlines()
        self.assertEqual(lines[0].split(), ["Compte", "Madagascar", "Sénégal", "Cameroun", "Total", "Quota", "restant"])
        rows = {line.split()[0]: line.split()[1:] for line in lines[1:4]}
        self.assertEqual(rows["A"][-2:], ["20", "20"])
        self.assertEqual(rows["B"][-1], "∞")
        self.assertEqual(sum(int(r[3]) for r in rows.values()), 61)
        self.assertIn("Pays (servis / à publier) : Madagascar 30/30 · Sénégal 21/21 · Cameroun 10/10", text)

    def test_membership_summary_text(self) -> None:
        keys = {"madagascar": [f"madagascar/g{i}" for i in range(4)], "senegal": ["senegal/s1"]}
        states = {"madagascar": {"g0": {"history": [{"at": at(20), "status": "posted", "profile": "H"}]},
                                 "g1": {"history": [{"at": at(20), "status": "posted", "profile": "H"}]}}}
        membership = {"N": {"madagascar/g0": {"status": "member", "checked_at": at(25, 10)},
                            "madagascar/g1": {"status": "not_member", "checked_at": at(25, 11)},
                            "madagascar/g2": {"status": "member", "checked_at": at(25, 12), "detail": "restriction",
                                              "restriction": {"reason": "suspended", "until": at(3, 6, month=12)}}}}
        memberships = ap.Memberships(states, {}, membership)
        restrictions = ap.group_restrictions(states, membership, NOW)
        text = ap.membership_summary_text([account("H"), account("N", active=False, quota=15), account("Z")], keys,
                                          memberships, restrictions, NAMES)
        lines = {line.split()[0]: line for line in text.splitlines() if line.split()}
        self.assertIn("Madagascar (4)", text)
        self.assertIn("2 membre · 0 non membre · 2 ?", lines["H"])
        self.assertIn("(publications)", lines["H"])
        self.assertIn("2 membre · 1 non membre · 1 ?", lines["N"])
        self.assertIn("25/11/2026 12:00", lines["N"])
        self.assertIn("non", lines["N"].split()[1])
        self.assertIn("15", lines["N"])
        self.assertIn("jamais", lines["Z"])
        self.assertIn("Groupes sans membre confirmé parmi les comptes actifs : 3 (madagascar/g2, madagascar/g3, senegal/s1)", text)
        self.assertIn("madagascar/g2 (suspendu jusqu'au 03/12/2026 06:00, vu par N)", text)

    def test_signals_text(self) -> None:
        text = ap.signals_text(
            unserved={"senegal/s1": "aucun compte membre confirmé", "senegal/s2": "membres au quota",
                      "madagascar/g9": "restriction de groupe jusqu'au 03/12/2026 06:00",
                      "cameroun/c1": "API du pays illisible"},
            inactive={"Z": "pause jusqu'au 02/12/2026 08:00"},
            stopped={"A": ("limite anti-spam du compte", 4)},
            loads={"H": 57, "B": 7, "C": 3},
            country_last={"senegal": datetime.fromisoformat(at(29)), "madagascar": None,
                          "cameroun": datetime.fromisoformat(at(30))},
            country_names=NAMES)
        for needle in ("aucun compte membre confirmé : senegal/s1", "membres au quota : senegal/s2",
                       "restriction de groupe jusqu'au 03/12/2026 06:00 : madagascar/g9",
                       "API du pays illisible : cameroun/c1", "Sénégal (2 groupe(s), en attente depuis le 29/11/2026)",
                       "Madagascar (1 groupe(s), jamais servi)", "Z (pause jusqu'au 02/12/2026 08:00)",
                       "A (limite anti-spam du compte) - 4 groupe(s) laissé(s) pour le cycle suivant",
                       "Sous-utilisés : B (7), C (3)"):
            self.assertIn(needle, text)
        empty = ap.signals_text({}, {}, {}, {"A": 3}, {}, NAMES)
        self.assertIn("Non servis : —", empty)
        self.assertIn("Sous-utilisés : —", empty)


if __name__ == "__main__":
    unittest.main()

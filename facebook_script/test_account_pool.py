"""
Tests de la logique pure du pool de comptes (account_pool.py) : aucun navigateur, aucun réseau.

    python -m unittest discover -s facebook_script -p "test_account_pool.py" -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import account_pool as ap

SCRIPT_DIR = Path(__file__).resolve().parent


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


if __name__ == "__main__":
    unittest.main()

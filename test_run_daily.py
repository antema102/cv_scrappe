"""Tests de la planification de run_daily.py (--at HH:MM). python -m unittest test_run_daily -v"""
import unittest
from datetime import datetime, time

import run_daily


class NextAtTest(unittest.TestCase):
    def test_later_today(self) -> None:
        self.assertEqual(run_daily.next_at(time(19, 0), datetime(2026, 11, 25, 17, 33)), datetime(2026, 11, 25, 19, 0))

    def test_already_passed_goes_to_tomorrow(self) -> None:
        self.assertEqual(run_daily.next_at(time(19, 0), datetime(2026, 11, 25, 19, 5)), datetime(2026, 11, 26, 19, 0))

    def test_exact_time_goes_to_tomorrow(self) -> None:  # un cycle qui finit pile à 19:00 ne se relance pas aussitôt
        self.assertEqual(run_daily.next_at(time(19, 0), datetime(2026, 11, 25, 19, 0)), datetime(2026, 11, 26, 19, 0))

    def test_parse_at(self) -> None:
        self.assertEqual(run_daily.parse_at("19:00"), time(19, 0))
        self.assertEqual(run_daily.parse_at("7:30"), time(7, 30))
        for bad in ("19h", "25:00", "19:60", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_daily.parse_at(bad)


if __name__ == "__main__":
    unittest.main()

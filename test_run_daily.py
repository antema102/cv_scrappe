"""
Tests de run_daily.py : planification (--at HH:MM), ordonnanceur parallèle, chien de garde, bilan.
Aucun accès réseau ni Facebook ; les tests du chien de garde lancent de vrais processus python (pas de Chrome).

    python -m unittest test_run_daily -v
"""
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from unittest import mock

import psutil

import run_daily
from countries import COUNTRIES
from run_daily import Step, StepResult

PY = sys.executable


class NextAtTest(unittest.TestCase):
    def test_later_today(self) -> None:
        self.assertEqual(run_daily.next_at(dtime(19, 0), datetime(2026, 11, 25, 17, 33)), datetime(2026, 11, 25, 19, 0))

    def test_already_passed_goes_to_tomorrow(self) -> None:
        self.assertEqual(run_daily.next_at(dtime(19, 0), datetime(2026, 11, 25, 19, 5)), datetime(2026, 11, 26, 19, 0))

    def test_exact_time_goes_to_tomorrow(self) -> None:  # un cycle qui finit pile à 19:00 ne se relance pas aussitôt
        self.assertEqual(run_daily.next_at(dtime(19, 0), datetime(2026, 11, 25, 19, 0)), datetime(2026, 11, 26, 19, 0))

    def test_parse_at(self) -> None:
        self.assertEqual(run_daily.parse_at("19:00"), dtime(19, 0))
        self.assertEqual(run_daily.parse_at("7:30"), dtime(7, 30))
        for bad in ("19h", "25:00", "19:60", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run_daily.parse_at(bad)


def _step(code: str, profile: str | None = None) -> Step:
    return Step(code, f"pays {code}", [PY, "-c", "pass"], Path(tempfile.gettempdir()) / (profile or f"profil_{code}"))


class StepsTest(unittest.TestCase):
    def test_each_country_gets_its_profile_explicitly(self) -> None:
        countries = [COUNTRIES["madagascar"], COUNTRIES["cameroun"]]
        with mock.patch.object(run_daily, "active_countries", return_value=countries):
            cycle = run_daily.steps({"cameroun": "my_custom_profile_facebook_cameroun"})
        by_code = {step.code: step for step in cycle}
        self.assertEqual(by_code["madagascar"].profile_dir.name, "my_custom_profile_facebook")  # profil historique
        self.assertEqual(by_code["cameroun"].profile_dir.name, "my_custom_profile_facebook_cameroun")
        for step in cycle:  # le profil surveillé est celui passé au script, jamais relu en cours de route
            self.assertEqual(step.cmd[step.cmd.index("--profile") + 1], step.profile_dir.name)
            self.assertEqual(step.cmd[step.cmd.index("--country") + 1], step.code)


class OrderTest(unittest.TestCase):
    def test_longest_first_unknown_in_front_ties_keep_order(self) -> None:
        cycle = [_step("madagascar"), _step("cameroun"), _step("maurice"), _step("tunisie"), _step("senegal")]
        durations = {"madagascar": 780.0, "cameroun": 600.0, "maurice": 30.0, "senegal": 30.0}
        self.assertEqual([s.code for s in run_daily.order_steps(cycle, durations)],
                         ["tunisie", "madagascar", "cameroun", "maurice", "senegal"])

    def test_last_durations_ignores_interrupted_runs(self) -> None:
        cycles = [
            {"countries": {"cameroun": {"status": "ok", "minutes": 600}, "maurice": {"status": "ok", "minutes": 20}}},
            {"countries": {"cameroun": {"status": "interrompue", "minutes": 3},
                           "maurice": {"status": "erreur", "minutes": 25}, "senegal": {"status": "non lancée"}}},
        ]
        self.assertEqual(run_daily.last_durations(cycles), {"cameroun": 600.0, "maurice": 25.0})


class FakeRunner:
    """Pays simulés (durée en secondes) : relève les intervalles d'exécution et le parallélisme maximal."""

    def __init__(self, durations: dict[str, float], fail: set[str] = frozenset()) -> None:
        self.durations = durations
        self.fail = fail
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.intervals: dict[str, tuple[float, float]] = {}

    def __call__(self, step: Step) -> StepResult:
        start = time.monotonic()
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.durations[step.code])
            if step.code in self.fail:
                raise RuntimeError("plantage simulé")
            return StepResult(status="ok", exit_code=0)
        finally:
            with self.lock:
                self.active -= 1
                self.intervals[step.code] = (start, time.monotonic())


def _quiet_parallel(*args, **kwargs):
    with mock.patch.object(run_daily, "log"):
        return run_daily.run_parallel(*args, poll=0.02, **kwargs)


class ParallelSchedulerTest(unittest.TestCase):
    def test_never_more_than_max_parallel(self) -> None:
        runner = FakeRunner({c: 0.25 for c in "abcde"})
        results = _quiet_parallel([_step(c) for c in "abcde"], 2, runner)
        self.assertEqual(runner.max_active, 2)
        self.assertEqual({c: r.status for c, r in results.items()}, {c: "ok" for c in "abcde"})

    def test_next_country_starts_as_soon_as_one_finishes(self) -> None:
        # a (long) et b (court) démarrent ensemble ; c prend la place de b sans attendre a
        runner = FakeRunner({"a": 1.2, "b": 0.2, "c": 0.2})
        _quiet_parallel([_step("a"), _step("b"), _step("c")], 2, runner)
        a, b, c = (runner.intervals[k] for k in "abc")
        self.assertGreaterEqual(c[0], b[1] - 0.01)  # après la fin de b
        self.assertLess(c[0], a[1])                 # avant la fin de a
        self.assertLess(c[1], a[1])                 # et c finit sans attendre a

    def test_two_countries_on_one_profile_never_overlap(self) -> None:
        runner = FakeRunner({"a": 0.4, "b": 0.4, "c": 0.4})
        cycle = [_step("a", "partagé"), _step("b", "partagé"), _step("c")]
        _quiet_parallel(cycle, 3, runner)
        (a0, a1), (b0, b1), (c0, c1) = (runner.intervals[k] for k in "abc")
        self.assertTrue(b0 >= a1 - 0.01 or a0 >= b1 - 0.01, "a et b ont ouvert le même profil en même temps")
        self.assertLess(c0, a1)  # c (autre profil) tourne pendant a
        self.assertEqual(runner.max_active, 2)

    def test_shared_profile_waits_without_blocking_free_countries(self) -> None:
        # b attend le profil de a, mais c (profil libre, plus loin dans la file) démarre tout de suite
        runner = FakeRunner({"a": 0.6, "b": 0.1, "c": 0.1})
        _quiet_parallel([_step("a", "p"), _step("b", "p"), _step("c")], 2, runner)
        self.assertLess(runner.intervals["c"][0], runner.intervals["a"][1])
        self.assertGreaterEqual(runner.intervals["b"][0], runner.intervals["a"][1] - 0.01)

    def test_failing_country_does_not_stop_the_others(self) -> None:
        runner = FakeRunner({"a": 0.1, "b": 0.1, "c": 0.3}, fail={"b"})
        results = _quiet_parallel([_step("a"), _step("b"), _step("c")], 2, runner)
        self.assertEqual(results["b"].status, "erreur")
        self.assertEqual((results["a"].status, results["c"].status), ("ok", "ok"))

    def test_low_memory_holds_extra_countries_but_never_the_first(self) -> None:
        runner = FakeRunner({c: 0.1 for c in "abc"})
        results = _quiet_parallel([_step(c) for c in "abc"], 2, runner, lambda: (False, "mémoire basse"))
        self.assertEqual(runner.max_active, 1)
        self.assertEqual(len(results), 3)

    def test_stop_event_set_on_ctrl_c(self) -> None:
        stop = threading.Event()

        def runner(step: Step) -> StepResult:
            stop.wait(5)
            return StepResult(status="interrompue")

        with mock.patch.object(run_daily, "wait", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            _quiet_parallel([_step("a"), _step("b")], 2, runner, stop=stop)
        self.assertTrue(stop.is_set())


def _child_pid(log_text: str) -> int:
    return int(re.search(r"enfant=(\d+)", log_text).group(1))  # la ligne « Début » cite le script : chiffres seuls


class WatchdogTest(unittest.TestCase):
    """Vrais processus : un pays bloqué est arrêté avec sa descendance, jamais un processus d'un autre pays."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        patches = [mock.patch.object(run_daily, "STALL_RETRIES", 0), mock.patch.object(run_daily, "KILL_GRACE", 5),
                   mock.patch("sys.stdout", self.stdout),
                   mock.patch("sys.stderr", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, code: str, script: str, stall: float = 60.0, stop: threading.Event | None = None) -> StepResult:
        step = Step(code, f"pays {code}", [PY, "-c", script], self.tmp / f"profil_{code}")
        return run_daily.run_step(step, stall, stop, {"FB_OCR_THREADS": "3"}, log_dir=self.tmp / "logs")

    def _log(self, code: str) -> str:
        return next((self.tmp / "logs" / code).glob("*.log")).read_text(encoding="utf-8")

    def test_stalled_country_killed_with_its_children_other_country_untouched(self) -> None:
        other_country = psutil.Popen([PY, "-c", "import time; time.sleep(120)"])
        self.addCleanup(lambda: other_country.kill() if other_country.is_running() else None)
        script = ("import subprocess, sys, time\n"
                  "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                  "print(f'enfant={c.pid}', flush=True)\n"
                  "time.sleep(120)\n")
        started = time.monotonic()
        result = self._run("bloque", script, stall=1.5)
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(result.status, "bloquée")
        child = _child_pid(self._log("bloque"))
        time.sleep(0.5)
        self.assertFalse(psutil.pid_exists(child) and psutil.Process(child).status() != psutil.STATUS_ZOMBIE,
                         "le sous-processus du pays bloqué tourne encore")
        self.assertTrue(other_country.is_running(), "le chien de garde a arrêté le processus d'un autre pays")

    def test_exit_code_country_log_and_prefixed_main_log(self) -> None:
        script = ("import os, sys\n"
                  "print('offres trouvées : 3')\n"
                  "print('ocr=' + os.environ['FB_OCR_THREADS'])\n"
                  "print('attention', file=sys.stderr)\n"
                  "sys.exit(3)\n")
        result = self._run("tunisie", script)
        self.assertEqual((result.status, result.exit_code, result.attempts), ("erreur", 3, 1))
        country_log = self._log("tunisie")
        self.assertRegex(country_log, r"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] offres trouvées : 3")
        self.assertIn("ocr=3", country_log)  # limite OCR transmise au pays
        self.assertIn("attention", country_log)
        self.assertIn("[run_daily] Début : pays tunisie", country_log)
        self.stdout.flush()
        main_log = self.stdout.buffer.getvalue().decode("utf-8")
        self.assertIn("[tunisie] offres trouvées : 3", main_log)

    def test_end_of_country_seen_at_once(self) -> None:  # régression : 5 s perdues par pays avant le suivant
        started = time.monotonic()
        result = self._run("rapide", "print('fini')")
        self.assertEqual(result.status, "ok")
        self.assertLess(time.monotonic() - started, 3)

    def test_stop_interrupts_running_country(self) -> None:
        stop = threading.Event()
        threading.Timer(0.8, stop.set).start()
        started = time.monotonic()
        result = self._run("long", "import time\nwhile True:\n    print('.', flush=True)\n    time.sleep(0.2)\n", stop=stop)
        self.assertEqual(result.status, "interrompue")
        self.assertLess(time.monotonic() - started, 20)


class RunCycleTest(unittest.TestCase):
    """Cycle complet (vrais processus python, pas de Chrome) : parallélisme, journaux et bilan au bon endroit."""

    def test_full_cycle(self) -> None:
        tmp = Path(tempfile.mkdtemp())

        def fake(code: str, seconds: float, profile: str, exit_code: int = 0) -> Step:
            script = f"import sys, time\nprint('{code} démarre', flush=True)\ntime.sleep({seconds})\nsys.exit({exit_code})"
            return Step(code, f"pays {code}", [PY, "-c", script], tmp / profile)

        # démarrage d'un processus python sous Windows : ~0,4 s, d'où deux pays longs pour mesurer le parallélisme
        cycle = [fake("madagascar", 3.0, "p_mdg"), fake("cameroun", 3.0, "p_cmr"), fake("maurice", 0.3, "p_petits"),
                 fake("senegal", 0.3, "p_petits"), fake("tunisie", 0.5, "p_tun", exit_code=1)]
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        with mock.patch.object(run_daily, "steps", lambda: cycle), \
                mock.patch.object(run_daily, "RUNS_FILE", tmp / "daily_runs.json"), \
                mock.patch.object(run_daily, "COUNTRY_LOG_DIR", tmp / "logs"), \
                mock.patch.object(run_daily, "PAGES_OUTPUT_DIR", tmp / "pages"), \
                mock.patch.object(run_daily, "SCHEDULER_POLL", 0.05), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            started = time.monotonic()
            results = run_daily.run_cycle(60, max_parallel=2, min_free_ram_gb=0)
            elapsed = time.monotonic() - started
        self.assertEqual(list(results), ["madagascar", "cameroun", "maurice", "senegal", "tunisie"])  # ordre du cycle
        self.assertEqual({c: r.status for c, r in results.items()},
                         {"madagascar": "ok", "cameroun": "ok", "maurice": "ok", "senegal": "ok", "tunisie": "erreur"})
        self.assertLess(elapsed, 3.0 + 3.0 + 0.3 + 0.3 + 0.5)  # plus court que la somme des pays (séquentiel)
        for code in results:  # journaux dans le dossier configuré (régression : écrits dans le vrai logs/)
            self.assertTrue(list((tmp / "logs" / code).glob("*.log")), code)
        saved = run_daily.load_runs(tmp / "daily_runs.json")
        self.assertEqual(saved[-1]["countries"]["tunisie"]["exit_code"], 1)
        self.assertIsNone(saved[-1]["countries"]["madagascar"]["jobs"])  # aucun JSON dans le dossier configuré


class FakeProc:
    def __init__(self, pid: int, cmdline: list[str], parent: "FakeProc | None" = None,
                 children: list["FakeProc"] = (), name: str = "chrome.exe") -> None:
        self.pid = pid
        self.info = {"name": name, "cmdline": cmdline}
        self._parent = parent
        self._children = list(children)

    def parent(self) -> "FakeProc | None":
        return self._parent

    def name(self) -> str:
        return self.info["name"]

    def children(self, recursive: bool = False) -> list["FakeProc"]:
        return self._children


class OrphanChromeTest(unittest.TestCase):
    def test_only_orphan_chromes_of_this_profile(self) -> None:
        tmp = Path(tempfile.gettempdir())
        mine, other = tmp / "profil_cameroun", tmp / "profil_madagascar"
        python = FakeProc(1, ["python.exe"], name="python.exe")
        renderer = FakeProc(11, [f"--user-data-dir={mine}", "--type=renderer"])
        processes = [
            FakeProc(10, ["chrome.exe", f"--user-data-dir={mine}"], parent=None, children=[renderer]),  # orphelin
            renderer,
            FakeProc(20, ["chrome.exe", f'--user-data-dir="{mine}"'], parent=python),  # script encore actif
            FakeProc(30, ["chrome.exe", f"--user-data-dir={other}"], parent=None),  # autre pays
            FakeProc(40, ["chrome.exe", f"--user-data-dir={mine}_2"], parent=None),  # autre profil au nom proche
            FakeProc(50, ["notepad.exe", f"--user-data-dir={mine}"], name="notepad.exe"),
        ]
        found = run_daily._orphan_profile_chromes(mine, processes)
        self.assertEqual(sorted(p.pid for p in found), [10, 11])


class SaveCycleTest(unittest.TestCase):
    def test_summary_counts_and_history_limit(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "jobs_cameroun.json").write_text(json.dumps({"j1": {}, "j2": {}}), encoding="utf-8")
        (tmp / "companies_cameroun.json").write_text(json.dumps({"c1": {}}), encoding="utf-8")
        runs = tmp / "daily_runs.json"
        start = datetime(2026, 11, 26, 19, 0, tzinfo=timezone(timedelta(hours=3)))
        results = {"cameroun": StepResult("ok", 0, 1, minutes=600.0), "maurice": StepResult("bloquée", None, 3)}
        with mock.patch.object(run_daily, "RUNS_KEEP", 2):
            for day in range(3):
                cycle = run_daily.save_cycle(start + timedelta(days=day), start + timedelta(days=day, hours=10),
                                             results, 2, runs, tmp)
        self.assertEqual(cycle["countries"]["cameroun"]["jobs"], 2)
        self.assertEqual(cycle["countries"]["cameroun"]["companies"], 1)
        self.assertIsNone(cycle["countries"]["maurice"]["jobs"])
        self.assertEqual(cycle["minutes"], 600.0)
        saved = run_daily.load_runs(runs)
        self.assertEqual(len(saved), 2)  # RUNS_KEEP
        self.assertEqual(run_daily.last_durations(saved)["cameroun"], 600.0)


if __name__ == "__main__":
    unittest.main()

"""
run_daily.py
============
Planificateur quotidien du scraper (tourne en continu sous PM2, voir ecosystem.config.js).

Chaque cycle, pour chaque pays qui a des liens (facebook_script/pays/<code>/pages.txt ou groups.txt), un processus
indépendant :
    python facebook_script/pages.py --push --headless --country <code> --profile <profil du pays>
        -> pages et groupes Facebook, analyse IA, envoi au backend
La liste des pays et leurs profils sont relus à chaque cycle : créer le fichier d'un nouveau pays suffit, sans
redémarrer PM2.

Parallélisme (--max-parallel, défaut 2) :
    - Chaque pays scrape avec SON profil Chrome, donc son compte Facebook (facebook_script/scrape_profiles.json ; pays
      absent = my_custom_profile_facebook, le profil historique). Au plus --max-parallel pays en même temps ; dès qu'un
      pays termine, le suivant en attente démarre. Les autres continuent jusqu'à leur propre fin.
    - Deux pays sur le MÊME profil ne tournent jamais en même temps (deux Chrome ne s'ouvrent pas sur un profil, et
      ce serait le même compte Facebook) : ils passent l'un après l'autre. Sans scrape_profiles.json, tous les pays
      sont sur le profil historique : même fonctionnement qu'avant, un pays à la fois.
    - Ordre : le pays le plus long au cycle précédent d'abord (downloaded_files/daily_runs.json ; pays jamais
      mesuré : en tête), pour finir le cycle au plus tôt.
    - Ressources : un pays de plus ne démarre que si la mémoire disponible dépasse --min-free-ram-gb ; OCR limité à
      --ocr-threads threads par pays (défaut : la moitié des cœurs, partagée entre les pays) ; processus en priorité
      basse (--priority low, Chrome compris) pour ne pas gêner le backend ni MongoDB.
    - Un pays en échec ou bloqué n'arrête jamais les autres. Bilan du cycle (statut, durée, offres et entreprises par
      pays) dans le journal et dans downloaded_files/daily_runs.json (30 derniers cycles).

Journaux : logs/countries/<code>/<date du cycle>.log (horodaté, un fichier par pays et par cycle, gardés
COUNTRY_LOG_KEEP_DAYS jours) ; le journal principal (PM2) reçoit aussi chaque ligne, préfixée de [code pays].

Chien de garde : un pays qui n'écrit plus rien pendant --stall-minutes (défaut 30) est considéré bloqué (navigateur
figé : les appels CDP n'ont pas de timeout) — arrêt de SON script et de sa descendance (chromedriver, Chrome), puis des
Chrome orphelins de SON profil (lanceur disparu), puis relance (STALL_RETRIES fois). Jamais un Chrome d'un autre
profil, ni un Chrome dont le lanceur (python / uc_driver) tourne encore : les autres pays, post_groups.py et les autres
scripts (gemini, sirene_google_sheets...) restent intacts. Relancer ne perd rien : tout est en cache.

Le script Facebook tourne sans fenêtre ni terminal : si la session d'un profil a expiré, ce pays s'arrête (pas
d'attente de connexion manuelle) — reconnecter le compte de ce pays avec
    python facebook_script/pages.py --login-only --country <code>
puis laisser le cycle suivant reprendre.

Planification :
    - Avec --at HH:MM (PM2 : --at 19:00) : chaque jour à cette heure, même après un redémarrage (un cycle de plus de
      24 h saute un jour au lieu de décaler l'horaire). Lancement manqué depuis moins de MISSED_RUN_GRACE (serveur ou
      PM2 arrêté à l'heure prévue, cycle interrompu) : rattrapé au démarrage.
    - Heures dans le fuseau --tz (défaut Indian/Antananarivo, heure de Madagascar), quel que soit celui du serveur
      (Astrakhan, UTC+4). Ne jamais passer par la variable d'environnement TZ : sous Windows, Python ne comprend pas un
      nom IANA et se met en UTC (bug du 25/11/2026 : « 19:00 » attendu à 22 h à Madagascar).
    - Sans --at : premier lancement à minuit (ou tout de suite avec --now), le suivant 24 h après le début du cycle ;
      un cycle de plus de 24 h relance dès sa fin et décale l'horaire d'autant.

L'heure du prochain lancement est sauvegardée dans downloaded_files/daily_schedule.json :
un redémarrage de PM2 ou du serveur ne décale pas la planification.

Utilisation :
    python run_daily.py --plan       # pays, profils, comptes, ordre et parallélisme prévus, sans rien lancer
    python run_daily.py          # attend minuit pour le premier cycle
    python run_daily.py --now    # premier cycle immédiatement
    python run_daily.py --once   # un seul cycle puis quitte (test)
    python run_daily.py --stall-minutes 45
    python run_daily.py --at 19:00 --tz Indian/Antananarivo
    python run_daily.py --max-parallel 3 --min-free-ram-gb 3 --ocr-threads 2
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from datetime import time as dtime
from pathlib import Path
from typing import IO, Any, BinaryIO, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psutil

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "facebook_script"))
import scrape_profiles  # noqa: E402
from countries import active_countries  # noqa: E402

SCHEDULE_FILE = PROJECT_ROOT / "downloaded_files" / "daily_schedule.json"
RUNS_FILE = PROJECT_ROOT / "downloaded_files" / "daily_runs.json"  # bilan des derniers cycles
RUNS_KEEP = 30
PAGES_OUTPUT_DIR = PROJECT_ROOT / "downloaded_files" / "facebook_pages"
COUNTRY_LOG_DIR = PROJECT_ROOT / "logs" / "countries"
COUNTRY_LOG_KEEP_DAYS = 14
INTERVAL = timedelta(hours=24)
# Lancement --at manqué depuis moins longtemps (serveur redémarré, cycle interrompu) : rattrapé au démarrage
MISSED_RUN_GRACE = timedelta(hours=12)
DEFAULT_TZ = "Indian/Antananarivo"
# Fuseaux sans heure d'été utilisables sans le paquet tzdata (Windows n'a pas de base IANA pour zoneinfo)
FIXED_ZONES = {"Indian/Antananarivo": timedelta(hours=3), "UTC": timedelta(0)}
# Phase silencieuse la plus longue d'un lancement sain : un appel IA (180 s x 4 essais + attentes)
DEFAULT_STALL_MINUTES = 30
STALL_RETRIES = 2
KILL_GRACE = 10
DEFAULT_MAX_PARALLEL = 2
# Un Chrome sur un gros groupe Facebook dépasse 1 Go (jusqu'à 4,5 Go mesurés) : pas de pays de plus en dessous
DEFAULT_MIN_FREE_RAM_GB = 2.0
SCHEDULER_POLL = 5.0
# Lanceurs du Chrome d'un script encore actif : son Chrome n'est jamais un orphelin
CHROME_LAUNCHERS = ("python.exe", "pythonw.exe", "python", "python3", "uc_driver.exe", "uc_driver",
                    "chromedriver.exe", "chromedriver")
CHROME_NAMES = ("chrome.exe", "chrome")


@dataclass
class Step:
    """Un pays du cycle : sa commande et son profil Chrome (ressource exclusive : un seul pays à la fois dessus)."""
    code: str
    label: str
    cmd: list[str]
    profile_dir: Path | None = None

    @property
    def profile_key(self) -> str:
        if self.profile_dir is None:
            return f"<sans profil:{self.code}>"
        return os.path.normcase(os.path.abspath(self.profile_dir))


@dataclass
class StepResult:
    status: str  # ok, erreur (code non nul), bloquée (chien de garde à chaque essai), interrompue, non lancée
    exit_code: int | None = None
    attempts: int = 0
    start: str = ""
    end: str = ""
    minutes: float = 0.0
    log_file: str = ""
    profile: str = ""


def steps(mapping: dict[str, str] | None = None) -> list[Step]:
    """Un pays actif = une étape ; profil de scrape_profiles.json (ProfileConfigError si le fichier est invalide)."""
    if mapping is None:
        mapping = scrape_profiles.load_mapping()
    result = []
    for country in active_countries("pages", "groups"):
        profile = scrape_profiles.profile_for(country, mapping)
        # --profile explicite : le profil surveillé (chien de garde) est celui qu'ouvre le script, même si
        # scrape_profiles.json change pendant le cycle
        cmd = [sys.executable, "facebook_script/pages.py", "--push", "--headless", "--country", country.code,
               "--profile", profile.name]
        result.append(Step(country.code, f"facebook pages et groupes - {country.name}", cmd, profile))
    return result


_output_lock = threading.Lock()


def schedule_zone(name: str) -> tzinfo:
    """Fuseau --tz : base IANA si disponible (tzdata), sinon FIXED_ZONES ; ValueError si inconnu."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name in FIXED_ZONES:
            return timezone(FIXED_ZONES[name], name)
        raise ValueError(f"fuseau inconnu « {name} » (sous Windows : pip install tzdata, ou {', '.join(FIXED_ZONES)})")


ZONE: tzinfo = schedule_zone(DEFAULT_TZ)


def now() -> datetime:
    """Heure actuelle dans le fuseau --tz (jamais l'heure locale du serveur ni la variable TZ)."""
    return datetime.now(ZONE)


def log(message: str) -> None:
    with _output_lock:
        print(f"[{now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def _write_stream(stream: IO[Any], data: bytes) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        stream.flush()  # texte déjà écrit par print() avant les octets du script
        buffer.write(data)
        buffer.flush()
    else:
        stream.write(data.decode("utf-8", errors="replace"))
        stream.flush()


class StepOutput:
    """
    Sortie d'un pays : journal du pays (logs/countries/<code>/<date>.log, chaque ligne horodatée) et journal principal
    (PM2), chaque ligne préfixée de [code] pour suivre plusieurs pays mêlés.
    """

    def __init__(self, code: str, log_file: Path, mirror: bool = True) -> None:
        self.code = code
        self.prefix = f"[{code}] ".encode()
        self.mirror = mirror
        log_file.parent.mkdir(parents=True, exist_ok=True)
        self.file: BinaryIO = open(log_file, "ab")

    def line(self, raw: bytes, error: bool = False) -> None:
        if not raw.endswith(b"\n"):
            raw += b"\n"
        stamp = f"[{now():%Y-%m-%d %H:%M:%S}] ".encode()
        with _output_lock:
            if self.mirror:
                _write_stream(sys.stderr if error else sys.stdout, self.prefix + raw)
            if not self.file.closed:  # lecteur encore actif après la fin du pays (tube gardé ouvert par un Chrome)
                self.file.write(stamp + raw)
                self.file.flush()

    def message(self, text: str) -> None:
        """Message de run_daily sur ce pays : journal principal ET journal du pays."""
        log(f"[{self.code}] {text}")
        with _output_lock:
            self.file.write(f"[{now():%Y-%m-%d %H:%M:%S}] [run_daily] {text}\n".encode())
            self.file.flush()

    def close(self) -> None:
        with _output_lock:
            self.file.close()


def _forward(pipe: BinaryIO, output: StepOutput, error: bool, last_output: list[float]) -> None:
    for line in iter(pipe.readline, b""):
        last_output[0] = time.monotonic()
        output.line(line, error)
    pipe.close()


def _same_dir(path: str, target: str) -> bool:
    return os.path.normcase(os.path.abspath(path)) == target


def _orphan_profile_chromes(profile_dir: Path, processes: Iterable[Any] | None = None) -> list[Any]:
    """
    Chrome principaux lancés sur CE profil exactement (--user-data-dir) dont le lanceur n'existe plus, avec leurs
    sous-processus. Jamais un Chrome d'un autre profil, ni un Chrome dont le lanceur (python, uc_driver) tourne encore.
    """
    target = os.path.normcase(os.path.abspath(profile_dir))
    found: list[Any] = []
    for proc in psutil.process_iter(["name", "cmdline"]) if processes is None else processes:
        with suppress(psutil.Error, OSError):
            if (proc.info["name"] or "").lower() not in CHROME_NAMES:
                continue
            args = proc.info["cmdline"] or []
            if any(arg.startswith("--type=") for arg in args):
                continue  # renderer, GPU... : arrêtés avec leur processus principal
            dirs = [arg.split("=", 1)[1].strip('"') for arg in args if arg.startswith("--user-data-dir=")]
            if not dirs or not _same_dir(dirs[0], target):
                continue
            parent = proc.parent()  # None si le lanceur est mort (psutil écarte un PID réattribué)
            if parent is not None and (parent.name() or "").lower() in CHROME_LAUNCHERS:
                continue
            found.extend([*proc.children(recursive=True), proc])
    return found


def _stop_processes(processes: Iterable[psutil.Process]) -> None:
    processes = list(processes)
    for proc in processes:
        with suppress(psutil.Error):
            proc.terminate()
    _gone, alive = psutil.wait_procs(processes, timeout=KILL_GRACE)
    for proc in alive:
        with suppress(psutil.Error):
            proc.kill()
    psutil.wait_procs(alive, timeout=KILL_GRACE)


def _kill_step(pid: int, profile_dir: Path | None) -> int:
    """
    Le script d'UN pays, toute sa descendance (chromedriver, Chrome), puis les Chrome orphelins de SON profil. Les
    autres pays (autres profils, ou lanceur encore vivant) ne sont jamais touchés.
    """
    victims: dict[int, psutil.Process] = {}
    with suppress(psutil.Error):
        root = psutil.Process(pid)
        for proc in [*root.children(recursive=True), root]:  # relevés AVANT l'arrêt : ensuite les liens disparaissent
            victims[proc.pid] = proc
    _stop_processes(victims.values())
    if profile_dir is not None:
        orphans = {proc.pid: proc for proc in _orphan_profile_chromes(profile_dir) if proc.pid not in victims}
        _stop_processes(orphans.values())
        victims.update(orphans)
    return len(victims)


def _popen_priority(low_priority: bool) -> dict[str, Any]:
    """Priorité basse sous Windows : héritée par chromedriver et Chrome (classe BELOW_NORMAL)."""
    flag = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    return {"creationflags": flag} if low_priority and flag else {}


def _run_watched(
    step: Step, output: StepOutput, stall_seconds: float, stop: threading.Event,
    env_extra: dict[str, str] | None = None, low_priority: bool = True,
) -> tuple[int | None, str]:
    """(code de sortie, "terminée") ; (None, "bloquée") si aucune sortie pendant stall_seconds ; (None, "interrompue")."""
    # Sortie non tamponnée : sinon le chien de garde prendrait un print en attente pour un blocage
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", **(env_extra or {}))
    # stdin fermé : sous PM2 personne ne peut répondre à un input() (connexion manuelle Facebook)
    proc = subprocess.Popen(
        step.cmd, cwd=PROJECT_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **_popen_priority(low_priority),
    )
    if low_priority and os.name != "nt":
        with suppress(psutil.Error):
            psutil.Process(proc.pid).nice(10)  # hérité par les processus lancés ensuite (Chrome)
    last_output = [time.monotonic()]
    readers = [
        threading.Thread(target=_forward, args=(proc.stdout, output, False, last_output), daemon=True),
        threading.Thread(target=_forward, args=(proc.stderr, output, True, last_output), daemon=True),
    ]
    for reader in readers:
        reader.start()
    reason = "terminée"
    while True:
        try:  # fin du script vue tout de suite (le pays suivant démarre aussitôt), arrêt demandé vu en 1 s au plus
            proc.wait(timeout=min(1.0, stall_seconds / 4))
            break
        except subprocess.TimeoutExpired:
            pass
        silent = time.monotonic() - last_output[0]
        if stop.is_set() or silent >= stall_seconds:
            if stop.is_set():
                reason = "interrompue"
                output.message("Arrêt demandé : arrêt de ce pays")
            else:
                reason = "bloquée"
                output.message(f"Aucune sortie depuis {silent / 60:.1f} min : étape bloquée (navigateur figé ?) - arrêt")
            killed = _kill_step(proc.pid, step.profile_dir)
            output.message(f"{killed} processus arrêté(s) (script de ce pays + son Chrome)")
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=KILL_GRACE)
            break
    for reader in readers:
        reader.join(timeout=KILL_GRACE)
    return (proc.returncode, reason) if reason == "terminée" else (None, reason)


def country_log_file(code: str, cycle_start: datetime, log_dir: Path | None = None) -> Path:
    return (log_dir or COUNTRY_LOG_DIR) / code / f"{cycle_start:%Y-%m-%d}.log"


def cleanup_country_logs(log_dir: Path | None = None, keep_days: int = COUNTRY_LOG_KEEP_DAYS) -> None:
    limit = time.time() - keep_days * 86400
    for path in (log_dir or COUNTRY_LOG_DIR).glob("*/*.log"):
        with suppress(OSError):
            if path.stat().st_mtime < limit:
                path.unlink()


def _minutes_text(minutes: float) -> str:
    return f"{int(minutes // 60)} h {int(minutes % 60):02d}" if minutes >= 60 else f"{minutes:.1f} min"


def run_step(
    step: Step, stall_seconds: float, stop: threading.Event | None = None, env_extra: dict[str, str] | None = None,
    low_priority: bool = True, cycle_start: datetime | None = None, log_dir: Path | None = None,
    mirror: bool = True,
) -> StepResult:
    stop = stop or threading.Event()
    start = now()
    log_file = country_log_file(step.code, cycle_start or start, log_dir)
    result = StepResult(status="erreur", start=start.isoformat(timespec="seconds"),
                        profile=step.profile_dir.name if step.profile_dir else "")
    with suppress(ValueError):
        result.log_file = str(log_file.relative_to(PROJECT_ROOT))
    result.log_file = result.log_file or str(log_file)
    output = StepOutput(step.code, log_file, mirror)
    began = time.monotonic()
    try:
        for attempt in range(1, STALL_RETRIES + 2):
            result.attempts = attempt
            output.message(f"Début : {step.label} ({' '.join(step.cmd[1:])})"
                           + (f" - relance {attempt - 1}/{STALL_RETRIES}" if attempt > 1 else ""))
            attempt_began = time.monotonic()
            code, reason = _run_watched(step, output, stall_seconds, stop, env_extra, low_priority)
            duration = (time.monotonic() - attempt_began) / 60
            if reason == "interrompue":
                result.status = "interrompue"
                break
            if code is not None:
                result.exit_code = code
                result.status = "ok" if code == 0 else "erreur"
                output.message(f"Fin : {step.label} — code {code} en {_minutes_text(duration)}")
                break
            output.message(f"Étape arrêtée pour blocage après {_minutes_text(duration)}")
        else:
            result.status = "bloquée"
            output.message(f"Abandon : {step.label} bloquée {STALL_RETRIES + 1} fois de suite")
    finally:
        result.minutes = round((time.monotonic() - began) / 60, 1)
        result.end = now().isoformat(timespec="seconds")
        output.close()
    return result


def memory_check(min_free_gb: float) -> Callable[[], tuple[bool, str]]:
    """Mémoire disponible suffisante pour un pays de plus (jamais consultée pour le premier pays)."""
    def check() -> tuple[bool, str]:
        if min_free_gb <= 0:
            return True, ""
        available = psutil.virtual_memory().available / 2 ** 30
        return available >= min_free_gb, f"mémoire disponible {available:.1f} Go < {min_free_gb:g} Go"
    return check


def run_parallel(
    cycle: list[Step], max_parallel: int, runner: Callable[[Step], StepResult],
    resources_ok: Callable[[], tuple[bool, str]] = lambda: (True, ""), stop: threading.Event | None = None,
    poll: float = SCHEDULER_POLL,
) -> dict[str, StepResult]:
    """
    Au plus max_parallel pays à la fois, jamais deux sur le même profil ; dès qu'un pays finit, le premier pays en
    attente dont le profil est libre démarre (ordre de `cycle`). Un pays en échec (exception comprise) n'arrête pas les
    autres. Ctrl+C : `stop` posé, chaque pays arrête son script et son Chrome, puis l'exception remonte.
    """
    max_parallel = max(1, max_parallel)
    stop = stop or threading.Event()
    pending = list(cycle)
    running: dict[Future[StepResult], Step] = {}
    busy: set[str] = set()
    results: dict[str, StepResult] = {}
    waiting_for_resources = False
    pool = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="pays")
    try:
        while pending or running:
            while pending and len(running) < max_parallel:
                step = next((s for s in pending if s.profile_key not in busy), None)
                if step is None:
                    break  # profils des pays en attente occupés : attendre qu'un pays finisse
                if running:
                    ok, why = resources_ok()
                    if not ok:
                        if not waiting_for_resources:
                            log(f"{step.code} attend avant de démarrer : {why}")
                        waiting_for_resources = True
                        break
                waiting_for_resources = False
                pending.remove(step)
                busy.add(step.profile_key)
                running[pool.submit(runner, step)] = step
                log(f"Démarrage {step.code} ({len(running)}/{max_parallel} en cours, {len(pending)} en attente)")
            if not running:
                break
            done, _ = wait(list(running), timeout=poll, return_when=FIRST_COMPLETED)
            for future in done:
                step = running.pop(future)
                busy.discard(step.profile_key)
                try:
                    results[step.code] = future.result()
                except Exception:  # ne doit pas arriver (run_step attrape tout) ; jamais fatal pour les autres pays
                    log(f"[ERREUR] {step.code} : {traceback.format_exc().strip()}")
                    results[step.code] = StepResult(status="erreur")
                log(f"Terminé {step.code} : {results[step.code].status} "
                    f"({len(running)} en cours, {len(pending)} en attente)")
    except BaseException:
        stop.set()
        raise
    finally:
        pool.shutdown(wait=True)  # après stop : chaque pays arrête aussitôt son script
    for step in pending:
        results.setdefault(step.code, StepResult(status="non lancée"))
    return results


def load_runs(path: Path | None = None) -> list[dict[str, Any]]:
    try:
        data = json.loads((path or RUNS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    cycles = data.get("cycles") if isinstance(data, dict) else None
    return cycles if isinstance(cycles, list) else []


def last_durations(cycles: list[dict[str, Any]]) -> dict[str, float]:
    """Durée la plus récente (minutes) de chaque pays : un pays interrompu ou non lancé n'est pas une mesure."""
    durations: dict[str, float] = {}
    for cycle in cycles:
        for code, entry in (cycle.get("countries") or {}).items():
            if isinstance(entry, dict) and entry.get("status") not in ("interrompue", "non lancée"):
                with suppress(TypeError, ValueError):
                    durations[code] = float(entry["minutes"])
    return durations


def order_steps(cycle: list[Step], durations: dict[str, float]) -> list[Step]:
    """Le plus long d'abord (fin de cycle au plus tôt) ; jamais mesuré = en tête ; égalité : ordre d'origine."""
    ranked = sorted(enumerate(cycle), key=lambda item: (-durations.get(item[1].code, math.inf), item[0]))
    return [step for _index, step in ranked]


def _document_count(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return len(data) if isinstance(data, (dict, list)) else None


def save_cycle(
    start: datetime, end: datetime, results: dict[str, StepResult], max_parallel: int,
    path: Path | None = None, pages_dir: Path | None = None,
) -> dict[str, Any]:
    """Bilan centralisé du cycle : statut, durée, journal, offres et entreprises (JSON consolidés) de chaque pays."""
    path = path or RUNS_FILE
    pages_dir = pages_dir or PAGES_OUTPUT_DIR
    countries: dict[str, Any] = {}
    for code, result in results.items():
        entry = asdict(result)
        entry["jobs"] = _document_count(pages_dir / f"jobs_{code}.json")
        entry["companies"] = _document_count(pages_dir / f"companies_{code}.json")
        countries[code] = entry
    cycle = {
        "start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
        "minutes": round((end - start).total_seconds() / 60, 1), "max_parallel": max_parallel, "countries": countries,
    }
    cycles = [*load_runs(path), cycle][-RUNS_KEEP:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"cycles": cycles}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return cycle


def log_cycle_summary(cycle: dict[str, Any]) -> None:
    log(f"Bilan du cycle : {_minutes_text(cycle['minutes'])} au total, {cycle['max_parallel']} pays au plus en parallèle")
    for code, entry in cycle["countries"].items():
        counts = ""
        if entry.get("jobs") is not None:
            counts = f", {entry['jobs']} offres / {entry.get('companies') or 0} entreprises dans les JSON"
        code_text = f" (code {entry['exit_code']})" if entry.get("exit_code") not in (None, 0) else ""
        log(f"  {code:<14} {entry['status']:<11}{code_text} {_minutes_text(entry['minutes'])}, "
            f"{entry['attempts']} essai(s){counts} - {entry.get('log_file') or '-'}")
    failed = [code for code, entry in cycle["countries"].items() if entry["status"] != "ok"]
    if failed:
        log(f"Pays à vérifier : {', '.join(failed)} (voir leur journal)")


def auto_ocr_threads(max_parallel: int) -> int:
    """La moitié des cœurs pour l'OCR, partagée entre les pays ; le reste pour Chrome, le backend et MongoDB."""
    return max(1, (os.cpu_count() or 2) // (2 * max(1, max_parallel)))


def _log_profiles(cycle: list[Step]) -> None:
    for step in cycle:
        missing = "" if step.profile_dir is None or step.profile_dir.is_dir() else " - INTROUVABLE (pays en échec)"
        log(f"  {step.code:<14} profil {step.profile_dir.name if step.profile_dir else '-'}{missing}")
    for name, codes in scrape_profiles.shared_profiles(
        {step.code: step.profile_dir for step in cycle if step.profile_dir is not None}
    ).items():
        log(f"[INFO] Profil {name} partagé par {', '.join(codes)} : ces pays passent l'un après l'autre "
            f"(un compte Facebook par profil ; voir facebook_script/scrape_profiles.json)")


def run_cycle(
    stall_seconds: float, max_parallel: int = DEFAULT_MAX_PARALLEL, min_free_ram_gb: float = DEFAULT_MIN_FREE_RAM_GB,
    ocr_threads: int = 0, low_priority: bool = True,
) -> dict[str, StepResult]:
    try:
        cycle = order_steps(steps(), last_durations(load_runs()))
    except scrape_profiles.ProfileConfigError as exc:
        # Jamais de repli sur le profil historique : un pays sur le mauvais profil = le mauvais compte Facebook
        log(f"[ERREUR] {exc} - aucun pays lancé ce cycle")
        return {}
    if not cycle:
        log("Aucun pays : ajoutez des liens dans facebook_script/pays/<code>/pages.txt ou groups.txt")
        return {}
    cleanup_country_logs()
    try:
        forced = max(0, int(os.environ.get("FB_OCR_THREADS") or 0))
    except ValueError:
        forced = 0
    threads = forced or ocr_threads or auto_ocr_threads(max_parallel)
    log(f"Cycle : {len(cycle)} pays ({', '.join(step.code for step in cycle)}), {max_parallel} au plus en parallèle, "
        f"OCR {threads} thread(s) par pays, priorité {'basse' if low_priority else 'normale'}")
    _log_profiles(cycle)
    start = now()
    stop = threading.Event()

    def runner(step: Step) -> StepResult:
        return run_step(step, stall_seconds, stop, {"FB_OCR_THREADS": str(threads)}, low_priority, start)

    finished = run_parallel(cycle, max_parallel, runner, memory_check(min_free_ram_gb), stop)
    results = {step.code: finished[step.code] for step in cycle if step.code in finished}  # bilan dans l'ordre du cycle
    try:
        log_cycle_summary(save_cycle(start, now(), results, max_parallel))
    except OSError as exc:
        log(f"[ERREUR] Bilan du cycle non enregistré : {exc}")
    return results


def print_plan(max_parallel: int) -> int:
    """--plan : organisation du prochain cycle (pays, profils, comptes, ordre), sans rien lancer."""
    try:
        mapping = scrape_profiles.load_mapping()
        cycle = order_steps(steps(mapping), last_durations(load_runs()))
    except scrape_profiles.ProfileConfigError as exc:
        print(f"[ERREUR] {exc}")
        return 1
    try:
        accounts = json.loads(scrape_profiles.ACCOUNTS_FILE.read_text(encoding="utf-8")).get("profiles") or {}
    except (OSError, ValueError, AttributeError):
        accounts = {}
    durations = last_durations(load_runs())
    print(f"{len(cycle)} pays actifs, {max_parallel} au plus en parallèle, dans cet ordre :")
    for index, step in enumerate(cycle, 1):
        name = step.profile_dir.name if step.profile_dir else "-"
        state = "ok" if step.profile_dir and step.profile_dir.is_dir() else "INTROUVABLE"
        account = (accounts.get(name) or {}).get("c_user") or "inconnu (enregistré au prochain lancement)"
        last = _minutes_text(durations[step.code]) if step.code in durations else "jamais mesurée"
        print(f"  {index}. {step.code:<14} profil {name:<40} [{state}]  compte Facebook {account}  "
              f"dernière durée {last}")
    shared = scrape_profiles.shared_profiles({s.code: s.profile_dir for s in cycle if s.profile_dir is not None})
    for name, codes in shared.items():
        print(f"\n[ATTENTION] Profil {name} partagé par {', '.join(codes)} : ces pays passent l'un après l'autre.")
        print("  Pour les faire tourner en parallèle, donnez à chacun son profil (son compte Facebook) dans "
              "facebook_script/scrape_profiles.json.")
    used = {s.profile_dir.name.casefold(): s.code for s in cycle if s.profile_dir is not None}
    share = scrape_profiles.share_profiles()
    print("\nProfils Chrome présents à la racine du projet :")
    for directory in sorted(PROJECT_ROOT.glob("my_custom_profile*")):
        if not directory.is_dir():
            continue
        folded = directory.name.casefold()
        if folded in used:
            role = "scraping : " + ", ".join(s.code for s in cycle if s.profile_dir and
                                             s.profile_dir.name.casefold() == folded)
        elif folded in share or folded in scrape_profiles.post_groups_default_profiles():
            role = "publication dans les groupes (post_groups.py) - jamais pour le scraping"
        elif folded.startswith(scrape_profiles.SHARE_PREFIX):
            role = "ancien profil de publication, hors réglages de post_groups.py"
        else:
            role = "non utilisé par run_daily"
        account = (accounts.get(directory.name) or {}).get("c_user")
        print(f"  {directory.name:<42} {role}" + (f" (compte {account})" if account else ""))
    return 0


def next_midnight() -> datetime:
    return datetime.combine(now().date() + timedelta(days=1), datetime.min.time(), tzinfo=ZONE)


def parse_at(value: str) -> dtime:
    """« 19:00 » -> heure du lancement quotidien (--at) ; ValueError si illisible."""
    hours, _, minutes = value.strip().partition(":")
    if not (hours.isdigit() and minutes.isdigit() and len(minutes) == 2):
        raise ValueError(f"heure attendue au format HH:MM, pas « {value} »")
    return dtime(int(hours), int(minutes))  # ValueError si 25:00 ou 19:60


def next_at(at: dtime, after: datetime) -> datetime:
    """Prochain HH:MM strictement après `after` (aujourd'hui s'il n'est pas passé, sinon demain)."""
    candidate = datetime.combine(after.date(), at, tzinfo=after.tzinfo)
    return candidate if candidate > after else candidate + timedelta(days=1)


def load_next_run() -> datetime | None:
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        planned = datetime.fromisoformat(data["next_run"])
    except (OSError, ValueError, KeyError):
        return None
    # Ancien format sans fuseau : heure du fuseau --tz
    return planned.replace(tzinfo=ZONE) if planned.tzinfo is None else planned.astimezone(ZONE)


def save_next_run(next_run: datetime) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(
        json.dumps({"next_run": next_run.isoformat(timespec="seconds")}, indent=2),
        encoding="utf-8",
    )


def wait_until(target: datetime) -> None:
    server = target.astimezone()  # heure du serveur, pour comparer avec les horodatages de PM2
    log(f"Prochain lancement : {target:%Y-%m-%d %H:%M:%S} ({target.tzname()}) "
        f"= {server:%Y-%m-%d %H:%M:%S} heure du serveur")
    while (remaining := (target - now()).total_seconds()) > 0:
        time.sleep(min(remaining, 60))


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("entier >= 1 attendu")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description="Planificateur quotidien du scraper")
    parser.add_argument("--now", action="store_true", help="lancer le premier cycle immédiatement")
    parser.add_argument("--once", action="store_true", help="un seul cycle immédiat puis quitter")
    parser.add_argument("--plan", action="store_true",
                        help="affiche les pays, profils, comptes et l'ordre du prochain cycle, sans rien lancer")
    parser.add_argument("--at", metavar="HH:MM", default=None,
                        help="lancement chaque jour à cette heure (fuseau --tz), même après un redémarrage "
                             "(défaut : minuit au premier lancement, puis 24 h après le début de chaque cycle)")
    parser.add_argument("--tz", default=DEFAULT_TZ,
                        help=f"fuseau des heures (--at, minuit, journal), défaut {DEFAULT_TZ} (heure de Madagascar)")
    parser.add_argument(
        "--stall-minutes", type=float, default=DEFAULT_STALL_MINUTES,
        help=f"arrête et relance un pays qui n'écrit plus rien depuis N minutes (défaut {DEFAULT_STALL_MINUTES})",
    )
    parser.add_argument("--max-parallel", type=_positive_int, default=DEFAULT_MAX_PARALLEL, metavar="N",
                        help=f"pays scrapés en même temps au plus (défaut {DEFAULT_MAX_PARALLEL}) ; deux pays sur le "
                             "même profil Chrome passent toujours l'un après l'autre")
    parser.add_argument("--min-free-ram-gb", type=float, default=DEFAULT_MIN_FREE_RAM_GB, metavar="GO",
                        help=f"mémoire disponible minimale pour démarrer un pays de plus (défaut "
                             f"{DEFAULT_MIN_FREE_RAM_GB:g} ; 0 = pas de contrôle)")
    parser.add_argument("--ocr-threads", type=int, default=0, metavar="N",
                        help="threads OCR par pays (défaut 0 = automatique : la moitié des cœurs partagée entre les "
                             "pays ; la variable FB_OCR_THREADS l'emporte)")
    parser.add_argument("--priority", choices=("low", "normal"), default="low",
                        help="priorité des processus des pays, Chrome compris (défaut low : le backend passe d'abord)")
    args = parser.parse_args()
    stall_seconds = args.stall_minutes * 60
    try:
        at = parse_at(args.at) if args.at else None
    except ValueError as exc:
        parser.error(f"--at : {exc}")
    global ZONE
    try:
        ZONE = schedule_zone(args.tz)
    except ValueError as exc:
        parser.error(f"--tz : {exc}")
    if args.plan:
        return print_plan(args.max_parallel)

    def cycle() -> dict[str, StepResult]:
        return run_cycle(stall_seconds, args.max_parallel, args.min_free_ram_gb, max(0, args.ocr_threads),
                         args.priority == "low")

    if args.once:
        results = cycle()
        return 0 if results and all(result.status == "ok" for result in results.values()) else 1

    if args.now:
        next_run = now()
    elif at:
        current = now()
        planned = load_next_run()
        if planned is not None and current - MISSED_RUN_GRACE < planned <= current:
            # Heure prévue passée sans cycle terminé (serveur ou PM2 arrêté, cycle interrompu) : rattrapage
            log(f"Lancement prévu à {planned:%Y-%m-%d %H:%M} manqué : rattrapage immédiat")
            next_run = current
        else:
            next_run = next_at(at, current)  # heure fixe : ni minuit ni un ancien horaire enregistré
    else:
        next_run = load_next_run() or next_midnight()
    save_next_run(next_run)

    while True:
        wait_until(next_run)
        cycle_start = now()
        cycle()
        cycle_end = now()

        if at:
            # Tous les jours à la même heure : prochain HH:MM après la fin du cycle (un cycle de plus de 24 h saute
            # un jour au lieu de décaler l'horaire)
            next_run = next_at(at, cycle_end)
        else:
            # 24 h après le début du cycle, ou dès maintenant si le cycle a dépassé 24 h
            next_run = max(cycle_start + INTERVAL, cycle_end)
            if next_run == cycle_end:
                retard = cycle_end - (cycle_start + INTERVAL)
                log(f"Cycle trop long ({retard} de retard) — horaire décalé")
        save_next_run(next_run)


if __name__ == "__main__":
    sys.exit(main())

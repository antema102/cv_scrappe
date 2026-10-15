"""
run_daily.py
============
Planificateur quotidien du scraper (tourne en continu sous PM2, voir ecosystem.config.js).

Chaque cycle :
    python facebook_script/pages.py --push --headless -> pages et groupes Facebook, analyse IA, envoi au backend

index.py (sites emploi.xx) et scrapers.emails_website ne font plus partie du cycle : à lancer à la main.
Pour en ajouter une étape, l'ajouter à STEPS ; une étape en échec (code de sortie non nul) n'empêche
pas les suivantes.

Chien de garde : une étape qui n'écrit plus rien pendant --stall-minutes (défaut 30) est considérée
bloquée (navigateur figé : les appels CDP n'ont pas de timeout) — arrêt du script, de sa descendance
(chromedriver, Chrome) et des Chrome de SON profil, puis relance (STALL_RETRIES fois). Jamais de
`taskkill /IM chrome.exe` : les Chrome des autres scripts (gemini, sirene_google_sheets...) ne sont
ni des descendants ni sur ce profil, ils restent intacts. Relancer ne perd rien : tout est en cache.

Le script Facebook tourne sans fenêtre ni terminal : si la session du profil my_custom_profile_facebook
a expiré, il s'arrête (pas d'attente de connexion manuelle) — reconnecter le profil avec
    python facebook_script/pages.py --login
puis laisser le cycle suivant reprendre.

Planification :
    - Premier lancement à minuit (ou tout de suite avec --now).
    - Le lancement suivant est prévu 24 h après le début du cycle.
    - Si le cycle dure plus de 24 h, le lancement suivant a lieu dès la fin du
      cycle, et l'horaire est décalé d'autant pour les jours suivants
      (ex. cycle démarré à 00:00, terminé le lendemain à 02:00 -> relance à 02:00,
      puis tous les jours à 02:00).

L'heure du prochain lancement est sauvegardée dans downloaded_files/daily_schedule.json :
un redémarrage de PM2 ou du serveur ne décale pas la planification.

Utilisation :
    python run_daily.py          # attend minuit pour le premier cycle
    python run_daily.py --now    # premier cycle immédiatement
    python run_daily.py --once   # un seul cycle puis quitte (test)
    python run_daily.py --stall-minutes 45
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

import psutil

PROJECT_ROOT = Path(__file__).resolve().parent
SCHEDULE_FILE = PROJECT_ROOT / "downloaded_files" / "daily_schedule.json"
INTERVAL = timedelta(hours=24)
# Phase silencieuse la plus longue d'un lancement sain : un appel IA (180 s x 4 essais + attentes)
DEFAULT_STALL_MINUTES = 30
STALL_RETRIES = 2
KILL_GRACE = 10

# (libellé, commande, profil Chrome de l'étape : ses Chrome sont arrêtés avec elle si elle se bloque)
STEPS: list[tuple[str, list[str], Path | None]] = [
    (
        "facebook pages et groupes",
        [sys.executable, "facebook_script/pages.py", "--push", "--headless"],
        PROJECT_ROOT / "my_custom_profile_facebook",
    ),
]

_output_lock = threading.Lock()


def log(message: str) -> None:
    with _output_lock:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def _forward(pipe: BinaryIO, sink: BinaryIO, last_output: list[float]) -> None:
    for line in iter(pipe.readline, b""):
        last_output[0] = time.monotonic()
        with _output_lock:
            sink.write(line)
            sink.flush()
    pipe.close()


def _profile_chromes(profile_dir: Path) -> list[psutil.Process]:
    """Chrome lancés avec --user-data-dir=<profile_dir> exactement (jamais un autre profil)."""
    target = os.path.normcase(os.path.abspath(profile_dir))
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        with suppress(psutil.Error, OSError):
            if (proc.info["name"] or "").lower() not in ("chrome.exe", "chrome"):
                continue
            for arg in proc.info["cmdline"] or ():
                if arg.startswith("--user-data-dir="):
                    path = arg.split("=", 1)[1].strip('"')
                    if os.path.normcase(os.path.abspath(path)) == target:
                        found.append(proc)
                    break
    return found


def _kill_step(pid: int, profile_dir: Path | None) -> int:
    """L'étape, sa descendance (chromedriver, Chrome) et les Chrome de son profil, eux seuls."""
    victims: dict[int, psutil.Process] = {}
    with suppress(psutil.Error):
        root = psutil.Process(pid)
        for proc in [*root.children(recursive=True), root]:
            victims[proc.pid] = proc
    if profile_dir is not None:
        for proc in _profile_chromes(profile_dir):
            victims[proc.pid] = proc
    for proc in victims.values():
        with suppress(psutil.Error):
            proc.terminate()
    _gone, alive = psutil.wait_procs(list(victims.values()), timeout=KILL_GRACE)
    for proc in alive:
        with suppress(psutil.Error):
            proc.kill()
    psutil.wait_procs(alive, timeout=KILL_GRACE)
    return len(victims)


def _run_watched(cmd: list[str], profile_dir: Path | None, stall_seconds: float) -> int | None:
    """Code de sortie de l'étape, ou None si elle a été arrêtée faute de sortie pendant stall_seconds."""
    # Sortie non tamponnée : sinon le chien de garde prendrait un print en attente pour un blocage
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    # stdin fermé : sous PM2 personne ne peut répondre à un input() (connexion manuelle Facebook)
    proc = subprocess.Popen(
        cmd, cwd=PROJECT_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    last_output = [time.monotonic()]
    readers = [
        threading.Thread(target=_forward, args=(proc.stdout, sys.stdout.buffer, last_output), daemon=True),
        threading.Thread(target=_forward, args=(proc.stderr, sys.stderr.buffer, last_output), daemon=True),
    ]
    for reader in readers:
        reader.start()
    while proc.poll() is None:
        silent = time.monotonic() - last_output[0]
        if silent >= stall_seconds:
            log(f"Aucune sortie depuis {silent / 60:.1f} min : étape bloquée (navigateur figé ?) - arrêt")
            log(f"{_kill_step(proc.pid, profile_dir)} processus arrêté(s) (script + son Chrome)")
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=KILL_GRACE)
            for reader in readers:
                reader.join(timeout=KILL_GRACE)
            return None
        time.sleep(min(5.0, stall_seconds / 4))
    for reader in readers:
        reader.join(timeout=KILL_GRACE)
    return proc.returncode


def next_midnight() -> datetime:
    return datetime.combine(datetime.now().date() + timedelta(days=1), datetime.min.time())


def load_next_run() -> datetime | None:
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["next_run"])
    except (OSError, ValueError, KeyError):
        return None


def save_next_run(next_run: datetime) -> None:
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(
        json.dumps({"next_run": next_run.isoformat(timespec="seconds")}, indent=2),
        encoding="utf-8",
    )


def wait_until(target: datetime) -> None:
    log(f"Prochain lancement : {target:%Y-%m-%d %H:%M:%S}")
    while (remaining := (target - datetime.now()).total_seconds()) > 0:
        time.sleep(min(remaining, 60))


def run_step(label: str, cmd: list[str], profile_dir: Path | None, stall_seconds: float) -> None:
    for attempt in range(1, STALL_RETRIES + 2):
        log(f"Début : {label} ({' '.join(cmd[1:])})" + (f" - relance {attempt - 1}/{STALL_RETRIES}" if attempt > 1 else ""))
        start = time.monotonic()
        code = _run_watched(cmd, profile_dir, stall_seconds)
        duration = (time.monotonic() - start) / 60
        if code is not None:
            log(f"Fin : {label} — code {code} en {duration:.1f} min")
            return
        log(f"Étape arrêtée pour blocage après {duration:.1f} min")
    log(f"Abandon : {label} bloquée {STALL_RETRIES + 1} fois de suite — étape suivante")


def run_cycle(stall_seconds: float) -> None:
    for label, cmd, profile_dir in STEPS:
        run_step(label, cmd, profile_dir, stall_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Planificateur quotidien du scraper")
    parser.add_argument("--now", action="store_true", help="lancer le premier cycle immédiatement")
    parser.add_argument("--once", action="store_true", help="un seul cycle immédiat puis quitter")
    parser.add_argument(
        "--stall-minutes", type=float, default=DEFAULT_STALL_MINUTES,
        help=f"arrête et relance une étape qui n'écrit plus rien depuis N minutes (défaut {DEFAULT_STALL_MINUTES})",
    )
    args = parser.parse_args()
    stall_seconds = args.stall_minutes * 60

    if args.once:
        run_cycle(stall_seconds)
        return 0

    if args.now:
        next_run = datetime.now()
    else:
        next_run = load_next_run() or next_midnight()
    save_next_run(next_run)

    while True:
        wait_until(next_run)
        cycle_start = datetime.now()
        run_cycle(stall_seconds)
        cycle_end = datetime.now()

        # 24 h après le début du cycle, ou dès maintenant si le cycle a dépassé 24 h
        next_run = max(cycle_start + INTERVAL, cycle_end)
        if next_run == cycle_end:
            retard = cycle_end - (cycle_start + INTERVAL)
            log(f"Cycle trop long ({retard} de retard) — horaire décalé")
        save_next_run(next_run)


if __name__ == "__main__":
    sys.exit(main())

"""
run_daily.py
============
Planificateur quotidien du scraper (tourne en continu sous PM2, voir ecosystem.config.js).

Chaque cycle, pour chaque pays qui a des liens (facebook_script/pays/<code>/pages.txt ou groups.txt),
Madagascar d'abord puis les autres, L'UN APRÈS L'AUTRE (même profil
Chrome, jamais deux à la fois) :
    python facebook_script/pages.py --push --headless --country <code> -> pages et groupes Facebook, analyse IA,
                                                                        envoi au backend
La liste des pays est relue à chaque cycle : créer le fichier d'un nouveau pays suffit, sans redémarrer PM2.
Un pays en échec n'empêche pas les suivants.

index.py (sites emploi.xx) et scrapers.emails_website ne font plus partie du cycle : à lancer à la main.
Pour en ajouter une étape, l'ajouter à steps() ; une étape en échec (code de sortie non nul) n'empêche
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
    - Avec --at HH:MM (PM2 : --at 19:00) : chaque jour à cette heure, même après un redémarrage (un cycle de
      plus de 24 h saute un jour au lieu de décaler l'horaire). Lancement manqué depuis moins de
      MISSED_RUN_GRACE (serveur ou PM2 arrêté à l'heure prévue, cycle interrompu) : rattrapé au démarrage.
    - Heures dans le fuseau --tz (défaut Indian/Antananarivo, heure de Madagascar), quel que soit celui du
      serveur (Astrakhan, UTC+4). Ne jamais passer par la variable d'environnement TZ : sous Windows, Python
      ne comprend pas un nom IANA et se met en UTC (bug du 25/11/2026 : « 19:00 » attendu à 22 h à Madagascar).
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
    python run_daily.py --at 19:00 --tz Indian/Antananarivo
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
from datetime import datetime, timedelta, timezone, tzinfo
from datetime import time as dtime
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psutil

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "facebook_script"))
from countries import active_countries  # noqa: E402

FACEBOOK_PROFILE = PROJECT_ROOT / "my_custom_profile_facebook"
SCHEDULE_FILE = PROJECT_ROOT / "downloaded_files" / "daily_schedule.json"
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


def steps() -> list[tuple[str, list[str], Path | None]]:
    """(libellé, commande, profil Chrome de l'étape : ses Chrome sont arrêtés avec elle si elle se bloque)."""
    return [
        (
            f"facebook pages et groupes - {country.name}",
            [sys.executable, "facebook_script/pages.py", "--push", "--headless", "--country", country.code],
            FACEBOOK_PROFILE,
        )
        for country in active_countries("pages", "groups")
    ]

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
    cycle = steps()
    if not cycle:
        log("Aucun pays : ajoutez des liens dans facebook_script/pays/<code>/pages.txt ou groups.txt")
    for label, cmd, profile_dir in cycle:
        run_step(label, cmd, profile_dir, stall_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Planificateur quotidien du scraper")
    parser.add_argument("--now", action="store_true", help="lancer le premier cycle immédiatement")
    parser.add_argument("--once", action="store_true", help="un seul cycle immédiat puis quitter")
    parser.add_argument("--at", metavar="HH:MM", default=None,
                        help="lancement chaque jour à cette heure (fuseau --tz), même après un redémarrage "
                             "(défaut : minuit au premier lancement, puis 24 h après le début de chaque cycle)")
    parser.add_argument("--tz", default=DEFAULT_TZ,
                        help=f"fuseau des heures (--at, minuit, journal), défaut {DEFAULT_TZ} (heure de Madagascar)")
    parser.add_argument(
        "--stall-minutes", type=float, default=DEFAULT_STALL_MINUTES,
        help=f"arrête et relance une étape qui n'écrit plus rien depuis N minutes (défaut {DEFAULT_STALL_MINUTES})",
    )
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

    if args.once:
        run_cycle(stall_seconds)
        return 0

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
        run_cycle(stall_seconds)
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

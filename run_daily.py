"""
run_daily.py
============
Planificateur quotidien du scraper (tourne en continu sous PM2, voir ecosystem.config.js).

Chaque cycle :
    1. python index.py                     -> scrape les offres et les entreprises
    2. python -m scrapers.emails_website   -> dès la fin de l'étape 1, extrait les emails

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
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SCHEDULE_FILE = PROJECT_ROOT / "downloaded_files" / "daily_schedule.json"
INTERVAL = timedelta(hours=24)

STEPS: list[tuple[str, list[str]]] = [
    ("offres d'emploi", [sys.executable, "index.py"]),
    ("emails entreprises", [sys.executable, "-m", "scrapers.emails_website"]),
]


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


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


def run_cycle() -> None:
    for label, cmd in STEPS:
        log(f"Début : {label} ({' '.join(cmd[1:])})")
        start = time.monotonic()
        code = subprocess.call(cmd, cwd=PROJECT_ROOT)
        duration = (time.monotonic() - start) / 60
        log(f"Fin : {label} — code {code} en {duration:.1f} min")


def main() -> int:
    parser = argparse.ArgumentParser(description="Planificateur quotidien du scraper")
    parser.add_argument("--now", action="store_true", help="lancer le premier cycle immédiatement")
    parser.add_argument("--once", action="store_true", help="un seul cycle immédiat puis quitter")
    args = parser.parse_args()

    if args.once:
        run_cycle()
        return 0

    if args.now:
        next_run = datetime.now()
    else:
        next_run = load_next_run() or next_midnight()
    save_next_run(next_run)

    while True:
        wait_until(next_run)
        cycle_start = datetime.now()
        run_cycle()
        cycle_end = datetime.now()

        # 24 h après le début du cycle, ou dès maintenant si le cycle a dépassé 24 h
        next_run = max(cycle_start + INTERVAL, cycle_end)
        if next_run == cycle_end:
            retard = cycle_end - (cycle_start + INTERVAL)
            log(f"Cycle trop long ({retard} de retard) — horaire décalé")
        save_next_run(next_run)


if __name__ == "__main__":
    sys.exit(main())

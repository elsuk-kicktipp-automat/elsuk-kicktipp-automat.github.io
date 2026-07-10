"""Lädt die Projekt-Konfiguration aus config.yaml; zentrale Daten-Pfade.

Datenfluss (Fairness-Mechanismus, siehe concept.md §5):

    data/predictions/  Klartext-Tipps (gitignored! nie vor Anstoß öffentlich)
    data/matchdays/    öffentlich: erst nur Hashes ("versiegelt"), nach Anstoß
                       der Klartext samt Salt ("enthüllt")
    data/sealed/       Fernet-verschlüsselte Klartext-Tipps (Schlüssel nur als
                       GitHub Actions Secret bzw. lokale .env)
    data/results/      Punkteabrechnung der enthüllten Tipps
    data/manual_results/ manuelle Ergebnis-Overrides, falls die Quelle hängt
    data/backtests/    Backtest-Reports
    data/kombi/        Paper-Kombiwetten: öffentliches JSON (erst Hash, nach
                       letztem Anstoß Klartext) + verschlüsseltes .enc daneben
    data/cache/        API-Antworten (gitignored)
"""

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
PREDICTIONS_DIR = DATA_DIR / "predictions"
MATCHDAYS_DIR = DATA_DIR / "matchdays"
SEALED_DIR = DATA_DIR / "sealed"
RESULTS_DIR = DATA_DIR / "results"
MANUAL_RESULTS_DIR = DATA_DIR / "manual_results"
BACKTESTS_DIR = DATA_DIR / "backtests"
MAPPINGS_DIR = DATA_DIR / "mappings"
KOMBI_DIR = DATA_DIR / "kombi"


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimaler .env-Loader (nur KEY=VALUE-Zeilen); ENV-Variablen haben Vorrang."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

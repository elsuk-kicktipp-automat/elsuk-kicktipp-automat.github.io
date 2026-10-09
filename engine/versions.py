"""Versionshistorie der Methode (data/versions.json).

Die Datei wird von Hand gepflegt: ein Eintrag je Änderung daran, wie Tipp,
Begründung oder Wette entstehen. predict.py schreibt die jeweils letzte Version
an jeden Tipp (factors.method_version), die Website zeigt Liste und Version.
"""

import json

from .config import DATA_DIR

VERSIONS_PATH = DATA_DIR / "versions.json"


def load_versions() -> list[dict]:
    return json.loads(VERSIONS_PATH.read_text(encoding="utf-8"))["versions"]


def current_version() -> int:
    return load_versions()[-1]["version"]

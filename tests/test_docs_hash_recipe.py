"""Das Nachrechen-Rezept auf der Website muss zu engine/seal.py passen.

Hintergrund: Als paper_bet zu HASHED_FIELDS dazukam, wurde das handgeschriebene
Beispiel auf der Erklärseite nicht nachgezogen. Wer der Anleitung folgte, bekam
einen anderen Hash - und hätte schließen müssen, der Automat lüge. Der
Fairness-Beweis ist der Kern des Projekts; eine falsche Anleitung entwertet ihn.

Die Website kann den Hash nicht selbst nachrechnen (JavaScript verliert beim
JSON-Parsen die Unterscheidung 0.0/0, und Python sortiert Schlüssel rekursiv),
deshalb wird die Doku hier gegen den Code geprüft statt umgekehrt.
"""

import re
from pathlib import Path

from engine.seal import HASHED_FIELDS

SEITE = Path(__file__).resolve().parent.parent / "site" / "src" / "pages" / "wie-ich-denke.astro"


def _feldliste() -> list[str]:
    """Die FELDER-Tupel-Literale aus dem Code-Beispiel der Seite."""
    text = SEITE.read_text(encoding="utf-8")
    block = re.search(r"FELDER = \((.*?)\)", text, re.S)
    assert block, "Kein FELDER-Tupel im Beispiel auf wie-ich-denke.astro gefunden"
    return re.findall(r'"([a-z_]+)"', block.group(1))


class TestHashRezept:
    def test_beispiel_nennt_alle_gehashten_felder(self):
        fehlend = set(HASHED_FIELDS) - set(_feldliste())
        assert not fehlend, (
            f"Diese Felder gehen in den Hash, fehlen aber im Beispiel auf der "
            f"Website: {sorted(fehlend)}. Wer der Anleitung folgt, bekommt einen "
            f"falschen Hash."
        )

    def test_beispiel_erfindet_keine_felder(self):
        zuviel = set(_feldliste()) - set(HASHED_FIELDS)
        assert not zuviel, (
            f"Das Beispiel nennt Felder, die gar nicht gehasht werden: "
            f"{sorted(zuviel)}."
        )

    def test_kanonische_form_ist_dokumentiert(self):
        """sort_keys und die kompakten Trenner entscheiden über den Hash."""
        text = SEITE.read_text(encoding="utf-8")
        assert "sort_keys=True" in text
        assert 'separators=(",", ":")' in text
        assert "ensure_ascii=False" in text

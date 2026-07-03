"""Team-Identität über normalisierte Namen.

OpenLigaDB-Community-Ligen vergeben keine stabilen Team-IDs (bei der WM 2026
haben Gruppenphase und K.o.-Runde teils unterschiedliche IDs und Schreibweisen,
z.B. "Bosnien-Herzegowina" vs. "Bosnien und Herzegowina"). Deshalb ist der
normalisierte Teamname der Schlüssel im gesamten System.
"""

import re
import unicodedata

_STOPWORDS = {"und", "and"}

PLACEHOLDER_PREFIXES = ("sieger", "verlierer", "tbd", "n.n")

# OpenLigaDB-Rundennamen der K.o.-Runde ('mb'-Liga). Die Gruppenphase (Liga
# 'wm2026') heißt "1. Runde".."3. Runde" und fällt hier nicht rein.
KNOCKOUT_STAGES = {
    "Sechzehntelfinale",
    "Achtelfinale",
    "Viertelfinale",
    "Halbfinale",
    "Spiel um Platz 3",
    "Finale",
}


def normalize(name: str) -> str:
    """'Bosnien und Herzegowina' / 'Bosnien-Herzegowina' -> 'bosnienherzegowina',
    Diakritika werden gefaltet ('Curaçao' -> 'curacao')."""
    folded = unicodedata.normalize("NFKD", name.lower().replace("ß", "ss"))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    words = re.split(r"[^a-z0-9]+", folded)
    return "".join(w for w in words if w and w not in _STOPWORDS)


def is_placeholder(name: str) -> bool:
    """Platzhalter in K.o.-Plänen wie 'Sieger SF 12' erkennen."""
    return name.lower().startswith(PLACEHOLDER_PREFIXES)


def is_knockout_stage(stage_name: str) -> bool:
    """K.o.-Spiel? Dort kann bei einem Remis-Tipp zusätzlich ein Elfmeterschießen-
    Sieger gefragt sein (in der Gruppenphase zählt ein Unentschieden final)."""
    return stage_name in KNOCKOUT_STAGES

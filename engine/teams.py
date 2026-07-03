"""Team-Identität über normalisierte Namen.

Die aktive WM-2026-Liga ('wm26') vergibt zwar durchgehend stabile Team-IDs,
der normalisierte Name bleibt trotzdem der Schlüssel – für andere Community-
Ligen (z.B. Vereins-Wettbewerbe) sind Namensvarianten wie "Bosnien-Herzegowina"
vs. "Bosnien und Herzegowina" weiterhin die Norm.
"""

import re
import unicodedata

_STOPWORDS = {"und", "and"}

PLACEHOLDER_PREFIXES = ("sieger", "verlierer", "tbd", "n.n")
# K.o.-Plan-Platzhalter der wm26-Liga, z.B. "ARG/CPV" (Sieger Argentinien/Kap
# Verde), statt "Sieger SF 12" wie bei der früheren 'mb'-Liga
PLACEHOLDER_PATTERN = re.compile(r"^[A-Z0-9]{2,4}(/[A-Z0-9]{2,4})+$")

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
    """Platzhalter in K.o.-Plänen wie 'Sieger SF 12' oder 'ARG/CPV' erkennen."""
    return name.lower().startswith(PLACEHOLDER_PREFIXES) or bool(PLACEHOLDER_PATTERN.match(name))


def is_knockout_stage(stage_name: str) -> bool:
    """K.o.-Spiel? Dort kann bei einem Remis-Tipp zusätzlich ein Elfmeterschießen-
    Sieger gefragt sein (in der Gruppenphase zählt ein Unentschieden final)."""
    return stage_name in KNOCKOUT_STAGES

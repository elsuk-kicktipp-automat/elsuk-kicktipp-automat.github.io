"""Kicktipp-Punktelogik und Erwartungswert-Optimierung.

Kernidee (siehe concept.md, Schicht 2): Nicht das wahrscheinlichste Ergebnis
tippen, sondern den Tipp mit dem höchsten Punkte-Erwartungswert unter dem
Punkteschema der Runde:

    E[Punkte(Tipp)] = Summe über alle Ergebnisse: P(Ergebnis) * Punkte(Tipp, Ergebnis)

Kicktipp-Standard: Bei Unentschieden gibt es keine Tordifferenz-Punkte – ein
nicht-exakt getipptes Remis (1:1 statt 2:2) zählt nur als richtige Tendenz.
"""

import numpy as np

DEFAULT_SCHEME = {"exact": 4, "goal_diff": 3, "tendency": 2}


def match_points(tip: tuple[int, int], result: tuple[int, int], scheme: dict = DEFAULT_SCHEME) -> int:
    """Kicktipp-Punkte für einen Tipp gegen das reale Ergebnis (nach 90 Minuten)."""
    tip_h, tip_a = tip
    res_h, res_a = result
    tip_diff, res_diff = tip_h - tip_a, res_h - res_a
    if (tip_h, tip_a) == (res_h, res_a):
        return scheme["exact"]
    if tip_diff == res_diff:
        # Unentschieden: richtige "Differenz" ist nur die richtige Tendenz
        return scheme["tendency"] if tip_diff == 0 else scheme["goal_diff"]
    if np.sign(tip_diff) == np.sign(res_diff):
        return scheme["tendency"]
    return 0


def expected_points(tip: tuple[int, int], matrix: np.ndarray, scheme: dict = DEFAULT_SCHEME) -> float:
    """Punkte-Erwartungswert eines Tipps über die Ergebnis-Wahrscheinlichkeitsmatrix."""
    size = matrix.shape[0]
    total = 0.0
    for res_h in range(size):
        for res_a in range(size):
            p = matrix[res_h, res_a]
            if p > 0:
                total += p * match_points(tip, (res_h, res_a), scheme)
    return total


def best_tip(
    matrix: np.ndarray, scheme: dict = DEFAULT_SCHEME, max_tip_goals: int = 5
) -> tuple[tuple[int, int], float]:
    """Der Tipp mit maximalem Punkte-Erwartungswert: ((heim, gast), erwartungswert)."""
    best, best_ev = (0, 0), -1.0
    for tip_h in range(max_tip_goals + 1):
        for tip_a in range(max_tip_goals + 1):
            ev = expected_points((tip_h, tip_a), matrix, scheme)
            if ev > best_ev:
                best, best_ev = (tip_h, tip_a), ev
    return best, best_ev


def most_probable_score(matrix: np.ndarray) -> tuple[int, int]:
    """Das wahrscheinlichste Einzelergebnis (zur Analyse, nicht als Tipp-Strategie)."""
    h, a = np.unravel_index(np.argmax(matrix), matrix.shape)
    return int(h), int(a)


def elo_favorite_tip(home_elo: float | None, away_elo: float | None) -> tuple[int, int]:
    """Baseline (a): immer 2:1 für den ELO-Favoriten (ohne Ratings: Heimteam)."""
    if home_elo is not None and away_elo is not None and away_elo > home_elo:
        return (1, 2)
    return (2, 1)


ALWAYS_DRAW_TIP = (1, 1)  # Baseline (b): immer 1:1


def penalty_shootout_favorite(probs: dict) -> tuple[str, float]:
    """Wer gewinnt wahrscheinlicher ein Elfmeterschießen? Für einen Remis-Tipp
    im K.o.-Spiel kennt das Modell keine Elfmeterschießen-Statistik; als
    Näherung wird Heim-/Auswärtssieg-Wahrscheinlichkeit ohne das Remis
    renormiert – wer in der regulären Spielzeit knapp vorn lag, gilt auch im
    Elfmeterschießen als knapp favorisiert. Rückgabe: ("home"|"away", Quote)."""
    home, away = probs["home"], probs["away"]
    total = home + away
    p_home = home / total if total > 0 else 0.5
    return ("home", p_home) if p_home >= 0.5 else ("away", 1 - p_home)

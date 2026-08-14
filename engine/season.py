"""Monte-Carlo-Saisonsimulation für die Bonusfragen (Meister, Herbstmeister, Abstieg).

Das Dixon-Coles-Modell liefert pro Paarung eine Wahrscheinlichkeitsmatrix über
alle Ergebnisse. Für eine Tabellenprognose reicht das nicht: Punkte entstehen
erst aus der Kombination aller 306 Spiele, und die Frage "wer wird Meister?"
ist keine Eigenschaft einer einzelnen Partie. Deshalb wird die Restsaison
einige tausend Mal komplett ausgewürfelt - je Simulation ein Ergebnis pro
offenem Spiel, daraus eine Abschlusstabelle - und ausgezählt, wie oft ein
Verein oben bzw. unten landet.

Bereits gespielte Partien gehen mit ihrem echten Ergebnis in JEDE Simulation
ein; die Prognose wird im Saisonverlauf also von selbst schärfer.

Tabellenwertung wie in der Bundesliga: Punkte, dann Tordifferenz, dann
erzielte Tore. Der direkte Vergleich als vierte Stufe ist bewusst NICHT
abgebildet - er würde die Simulation deutlich verteuern und betrifft nur
exakte Gleichstände, die hier ohnehin zufällig aufgelöst werden.
"""

from dataclasses import dataclass

import numpy as np

from .teams import normalize

# Punkte, Tordifferenz, erzielte Tore in EINE Sortierzahl gepackt: die Gewichte
# sind so gewählt, dass eine höhere Stufe jede niedrigere überstimmt
# (Tordifferenz-Offset, weil sie negativ werden kann).
_POINTS_WEIGHT = 1_000_000
_GOAL_DIFF_WEIGHT = 1_000
_GOAL_DIFF_OFFSET = 200


@dataclass(frozen=True)
class SeasonProbabilities:
    """Auszählung über alle Simulationen; Schlüssel sind OpenLigaDB-Teamnamen."""

    teams: list[str]
    champion: dict[str, float]
    autumn_champion: dict[str, float]
    bottom_three: dict[str, float]
    expected_points: dict[str, float]
    expected_goals_for: dict[str, float]
    simulations: int
    simulated_matches: int

    def ranked(self, probabilities: dict[str, float]) -> list[tuple[str, float]]:
        """Vereine nach Wahrscheinlichkeit absteigend."""
        return sorted(probabilities.items(), key=lambda kv: (-kv[1], kv[0]))

    def most_likely(self, probabilities: dict[str, float], count: int = 1) -> list[str]:
        return [team for team, _ in self.ranked(probabilities)[:count]]


def table_from_results(matches, upto_matchday: int | None = None) -> list[str] | None:
    """Echte Tabelle aus gespielten Ergebnissen, bester Verein zuerst.

    None, solange noch nicht alle relevanten Spiele abgeschlossen sind - eine
    Zwischentabelle taugt nicht zur Abrechnung. `upto_matchday` grenzt auf die
    Hinrunde ein (Herbstmeister).
    """
    relevant = [
        m for m in matches
        if upto_matchday is None or m.matchday <= upto_matchday
    ]
    if not relevant or not all(m.has_result for m in relevant):
        return None

    stats: dict[str, list[int]] = {}
    for m in relevant:
        home = stats.setdefault(m.home_name, [0, 0, 0])  # Punkte, Differenz, Tore
        away = stats.setdefault(m.away_name, [0, 0, 0])
        home[0] += 3 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 0
        away[0] += 3 if m.away_goals > m.home_goals else 1 if m.home_goals == m.away_goals else 0
        home[1] += m.home_goals - m.away_goals
        away[1] += m.away_goals - m.home_goals
        home[2] += m.home_goals
        away[2] += m.away_goals

    return [
        team
        for team, _ in sorted(stats.items(), key=lambda kv: (-kv[1][0], -kv[1][1], -kv[1][2], kv[0]))
    ]


def _table_scores(points, goal_diff, goals_for, jitter):
    """Sortierzahl je Verein: Punkte > Tordifferenz > Tore, Gleichstand zufällig.

    Der Jitter bleibt unter 1 und kann damit nur exakte Gleichstände auflösen -
    jeder echte Unterschied ist mindestens 1 Tor groß.
    """
    return (
        points * _POINTS_WEIGHT
        + (goal_diff + _GOAL_DIFF_OFFSET) * _GOAL_DIFF_WEIGHT
        + goals_for
        + jitter
    )


def simulate(
    model,
    matches,
    simulations: int = 10000,
    autumn_matchday: int = 17,
    seed: int = 20262027,
) -> SeasonProbabilities:
    """Würfelt die Saison `simulations` Mal aus und zählt die Platzierungen aus.

    `model` muss bereits gefittet sein. Fester Seed: dieselbe Datenlage soll
    dieselbe Prognose ergeben, sonst ist eine versiegelte Antwort nicht
    reproduzierbar nachvollziehbar.
    """
    teams = sorted({m.home_name for m in matches} | {m.away_name for m in matches})
    index = {normalize(t): i for i, t in enumerate(teams)}
    n_teams = len(teams)
    rng = np.random.default_rng(seed)

    points = np.zeros((simulations, n_teams))
    goals_for = np.zeros((simulations, n_teams))
    goals_against = np.zeros((simulations, n_teams))
    autumn_points = np.zeros((simulations, n_teams))
    autumn_goals_for = np.zeros((simulations, n_teams))
    autumn_goals_against = np.zeros((simulations, n_teams))

    simulated = 0
    for match in matches:
        home = index.get(match.home_key)
        away = index.get(match.away_key)
        if home is None or away is None:
            continue  # Platzhalter-Paarung, gehört nicht in die Tabelle

        if match.has_result:
            # Gespielt: fließt mit dem echten Ergebnis in jede Simulation ein.
            home_goals = np.full(simulations, match.home_goals)
            away_goals = np.full(simulations, match.away_goals)
        else:
            matrix = model.score_matrix(match.home_key, match.away_key)
            flat = matrix.ravel()
            draws = rng.choice(flat.size, size=simulations, p=flat / flat.sum())
            home_goals, away_goals = np.divmod(draws, matrix.shape[1])
            simulated += 1

        home_won = home_goals > away_goals
        away_won = away_goals > home_goals
        drawn = home_goals == away_goals
        home_points = np.where(home_won, 3, np.where(drawn, 1, 0))
        away_points = np.where(away_won, 3, np.where(drawn, 1, 0))

        points[:, home] += home_points
        points[:, away] += away_points
        goals_for[:, home] += home_goals
        goals_for[:, away] += away_goals
        goals_against[:, home] += away_goals
        goals_against[:, away] += home_goals

        if match.matchday <= autumn_matchday:
            autumn_points[:, home] += home_points
            autumn_points[:, away] += away_points
            autumn_goals_for[:, home] += home_goals
            autumn_goals_for[:, away] += away_goals
            autumn_goals_against[:, home] += away_goals
            autumn_goals_against[:, away] += home_goals

    jitter = rng.random((simulations, n_teams))
    final = _table_scores(points, goals_for - goals_against, goals_for, jitter)
    autumn = _table_scores(
        autumn_points, autumn_goals_for - autumn_goals_against, autumn_goals_for, jitter
    )

    champion_counts = np.bincount(final.argmax(axis=1), minlength=n_teams)
    autumn_counts = np.bincount(autumn.argmax(axis=1), minlength=n_teams)

    # Plätze 16-18 = die drei schlechtesten Sortierzahlen je Simulation
    bottom = np.argsort(final, axis=1)[:, :3]
    bottom_counts = np.bincount(bottom.ravel(), minlength=n_teams)

    return SeasonProbabilities(
        teams=teams,
        champion={t: champion_counts[i] / simulations for i, t in enumerate(teams)},
        autumn_champion={t: autumn_counts[i] / simulations for i, t in enumerate(teams)},
        bottom_three={t: bottom_counts[i] / simulations for i, t in enumerate(teams)},
        expected_points={t: float(points[:, i].mean()) for i, t in enumerate(teams)},
        expected_goals_for={t: float(goals_for[:, i].mean()) for i, t in enumerate(teams)},
        simulations=simulations,
        simulated_matches=simulated,
    )

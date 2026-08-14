from datetime import datetime, timezone

import numpy as np

from engine.season import simulate, table_from_results
from engine.sources.openligadb import Match


def _match(home, away, matchday, home_goals=None, away_goals=None, day=1):
    return Match(
        home_name=home,
        away_name=away,
        home_goals=home_goals,
        away_goals=away_goals,
        kickoff_utc=datetime(2026, 8, day, 18, 30, tzinfo=timezone.utc),
        matchday=matchday,
        stage_name=f"{matchday}. Spieltag",
        finished=home_goals is not None,
    )


class FakeModel:
    """Liefert eine feste Ergebnismatrix - so ist die Simulation vorhersagbar."""

    def __init__(self, always_home_win=True):
        self.always_home_win = always_home_win

    def score_matrix(self, home_key, away_key):
        matrix = np.zeros((3, 3))
        if self.always_home_win:
            matrix[1, 0] = 1.0  # immer 1:0 für Heim
        else:
            matrix[0, 0] = 1.0  # immer 0:0
        return matrix


class TestTableFromResults:
    def test_orders_by_points_then_goal_difference(self):
        matches = [
            _match("A", "B", 1, 3, 0),  # A: 3 Punkte, +3
            _match("C", "D", 1, 1, 0),  # C: 3 Punkte, +1
            _match("B", "C", 2, 0, 0),  # B und C je 1 Punkt
            _match("D", "A", 2, 0, 0),  # D und A je 1 Punkt
        ]
        assert table_from_results(matches) == ["A", "C", "D", "B"]

    def test_none_while_a_match_is_open(self):
        matches = [_match("A", "B", 1, 2, 0), _match("C", "D", 1)]
        assert table_from_results(matches) is None

    def test_limits_to_matchday(self):
        matches = [
            _match("A", "B", 1, 0, 3),
            _match("C", "D", 1, 0, 1),
            _match("B", "C", 2),  # Spieltag 2 noch offen
        ]
        # B (3 Pkt, +3), D (3 Pkt, +1), dann C (-1) vor A (-3) nach Tordifferenz
        assert table_from_results(matches, upto_matchday=1) == ["B", "D", "C", "A"]
        assert table_from_results(matches) is None


class TestSimulate:
    def _round_robin(self, teams, matchdays=2):
        matches, md = [], 1
        for i, home in enumerate(teams):
            for away in teams[i + 1:]:
                matches.append(_match(home, away, min(md, matchdays)))
                md += 1
        return matches

    def test_probabilities_are_consistent(self):
        matches = self._round_robin(["A", "B", "C", "D"])
        p = simulate(FakeModel(), matches, simulations=200, autumn_matchday=1)
        assert p.simulations == 200
        assert p.simulated_matches == len(matches)
        assert sum(p.champion.values()) == 1.0
        assert sum(p.autumn_champion.values()) == 1.0
        # Genau drei Vereine belegen die letzten drei Plätze
        assert round(sum(p.bottom_three.values()), 6) == 3.0

    def test_played_results_count_in_every_simulation(self):
        # A gewinnt real 5:0, alle anderen Spiele enden 0:0 -> A ist immer Meister
        matches = [
            _match("A", "B", 1, 5, 0),
            _match("C", "D", 1, 0, 0),
            _match("A", "C", 2),
            _match("B", "D", 2),
        ]
        p = simulate(FakeModel(always_home_win=False), matches, simulations=50)
        assert p.champion["A"] == 1.0

    def test_stronger_team_wins_more_often(self):
        class HomeStrong:
            def score_matrix(self, home_key, away_key):
                m = np.zeros((3, 3))
                # A gewinnt zuhause immer, sonst 0:0
                m[1, 0] = 1.0 if home_key == "a" else 0.0
                m[0, 0] = 1.0 - m[1, 0]
                return m

        matches = self._round_robin(["A", "B", "C", "D"])
        p = simulate(HomeStrong(), matches, simulations=100)
        assert p.champion["A"] > p.champion["B"]
        assert p.expected_points["A"] > p.expected_points["B"]

    def test_same_seed_gives_same_answer(self):
        matches = self._round_robin(["A", "B", "C", "D"])
        first = simulate(FakeModel(), matches, simulations=100)
        second = simulate(FakeModel(), matches, simulations=100)
        assert first.champion == second.champion

    def test_placeholder_teams_are_ignored(self):
        matches = self._round_robin(["A", "B", "C"])
        p = simulate(FakeModel(), matches, simulations=50)
        assert set(p.teams) == {"A", "B", "C"}

import numpy as np
import pytest

from engine.optimizer import (
    ALWAYS_DRAW_TIP,
    DEFAULT_SCHEME,
    best_tip,
    elo_favorite_tip,
    expected_points,
    match_points,
    most_probable_score,
    penalty_shootout_favorite,
)


class TestMatchPoints:
    def test_exact_result(self):
        assert match_points((2, 1), (2, 1)) == 4

    def test_correct_goal_diff(self):
        assert match_points((2, 1), (3, 2)) == 3
        assert match_points((2, 1), (1, 0)) == 3

    def test_correct_tendency(self):
        assert match_points((2, 1), (3, 0)) == 2
        assert match_points((0, 1), (1, 3)) == 2

    def test_draw_exact(self):
        assert match_points((1, 1), (1, 1)) == 4

    def test_draw_wrong_score_gives_only_tendency(self):
        # Kicktipp-Standard: bei Unentschieden gibt es keine Tordifferenz-Punkte
        assert match_points((1, 1), (2, 2)) == 2
        assert match_points((0, 0), (3, 3)) == 2

    def test_wrong_tendency(self):
        assert match_points((2, 1), (1, 1)) == 0
        assert match_points((2, 1), (0, 1)) == 0
        assert match_points((1, 1), (2, 1)) == 0

    def test_custom_scheme(self):
        scheme = {"exact": 5, "goal_diff": 3, "tendency": 1}
        assert match_points((2, 0), (2, 0), scheme) == 5
        assert match_points((2, 0), (3, 1), scheme) == 3
        assert match_points((2, 0), (1, 0), scheme) == 1
        assert match_points((1, 1), (0, 0), scheme) == 1


class TestExpectedPoints:
    def test_certain_result(self):
        matrix = np.zeros((7, 7))
        matrix[2, 1] = 1.0
        assert expected_points((2, 1), matrix) == pytest.approx(4.0)
        assert expected_points((1, 0), matrix) == pytest.approx(3.0)
        assert expected_points((3, 0), matrix) == pytest.approx(2.0)
        assert expected_points((0, 0), matrix) == pytest.approx(0.0)

    def test_mixed_distribution(self):
        matrix = np.zeros((7, 7))
        matrix[1, 0] = 0.5
        matrix[3, 1] = 0.5
        # Tipp 1:0 -> 0.5*4 (exakt) + 0.5*2 (Tendenz) = 3.0
        assert expected_points((1, 0), matrix) == pytest.approx(3.0)
        # Tipp 2:1 -> 0.5*3 (Differenz zu 1:0) + 0.5*2 (Tendenz zu 3:1) = 2.5
        assert expected_points((2, 1), matrix) == pytest.approx(2.5)

    def test_draw_tip_on_draw_heavy_matrix(self):
        matrix = np.zeros((7, 7))
        matrix[1, 1] = 0.5
        matrix[2, 2] = 0.5
        # Tipp 1:1 -> 0.5*4 (exakt) + 0.5*2 (Tendenz, keine Differenz-Punkte) = 3.0
        assert expected_points((1, 1), matrix) == pytest.approx(3.0)


class TestBestTip:
    def test_prefers_ev_over_probability(self):
        # 1:1 ist das wahrscheinlichste Einzelergebnis, aber die Heimsieg-Masse
        # (43%) bringt einem Heimsieg-Tipp mehr Erwartungswert als der Tipp
        # auf das Unentschieden (25% Remis-Masse).
        matrix = np.zeros((7, 7))
        matrix[1, 1] = 0.14
        matrix[0, 0] = 0.06
        matrix[2, 2] = 0.05
        matrix[1, 0] = 0.13
        matrix[2, 1] = 0.12
        matrix[2, 0] = 0.09
        matrix[3, 1] = 0.05
        matrix[3, 2] = 0.04
        matrix[0, 1] = 0.12
        matrix[1, 2] = 0.10
        matrix[0, 2] = 0.06
        matrix[1, 3] = 0.04

        assert most_probable_score(matrix) == (1, 1)
        tip, ev = best_tip(matrix)
        assert tip == (1, 0)
        assert ev == pytest.approx(expected_points((1, 0), matrix))
        assert ev > expected_points((1, 1), matrix)

    def test_best_tip_never_worse_than_most_probable(self):
        rng = np.random.default_rng(42)
        for _ in range(20):
            matrix = rng.random((7, 7))
            matrix /= matrix.sum()
            tip, ev = best_tip(matrix, max_tip_goals=6)
            baseline_ev = expected_points(most_probable_score(matrix), matrix)
            assert ev >= baseline_ev

    def test_respects_max_tip_goals(self):
        matrix = np.zeros((7, 7))
        matrix[6, 6] = 1.0
        tip, _ = best_tip(matrix, max_tip_goals=5)
        assert tip[0] <= 5 and tip[1] <= 5
        assert tip[0] == tip[1]  # Unentschieden bleibt die beste Wahl


class TestBaselines:
    def test_elo_favorite(self):
        assert elo_favorite_tip(1900, 1700) == (2, 1)
        assert elo_favorite_tip(1700, 1900) == (1, 2)
        assert elo_favorite_tip(1800, 1800) == (2, 1)  # Gleichstand: Heimteam
        assert elo_favorite_tip(None, None) == (2, 1)  # ohne Ratings: Heimteam

    def test_always_draw(self):
        assert ALWAYS_DRAW_TIP == (1, 1)

    def test_scheme_default(self):
        assert DEFAULT_SCHEME == {"exact": 4, "goal_diff": 3, "tendency": 2}


class TestPenaltyShootoutFavorite:
    def test_home_side_favored(self):
        side, p = penalty_shootout_favorite({"home": 0.355, "draw": 0.371, "away": 0.274})
        assert side == "home"
        assert p == pytest.approx(0.355 / (0.355 + 0.274))

    def test_away_side_favored(self):
        side, p = penalty_shootout_favorite({"home": 0.2, "draw": 0.3, "away": 0.5})
        assert side == "away"
        assert p == pytest.approx(0.5 / 0.7)

    def test_dead_heat_defaults_to_home(self):
        side, p = penalty_shootout_favorite({"home": 0.3, "draw": 0.4, "away": 0.3})
        assert side == "home"
        assert p == pytest.approx(0.5)

    def test_no_win_mass_defaults_to_even_odds(self):
        side, p = penalty_shootout_favorite({"home": 0.0, "draw": 1.0, "away": 0.0})
        assert side == "home"
        assert p == pytest.approx(0.5)

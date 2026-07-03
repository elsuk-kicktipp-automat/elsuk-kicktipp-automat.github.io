import pytest

from engine.evaluate import evaluate_matchday

SCHEME = {"exact": 4, "goal_diff": 3, "tendency": 2}

MATCHDAY = {
    "competition": "wm2026",
    "season": 2026,
    "matchday": 5,
    "stage": "Achtelfinale",
    "model_version": "dixon-coles-elo-1",
    "matches": [
        {"home": "Kanada", "away": "Marokko", "kickoff_utc": "2026-07-04T17:00:00Z",
         "status": "revealed", "tip": [1, 1],
         "shadow_tips": {"most_probable": [1, 1], "elo_favorite": [2, 1], "always_draw": [1, 1]},
         "factors": {"probabilities": {"home": 0.3, "draw": 0.4, "away": 0.3}}},
        {"home": "Paraguay", "away": "Frankreich", "kickoff_utc": "2026-07-04T21:00:00Z",
         "status": "revealed", "tip": [0, 2],
         # Alt-Daten ohne shadow_tips: elo_favorite/always_draw werden abgeleitet
         "factors": {"elo": {"home": 1700.0, "away": 1998.0}}},
        {"home": "Brasilien", "away": "Norwegen", "kickoff_utc": "2026-07-05T20:00:00Z",
         "status": "sealed", "hash": "ab" * 32},
    ],
}

RESULTS = {
    ("kanada", "marokko"): (2, 2),      # Tipp 1:1 -> Tendenz (2), kein Differenz-Punkt
    ("paraguay", "frankreich"): (0, 2), # Tipp 0:2 -> exakt (4)
    ("brasilien", "norwegen"): (1, 0),  # versiegelt -> darf nicht gewertet werden
}


class TestEvaluateMatchday:
    def test_scores_revealed_matches(self):
        report = evaluate_matchday(MATCHDAY, RESULTS, SCHEME)
        assert report["points_total"] == 6
        assert report["matches_scored"] == 2
        assert report["hits"] == {"exact": 1, "goal_diff": 0, "tendency": 1, "miss": 0}

    def test_sealed_matches_stay_open_without_leaking_tip(self):
        report = evaluate_matchday(MATCHDAY, RESULTS, SCHEME)
        sealed_entry = report["matches"][2]
        assert report["matches_open"] == 1
        assert sealed_entry["status"] == "sealed"
        assert "tip" not in sealed_entry
        assert "points" not in sealed_entry

    def test_revealed_without_result_stays_open(self):
        results = {("paraguay", "frankreich"): (0, 2)}
        report = evaluate_matchday(MATCHDAY, results, SCHEME)
        assert report["matches_scored"] == 1
        assert report["matches_open"] == 2


class TestShadowAndCalibration:
    def test_shadow_tippers_are_scored(self):
        report = evaluate_matchday(MATCHDAY, RESULTS, SCHEME)
        # Kanada 2:2: most_probable 1:1 -> 2 (Remis-Tendenz), elo_favorite 2:1 -> 0,
        # always_draw 1:1 -> 2. Paraguay 0:2 (abgeleitet): elo_favorite 1:2 -> 2,
        # always_draw 1:1 -> 0.
        assert report["shadow_points"] == {"most_probable": 2, "elo_favorite": 2, "always_draw": 2}
        assert report["shadow_matches"] == {"most_probable": 1, "elo_favorite": 2, "always_draw": 2}

    def test_brier_score_against_draw_outcome(self):
        report = evaluate_matchday(MATCHDAY, RESULTS, SCHEME)
        kanada = report["matches"][0]
        # Ergebnis 2:2 (Remis): (0.3-0)² + (0.4-1)² + (0.3-0)² = 0.54
        assert kanada["brier"] == pytest.approx(0.54)
        assert report["brier_avg"] == pytest.approx(0.54)  # nur 1 Spiel mit probabilities

    def test_match_without_probabilities_has_no_brier(self):
        report = evaluate_matchday(MATCHDAY, RESULTS, SCHEME)
        assert "brier" not in report["matches"][1]

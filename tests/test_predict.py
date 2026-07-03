from datetime import datetime, timezone

import numpy as np
import pytest

from engine.predict import (
    build_begruendung,
    build_model,
    load_elo,
    marginal_expected_goals,
    outcome_probabilities,
    resolve_l2_penalty,
)
from engine.sources.openligadb import Match

MODEL_CFG = {
    "time_decay_xi": 0.002,
    "l2_penalty": {"club": 0.2, "national": 5.0},
    "max_goals": 6,
    "max_tip_goals": 5,
    "elo": {"enabled": True, "beta_prior": 0.15, "beta_penalty": 50.0},
}


class TestOutcomeProbabilities:
    def test_sums_to_one_and_splits_correctly(self):
        matrix = np.zeros((3, 3))
        matrix[1, 0] = 0.5   # Heimsieg
        matrix[1, 1] = 0.3   # Remis
        matrix[0, 2] = 0.2   # Auswärtssieg
        probs = outcome_probabilities(matrix)
        assert probs["home"] == pytest.approx(0.5)
        assert probs["draw"] == pytest.approx(0.3)
        assert probs["away"] == pytest.approx(0.2)


class TestMarginalExpectedGoals:
    def test_matches_simple_distribution(self):
        matrix = np.zeros((3, 3))
        matrix[2, 0] = 0.5  # 2:0
        matrix[0, 1] = 0.5  # 0:1
        lam, mu = marginal_expected_goals(matrix)
        assert lam == pytest.approx(1.0)  # 0.5*2 + 0.5*0
        assert mu == pytest.approx(0.5)  # 0.5*0 + 0.5*1


class TestLoadEloResilience:
    def test_network_failure_returns_none_instead_of_raising(self, monkeypatch):
        import requests

        class FailingSource:
            def ratings(self, on_date):
                raise requests.ConnectionError("eloratings.net blockiert gerade")

        monkeypatch.setattr("engine.predict.make_elo_source", lambda team_type: FailingSource())
        config = {"model": {"elo": {"enabled": True}}}
        assert load_elo(config, "national") is None

    def test_disabled_returns_none_without_network_call(self, monkeypatch):
        def fail_if_called(team_type):
            raise AssertionError("make_elo_source darf bei enabled=False nicht aufgerufen werden")

        monkeypatch.setattr("engine.predict.make_elo_source", fail_if_called)
        config = {"model": {"elo": {"enabled": False}}}
        assert load_elo(config, "club") is None


class TestL2PerTeamType:
    def test_dict_resolves_per_team_type(self):
        assert resolve_l2_penalty(MODEL_CFG, "club") == 0.2
        assert resolve_l2_penalty(MODEL_CFG, "national") == 5.0

    def test_plain_float_applies_to_both(self):
        cfg = {**MODEL_CFG, "l2_penalty": 0.1}
        assert resolve_l2_penalty(cfg, "club") == 0.1
        assert resolve_l2_penalty(cfg, "national") == 0.1

    def test_build_model_uses_team_type(self):
        config = {"model": MODEL_CFG}
        assert build_model(config, True, "national").l2_penalty == 5.0
        assert build_model(config, False, "club").l2_penalty == 0.2
        assert build_model(config, True, "national").neutral_venue is True


def _match(home="Australien", away="Ägypten"):
    return Match(
        home_name=home,
        away_name=away,
        home_goals=None,
        away_goals=None,
        kickoff_utc=datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc),
        matchday=4,
        stage_name="Sechzehntelfinale",
        finished=False,
    )


class TestBuildBegruendungAdvanceTip:
    def test_appends_shootout_sentence_when_advance_tip_given(self):
        probs = {"home": 0.355, "draw": 0.371, "away": 0.274}
        advance_tip = {"pick": "Australien", "probability": 0.564}
        text = build_begruendung(_match(), 1.18, 1.02, probs, (1, 1), 1.088, advance_tip)
        assert "Elfmeterschießen" in text
        assert "Australien" in text
        assert "56%" in text or "56 %" in text

    def test_no_shootout_sentence_without_advance_tip(self):
        probs = {"home": 0.355, "draw": 0.371, "away": 0.274}
        text = build_begruendung(_match(), 1.18, 1.02, probs, (1, 1), 1.088)
        assert "Elfmeterschießen" not in text

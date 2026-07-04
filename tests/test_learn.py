import pytest

from engine.learn import llm_trust_report, market_weight_report
from engine.predict import apply_llm_adjustment


def _sample(points=2, llm=None, result=(2, 1), raw=None, market=None):
    return {
        "points": points,
        "llm_shadow_points": llm,
        "result": result,
        "raw_probabilities": raw,
        "market": market,
    }


class TestLlmTrust:
    def test_not_trusted_below_min_samples(self):
        samples = [_sample(points=0, llm=4)] * 5  # klar positiv, aber zu wenige
        report = llm_trust_report(samples, min_samples=10)
        assert report["trusted"] is False
        assert report["samples"] == 5
        assert report["points_delta"] == 20

    def test_trusted_with_enough_positive_samples(self):
        samples = [_sample(points=2, llm=3)] * 10
        report = llm_trust_report(samples, min_samples=10)
        assert report["trusted"] is True

    def test_not_trusted_when_costing_points(self):
        samples = [_sample(points=4, llm=0)] * 12
        report = llm_trust_report(samples, min_samples=10)
        assert report["trusted"] is False
        assert report["points_delta"] == -48

    def test_matches_without_adjustment_are_ignored(self):
        samples = [_sample(llm=None)] * 50 + [_sample(points=0, llm=2)] * 3
        report = llm_trust_report(samples, min_samples=10)
        assert report["samples"] == 3


class TestMarketWeight:
    RAW = {"home": 0.2, "draw": 0.3, "away": 0.5}     # Modell tippt auswärts
    MARKET = {"home": 0.7, "draw": 0.2, "away": 0.1}  # Markt tippt heim

    def test_stays_at_default_below_min_samples(self):
        samples = [_sample(raw=self.RAW, market=self.MARKET, result=(2, 0))] * 5
        report = market_weight_report(samples, 0.7, min_samples=20, pseudo_samples=20)
        assert report["applied"] == 0.7
        assert report["best"] is None

    def test_learns_towards_market_when_market_is_right(self):
        # Heimsiege treffen die Markt-, nicht die Modellmeinung -> Optimum bei w=1
        samples = [_sample(raw=self.RAW, market=self.MARKET, result=(2, 0))] * 40
        report = market_weight_report(samples, 0.7, min_samples=20, pseudo_samples=20)
        assert report["best"] == 1.0
        # Pseudo-Count-Regularisierung: (40*1.0 + 20*0.7) / 60 = 0.9
        assert report["applied"] == pytest.approx(0.9)

    def test_learns_towards_model_when_model_is_right(self):
        samples = [_sample(raw=self.RAW, market=self.MARKET, result=(0, 2))] * 40
        report = market_weight_report(samples, 0.7, min_samples=20, pseudo_samples=20)
        assert report["best"] == 0.0
        assert report["applied"] == pytest.approx(40 * 0.0 / 60 + 20 * 0.7 / 60, abs=0.001)

    def test_ignores_samples_without_market_data(self):
        samples = [_sample(raw=None, market=None)] * 100
        report = market_weight_report(samples, 0.7, min_samples=20, pseudo_samples=20)
        assert report["samples"] == 0
        assert report["applied"] == 0.7


class TestApplyLlmAdjustment:
    def test_applies_regular_adjustment(self):
        assert apply_llm_adjustment((2, 1), (1, 1), "1. Runde") == (1, 1)

    def test_rejects_draw_for_knockout(self):
        # "nach Elfmeterschießen": Remis ist bei K.o.-Spielen nie zulässig
        assert apply_llm_adjustment((2, 1), (1, 1), "Viertelfinale") is None

    def test_rejects_identical_tip(self):
        assert apply_llm_adjustment((2, 1), (2, 1), "1. Runde") is None

    def test_allows_non_draw_adjustment_for_knockout(self):
        assert apply_llm_adjustment((2, 1), (1, 0), "Viertelfinale") == (1, 0)

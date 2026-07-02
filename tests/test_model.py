from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import engine.model as model_module
from engine.model import DEFAULT_HOME_ADV, DixonColes, FittedParams
from engine.sources.openligadb import Match

REF_DATE = datetime(2026, 5, 1, tzinfo=timezone.utc)


def make_match(home, away, hg, ag, days_before_ref=100, matchday=1):
    return Match(
        home_name=home,
        away_name=away,
        home_goals=hg,
        away_goals=ag,
        kickoff_utc=REF_DATE - timedelta(days=days_before_ref),
        matchday=matchday,
        stage_name=f"{matchday}. Spieltag",
        finished=True,
    )


def synthetic_season(seed=7):
    """Doppelrunde mit 6 Teams: Team A ist klar am stärksten, Team F am schwächsten."""
    rng = np.random.default_rng(seed)
    strength = {"Team A": 0.5, "Team B": 0.25, "Team C": 0.1,
                "Team D": -0.1, "Team E": -0.25, "Team F": -0.5}
    matches = []
    day = 300
    for _ in range(4):  # 4 Durchgänge für stabilere Schätzung
        for home in strength:
            for away in strength:
                if home == away:
                    continue
                lam = np.exp(0.2 + 0.25 + strength[home] - strength[away] * 0.8)
                mu = np.exp(0.2 + strength[away] - strength[home] * 0.8)
                matches.append(
                    make_match(home, away, rng.poisson(lam), rng.poisson(mu), day)
                )
                day -= 0.1
    return matches


@pytest.fixture(scope="module")
def fitted():
    model = DixonColes(xi=0.0, l2_penalty=0.1)
    model.fit(synthetic_season(), REF_DATE)
    return model


class TestDixonColesFit:
    def test_home_advantage_positive(self, fitted):
        assert fitted.params.home_adv > 0

    def test_recovers_team_order(self, fitted):
        attack = fitted.params.attack
        assert attack["teama"] > attack["teamf"]
        assert attack["teamb"] > attack["teame"]

    def test_expected_goals_favor_stronger_team(self, fitted):
        lam, mu = fitted.expected_goals("teama", "teamf")
        assert lam > mu

    def test_unknown_team_uses_league_average(self, fitted):
        lam, mu = fitted.expected_goals("unbekannt1", "unbekannt2")
        assert 0.5 < lam < 3.0
        assert 0.5 < mu < 3.0
        assert lam > mu  # Heimvorteil bleibt

    def test_rho_within_bounds(self, fitted):
        assert -0.3 <= fitted.params.rho <= 0.3


class TestScoreMatrix:
    def test_sums_to_one(self, fitted):
        matrix = fitted.score_matrix("teama", "teamb")
        assert matrix.sum() == pytest.approx(1.0)
        assert (matrix >= 0).all()

    def test_shape_covers_0_to_6(self, fitted):
        assert fitted.score_matrix("teama", "teamb").shape == (7, 7)

    def test_home_win_more_likely_for_strong_home_team(self, fitted):
        matrix = fitted.score_matrix("teama", "teamf")
        p_home = np.tril(matrix, -1).sum()  # Heimtore > Gasttore
        p_away = np.triu(matrix, 1).sum()
        assert p_home > p_away


class TestNeutralVenueAndElo:
    def test_neutral_venue_disables_home_advantage(self):
        model = DixonColes(xi=0.0, l2_penalty=0.1, neutral_venue=True)
        model.fit(synthetic_season(), REF_DATE)
        assert model.params.home_adv == 0.0

    def test_elo_difference_shifts_expected_goals(self):
        # Wenige Spiele, große ELO-Differenz: ELO muss die Prognose tragen
        matches = [make_match("Team A", "Team B", 1, 1, d) for d in (10, 20, 30)]
        elo = {"teama": 2000.0, "teamb": 1600.0, "teamc": 2000.0, "teamd": 1600.0}
        model = DixonColes(xi=0.0, l2_penalty=0.5, neutral_venue=True)
        model.fit(matches, REF_DATE, elo=elo)
        # Teams ohne eigene Spielhistorie: nur der ELO-Term unterscheidet sie
        lam, mu = model.expected_goals("teamc", "teamd")
        assert lam > mu * 1.5
        assert model.params.elo_beta > 0

    def test_without_elo_beta_is_zero(self, fitted):
        assert fitted.params.elo_beta == 0.0

    def test_empty_training_falls_back_to_priors(self):
        model = DixonColes(neutral_venue=True)
        params = model.fit([], REF_DATE, elo={"teama": 1900.0, "teamb": 1700.0})
        assert params.home_adv == 0.0
        assert params.elo_beta == model.elo_beta_prior
        lam, mu = model.expected_goals("teama", "teamb")
        assert lam > mu  # ELO-Favorit auch ohne ein einziges Trainingsspiel
        assert 0.5 < mu < lam < 3.5

    def test_empty_training_with_home_advantage(self):
        model = DixonColes(neutral_venue=False)
        params = model.fit([], REF_DATE)
        assert params.home_adv == DEFAULT_HOME_ADV

    def test_refit_warmstarts_all_shared_parameters(self, monkeypatch):
        matches = [
            make_match("Team A", "Team B", 1, 0),
            make_match("Team C", "Team A", 0, 2),
        ]
        model = DixonColes(neutral_venue=False)
        model.params = FittedParams(
            attack={"teama": 0.1, "teamb": 0.2, "teamc": 0.3},
            defense={"teama": -0.1, "teamb": -0.2, "teamc": -0.3},
            intercept=0.4,
            home_adv=0.2,
            rho=0.05,
            elo_beta=0.12,
        )
        captured = {}

        def fake_minimize(_nll, x0, method, bounds):
            captured["x0"] = x0.copy()
            return SimpleNamespace(x=x0)

        monkeypatch.setattr(model_module, "minimize", fake_minimize)
        model.fit(matches, REF_DATE, elo={"teama": 1800.0, "teamb": 1700.0, "teamc": 1600.0})

        x0 = captured["x0"]
        assert x0[:3].tolist() == pytest.approx([0.1, 0.2, 0.3])
        assert x0[3:6].tolist() == pytest.approx([-0.1, -0.2, -0.3])
        assert x0[6:10].tolist() == pytest.approx([0.4, 0.2, 0.05, 0.12])


class TestWeighting:
    def test_time_decay_prefers_recent_form(self):
        # Team A war früher schwach (verlor gegen B), zuletzt stark (gewann hoch).
        old = [make_match("Team A", "Team B", 0, 3, 700 + i) for i in range(10)]
        old += [make_match("Team B", "Team A", 3, 0, 750 + i) for i in range(10)]
        recent = [make_match("Team A", "Team B", 3, 0, 10 + i) for i in range(10)]
        recent += [make_match("Team B", "Team A", 0, 3, 30 + i) for i in range(10)]

        no_decay = DixonColes(xi=0.0, l2_penalty=0.1)
        no_decay.fit(old + recent, REF_DATE)
        strong_decay = DixonColes(xi=0.01, l2_penalty=0.1)
        strong_decay.fit(old + recent, REF_DATE)

        # Mit Abklinggewichtung dominiert die junge Form von Team A
        assert (
            strong_decay.params.attack["teama"] - strong_decay.params.attack["teamb"]
            > no_decay.params.attack["teama"] - no_decay.params.attack["teamb"]
        )

    def test_predict_before_fit_raises(self):
        with pytest.raises(ValueError):
            DixonColes().score_matrix("teama", "teamb")

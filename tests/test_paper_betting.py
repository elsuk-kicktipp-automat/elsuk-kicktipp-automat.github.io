import pytest

from engine.paper_betting import build_paper_bet, outcome_from_tip, settle_paper_bet


CFG = {
    "enabled": True,
    "market": "h2h_90min",
    "staking": {
        "bankroll_eur": 1000,
        "kelly_fraction": 0.25,
        "max_stake_eur": 100,
        "min_edge": 0.02,
        "min_stake_eur": 0,
    },
}


class TestOutcomeFromTip:
    def test_maps_tip_to_1x2_selection(self):
        assert outcome_from_tip((2, 1)) == "home"
        assert outcome_from_tip((1, 1)) == "draw"
        assert outcome_from_tip((0, 2)) == "away"


class TestBuildPaperBet:
    def test_recommends_fractional_kelly_stake_for_positive_edge(self):
        bet = build_paper_bet(
            cfg=CFG,
            home="Kanada",
            away="Marokko",
            tip=(2, 1),
            raw_probabilities={"home": 0.55, "draw": 0.25, "away": 0.20},
            market={
                "source": "tipico_de",
                "source_label": "Tipico (DE)",
                "bookmaker_count": 3,
                "odds": {"home": 2.1, "draw": 3.4, "away": 3.5},
            },
        )

        assert bet["status"] == "recommended"
        assert bet["selection"] == "home"
        assert bet["source"] == "tipico_de"
        assert bet["stake_eur"] == pytest.approx(35.23)
        assert bet["expected_value_eur"] == pytest.approx(5.46)

    def test_caps_stake_at_configured_maximum(self):
        bet = build_paper_bet(
            cfg=CFG,
            home="Kanada",
            away="Marokko",
            tip=(2, 1),
            raw_probabilities={"home": 0.80, "draw": 0.10, "away": 0.10},
            market={
                "source": "tipico_de",
                "source_label": "Tipico (DE)",
                "bookmaker_count": 1,
                "odds": {"home": 3.0, "draw": 4.0, "away": 4.0},
            },
        )

        assert bet["stake_eur"] == 100.0

    def test_skips_without_value_but_keeps_transparent_record(self):
        bet = build_paper_bet(
            cfg=CFG,
            home="Kanada",
            away="Marokko",
            tip=(2, 1),
            raw_probabilities={"home": 0.40, "draw": 0.30, "away": 0.30},
            market={
                "source": "market_average",
                "source_label": "Marktdurchschnitt",
                "bookmaker_count": 2,
                "odds": {"home": 2.1, "draw": 3.4, "away": 3.5},
            },
        )

        assert bet["status"] == "skipped_no_value"
        assert bet["stake_eur"] == 0.0
        assert bet["source_label"] == "Marktdurchschnitt"

    def test_missing_odds_returns_no_stake_record(self):
        bet = build_paper_bet(
            cfg=CFG,
            home="Kanada",
            away="Marokko",
            tip=(1, 1),
            raw_probabilities={"home": 0.30, "draw": 0.40, "away": 0.30},
            market=None,
        )

        assert bet["status"] == "missing_odds"
        assert bet["selection"] == "draw"


class TestSettlePaperBet:
    def test_winning_bet_returns_payout_and_profit(self):
        result = settle_paper_bet(
            {"selection": "away", "stake_eur": 10.0, "odds_decimal": 2.4},
            (0, 2),
        )
        assert result == {
            "outcome": "won",
            "stake_eur": 10.0,
            "payout_eur": 24.0,
            "profit_eur": 14.0,
        }

    def test_losing_bet_loses_stake(self):
        result = settle_paper_bet(
            {"selection": "draw", "stake_eur": 10.0, "odds_decimal": 3.2},
            (2, 1),
        )
        assert result["outcome"] == "lost"
        assert result["profit_eur"] == -10.0

    def test_zero_stake_is_skipped(self):
        result = settle_paper_bet(
            {"selection": "home", "stake_eur": 0.0, "odds_decimal": 2.0},
            (2, 1),
        )
        assert result["outcome"] == "skipped"
        assert result["profit_eur"] == 0.0


class TestSettlementUses90MinuteResult:
    """Buchmacher-1X2 wird auf 90 Minuten abgerechnet, nicht auf n.E.-Wertung."""

    BET = {
        "mode": "paper", "market": "h2h_90min", "selection": "home",
        "status": "recommended", "odds_decimal": 2.5, "stake_eur": 10.0,
    }

    def test_extra_time_win_is_lost_bet(self):
        # Argentinien-Kap Verde: 1:1 nach 90 -> Wette auf Heimsieg VERLOREN,
        # auch wenn das Spiel 3:2 n.V. endete
        settled = settle_paper_bet(self.BET, (1, 1))
        assert settled["outcome"] == "lost"
        assert settled["profit_eur"] == -10.0

    def test_regular_win_pays_out(self):
        settled = settle_paper_bet(self.BET, (2, 0))
        assert settled["outcome"] == "won"
        assert settled["payout_eur"] == 25.0

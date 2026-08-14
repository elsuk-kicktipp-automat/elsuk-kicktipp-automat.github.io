"""Lockere Vergleichs-Wettvariante (config: paper_betting.shadow)."""

from engine.paper_betting import build_shadow_bet, confidence_stake, settle_paper_bet
from engine.seal import HASHED_FIELDS, REVEALED_FIELDS

STAKING = {
    "mode": "confidence",
    "bankroll_eur": 1000,
    "min_stake_eur": 5,
    "max_stake_eur": 25,
    "confidence_from": 0.35,
    "confidence_to": 0.70,
}
CFG = {"enabled": True, "label": "locker", "market": "h2h_90min", "staking": STAKING}
MARKET = {
    "source": "tipico_de",
    "source_label": "Tipico",
    "bookmaker_count": 21,
    "odds": {"home": 2.0, "draw": 3.4, "away": 3.6},
}


class TestConfidenceStake:
    def test_minimum_below_the_lower_bound(self):
        assert confidence_stake(0.20, STAKING) == 5.0
        assert confidence_stake(0.35, STAKING) == 5.0

    def test_maximum_from_the_upper_bound(self):
        assert confidence_stake(0.70, STAKING) == 25.0
        assert confidence_stake(0.95, STAKING) == 25.0

    def test_scales_linearly_in_between(self):
        # Mitte zwischen 0.35 und 0.70 -> Mitte zwischen 5 und 25
        assert confidence_stake(0.525, STAKING) == 15.0

    def test_survives_a_degenerate_range(self):
        assert confidence_stake(0.5, {**STAKING, "confidence_from": 0.7, "confidence_to": 0.7}) == 5.0


class TestBuildShadowBet:
    def _bet(self, probs, market=MARKET):
        return build_shadow_bet(
            cfg=CFG, home="FC Bayern München", away="VfB Stuttgart",
            tip=(2, 1), raw_probabilities=probs, market=market,
        )

    def test_bets_even_without_value(self):
        # Negativer Edge - die strenge Variante wuerde hier nicht setzen
        bet = self._bet({"home": 0.40, "draw": 0.3, "away": 0.3})
        assert bet["edge"] < 0
        assert bet["stake_eur"] > 0
        assert bet["status"] == "recommended"

    def test_stake_follows_confidence(self):
        wenig = self._bet({"home": 0.40, "draw": 0.3, "away": 0.3})
        viel = self._bet({"home": 0.75, "draw": 0.15, "away": 0.10})
        assert viel["stake_eur"] > wenig["stake_eur"]
        assert viel["stake_eur"] == 25.0

    def test_follows_the_tip_not_the_odds(self):
        bet = build_shadow_bet(
            cfg=CFG, home="A", away="B", tip=(0, 2),
            raw_probabilities={"home": 0.2, "draw": 0.3, "away": 0.5}, market=MARKET,
        )
        assert bet["selection"] == "away"
        assert bet["odds_decimal"] == 3.6

    def test_without_odds_nothing_is_staked(self):
        bet = self._bet({"home": 0.5, "draw": 0.3, "away": 0.2}, market=None)
        assert bet["status"] == "missing_odds"
        assert "stake_eur" not in bet

    def test_disabled_returns_none(self):
        assert build_shadow_bet(
            cfg={**CFG, "enabled": False}, home="A", away="B", tip=(1, 0),
            raw_probabilities={"home": 0.5, "draw": 0.3, "away": 0.2}, market=MARKET,
        ) is None

    def test_settles_like_a_normal_paper_bet(self):
        bet = self._bet({"home": 0.60, "draw": 0.25, "away": 0.15})
        gewonnen = settle_paper_bet(bet, (2, 1))
        verloren = settle_paper_bet(bet, (0, 1))
        assert gewonnen["outcome"] == "won"
        assert gewonnen["payout_eur"] == round(bet["stake_eur"] * 2.0, 2)
        assert verloren["outcome"] == "lost"
        assert verloren["profit_eur"] == -bet["stake_eur"]


class TestSealedAlongsideTheTip:
    """Eine Wette, die erst nach Anstoß feststeht, wäre wertlos."""

    def test_shadow_bets_are_part_of_the_hash(self):
        assert "shadow_bets" in HASHED_FIELDS

    def test_shadow_bets_are_revealed(self):
        assert "shadow_bets" in REVEALED_FIELDS

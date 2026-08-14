"""Paper-Betting: theoretische 1X2-Wetten ohne echte Wettabgabe.

Die Einsatzempfehlung nutzt eine konservative Fractional-Kelly-Logik und wird
bei 100 EUR (konfigurierbar) gedeckelt. Als Wahrscheinlichkeit dient bewusst
die rohe Modellwahrscheinlichkeit vor dem Markt-Blend, damit die Quote nicht
erst in die Prognose eingerechnet und danach gegen sich selbst bewertet wird.
"""

from __future__ import annotations


OUTCOME_LABELS = {
    "home": "Heimsieg",
    "draw": "Remis",
    "away": "Auswärtssieg",
}


def outcome_from_tip(tip: tuple[int, int] | list[int]) -> str:
    diff = tip[0] - tip[1]
    if diff > 0:
        return "home"
    if diff < 0:
        return "away"
    return "draw"


def selection_label(selection: str, home: str, away: str) -> str:
    if selection == "home":
        return f"{home} gewinnt"
    if selection == "away":
        return f"{away} gewinnt"
    return "Remis"


def kelly_fraction(probability: float, decimal_odds: float) -> float:
    if probability <= 0 or decimal_odds <= 1:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - probability
    return max(0.0, (b * probability - q) / b)


def build_paper_bet(
    *,
    cfg: dict,
    home: str,
    away: str,
    tip: tuple[int, int] | list[int],
    raw_probabilities: dict[str, float],
    market: dict | None,
) -> dict | None:
    if not cfg.get("enabled"):
        return None

    selection = outcome_from_tip(tip)
    base = {
        "mode": "paper",
        "market": cfg.get("market", "h2h_90min"),
        "selection": selection,
        "selection_label": selection_label(selection, home, away),
        "status": "missing_odds",
    }
    if market is None:
        return base

    odds = market["odds"][selection]
    probability = raw_probabilities[selection]
    implied_probability = 1.0 / odds if odds > 0 else 0.0
    edge = probability * odds - 1.0

    staking = cfg.get("staking", {})
    bankroll = float(staking.get("bankroll_eur", 1000.0))
    fraction = float(staking.get("kelly_fraction", 0.25))
    max_stake = float(staking.get("max_stake_eur", 100.0))
    min_stake = float(staking.get("min_stake_eur", 0.0))
    min_edge = float(staking.get("min_edge", 0.02))

    full_kelly = kelly_fraction(probability, odds)
    stake = bankroll * full_kelly * fraction if edge >= min_edge else 0.0
    stake = min(stake, max_stake)
    if stake < min_stake:
        stake = 0.0

    stake = round(stake, 2)
    return {
        **base,
        "status": "recommended" if stake > 0 else "skipped_no_value",
        "source": market["source"],
        "source_label": market["source_label"],
        "bookmaker_count": market.get("bookmaker_count"),
        "odds_decimal": round(odds, 3),
        "model_probability": round(probability, 4),
        "implied_probability": round(implied_probability, 4),
        "edge": round(edge, 4),
        "expected_value_eur": round(stake * edge, 2),
        "stake_eur": stake,
        "max_stake_eur": round(max_stake, 2),
        "kelly_fraction": round(full_kelly, 4),
        "applied_kelly_fraction": round(fraction, 4),
        "bankroll_eur": round(bankroll, 2),
    }


def confidence_stake(probability: float, staking: dict) -> float:
    """Einsatz nach Zuversicht: linear zwischen Mindest- und Höchsteinsatz.

    Unterhalb von `confidence_from` (Default 35% - kaum mehr als Zufall bei drei
    möglichen Ausgängen) immer der Mindesteinsatz, ab `confidence_to` immer der
    Höchsteinsatz, dazwischen linear. Bewusst keine Kelly-Formel: Kelly braucht
    einen Vorsprung gegenüber der Quote, und genau den fragt diese Variante
    nicht ab.
    """
    lo = float(staking.get("confidence_from", 0.35))
    hi = float(staking.get("confidence_to", 0.70))
    smin = float(staking.get("min_stake_eur", 5.0))
    smax = float(staking.get("max_stake_eur", 25.0))
    if hi <= lo:
        return round(smin, 2)
    share = min(1.0, max(0.0, (probability - lo) / (hi - lo)))
    return round(smin + (smax - smin) * share, 2)


def build_shadow_bet(
    *,
    cfg: dict,
    home: str,
    away: str,
    tip: tuple[int, int] | list[int],
    raw_probabilities: dict[str, float],
    market: dict | None,
) -> dict | None:
    """Lockere Vergleichsvariante: setzt auf JEDE Partie, ohne Value-Prüfung.

    Gegenstück zur strengen Variante (build_paper_bet), die nur bei rechnerischem
    Vorsprung setzt und deshalb selten zum Zug kommt. Hier wird immer auf den
    Modell-Favoriten gesetzt, der Einsatz steigt mit der Zuversicht. Sinn ist der
    Vergleich über eine ganze Saison: Bringt die Value-Regel mehr, als einfach
    mitzuspielen? Ohne die lockere Variante bliebe das unbeantwortbar, weil die
    strenge Variante zu selten wettet, um eine Aussage zu tragen.
    """
    if not cfg.get("enabled"):
        return None

    selection = outcome_from_tip(tip)
    base = {
        "mode": "paper",
        "variant": cfg.get("label", "locker"),
        "market": cfg.get("market", "h2h_90min"),
        "selection": selection,
        "selection_label": selection_label(selection, home, away),
        "status": "missing_odds",
    }
    if market is None:
        return base

    odds = market["odds"][selection]
    probability = raw_probabilities[selection]
    staking = cfg.get("staking", {})
    stake = min(confidence_stake(probability, staking), float(staking.get("max_stake_eur", 25.0)))
    edge = probability * odds - 1.0

    return {
        **base,
        "status": "recommended" if stake > 0 else "skipped_no_odds",
        "source": market["source"],
        "source_label": market["source_label"],
        "bookmaker_count": market.get("bookmaker_count"),
        "odds_decimal": round(odds, 3),
        "model_probability": round(probability, 4),
        "implied_probability": round(1.0 / odds, 4) if odds > 0 else 0.0,
        # Edge nur zur Information - er entscheidet hier bewusst nichts
        "edge": round(edge, 4),
        "expected_value_eur": round(stake * edge, 2),
        "stake_eur": stake,
        "staking_mode": "confidence",
        "bankroll_eur": round(float(staking.get("bankroll_eur", 1000.0)), 2),
    }


def settle_paper_bet(bet: dict | None, result: tuple[int, int]) -> dict | None:
    if not bet:
        return None
    stake = float(bet.get("stake_eur") or 0.0)
    if stake <= 0:
        return {
            "outcome": "skipped",
            "stake_eur": 0.0,
            "payout_eur": 0.0,
            "profit_eur": 0.0,
        }

    actual = outcome_from_tip(result)
    won = bet["selection"] == actual
    payout = stake * float(bet["odds_decimal"]) if won else 0.0
    return {
        "outcome": "won" if won else "lost",
        "stake_eur": round(stake, 2),
        "payout_eur": round(payout, 2),
        "profit_eur": round(payout - stake, 2),
    }

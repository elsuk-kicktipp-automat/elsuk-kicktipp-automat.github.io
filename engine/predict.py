"""Prognose für die nächsten anstehenden Spiele des aktiven Wettbewerbs.

Fittet das Modell auf alle bisher gespielten Partien (plus ggf. Vorsaisons),
berechnet pro Spiel die EV-optimalen Tipps und schreibt sie mit Zeitstempel,
Modellversion und Eingangsfaktoren nach data/predictions/.
"""

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from .config import MATCHDAYS_DIR, PREDICTIONS_DIR, PROJECT_ROOT
from .model import DixonColes
from .optimizer import (
    ALWAYS_DRAW_TIP,
    best_tip,
    elo_favorite_tip,
    most_probable_score,
    penalty_shootout_favorite,
)
from .sources.elo import make_elo_source
from .sources.openligadb import Match, fetch_competition
from .teams import is_knockout_stage

MODEL_VERSION = "dixon-coles-elo-1"


def outcome_probabilities(matrix: np.ndarray) -> dict[str, float]:
    """Heimsieg-/Remis-/Auswärtssieg-Wahrscheinlichkeit aus der Ergebnismatrix."""
    return {
        "home": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, 1).sum()),
    }


def build_begruendung(
    m: Match, lam: float, mu: float, probs: dict, tip: tuple, ev: float, advance_tip: dict | None = None
) -> str:
    """Template-Begründung aus den Modellzahlen (LLM-Schicht kommt in Phase 3)."""
    favorit = (
        m.home_name if probs["home"] > max(probs["draw"], probs["away"])
        else m.away_name if probs["away"] > max(probs["draw"], probs["home"])
        else None
    )
    lage = (
        f"Das Modell sieht {favorit} vorn" if favorit
        else "Das Modell sieht ein ausgeglichenes Spiel"
    )
    text = (
        f"{lage} (Heimsieg {probs['home']:.0%}, Remis {probs['draw']:.0%}, "
        f"Auswärtssieg {probs['away']:.0%}) und erwartet im Schnitt "
        f"{lam:.1f}:{mu:.1f} Tore. Der Tipp {tip[0]}:{tip[1]} maximiert den "
        f"Punkte-Erwartungswert ({ev:.2f} Punkte) über alle möglichen Ergebnisse "
        f"– nicht die Trefferchance auf das exakte Resultat."
    )
    if advance_tip:
        text += (
            f" Bei Unentschieden nach 90 Minuten tippt das Modell {advance_tip['pick']} "
            f"als Sieger im Elfmeterschießen ({advance_tip['probability']:.0%})."
        )
    return text


def resolve_l2_penalty(model_cfg: dict, team_type: str) -> float:
    """l2_penalty ist pro team_type konfigurierbar (Nationalteams haben nur
    wenige Turnierspiele und brauchen deutlich stärkere Shrinkage)."""
    l2 = model_cfg["l2_penalty"]
    return l2[team_type] if isinstance(l2, dict) else l2


def build_model(config: dict, neutral_venue: bool, team_type: str) -> DixonColes:
    model_cfg = config["model"]
    return DixonColes(
        xi=model_cfg["time_decay_xi"],
        l2_penalty=resolve_l2_penalty(model_cfg, team_type),
        max_goals=model_cfg["max_goals"],
        neutral_venue=neutral_venue,
        elo_beta_prior=model_cfg["elo"]["beta_prior"],
        elo_beta_penalty=model_cfg["elo"]["beta_penalty"],
    )


def load_elo(config: dict, team_type: str, on_date=None) -> dict[str, float] | None:
    if not config["model"]["elo"]["enabled"]:
        return None
    return make_elo_source(team_type).ratings(on_date)


def predict_matches(
    config: dict, targets: list[Match], train: list[Match], neutral_venue: bool, team_type: str
) -> list[dict]:
    """Tipps für die Zielspiele; Modell wird auf den Trainingsspielen gefittet."""
    ref_date = min(m.kickoff_utc for m in targets)
    elo = load_elo(config, team_type, ref_date.date())
    model = build_model(config, neutral_venue, team_type)
    model.fit(train, ref_date, elo=elo)

    scheme = config["kicktipp"]["points"]
    max_tip = config["model"]["max_tip_goals"]
    predictions = []
    for m in sorted(targets, key=lambda t: (t.kickoff_utc, t.home_name)):
        matrix = model.score_matrix(m.home_key, m.away_key)
        tip, ev = best_tip(matrix, scheme, max_tip)
        lam, mu = model.expected_goals(m.home_key, m.away_key)
        probs = outcome_probabilities(matrix)

        advance_tip = None
        if tip[0] == tip[1] and is_knockout_stage(m.stage_name):
            side, p = penalty_shootout_favorite(probs)
            advance_tip = {
                "pick": m.home_name if side == "home" else m.away_name,
                "probability": round(p, 3),
            }

        predictions.append(
            {
                "home": m.home_name,
                "away": m.away_name,
                "kickoff_utc": m.kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "matchday": m.matchday,
                "stage": m.stage_name,
                "tip": list(tip),
                "expected_points": round(ev, 3),
                # Bei K.o.-Remis-Tipp: Zusatzfrage "Wer kommt weiter?" (None
                # in der Gruppenphase oder wenn der Tipp kein Remis ist)
                "advance_tip": advance_tip,
                # Schattentipper (concept.md Schicht 4): parallel geführte
                # Vergleichsstrategien, abgerechnet in evaluate
                "shadow_tips": {
                    "most_probable": list(most_probable_score(matrix)),
                    "elo_favorite": list(
                        elo_favorite_tip((elo or {}).get(m.home_key), (elo or {}).get(m.away_key))
                    ),
                    "always_draw": list(ALWAYS_DRAW_TIP),
                },
                "factors": {
                    "expected_goals": [round(lam, 2), round(mu, 2)],
                    "probabilities": {k: round(v, 3) for k, v in probs.items()},
                    "elo": {
                        "home": (elo or {}).get(m.home_key),
                        "away": (elo or {}).get(m.away_key),
                        "beta": round(model.params.elo_beta, 4),
                    },
                    "home_advantage": round(model.params.home_adv, 3),
                    "trained_on_matches": len([t for t in train if t.has_result]),
                },
                "begruendung": build_begruendung(m, lam, mu, probs, tip, ev, advance_tip),
            }
        )
    return predictions


def run_predict(config: dict) -> dict | None:
    """Prognostiziert die nächste anstehende Runde; None, wenn nichts ansteht."""
    now = datetime.now(timezone.utc)
    season = config["season"]
    matches = fetch_competition(config["leagues"], season, force_refresh=True)

    upcoming = [
        m for m in matches
        if not m.finished and not m.has_placeholder
        and m.kickoff_utc > now - timedelta(hours=3)
    ]
    if not upcoming:
        return None

    next_matchday = min(upcoming, key=lambda m: m.kickoff_utc).matchday
    targets = [m for m in upcoming if m.matchday == next_matchday]
    train = [m for m in matches if m.has_result]
    if config["team_type"] == "club":
        lookback = config.get("backtest", {}).get("club", {}).get("lookback_seasons", 2)
        for s in range(season - lookback, season):
            train += [m for m in fetch_competition(config["leagues"], s) if m.has_result]

    predictions = predict_matches(
        config, targets, train, config["neutral_venue"], config["team_type"]
    )
    return {
        "competition": config["competition"],
        "season": season,
        "matchday": next_matchday,
        "stage": targets[0].stage_name,
        "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_version": MODEL_VERSION,
        "kicktipp_scheme": config["kicktipp"]["points"],
        "matches": predictions,
    }


def _covered_pairings(stem: str) -> set[tuple[str, str]]:
    """Paarungen, die für diese Runde schon getippt (versiegelt oder lokal) sind."""
    covered = set()
    for path in [MATCHDAYS_DIR / f"{stem}.json", *PREDICTIONS_DIR.glob(f"{stem}*.json")]:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            covered |= {(m["home"], m["away"]) for m in data["matches"]}
    return covered


def main(config: dict) -> None:
    report = run_predict(config)
    if report is None:
        print("Keine anstehenden Spiele gefunden (Saisonpause?), nichts zu tun.")
        return

    # Pro Paarung wird genau einmal getippt (Fairness-Mechanismus). Neue
    # Paarungen derselben Runde (K.o.-Plan: Platzhalter, die erst später
    # feststehen) kommen als weiterer Batch dazu.
    stem = f"{report['competition']}_{report['season']}_md{report['matchday']:02d}"
    covered = _covered_pairings(stem)
    new_matches = [m for m in report["matches"] if (m["home"], m["away"]) not in covered]
    if not new_matches:
        print(f"Runde {report['matchday']} ist bereits vollständig getippt, nichts zu tun.")
        return
    report["matches"] = new_matches

    name = f"{stem}.json" if not covered else f"{stem}_b{len(covered)}.json"
    out = PREDICTIONS_DIR / name
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(report['matches'])} Tipps für {report['stage']} (Runde {report['matchday']}):")
    for p in report["matches"]:
        line = f"  {p['home']} - {p['away']}: {p['tip'][0]}:{p['tip'][1]} (EV {p['expected_points']})"
        if p.get("advance_tip"):
            line += f" | Elfmeterschießen: {p['advance_tip']['pick']}"
        print(line)
    print(f"Gespeichert: {out.relative_to(PROJECT_ROOT)}")

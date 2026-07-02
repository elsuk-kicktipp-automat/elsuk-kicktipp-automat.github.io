"""Punkteabrechnung: abgegebene Tipps gegen die realen Ergebnisse.

Liest alle Prognosen des aktiven Wettbewerbs aus data/predictions/, holt die
aktuellen Ergebnisse und schreibt die Abrechnung nach data/results/.
"""

import json

from .config import PROJECT_ROOT
from .optimizer import match_points
from .predict import PREDICTIONS_DIR
from .sources.openligadb import fetch_competition
from .teams import normalize

RESULTS_DIR = PROJECT_ROOT / "data" / "results"


def evaluate_prediction(pred: dict, results_by_pairing: dict, scheme: dict) -> dict:
    """Rechnet eine Prognose-Datei ab; Spiele ohne Ergebnis bleiben offen."""
    matches, total, scored = [], 0, 0
    counts = {"exact": 0, "goal_diff": 0, "tendency": 0, "miss": 0}
    for p in pred["matches"]:
        entry = dict(p)
        result = results_by_pairing.get((normalize(p["home"]), normalize(p["away"])))
        if result is not None:
            points = match_points(tuple(p["tip"]), result, scheme)
            entry["result"] = list(result)
            entry["points"] = points
            total += points
            scored += 1
            key = next(
                (k for k in ("exact", "goal_diff", "tendency") if points == scheme[k] and points > 0),
                "miss",
            )
            counts[key] += 1
        else:
            entry["result"] = None
            entry["points"] = None
        matches.append(entry)

    return {
        "competition": pred["competition"],
        "season": pred["season"],
        "matchday": pred["matchday"],
        "stage": pred.get("stage"),
        "model_version": pred.get("model_version"),
        "points_total": total,
        "matches_scored": scored,
        "matches_open": len(matches) - scored,
        "hits": counts,
        "matches": matches,
    }


def main(config: dict) -> None:
    prefix = f"{config['competition']}_{config['season']}_"
    prediction_files = sorted(PREDICTIONS_DIR.glob(f"{prefix}*.json"))
    if not prediction_files:
        print(f"Keine Prognosen für {config['competition']} in data/predictions/ gefunden.")
        return

    scheme = config["kicktipp"]["points"]
    finished = [
        m for m in fetch_competition(config["leagues"], config["season"], force_refresh=True)
        if m.has_result
    ]
    results_by_pairing = {
        (m.home_key, m.away_key): (m.home_goals, m.away_goals) for m in finished
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for pred_file in prediction_files:
        pred = json.loads(pred_file.read_text(encoding="utf-8"))
        report = evaluate_prediction(pred, results_by_pairing, scheme)
        out = RESULTS_DIR / pred_file.name
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"{pred_file.stem}: {report['points_total']} Punkte aus "
            f"{report['matches_scored']} gewerteten Spielen "
            f"({report['matches_open']} offen) -> {out.relative_to(PROJECT_ROOT)}"
        )

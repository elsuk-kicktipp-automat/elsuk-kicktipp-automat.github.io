"""Punkteabrechnung: enthüllte Tipps gegen die realen Ergebnisse.

Liest die öffentlichen Spieltags-Dateien (data/matchdays/) des aktiven
Wettbewerbs, wertet alle enthüllten Tipps mit vorliegendem Ergebnis und
schreibt die Abrechnung nach data/results/. Versiegelte Tipps können und
müssen nicht gewertet werden – ihr Spiel hat noch nicht stattgefunden.

Zusätzlich (concept.md Schicht 4):
- Schattentipper: parallel geführte Vergleichsstrategien (wahrscheinlichstes
  Ergebnis, ELO-Favorit 2:1, immer 1:1) werden mit abgerechnet.
- Kalibrierung: Brier-Score der Heimsieg/Remis/Auswärtssieg-
  Wahrscheinlichkeiten gegen den realen Ausgang (0 = perfekt, kleiner = besser).
"""

import json

from .config import MATCHDAYS_DIR, PROJECT_ROOT, RESULTS_DIR
from .optimizer import ALWAYS_DRAW_TIP, elo_favorite_tip, match_points
from .sources.openligadb import fetch_competition
from .teams import normalize

SHADOW_TIPPERS = ("most_probable", "elo_favorite", "always_draw")


def _shadow_tips(match: dict) -> dict[str, tuple[int, int]]:
    """Schattentipps aus der Prognose; für Alt-Daten ohne shadow_tips abgeleitet."""
    stored = match.get("shadow_tips", {})
    tips = {name: tuple(tip) for name, tip in stored.items()}
    if "always_draw" not in tips:
        tips["always_draw"] = ALWAYS_DRAW_TIP
    if "elo_favorite" not in tips:
        elo = match.get("factors", {}).get("elo", {})
        if elo.get("home") is not None and elo.get("away") is not None:
            tips["elo_favorite"] = elo_favorite_tip(elo["home"], elo["away"])
    return tips


def _brier(match: dict, result: tuple[int, int]) -> float | None:
    """Brier-Score der H/U/A-Wahrscheinlichkeiten gegen den realen Ausgang."""
    probs = match.get("factors", {}).get("probabilities")
    if not probs:
        return None
    diff = result[0] - result[1]
    outcome = "home" if diff > 0 else "away" if diff < 0 else "draw"
    return sum((probs[k] - (1.0 if k == outcome else 0.0)) ** 2 for k in ("home", "draw", "away"))


def evaluate_matchday(matchday: dict, results_by_pairing: dict, scheme: dict) -> dict:
    """Rechnet eine Spieltags-Datei ab; Spiele ohne Ergebnis/Tipp bleiben offen."""
    matches, total, scored = [], 0, 0
    counts = {"exact": 0, "goal_diff": 0, "tendency": 0, "miss": 0}
    shadow_points = {name: 0 for name in SHADOW_TIPPERS}
    shadow_matches = {name: 0 for name in SHADOW_TIPPERS}
    brier_sum, brier_n = 0.0, 0

    for m in matchday["matches"]:
        entry = {k: m[k] for k in ("home", "away", "kickoff_utc", "status")}
        result = results_by_pairing.get((normalize(m["home"]), normalize(m["away"])))
        if m["status"] == "revealed" and result is not None:
            points = match_points(tuple(m["tip"]), result, scheme)
            entry.update(tip=m["tip"], result=list(result), points=points)
            total += points
            scored += 1
            key = next(
                (k for k in ("exact", "goal_diff", "tendency") if points == scheme[k] and points > 0),
                "miss",
            )
            counts[key] += 1

            entry["shadow_points"] = {}
            for name, tip in _shadow_tips(m).items():
                pts = match_points(tip, result, scheme)
                entry["shadow_points"][name] = pts
                shadow_points[name] += pts
                shadow_matches[name] += 1

            brier = _brier(m, result)
            if brier is not None:
                entry["brier"] = round(brier, 4)
                brier_sum += brier
                brier_n += 1
        matches.append(entry)

    return {
        "competition": matchday["competition"],
        "season": matchday["season"],
        "matchday": matchday["matchday"],
        "stage": matchday.get("stage"),
        "model_version": matchday.get("model_version"),
        "points_total": total,
        "matches_scored": scored,
        "matches_open": len(matches) - scored,
        "hits": counts,
        "shadow_points": shadow_points,
        "shadow_matches": shadow_matches,
        "brier_avg": round(brier_sum / brier_n, 4) if brier_n else None,
        "matches": matches,
    }


def main(config: dict) -> None:
    prefix = f"{config['competition']}_{config['season']}_"
    matchday_files = sorted(MATCHDAYS_DIR.glob(f"{prefix}*.json"))
    if not matchday_files:
        print(f"Keine Spieltags-Dateien für {config['competition']} in data/matchdays/ gefunden.")
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
    for md_file in matchday_files:
        matchday = json.loads(md_file.read_text(encoding="utf-8"))
        report = evaluate_matchday(matchday, results_by_pairing, scheme)
        out = RESULTS_DIR / md_file.name
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"{md_file.stem}: {report['points_total']} Punkte aus "
            f"{report['matches_scored']} gewerteten Spielen "
            f"({report['matches_open']} offen) -> {out.relative_to(PROJECT_ROOT)}"
        )

"""Backtesting des Modells mit Kicktipp-Punkteoptimierung.

Zwei Modi (config.yaml, Abschnitt backtest):

- club:     Rollierender Backtest über die letzten Bundesliga-Saisons. Vor jedem
            Spieltag wird nur auf bis dahin gespielten Partien gefittet (plus
            Vorsaisons als Warmup), ELO-Stände kommen historisch korrekt vom
            jeweiligen Stichtag (clubelo.com).
- national: WM 2026 als Out-of-Sample-Test: Gruppenphase + bisherige K.o.-Spiele,
            Runde für Runde nur mit den davor gespielten Partien. Achtung:
            eloratings.net liefert nur aktuelle Ratings (leichter Lookahead-Bias).

Vergleich gegen zwei Baselines: (a) immer 2:1 für den ELO-Favoriten,
(b) immer 1:1.
"""

import json
from datetime import datetime, timezone

from .config import PROJECT_ROOT
from .optimizer import ALWAYS_DRAW_TIP, best_tip, elo_favorite_tip, match_points
from .predict import build_model, load_elo
from .sources.openligadb import Match, fetch_competition
from .teams import is_knockout_stage

BACKTESTS_DIR = PROJECT_ROOT / "data" / "backtests"


def _score_round(
    config: dict,
    model,
    targets: list[Match],
    elo: dict[str, float] | None,
) -> list[dict]:
    """Bewertet die Zielspiele einer Runde gegen ihre realen Ergebnisse."""
    scheme = config["kicktipp"]["points"]
    max_tip = config["model"]["max_tip_goals"]
    details = []
    for m in targets:
        matrix = model.score_matrix(m.home_key, m.away_key)
        tip, ev = best_tip(matrix, scheme, max_tip, allow_draw=not is_knockout_stage(m.stage_name))
        result = (m.home_goals, m.away_goals)
        home_elo = (elo or {}).get(m.home_key)
        away_elo = (elo or {}).get(m.away_key)
        details.append(
            {
                "home": m.home_name,
                "away": m.away_name,
                "tip": list(tip),
                "expected_points": round(ev, 3),
                "result": list(result),
                "points": match_points(tip, result, scheme),
                "baseline_elo_points": match_points(
                    elo_favorite_tip(home_elo, away_elo), result, scheme
                ),
                "baseline_draw_points": match_points(ALWAYS_DRAW_TIP, result, scheme),
            }
        )
    return details


def _summarize(details: list[dict], scheme: dict) -> dict:
    n = len(details)
    points = sum(d["points"] for d in details)
    hits = {
        "exact": sum(1 for d in details if d["points"] == scheme["exact"]),
        "goal_diff": sum(1 for d in details if d["points"] == scheme["goal_diff"]),
        "tendency": sum(
            1 for d in details if d["points"] == scheme["tendency"] and d["points"] > 0
        ),
    }
    return {
        "matches": n,
        "points": points,
        "points_per_match": round(points / n, 3) if n else 0.0,
        "baseline_elo_points": sum(d["baseline_elo_points"] for d in details),
        "baseline_draw_points": sum(d["baseline_draw_points"] for d in details),
        "hits": hits,
        "hit_rate": round(sum(hits.values()) / n, 3) if n else 0.0,
    }


def backtest_club(config: dict) -> dict:
    """Rollierender Backtest über die konfigurierten Bundesliga-Saisons."""
    cfg = config["backtest"]["club"]
    season_reports = []

    for season in cfg["seasons"]:
        history = [
            m
            for s in range(season - cfg["lookback_seasons"], season)
            for m in fetch_competition(cfg["leagues"], s)
            if m.has_result
        ]
        season_matches = [
            m for m in fetch_competition(cfg["leagues"], season) if m.has_result
        ]
        model = build_model(config, cfg["neutral_venue"], cfg["team_type"])
        matchday_reports = []
        season_details = []

        for matchday in sorted({m.matchday for m in season_matches}):
            targets = [m for m in season_matches if m.matchday == matchday]
            train = history + [m for m in season_matches if m.matchday < matchday]
            ref_date = min(m.kickoff_utc for m in targets)
            elo = load_elo(config, cfg["team_type"], ref_date.date())
            model.fit(train, ref_date, elo=elo)

            details = _score_round(config, model, targets, elo)
            season_details += details
            matchday_reports.append(
                {"matchday": matchday, **_summarize(details, config["kicktipp"]["points"]),
                 "matches_detail": details}
            )

        summary = _summarize(season_details, config["kicktipp"]["points"])
        season_reports.append({"season": season, **summary, "matchdays": matchday_reports})
        print(
            f"  Saison {season}/{str(season + 1)[-2:]}: {summary['points']} Punkte "
            f"(Ø {summary['points_per_match']}/Spiel) | Baselines: "
            f"ELO-Favorit 2:1 = {summary['baseline_elo_points']}, "
            f"immer 1:1 = {summary['baseline_draw_points']} | "
            f"exakt {summary['hits']['exact']}, Differenz {summary['hits']['goal_diff']}, "
            f"Tendenz {summary['hits']['tendency']}"
        )

    all_details = [d for r in season_reports for md in r["matchdays"] for d in md["matches_detail"]]
    return {
        "mode": "club",
        "competition": cfg["competition"],
        "seasons": season_reports,
        "total": _summarize(all_details, config["kicktipp"]["points"]),
    }


def backtest_national(config: dict) -> dict:
    """WM 2026 out-of-sample: Runde für Runde nur mit davor gespielten Partien."""
    cfg = config["backtest"]["national"]
    matches = [
        m
        for m in fetch_competition(cfg["leagues"], cfg["season"], force_refresh=True)
        if m.has_result
    ]
    model = build_model(config, cfg["neutral_venue"], cfg["team_type"])
    round_reports = []
    all_details = []

    for matchday in sorted({m.matchday for m in matches}):
        targets = [m for m in matches if m.matchday == matchday]
        train = [m for m in matches if m.matchday < matchday]
        ref_date = min(m.kickoff_utc for m in targets)
        elo = load_elo(config, cfg["team_type"], ref_date.date())
        model.fit(train, ref_date, elo=elo)

        details = _score_round(config, model, targets, elo)
        all_details += details
        summary = _summarize(details, config["kicktipp"]["points"])
        round_reports.append(
            {"matchday": matchday, "stage": targets[0].stage_name, **summary,
             "matches_detail": details}
        )
        print(
            f"  {targets[0].stage_name} ({summary['matches']} Spiele): "
            f"{summary['points']} Punkte (Ø {summary['points_per_match']}) | "
            f"ELO-Favorit 2:1 = {summary['baseline_elo_points']}, "
            f"immer 1:1 = {summary['baseline_draw_points']}"
        )

    return {
        "mode": "national",
        "competition": cfg["competition"],
        "season": cfg["season"],
        "elo_hinweis": "eloratings.net liefert nur aktuelle Ratings (leichter Lookahead-Bias)",
        "rounds": round_reports,
        "total": _summarize(all_details, config["kicktipp"]["points"]),
    }


def main(config: dict, mode: str = "all") -> None:
    BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
    reports = []
    if mode in ("club", "all"):
        print("=== Backtest Bundesliga (club) ===")
        reports.append(backtest_club(config))
    if mode in ("national", "all"):
        print("=== Backtest WM 2026 (national, out-of-sample) ===")
        reports.append(backtest_national(config))

    for report in reports:
        report["created_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        report["kicktipp_scheme"] = config["kicktipp"]["points"]
        out = BACKTESTS_DIR / f"{report['mode']}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        t = report["total"]
        print(
            f"Gesamt ({report['mode']}): {t['points']} Punkte in {t['matches']} Spielen "
            f"(Ø {t['points_per_match']}/Spiel) | Baselines: ELO-Favorit {t['baseline_elo_points']}, "
            f"immer 1:1 {t['baseline_draw_points']} | Report: {out.relative_to(PROJECT_ROOT)}"
        )

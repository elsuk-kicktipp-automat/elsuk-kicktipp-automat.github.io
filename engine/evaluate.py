"""Punkteabrechnung: enthüllte Tipps gegen die realen Ergebnisse.

Liest die öffentlichen Spieltags-Dateien (data/matchdays/) des aktiven
Wettbewerbs, wertet alle enthüllten Tipps mit vorliegendem Ergebnis und
schreibt die Abrechnung nach data/results/. Versiegelte Tipps können und
müssen nicht gewertet werden – ihr Spiel hat noch nicht stattgefunden.

Zusätzlich (concept.md Schicht 4):
- Schattentipper: parallel geführte Vergleichsstrategien (wahrscheinlichstes
  Ergebnis, ELO-Favorit 2:1, immer 1:1, LLM-Anpassung) werden mit abgerechnet.
  "llm_adjusted" ist nur dort gesetzt, wo das LLM einen news-gestützten
  Anpassungsvorschlag gemacht hat (siehe engine/llm.py) - läuft im Schatten,
  ändert nicht den echten Tipp; erst engine/learn.py entscheidet anhand dieser
  Werte, ob er irgendwann scharf geschaltet wird.
- Kalibrierung: Brier-Score und Log-Loss der Heimsieg/Remis/Auswärtssieg-
  Wahrscheinlichkeiten gegen den realen Ausgang (kleiner = besser, 0 = perfekt).
"""

import json
import math

from . import kombi
from .config import MANUAL_RESULTS_DIR, MATCHDAYS_DIR, PROJECT_ROOT, RESULTS_DIR
from .optimizer import ALWAYS_DRAW_TIP, elo_favorite_tip, match_category, match_points
from .paper_betting import settle_paper_bet
from .sources.openligadb import fetch_competition
from .teams import is_knockout_stage, normalize

SHADOW_TIPPERS = ("most_probable", "elo_favorite", "always_draw", "llm_adjusted")


def _advance_sides(m: dict, result: tuple[int, int], advancers: dict | None) -> tuple[str | None, str | None]:
    """(getippter, tatsächlicher) Weiterkommer eines K.o.-Spiels als "home"/"away".

    Getippt: der Tipp-Sieger; bei Remis-Tipp der advance_tip (Elfmeterschießen).
    Tatsächlich: der Ergebnis-Sieger; bei Remis nach 90 Minuten aus den späteren
    Runden abgeleitet (wer dort wieder auftaucht, kam weiter) - nicht ableitbar
    z.B. beim Halbfinale (Verlierer spielt um Platz 3) oder Finale.
    """
    tip_h, tip_a = m["tip"]
    if tip_h != tip_a:
        implied = "home" if tip_h > tip_a else "away"
    elif m.get("advance_tip"):
        implied = "home" if m["advance_tip"]["pick"] == m["home"] else "away"
    else:
        implied = None

    if result[0] != result[1]:
        actual = "home" if result[0] > result[1] else "away"
    else:
        actual = (advancers or {}).get((normalize(m["home"]), normalize(m["away"])))
    return implied, actual


def load_manual_results() -> dict[tuple[str, str], tuple[int, int]]:
    """Manuelle Ergebnis-Overrides, falls OpenLigaDB-API einzelne Resultate haengen laesst."""
    results = {}
    if not MANUAL_RESULTS_DIR.exists():
        return results

    for path in sorted(MANUAL_RESULTS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        matches = data.get("matches", data if isinstance(data, list) else [])
        for match in matches:
            result = match.get("result")
            if not result or len(result) != 2:
                continue
            key = (normalize(match["home"]), normalize(match["away"]))
            results[key] = (int(result[0]), int(result[1]))
    return results


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


def evaluate_matchday(
    matchday: dict,
    results_by_pairing: dict,
    scheme: dict,
    advancers_by_pairing: dict | None = None,
    results90_by_pairing: dict | None = None,
) -> dict:
    """Rechnet eine Spieltags-Datei ab; Spiele ohne Ergebnis/Tipp bleiben offen."""
    matches, total, scored = [], 0, 0
    counts = {"exact": 0, "goal_diff": 0, "tendency": 0, "miss": 0}
    shadow_points = {name: 0 for name in SHADOW_TIPPERS}
    shadow_matches = {name: 0 for name in SHADOW_TIPPERS}
    brier_sum, brier_n = 0.0, 0
    advance_total, advance_scored = 0, 0
    bet_stake, bet_payout, bet_profit = 0.0, 0.0, 0.0
    bets_scored, bets_won = 0, 0

    for m in matchday["matches"]:
        entry = {k: m[k] for k in ("home", "away", "kickoff_utc", "status")}
        result = results_by_pairing.get((normalize(m["home"]), normalize(m["away"])))
        if m["status"] == "revealed" and result is not None:
            points = match_points(tuple(m["tip"]), result, scheme)
            entry.update(tip=m["tip"], result=list(result), points=points)
            total += points
            scored += 1
            counts[match_category(tuple(m["tip"]), result)] += 1

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

            # Zusatzfrage bei K.o.-Spielen: "Wer kommt weiter?" - separat
            # ausgewiesen, weil nicht jede Runde sie wertet
            stage = m.get("stage") or matchday.get("stage") or ""
            if is_knockout_stage(stage):
                implied, actual = _advance_sides(m, result, advancers_by_pairing)
                if implied is not None and actual is not None:
                    pts = scheme.get("advance", 0) if implied == actual else 0
                    entry["advance"] = {
                        "tip_side": implied,
                        "actual_side": actual,
                        "correct": implied == actual,
                        "points": pts,
                    }
                    advance_total += pts
                    advance_scored += 1

            if m.get("paper_bet"):
                entry["paper_bet"] = m["paper_bet"]
                # Buchmacher-1X2 wird auf das 90-Minuten-Ergebnis abgerechnet,
                # nicht auf die n.E.-Gesamtwertung: Argentinien-Kap Verde 1:1
                # nach 90 (Wette auf Argentinien verloren) endete 3:2 n.V. -
                # gegen das Endergebnis gerechnet wäre die Bilanz geschönt.
                pairing = (normalize(m["home"]), normalize(m["away"]))
                result_90 = (results90_by_pairing or {}).get(pairing, result)
                settled = settle_paper_bet(m["paper_bet"], result_90)
                if settled is not None:
                    entry["paper_bet_result"] = settled
                    bet_stake += settled["stake_eur"]
                    bet_payout += settled["payout_eur"]
                    bet_profit += settled["profit_eur"]
                    if settled["outcome"] in ("won", "lost"):
                        bets_scored += 1
                    if settled["outcome"] == "won":
                        bets_won += 1
        matches.append(entry)

    bet_roi = bet_profit / bet_stake if bet_stake else None

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
        "advance_points_total": advance_total,
        "advance_scored": advance_scored,
        "paper_betting": {
            "stake_total_eur": round(bet_stake, 2),
            "payout_total_eur": round(bet_payout, 2),
            "profit_total_eur": round(bet_profit, 2),
            "roi": round(bet_roi, 4) if bet_roi is not None else None,
            "bets_scored": bets_scored,
            "bets_won": bets_won,
        },
        "matches": matches,
    }


def main(config: dict) -> None:
    prefix = f"{config['competition']}_{config['season']}_"
    matchday_files = sorted(MATCHDAYS_DIR.glob(f"{prefix}*.json"))
    if not matchday_files:
        print(f"Keine Spieltags-Dateien für {config['competition']} in data/matchdays/ gefunden.")
        return

    scheme = config["kicktipp"]["points"]
    all_matches = fetch_competition(config["leagues"], config["season"], force_refresh=True)
    finished = [m for m in all_matches if m.has_result]
    results_by_pairing = {
        (m.home_key, m.away_key): (m.home_goals, m.away_goals) for m in finished
    }
    results90_by_pairing = {
        (m.home_key, m.away_key): (m.home_goals_90, m.away_goals_90)
        for m in finished
        if m.home_goals_90 is not None and m.away_goals_90 is not None
    }
    manual = load_manual_results()
    results_by_pairing.update(manual)
    # Manuelle Overrides gelten mangels separatem 90-Minuten-Feld für beide
    results90_by_pairing.update(manual)

    # Wer nach einem 90-Minuten-Remis weiterkam, steht in keiner API - aber wer
    # in einer späteren Runde wieder auftaucht, hat das Elfmeterschießen gewonnen.
    appearances: dict[str, set[int]] = {}
    for m in all_matches:
        if not m.has_placeholder:
            appearances.setdefault(m.home_key, set()).add(m.matchday)
            appearances.setdefault(m.away_key, set()).add(m.matchday)

    def derived_advancer(m) -> str | None:
        home_later = any(md > m.matchday for md in appearances.get(m.home_key, ()))
        away_later = any(md > m.matchday for md in appearances.get(m.away_key, ()))
        if home_later != away_later:
            return "home" if home_later else "away"
        return None  # beide (Halbfinale: Platz 3) oder keiner (Finale) -> offen

    advancers_by_pairing = {
        (m.home_key, m.away_key): derived_advancer(m)
        for m in finished
        if m.home_goals == m.away_goals
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for md_file in matchday_files:
        matchday = json.loads(md_file.read_text(encoding="utf-8"))
        report = evaluate_matchday(
            matchday, results_by_pairing, scheme, advancers_by_pairing, results90_by_pairing
        )
        out = RESULTS_DIR / md_file.name
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"{md_file.stem}: {report['points_total']} Punkte aus "
            f"{report['matches_scored']} gewerteten Spielen "
            f"({report['matches_open']} offen) -> {out.relative_to(PROJECT_ROOT)}"
        )

    # Enthüllte Kombiwetten abrechnen (90-Minuten-Ergebnisse, wie Einzelwetten)
    for path in kombi.settle_open(results90_by_pairing):
        print(f"Kombi abgerechnet: {path.relative_to(PROJECT_ROOT)}")

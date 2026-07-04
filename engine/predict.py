"""Prognose für die nächsten anstehenden Spiele des aktiven Wettbewerbs.

Fittet das Modell auf alle bisher gespielten Partien (plus ggf. Vorsaisons),
berechnet pro Spiel die EV-optimalen Tipps und schreibt sie mit Zeitstempel,
Modellversion und Eingangsfaktoren nach data/predictions/.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

from . import llm
from .config import MATCHDAYS_DIR, PREDICTIONS_DIR, PROJECT_ROOT, load_dotenv
from .market import blend_with_market
from .model import DixonColes
from .optimizer import (
    ALWAYS_DRAW_TIP,
    best_tip,
    elo_favorite_tip,
    most_probable_score,
    penalty_shootout_favorite,
)
from .paper_betting import build_paper_bet
from .sources import news as news_source
from .sources.elo import make_elo_source
from .sources.odds import (
    load_betting_markets as load_odds_betting_markets,
    load_probabilities as load_odds_probabilities,
)
from .sources.openligadb import Match, fetch_competition
from .teams import is_knockout_stage

MODEL_VERSION = "dixon-coles-elo-3-market-llm-news"


def outcome_probabilities(matrix: np.ndarray) -> dict[str, float]:
    """Heimsieg-/Remis-/Auswärtssieg-Wahrscheinlichkeit aus der Ergebnismatrix."""
    return {
        "home": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, 1).sum()),
    }


def marginal_expected_goals(matrix: np.ndarray) -> tuple[float, float]:
    """Erwartete Tore aus den Randverteilungen der Matrix – bleibt korrekt,
    auch nachdem die Matrix per Marktquote nachjustiert wurde."""
    size = matrix.shape[0]
    goals = np.arange(size)
    lam = float((matrix.sum(axis=1) * goals).sum())
    mu = float((matrix.sum(axis=0) * goals).sum())
    return lam, mu


def load_odds(config: dict, on_date=None) -> dict[tuple[str, str], dict[str, float]]:
    odds_cfg = config.get("odds", {})
    if not odds_cfg.get("enabled"):
        return {}
    load_dotenv()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY fehlt, laufe ohne Quoten-Prior.")
        return {}
    cache_tag = (on_date or datetime.now(timezone.utc).date()).isoformat()
    return load_odds_probabilities(
        api_key, odds_cfg["sport_key"], odds_cfg.get("regions", "eu"), cache_tag=cache_tag
    )


def load_betting_markets(config: dict, on_date=None) -> dict[tuple[str, str], dict]:
    betting_cfg = config.get("paper_betting", {})
    odds_cfg = config.get("odds", {})
    if not betting_cfg.get("enabled") or not odds_cfg.get("enabled"):
        return {}
    load_dotenv()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY fehlt, Paper-Betting läuft ohne Quoten.")
        return {}
    cache_tag = (on_date or datetime.now(timezone.utc).date()).isoformat()
    return load_odds_betting_markets(
        api_key,
        odds_cfg["sport_key"],
        odds_cfg.get("regions", "eu"),
        cache_tag=cache_tag,
        preferred_bookmakers=betting_cfg.get("provider_preference", ["tipico_de"]),
    )


def build_begruendung(
    m: Match,
    lam: float,
    mu: float,
    probs: dict,
    tip: tuple,
    ev: float,
    advance_tip: dict | None = None,
    elo: dict | None = None,
    market_probs: dict | None = None,
    market_weight: float = 0.0,
    news_checked: int | None = None,
    llm_adjustment: dict | None = None,
) -> str:
    """Ausführliche Template-Begründung (LLM-Schicht formuliert bei Bedarf um,
    siehe engine/llm.py) - nennt explizit, welche Quellen mit welchem Gewicht
    zur Entscheidung beigetragen haben: ELO-Prior, Buchmacherquoten-Blend,
    News-Prüfung (Schatten-Anpassung), zum Schluss die Punkteoptimierung."""
    favorit = (
        m.home_name if probs["home"] > max(probs["draw"], probs["away"])
        else m.away_name if probs["away"] > max(probs["draw"], probs["home"])
        else None
    )
    lage = (
        f"Das Modell sieht {favorit} vorn" if favorit
        else "Das Modell sieht ein ausgeglichenes Spiel"
    )
    sentences = [
        f"{lage} (Heimsieg {probs['home']:.0%}, Remis {probs['draw']:.0%}, "
        f"Auswärtssieg {probs['away']:.0%}) und erwartet im Schnitt {lam:.1f}:{mu:.1f} Tore."
    ]

    if elo and elo.get("home") is not None and elo.get("away") is not None:
        diff = elo["home"] - elo["away"]
        sentences.append(
            f"Die ELO-Bewertung ({elo['home']:.0f} zu {elo['away']:.0f}, Differenz {diff:+.0f}) "
            "fließt als Prior in die statistische Grundeinschätzung ein."
        )

    if market_probs is not None and market_weight > 0:
        sentences.append(
            f"Die Buchmacherquoten (Heimsieg {market_probs['home']:.0%}, Remis "
            f"{market_probs['draw']:.0%}, Auswärtssieg {market_probs['away']:.0%}) sind zu "
            f"{market_weight:.0%} in die Prognose eingerechnet."
        )

    if llm_adjustment:
        sentences.append(
            f"Eine der {llm_adjustment['news_count']} geprüften aktuellen Schlagzeilen lieferte "
            f"einen möglichen Grund für eine Anpassung auf {llm_adjustment['tip'][0]}:"
            f"{llm_adjustment['tip'][1]} ({llm_adjustment['grund']}) - das läuft nur als "
            "Schattentipp mit und ändert nicht den hier gezeigten, offiziellen Tipp."
        )
    elif news_checked is not None:
        if news_checked > 0:
            schlagzeile = "Schlagzeile" if news_checked == 1 else "Schlagzeilen"
            sentences.append(
                f"{news_checked} aktuelle {schlagzeile} wurden auf einen harten Grund für "
                "eine Anpassung geprüft (Verletzung, Sperre, Rotation), ohne konkreten Treffer."
            )
        else:
            sentences.append("Es wurden keine einschlägigen aktuellen Schlagzeilen zu diesem Spiel gefunden.")

    sentences.append(
        f"Der Tipp {tip[0]}:{tip[1]} maximiert den Punkte-Erwartungswert ({ev:.2f} Punkte) über "
        "alle möglichen Ergebnisse – nicht die Trefferchance auf das exakte Resultat."
    )
    if advance_tip:
        sentences.append(
            f"Bei Unentschieden nach 90 Minuten tippt das Modell {advance_tip['pick']} als "
            f"Sieger im Elfmeterschießen ({advance_tip['probability']:.0%})."
        )
    return " ".join(sentences)


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
    """Best-effort: liefert None statt eines Fehlers, wenn die ELO-Quelle nicht
    erreichbar ist (z.B. eloratings.net blockt gelegentlich einzelne IP-Bereiche).
    Das Modell fällt dann auf den reinen Angriffs-/Abwehrstärke-Fit zurück –
    genauso resilient wie Odds/LLM (siehe engine/sources/odds.py, engine/llm.py)."""
    if not config["model"]["elo"]["enabled"]:
        return None
    try:
        return make_elo_source(team_type).ratings(on_date)
    except requests.RequestException as exc:
        print(f"ELO-Ratings nicht verfügbar ({team_type}): {exc}")
        return None


def predict_matches(
    config: dict,
    targets: list[Match],
    train: list[Match],
    neutral_venue: bool,
    team_type: str,
    odds: dict[tuple[str, str], dict[str, float]] | None = None,
    betting_markets: dict[tuple[str, str], dict] | None = None,
    groq_api_key: str | None = None,
) -> list[dict]:
    """Tipps für die Zielspiele; Modell wird auf den Trainingsspielen gefittet."""
    ref_date = min(m.kickoff_utc for m in targets)
    elo = load_elo(config, team_type, ref_date.date())
    model = build_model(config, neutral_venue, team_type)
    model.fit(train, ref_date, elo=elo)
    trained_n = len([t for t in train if t.has_result])

    scheme = config["kicktipp"]["points"]
    max_tip = config["model"]["max_tip_goals"]
    market_weight = config.get("odds", {}).get("market_weight", 0.0)
    llm_cfg = config.get("llm", {})
    llm_model = llm_cfg.get("model", llm.DEFAULT_MODEL)

    predictions = []
    for m in sorted(targets, key=lambda t: (t.kickoff_utc, t.home_name)):
        matrix = model.score_matrix(m.home_key, m.away_key)
        raw_probs = outcome_probabilities(matrix)

        market_probs = (odds or {}).get((m.home_key, m.away_key))
        if market_probs is not None and market_weight > 0:
            matrix = blend_with_market(model, m.home_key, m.away_key, market_probs, market_weight)

        # Kicktipp-Regel dieser Runde: "nach Elfmeterschießen" - das gewertete
        # Ergebnis geht bei K.o.-Spielen nie unentschieden aus (siehe
        # is_knockout_stage), ein Remis-Tipp kann dort nie Punkte bringen.
        tip, ev = best_tip(matrix, scheme, max_tip, allow_draw=not is_knockout_stage(m.stage_name))
        lam, mu = marginal_expected_goals(matrix)
        probs = outcome_probabilities(matrix)
        paper_bet = build_paper_bet(
            cfg=config.get("paper_betting", {}),
            home=m.home_name,
            away=m.away_name,
            tip=tip,
            raw_probabilities=raw_probs,
            market=(betting_markets or {}).get((m.home_key, m.away_key)),
        )

        advance_tip = None
        if tip[0] == tip[1] and is_knockout_stage(m.stage_name):
            side, p = penalty_shootout_favorite(probs)
            advance_tip = {
                "pick": m.home_name if side == "home" else m.away_name,
                "probability": round(p, 3),
            }

        # Schatten-Anpassung (concept.md Schicht 3, Teil 1): läuft NUR geloggt
        # mit, ändert nie den echten Tipp - siehe engine/llm.py und
        # engine/learn.py (Vertrauensregler entscheidet später über scharf-
        # schalten anhand der hier gesammelten Schattentipper-Punkte). Läuft
        # VOR der Begründung, damit diese auf den News-Check verweisen kann.
        llm_adjustment = None
        news_checked = None
        news_cfg = config.get("llm", {}).get("adjustment", {})
        if news_cfg.get("enabled") and groq_api_key:
            news = news_source.fetch_snippets(
                m.home_name, m.away_name, max_age_days=news_cfg.get("max_news_age_days", 5), now=m.kickoff_utc
            )
            news_checked = len(news)
            proposal = llm.propose_adjustment(
                {"home": m.home_name, "away": m.away_name, "tip": tip}, news, groq_api_key, llm_model
            )
            if proposal is not None:
                adjusted = (
                    max(0, tip[0] + proposal["home_delta"]),
                    max(0, tip[1] + proposal["away_delta"]),
                )
                llm_adjustment = {
                    "tip": list(adjusted),
                    "grund": proposal["grund"],
                    "news_count": len(news),
                }

        elo_values = {"home": (elo or {}).get(m.home_key), "away": (elo or {}).get(m.away_key)}
        template_text = build_begruendung(
            m, lam, mu, probs, tip, ev, advance_tip,
            elo=elo_values, market_probs=market_probs, market_weight=market_weight,
            news_checked=news_checked, llm_adjustment=llm_adjustment,
        )
        begruendung, source = template_text, "template"
        if llm_cfg.get("enabled"):
            context = {
                "home": m.home_name,
                "away": m.away_name,
                "stage": m.stage_name,
                "probabilities": probs,
                "expected_goals": (lam, mu),
                "tip": tip,
                "market_probabilities": market_probs,
                "market_weight": market_weight,
                "elo": elo_values,
                "trained_on_matches": trained_n,
                "news_checked": news_checked,
                "llm_adjustment": llm_adjustment,
            }
            llm_text, source = llm.generate_begruendung(context, groq_api_key, llm_model)
            begruendung = llm_text or template_text

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
                    "elo_favorite": list(elo_favorite_tip(elo_values["home"], elo_values["away"])),
                    "always_draw": list(ALWAYS_DRAW_TIP),
                    **({"llm_adjusted": llm_adjustment["tip"]} if llm_adjustment else {}),
                },
                "factors": {
                    "expected_goals": [round(lam, 2), round(mu, 2)],
                    "probabilities": {k: round(v, 3) for k, v in probs.items()},
                    # Rohes Modell vor Markt-Blend: Grundlage für die
                    # Paper-Betting-Edge, damit die Quote nicht gegen sich
                    # selbst bewertet wird.
                    "raw_probabilities": {k: round(v, 3) for k, v in raw_probs.items()},
                    "elo": {**elo_values, "beta": round(model.params.elo_beta, 4)},
                    # Marktquote (entvigt) und Blend-Gewicht, nur wenn eine
                    # Quote für genau diese Paarung vorlag (siehe engine/market.py)
                    "market": (
                        {**{k: round(v, 3) for k, v in market_probs.items()}, "weight": market_weight}
                        if market_probs is not None
                        else None
                    ),
                    "home_advantage": round(model.params.home_adv, 3),
                    "trained_on_matches": trained_n,
                    # Anzahl geprüfter Schlagzeilen (None = News-Check war aus);
                    # llm_adjustment ist der Schatten-Anpassungsvorschlag des
                    # LLM (siehe oben), None wenn kein harter Grund gefunden wurde
                    "news_checked": news_checked,
                    "llm_adjustment": llm_adjustment,
                },
                "begruendung": begruendung,
                "paper_bet": paper_bet,
                # "llm" oder "template" - Transparenz, welche Quelle den Text
                # geschrieben hat (LLM passt NICHT den Tipp an, siehe engine/llm.py)
                "begruendung_source": source,
            }
        )
    return predictions


def in_tip_window(match: Match, now: datetime, window: timedelta, margin: timedelta) -> bool:
    """Tippbar ist ein Spiel nur im Fenster (now+margin, now+window]:

    - Obergrenze window: so spät wie möglich tippen, damit tagesaktuelle News
      und Quotenbewegungen (kurzfristige Ausfälle!) noch in den Tipp einfließen.
    - Untergrenze margin: nie für bereits laufende oder unmittelbar anstehende
      Spiele tippen - der Fairness-Beweis ("Tipp stand vor Anstoß fest") wäre
      sonst wertlos, und Live-Quoten würden den laufenden Spielstand einpreisen.
    """
    return (
        not match.finished
        and not match.has_placeholder
        and now + margin < match.kickoff_utc <= now + window
    )


def run_predict(config: dict) -> dict | None:
    """Prognostiziert tippbare Spiele im Zeitfenster; None, wenn nichts ansteht.

    Pro Lauf wird höchstens eine Runde bearbeitet (Dateinamen sind rundenbasiert);
    bereits getippte Paarungen werden VOR der teuren Arbeit (Quoten, LLM)
    ausgefiltert - der stündliche Leerlauf kostet so weder API-Kontingent noch Zeit.
    """
    now = datetime.now(timezone.utc)
    timing = config.get("timing", {})
    window = timedelta(hours=timing.get("tip_window_hours", 4))
    margin = timedelta(minutes=timing.get("safety_margin_minutes", 20))
    season = config["season"]
    matches = fetch_competition(config["leagues"], season, force_refresh=True)

    tippable = [m for m in matches if in_tip_window(m, now, window, margin)]
    if not tippable:
        return None

    next_matchday = min(tippable, key=lambda m: m.kickoff_utc).matchday
    stem = f"{config['competition']}_{season}_md{next_matchday:02d}"
    covered = _covered_pairings(stem)
    targets = [
        m for m in tippable
        if m.matchday == next_matchday and (m.home_name, m.away_name) not in covered
    ]
    if not targets:
        return None

    train = [m for m in matches if m.has_result]
    if config["team_type"] == "club":
        lookback = config.get("backtest", {}).get("club", {}).get("lookback_seasons", 2)
        for s in range(season - lookback, season):
            train += [m for m in fetch_competition(config["leagues"], s) if m.has_result]

    load_dotenv()
    odds_date = min(m.kickoff_utc for m in targets).date()
    odds = load_odds(config, odds_date)
    betting_markets = load_betting_markets(config, odds_date)
    groq_api_key = os.environ.get("GROQ_API_KEY")

    predictions = predict_matches(
        config,
        targets,
        train,
        config["neutral_venue"],
        config["team_type"],
        odds,
        betting_markets,
        groq_api_key,
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
            covered |= {(m["home"], m["away"]) for m in data.get("matches", [])}
    return covered


def main(config: dict) -> None:
    report = run_predict(config)
    if report is None:
        print("Kein ungetipptes Spiel im Tipp-Fenster, nichts zu tun.")
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
    # Absichtlich keine Tipp-Details hier: dieser Schritt läuft in GitHub
    # Actions, dessen Logs bei einem öffentlichen Repo für jeden einsehbar
    # sind - der Klartext-Tipp darf erst nach der Versiegelung (Hash) bzw.
    # nach Anstoß (Enthüllung) sichtbar werden. Details stehen lokal in der
    # (gitignorten) JSON-Datei.
    print(f"{len(report['matches'])} Tipps für {report['stage']} (Runde {report['matchday']}) berechnet.")
    print(f"Gespeichert: {out.relative_to(PROJECT_ROOT)}")

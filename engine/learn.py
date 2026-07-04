"""Selbstlernen (concept.md Schicht 4 / Phase 5).

Zwei Lernmechanismen, beide bewusst konservativ (Mindest-Stichproben +
Regularisierung Richtung Default - "zu Beginn vorsichtig, mit wachsender
Datenmenge aggressiver"):

1. LLM-Vertrauensregler: Die news-gestützten Anpassungsvorschläge laufen als
   Schattentipper mit (evaluate: shadow_points["llm_adjusted"]). Erst wenn
   genug Anpassungen abgerechnet sind UND sie in Summe mehr Punkte holen als
   der Haupttipp derselben Spiele, wird der Vorschlag scharf geschaltet
   (predict wendet ihn dann auf den echten Tipp an, ±1 Tor, nie Remis bei
   K.o.). Kostet er messbar Punkte, bleibt/fällt er zurück in den Schatten.

2. Quotengewicht: Wie stark die Buchmacherquote in die Prognose einfließt
   (odds.market_weight), wird aus den abgerechneten Spielen nachjustiert:
   Grid-Search über den Log-Loss des Blends w*Quote + (1-w)*Rohmodell gegen
   die realen Ausgänge, dann Pseudo-Count-Regularisierung Richtung
   config-Default.

Der Zustand liegt versioniert in data/learning/state.json (Repo = Datenbank,
die Lernentwicklung bleibt nachvollziehbar); predict.py liest ihn beim
nächsten Lauf.
"""

import json
import math
from datetime import datetime, timezone

from .config import DATA_DIR, MATCHDAYS_DIR, RESULTS_DIR

LEARNING_DIR = DATA_DIR / "learning"
STATE_FILE = LEARNING_DIR / "state.json"

GRID = [round(w * 0.05, 2) for w in range(0, 21)]  # 0.00 .. 1.00


def load_state() -> dict:
    """Gelernter Zustand für predict.py; {} solange noch nichts gelernt wurde."""
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def collect_samples(competition: str, season: int) -> list[dict]:
    """Abgerechnete Spiele mit allem, was zum Lernen nötig ist.

    Paart results/ (Punkte, Schattenpunkte, Ergebnis) mit matchdays/
    (factors: rohe Modell- und Markt-Wahrscheinlichkeiten) über die Paarung.
    """
    factors_by_pairing = {}
    for md_file in sorted(MATCHDAYS_DIR.glob(f"{competition}_{season}_*.json")):
        data = json.loads(md_file.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            factors_by_pairing[(m["home"], m["away"])] = m.get("factors", {})

    samples = []
    for res_file in sorted(RESULTS_DIR.glob(f"{competition}_{season}_*.json")):
        data = json.loads(res_file.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            if m.get("points") is None or m.get("result") is None:
                continue
            factors = factors_by_pairing.get((m["home"], m["away"]), {})
            samples.append(
                {
                    "points": m["points"],
                    "llm_shadow_points": (m.get("shadow_points") or {}).get("llm_adjusted"),
                    "result": tuple(m["result"]),
                    "raw_probabilities": factors.get("raw_probabilities"),
                    "market": factors.get("market"),
                }
            )
    return samples


def llm_trust_report(samples: list[dict], min_samples: int) -> dict:
    """Saldo der LLM-Schatten-Anpassungen gegen den Haupttipp derselben Spiele."""
    adjusted = [s for s in samples if s["llm_shadow_points"] is not None]
    delta = sum(s["llm_shadow_points"] - s["points"] for s in adjusted)
    trusted = len(adjusted) >= min_samples and delta > 0
    return {
        "samples": len(adjusted),
        "min_samples": min_samples,
        "points_delta": delta,
        "trusted": trusted,
    }


def _outcome(result: tuple[int, int]) -> str:
    diff = result[0] - result[1]
    return "home" if diff > 0 else "away" if diff < 0 else "draw"


def _log_loss(samples: list[dict], weight: float) -> float:
    total = 0.0
    for s in samples:
        raw, market = s["raw_probabilities"], s["market"]
        blended = {
            k: weight * market[k] + (1 - weight) * raw[k] for k in ("home", "draw", "away")
        }
        total += -math.log(max(blended[_outcome(s["result"])], 1e-9))
    return total / len(samples)


def market_weight_report(
    samples: list[dict], default_weight: float, min_samples: int, pseudo_samples: int
) -> dict:
    """Grid-Search über den Blend-Log-Loss + Pseudo-Count-Regularisierung.

    applied = (n*best + k*default) / (n+k): mit wenig Daten bleibt das Gewicht
    beim Default, mit wachsender Stichprobe zieht es Richtung Optimum.
    """
    usable = [
        s for s in samples
        if s["raw_probabilities"] and s["market"]
        and all(k in s["market"] for k in ("home", "draw", "away"))
    ]
    report = {
        "samples": len(usable),
        "min_samples": min_samples,
        "default": default_weight,
        "best": None,
        "applied": default_weight,
    }
    if len(usable) < min_samples:
        return report

    losses = {w: round(_log_loss(usable, w), 4) for w in GRID}
    best = min(losses, key=losses.get)
    n, k = len(usable), pseudo_samples
    report.update(
        best=best,
        applied=round((n * best + k * default_weight) / (n + k), 3),
        log_loss={"best": losses[best], "default": losses.get(round(default_weight, 2))},
    )
    return report


def main(config: dict) -> None:
    learning_cfg = config.get("learning", {})
    if not learning_cfg.get("enabled"):
        print("Selbstlernen deaktiviert (config.yaml: learning.enabled).")
        return

    samples = collect_samples(config["competition"], config["season"])
    state = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "competition": config["competition"],
        "season": config["season"],
        "scored_matches": len(samples),
        "llm_trust": llm_trust_report(
            samples, learning_cfg.get("llm_trust", {}).get("min_samples", 10)
        ),
        "market_weight": market_weight_report(
            samples,
            config.get("odds", {}).get("market_weight", 0.7),
            learning_cfg.get("market_weight", {}).get("min_samples", 20),
            learning_cfg.get("market_weight", {}).get("pseudo_samples", 20),
        ),
    }

    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    trust = state["llm_trust"]
    weight = state["market_weight"]
    print(
        f"Lernzustand aktualisiert ({len(samples)} abgerechnete Spiele): "
        f"LLM-Regler {'SCHARF' if trust['trusted'] else 'Schatten'} "
        f"({trust['samples']}/{trust['min_samples']} Anpassungen, Saldo {trust['points_delta']:+d}), "
        f"Quotengewicht {weight['applied']} (Default {weight['default']}, "
        f"{weight['samples']}/{weight['min_samples']} Spiele)"
    )

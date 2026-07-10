"""Paper-Kombiwette: klassischer 2-3er-Akkumulator ohne echte Wettabgabe.

Kombiwetten multiplizieren die Quoten ihrer Beine - und für Gelegenheitstipper
auch die Buchmachermarge (~5% pro Bein, ~15% bei einer 3er-Kombi), weshalb
Buchmacher sie so aggressiv bewerben. Der Automat dreht das Prinzip um: Ein
Bein kommt nur in die Kombi, wenn es auch als Einzelwette Value hätte
(Modellwahrscheinlichkeit x Quote > 1) UND wahrscheinlich genug ist. Dann
multipliziert sich statt der Marge die eigene Edge; der Preis ist Varianz
(zwei Beine à 65% treffen kombiniert nur noch zu 42%).

Eigenes Kombi-Fenster (Default 72h) statt des 4h-Tipp-Fensters: In K.o.-Runden
liegen fast nie zwei Spiele im selben Tipp-Fenster. Es läuft höchstens eine
Kombi gleichzeitig; der Einsatz kommt aus konservativem Fractional Kelly auf
die kombinierte Wahrscheinlichkeit, mit Mindesteinsatz (darunter gilt die
Kombi als "nicht sicher genug" und entfällt) und Deckel.

Fairness wie bei den Tipps (concept.md §5): Die Kombi wird komplett versiegelt
- öffentlich sind nur Hash und Enthüllungszeitpunkt (data/kombi/<id>.json),
der Klartext liegt Fernet-verschlüsselt daneben (<id>.enc). Enthüllt wird erst
nach Anstoß des LETZTEN Beins, vorher würde die Kombi die Tendenz noch
versiegelter Einzeltipps verraten.

Abgerechnet wird auf das 90-Minuten-Ergebnis (wie die Einzel-Paper-Wetten).
Ein manuell als "void" markiertes Bein (abgesagtes Spiel) zählt mit Quote 1.0
- die Standard-Buchmacherregel (z.B. Tipico).
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import KOMBI_DIR, load_dotenv
from .paper_betting import kelly_fraction, selection_label
from .predict import build_model, load_betting_markets, load_elo, outcome_probabilities
from .seal import _fernet, require_secret
from .sources.openligadb import fetch_competition
from .teams import normalize

# Diese Felder (plus Salt) gehen in den Hash - dieselbe Rezeptur wie bei den
# Tipps (engine/seal.py), nachrechenbar nach der Enthüllung.
HASHED_FIELDS = ("legs", "total_odds", "stake_eur", "created_utc")

# Beim Enthüllen werden zusätzlich diese Felder veröffentlicht.
REVEALED_FIELDS = (
    "legs",
    "leg_count",
    "total_odds",
    "combined_probability",
    "combined_edge",
    "stake_eur",
    "potential_payout_eur",
    "staking",
    "created_utc",
)


def canonical_payload(kombi: dict, salt: str) -> str:
    core = {field: kombi[field] for field in HASHED_FIELDS}
    core["salt"] = salt
    return json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(kombi: dict, salt: str) -> str:
    return hashlib.sha256(canonical_payload(kombi, salt).encode("utf-8")).hexdigest()


def choose_kombi(candidates: list[dict], cfg: dict, now: datetime | None = None) -> dict | None:
    """Baut die Kombi aus den Kandidaten-Spielen; None, wenn keine zustande kommt.

    candidates: [{home, away, kickoff_utc, probabilities, market|None}, ...] -
    probabilities sind die ROHEN Modellwahrscheinlichkeiten (vor Markt-Blend),
    damit die Quote nicht gegen sich selbst bewertet wird (wie paper_betting).
    """
    min_p = float(cfg.get("min_leg_probability", 0.55))
    min_edge = float(cfg.get("min_leg_edge", 0.02))
    max_legs = int(cfg.get("max_legs", 3))

    legs = []
    for c in candidates:
        market = c.get("market")
        if market is None:
            continue
        probs = c["probabilities"]
        selection = max(probs, key=probs.get)
        p = probs[selection]
        odds = market["odds"][selection]
        edge = p * odds - 1.0
        if p < min_p or edge < min_edge:
            continue
        legs.append(
            {
                "home": c["home"],
                "away": c["away"],
                "kickoff_utc": c["kickoff_utc"],
                "selection": selection,
                "selection_label": selection_label(selection, c["home"], c["away"]),
                "odds_decimal": round(odds, 3),
                "model_probability": round(p, 4),
                "implied_probability": round(1.0 / odds, 4) if odds > 0 else None,
                "edge": round(edge, 4),
                "source": market["source"],
                "source_label": market["source_label"],
            }
        )

    # Die sichersten Beine zuerst; eine Kombi braucht mindestens zwei.
    legs.sort(key=lambda leg: leg["model_probability"], reverse=True)
    legs = legs[:max_legs]
    if len(legs) < 2:
        return None

    combined_p = 1.0
    total_odds = 1.0
    for leg in legs:
        combined_p *= leg["model_probability"]
        total_odds *= leg["odds_decimal"]

    staking = cfg.get("staking", {})
    bankroll = float(staking.get("bankroll_eur", 1000.0))
    fraction = float(staking.get("kelly_fraction", 0.25))
    max_stake = float(staking.get("max_stake_eur", 100.0))
    min_stake = float(staking.get("min_stake_eur", 10.0))

    full_kelly = kelly_fraction(combined_p, total_odds)
    stake = min(bankroll * full_kelly * fraction, max_stake)
    if stake < min_stake:
        return None  # nicht sicher genug für den Mindesteinsatz
    stake = round(stake, 2)

    created = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "type": "kombi",
        "mode": "paper",
        "market": "h2h_90min",
        "created_utc": created,
        "legs": legs,
        "leg_count": len(legs),
        "total_odds": round(total_odds, 3),
        "combined_probability": round(combined_p, 4),
        "combined_edge": round(combined_p * total_odds - 1.0, 4),
        "stake_eur": stake,
        "potential_payout_eur": round(stake * total_odds, 2),
        "staking": {
            "mode": "fractional_kelly",
            "bankroll_eur": round(bankroll, 2),
            "kelly_fraction": round(full_kelly, 4),
            "applied_kelly_fraction": round(fraction, 4),
            "min_stake_eur": round(min_stake, 2),
            "max_stake_eur": round(max_stake, 2),
        },
    }


def next_kombi_id(competition: str, season: int, kombi_dir: Path = KOMBI_DIR) -> str:
    existing = list(kombi_dir.glob(f"{competition}_{season}_kombi*.json"))
    return f"{competition}_{season}_kombi{len(existing) + 1:02d}"


def open_kombi_exists(kombi_dir: Path = KOMBI_DIR) -> bool:
    """Versiegelt oder enthüllt-aber-unabgerechnet = offen; es läuft nur eine."""
    for path in kombi_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "sealed" or (data.get("status") == "revealed" and "result" not in data):
            return True
    return False


def seal_kombi(kombi: dict, secret: str, kombi_dir: Path = KOMBI_DIR) -> Path:
    """Öffentlich nur Hash + Enthüllungszeitpunkt; Klartext verschlüsselt daneben."""
    salt = secrets.token_hex(16)
    h = payload_hash(kombi, salt)
    reveal_after = max(leg["kickoff_utc"] for leg in kombi["legs"])
    public = {
        "id": kombi["id"],
        "competition": kombi["competition"],
        "season": kombi["season"],
        "type": "kombi",
        "mode": "paper",
        "market": kombi["market"],
        "status": "sealed",
        "hash": h,
        "sealed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reveal_after_utc": reveal_after,
    }
    kombi_dir.mkdir(parents=True, exist_ok=True)
    public_path = kombi_dir / f"{kombi['id']}.json"
    public_path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")

    private = {**kombi, "salt": salt, "hash": h}
    encrypted = _fernet(secret).encrypt(json.dumps(private, ensure_ascii=False).encode("utf-8"))
    (kombi_dir / f"{kombi['id']}.enc").write_bytes(encrypted)
    return public_path


def unseal_due(secret: str, kombi_dir: Path = KOMBI_DIR, now: datetime | None = None) -> list[Path]:
    """Enthüllt Kombis, deren letztes Bein angepfiffen wurde."""
    now = now or datetime.now(timezone.utc)
    fernet = _fernet(secret)
    changed = []
    for enc_path in sorted(kombi_dir.glob("*.enc")):
        public_path = enc_path.with_suffix(".json")
        public = json.loads(public_path.read_text(encoding="utf-8"))
        reveal_after = datetime.strptime(public["reveal_after_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if public["status"] != "sealed" or now < reveal_after:
            continue
        private = json.loads(fernet.decrypt(enc_path.read_bytes()).decode("utf-8"))
        public.update({field: private[field] for field in REVEALED_FIELDS if field in private})
        public["salt"] = private["salt"]
        public["status"] = "revealed"
        public_path.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        enc_path.unlink()
        changed.append(public_path)
    return changed


def settle_kombi(kombi: dict, results90_by_pairing: dict) -> dict | None:
    """Abrechnung gegen 90-Minuten-Ergebnisse; None, solange ein Bein offen ist."""
    total_odds = 1.0
    all_won = True
    settled_legs = []
    for leg in kombi["legs"]:
        if leg.get("void"):
            # Abgesagtes Spiel: Bein zählt mit Quote 1.0 (Buchmacher-Standard)
            settled_legs.append({**leg, "won": True})
            continue
        result = results90_by_pairing.get((normalize(leg["home"]), normalize(leg["away"])))
        if result is None:
            return None
        diff = result[0] - result[1]
        actual = "home" if diff > 0 else "away" if diff < 0 else "draw"
        won = leg["selection"] == actual
        settled_legs.append({**leg, "result": list(result), "won": won})
        total_odds *= leg["odds_decimal"]
        all_won = all_won and won

    stake = float(kombi["stake_eur"])
    payout = stake * total_odds if all_won else 0.0
    return {
        "legs": settled_legs,
        "result": {
            "outcome": "won" if all_won else "lost",
            "stake_eur": round(stake, 2),
            "payout_eur": round(payout, 2),
            "profit_eur": round(payout - stake, 2),
            "settled_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def settle_open(results90_by_pairing: dict, kombi_dir: Path = KOMBI_DIR) -> list[Path]:
    """Rechnet alle enthüllten, noch offenen Kombis ab, deren Beine fertig sind."""
    changed = []
    for path in sorted(kombi_dir.glob("*.json")):
        kombi = json.loads(path.read_text(encoding="utf-8"))
        if kombi.get("status") != "revealed" or "result" in kombi:
            continue
        settled = settle_kombi(kombi, results90_by_pairing)
        if settled is None:
            continue
        kombi.update(settled)
        path.write_text(json.dumps(kombi, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(path)
    return changed


def run_kombi(config: dict, now: datetime | None = None) -> dict | None:
    """Baut die Kombi aus den Spielen im Kombi-Fenster; None, wenn keine zustande kommt."""
    cfg = config["kombi"]
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=cfg.get("window_hours", 72))
    margin = timedelta(minutes=config.get("timing", {}).get("safety_margin_minutes", 20))
    season = config["season"]
    matches = fetch_competition(config["leagues"], season, force_refresh=True)

    upcoming = [
        m for m in matches
        if not m.finished and not m.has_placeholder and now + margin < m.kickoff_utc <= now + window
    ]
    if len(upcoming) < 2:
        return None

    train = [m for m in matches if m.has_result]
    if config["team_type"] == "club":
        lookback = config.get("backtest", {}).get("club", {}).get("lookback_seasons", 2)
        for s in range(season - lookback, season):
            train += [m for m in fetch_competition(config["leagues"], s) if m.has_result]

    load_dotenv()
    ref_date = min(m.kickoff_utc for m in upcoming)
    elo = load_elo(config, config["team_type"], ref_date.date())
    model = build_model(config, config["neutral_venue"], config["team_type"])
    model.fit(train, ref_date, elo=elo)
    markets = load_betting_markets(config, ref_date.date())

    candidates = []
    for m in sorted(upcoming, key=lambda t: (t.kickoff_utc, t.home_name)):
        probs = outcome_probabilities(model.score_matrix(m.home_key, m.away_key))
        candidates.append(
            {
                "home": m.home_name,
                "away": m.away_name,
                "kickoff_utc": m.kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "probabilities": probs,
                "market": markets.get((m.home_key, m.away_key)),
            }
        )
    return choose_kombi(candidates, cfg, now=now)


def main_unseal() -> None:
    # Secret erst verlangen, wenn wirklich eine Kombi fällig sein kann -
    # so bleibt der Leerlauf-Lauf grün (wie seal.main_unseal).
    if not list(KOMBI_DIR.glob("*.enc")):
        return
    for path in unseal_due(require_secret()):
        print(f"Kombi enthüllt: {path.name}")


def main(config: dict) -> None:
    cfg = config.get("kombi") or {}
    if not cfg.get("enabled"):
        print("Kombiwetten sind deaktiviert (config.yaml: kombi.enabled).")
        return
    if open_kombi_exists():
        print("Es läuft bereits eine offene Kombi, nichts zu tun.")
        return

    kombi = run_kombi(config)
    if kombi is None:
        print("Keine Kombi: zu wenige Value-Beine im Fenster oder Einsatz unter Minimum.")
        return

    kombi["competition"] = config["competition"]
    kombi["season"] = config["season"]
    kombi["id"] = next_kombi_id(config["competition"], config["season"])
    path = seal_kombi(kombi, require_secret())
    # Keine Auswahl-Details ins Log: GitHub-Actions-Logs sind öffentlich,
    # die Kombi ist bis zum Anstoß des letzten Beins versiegelt.
    print(f"{kombi['leg_count']}er-Kombi versiegelt: {path.name} (Enthüllung nach dem letzten Anstoß).")

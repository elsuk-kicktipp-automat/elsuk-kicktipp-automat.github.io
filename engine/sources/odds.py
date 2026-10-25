"""Buchmacherquoten von The Odds API (the-odds-api.com, Free Tier 500 Requests/Monat).

Liefert entvigte (um die Buchmacher-Marge bereinigte) Heimsieg-/Remis-/
Auswärtssieg-Wahrscheinlichkeiten pro Spiel – der stärkste Einzel-Prior fürs
Modell (siehe concept.md §3). Wegen des knappen Freikontingents wird jede
Antwort mit Abrufzeit gecacht und erst neu geholt, wenn sie älter als
`max_age_minutes` ist. Der Workflow hebt den Cache zwischen den Läufen auf
(actions/cache); ohne das holte jeder 15-Minuten-Lauf neu und das Kontingent
war am 25.10.2026 nach drei Spieltagen leer.

Kein historischer Endpunkt im Free Tier – Backtests laufen daher ohne
Quoten-Term (config: `model.odds.enabled: false` im Abschnitt `backtest`).
"""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import CACHE_DIR, MAPPINGS_DIR
from ..teams import normalize

API_BASE = "https://api.the-odds-api.com/v4"


def _load_mapping() -> dict[str, str]:
    """OpenLigaDB-Name -> Name bei The Odds API, Schlüssel normalisiert."""
    raw = json.loads((MAPPINGS_DIR / "odds_teams.json").read_text(encoding="utf-8"))
    return {normalize(k): v for k, v in raw.items() if not k.startswith("_")}


# Antwortcodes von The Odds API, wenn das Monatskontingent eines Schlüssels leer ist
QUOTA_EXHAUSTED = (401, 429)
KEY_LABELS = ("Hauptschlüssel", "Ersatzschlüssel")


def fetch_raw_odds(
    api_key: str | Sequence[str],
    sport_key: str,
    regions: str = "eu",
    cache_dir: Path = CACHE_DIR,
    max_age_minutes: int = 60,
    now: datetime | None = None,
) -> list[dict]:
    """Rohe h2h-Quoten aller anstehenden Spiele einer Sportart; aus dem Cache,
    solange der letzte Abruf höchstens `max_age_minutes` alt ist.

    `api_key` darf eine Liste sein: Der zweite Schlüssel wird nur benutzt, wenn
    der erste kein Kontingent mehr hat (Ersatz bis zum Monatswechsel)."""
    now = now or datetime.now(timezone.utc)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"odds_{sport_key}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        # Ältere Cache-Dateien waren eine nackte Liste ohne Abrufzeit: gelten als veraltet
        if isinstance(cached, dict) and "fetched_utc" in cached:
            fetched = datetime.fromisoformat(cached["fetched_utc"])
            if now - fetched <= timedelta(minutes=max_age_minutes):
                return cached["events"]

    keys = [api_key] if isinstance(api_key, str) else [k for k in api_key if k]
    for index, key in enumerate(keys):
        label = KEY_LABELS[min(index, len(KEY_LABELS) - 1)]
        resp = requests.get(
            f"{API_BASE}/sports/{sport_key}/odds",
            params={"apiKey": key, "regions": regions, "markets": "h2h", "oddsFormat": "decimal"},
            timeout=30,
        )
        if resp.status_code in QUOTA_EXHAUSTED and index < len(keys) - 1:
            print(f"{label}: Kontingent erschöpft (HTTP {resp.status_code}), versuche {KEY_LABELS[index + 1]}.")
            continue
        break
    # Restkontingent steht im Antwortkopf (keine Geheimnisse, darf ins öffentliche Log)
    remaining = resp.headers.get("x-requests-remaining")
    if remaining is not None:
        print(f"Quoten neu geladen ({sport_key}, {label}), Restkontingent diesen Monat: {remaining}")
    resp.raise_for_status()
    raw = resp.json()
    cache_file.write_text(
        json.dumps({"fetched_utc": now.isoformat(), "events": raw}, ensure_ascii=False),
        encoding="utf-8",
    )
    return raw


def _devig(outcomes: list[dict]) -> dict[str, float] | None:
    """Entfernt die Buchmacher-Marge: p_i = (1/quote_i) / Summe(1/quote_j)."""
    prices = {o["name"]: o["price"] for o in outcomes if o.get("price")}
    if len(prices) < 3:
        return None
    raw = {name: 1.0 / price for name, price in prices.items()}
    total = sum(raw.values())
    return {name: p / total for name, p in raw.items()}


def _decimal_prices(event: dict, outcomes: list[dict]) -> dict[str, float] | None:
    prices = {o["name"]: float(o["price"]) for o in outcomes if o.get("price")}
    home_p = prices.get(event["home_team"])
    away_p = prices.get(event["away_team"])
    draw_p = prices.get("Draw")
    if home_p is None or away_p is None or draw_p is None:
        return None
    return {"home": home_p, "draw": draw_p, "away": away_p}


def parse_probabilities(raw_events: list[dict]) -> dict[tuple[str, str], dict[str, float]]:
    """{(home_key, away_key) -> {"home": p, "draw": p, "away": p}}, über alle
    Buchmacher gemittelt (robuster als ein einzelner Anbieter)."""
    mapping = _load_mapping()
    source_to_key = {v: k for k, v in mapping.items()}
    result = {}

    for event in raw_events:
        home_key = source_to_key.get(event.get("home_team"))
        away_key = source_to_key.get(event.get("away_team"))
        if home_key is None or away_key is None:
            continue

        devigged_per_bookmaker = []
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                devigged = _devig(market.get("outcomes", []))
                if devigged is None:
                    continue
                home_p = devigged.get(event["home_team"])
                away_p = devigged.get(event["away_team"])
                draw_p = devigged.get("Draw")
                if home_p is not None and away_p is not None and draw_p is not None:
                    devigged_per_bookmaker.append({"home": home_p, "draw": draw_p, "away": away_p})

        if not devigged_per_bookmaker:
            continue
        n = len(devigged_per_bookmaker)
        result[(home_key, away_key)] = {
            outcome: sum(d[outcome] for d in devigged_per_bookmaker) / n
            for outcome in ("home", "draw", "away")
        }

    return result


def parse_betting_markets(
    raw_events: list[dict],
    preferred_bookmakers: list[str] | tuple[str, ...] = ("tipico_de",),
) -> dict[tuple[str, str], dict]:
    """Dezimalquoten für Paper-Betting.

    Rückgabe je Paarung:
    {
      "source": "tipico_de" | "market_average",
      "source_label": "Tipico (DE)" | "Marktdurchschnitt",
      "bookmaker_count": n,
      "odds": {"home": 2.1, "draw": 3.4, "away": 3.2}
    }
    """
    mapping = _load_mapping()
    source_to_key = {v: k for k, v in mapping.items()}
    result = {}

    for event in raw_events:
        home_key = source_to_key.get(event.get("home_team"))
        away_key = source_to_key.get(event.get("away_team"))
        if home_key is None or away_key is None:
            continue

        bookmaker_prices = []
        by_key = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                prices = _decimal_prices(event, market.get("outcomes", []))
                if prices is None:
                    continue
                entry = {
                    "key": bookmaker.get("key"),
                    "label": bookmaker.get("title") or bookmaker.get("key"),
                    "last_update": bookmaker.get("last_update"),
                    "odds": prices,
                }
                bookmaker_prices.append(entry)
                by_key[entry["key"]] = entry

        if not bookmaker_prices:
            continue

        chosen = next((by_key[key] for key in preferred_bookmakers if key in by_key), None)
        if chosen is not None:
            result[(home_key, away_key)] = {
                "source": chosen["key"],
                "source_label": chosen["label"],
                "bookmaker_count": len(bookmaker_prices),
                "last_update": chosen["last_update"],
                "odds": chosen["odds"],
            }
            continue

        n = len(bookmaker_prices)
        avg = {
            outcome: sum(b["odds"][outcome] for b in bookmaker_prices) / n
            for outcome in ("home", "draw", "away")
        }
        result[(home_key, away_key)] = {
            "source": "market_average",
            "source_label": "Marktdurchschnitt",
            "bookmaker_count": n,
            "last_update": max((b["last_update"] or "") for b in bookmaker_prices) or None,
            "odds": avg,
        }

    return result


def load_probabilities(
    api_key: str | Sequence[str],
    sport_key: str,
    regions: str = "eu",
    cache_dir: Path = CACHE_DIR,
    max_age_minutes: int = 60,
) -> dict[tuple[str, str], dict[str, float]]:
    """Best-effort: liefert {} statt eines Fehlers, wenn die API nicht erreichbar
    ist oder der Sport-Key nicht (mehr) existiert – Quoten sind ein Prior, kein
    Hard-Requirement (System bleibt ohne sie funktionsfähig, siehe concept.md)."""
    try:
        raw = fetch_raw_odds(api_key, sport_key, regions, cache_dir, max_age_minutes)
    except requests.RequestException as exc:
        print(f"Quoten nicht verfügbar ({sport_key}): {exc}")
        return {}
    if not isinstance(raw, list):
        # z.B. {"message": "Unknown sport ..."} bei falschem sport_key
        print(f"Quoten-API lieferte kein Array für {sport_key}: {raw}")
        return {}
    return parse_probabilities(raw)


def load_betting_markets(
    api_key: str | Sequence[str],
    sport_key: str,
    regions: str = "eu",
    cache_dir: Path = CACHE_DIR,
    max_age_minutes: int = 60,
    preferred_bookmakers: list[str] | tuple[str, ...] = ("tipico_de",),
) -> dict[tuple[str, str], dict]:
    """Best-effort Dezimalquoten für Paper-Betting; nutzt denselben Cache wie load_probabilities."""
    try:
        raw = fetch_raw_odds(api_key, sport_key, regions, cache_dir, max_age_minutes)
    except requests.RequestException as exc:
        print(f"Paper-Betting-Quoten nicht verfügbar ({sport_key}): {exc}")
        return {}
    if not isinstance(raw, list):
        print(f"Quoten-API lieferte kein Array für {sport_key}: {raw}")
        return {}
    return parse_betting_markets(raw, preferred_bookmakers)

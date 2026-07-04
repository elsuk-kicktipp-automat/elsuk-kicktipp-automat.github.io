"""OpenLigaDB-Client (api.openligadb.de, kein API-Key nötig).

Lädt komplette Saisons und cacht die Roh-JSON-Antworten unter data/cache/,
damit Backtests nicht bei jedem Lauf die API belasten. Ein Wettbewerb kann
über mehrere OpenLigaDB-Ligen verteilt sein – fetch_competition führt sie
zusammen (aktuell: nur 'wm26').

Wertungsregel ("nach Elfmeterschießen", wie die Kicktipp-Runde eingestellt
ist): Es zählt die höchste vorhandene Ausbaustufe des Ergebnisses -
Elfmeterschießen (resultTypeID 5) vor Verlängerung (4) vor Endergebnis nach
90 Minuten (2). Explizite Priorität statt Vertrauen in die typeID-2-Semantik:
OpenLigaDB hat dort schon rückwirkend mal die Summe inkl. Elfmeter, mal das
reine 90-Minuten-Ergebnis gepflegt.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json

import requests

from ..config import CACHE_DIR
from ..teams import is_placeholder, normalize

API_BASE = "https://api.openligadb.de"

# Höchste Ausbaustufe zuerst: 5 = nach Elfmeterschießen, 4 = nach
# Verlängerung, 2 = Endergebnis nach 90 Minuten (Kicktipp-Regel "n.E.":
# Tore aus Verlängerung und Elfmeterschießen zählen zum Wertungsergebnis).
RESULT_TYPE_PRIORITY = (5, 4, 2)


@dataclass(frozen=True)
class Match:
    home_name: str
    away_name: str
    home_goals: int | None
    away_goals: int | None
    kickoff_utc: datetime
    matchday: int
    stage_name: str
    finished: bool
    # Ergebnis nach 90 Minuten (resultTypeID 2): Buchmacher-1X2-Wetten werden
    # darauf abgerechnet, nicht auf das n.E.-Gesamtergebnis (Paper-Betting).
    # Defaults, weil viele Test-Fixtures nur die Kernfelder konstruieren.
    home_goals_90: int | None = None
    away_goals_90: int | None = None

    @property
    def home_key(self) -> str:
        return normalize(self.home_name)

    @property
    def away_key(self) -> str:
        return normalize(self.away_name)

    @property
    def has_placeholder(self) -> bool:
        return is_placeholder(self.home_name) or is_placeholder(self.away_name)

    @property
    def has_result(self) -> bool:
        return (
            self.finished
            and not self.has_placeholder
            and self.home_goals is not None
            and self.away_goals is not None
        )


def _extract_scores(match_json: dict) -> tuple[int | None, int | None, int | None, int | None]:
    """(final_heim, final_gast, 90min_heim, 90min_gast) eines Spiels.

    Final = höchste Ausbaustufe (Kicktipp-"n.E."-Wertung); 90 Minuten =
    resultTypeID 2 (Basis der Buchmacher-1X2-Abrechnung beim Paper-Betting).
    Ohne typeID-2-Eintrag (Alt-Daten) gilt der Fallback für beide.
    """
    results = match_json.get("matchResults") or []
    by_type = {r.get("resultTypeID"): r for r in results}
    # Fallback für Alt-Daten ohne bekannte Typen: letzter Eintrag
    # (= höchste resultOrderID, in der Praxis das Endergebnis)
    fallback = results[-1] if results else None
    final = next((by_type[t] for t in RESULT_TYPE_PRIORITY if t in by_type), fallback)
    ninety = by_type.get(2, fallback)
    if final is None:
        return None, None, None, None
    return (
        final.get("pointsTeam1"),
        final.get("pointsTeam2"),
        ninety.get("pointsTeam1") if ninety else None,
        ninety.get("pointsTeam2") if ninety else None,
    )


def parse_matches(raw: list[dict]) -> list[Match]:
    matches = []
    for m in raw:
        home_goals, away_goals, home_goals_90, away_goals_90 = _extract_scores(m)
        matches.append(
            Match(
                home_name=m["team1"]["teamName"],
                away_name=m["team2"]["teamName"],
                home_goals=home_goals,
                away_goals=away_goals,
                home_goals_90=home_goals_90,
                away_goals_90=away_goals_90,
                kickoff_utc=datetime.fromisoformat(
                    m["matchDateTimeUTC"].replace("Z", "+00:00")
                ),
                matchday=m["group"]["groupOrderID"],
                stage_name=m["group"]["groupName"],
                finished=bool(m.get("matchIsFinished")),
            )
        )
    return matches


def fetch_season(
    league: str,
    season: int,
    cache_dir: Path = CACHE_DIR,
    force_refresh: bool = False,
) -> list[Match]:
    """Alle Spiele einer Saison, aus dem Cache oder frisch von der API."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{league}_{season}.json"

    if cache_file.exists() and not force_refresh:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        resp = requests.get(f"{API_BASE}/getmatchdata/{league}/{season}", timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        cache_file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    return parse_matches(raw)


def fetch_competition(
    leagues: list[str],
    season: int,
    cache_dir: Path = CACHE_DIR,
    force_refresh: bool = False,
) -> list[Match]:
    """Spiele aller Teil-Ligen eines Wettbewerbs, sortiert nach Anstoß."""
    matches = [
        m
        for league in leagues
        for m in fetch_season(league, season, cache_dir, force_refresh)
    ]
    return sorted(matches, key=lambda m: (m.kickoff_utc, m.home_name))

"""OpenLigaDB-Client (api.openligadb.de, kein API-Key nötig).

Lädt komplette Saisons und cacht die Roh-JSON-Antworten unter data/cache/,
damit Backtests nicht bei jedem Lauf die API belasten. Ein Wettbewerb kann
über mehrere OpenLigaDB-Ligen verteilt sein (WM 2026: Gruppenphase 'wm2026'
+ K.o.-Runde 'mb') – fetch_competition führt sie zusammen.

Wertungsregel: resultTypeID 2 ("Endergebnis") zählt. Bei K.o.-Spielen pflegt
die Community dort das Ergebnis ohne Elfmeterschießen ("Endergebniss (o.E.)"),
ein Unentschieden ist also ein gültiger Ausgang.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json

import requests

from ..config import PROJECT_ROOT
from ..teams import is_placeholder, normalize

API_BASE = "https://api.openligadb.de"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"

FINAL_RESULT_TYPE_ID = 2


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


def _extract_final_score(match_json: dict) -> tuple[int | None, int | None]:
    results = match_json.get("matchResults") or []
    final = next(
        (r for r in results if r.get("resultTypeID") == FINAL_RESULT_TYPE_ID),
        results[-1] if results else None,
    )
    if final is None:
        return None, None
    return final.get("pointsTeam1"), final.get("pointsTeam2")


def parse_matches(raw: list[dict]) -> list[Match]:
    matches = []
    for m in raw:
        home_goals, away_goals = _extract_final_score(m)
        matches.append(
            Match(
                home_name=m["team1"]["teamName"],
                away_name=m["team2"]["teamName"],
                home_goals=home_goals,
                away_goals=away_goals,
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

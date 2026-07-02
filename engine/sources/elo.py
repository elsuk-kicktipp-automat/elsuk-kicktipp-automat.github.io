"""ELO-Ratings: gemeinsame Schnittstelle mit zwei Adaptern.

- ClubEloSource (Vereine): api.clubelo.com liefert datierte CSV-Snapshots –
  auch historisch, damit Backtests die ELO-Stände des jeweiligen Spieltags
  nutzen können.
- NationalEloSource (Nationalteams): eloratings.net liefert nur den aktuellen
  Stand (World.tsv + en.teams.tsv für Namen).

Beide Adapter liefern ratings(date) als Dict {normalisierter OpenLigaDB-Name
-> Rating}. Die Zuordnung OpenLigaDB-Name -> Quell-Name liegt als editierbares
JSON unter data/mappings/. Auswahl über config.yaml (team_type: club | national).
"""

import csv
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from ..config import PROJECT_ROOT
from ..teams import normalize

CACHE_DIR = PROJECT_ROOT / "data" / "cache"
MAPPINGS_DIR = PROJECT_ROOT / "data" / "mappings"

CLUBELO_API = "http://api.clubelo.com"
ELORATINGS_RATINGS_URL = "https://eloratings.net/World.tsv"
ELORATINGS_TEAMS_URL = "https://eloratings.net/en.teams.tsv"


def _cached_get(url: str, cache_file: Path) -> str:
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # eloratings.net deklariert kein Charset
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(resp.text, encoding="utf-8")
    return resp.text


def _load_mapping(filename: str) -> dict[str, str]:
    """OpenLigaDB-Name -> Name in der ELO-Quelle, Schlüssel normalisiert."""
    raw = json.loads((MAPPINGS_DIR / filename).read_text(encoding="utf-8"))
    return {normalize(k): v for k, v in raw.items() if not k.startswith("_")}


class ClubEloSource:
    """clubelo.com: CSV 'Rank,Club,Country,Level,Elo,From,To' pro Stichtag."""

    mapping_file = "clubs.json"

    def ratings(self, on_date: date | None = None) -> dict[str, float]:
        on_date = on_date or datetime.now(timezone.utc).date()
        text = _cached_get(
            f"{CLUBELO_API}/{on_date.isoformat()}",
            CACHE_DIR / f"clubelo_{on_date.isoformat()}.csv",
        )
        by_source_name = {
            row["Club"]: float(row["Elo"]) for row in csv.DictReader(io.StringIO(text))
        }
        mapping = _load_mapping(self.mapping_file)
        return {
            key: by_source_name[source_name]
            for key, source_name in mapping.items()
            if source_name in by_source_name
        }


class NationalEloSource:
    """eloratings.net: World.tsv (Rang, Code, Rating, ...) – nur aktueller Stand."""

    mapping_file = "national_teams.json"

    def ratings(self, on_date: date | None = None) -> dict[str, float]:
        # eloratings.net bietet keine historischen Stände; on_date steuert nur
        # den Tages-Cache. Backtests tragen dadurch einen leichten Lookahead-Bias.
        cache_day = (on_date or datetime.now(timezone.utc).date()).isoformat()
        ratings_tsv = _cached_get(
            ELORATINGS_RATINGS_URL, CACHE_DIR / f"eloratings_{cache_day}.tsv"
        )
        teams_tsv = _cached_get(
            ELORATINGS_TEAMS_URL, CACHE_DIR / "eloratings_teams.tsv"
        )

        # en.teams.tsv: Code \t Name [\t weitere Namensvarianten]
        code_by_name = {}
        for line in teams_tsv.splitlines():
            fields = line.split("\t")
            if len(fields) >= 2:
                for variant in fields[1:]:
                    code_by_name[normalize(variant)] = fields[0]

        # World.tsv: Spalte 2 = Team-Code, Spalte 3 = Rating
        rating_by_code = {}
        for line in ratings_tsv.splitlines():
            fields = line.split("\t")
            if len(fields) >= 4:
                rating_by_code[fields[2]] = float(fields[3])

        mapping = _load_mapping(self.mapping_file)
        result = {}
        for key, english_name in mapping.items():
            code = code_by_name.get(normalize(english_name))
            if code is not None and code in rating_by_code:
                result[key] = rating_by_code[code]
        return result


def make_elo_source(team_type: str) -> ClubEloSource | NationalEloSource:
    if team_type == "club":
        return ClubEloSource()
    if team_type == "national":
        return NationalEloSource()
    raise ValueError(f"Unbekannter team_type: {team_type!r} (club | national)")

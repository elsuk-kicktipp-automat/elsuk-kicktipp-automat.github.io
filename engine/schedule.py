"""Öffentlicher Spielplan (data/schedule/) für die Startseite.

Die Site liest nur data/; ohne diese Datei wüsste sie nicht, wann der nächste
Spieltag beginnt oder ob gerade Spielpause ist. Bewusst ohne Ergebnisse und
Tipps: Die Datei ändert sich nur, wenn Anstoßzeiten festgelegt oder verlegt
werden - sonst gibt es nichts zu committen.
"""

import json
from datetime import timezone
from pathlib import Path

import requests

from .config import SCHEDULE_DIR
from .sources.openligadb import Match, fetch_competition


def build_schedule(matches: list[Match], competition: str, season: int) -> dict:
    by_matchday: dict[int, dict] = {}
    for m in sorted(matches, key=lambda m: (m.kickoff_utc, m.home_name)):
        md = by_matchday.setdefault(
            m.matchday, {"matchday": m.matchday, "stage": m.stage_name, "matches": []}
        )
        md["matches"].append(
            {
                "home": m.home_name,
                "away": m.away_name,
                "kickoff_utc": m.kickoff_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    return {
        "competition": competition,
        "season": season,
        "matchdays": [by_matchday[k] for k in sorted(by_matchday)],
    }


def main(config: dict, schedule_dir: Path = SCHEDULE_DIR) -> None:
    competition, season = config["competition"], config["season"]
    try:
        matches = fetch_competition(config["leagues"], season, force_refresh=True)
    except requests.RequestException as exc:
        # Nur Anzeige: ein Ausfall darf die Tippabgabe im selben Lauf nicht aufhalten.
        print(f"Spielplan nicht aktualisiert: {exc}")
        return
    schedule_dir.mkdir(parents=True, exist_ok=True)
    path = schedule_dir / f"{competition}_{season}.json"
    text = json.dumps(build_schedule(matches, competition, season), ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.write_text(text, encoding="utf-8")
    print(f"Spielplan geschrieben: {path.name}")

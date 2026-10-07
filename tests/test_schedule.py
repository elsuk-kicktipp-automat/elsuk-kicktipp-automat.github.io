import json
from datetime import datetime, timezone

import requests

from engine import schedule
from engine.sources.openligadb import Match


def _match(home, away, kickoff, matchday, goals=None):
    return Match(
        home_name=home,
        away_name=away,
        home_goals=goals,
        away_goals=goals,
        kickoff_utc=kickoff,
        matchday=matchday,
        stage_name=f"{matchday}. Spieltag",
        finished=goals is not None,
    )


MATCHES = [
    _match("SC Freiburg", "FC Schalke 04", datetime(2026, 10, 11, 15, 30, tzinfo=timezone.utc), 5),
    _match("Borussia Dortmund", "SV Werder Bremen", datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc), 5),
    _match("FC Bayern München", "1. FC Union Berlin", datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc), 4, goals=7),
]


def test_build_schedule_groups_by_matchday_without_results():
    data = schedule.build_schedule(MATCHES, "bl1", 2026)
    assert data["competition"] == "bl1" and data["season"] == 2026
    assert [md["matchday"] for md in data["matchdays"]] == [4, 5]
    md5 = data["matchdays"][1]
    assert md5["stage"] == "5. Spieltag"
    assert [m["home"] for m in md5["matches"]] == ["Borussia Dortmund", "SC Freiburg"]
    assert md5["matches"][0] == {
        "home": "Borussia Dortmund",
        "away": "SV Werder Bremen",
        "kickoff_utc": "2026-10-09T18:30:00Z",
    }


def test_main_writes_file_and_skips_on_api_error(tmp_path, monkeypatch):
    config = {"competition": "bl1", "season": 2026, "leagues": ["bl1"]}
    monkeypatch.setattr(schedule, "fetch_competition", lambda *a, **k: MATCHES)
    schedule.main(config, schedule_dir=tmp_path)
    path = tmp_path / "bl1_2026.json"
    assert json.loads(path.read_text(encoding="utf-8"))["matchdays"][0]["matchday"] == 4

    def fail(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(schedule, "fetch_competition", fail)
    path.unlink()
    schedule.main(config, schedule_dir=tmp_path)
    assert not path.exists()

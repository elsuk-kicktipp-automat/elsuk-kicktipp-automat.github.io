import json
from datetime import datetime, timedelta, timezone

from engine.delivery_audit import audit_delivery
from engine.kicktipp_bot import team_key
from engine.sources.openligadb import Match


NOW = datetime(2026, 8, 28, 14, 40, tzinfo=timezone.utc)
CONFIG = {
    "competition": "bl1",
    "season": 2026,
    "timing": {"tip_window_hours": 4},
    "delivery_audit": {
        "lookback_hours": 6,
        "boundary_grace_minutes": 10,
        "expected_schedule_matches": 1,
    },
}


def match_at(kickoff, home="FC Bayern München", away="VfB Stuttgart"):
    return Match(
        home_name=home,
        away_name=away,
        home_goals=None,
        away_goals=None,
        kickoff_utc=kickoff,
        matchday=1,
        stage_name="1. Spieltag",
        finished=False,
    )


def write_covered(directory, match):
    directory.mkdir()
    (directory / "bl1_2026_md01.json").write_text(
        json.dumps({
            "competition": "bl1",
            "season": 2026,
            "matches": [{"home": match.home_name, "away": match.away_name}],
        }),
        encoding="utf-8",
    )


def write_verified(directory, match):
    directory.mkdir()
    (directory / "bl1_2026.json").write_text(
        json.dumps({
            "competition": "bl1",
            "season": 2026,
            "submissions": [{
                "home_key": team_key(match.home_name),
                "away_key": team_key(match.away_name),
                "verified_at_utc": "2026-08-28T14:35:00Z",
            }],
        }),
        encoding="utf-8",
    )


def test_due_match_is_green_only_with_seal_and_submission_receipt(tmp_path):
    match = match_at(NOW + timedelta(hours=3))
    matchdays, submissions = tmp_path / "matchdays", tmp_path / "submissions"
    write_covered(matchdays, match)
    write_verified(submissions, match)

    report = audit_delivery(CONFIG, [match], NOW, matchdays, submissions)

    assert report["checked"] == 1
    assert report["missing_sealed"] == []
    assert report["missing_submitted"] == []
    assert report["schedule_problem"] is None


def test_recently_started_uncovered_match_is_reported(tmp_path):
    match = match_at(NOW - timedelta(hours=2))

    report = audit_delivery(
        CONFIG, [match], NOW, tmp_path / "matchdays", tmp_path / "submissions"
    )

    assert [m["home"] for m in report["missing_sealed"]] == ["FC Bayern München"]
    assert [m["home"] for m in report["missing_submitted"]] == ["FC Bayern München"]


def test_far_future_and_old_matches_are_outside_audit_window(tmp_path):
    matches = [
        match_at(NOW + timedelta(hours=4), home="A", away="B"),
        match_at(NOW - timedelta(hours=6, seconds=1), home="C", away="D"),
    ]
    config = {
        **CONFIG,
        "delivery_audit": {**CONFIG["delivery_audit"], "expected_schedule_matches": 2},
    }

    report = audit_delivery(
        config, matches, NOW, tmp_path / "matchdays", tmp_path / "submissions"
    )

    assert report["checked"] == 0
    assert report["missing_sealed"] == []
    assert report["missing_submitted"] == []


def test_incomplete_schedule_is_reported_even_without_due_match(tmp_path):
    config = {
        **CONFIG,
        "delivery_audit": {**CONFIG["delivery_audit"], "expected_schedule_matches": 306},
    }
    report = audit_delivery(
        config,
        [match_at(NOW + timedelta(days=2))],
        NOW,
        tmp_path / "matchdays",
        tmp_path / "submissions",
    )
    assert "1 statt der erwarteten 306" in report["schedule_problem"]

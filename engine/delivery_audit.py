"""Betriebs-Audit: Ist jedes fällige Spiel versiegelt und bei Kicktipp bestätigt?

Der Audit läuft am Ende jedes extern ausgelösten Spieltag-Workflows. Er ist die
Gegenprobe zum absichtlich stillen Leerlauf von ``predict``: Ein verspäteter
Trigger darf nicht länger grün enden, wenn während der Trigger-Lücke ein Spiel
ohne Tipp begonnen hat.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import MATCHDAYS_DIR, SUBMISSIONS_DIR
from .kicktipp_bot import team_key
from .sources.openligadb import Match, fetch_competition


def _covered_pairings(config: dict, matchdays_dir: Path) -> set[tuple[str, str]]:
    covered = set()
    for path in sorted(matchdays_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("competition") != config["competition"]
            or int(data.get("season", -1)) != int(config["season"])
        ):
            continue
        covered.update(
            (team_key(match["home"]), team_key(match["away"]))
            for match in data.get("matches", [])
        )
    return covered


def _verified_pairings(config: dict, submissions_dir: Path) -> set[tuple[str, str]]:
    path = submissions_dir / f"{config['competition']}_{config['season']}.json"
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (entry["home_key"], entry["away_key"])
        for entry in data.get("submissions", [])
    }


def audit_delivery(
    config: dict,
    matches: list[Match],
    now: datetime | None = None,
    matchdays_dir: Path = MATCHDAYS_DIR,
    submissions_dir: Path = SUBMISSIONS_DIR,
) -> dict:
    """Liefert fehlende Versiegelungen/Abgaben im relevanten Zeitkorridor.

    Geprüft werden alle Spiele vom konfigurierten Rückblick bis zum Ende des
    normalen Tipp-Fensters. Damit werden sowohl verspätete Trigger nach Anstoß
    als auch ein normaler Lauf erfasst, der trotz fälligem Spiel nichts erzeugt.
    """
    now = now or datetime.now(timezone.utc)
    cfg = config.get("delivery_audit") or {}
    window = timedelta(hours=float(config.get("timing", {}).get("tip_window_hours", 4)))
    lookback = timedelta(hours=float(cfg.get("lookback_hours", 6)))
    boundary_grace = timedelta(minutes=float(cfg.get("boundary_grace_minutes", 10)))

    expected = cfg.get("expected_schedule_matches")
    schedule_problem = None
    if not matches:
        schedule_problem = "OpenLigaDB lieferte keinen Spielplan"
    elif expected is not None and len(matches) != int(expected):
        schedule_problem = (
            f"OpenLigaDB lieferte {len(matches)} statt der erwarteten "
            f"{int(expected)} Spiele"
        )

    relevant = [
        match
        for match in matches
        if not match.has_placeholder
        and now - lookback <= match.kickoff_utc <= now + window - boundary_grace
    ]
    covered = _covered_pairings(config, matchdays_dir)
    verified = _verified_pairings(config, submissions_dir)

    def describe(match: Match) -> dict:
        return {
            "home": match.home_name,
            "away": match.away_name,
            "kickoff_utc": match.kickoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    missing_sealed = [
        describe(match)
        for match in relevant
        if (team_key(match.home_name), team_key(match.away_name)) not in covered
    ]
    missing_submitted = [
        describe(match)
        for match in relevant
        if (team_key(match.home_name), team_key(match.away_name)) not in verified
    ]
    return {
        "schedule_problem": schedule_problem,
        "checked": len(relevant),
        "missing_sealed": missing_sealed,
        "missing_submitted": missing_submitted,
    }


def main(config: dict) -> None:
    cfg = config.get("delivery_audit") or {}
    if not cfg.get("enabled", True):
        print("Abgabe-Audit ist deaktiviert.")
        return

    matches = fetch_competition(config["leagues"], config["season"], force_refresh=True)
    report = audit_delivery(config, matches)
    problems = []
    if report["schedule_problem"]:
        problems.append(report["schedule_problem"])
    if report["missing_sealed"]:
        problems.append(
            f"{len(report['missing_sealed'])} fällige Spiel(e) ohne Versiegelung: "
            + ", ".join(
                f"{m['home']}–{m['away']} ({m['kickoff_utc']})"
                for m in report["missing_sealed"]
            )
        )
    if report["missing_submitted"]:
        problems.append(
            f"{len(report['missing_submitted'])} fällige Spiel(e) ohne bestätigte Kicktipp-Abgabe: "
            + ", ".join(
                f"{m['home']}–{m['away']} ({m['kickoff_utc']})"
                for m in report["missing_submitted"]
            )
        )
    if problems:
        raise SystemExit("ABGABE-AUDIT FEHLGESCHLAGEN: " + "; ".join(problems))
    print(f"Abgabe-Audit grün: {report['checked']} fällige Spiel(e) vollständig bestätigt.")

import json
from datetime import datetime, timezone

from engine.sources.openligadb import fetch_competition, fetch_season, parse_matches
from engine.teams import is_knockout_stage, is_placeholder, normalize

SAMPLE_MATCH = {
    "matchID": 77561,
    "matchDateTimeUTC": "2026-05-16T13:30:00Z",
    "group": {"groupName": "34. Spieltag", "groupOrderID": 34, "groupID": 47644},
    "team1": {"teamId": 98, "teamName": "FC St. Pauli", "shortName": "St. Pauli"},
    "team2": {"teamId": 131, "teamName": "VfL Wolfsburg", "shortName": "Wolfsburg"},
    "matchIsFinished": True,
    "matchResults": [
        {"resultTypeID": 1, "resultName": "Halbzeit", "pointsTeam1": 0, "pointsTeam2": 1},
        {"resultTypeID": 2, "resultName": "Endergebnis", "pointsTeam1": 2, "pointsTeam2": 1},
    ],
}

# Reale wm26-Struktur eines K.o.-Spiels mit Elfmeterschießen (1:1 nach 90,
# 1:1 nach Verlängerung, 3:5 nach Elfmeterschießen)
KO_PENALTY_MATCH = {
    "matchID": 88888,
    "matchDateTimeUTC": "2026-06-30T18:00:00Z",
    "group": {"groupName": "Sechzehntelfinale", "groupOrderID": 4, "groupID": 50001},
    "team1": {"teamId": 139, "teamName": "Deutschland", "shortName": "Deutschland"},
    "team2": {"teamId": 756, "teamName": "Paraguay", "shortName": "Paraguay"},
    "matchIsFinished": True,
    "matchResults": [
        {"resultTypeID": 1, "resultName": "Halbzeit", "pointsTeam1": 0, "pointsTeam2": 1},
        {"resultTypeID": 2, "resultName": "Endergebnis", "pointsTeam1": 1, "pointsTeam2": 1},
        {"resultTypeID": 4, "resultName": "nach Verlängerung", "pointsTeam1": 1, "pointsTeam2": 1},
        {"resultTypeID": 5, "resultName": "nach Elfmeterschießen", "pointsTeam1": 3, "pointsTeam2": 5},
    ],
}

# K.o.-Spiel, das in der Verlängerung entschieden wurde (kein Elfmeterschießen)
KO_EXTRA_TIME_MATCH = {
    "matchID": 88889,
    "matchDateTimeUTC": "2026-06-30T21:00:00Z",
    "group": {"groupName": "Sechzehntelfinale", "groupOrderID": 4, "groupID": 50001},
    "team1": {"teamId": 100, "teamName": "Argentinien", "shortName": "Argentinien"},
    "team2": {"teamId": 101, "teamName": "Kap Verde", "shortName": "Kap Verde"},
    "matchIsFinished": True,
    "matchResults": [
        {"resultTypeID": 1, "resultName": "Halbzeit", "pointsTeam1": 1, "pointsTeam2": 0},
        {"resultTypeID": 2, "resultName": "Endergebnis", "pointsTeam1": 1, "pointsTeam2": 1},
        {"resultTypeID": 4, "resultName": "nach Verlängerung", "pointsTeam1": 3, "pointsTeam2": 2},
    ],
}

PLACEHOLDER_MATCH = {
    "matchID": 99999,
    "matchDateTimeUTC": "2026-07-06T19:00:00Z",
    "group": {"groupName": "Achtelfinale", "groupOrderID": 5, "groupID": 50002},
    "team1": {"teamId": 7680, "teamName": "Sieger SF 12", "shortName": ""},
    "team2": {"teamId": 7679, "teamName": "Sieger SF 11", "shortName": ""},
    "matchIsFinished": False,
    "matchResults": [],
}


class TestTeams:
    def test_normalize_bridges_community_spellings(self):
        assert normalize("Bosnien-Herzegowina") == normalize("Bosnien und Herzegowina")
        assert normalize("Curaçao") == "curacao"
        assert normalize("FC Bayern München") == "fcbayernmunchen"

    def test_is_placeholder(self):
        assert is_placeholder("Sieger SF 12")
        assert is_placeholder("Verlierer HF 1")
        assert not is_placeholder("Deutschland")

    def test_is_placeholder_slash_code_pattern(self):
        # wm26-Liga: Platzhalter als "XXX/YYY" statt "Sieger SF 12"
        assert is_placeholder("ARG/CPV")
        assert is_placeholder("MEX/ENG")
        assert not is_placeholder("USA")
        assert not is_placeholder("Bosnien-Herzegowina")

    def test_is_knockout_stage(self):
        for stage in ("Sechzehntelfinale", "Achtelfinale", "Viertelfinale", "Halbfinale", "Finale"):
            assert is_knockout_stage(stage)
        for stage in ("1. Runde", "2. Runde", "3. Runde", "34. Spieltag"):
            assert not is_knockout_stage(stage)


class TestParseMatches:
    def test_parses_final_result_not_halftime(self):
        (m,) = parse_matches([SAMPLE_MATCH])
        assert (m.home_goals, m.away_goals) == (2, 1)

    def test_teams_matchday_stage(self):
        (m,) = parse_matches([SAMPLE_MATCH])
        assert m.home_name == "FC St. Pauli"
        assert m.home_key == "fcstpauli"
        assert m.matchday == 34
        assert m.stage_name == "34. Spieltag"

    def test_kickoff_is_utc(self):
        (m,) = parse_matches([SAMPLE_MATCH])
        assert m.kickoff_utc == datetime(2026, 5, 16, 13, 30, tzinfo=timezone.utc)

    def test_ko_penalty_match_counts_full_tally(self):
        # Kicktipp-Regel "n.E.": gewertet wird die höchste Ausbaustufe -
        # hier das Elfmeterschießen-Ergebnis (3:5), nicht das 1:1 nach 90.
        (m,) = parse_matches([KO_PENALTY_MATCH])
        assert (m.home_goals, m.away_goals) == (3, 5)
        assert m.has_result

    def test_ko_extra_time_match_counts_extra_time_result(self):
        # Ohne Elfmeterschießen zählt das Ergebnis nach Verlängerung (3:2)
        (m,) = parse_matches([KO_EXTRA_TIME_MATCH])
        assert (m.home_goals, m.away_goals) == (3, 2)

    def test_placeholder_match_has_no_result(self):
        (m,) = parse_matches([PLACEHOLDER_MATCH])
        assert m.has_placeholder
        assert not m.has_result

    def test_fallback_to_last_result_entry(self):
        # Ältere Saisons haben teils keinen Eintrag mit resultTypeID 2
        match = dict(SAMPLE_MATCH)
        match["matchResults"] = [
            {"resultTypeID": 1, "resultName": "Halbzeit", "pointsTeam1": 1, "pointsTeam2": 0},
            {"resultTypeID": 3, "resultName": "n.V.", "pointsTeam1": 3, "pointsTeam2": 2},
        ]
        (m,) = parse_matches([match])
        assert (m.home_goals, m.away_goals) == (3, 2)


class TestFetchAndCache:
    def test_uses_cache_without_network(self, tmp_path):
        (tmp_path / "bl1_2025.json").write_text(json.dumps([SAMPLE_MATCH]), encoding="utf-8")
        matches = fetch_season("bl1", 2025, cache_dir=tmp_path)
        assert len(matches) == 1
        assert matches[0].home_name == "FC St. Pauli"

    def test_writes_cache_on_fetch(self, tmp_path, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return [SAMPLE_MATCH]

        requested_urls = []

        def fake_get(url, timeout):
            requested_urls.append(url)
            return FakeResponse()

        monkeypatch.setattr("engine.sources.openligadb.requests.get", fake_get)

        matches = fetch_season("bl1", 2024, cache_dir=tmp_path)
        assert len(matches) == 1
        assert requested_urls == ["https://api.openligadb.de/getmatchdata/bl1/2024"]
        assert (tmp_path / "bl1_2024.json").exists()

        # Zweiter Aufruf kommt aus dem Cache, kein weiterer Request
        fetch_season("bl1", 2024, cache_dir=tmp_path)
        assert len(requested_urls) == 1

    def test_fetch_competition_merges_leagues_sorted(self, tmp_path):
        (tmp_path / "wm2026_2026.json").write_text(json.dumps([KO_PENALTY_MATCH]), encoding="utf-8")
        (tmp_path / "mb_2026.json").write_text(json.dumps([PLACEHOLDER_MATCH]), encoding="utf-8")
        matches = fetch_competition(["wm2026", "mb"], 2026, cache_dir=tmp_path)
        assert [m.home_name for m in matches] == ["Deutschland", "Sieger SF 12"]
        assert matches[0].kickoff_utc < matches[1].kickoff_utc


class TestNinetyMinuteScore:
    def test_penalty_match_exposes_both_tallies(self):
        (m,) = parse_matches([KO_PENALTY_MATCH])
        assert (m.home_goals, m.away_goals) == (3, 5)      # n.E.-Wertung (Kicktipp)
        assert (m.home_goals_90, m.away_goals_90) == (1, 1)  # Buchmacher-1X2-Basis

    def test_regular_match_tallies_are_identical(self):
        (m,) = parse_matches([SAMPLE_MATCH])
        assert (m.home_goals_90, m.away_goals_90) == (m.home_goals, m.away_goals) == (2, 1)

import json

import pytest

import engine.sources.odds as odds_module
from engine.sources.odds import (
    fetch_raw_odds,
    load_probabilities,
    parse_betting_markets,
    parse_probabilities,
)

RAW_EVENT = {
    "id": "abc123",
    "home_team": "Bayern Munich",
    "away_team": "Borussia Dortmund",
    "commence_time": "2026-08-15T18:30:00Z",
    "bookmakers": [
        {
            "key": "pinnacle",
            "title": "Pinnacle",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Bayern Munich", "price": 1.80},
                        {"name": "Draw", "price": 3.80},
                        {"name": "Borussia Dortmund", "price": 4.50},
                    ],
                }
            ],
        },
        {
            "key": "tipico_de",
            "title": "Tipico (DE)",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Bayern Munich", "price": 1.75},
                        {"name": "Draw", "price": 3.75},
                        {"name": "Borussia Dortmund", "price": 4.75},
                    ],
                }
            ],
        },
    ],
}

UNMAPPED_EVENT = {
    "id": "def456",
    "home_team": "Some Random FC",
    "away_team": "Other Team",
    "bookmakers": [],
}


@pytest.fixture
def mapping_env(tmp_path, monkeypatch):
    mappings = tmp_path / "mappings"
    mappings.mkdir()
    (mappings / "odds_teams.json").write_text(
        json.dumps(
            {
                "_kommentar": "test",
                "FC Bayern München": "Bayern Munich",
                "Borussia Dortmund": "Borussia Dortmund",
                "USA": "USA",
                "Belgien": "Belgium",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(odds_module, "MAPPINGS_DIR", mappings)
    return mappings


class TestDevig:
    def test_removes_bookmaker_margin(self, mapping_env):
        outcomes = [
            {"name": "A", "price": 2.0},
            {"name": "Draw", "price": 4.0},
            {"name": "B", "price": 4.0},
        ]
        devigged = odds_module._devig(outcomes)
        # Rohsumme 1/2 + 1/4 + 1/4 = 1.0 -> hier bereits marge-frei, Werte unverändert
        assert devigged["A"] == pytest.approx(0.5)
        assert sum(devigged.values()) == pytest.approx(1.0)

    def test_with_real_margin(self, mapping_env):
        outcomes = [
            {"name": "A", "price": 1.80},
            {"name": "Draw", "price": 3.80},
            {"name": "B", "price": 4.50},
        ]
        devigged = odds_module._devig(outcomes)
        assert sum(devigged.values()) == pytest.approx(1.0)
        assert devigged["A"] > devigged["B"]  # A ist der Favorit


class TestParseProbabilities:
    def test_averages_across_bookmakers_and_maps_teams(self, mapping_env):
        result = parse_probabilities([RAW_EVENT])
        key = ("fcbayernmunchen", "borussiadortmund")
        assert key in result
        probs = result[key]
        assert sum(probs.values()) == pytest.approx(1.0)
        assert probs["home"] > probs["away"]

    def test_unmapped_teams_are_skipped(self, mapping_env):
        result = parse_probabilities([UNMAPPED_EVENT])
        assert result == {}

    def test_empty_input(self, mapping_env):
        assert parse_probabilities([]) == {}


class TestParseBettingMarkets:
    def test_prefers_tipico_when_available(self, mapping_env):
        result = parse_betting_markets([RAW_EVENT], preferred_bookmakers=["tipico_de"])
        market = result[("fcbayernmunchen", "borussiadortmund")]
        assert market["source"] == "tipico_de"
        assert market["source_label"] == "Tipico (DE)"
        assert market["odds"]["home"] == 1.75
        assert market["bookmaker_count"] == 2

    def test_falls_back_to_market_average(self, mapping_env):
        result = parse_betting_markets([RAW_EVENT], preferred_bookmakers=["missing_book"])
        market = result[("fcbayernmunchen", "borussiadortmund")]
        assert market["source"] == "market_average"
        assert market["odds"]["home"] == pytest.approx((1.80 + 1.75) / 2)
        assert market["odds"]["away"] == pytest.approx((4.50 + 4.75) / 2)

    def test_maps_usa_as_named_by_the_odds_api(self, mapping_env):
        event = {
            "home_team": "USA",
            "away_team": "Belgium",
            "bookmakers": [
                {
                    "key": "tipico_de",
                    "title": "Tipico",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "USA", "price": 2.65},
                                {"name": "Draw", "price": 3.40},
                                {"name": "Belgium", "price": 2.65},
                            ],
                        }
                    ],
                }
            ],
        }

        result = parse_betting_markets([event], preferred_bookmakers=["tipico_de"])

        assert result[("usa", "belgien")]["odds"]["away"] == 2.65


class TestFetchAndCache:
    def test_uses_cache_without_network(self, tmp_path, mapping_env):
        cache_file = tmp_path / "odds_soccer_test_2026-07-04.json"
        cache_file.write_text(json.dumps([RAW_EVENT]), encoding="utf-8")
        raw = fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, cache_tag="2026-07-04")
        assert raw == [RAW_EVENT]

    def test_writes_cache_on_fetch(self, tmp_path, mapping_env, monkeypatch):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return [RAW_EVENT]

        calls = []

        def fake_get(url, params, timeout):
            calls.append(url)
            return FakeResponse()

        monkeypatch.setattr("engine.sources.odds.requests.get", fake_get)
        raw = fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, cache_tag="2026-07-05")
        assert raw == [RAW_EVENT]
        assert len(calls) == 1

        fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, cache_tag="2026-07-05")
        assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache


class TestLoadProbabilitiesResilience:
    def test_network_error_returns_empty_dict(self, tmp_path, mapping_env, monkeypatch):
        import requests

        def fake_get(*args, **kwargs):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("engine.sources.odds.requests.get", fake_get)
        result = load_probabilities("key", "soccer_test", cache_dir=tmp_path, cache_tag="x")
        assert result == {}

    def test_error_object_instead_of_list_returns_empty_dict(self, tmp_path, mapping_env):
        cache_file = tmp_path / "odds_bad_sport_x.json"
        cache_file.write_text(json.dumps({"message": "Unknown sport"}), encoding="utf-8")
        result = load_probabilities("key", "bad_sport", cache_dir=tmp_path, cache_tag="x")
        assert result == {}

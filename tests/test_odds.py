import json
from datetime import datetime, timedelta, timezone

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
    NOW = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def _fake_get(calls):
        class FakeResponse:
            status_code = 200
            headers = {"x-requests-remaining": "123"}

            def raise_for_status(self):
                pass

            def json(self):
                return [RAW_EVENT]

        def fake_get(url, params, timeout):
            calls.append(url)
            return FakeResponse()

        return fake_get

    def _write_cache(self, tmp_path, fetched):
        cache_file = tmp_path / "odds_soccer_test.json"
        cache_file.write_text(
            json.dumps({"fetched_utc": fetched.isoformat(), "events": [RAW_EVENT]}), encoding="utf-8"
        )

    def test_uses_fresh_cache_without_network(self, tmp_path, mapping_env, monkeypatch):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls))
        self._write_cache(tmp_path, self.NOW - timedelta(minutes=59))
        raw = fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, max_age_minutes=60, now=self.NOW)
        assert raw == [RAW_EVENT]
        assert calls == []

    def test_refetches_stale_cache(self, tmp_path, mapping_env, monkeypatch):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls))
        self._write_cache(tmp_path, self.NOW - timedelta(minutes=61))
        fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, max_age_minutes=60, now=self.NOW)
        assert len(calls) == 1

    def test_longer_max_age_reuses_older_cache(self, tmp_path, mapping_env, monkeypatch):
        # Kombi-Suche: 3 Stunden alte Quoten reichen ihr
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls))
        self._write_cache(tmp_path, self.NOW - timedelta(minutes=150))
        fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, max_age_minutes=180, now=self.NOW)
        assert calls == []

    def test_legacy_list_cache_counts_as_stale(self, tmp_path, mapping_env, monkeypatch):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls))
        (tmp_path / "odds_soccer_test.json").write_text(json.dumps([RAW_EVENT]), encoding="utf-8")
        fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, now=self.NOW)
        assert len(calls) == 1

    def test_writes_cache_on_fetch_and_logs_quota(self, tmp_path, mapping_env, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls))
        raw = fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, now=self.NOW)
        assert raw == [RAW_EVENT]
        assert len(calls) == 1
        out = capsys.readouterr().out
        assert "Restkontingent diesen Monat: 123" in out
        assert "apiKey" not in out  # API-Schlüssel nie ins öffentliche Log

        later = self.NOW + timedelta(minutes=10)
        fetch_raw_odds("key", "soccer_test", cache_dir=tmp_path, now=later)
        assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache


class TestFallbackKey:
    NOW = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def _fake_get(calls, status_by_key):
        class FakeResponse:
            def __init__(self, status):
                self.status_code = status
                self.headers = {"x-requests-remaining": "0" if status != 200 else "499"}

            def raise_for_status(self):
                if self.status_code != 200:
                    import requests

                    raise requests.HTTPError(f"{self.status_code} Client Error")

            def json(self):
                return [RAW_EVENT]

        def fake_get(url, params, timeout):
            calls.append(params["apiKey"])
            return FakeResponse(status_by_key[params["apiKey"]])

        return fake_get

    def test_primary_key_is_used_while_it_has_quota(self, tmp_path, mapping_env, monkeypatch):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls, {"alt": 200, "neu": 200}))
        fetch_raw_odds(["alt", "neu"], "soccer_test", cache_dir=tmp_path, now=self.NOW)
        assert calls == ["alt"]

    def test_fallback_key_only_when_primary_is_exhausted(self, tmp_path, mapping_env, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls, {"alt": 401, "neu": 200}))
        raw = fetch_raw_odds(["alt", "neu"], "soccer_test", cache_dir=tmp_path, now=self.NOW)
        assert raw == [RAW_EVENT]
        assert calls == ["alt", "neu"]
        out = capsys.readouterr().out
        assert "Ersatzschlüssel" in out and "alt" not in out and "neu" not in out.replace("neu geladen", "")

    def test_both_exhausted_returns_empty_dict(self, tmp_path, mapping_env, monkeypatch):
        calls = []
        monkeypatch.setattr("engine.sources.odds.requests.get", self._fake_get(calls, {"alt": 401, "neu": 429}))
        assert load_probabilities(["alt", "neu"], "soccer_test", cache_dir=tmp_path) == {}
        assert calls == ["alt", "neu"]


class TestLoadProbabilitiesResilience:
    def test_network_error_returns_empty_dict(self, tmp_path, mapping_env, monkeypatch):
        import requests

        def fake_get(*args, **kwargs):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("engine.sources.odds.requests.get", fake_get)
        result = load_probabilities("key", "soccer_test", cache_dir=tmp_path)
        assert result == {}

    def test_error_object_instead_of_list_returns_empty_dict(self, tmp_path, mapping_env):
        cache_file = tmp_path / "odds_bad_sport.json"
        fetched = datetime.now(timezone.utc).isoformat()
        cache_file.write_text(
            json.dumps({"fetched_utc": fetched, "events": {"message": "Unknown sport"}}), encoding="utf-8"
        )
        result = load_probabilities("key", "bad_sport", cache_dir=tmp_path)
        assert result == {}

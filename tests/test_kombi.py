import json
from datetime import datetime, timezone

from engine.kombi import (
    choose_kombi,
    next_kombi_id,
    open_kombi_exists,
    payload_hash,
    seal_kombi,
    settle_kombi,
    settle_open,
    unseal_due,
)
from engine.teams import normalize

CFG = {
    "enabled": True,
    "window_hours": 72,
    "max_legs": 3,
    "min_leg_probability": 0.55,
    "min_leg_edge": 0.02,
    "staking": {
        "bankroll_eur": 1000,
        "kelly_fraction": 0.25,
        "min_stake_eur": 10,
        "max_stake_eur": 100,
    },
}

NOW = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)


def cand(home, away, p_home, odds_home, kickoff="2026-07-14T19:00:00Z", with_market=True):
    rest = round((1 - p_home) / 2, 4)
    return {
        "home": home,
        "away": away,
        "kickoff_utc": kickoff,
        "probabilities": {"home": p_home, "draw": rest, "away": rest},
        "market": (
            {
                "source": "tipico_de",
                "source_label": "Tipico",
                "odds": {"home": odds_home, "draw": 3.5, "away": 4.0},
            }
            if with_market
            else None
        ),
    }


class TestChooseKombi:
    def test_builds_two_leg_kombi_from_value_legs(self):
        kombi = choose_kombi([cand("A", "B", 0.7, 1.6), cand("C", "D", 0.7, 1.6)], CFG, now=NOW)
        assert kombi is not None
        assert kombi["leg_count"] == 2
        assert kombi["total_odds"] == round(1.6 * 1.6, 3)
        assert kombi["combined_probability"] == round(0.7 * 0.7, 4)
        # Fractional Kelly: 1000 * 0.1631 * 0.25 ~ 40.77
        assert 40 < kombi["stake_eur"] < 42
        assert kombi["potential_payout_eur"] == round(kombi["stake_eur"] * kombi["total_odds"], 2)

    def test_filters_leg_below_min_probability(self):
        # 0.50 < 0.55 trotz sattem Value -> nur 1 Bein übrig -> keine Kombi
        kombi = choose_kombi([cand("A", "B", 0.50, 2.5), cand("C", "D", 0.7, 1.6)], CFG, now=NOW)
        assert kombi is None

    def test_filters_leg_without_edge(self):
        # 0.7 * 1.40 = 0.98 -> negative Edge, Bein fliegt raus
        kombi = choose_kombi([cand("A", "B", 0.7, 1.40), cand("C", "D", 0.7, 1.6)], CFG, now=NOW)
        assert kombi is None

    def test_skips_candidates_without_market(self):
        kombi = choose_kombi(
            [cand("A", "B", 0.7, 1.6, with_market=False), cand("C", "D", 0.7, 1.6)], CFG, now=NOW
        )
        assert kombi is None

    def test_caps_at_max_legs_keeping_most_probable(self):
        kombi = choose_kombi(
            [
                cand("A", "B", 0.65, 1.7),
                cand("C", "D", 0.80, 1.4),
                cand("E", "F", 0.75, 1.5),
                cand("G", "H", 0.70, 1.6),
            ],
            CFG,
            now=NOW,
        )
        assert kombi["leg_count"] == 3
        assert [leg["model_probability"] for leg in kombi["legs"]] == [0.80, 0.75, 0.70]

    def test_no_kombi_below_min_stake(self):
        # Hauchdünne Edge -> Kelly-Einsatz ~5 EUR < 10 EUR Minimum
        kombi = choose_kombi([cand("A", "B", 0.56, 1.83), cand("C", "D", 0.56, 1.83)], CFG, now=NOW)
        assert kombi is None

    def test_stake_capped_at_max(self):
        kombi = choose_kombi([cand("A", "B", 0.8, 1.8), cand("C", "D", 0.8, 1.8)], CFG, now=NOW)
        assert kombi["stake_eur"] == 100.0


def _sealed_kombi(tmp_path, secret="test-secret"):
    kombi = choose_kombi(
        [
            cand("Kanada", "Marokko", 0.7, 1.6, kickoff="2026-07-14T16:00:00Z"),
            cand("Brasilien", "Norwegen", 0.7, 1.6, kickoff="2026-07-14T20:00:00Z"),
        ],
        CFG,
        now=NOW,
    )
    kombi["competition"] = "wm26"
    kombi["season"] = 2026
    kombi["id"] = next_kombi_id("wm26", 2026, kombi_dir=tmp_path)
    seal_kombi(kombi, secret, kombi_dir=tmp_path)
    return kombi


class TestSealUnseal:
    def test_public_file_reveals_nothing_but_hash(self, tmp_path):
        _sealed_kombi(tmp_path)
        public = json.loads((tmp_path / "wm26_2026_kombi01.json").read_text(encoding="utf-8"))
        assert public["status"] == "sealed"
        assert public["reveal_after_utc"] == "2026-07-14T20:00:00Z"  # letzter Anstoß
        assert "legs" not in public and "stake_eur" not in public and "total_odds" not in public
        assert (tmp_path / "wm26_2026_kombi01.enc").exists()
        assert open_kombi_exists(kombi_dir=tmp_path)

    def test_unseal_only_after_last_kickoff(self, tmp_path):
        _sealed_kombi(tmp_path)
        before = datetime(2026, 7, 14, 17, tzinfo=timezone.utc)  # erstes Bein läuft schon
        assert unseal_due("test-secret", kombi_dir=tmp_path, now=before) == []

        after = datetime(2026, 7, 14, 20, 0, 1, tzinfo=timezone.utc)
        changed = unseal_due("test-secret", kombi_dir=tmp_path, now=after)
        assert len(changed) == 1
        data = json.loads(changed[0].read_text(encoding="utf-8"))
        assert data["status"] == "revealed"
        assert data["leg_count"] == 2
        assert not (tmp_path / "wm26_2026_kombi01.enc").exists()

    def test_revealed_hash_is_verifiable(self, tmp_path):
        _sealed_kombi(tmp_path)
        after = datetime(2026, 7, 15, tzinfo=timezone.utc)
        [path] = unseal_due("test-secret", kombi_dir=tmp_path, now=after)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert payload_hash(data, data["salt"]) == data["hash"]


class TestSettle:
    def _revealed(self, tmp_path):
        _sealed_kombi(tmp_path)
        [path] = unseal_due("test-secret", kombi_dir=tmp_path, now=datetime(2026, 7, 15, tzinfo=timezone.utc))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_all_legs_won_pays_total_odds(self, tmp_path):
        kombi = self._revealed(tmp_path)
        results = {
            (normalize("Kanada"), normalize("Marokko")): (2, 0),
            (normalize("Brasilien"), normalize("Norwegen")): (1, 0),
        }
        settled = settle_kombi(kombi, results)
        assert settled["result"]["outcome"] == "won"
        assert settled["result"]["payout_eur"] == round(kombi["stake_eur"] * kombi["total_odds"], 2)
        assert all(leg["won"] for leg in settled["legs"])

    def test_one_lost_leg_loses_everything(self, tmp_path):
        kombi = self._revealed(tmp_path)
        results = {
            (normalize("Kanada"), normalize("Marokko")): (2, 0),
            (normalize("Brasilien"), normalize("Norwegen")): (0, 0),  # Remis statt Heimsieg
        }
        settled = settle_kombi(kombi, results)
        assert settled["result"]["outcome"] == "lost"
        assert settled["result"]["payout_eur"] == 0.0
        assert settled["result"]["profit_eur"] == -kombi["stake_eur"]

    def test_open_leg_keeps_kombi_unsettled(self, tmp_path):
        kombi = self._revealed(tmp_path)
        results = {(normalize("Kanada"), normalize("Marokko")): (2, 0)}
        assert settle_kombi(kombi, results) is None
        assert settle_open(results, kombi_dir=tmp_path) == []
        assert open_kombi_exists(kombi_dir=tmp_path)

    def test_void_leg_counts_as_odds_one(self, tmp_path):
        kombi = self._revealed(tmp_path)
        kombi["legs"][0]["void"] = True  # abgesagtes Spiel: Quote 1.0 (Buchmacher-Standard)
        results = {(normalize("Brasilien"), normalize("Norwegen")): (1, 0)}
        settled = settle_kombi(kombi, results)
        assert settled["result"]["outcome"] == "won"
        expected = round(kombi["stake_eur"] * kombi["legs"][1]["odds_decimal"], 2)
        assert settled["result"]["payout_eur"] == expected

    def test_settle_open_writes_result_and_closes_kombi(self, tmp_path):
        self._revealed(tmp_path)
        results = {
            (normalize("Kanada"), normalize("Marokko")): (2, 0),
            (normalize("Brasilien"), normalize("Norwegen")): (1, 0),
        }
        changed = settle_open(results, kombi_dir=tmp_path)
        assert len(changed) == 1
        data = json.loads(changed[0].read_text(encoding="utf-8"))
        assert data["result"]["outcome"] == "won"
        assert not open_kombi_exists(kombi_dir=tmp_path)

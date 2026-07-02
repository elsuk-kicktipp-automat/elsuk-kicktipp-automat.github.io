from datetime import date

import pytest

import engine.sources.elo as elo_module
from engine.sources.elo import ClubEloSource, NationalEloSource, make_elo_source

CLUBELO_CSV = """Rank,Club,Country,Level,Elo,From,To
1,Arsenal,ENG,1,2063.75,2026-05-31,2026-08-21
2,Bayern,GER,1,2000.87,2026-05-21,2026-08-24
17,Dortmund,GER,1,1835.42,2026-05-21,2026-08-24
"""

# eloratings.net: en.teams.tsv = Code \t Name [\t Varianten], World.tsv = Spalte 3 Code, 4 Rating
ELORATINGS_TEAMS_TSV = "DE\tGermany\nBA\tBosnia and Herzegovina\tBosnia & Herzegovina\nCW\tCuraçao\n"
ELORATINGS_WORLD_TSV = "5\t5\tDE\t1908\t1\t2000\n30\t30\tBA\t1600\t2\t1700\n91\t91\tCW\t1438\t26\t1618\n"


@pytest.fixture
def elo_env(tmp_path, monkeypatch):
    """Leitet Cache und Mappings auf Fixture-Dateien um - kein Netzwerk."""
    cache = tmp_path / "cache"
    mappings = tmp_path / "mappings"
    cache.mkdir()
    mappings.mkdir()
    monkeypatch.setattr(elo_module, "CACHE_DIR", cache)
    monkeypatch.setattr(elo_module, "MAPPINGS_DIR", mappings)

    (cache / "clubelo_2026-07-02.csv").write_text(CLUBELO_CSV, encoding="utf-8")
    (cache / "eloratings_2026-07-02.tsv").write_text(ELORATINGS_WORLD_TSV, encoding="utf-8")
    (cache / "eloratings_teams.tsv").write_text(ELORATINGS_TEAMS_TSV, encoding="utf-8")
    (mappings / "clubs.json").write_text(
        '{"_kommentar": "test", "FC Bayern München": "Bayern", "Borussia Dortmund": "Dortmund"}',
        encoding="utf-8",
    )
    (mappings / "national_teams.json").write_text(
        '{"Deutschland": "Germany", "Bosnien-Herzegowina": "Bosnia and Herzegovina", "Curaçao": "Curacao"}',
        encoding="utf-8",
    )
    return date(2026, 7, 2)


class TestClubElo:
    def test_ratings_mapped_to_normalized_openligadb_names(self, elo_env):
        ratings = ClubEloSource().ratings(elo_env)
        assert ratings == {"fcbayernmunchen": 2000.87, "borussiadortmund": 1835.42}


class TestNationalElo:
    def test_ratings_resolved_via_code_and_name_variants(self, elo_env):
        ratings = NationalEloSource().ratings(elo_env)
        assert ratings["deutschland"] == 1908.0
        # Community-Schreibweise mit Bindestrich trifft die en.teams.tsv-Variante
        assert ratings["bosnienherzegowina"] == 1600.0
        # Diakritika-Folding: Mapping "Curacao" trifft "Curaçao" der Quelle
        assert ratings["curacao"] == 1438.0


class TestFactory:
    def test_make_elo_source(self):
        assert isinstance(make_elo_source("club"), ClubEloSource)
        assert isinstance(make_elo_source("national"), NationalEloSource)
        with pytest.raises(ValueError):
            make_elo_source("galactic")

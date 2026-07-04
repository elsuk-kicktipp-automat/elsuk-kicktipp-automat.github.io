from datetime import datetime, timezone

import pytest

from engine.sources.news import fetch_report, fetch_snippets

FEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item>
  <title>Deutschland ohne Stammtorwart: Ausfall vor dem Achtelfinale</title>
  <description>Der Kapitän fehlt verletzt.</description>
  <pubDate>Thu, 02 Jul 2026 10:00:00 GMT</pubDate>
</item>
<item>
  <title>Frankreich siegt souverän</title>
  <description>Ein Spielbericht ohne Bezug zu anderen Teams.</description>
  <pubDate>Thu, 02 Jul 2026 09:00:00 GMT</pubDate>
</item>
<item>
  <title>Deutschland vor zwei Wochen: alter Bericht</title>
  <description>Das ist längst vorbei.</description>
  <pubDate>Mon, 15 Jun 2026 09:00:00 GMT</pubDate>
</item>
</channel></rss>
"""

BILD_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
     <loc>https://www.bild.de/sport/fussball/deutschland-ohne-stammtorwart</loc>
     <news:news>
         <news:publication><news:name>BILD</news:name><news:language>de</news:language></news:publication>
         <news:publication_date>2026-07-03T11:00:00+02:00</news:publication_date>
         <news:title>Deutschland bangt um den Stammtorwart</news:title>
         <news:keywords>Fußball, Deutschland, Portugal</news:keywords>
     </news:news>
  </url>
</urlset>
"""


@pytest.fixture
def cache_with_feed(tmp_path):
    (tmp_path / "news_kicker_2026-07-03.xml").write_text(FEED_XML, encoding="utf-8")
    (tmp_path / "news_sportschau_2026-07-03.xml").write_text(
        '<?xml version="1.0"?><rss><channel></channel></rss>', encoding="utf-8"
    )
    (tmp_path / "news_bild_2026-07-03.xml").write_text(BILD_SITEMAP_XML, encoding="utf-8")
    return tmp_path


class TestFetchSnippets:
    def test_filters_by_team_name(self, cache_with_feed):
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        result = fetch_snippets("Deutschland", "Portugal", cache_dir=cache_with_feed, cache_tag="2026-07-03", now=now)
        titles = [r["title"] for r in result]
        assert any("Stammtorwart" in t for t in titles)
        assert not any("Frankreich" in t for t in titles)

    def test_excludes_items_older_than_max_age(self, cache_with_feed):
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        result = fetch_snippets(
            "Deutschland", "Portugal", max_age_days=5, cache_dir=cache_with_feed, cache_tag="2026-07-03", now=now
        )
        titles = [r["title"] for r in result]
        assert not any("alter Bericht" in t for t in titles)

    def test_no_match_returns_empty(self, cache_with_feed):
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        result = fetch_snippets(
            "Uruguay", "Japan", cache_dir=cache_with_feed, cache_tag="2026-07-03", now=now
        )
        assert result == []

    def test_respects_max_items(self, tmp_path):
        items = "".join(
            f"<item><title>Deutschland Nachricht {i}</title><description>x</description>"
            f"<pubDate>Thu, 02 Jul 2026 10:00:00 GMT</pubDate></item>"
            for i in range(10)
        )
        (tmp_path / "news_kicker_2026-07-03.xml").write_text(
            f'<?xml version="1.0"?><rss><channel>{items}</channel></rss>', encoding="utf-8"
        )
        (tmp_path / "news_sportschau_2026-07-03.xml").write_text(
            '<?xml version="1.0"?><rss><channel></channel></rss>', encoding="utf-8"
        )
        (tmp_path / "news_bild_2026-07-03.xml").write_text(
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"></urlset>',
            encoding="utf-8",
        )
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        result = fetch_snippets(
            "Deutschland", "Portugal", max_items=3, cache_dir=tmp_path, cache_tag="2026-07-03", now=now
        )
        assert len(result) == 3

    def test_feed_failure_is_resilient(self, tmp_path, monkeypatch):
        import requests

        def fail(*args, **kwargs):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("engine.sources.news.requests.get", fail)
        result = fetch_snippets("Deutschland", "Portugal", cache_dir=tmp_path, cache_tag="none-cached")
        assert result == []

    def test_reads_raw_bytes_for_correct_encoding(self, tmp_path):
        # kicker.de sendet "Content-Type: text/xml" ohne charset - requests
        # rät sonst faelschlich ISO-8859-1 statt des tatsaechlichen UTF-8.
        xml = (
            '<?xml version="1.0" encoding="utf-8"?><rss><channel>'
            "<item><title>Deutschland: Elfmeterschießen entscheidet</title>"
            "<description>Ümlaut-Test</description>"
            "<pubDate>Thu, 02 Jul 2026 10:00:00 GMT</pubDate></item>"
            "</channel></rss>"
        )
        (tmp_path / "news_kicker_2026-07-03.xml").write_bytes(xml.encode("utf-8"))
        (tmp_path / "news_sportschau_2026-07-03.xml").write_text(
            '<?xml version="1.0"?><rss><channel></channel></rss>', encoding="utf-8"
        )
        (tmp_path / "news_bild_2026-07-03.xml").write_text(BILD_SITEMAP_XML, encoding="utf-8")
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        result = fetch_snippets("Deutschland", "Portugal", cache_dir=tmp_path, cache_tag="2026-07-03", now=now)
        assert any("Elfmeterschießen" in item["title"] for item in result)

    def test_report_lists_sources_and_news_state(self, cache_with_feed):
        now = datetime(2026, 7, 3, 12, tzinfo=timezone.utc)
        report = fetch_report("Deutschland", "Portugal", cache_dir=cache_with_feed, cache_tag="2026-07-03", now=now)
        assert report["has_news"] is True
        assert report["checked"] == 3
        assert {s["label"] for s in report["sources"]} == {"Kicker", "Sportschau", "BILD"}
        assert any(s["label"] == "BILD" and s["matches"] == 1 for s in report["sources"])

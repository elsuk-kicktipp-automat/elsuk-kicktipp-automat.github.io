"""News-Schnipsel für die LLM-Analyse (concept.md Schicht 3, Teil 1).

RSS statt HTML-Scraping: stabiles XML, kein Layout-Risiko wie bei
Kicktipp-Scraping. Liefert die jüngsten Schlagzeilen, die einen der beiden
Teamnamen erwähnen – unstrukturiert als roher Text ans LLM, das selbst
beurteilt, ob etwas relevant ist (Verletzung, Sperre, Umbruch). Kein Anspruch
auf Vollständigkeit oder Team-Alias-Erkennung; findet sich nichts, bleibt die
LLM-Anpassung aus (siehe engine/llm.py) – besser keine Anpassung als eine auf
Basis von nichts.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

from ..config import CACHE_DIR

FEEDS = {
    "kicker": "https://newsfeed.kicker.de/news/aktuell",
    "sportschau": "https://www.sportschau.de/fussball/index~rss2.xml",
}

SOURCE_LABELS = {
    "kicker": "Kicker",
    "sportschau": "Sportschau",
    "bild": "BILD",
}

BILD_NEWS_SITEMAP = "https://www.bild.de/sitemap-news.xml"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fetch_feed(name: str, url: str, cache_dir: Path, cache_tag: str) -> list[dict]:
    cache_file = cache_dir / f"news_{name}_{cache_tag}.xml"
    if cache_file.exists():
        raw = cache_file.read_bytes()
    else:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        raw = resp.content
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(raw)

    # rohe Bytes statt resp.text: ElementTree liest die Kodierung aus der
    # XML-Deklaration selbst - manche Feeds (z.B. kicker.de) senden
    # "Content-Type: text/xml" ohne charset, worauf requests fälschlich
    # ISO-8859-1 statt des tatsächlichen UTF-8 rät (Mojibake bei Umlauten)
    root = ElementTree.fromstring(raw)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date_raw = item.findtext("pubDate")
        try:
            pub_date = parsedate_to_datetime(pub_date_raw) if pub_date_raw else None
        except (TypeError, ValueError):
            pub_date = None
        if title:
            items.append(
                {
                    "title": title,
                    "description": description,
                    "published": pub_date,
                    "source": name,
                    "source_label": SOURCE_LABELS.get(name, name),
                }
            )
    return items


def _fetch_bild_news_sitemap(cache_dir: Path, cache_tag: str) -> list[dict]:
    cache_file = cache_dir / f"news_bild_{cache_tag}.xml"
    if cache_file.exists():
        raw = cache_file.read_bytes()
    else:
        resp = requests.get(BILD_NEWS_SITEMAP, timeout=15)
        resp.raise_for_status()
        raw = resp.content
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(raw)

    root = ElementTree.fromstring(raw)
    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
    }
    items = []
    for url_node in root.findall("sm:url", ns):
        loc = (url_node.findtext("sm:loc", default="", namespaces=ns) or "").strip()
        title = (url_node.findtext("news:news/news:title", default="", namespaces=ns) or "").strip()
        keywords = (url_node.findtext("news:news/news:keywords", default="", namespaces=ns) or "").strip()
        published = _parse_iso_datetime(
            url_node.findtext("news:news/news:publication_date", default="", namespaces=ns)
        )
        if title:
            items.append(
                {
                    "title": title,
                    "description": keywords,
                    "published": published,
                    "source": "bild",
                    "source_label": SOURCE_LABELS["bild"],
                    "url": loc,
                }
            )
    return items


def _source_summary(source: str, items: list[dict], error: str | None = None) -> dict:
    return {
        "source": source,
        "label": SOURCE_LABELS.get(source, source),
        "checked": error is None,
        "matches": len(items),
        **({"error": error} if error else {}),
    }


def fetch_report(
    home_name: str,
    away_name: str,
    max_age_days: int = 5,
    max_items: int = 5,
    cache_dir: Path = CACHE_DIR,
    cache_tag: str | None = None,
    now: datetime | None = None,
) -> dict:
    """News-Report für ein Spiel.

    Liefert die relevanten Schnipsel plus eine Quellenübersicht für die
    Website. Best-effort: einzelne kaputte Quellen werden im Report markiert,
    blockieren aber keine Prognose.
    """
    now = now or datetime.now(timezone.utc)
    cache_tag = cache_tag or now.date().isoformat()
    cutoff = now - timedelta(days=max_age_days)
    names = (home_name, away_name)

    all_relevant = []
    sources = []
    fetchers = {
        **{name: (lambda n=name, u=url: _fetch_feed(n, u, cache_dir, cache_tag)) for name, url in FEEDS.items()},
        "bild": lambda: _fetch_bild_news_sitemap(cache_dir, cache_tag),
    }

    for name, fetcher in fetchers.items():
        try:
            items = fetcher()
        except (requests.RequestException, ElementTree.ParseError) as exc:
            print(f"News-Feed {name} nicht verfügbar: {exc}")
            sources.append(_source_summary(name, [], str(exc)))
            continue

        relevant = [
            item
            for item in items
            if any(n in f"{item['title']} {item['description']}" for n in names)
            and (item["published"] is None or item["published"] >= cutoff)
        ]
        sources.append(_source_summary(name, relevant))
        all_relevant += relevant

    all_relevant.sort(key=lambda i: i["published"] or cutoff, reverse=True)
    snippets = all_relevant[:max_items]
    return {
        "snippets": snippets,
        "checked": sum(1 for s in sources if s["checked"]),
        "sources": sources,
        "total_matches": len(all_relevant),
        "returned": len(snippets),
        "has_news": bool(all_relevant),
    }


def fetch_snippets(
    home_name: str,
    away_name: str,
    max_age_days: int = 5,
    max_items: int = 5,
    cache_dir: Path = CACHE_DIR,
    cache_tag: str | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Bis zu `max_items` jüngste Schlagzeilen, die eines der beiden Teams
    erwähnen. Best-effort: [] statt Fehler, wenn ein Feed nicht erreichbar ist."""
    return fetch_report(
        home_name,
        away_name,
        max_age_days=max_age_days,
        max_items=max_items,
        cache_dir=cache_dir,
        cache_tag=cache_tag,
        now=now,
    )["snippets"]

import asyncio
import gzip
import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from mediabias.articles import Crawler, extract, gunzip, parse_sitemap, sitemap_period, window
from mediabias.config import DATA, Settings
from mediabias.db import Article, Base, FeedState, database

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
BODY = "Meclis'te bugün yapılan oturumda bütçe görüşmeleri uzun tartışmalara sahne oldu. " * 6

ROBOTS = """User-agent: *
Disallow: /arama/
Sitemap: https://www.example.com.tr/feeds/sitemapindex
"""
INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.example.com.tr/feeds/sitemapposts?year=2026&amp;month=9</loc>
<lastmod>2026-10-09T11:49:58+03:00</lastmod></sitemap>
<sitemap><loc>https://www.example.com.tr/feeds/sitemapposts?year=2026&amp;month=6</loc>
<lastmod>2026-10-09T11:49:58+03:00</lastmod></sitemap>
<sitemap><loc>https://elsewhere.example/sitemap.xml</loc></sitemap>
</sitemapindex>"""
URLSET = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
 xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://www.example.com.tr/gundem/jsonld-haber?utm_source=x</loc>
<news:news><news:publication_date>2026-09-03T10:00:00+03:00</news:publication_date></news:news></url>
<url><loc>https://www.example.com.tr/gundem/html-haber</loc><lastmod>2026-09-04T09:00:00+03:00</lastmod></url>
<url><loc>https://www.example.com.tr/gundem/ucretli</loc><lastmod>2026-09-05</lastmod></url>
<url><loc>https://www.example.com.tr/arama/gizli</loc><lastmod>2026-09-05</lastmod></url>
<url><loc>https://www.example.com.tr/gundem/eski</loc><lastmod>2026-08-01</lastmod></url>
<url><loc>https://www.example.com.tr/gundem/yasak</loc><lastmod>2026-09-06</lastmod></url>
</urlset>"""


def page(ld=None, body="", title="Başlık"):
    script = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld else ""
    return f"<html><head><title>{title}</title>{script}</head><body>{body}</body></html>"


PAGES = {
    "/gundem/jsonld-haber": page(
        {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebPage"},
                {
                    "@type": "NewsArticle",
                    "headline": "Bütçe görüşmeleri",
                    "datePublished": "2026-09-03T10:00:00+03:00",
                    "author": [{"@type": "Person", "name": "Ayşe Yazar"}],
                    "articleBody": BODY,
                },
            ],
        }
    ),
    "/gundem/html-haber": (
        '<html><head><meta property="og:title" content="Ekonomi haberi">'
        '<meta property="article:published_time" content="2026-09-04T09:00:00">'
        '<link rel="canonical" href="/gundem/html-haber"></head><body><article>'
        + "".join(f"<p>{BODY[:120]} {i}</p>" for i in range(4))
        + "<aside><p>İlgili haberler listesi burada yer alıyor ve sayılmamalı.</p></aside></article></body></html>"
    ),
    "/gundem/ucretli": page(
        {
            "@type": "NewsArticle",
            "headline": "Abone",
            "datePublished": "2026-09-05",
            "isAccessibleForFree": False,
            "articleBody": BODY,
        }
    ),
}


def handler(requested):
    def respond(request):
        requested.append(str(request.url))
        path, query = request.url.path, request.url.query.decode()
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if path == "/feeds/sitemapindex":
            return httpx.Response(200, text=INDEX)
        if path == "/feeds/sitemapposts" and "month=9" in query:
            return httpx.Response(200, content=gzip.compress(URLSET.encode()))
        if path == "/gundem/yasak":
            return httpx.Response(403, text="challenge")
        if path in PAGES:
            return httpx.Response(200, text=PAGES[path])
        return httpx.Response(404)

    return respond


def run_crawl(start, end, db=None):
    settings = Settings(database_url="sqlite:///:memory:", crawl_delay_seconds=0.5)
    engine, sessions = db or database(settings.database_url)
    Base.metadata.create_all(engine)
    source = {"id": "ornek", "name": "Örnek", "website": "https://www.example.com.tr", "feeds": []}
    requested = []
    crawler = Crawler(settings, sessions)

    async def go():
        transport = httpx.MockTransport(handler(requested))
        async with httpx.AsyncClient(transport=transport) as client:
            from mediabias.articles import HostGate

            return await crawler.one(client, HostGate(0), source, start, end, 100)

    with patch("mediabias.feeds.socket.getaddrinfo", return_value=PUBLIC_DNS):
        outcome = asyncio.run(go())
    with sessions() as s:
        articles = s.scalars(select(Article).order_by(Article.url)).all()
        state = s.get(FeedState, "crawl:ornek").payload
    if db is None:
        engine.dispose()
    return outcome, articles, state, requested


def test_end_to_end_backfill_from_sitemaps():
    outcome, articles, state, requested = run_crawl(*window(date(2026, 9, 1), date(2026, 10, 10)))
    assert [a.url for a in articles] == [
        "https://www.example.com.tr/gundem/html-haber",
        "https://www.example.com.tr/gundem/jsonld-haber",
    ]
    assert all(a.content_scope == "full_text" and len(a.text) >= 300 for a in articles)
    assert "İlgili haberler" not in articles[0].text
    assert articles[1].published_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 9, 3, 7, tzinfo=timezone.utc
    )
    assert outcome["inserted"] == 2 and outcome["skipped"] == {"paywalled": 1, "blocked": 1}
    assert outcome["candidates"] == 4
    assert state["status"] == "ok"
    # Out-of-window month, foreign host, robots-disallowed path and old article are never requested.
    assert not any("month=6" in u or "elsewhere" in u or "/arama/" in u or "/eski" in u for u in requested)


def test_rerun_skips_collected_articles():
    start, end = window(date(2026, 9, 1), date(2026, 10, 10))
    db = database("sqlite:///:memory:")
    first, *_ = run_crawl(start, end, db)
    second, articles, _, requested = run_crawl(start, end, db)
    assert first["candidates"] == 4  # jsonld, html, paywalled, blocked; disallowed never queued
    assert second["candidates"] == 2 and second["inserted"] == 0 and len(articles) == 2
    assert not any(u.endswith(("/jsonld-haber", "/html-haber")) for u in requested[-6:])
    db[0].dispose()


def test_feed_excerpt_is_upgraded_to_full_text():
    settings = Settings(database_url="sqlite:///:memory:")
    engine, sessions = database(settings.database_url)
    Base.metadata.create_all(engine)
    from mediabias.articles import store
    from mediabias.db import NewsEvent

    with sessions.begin() as s:
        s.add(NewsEvent(id="e1", title="x"))
        s.flush()
        s.add(
            Article(
                event_id="e1",
                source_id="ornek",
                source_name="Örnek",
                title="t",
                text="kısa özet",
                url="https://www.example.com.tr/a",
                content_scope="feed_excerpt",
                content_hash="0" * 64,
            )
        )
    item = {
        "title": "t",
        "text": BODY,
        "url": "https://www.example.com.tr/a",
        "canonical": "https://www.example.com.tr/a",
        "published_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
    }
    with sessions.begin() as s:
        assert store(s, {"id": "ornek", "name": "Örnek"}, item) == "upgraded"
    with sessions() as s:
        row = s.scalar(select(Article))
        assert row.content_scope == "full_text" and row.text == BODY
    engine.dispose()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://x.tr/feeds/sitemapposts?year=2026&month=9", (date(2026, 9, 1), date(2026, 9, 30))),
        ("https://x.tr/sitemap/2026/02/news.xml", (date(2026, 2, 1), date(2026, 2, 28))),
        ("https://x.tr/sitemap-2026-09-14.xml", (date(2026, 9, 14), date(2026, 9, 14))),
        ("https://x.tr/sitemap-2025.xml", (date(2025, 1, 1), date(2025, 12, 31))),
        ("https://x.tr/sitemap_google_news.xml", None),
    ],
)
def test_sitemap_period(url, expected):
    assert sitemap_period(url) == expected


def test_undated_article_is_rejected_not_dated_now():
    result = extract(page({"@type": "NewsArticle", "headline": "x", "articleBody": BODY}), "https://x.tr/a")
    assert result["reason"] == "no_date" and result["published_at"] is None


def test_hostile_xml_and_gzip_bomb_rejected():
    with pytest.raises(Exception):
        parse_sitemap(b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><urlset>&y;</urlset>')
    with pytest.raises(ValueError):
        gunzip(gzip.compress(b"0" * 60_000_000))


def test_every_registered_outlet_is_crawlable():
    register = json.loads((DATA / "sources.json").read_text())["sources"]
    assert len(register) == 53
    assert all(s["website"] and s["website"].startswith("https://") for s in register)
    settings = Settings(database_url="sqlite:///:memory:")
    assert len(Crawler(settings, None).sources()) == 53

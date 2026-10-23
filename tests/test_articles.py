import asyncio
import gzip
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from mediabias.articles import (
    Crawler,
    Fetcher,
    HostGate,
    extract,
    gunzip,
    parse_sitemap,
    sitemap_period,
    window,
)
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


def run_crawl(start, end, db=None, respond=None, **overrides):
    settings = Settings(database_url="sqlite:///:memory:", crawl_delay_seconds=0.5, **overrides)
    engine, sessions = db or database(settings.database_url)
    Base.metadata.create_all(engine)
    source = {"id": "ornek", "name": "Örnek", "website": "https://www.example.com.tr", "feeds": []}
    requested, slept = [], []
    crawler = Crawler(settings, sessions)

    async def no_sleep(seconds):
        slept.append(seconds)

    async def go():
        transport = httpx.MockTransport(respond or handler(requested))
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(client, HostGate(0), settings, no_sleep)
            return await crawler.one(fetcher, source, start, end, 100)

    with patch("mediabias.feeds.socket.getaddrinfo", return_value=PUBLIC_DNS):
        outcome = asyncio.run(go())
    with sessions() as s:
        articles = s.scalars(select(Article).order_by(Article.url)).all()
        state = s.get(FeedState, "crawl:ornek").payload
    report = crawler.report()
    if db is None:
        engine.dispose()
    return {
        "outcome": outcome,
        "articles": articles,
        "state": state,
        "requested": requested,
        "slept": slept,
        "report": report,
    }


WINDOW = window(date(2026, 9, 1), date(2026, 10, 10))
UNLOCKER = {"BRIGHTDATA_API_KEY": "key", "BRIGHTDATA_UNLOCKER_ZONE": "web_unlocker1"}


def test_end_to_end_backfill_from_sitemaps():
    r = run_crawl(*WINDOW)
    outcome, articles, state, requested = r["outcome"], r["articles"], r["state"], r["requested"]
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
    assert state["status"] == "ok" and outcome["pending_links"] == 1
    assert [(x["url"].rsplit("/", 1)[1], x["status"], x["last_reason"]) for x in r["report"]] == [
        ("yasak", "pending", "blocked")
    ]
    # Out-of-window month, foreign host, robots-disallowed path and old article are never requested.
    assert not any("month=6" in u or "elsewhere" in u or "/arama/" in u or "/eski" in u for u in requested)


def test_rerun_skips_collected_articles_and_retries_refused_link():
    db = database("sqlite:///:memory:")
    first = run_crawl(*WINDOW, db)["outcome"]
    second = run_crawl(*WINDOW, db)
    assert first["candidates"] == 4  # jsonld, html, paywalled, blocked; disallowed never queued
    assert second["outcome"]["candidates"] == 1  # paywalled is re-checked; collected ones are skipped
    assert second["outcome"]["retried_from_queue"] == 1 and len(second["articles"]) == 2
    assert not any(u.endswith(("/jsonld-haber", "/html-haber")) for u in second["requested"])
    assert second["report"][0]["attempts"] == 2
    db[0].dispose()


def flaky(requested, behaviour):
    """The example outlet, except /gundem/yasak follows `behaviour(call_number)`."""
    base, calls = handler(requested), []

    def respond(request):
        if request.url.host == "api.brightdata.com":
            requested.append("UNLOCKER " + json.loads(request.content)["url"])
            body = json.loads(request.content)
            assert body["zone"] == "web_unlocker1" and body["country"] == "tr" and body["format"] == "raw"
            assert request.headers["authorization"] == "Bearer key"
            return httpx.Response(200, text=YASAK)
        if request.url.path == "/gundem/yasak":
            requested.append(str(request.url))
            calls.append(1)
            return behaviour(len(calls))
        return base(request)

    return respond


YASAK = page(
    {"@type": "NewsArticle", "headline": "Engelli haber", "datePublished": "2026-09-06", "articleBody": BODY}
)


def test_rate_limit_waits_retry_after_then_succeeds():
    requested = []
    r = run_crawl(
        *WINDOW,
        respond=flaky(
            requested,
            lambda n: (
                httpx.Response(429, headers={"retry-after": "7"})
                if n < 3
                else httpx.Response(200, text=YASAK)
            ),
        ),
    )
    assert r["outcome"]["inserted"] == 3 and r["outcome"]["pending_links"] == 0
    assert r["slept"].count(7.0) == 2


def test_challenge_page_with_200_is_a_refusal_not_an_article():
    challenge = (
        "<html><head><title>Just a moment...</title></head><body>cf-browser-verification</body></html>"
    )
    r = run_crawl(*WINDOW, respond=flaky([], lambda n: httpx.Response(200, text=challenge)))
    assert r["outcome"]["skipped"]["blocked"] == 1 and r["outcome"]["pending_links"] == 1
    assert not any("Engelli" in a.title or "moment" in a.title for a in r["articles"])


def test_refused_page_fetched_through_unlocker_when_configured():
    requested = []
    r = run_crawl(*WINDOW, respond=flaky(requested, lambda n: httpx.Response(403)), **UNLOCKER)
    assert r["outcome"]["inserted"] == 3 and r["outcome"]["via_unlocker"] == 1
    assert r["outcome"]["pending_links"] == 0 and r["report"] == []
    assert "UNLOCKER https://www.example.com.tr/gundem/yasak" in requested
    # Paywalled pages are never sent to the unlocker.
    assert not any(u.startswith("UNLOCKER") and "ucretli" in u for u in requested)


def test_refused_robots_txt_is_read_through_unlocker_and_obeyed():
    requested, base = [], handler([])

    def respond(request):
        if request.url.host == "api.brightdata.com":
            target = json.loads(request.content)["url"]
            requested.append("UNLOCKER " + target)
            assert target.endswith("/robots.txt")
            return httpx.Response(200, text=ROBOTS + "Disallow: /gundem/html-haber\n")
        if request.url.path == "/robots.txt":
            return httpx.Response(403)
        return base(request)

    r = run_crawl(*WINDOW, respond=respond, **UNLOCKER)
    assert [a.url.rsplit("/", 1)[1] for a in r["articles"]] == ["jsonld-haber"]


def test_link_given_up_after_repeated_refusals_stays_in_report():
    db = database("sqlite:///:memory:")
    for _ in range(10):
        r = run_crawl(*WINDOW, db, respond=flaky([], lambda n: httpx.Response(403)))
    assert r["outcome"]["given_up_links"] == 1 and r["report"][0]["status"] == "given_up"
    eleventh = run_crawl(*WINDOW, db, respond=flaky([], lambda n: httpx.Response(403)))
    assert eleventh["outcome"]["retried_from_queue"] == 0 and len(eleventh["report"]) == 1
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


# --------------------------------------------------------------------------- survey-driven behaviour


def test_subdomains_of_the_outlet_are_allowed_but_lookalikes_are_not():
    from mediabias.articles import AllowedHosts

    allowed = AllowedHosts({"haber7.com", "t24.com.tr"})
    assert "spor.haber7.com" in allowed and "media-cdn.t24.com.tr" in allowed
    assert "evilhaber7.com" not in allowed and "other.com.tr" not in allowed and "com.tr" not in allowed


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.ekonomim.com/sitemap/2026/9", (date(2026, 9, 1), date(2026, 9, 30))),
        ("https://onedio.com/secure/sitemap/09-2026.xml.gz", (date(2026, 9, 1), date(2026, 9, 30))),
        ("https://media-cdn.t24.com.tr/media/sitemaps/sitemap-20261022.xml", (date(2026, 10, 22),) * 2),
        ("https://www.trthaber.com/sitemaps/news-2026-09.xml", (date(2026, 9, 1), date(2026, 9, 30))),
    ],
)
def test_survey_archive_sitemap_names(url, expected):
    assert sitemap_period(url) == expected


def test_media_pages_and_url_dates():
    from mediabias.articles import is_media, url_date

    assert is_media("https://www.yenisafak.com/video/gundem/x-123456")
    assert is_media("https://www.ekonomim.com/foto/x-galeri-926664")
    assert not is_media("https://www.sabah.com.tr/gundem/2026/10/22/videolu-degil-haber")
    assert url_date("https://www.sabah.com.tr/gundem/2026/10/22/x") == datetime(
        2026, 10, 22, 9, tzinfo=timezone.utc
    )


def test_breaking_news_stub_is_queued_for_a_later_fetch():
    stub = page(
        {
            "@type": "NewsArticle",
            "headline": "Son dakika",
            "datePublished": "2026-09-06",
            "articleBody": "Son dakika: Meclis'te kritik oylama tamamlandı. Ayrıntılar geliyor... " * 5,
        }
    )
    assert extract(stub, "https://x.tr/a")["reason"] == "stub"
    r = run_crawl(*WINDOW, respond=flaky([], lambda n: httpx.Response(200, text=stub)))
    assert r["outcome"]["skipped"]["stub"] == 1
    assert [(x["url"].rsplit("/", 1)[1], x["last_reason"]) for x in r["report"]] == [("yasak", "stub")]


def test_outlet_without_sitemaps_is_discovered_from_homepage_sections_and_feed():
    """Posta/Vatan/CNN Türk/Milliyet/DHA-style outlet: no Sitemap line, sitemap guesses fail."""
    from mediabias.db import now

    today = now().astimezone(timezone.utc)
    stamp = today.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    article = page(
        {"@type": "NewsArticle", "headline": "Yeni haber", "datePublished": stamp, "articleBody": BODY}
    )
    home = (
        '<a href="/gundem">Gündem</a><a href="/ekonomi/kur-artisi-2419590">a</a>'
        '<a href="https://spor.example.com.tr/futbol/mac-3670785">b</a>'
        '<a href="/video/izle-1234567">video</a><a href="/hakkimizda">static</a>'
        '<a href="https://elsewhere.example/x-1234567">foreign</a>'
    )
    section = '<a href="/gundem/meclis-karari-2419600">c</a>'
    requested = []

    def respond(request):
        requested.append(str(request.url))
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /arama/\n")
        if path == "/":
            return httpx.Response(200, text=home)
        if path == "/gundem":
            return httpx.Response(200, text=section)
        if path in {"/ekonomi/kur-artisi-2419590", "/futbol/mac-3670785", "/gundem/meclis-karari-2419600"}:
            return httpx.Response(200, text=article.replace("Yeni haber", "Haber " + path[-7:]))
        return httpx.Response(404, text="yok")

    end = now()
    r = run_crawl(end - timedelta(hours=48), end, respond=respond)
    assert sorted(a.url.rsplit("-", 1)[1] for a in r["articles"]) == ["2419590", "2419600", "3670785"]
    assert not any("/video/" in u or "hakkimizda" in u or "elsewhere" in u for u in requested)
    assert r["outcome"]["sitemap_errors"] == len(__import__("mediabias.articles").articles.FALLBACK_SITEMAPS)


def test_old_backfill_does_not_trawl_homepage_when_sitemaps_answer():
    r = run_crawl(*WINDOW)
    assert "listing_pages_read" not in r["outcome"]
    assert not any(u.rstrip("/") == "https://www.example.com.tr" for u in r["requested"])


def test_overrides_file_is_valid_and_names_registered_outlets():
    from mediabias.articles import OVERRIDES_PATH

    ids = {s["id"] for s in json.loads((DATA / "sources.json").read_text())["sources"]}
    data = json.loads(OVERRIDES_PATH.read_text())
    assert set(data) - {"_note"} <= ids

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from mediabias.feeds import canonical, fetch, parse, public_url

CLOCK = datetime(2026, 9, 9, tzinfo=timezone.utc)
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title><link>https://example.org</link><description>Test</description><item><title>New tariff</title><link>https://example.org/story?utm_source=rss</link><description>Fare changed from 25 to 30 lira for next month.</description><pubDate>Tue, 08 Sep 2026 12:00:00 GMT</pubDate></item></channel></rss>"""


def test_feed_publication_window_and_canonical_url():
    items, rejected = parse(RSS, {"example.org"}, CLOCK)
    assert len(items) == 1 and rejected == 0
    assert items[0]["url"] == "https://example.org/story"
    assert items[0]["published_at"].tzinfo is not None


@pytest.mark.parametrize("change", ["missing_date", "old_date", "future_date", "foreign_link"])
def test_unusable_items_not_silently_dated_now(change):
    rss = RSS
    if change == "missing_date":
        rss = rss.replace(b"<pubDate>Tue, 08 Sep 2026 12:00:00 GMT</pubDate>", b"")
    elif change == "old_date":
        rss = rss.replace(b"08 Sep 2026", b"08 Aug 2026")
    elif change == "future_date":
        rss = rss.replace(b"08 Sep 2026", b"12 Sep 2026")
    else:
        rss = rss.replace(b"https://example.org/story", b"https://foreign.org/story")
    assert parse(rss, {"example.org"}, CLOCK) == ([], 1)


def test_malformed_or_html_response_not_feed():
    with pytest.raises(ValueError):
        parse(b"<html><body>Login</body></html>", {"example.org"}, CLOCK)
    with pytest.raises(Exception):
        parse(b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><rss>&y;</rss>', {"example.org"}, CLOCK)


def test_canonical_preserves_meaningful_query():
    assert canonical("https://Example.org/a?id=2&utm_medium=x#fragment") == "https://example.org/a?id=2"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/rss",
        "https://user:pass@example.org/rss",
        "https://example.org:8443/rss",
        "https://foreign.org/rss",
    ],
)
def test_feed_allowlist(url):
    with pytest.raises(ValueError):
        asyncio.run(public_url(url, {"example.org"}))


def test_private_dns_address_rejected():
    with patch("mediabias.feeds.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        with pytest.raises(ValueError, match="Non-public"):
            asyncio.run(public_url("https://example.org/rss", {"example.org"}))


def test_redirect_to_unlisted_host_rejected_before_request():
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://unlisted.example/rss"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="allowlist"):
                await fetch(client, "https://example.org/rss", {"example.org"}, {})

    with patch("mediabias.feeds.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        asyncio.run(run())
    assert len(requested) == 1


def test_conditional_not_modified():
    def handler(request):
        assert request.headers["if-none-match"] == "etag"
        return httpx.Response(304)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch(client, "https://example.org/rss", {"example.org"}, {"etag": "etag"})

    with patch("mediabias.feeds.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        assert asyncio.run(run())[0] is None


def test_new_network_failure_does_not_reuse_old_http_success():
    from unittest.mock import AsyncMock

    from mediabias.config import Settings
    from mediabias.db import Base, FeedState, database
    from mediabias.feeds import Collector

    settings = Settings(database_url="sqlite:///:memory:")
    engine, sessions = database(settings.database_url)
    Base.metadata.create_all(engine)
    with sessions.begin() as s:
        s.add(
            FeedState(
                url="https://example.org/rss",
                payload={"http_status": 200, "parsed": True, "usable_items": 5, "last_success": "previous"},
            )
        )
    with patch("mediabias.feeds.fetch", AsyncMock(side_effect=OSError("DNS unavailable"))):
        result = asyncio.run(
            Collector(settings, sessions).one(
                None, {"id": "one"}, {"url": "https://example.org/rss"}, {"example.org"}
            )
        )
    assert result["http_status"] is None and result["parsed"] is None
    assert result["usable_items"] is None
    assert result["last_success"] == "previous"
    engine.dispose()

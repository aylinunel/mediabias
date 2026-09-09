"""Bounded, unauthenticated RSS/Atom ingestion. No paywall or challenge bypass."""

import asyncio
import calendar
import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from sqlalchemy import select

from .db import Article, FeedState, NewsEvent, now

MAX_BYTES = 2_000_000


def canonical(url):
    p = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(p.query)
        if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}
    ]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(query), ""))


def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def clean(value):
    soup = BeautifulSoup(value or "", "html.parser")
    for tag in soup(["script", "style", "iframe"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


async def public_url(url, allowed_hosts):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.username
        or p.password
        or p.port not in {None, 443}
        or host(url) not in allowed_hosts
    ):
        raise ValueError("Endpoint is outside the configured public HTTPS allowlist.")
    addresses = await asyncio.to_thread(socket.getaddrinfo, p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Non-public destination is not permitted.")


async def fetch(client, url, allowed_hosts, state):
    headers = {
        "User-Agent": "MediaBiasResearch/0.1 (public RSS only)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
    }
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("modified"):
        headers["If-Modified-Since"] = state["modified"]
    for _ in range(4):
        await public_url(url, allowed_hosts)
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.headers.get("location", ""))
                # Conditional headers are not forwarded across redirects.
                headers.pop("If-None-Match", None)
                headers.pop("If-Modified-Since", None)
                continue
            if response.status_code == 304:
                return None, dict(response.headers), 304
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Feed exceeds the 2 MB decoded size limit.")
                chunks.append(chunk)
            return b"".join(chunks), dict(response.headers), response.status_code
    raise ValueError("Feed redirect limit exceeded.")


def parse(data, allowed_hosts, clock=None):
    root = ElementTree.fromstring(data)
    if root.tag.split("}")[-1].lower() not in {"rss", "feed", "rdf"}:
        raise ValueError("Response is not RSS or Atom.")
    feed = feedparser.parse(data)
    if feed.bozo:
        raise ValueError("Feed is malformed.")
    clock = clock or now()
    items, rejected = [], 0
    for entry in feed.entries[:100]:
        title, text, url = clean(entry.get("title")), clean(entry.get("summary")), entry.get("link", "")
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if (
            not stamp
            or not title
            or len(text) < 20
            or host(url) not in allowed_hosts
            or urlsplit(url).scheme != "https"
        ):
            rejected += 1
            continue
        published = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc)
        if published < clock - timedelta(days=7) or published > clock + timedelta(hours=6):
            rejected += 1
            continue
        items.append(
            {"title": title[:500], "text": text[:20000], "url": canonical(url), "published_at": published}
        )
    return items[:12], rejected


def tokens(text):
    return set(re.findall(r"\w{3,}", text.replace("İ", "i").replace("I", "ı").lower())) - {
        "için",
        "ile",
        "bir",
        "olan",
        "son",
        "haber",
    }


def candidate_event(session, title, published):
    # Conservative candidate grouping; the interface supports human reassignment.
    recent = session.scalars(
        select(Article)
        .where(
            Article.published_at >= published - timedelta(hours=48),
            Article.published_at <= published + timedelta(hours=48),
        )
        .order_by(Article.published_at.desc())
        .limit(1000)
    ).all()
    wanted, best, score = tokens(title), None, 0.0
    for a in recent:
        t = tokens(a.title)
        similarity = len(t & wanted) / max(1, len(t | wanted))
        if similarity > max(0.42, score):
            best, score = a.event_id, similarity
    if best:
        return best
    event = NewsEvent(title=title, grouping="lexical_candidate")
    session.add(event)
    session.flush()
    return event.id


class Collector:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions
        self.lock = asyncio.Lock()

    def register(self):
        return json.loads(self.settings.sources_path.read_text())

    async def collect(self):
        if self.lock.locked():
            raise ValueError("A collection is already running in this process.")
        async with self.lock:
            gate = asyncio.Semaphore(self.settings.http_concurrency)
            selected = set(self.settings.active_source_ids)
            sources = [s for s in self.register()["sources"] if not selected or s["id"] in selected]
            if selected - {s["id"] for s in sources}:
                raise ValueError("Unknown active source ID in configuration.")
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:

                async def collect_source(source):
                    results = []
                    allowed = {host(source.get("website") or "")} | {host(f["url"]) for f in source["feeds"]}
                    allowed.discard("")
                    for f in source["feeds"]:
                        if not f["enabled"]:
                            continue
                        async with gate:
                            results.append(await self.one(client, source, f, allowed))
                    return results

                groups = await asyncio.gather(*(collect_source(s) for s in sources))
                return [r for group in groups for r in group]

    async def one(self, client, source, feed, allowed):
        url = feed["url"]
        with self.sessions() as session:
            record = session.get(FeedState, url)
            old = record.payload if record else {}
        outcome = {
            **old,
            "source_id": source["id"],
            "url": url,
            "checked_at": now().isoformat(),
            "http_status": None,
            "parsed": None,
            "usable_items": None,
            "rejected_items": None,
            "inserted": 0,
            "updated": 0,
        }
        try:
            data, headers, status = await fetch(client, url, allowed, old)
            outcome["http_status"] = status
            if data is None:
                outcome.update(status="unchanged", error=None)
            else:
                items, rejected = parse(data, allowed)
                outcome.update(
                    status="eligible_active" if items else "no_recent_usable_items",
                    error=None,
                    parsed=True,
                    usable_items=len(items),
                    rejected_items=rejected,
                    etag=headers.get("etag"),
                    modified=headers.get("last-modified"),
                )
                with self.sessions.begin() as session:
                    for item in items:
                        digest = hashlib.sha256((item["title"] + "\n" + item["text"]).encode()).hexdigest()
                        article = session.scalar(select(Article).where(Article.url == item["url"]))
                        if article:
                            if article.content_hash != digest:
                                article.title, article.text, article.content_hash = (
                                    item["title"],
                                    item["text"],
                                    digest,
                                )
                                article.fetched_at = now()
                                outcome["updated"] += 1
                            continue
                        # Wire duplicates stay visible across outlets; repeated same-outlet payloads do not.
                        duplicate = session.scalar(
                            select(Article.id).where(
                                Article.source_id == source["id"], Article.content_hash == digest
                            )
                        )
                        if duplicate:
                            continue
                        event_id = candidate_event(session, item["title"], item["published_at"])
                        session.add(
                            Article(
                                event_id=event_id,
                                source_id=source["id"],
                                source_name=source["name"],
                                content_scope="feed_excerpt",
                                content_hash=digest,
                                **item,
                            )
                        )
                        session.flush()
                        outcome["inserted"] += 1
            outcome.update(last_success=now().isoformat(), consecutive_failures=0)
        except httpx.HTTPStatusError as error:
            outcome.update(
                status="access_restricted"
                if error.response.status_code in {401, 403}
                else "candidate_failed_runtime",
                http_status=error.response.status_code,
                error="Feed HTTP request was not successful.",
            )
        except (ValueError, httpx.HTTPError, OSError, ElementTree.ParseError) as error:
            outcome.update(status="candidate_failed_runtime", error=type(error).__name__)
        except Exception as error:
            # Preserve per-source observability even if one parser/driver fails; never emit raw response bodies.
            outcome.update(status="needs_editorial_review", error=type(error).__name__)
        if outcome.get("error"):
            outcome["consecutive_failures"] = old.get("consecutive_failures", 0) + 1
        with self.sessions.begin() as session:
            record = session.get(FeedState, url)
            if record:
                record.payload = outcome
            else:
                session.add(FeedState(url=url, payload=outcome))
        return outcome

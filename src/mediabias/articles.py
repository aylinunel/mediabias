"""Full-text collection from each registered outlet's own sitemaps and article pages.

Covers every outlet in the source register, including those without a working RSS feed.
Honours robots.txt (including Crawl-delay), stays on the outlet's registered hosts, and
never bypasses paywalls or logins. Pages a publisher refuses (401/403/451, persistent 429,
challenge pages) are retried politely and, when a Bright Data Web Unlocker zone is configured,
fetched through it; links that still fail stay in a per-outlet retry queue and in
`mediabias crawl-report`, so none is silently dropped.
Publication dates are taken from the publisher; undated articles are dropped, never dated
from fetch time.
"""

import asyncio
import hashlib
import json
import re
import time
import zlib
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from sqlalchemy import select

from .db import Article, FeedState, now
from .feeds import candidate_event, canonical, clean, host, public_url

# Browser-compatible but honest: names the project so publishers can contact or block it.
USER_AGENT = "Mozilla/5.0 (compatible; MediaBiasResearch/0.3; +https://github.com/aylinunel/mediabias)"
UNLOCKER_ENDPOINT = "https://api.brightdata.com/request"
GIVE_UP_AFTER = 10  # attempts across runs before a refused link is reported as given up
ROBOTS_AGENT = "MediaBiasResearch"
ISTANBUL = timezone(timedelta(hours=3))
MAX_PAGE_BYTES = 3_000_000
MAX_SITEMAP_BYTES = 50_000_000  # sitemaps.org limit for an uncompressed sitemap
MAX_SITEMAPS_PER_SOURCE = 300
MIN_TEXT_CHARS = 300
FALLBACK_SITEMAPS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/news-sitemap.xml",
    "/sitemap_google_news.xml",
    "/sitemaps/news.xml",
)
ARTICLE_TYPES = {
    "newsarticle",
    "article",
    "reportagenewsarticle",
    "analysisnewsarticle",
    "opinionnewsarticle",
    "backgroundnewsarticle",
    "blogposting",
}
BODY_SELECTORS = (
    "[itemprop=articleBody]",
    "article .content-text",
    "article .news-content",
    ".article-content",
    ".article-body",
    ".news-content",
    ".news-detail",
    ".content-text",
    ".detail-content",
    ".haber-detay",
    ".post-content",
    ".entry-content",
    "article",
)


# --------------------------------------------------------------------------- fetching


class Blocked(Exception):
    """The publisher refused the request (401/403/451) or served a challenge."""


class HostGate:
    """One request at a time per host, spaced by the larger of our delay and robots Crawl-delay."""

    def __init__(self, delay):
        self.delay, self.locks, self.last, self.extra = delay, {}, {}, {}

    async def wait(self, hostname):
        lock = self.locks.setdefault(hostname, asyncio.Lock())
        await lock.acquire()
        gap = max(self.delay, self.extra.get(hostname, 0)) - (time.monotonic() - self.last.get(hostname, 0))
        if gap > 0:
            await asyncio.sleep(gap)
        return lock

    def done(self, hostname, lock):
        self.last[hostname] = time.monotonic()
        lock.release()

    def slow_down(self, hostname):
        """After a refusal, double this host's spacing (up to 30 s) for the rest of the run."""
        self.extra[hostname] = min(30.0, max(self.delay, self.extra.get(hostname, 0)) * 2)


CHALLENGE_MARKERS = (
    b"cf-browser-verification",
    b"challenge-platform",
    b"<title>just a moment",
    b"<title>attention required",
    b"<title>access denied",
    b"_incapsula_resource",
    b"captcha-delivery.com",
)


def looks_challenged(data):
    head = data[:30_000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


def retry_after(response, attempt):
    value = response.headers.get("retry-after", "")
    return min(120.0, float(value)) if value.isdigit() else (5.0, 20.0, 60.0)[min(attempt, 2)]


async def read(response, limit):
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise ValueError("Response exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


class Fetcher:
    """Direct, polite requests first; Bright Data Web Unlocker only for what a publisher refuses."""

    TRANSIENT = {429, 500, 502, 503, 504}

    def __init__(self, client, gate, settings, sleep=asyncio.sleep):
        self.client, self.gate, self.sleep = client, gate, sleep
        self.retries = settings.crawl_retries
        self.unlocker = (
            (settings.brightdata_api_key, settings.brightdata_zone)
            if settings.brightdata_api_key and settings.brightdata_zone
            else None
        )
        self.unlocked = 0

    async def get(self, url, allowed, limit, accept="*/*"):
        """Returns (bytes, final_url, via) where via is 'direct' or 'unlocker'. Raises Blocked."""
        try:
            data, final = await self.direct(url, allowed, limit, accept)
            return data, final, "direct"
        except Blocked:
            self.gate.slow_down(host(url))
            if not self.unlocker:
                raise
        return await self.via_unlocker(url, allowed, limit), url, "unlocker"

    async def direct(self, url, allowed, limit, accept):
        """GET a public HTTPS URL on an allowed host; checked redirects; waits out rate limits."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
        }
        attempt = redirects = 0
        while True:
            await public_url(url, allowed)
            hostname = host(url)
            lock = await self.gate.wait(hostname)
            try:
                async with self.client.stream("GET", url, headers=headers) as response:
                    status = response.status_code
                    if status in {301, 302, 303, 307, 308}:
                        redirects += 1
                        if redirects > 4:
                            raise ValueError("Redirect limit exceeded.")
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if status in {401, 403, 451}:
                        raise Blocked(status)
                    if status in self.TRANSIENT:
                        if attempt >= self.retries:
                            raise (
                                Blocked(status)
                                if status == 429
                                else httpx.HTTPStatusError(
                                    "Transient failure persisted.",
                                    request=response.request,
                                    response=response,
                                )
                            )
                        wait = retry_after(response, attempt)
                    else:
                        response.raise_for_status()
                        data = await read(response, limit)
                        if looks_challenged(data):  # regardless of content-type: some send challenges as text
                            raise Blocked("challenge")
                        return data, url
            finally:
                self.gate.done(hostname, lock)
            attempt += 1
            await self.sleep(wait)

    async def via_unlocker(self, url, allowed, limit):
        await public_url(url, allowed)  # same host allowlist as direct requests
        key, zone = self.unlocker
        for attempt in range(self.retries + 1):
            async with self.client.stream(
                "POST",
                UNLOCKER_ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                json={"zone": zone, "url": url, "format": "raw", "country": "tr"},
                timeout=120,
            ) as response:
                if response.status_code == 200:
                    data = await read(response, limit)
                    if looks_challenged(data):
                        raise Blocked("challenge_after_unlocker")
                    self.unlocked += 1
                    return data
                if response.status_code not in self.TRANSIENT or attempt == self.retries:
                    raise Blocked(f"unlocker_{response.status_code}")
            await self.sleep((5.0, 20.0, 60.0)[min(attempt, 2)])
        raise Blocked("unlocker")


def gunzip(data):
    if not data.startswith(b"\x1f\x8b"):
        return data
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = inflater.decompress(data, MAX_SITEMAP_BYTES)
    if inflater.unconsumed_tail:
        raise ValueError("Decompressed sitemap exceeds the size limit.")
    return out


# --------------------------------------------------------------------------- robots


async def robots_for(fetcher, website, allowed):
    """Return (RobotFileParser, sitemap URLs). 404/410 = no rules; a refused robots.txt is read
    through the unlocker when configured, so the publisher's real rules still apply."""
    url = urljoin(website, "/robots.txt")
    parser = RobotFileParser(url)
    try:
        data, _, _ = await fetcher.get(url, allowed, 500_000, "text/plain")
    except httpx.HTTPStatusError as error:
        if 400 <= error.response.status_code < 500:
            parser.parse([])
            return parser, []
        raise
    lines = data.decode("utf-8", "replace").splitlines()
    parser.parse(lines)
    sitemaps = [ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")]
    return parser, sitemaps


# --------------------------------------------------------------------------- sitemaps


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_date(value):
    if not value:
        return None
    value = str(value).strip()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
        if not match:
            return None
        stamp = datetime(*map(int, match.groups()))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ISTANBUL)  # Turkish publishers' local time
    return stamp.astimezone(timezone.utc)


def parse_sitemap(data):
    """Return ('index'|'urlset', [entry dicts])."""
    root = ElementTree.fromstring(gunzip(data))
    kind = local(root.tag)
    if kind not in {"sitemapindex", "urlset"}:
        raise ValueError("Not a sitemap.")
    entries = []
    for node in root:
        fields = {}
        for child in node.iter():
            name = local(child.tag)
            if name in {"loc", "lastmod", "publication_date", "title"} and child.text:
                fields.setdefault(name, child.text.strip())
        if fields.get("loc"):
            entries.append(
                {
                    "loc": fields["loc"],
                    "lastmod": parse_date(fields.get("lastmod")),
                    "published": parse_date(fields.get("publication_date")),
                    "title": fields.get("title"),
                }
            )
    return ("index" if kind == "sitemapindex" else "urlset"), entries


def sitemap_period(url):
    """Date range a child sitemap covers, if its URL encodes one (year/month/day)."""
    params = {k.lower(): v for k, v in parse_qsl(urlsplit(url).query)}
    y, m, d = (
        params.get("year") or params.get("yil"),
        params.get("month") or params.get("ay"),
        params.get("day"),
    )
    if not (y and y.isdigit()):
        match = re.search(r"(20\d{2})[-/_]?(0[1-9]|1[0-2])(?:[-/_]?(0[1-9]|[12]\d|3[01]))?(?!\d)", url)
        if not match:
            match = re.search(r"(20\d{2})(?!\d)", urlsplit(url).path)
            if not match:
                return None
            y, m, d = match.group(1), None, None
        else:
            y, m, d = match.groups()
    y = int(y)
    if m and str(m).isdigit():
        m = int(m)
        if d and str(d).isdigit():
            start = date(y, m, int(d))
            return start, start
        last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        return date(y, m, 1), last
    return date(y, 1, 1), date(y, 12, 31)


def child_in_window(entry, start, end):
    period = sitemap_period(entry["loc"])
    if period:
        return period[0] <= end.date() and period[1] >= start.date(), True
    # Undated child: skip only when it provably stopped changing before the window.
    if entry["lastmod"] and entry["lastmod"] < start - timedelta(days=1):
        return False, False
    return True, False


async def discover(fetcher, allowed, robots, roots, start, end, cap):
    """Walk sitemaps breadth-first; return {canonical_url: hint} for in-window article URLs."""
    found, seen, queue = {}, set(), [(u, False) for u in roots]
    stats = {"sitemaps_read": 0, "sitemap_errors": 0, "sitemaps_refused": []}
    while queue and stats["sitemaps_read"] < MAX_SITEMAPS_PER_SOURCE and len(found) < cap:
        url, dated_scope = queue.pop(0)
        if url in seen or host(url) not in allowed or not robots.can_fetch(ROBOTS_AGENT, url):
            continue
        seen.add(url)
        try:
            data, _, _ = await fetcher.get(url, allowed, MAX_SITEMAP_BYTES, "application/xml, text/xml")
            kind, entries = parse_sitemap(data)
        except Blocked:
            stats["sitemaps_refused"].append(url)
            continue
        except (ValueError, httpx.HTTPError, OSError, ElementTree.ParseError):
            stats["sitemap_errors"] += 1
            continue
        stats["sitemaps_read"] += 1
        if kind == "index":
            for entry in entries:
                keep, dated = child_in_window(entry, start, end)
                if keep:
                    queue.append((entry["loc"], dated))
            continue
        for entry in entries:
            stamp = entry["published"] or entry["lastmod"]
            if stamp is None and not dated_scope:
                continue  # no date and the sitemap itself is not date-scoped
            if stamp is not None and not (start <= stamp <= end):
                continue
            loc = canonical(entry["loc"])
            if (
                host(loc) in allowed
                and urlsplit(loc).scheme == "https"
                and robots.can_fetch(ROBOTS_AGENT, loc)
            ):
                found.setdefault(loc, {"published": entry["published"], "title": entry["title"]})
            if len(found) >= cap:
                break
    return found, stats


# --------------------------------------------------------------------------- extraction


def jsonld_objects(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                stack.extend(item.get("@graph", []) if isinstance(item.get("@graph"), list) else [])
                yield item


def is_article(obj):
    kinds = obj.get("@type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    return any(str(k).lower() in ARTICLE_TYPES for k in kinds if k)


def names(value):
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def meta(soup, *keys):
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def html_body(soup):
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if not node:
            continue
        for junk in node.select(
            "script, style, iframe, aside, nav, figure, figcaption, form, .ads, .related"
        ):
            junk.decompose()
        paragraphs = [p.get_text(" ", strip=True) for p in node.find_all(["p", "h2", "h3"])]
        text = "\n\n".join(p for p in paragraphs if len(p) >= 20)
        if len(text) >= MIN_TEXT_CHARS:
            return text
    return ""


def extract(html, url):
    """Extract one article. Returns a dict; 'reason' is set when the page is not usable."""
    soup = BeautifulSoup(html, "html.parser")
    article = next((o for o in jsonld_objects(soup) if is_article(o)), {})
    link = soup.find("link", rel="canonical")
    result = {
        "title": clean(
            article.get("headline") or meta(soup, "og:title") or (soup.title.string if soup.title else "")
        ),
        "published_at": parse_date(
            article.get("datePublished") or meta(soup, "article:published_time", "pubdate")
        ),
        "authors": names(article.get("author")) or ([meta(soup, "author")] if meta(soup, "author") else []),
        "canonical": canonical(urljoin(url, link["href"])) if link and link.get("href") else canonical(url),
        "method": "json-ld",
        "reason": None,
    }
    text = clean(article.get("articleBody") or "")
    if len(text) < MIN_TEXT_CHARS:
        text, result["method"] = html_body(soup), "html"
    result["text"] = text
    free = article.get("isAccessibleForFree")
    if free is False or str(free).lower() == "false":
        result["reason"] = "paywalled"
    elif not result["title"]:
        result["reason"] = "no_title"
    elif not result["published_at"]:
        result["reason"] = "no_date"
    elif len(text) < MIN_TEXT_CHARS:
        result["reason"] = "no_body"
    return result


# --------------------------------------------------------------------------- storage


def store(session, source, item):
    """Insert or upgrade an article to full text. Returns 'inserted', 'upgraded' or 'unchanged'."""
    digest = hashlib.sha256((item["title"] + "\n" + item["text"]).encode()).hexdigest()
    existing = session.scalar(select(Article).where(Article.url.in_({item["url"], item["canonical"]})))
    if existing:
        if existing.content_hash == digest and existing.content_scope == "full_text":
            return "unchanged"
        # Existing analysis snapshots are immutable; a new hash marks them stale.
        existing.title, existing.text, existing.content_hash = item["title"], item["text"], digest
        existing.content_scope, existing.fetched_at = "full_text", now()
        existing.published_at = existing.published_at or item["published_at"]
        return "upgraded"
    duplicate = session.scalar(
        select(Article.id).where(Article.source_id == source["id"], Article.content_hash == digest)
    )
    if duplicate:
        return "unchanged"
    session.add(
        Article(
            event_id=candidate_event(session, item["title"], item["published_at"]),
            source_id=source["id"],
            source_name=source["name"],
            title=item["title"][:500],
            text=item["text"],
            url=item["canonical"],
            content_scope="full_text",
            content_hash=digest,
            published_at=item["published_at"],
        )
    )
    session.flush()
    return "inserted"


# --------------------------------------------------------------------------- orchestration


class Crawler:
    def __init__(self, settings, sessions):
        self.settings, self.sessions = settings, sessions

    def sources(self, only=None):
        register = json.loads(self.settings.sources_path.read_text())["sources"]
        wanted = set(only or self.settings.active_source_ids)
        chosen = [s for s in register if not wanted or s["id"] in wanted]
        if wanted - {s["id"] for s in chosen}:
            raise ValueError("Unknown source ID: " + ", ".join(sorted(wanted - {s["id"] for s in chosen})))
        return chosen

    async def crawl(self, start, end, only=None, max_per_source=None, client=None, sleep=asyncio.sleep):
        cap = max_per_source or self.settings.crawl_max_articles
        gate = HostGate(self.settings.crawl_delay_seconds)
        outlets = asyncio.Semaphore(self.settings.http_concurrency)
        owned = client is None
        client = client or httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
        fetcher = Fetcher(client, gate, self.settings, sleep)
        try:

            async def run(source):
                async with outlets:
                    return await self.one(fetcher, source, start, end, cap)

            return await asyncio.gather(*(run(s) for s in self.sources(only)))
        finally:
            if owned:
                await client.aclose()

    def known(self, source, start, end):
        """URLs already collected for this outlet: full-text ones to skip, excerpts to upgrade."""
        with self.sessions() as session:
            rows = session.execute(
                select(Article.url, Article.content_scope, Article.published_at).where(
                    Article.source_id == source["id"]
                )
            ).all()
        done = {u for u, scope, _ in rows if scope == "full_text"}
        excerpts = {
            u: {"published": p, "title": None}
            for u, scope, p in rows
            if scope != "full_text" and p and start <= p.astimezone(timezone.utc) <= end
        }
        return done, excerpts

    def save_state(self, key, payload):
        with self.sessions.begin() as session:
            record = session.get(FeedState, key)
            if record:
                record.payload = payload
            else:
                session.add(FeedState(url=key, payload=payload))

    def load_state(self, key):
        with self.sessions() as session:
            record = session.get(FeedState, key)
            return dict(record.payload) if record else {}

    def pending(self, source_id):
        """Refused or failed links kept across runs so none is silently lost."""
        return dict(self.load_state("crawl-pending:" + source_id).get("links", {}))

    async def one(self, fetcher, source, start, end, cap):
        website = source.get("website") or ""
        allowed = {host(website)} | {host(f["url"]) for f in source.get("feeds", [])}
        allowed.discard("")
        outcome = {
            "source_id": source["id"],
            "window": [start.isoformat(), end.isoformat()],
            "checked_at": now().isoformat(),
            "status": None,
            "candidates": 0,
            "retried_from_queue": 0,
            "inserted": 0,
            "upgraded": 0,
            "unchanged": 0,
            "via_unlocker": 0,
            "skipped": {},
            "pending_links": 0,
            "given_up_links": 0,
            "error": None,
        }
        queue_state = self.pending(source["id"])
        try:
            robots, listed = await robots_for(fetcher, website, allowed)
            delay = robots.crawl_delay(ROBOTS_AGENT)
            if delay:
                fetcher.gate.extra[host(website)] = float(delay)
            roots = listed or [urljoin(website, p) for p in FALLBACK_SITEMAPS]
            found, stats = await discover(fetcher, allowed, robots, roots, start, end, cap)
            outcome.update(stats, sitemap_roots="robots.txt" if listed else "fallback")
            done, excerpts = self.known(source, start, end)
            for url, hint in excerpts.items():
                found.setdefault(url, hint)  # RSS-collected articles get their full text too
            fresh = [u for u in found if u not in done and u not in queue_state][:cap]
            retry = [
                u for u, info in queue_state.items() if info["attempts"] < GIVE_UP_AFTER and u not in done
            ]
            outcome["candidates"], outcome["retried_from_queue"] = len(fresh), len(retry)
            work = [(u, True) for u in fresh] + [(u, False) for u in retry]
            refused_streak = 0
            for index, (url, in_window_check) in enumerate(work):
                reason, via = await self.article(
                    fetcher, source, allowed, robots, url, start, end, in_window_check
                )
                outcome["via_unlocker"] += via == "unlocker"
                if reason in {"inserted", "upgraded", "unchanged"}:
                    outcome[reason] += 1
                    queue_state.pop(url, None)
                    refused_streak = 0
                    continue
                outcome["skipped"][reason] = outcome["skipped"].get(reason, 0) + 1
                if reason in {"blocked", "fetch_error"}:
                    info = queue_state.setdefault(
                        url, {"attempts": 0, "first_seen": now().isoformat(), "published": None}
                    )
                    info.update(attempts=info["attempts"] + 1, last_reason=reason, last_try=now().isoformat())
                    hint = found.get(url, {}).get("published")
                    info["published"] = info["published"] or (hint.isoformat() if hint else None)
                    refused_streak += reason == "blocked"
                else:
                    queue_state.pop(url, None)  # terminal: paywalled, no_body, robots, outside window...
                    refused_streak = 0
                if refused_streak >= 10:
                    # Stop hammering this outlet; everything left waits in the queue for the next run.
                    for rest, _ in work[index + 1 :]:
                        queue_state.setdefault(
                            rest, {"attempts": 0, "first_seen": now().isoformat(), "published": None}
                        ).update(last_reason="deferred")
                    outcome["error"] = "Publisher refused 10 requests in a row; remaining links queued."
                    break
            outcome["status"] = (
                "access_restricted"
                if outcome["error"]
                else "ok"
                if outcome["inserted"] + outcome["upgraded"] + outcome["unchanged"]
                else "no_usable_articles"
            )
        except Exception as error:  # keep other outlets running; never store raw bodies
            outcome.update(status="failed", error=type(error).__name__)
        outcome["pending_links"] = sum(i["attempts"] < GIVE_UP_AFTER for i in queue_state.values())
        outcome["given_up_links"] = len(queue_state) - outcome["pending_links"]
        self.save_state("crawl-pending:" + source["id"], {"links": queue_state})
        self.save_state("crawl:" + source["id"], outcome)
        return outcome

    async def article(self, fetcher, source, allowed, robots, url, start, end, check_window=True):
        if not robots.can_fetch(ROBOTS_AGENT, url):
            return "robots_disallowed", None
        try:
            data, final, via = await fetcher.get(url, allowed, MAX_PAGE_BYTES, "text/html")
        except Blocked:
            return "blocked", None
        except (ValueError, httpx.HTTPError, OSError):
            return "fetch_error", None
        item = extract(data.decode("utf-8", "replace"), final)
        if item["reason"]:
            return item["reason"], via
        if host(item["canonical"]) not in allowed:
            item["canonical"] = canonical(final)
        published = item["published_at"]
        if check_window and not (start - timedelta(days=1) <= published <= end + timedelta(days=1)):
            return "outside_window", via
        item["url"] = url
        with self.sessions.begin() as session:
            return store(session, source, item), via

    def report(self):
        """Rows for every link not yet collected after refusal or failure, across all outlets."""
        with self.sessions() as session:
            states = session.scalars(select(FeedState).where(FeedState.url.like("crawl-pending:%"))).all()
            rows = []
            for state in states:
                for url, info in state.payload.get("links", {}).items():
                    rows.append(
                        {
                            "source_id": state.url.split(":", 1)[1],
                            "url": url,
                            "status": "given_up" if info["attempts"] >= GIVE_UP_AFTER else "pending",
                            "attempts": info["attempts"],
                            "last_reason": info.get("last_reason"),
                            "published": info.get("published"),
                            "first_seen": info.get("first_seen"),
                            "last_try": info.get("last_try"),
                        }
                    )
        return sorted(rows, key=lambda r: (r["source_id"], r["url"]))


def window(since, until):
    start = datetime.combine(since, dtime.min, ISTANBUL).astimezone(timezone.utc)
    end = datetime.combine(until, dtime.max, ISTANBUL).astimezone(timezone.utc) if until else now()
    if end < start:
        raise ValueError("--until is before --since.")
    return start, end

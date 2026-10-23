# Architecture and operating limits

## Data flow

A dated source register feeds a bounded RSS/Atom collector. Usable dated excerpts are canonicalized and assigned to tentative events using title overlap within 48 hours. An editor checks grouping, moves incorrect matches, or creates an event from supplied text. Analysis takes immutable text snapshots, masks publisher metadata, calls generation and verification, checks every quote, and saves a version. Human review and editorial approval append separate records.

The event comparison is grounded in supplied reporting. It is not an independently fact-checked reconstruction. Cross-outlet repetition can share a wire-service origin; count distinct outlets as coverage, not independent corroboration. Wire duplicates are retained across outlets to make comparisons inspectable.

## Components

| Component | Initial implementation | Scaling boundary |
|---|---|---|
| API | FastAPI, one process, bounded inference | Add distributed request deduplication and job queue before multiple API workers |
| Database | SQLite WAL locally, SQLAlchemy; PostgreSQL driver extra | Back up data; use PostgreSQL before concurrent distributed writers |
| Collection | One collector, async network concurrency 5, sequential feeds per outlet | Distributed leases and per-domain quotas needed before multiple collectors |
| Grouping | Conservative title-token Jaccard candidate grouping | Replace or supplement with evaluated Turkish entity/time embeddings |
| Inference | OpenAI-compatible HTTPS JSON adapter, two passes | Provider quotas, cost budgets, cancellation and queue observability need deployment-specific configuration |
| UI | Same-origin static HTML/CSS/JavaScript | No third-party assets or frontend framework build required |
| Review | Append-only annotations and editor decisions | Account provisioning is operator-managed tokens; enterprise SSO is not implemented |

The code is modular and PostgreSQL-compatible, but this is not a distributed production deployment. PostgreSQL behavior is not integration-tested in this workspace. Use a staged database before switching a live installation.

## Feed policy

Only operator-configured HTTPS feed URLs are fetched, using ordinary public requests. Redirects are limited and checked against configured publisher/feed hosts. Private/reserved IP resolutions, credentials, nonstandard ports, oversized decoded responses and malformed XML are rejected. HTML error/challenge pages are not accepted as feeds. There is no browser fallback, credential use, paywall bypass or arbitrary article URL fetching: article pages are requested only by the full-text crawler, only on a registered outlet's own hosts, and only when listed in that outlet's sitemaps or already collected from its feeds.

ETag and Last-Modified are recorded for conditional requests. Each endpoint records checked time, HTTP status when available, parsing/usable counts, error class and last success. `unchanged` records an HTTP 304, not a new article. Stale, missing-date, future-dated and foreign-link items are excluded. A run reads at most 100 entries and ingests at most 12 usable items per endpoint; these throughput limits can miss a busy feed's older items. The RSS collector does not backfill archives; the full-text crawler below does.

Source IDs and all feed hostnames come from the supplied register. Cross-domain content delivery requires explicit operator verification and a registry change. The original spreadsheet’s `canlı` observations are provisional enablement, not a newly verified official-feed audit.

## Full-text crawler

`mediabias crawl` (module `articles.py`) works from every outlet in the register, with or without feeds. Per outlet it reads robots.txt; follows the `Sitemap:` lines or, if none are listed, tries common sitemap paths; walks sitemap indexes, skipping child sitemaps whose URL dates (for example `?year=2026&month=6`, `/2026/06/`) or `lastmod` fall outside the window; and keeps article URLs on the outlet's registered hosts whose publication date or lastmod is in the window. Undated entries are kept only from date-scoped sitemaps, and the page's own date then decides.

Article text comes from schema.org `NewsArticle`/`Article` JSON-LD (`articleBody`, `headline`, `datePublished`, `author`), falling back to Open Graph/`article:published_time` metadata and paragraphs inside the article container. Naive timestamps are read as Istanbul time (UTC+3). Pages marked `isAccessibleForFree: false`, without a title, date or 300+ characters of body, or outside the window by more than a day are skipped with a counted reason. Refusals are handled so that no link is silently lost:

- 429 and 5xx responses are retried up to `MEDIABIAS_CRAWL_RETRIES` times (default 3), waiting out `Retry-After` (capped at 120 s) or backing off 5/20/60 s.
- 401/403/451, a persistent 429, and bot-challenge pages (detected by markers such as "Just a moment", Cloudflare/Incapsula/DataDome scripts, even when served with HTTP 200) count as refusals. After a refusal, that host's request spacing doubles, up to 30 s, for the rest of the run.
- If `MEDIABIAS_BRIGHTDATA_API_KEY` and `MEDIABIAS_BRIGHTDATA_ZONE` (or `BRIGHTDATA_API_KEY` / `BRIGHTDATA_UNLOCKER_ZONE`) are set, a refused URL is requested once more through Bright Data Web Unlocker with Turkish exit IPs. The same host allowlist and robots.txt rules apply. A refused robots.txt is itself read through the unlocker, so the publisher's real rules are obeyed. Pages marked `isAccessibleForFree: false` are skipped and are never sent to the unlocker. Without these settings, nothing is sent to Bright Data.
- Links still refused or failing are kept in a per-outlet retry queue (`crawl-pending:<source_id>` in the feed-state table) and retried on every later run, including `--follow` runs. Links outside the current window are retried too. After 10 attempts a link is marked `given_up` but stays listed. `mediabias crawl-report --output pending.csv` lists every waiting or given-up link.
- Ten consecutive refusals stop that outlet for the run (`access_restricted`); the remaining links go into the queue rather than being dropped. Robots rules and Crawl-delay are honoured; one request at a time per host with a minimum delay. Sitemaps are capped at 300 per outlet and 50 MB decompressed; pages at 3 MB.

Stored rows use `content_scope = "full_text"`. An existing feed excerpt with the same URL is upgraded in place; its hash changes, so earlier analyses of the excerpt show as stale rather than being altered. Selectors cannot fit every template: check each outlet's `crawl:<id>` summary after the first run (`no_body` counts point to an outlet that needs a selector added to `BODY_SELECTORS`).

## Versioning

Article text may update on a repeated URL. Its hash changes; existing analysis snapshots remain untouched. Event detail marks results stale when IDs/hashes differ, including after article reassignment. Cache fingerprints include snapshots, taxonomy hash, prompt version, sensitivity, endpoint and both model IDs. Publication timestamps are not invented from fetch time.

SQLite demo startup creates fresh demo tables automatically. Live setup explicitly runs `mediabias migrate`; Alembic revision 0001 contains fixed table definitions. New migrations must preserve old analysis/review history. Back up before a schema upgrade; destructive downgrades are for disposable development databases only.

## Operator procedure

1. Create the virtual environment, install constrained dependencies, configure secrets outside Git.
2. Start with demo mode and inspect the five-question comparison.
3. Enable live mode with distinct reviewer/editor tokens; run migrations.
4. Select source IDs and run one feed check. Review statuses, dates and item counts.
5. Check event membership and excerpt scope before analysis.
6. Configure a provider and model; inspect cost/context limits before sending publisher content.
7. Collect human review and independent editorial decisions. Export only approved records.
8. Back up the database and private exports. Use a separate staging database for upgrades.

A running periodic collector is an operating-system process; this repository does not create a hosted scheduler. In worker mode the API collection mutation is disabled. Do not run two CLI collectors against the same database.

## Docker

Build with `docker build -t mediabias .`. The image runs as an unprivileged user, binds to localhost from the container by default, and requires explicit authenticated configuration for `--host 0.0.0.0`. Mount `/app/var` for SQLite persistence and pass configuration through an env file. A reverse proxy supplies TLS. No public deployment is performed by this repository.

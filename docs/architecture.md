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

Only operator-configured HTTPS feed URLs are fetched, using ordinary public requests. Redirects are limited and checked against configured publisher/feed hosts. Private/reserved IP resolutions, credentials, nonstandard ports, oversized decoded responses and malformed XML are rejected. HTML error/challenge pages are not accepted as feeds. There is no browser fallback, credential use, paywall bypass or arbitrary article URL fetching.

ETag and Last-Modified are recorded for conditional requests. Each endpoint records checked time, HTTP status when available, parsing/usable counts, error class and last success. `unchanged` records an HTTP 304, not a new article. Stale, missing-date, future-dated and foreign-link items are excluded. A run reads at most 100 entries and ingests at most 12 usable items per endpoint; these throughput limits can miss a busy feed's older items. The initial version does not backfill archives.

Source IDs and all feed hostnames come from the supplied register. Cross-domain content delivery requires explicit operator verification and a registry change. The original spreadsheet’s `canlı` observations are provisional enablement, not a newly verified official-feed audit.

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

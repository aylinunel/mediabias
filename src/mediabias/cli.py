import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from .config import Settings
from .db import database


def migrate(settings):
    from alembic import command
    from alembic.config import Config

    engine, _ = database(settings.database_url)  # ensure SQLite parent exists
    engine.dispose()
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


async def collect(settings, once):
    from .feeds import Collector

    engine, sessions = database(settings.database_url)
    collector = Collector(settings, sessions)
    try:
        while True:
            results = await collector.collect()
            print(json.dumps(results, ensure_ascii=False), flush=True)
            if once:
                break
            await asyncio.sleep(settings.poll_seconds)
    finally:
        engine.dispose()


async def crawl(settings, since, until, sources, max_per_source, follow):
    from datetime import timedelta

    from .articles import Crawler, window
    from .db import now

    engine, sessions = database(settings.database_url)
    crawler = Crawler(settings, sessions)
    try:
        if since:
            start, end = window(since, until)
            results = await crawler.crawl(start, end, sources, max_per_source)
            print(json.dumps(results, ensure_ascii=False), flush=True)
        while follow:
            end = now()
            results = await crawler.crawl(end - timedelta(hours=settings.crawl_follow_hours), end, sources)
            print(json.dumps(results, ensure_ascii=False), flush=True)
            await asyncio.sleep(settings.poll_seconds)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Turkish news evidence workspace")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("migrate")
    worker = sub.add_parser("collect")
    worker.add_argument("--once", action="store_true")
    crawler = sub.add_parser("crawl", help="Full-text articles from every registered outlet's sitemaps")
    crawler.add_argument(
        "--since", type=date.fromisoformat, help="Backfill start, YYYY-MM-DD (Istanbul time)"
    )
    crawler.add_argument("--until", type=date.fromisoformat, help="Backfill end, inclusive; default now")
    crawler.add_argument(
        "--sources", type=lambda v: [s for s in v.split(",") if s], help="Comma-separated IDs"
    )
    crawler.add_argument("--max-per-source", type=int)
    crawler.add_argument("--follow", action="store_true", help="Then keep collecting a rolling recent window")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--cases", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "serve":
        import uvicorn

        if args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.users:
            parser.error("Non-loopback serving requires MEDIABIAS_USERS.")
        uvicorn.run("mediabias.api:create_app", factory=True, host=args.host, port=args.port)
    elif args.command == "migrate":
        migrate(settings)
    elif args.command == "collect":
        if settings.mode != "live":
            parser.error("Collection requires live mode; use migrate first.")
        if settings.collector_mode != "worker":
            parser.error("CLI collection requires MEDIABIAS_COLLECTOR_MODE=worker.")
        asyncio.run(collect(settings, args.once))
    elif args.command == "crawl":
        if settings.mode != "live":
            parser.error("Crawling requires live mode; use migrate first.")
        if not (args.since or args.follow):
            parser.error("Give --since for a backfill, --follow for ongoing collection, or both.")
        asyncio.run(crawl(settings, args.since, args.until, args.sources, args.max_per_source, args.follow))
    else:
        from .evaluation import evaluate

        asyncio.run(evaluate(settings, args.cases, args.output))


if __name__ == "__main__":
    main()

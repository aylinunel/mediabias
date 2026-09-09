"""Append-only analyses and review decisions; replaceable ingestion state."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class NewsEvent(Base):
    __tablename__ = 'events'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(default=False)
    grouping: Mapped[str] = mapped_column(String(40), default='manual')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Article(Base):
    __tablename__ = 'articles'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey('events.id'), index=True)
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    source_name: Mapped[str] = mapped_column(String(150))
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, unique=True)
    content_scope: Mapped[str] = mapped_column(String(30))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Analysis(Base):
    __tablename__ = 'analyses'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey('events.id'), index=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    snapshots: Mapped[list] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Review(Base):
    __tablename__ = 'reviews'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    analysis_id: Mapped[str] = mapped_column(ForeignKey('analyses.id'), index=True)
    finding_id: Mapped[str] = mapped_column(String(80), index=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Decision(Base):
    __tablename__ = 'decisions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    review_id: Mapped[str] = mapped_column(ForeignKey('reviews.id'), index=True)
    editor: Mapped[str] = mapped_column(String(100))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FeedState(Base):
    __tablename__ = 'feed_states'
    url: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


def database(url):
    if url.startswith('sqlite:///') and ':memory:' not in url:
        Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True, exist_ok=True)
    options = {'connect_args': {'check_same_thread': False}} if url.startswith('sqlite') else {}
    if ':memory:' in url:
        from sqlalchemy.pool import StaticPool
        options['poolclass'] = StaticPool
    engine = create_engine(url, **options)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def configure(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=5000')
            connection.execute('PRAGMA journal_mode=WAL')
    return engine, sessionmaker(engine, expire_on_commit=False)


def snapshot(article, limit):
    # Offsets always refer to this immutable analyzed text, including its title.
    text = (article.title + '\n' + article.text)[:limit]
    return {'id': article.id, 'source_id': article.source_id, 'source_name': article.source_name,
            'title': article.title, 'text': text, 'url': article.url,
            'content_hash': article.content_hash, 'content_scope': article.content_scope,
            'truncated': len(article.title + '\n' + article.text) > limit}

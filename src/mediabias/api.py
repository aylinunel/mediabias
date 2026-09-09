import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .config import Settings
from .db import Analysis, Article, Base, Decision, FeedState, NewsEvent, Review, database, snapshot, uid
from .demo import seed
from .evidence import TAXONOMY, TAXONOMY_HASH
from .feeds import Collector
from .model import PROMPT_VERSION, Analyzer, fingerprint
from .reviews import export_rows, validate_review
from .schema import AnalyzeInput, DecisionInput, EventInput, MoveInput, ReviewInput

STATIC = Path(__file__).parent / 'static'


def create_app(settings=None):
    settings = settings or Settings()
    engine, sessions = database(settings.database_url)
    analyzer, collector = Analyzer(settings), Collector(settings, sessions)
    analysis_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if settings.mode == 'demo':
            Base.metadata.create_all(engine)
            seed(sessions, settings.max_article_chars)
        yield
        engine.dispose()

    app = FastAPI(title='MediaBias · Türkçe haber kanıtı', version='0.1.0', lifespan=lifespan)
    app.state.sessions, app.state.analyzer, app.state.collector = sessions, analyzer, collector

    @app.middleware('http')
    async def protect(request, call_next):
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin writes are not permitted.'}, status_code=403)
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 3_000_000:
                    return JSONResponse({'detail': 'Request exceeds 3 MB.'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            'Cache-Control': 'no-store'})
        return response

    def identity(request: Request):
        if settings.mode == 'demo' and not settings.users:
            return {'id': 'demo-editor', 'role': 'editor'}
        supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
        for token, user in settings.users.items():
            if secrets.compare_digest(supplied, token):
                return user
        raise HTTPException(401, 'Geçerli erişim anahtarı gerekli.')

    def editor(user=Depends(identity)):
        if user['role'] != 'editor':
            raise HTTPException(403, 'Editör yetkisi gerekli.')
        return user

    def required(session, model, identifier):
        item = session.get(model, identifier)
        if item is None:
            raise HTTPException(404, 'Kayıt bulunamadı.')
        return item

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/api/config')
    def config():
        return {'mode': settings.mode, 'auth_required': bool(settings.users),
                'model_configured': bool(settings.llm_model and settings.llm_api_key),
                'max_event_articles': settings.max_event_articles}

    @app.get('/api/me')
    def me(user=Depends(identity)):
        return user

    @app.get('/api/taxonomy', dependencies=[Depends(identity)])
    def taxonomy():
        return TAXONOMY

    @app.get('/api/sources', dependencies=[Depends(identity)])
    def sources():
        register = collector.register()
        with sessions() as session:
            states = {s.url: s.payload for s in session.scalars(select(FeedState))}
        selected = set(settings.active_source_ids)
        for source in register['sources']:
            for feed in source['feeds']:
                feed['selected'] = feed['enabled'] and (not selected or source['id'] in selected)
                feed['runtime'] = states.get(feed['url'])
        return register

    @app.get('/api/events', dependencies=[Depends(identity)])
    def events():
        with sessions() as session:
            result = session.execute(select(NewsEvent, func.count(Article.id), func.count(func.distinct(Article.source_id)))
                .outerjoin(Article, Article.event_id == NewsEvent.id).group_by(NewsEvent.id)
                .order_by(NewsEvent.created_at.desc()).limit(200)).all()
            return [{'id': e.id, 'title': e.title, 'is_demo': e.is_demo, 'grouping': e.grouping,
                     'article_count': count, 'source_count': sources} for e, count, sources in result]

    @app.get('/api/events/{event_id}', dependencies=[Depends(identity)])
    def event_detail(event_id: str):
        with sessions() as session:
            e = required(session, NewsEvent, event_id)
            articles = session.scalars(select(Article).where(Article.event_id == event_id).order_by(Article.id)).all()
            analysis = session.scalar(select(Analysis).where(Analysis.event_id == event_id).order_by(Analysis.created_at.desc()).limit(1))
            snapshots = [snapshot(a, settings.max_article_chars) for a in articles]
            stale = bool(analysis and {(a['id'], a['content_hash']) for a in analysis.snapshots} != {(a['id'], a['content_hash']) for a in snapshots})
            return {'id': e.id, 'title': e.title, 'is_demo': e.is_demo, 'grouping': e.grouping, 'articles': snapshots,
                    'analysis': {'id': analysis.id, 'payload': analysis.payload, 'snapshots': analysis.snapshots,
                                 'provenance': analysis.provenance, 'stale': stale} if analysis else None}

    @app.post('/api/events', dependencies=[Depends(editor)], status_code=201)
    def add_event(body: EventInput):
        if len({a.source_id for a in body.articles}) < 2:
            raise HTTPException(422, 'En az iki farklı kaynak gerekli.')
        with sessions.begin() as session:
            e = NewsEvent(title=body.title, is_demo=settings.mode == 'demo', grouping='manual')
            session.add(e)
            session.flush()
            for a in body.articles:
                values = a.model_dump()
                # URLs are metadata only. Manual import never fetches a supplied link.
                if values['url'] and not values['url'].startswith('https://'):
                    raise HTTPException(422, 'Kaynak bağlantısı HTTPS olmalı.')
                values['url'] = values['url'] or 'https://example.invalid/manual/' + uid()
                if session.scalar(select(Article.id).where(Article.url == values['url'])):
                    raise HTTPException(409, 'Bu URL zaten kayıtlı; olay taşıma işlemini kullanın.')
                session.add(Article(event_id=e.id, content_hash=hashlib.sha256((a.title+'\n'+a.text).encode()).hexdigest(), **values))
                session.flush()
            return {'id': e.id}

    @app.post('/api/articles/{article_id}/move', dependencies=[Depends(editor)])
    def move(article_id: str, body: MoveInput):
        with sessions.begin() as session:
            a = required(session, Article, article_id)
            target = required(session, NewsEvent, body.event_id)
            old = required(session, NewsEvent, a.event_id)
            if target.is_demo != old.is_demo:
                raise HTTPException(409, 'Kurgu ve gerçek olaylar birleştirilemez.')
            a.event_id = target.id
            target.grouping = 'editor_reviewed'
            old.grouping = 'editor_reviewed'
        return {'moved': True}

    @app.post('/api/events/{event_id}/analyze', dependencies=[Depends(editor)])
    async def analyze(event_id: str, body: AnalyzeInput):
        # Serializes duplicate writes within a worker; deploy one API writer in the initial release.
        async with analysis_lock:
            with sessions() as session:
                e = required(session, NewsEvent, event_id)
                if e.is_demo:
                    existing = session.scalar(select(Analysis).where(Analysis.event_id == event_id).order_by(Analysis.created_at.desc()))
                    if existing:
                        return {'id': existing.id, 'cached': True, 'demo': True}
                    raise HTTPException(409, 'Model çalıştırmak için canlı moda geçin.')
                articles = session.scalars(select(Article).where(Article.event_id == event_id).order_by(Article.id)).all()
                if len({a.source_id for a in articles}) < 2:
                    raise HTTPException(422, 'Karşılaştırma için en az iki kaynak gerekli.')
                if len(articles) > settings.max_event_articles:
                    raise HTTPException(422, 'Olaydaki metin sayısı sınırı aşıyor; olayı daraltın veya sınırı yapılandırın.')
                snapshots = [snapshot(a, settings.max_article_chars) for a in articles]
                key = fingerprint(snapshots, settings, body.sensitivity)
                existing = session.scalar(select(Analysis).where(Analysis.event_id == event_id, Analysis.cache_key == key))
                if existing and not body.force:
                    return {'id': existing.id, 'cached': True}
            try:
                payload = await analyzer.analyze(snapshots, body.sensitivity)
            except Exception as error:
                # No raw provider response, credentials, or article text in public errors.
                raise HTTPException(502, 'Model analizi tamamlanamadı veya kanıt doğrulaması başarısız. Yapılandırmayı ve sağlayıcı durumunu kontrol edin.') from error
            with sessions.begin() as session:
                current = session.scalars(select(Article).where(Article.event_id == event_id)).all()
                if {(a.id, a.content_hash) for a in current} != {(a['id'], a['content_hash']) for a in snapshots}:
                    raise HTTPException(409, 'Analiz sırasında haberler değişti; yeniden deneyin.')
                record = Analysis(event_id=event_id, cache_key=key, payload=payload, snapshots=snapshots,
                    provenance={'mode': 'live', 'model': settings.llm_model,
                    'verifier_model': settings.verifier_model or settings.llm_model, 'prompt_version': PROMPT_VERSION,
                    'taxonomy_hash': TAXONOMY_HASH, 'sensitivity': body.sensitivity})
                session.add(record)
                session.flush()
                return {'id': record.id, 'cached': False}

    @app.post('/api/collect', dependencies=[Depends(editor)])
    async def collect():
        if settings.mode != 'live':
            raise HTTPException(409, 'Canlı toplama demo modunda kapalıdır.')
        try:
            return {'feeds': await collector.collect()}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get('/api/analyses/{analysis_id}/reviews', dependencies=[Depends(identity)])
    def reviews(analysis_id: str):
        with sessions() as session:
            required(session, Analysis, analysis_id)
            rows = session.scalars(select(Review).where(Review.analysis_id == analysis_id).order_by(Review.created_at)).all()
            decisions = session.execute(select(Decision, Review).join(Review, Decision.review_id == Review.id).where(Review.analysis_id == analysis_id)).all()
            return {'reviews': [{'id': r.id, 'reviewer': r.reviewer, 'payload': r.payload, 'created_at': r.created_at.isoformat()} for r in rows],
                'decisions': [{'id': d.id, 'review_id': d.review_id, 'editor': d.editor, 'notes': d.notes} for d, _ in decisions]}

    @app.post('/api/analyses/{analysis_id}/reviews', status_code=201)
    def review(analysis_id: str, body: ReviewInput, user=Depends(identity)):
        with sessions.begin() as session:
            analysis = required(session, Analysis, analysis_id)
            try:
                payload = validate_review(body, analysis)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            r = Review(analysis_id=analysis_id, finding_id=body.finding_id, reviewer=user['id'], payload=payload)
            session.add(r)
            session.flush()
            return {'id': r.id}

    @app.post('/api/analyses/{analysis_id}/decisions', status_code=201)
    def decide(analysis_id: str, body: DecisionInput, user=Depends(editor)):
        with sessions.begin() as session:
            r = required(session, Review, body.review_id)
            if r.analysis_id != analysis_id:
                raise HTTPException(422, 'İnceleme bu analize ait değil.')
            if r.reviewer == user['id']:
                raise HTTPException(409, 'Son kararı farklı bir editör vermeli.')
            d = Decision(review_id=r.id, editor=user['id'], notes=body.notes)
            session.add(d)
            session.flush()
            return {'id': d.id}

    @app.get('/api/export', dependencies=[Depends(editor)])
    def export():
        with sessions() as session:
            # Materialize inside transaction to avoid closing the session before streaming.
            content = ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in export_rows(session))
        return Response(content, media_type='application/x-ndjson', headers={'Content-Disposition': 'attachment; filename="reviewed_annotations.jsonl"'})

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/')
    def home():
        return FileResponse(STATIC / 'index.html')

    return app

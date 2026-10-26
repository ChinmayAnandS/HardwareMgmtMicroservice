import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlparse
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pymongo.errors import PyMongoError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .auth import actor, digest, install_sessions
from .config import Settings
from .models import Allocate, Adjust, Checkout, Correction, Create, Edit, Identifier, Login, Retire, Return
from .store import Store, clean, now

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('hardware')


def create_app(settings=None):
    cfg = settings or Settings()
    cfg.check()
    store = Store(cfg)

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.client.close()

    app = FastAPI(title='Hardware Management', version='1.0.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store = cfg, store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlparse(cfg.origin).hostname,
                      'api', 'localhost', '127.0.0.1'] + ([] if cfg.environment == 'production' else ['testserver']))
    install_sessions(app)
    oauth = OAuth()
    if cfg.issuer:
        oauth.register('entra', client_id=cfg.client_id, client_secret=cfg.client_secret,
                       server_metadata_url=cfg.issuer.rstrip('/') + '/.well-known/openid-configuration',
                       client_kwargs={'scope': 'openid profile', 'code_challenge_method': 'S256'})

    @app.exception_handler(PyMongoError)
    async def db_error(request, exc):
        log.error('Database operation failed: %s', type(exc).__name__)
        return JSONResponse({'detail': 'Storage temporarily unavailable; retry with the same idempotency key'}, 503)

    @app.middleware('http')
    async def security(request, call_next):
        if int(request.headers.get('content-length', '0')) > 16384:
            return JSONResponse({'detail': 'Request too large'}, 413)
        try:
            response = await call_next(request)
        except PyMongoError as exc:
            log.error('Session storage failed: %s', type(exc).__name__)
            response = JSONResponse({'detail': 'Storage temporarily unavailable'}, 503)
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
                                 'Referrer-Policy': 'no-referrer'})
        return response

    @app.get('/health', tags=['Operations'])
    def health():
        return {'status': 'alive'}

    @app.get('/ready', tags=['Operations'])
    def ready():
        hello = store.client.admin.command('hello')
        if not (hello.get('setName') or hello.get('msg') == 'isdbgrid'):
            raise HTTPException(503, 'Transactional replica set required')
        return {'status': 'ready'}

    @app.get('/auth/login', tags=['Authentication'])
    async def login(request: Request):
        if not cfg.issuer:
            return RedirectResponse('/?local=1', 303)
        return await oauth.entra.authorize_redirect(request, cfg.origin + '/auth/callback')

    @app.get('/auth/callback', tags=['Authentication'])
    async def callback(request: Request):
        if not cfg.issuer:
            raise HTTPException(404)
        try:
            token = await oauth.entra.authorize_access_token(request)
            info = token['userinfo']
            if info['iss'] != cfg.issuer or not set(info.get('roles', [])).intersection({'Hardware.Admin', 'Hardware.Auditor'}):
                raise ValueError('Unauthorized issuer or role')
            request.session.clear()
            request.session.update(user={'sub': info['iss'] + ':' + info['sub'],
                                         'name': info.get('name', info['sub']), 'roles': info['roles']},
                                   csrf=secrets.token_urlsafe(32))
            request.scope['rotate_session'] = True
            return RedirectResponse('/', 303)
        except Exception:
            request.session.clear()
            log.warning('OIDC callback rejected')
            raise HTTPException(401, 'Identity verification failed') from None

    @app.post('/auth/dev', tags=['Authentication'])
    def dev_login(data: Login, request: Request):
        if cfg.environment == 'production' or not cfg.dev_code:
            raise HTTPException(404)
        if request.headers.get('origin') != cfg.origin:
            raise HTTPException(403, 'Invalid origin')
        # Shared rate bucket across workers/replicas. No code is logged or stored here.
        bucket = digest(str(request.client.host) + ':' + str(int(now().timestamp()) // 300))
        from datetime import timedelta
        from pymongo import ReturnDocument
        limit = store.db.limits.find_one_and_update({'_id': bucket},
                  {'$inc': {'attempts': 1}, '$setOnInsert': {'expires_at': now() + timedelta(minutes=10)}},
                  upsert=True, return_document=ReturnDocument.AFTER)
        if limit['attempts'] > 5:
            raise HTTPException(429, 'Try again in five minutes')
        if not secrets.compare_digest(data.code, cfg.dev_code):
            raise HTTPException(401, 'Invalid development code')
        request.session.clear()
        request.session.update(user={'sub': 'local:admin', 'name': 'Local administrator',
                                     'roles': ['Hardware.Admin']}, csrf=secrets.token_urlsafe(32))
        request.scope['rotate_session'] = True
        return {'status': 'signed_in'}

    @app.post('/auth/logout', tags=['Authentication'])
    def logout(request: Request, user=Depends(actor)):
        request.session.clear()
        return {'status': 'signed_out'}

    @app.get('/api/me', tags=['Authentication'])
    def me(request: Request, user=Depends(actor)):
        return dict(request.session['user'], csrf=request.session['csrf'])

    @app.get('/api/openapi.json', tags=['Operations'])
    def schema(user=Depends(actor)):
        return app.openapi()

    Key = Annotated[str, Header(alias='Idempotency-Key', min_length=16, max_length=100,
                               pattern=r'^[A-Za-z0-9_-]+$')]
    Entity = Annotated[str, Query(min_length=1, max_length=300)]

    def run(command, entity, data, key, user):
        return store.mutate(user, key, command, entity, data)

    @app.post('/api/hardware', tags=['Hardware'], status_code=201)
    def create(data: Create, key: Key, user=Depends(actor)):
        return run('create', '', data.model_dump(), key, user)

    @app.get('/api/hardware', tags=['Hardware'])
    def hardware(q: str = Query('', max_length=160), status: Literal['active', 'damaged', 'under_maintenance', 'retired'] | None = None,
                 category: str | None = Query(None, max_length=160),
                 sort: Literal['name_key', 'asset_key', 'status', 'total'] = 'name_key',
                 direction: Literal['asc', 'desc'] = 'asc', page: int = Query(1, ge=1, le=10000),
                 size: int = Query(25, ge=1, le=100), user=Depends(actor)):
        query = {}
        if q.strip():
            prefix = '^' + re.escape(q.strip().casefold())
            query['$or'] = [{'name_key': {'$regex': prefix}}, {'asset_key': {'$regex': prefix}}]
        if status:
            query['status'] = status
        if category:
            query['category'] = category
        return listing('hardware', query, page, size, [(sort, 1 if direction == 'asc' else -1), ('_id', 1 if direction == 'asc' else -1)])

    def listing(collection, query, page, size, sort=None):
        offset = (page - 1) * size
        if offset > 20000:
            raise HTTPException(422, 'Narrow filters; maximum offset is 20000')
        cursor = store.db[collection].find(query).sort(sort or [('_id', 1)]).skip(offset).limit(size).max_time_ms(5000)
        return clean({'items': list(cursor), 'total': store.db[collection].count_documents(query, maxTimeMS=5000),
                      'page': page, 'size': size})

    @app.get('/api/hardware/{hardware_id}', tags=['Hardware'])
    def detail(hardware_id: Identifier, user=Depends(actor)):
        h = store.get('hardware', hardware_id)
        return clean(dict(h, available=h['total']-h['out'], unallocated=h['total']-h['reserved']))

    @app.patch('/api/hardware/{hardware_id}', tags=['Hardware'])
    def edit(hardware_id: Identifier, data: Edit, key: Key, user=Depends(actor)):
        return run('edit', hardware_id, data.model_dump(exclude_none=True), key, user)

    @app.post('/api/hardware/{hardware_id}/retire', tags=['Hardware'])
    def retire(hardware_id: Identifier, data: Retire, key: Key, user=Depends(actor)):
        return run('retire', hardware_id, data.model_dump(), key, user)

    @app.post('/api/hardware/{hardware_id}/adjustments', tags=['Inventory'])
    def adjust(hardware_id: Identifier, data: Adjust, key: Key, user=Depends(actor)):
        if data.delta == 0:
            raise HTTPException(422, 'Delta must be nonzero')
        return run('adjust', hardware_id, data.model_dump(), key, user)

    @app.put('/api/hardware/{hardware_id}/allocations/{project_id}', tags=['Projects'])
    def allocate(hardware_id: Identifier, project_id: Identifier, data: Allocate, key: Key, user=Depends(actor)):
        return run('allocate', hardware_id, dict(data.model_dump(), project_id=project_id), key, user)

    @app.get('/api/allocations', tags=['Projects'])
    def allocations(project_id: Identifier | None = None, hardware_id: Identifier | None = None,
                    page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100), user=Depends(actor)):
        query = {k: v for k, v in {'project_id': project_id, 'hardware_id': hardware_id}.items() if v}
        return listing('allocations', query, page, size)

    @app.post('/api/checkouts', tags=['Checkouts'], status_code=201)
    def checkout(data: Checkout, key: Key, user=Depends(actor)):
        return run('checkout', data.hardware_id, data.model_dump(), key, user)

    @app.get('/api/checkouts', tags=['Checkouts'])
    def checkouts(project_id: Identifier | None = None, hardware_id: Identifier | None = None,
                  user_id: Identifier | None = None, outstanding: bool = True,
                  page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100), user=Depends(actor)):
        query = {k: v for k, v in {'project_id': project_id, 'hardware_id': hardware_id, 'user_id': user_id}.items() if v}
        if outstanding:
            query['$expr'] = {'$lt': ['$returned', '$quantity']}
        return listing('checkouts', query, page, size, [('created_at', -1), ('_id', -1)])

    @app.post('/api/checkouts/{checkout_id}/returns', tags=['Checkouts'])
    def checkin(checkout_id: Identifier, data: Return, key: Key, user=Depends(actor)):
        return run('return', checkout_id, data.model_dump(), key, user)

    @app.post('/api/checkouts/{checkout_id}/corrections', tags=['Checkouts'])
    def correct(checkout_id: Identifier, data: Correction, key: Key, user=Depends(actor)):
        return run('correct', checkout_id, data.model_dump(), key, user)

    @app.get('/api/transactions', tags=['Audit'])
    def transactions(project_id: Identifier | None = None, hardware_id: Identifier | None = None,
                     actor_id: Entity | None = None, user_id: Identifier | None = None, operation: str | None = Query(None, max_length=30),
                     start: datetime | None = None, end: datetime | None = None,
                     page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100), user=Depends(actor)):
        query = {k: v for k, v in {'project_id': project_id, 'hardware_id': hardware_id,
                                  'actor': actor_id, 'user_id': user_id, 'operation': operation}.items() if v}
        if any(x and x.tzinfo is None for x in (start, end)):
            raise HTTPException(422, 'Use timezone-aware timestamps')
        if start and end and start > end:
            raise HTTPException(422, 'Start must precede end')
        if start or end:
            query['timestamp'] = {k: v for k, v in {'$gte': start, '$lte': end}.items() if v}
        return listing('audit', query, page, size, [('timestamp', -1), ('_id', -1)])

    @app.get('/api/summary', tags=['Inventory'])
    def summary(user=Depends(actor)):
        # One snapshot across both collections gives consistent dashboard totals.
        with store.client.start_session() as s:
            from pymongo.read_concern import ReadConcern
            def read(session):
                totals = list(store.db.hardware.aggregate([{'$group': {'_id': '$status',
                     'records': {'$sum': 1}, 'total': {'$sum': '$total'},
                     'out': {'$sum': '$out'}, 'reserved': {'$sum': '$reserved'}}}], session=session))
                projects = list(store.db.allocations.aggregate([
                    {'$group': {'_id': '$project_id', 'quantity': {'$sum': '$quantity'}, 'out': {'$sum': '$out'}}},
                    {'$sort': {'_id': 1}}, {'$limit': 100}], session=session))
                return {'statuses': totals, 'projects': projects, 'project_limit': 100}
            return clean(s.with_transaction(read, read_concern=ReadConcern('snapshot')))

    # Cookie authentication is represented in the checked-in OpenAPI contract.
    original_openapi = app.openapi
    def with_security():
        schema = original_openapi()
        schema.setdefault('components', {}).setdefault('securitySchemes', {})['Session'] = {
            'type': 'apiKey', 'in': 'cookie', 'name': '__Host-hardware'}
        for path, methods in schema['paths'].items():
            if path.startswith('/api/') or path == '/auth/logout':
                for operation in methods.values():
                    if isinstance(operation, dict):
                        operation['security'] = [{'Session': []}]
        return schema
    app.openapi = with_security
    return app

import hashlib
import secrets
from datetime import timedelta
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from .store import now


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def actor(request: Request):
    user = request.session.get('user')
    if not user:
        raise HTTPException(401, 'Sign in required')
    roles = set(user.get('roles', []))
    if not roles.intersection({'Hardware.Admin', 'Hardware.Auditor'}):
        raise HTTPException(403, 'Administrative role required')
    if request.method not in ('GET', 'HEAD'):
        if 'Hardware.Admin' not in roles and request.url.path != '/auth/logout':
            raise HTTPException(403, 'Hardware.Admin required')
        csrf = request.headers.get('x-csrf-token', '')
        if not secrets.compare_digest(csrf, request.session.get('csrf', '!')):
            raise HTTPException(403, 'Invalid CSRF nonce')
        if request.headers.get('origin') != request.app.state.settings.origin:
            raise HTTPException(403, 'Invalid request origin')
    return user['sub']


def install_sessions(app):
    @app.middleware('http')
    async def session_middleware(request, call_next):
        cfg, db = app.state.settings, app.state.store.db
        cookie = '__Host-hardware' if cfg.environment == 'production' else 'hardware_session'
        sid = request.cookies.get(cookie, '')
        doc = None
        if sid and len(sid) <= 100:
            doc = await run_in_threadpool(db.sessions.find_one,
                                         {'_id': digest(sid), 'expires_at': {'$gt': now()}})
        request.scope['session'] = doc['data'] if doc else {}
        original = repr(request.scope['session'])
        response = await call_next(request)
        if repr(request.scope['session']) != original:
            if not request.session or request.scope.get('rotate_session'):
                if sid:
                    await run_in_threadpool(db.sessions.delete_one, {'_id': digest(sid)})
                sid = ''
            if request.session:
                sid = sid or secrets.token_urlsafe(32)
                expires = doc['expires_at'] if doc and not request.scope.get('rotate_session') else now() + timedelta(seconds=cfg.session_seconds)
                await run_in_threadpool(db.sessions.replace_one, {'_id': digest(sid)},
                                         {'_id': digest(sid), 'data': request.session, 'expires_at': expires}, upsert=True)
                response.set_cookie(cookie, sid, max_age=max(0, int((expires-now()).total_seconds())),
                                    secure=cfg.environment == 'production', httponly=True,
                                    samesite='lax', path='/')
            else:
                response.delete_cookie(cookie, path='/', secure=cfg.environment == 'production',
                                       httponly=True, samesite='lax')
        return response

import base64
import binascii
import hashlib
import secrets
from datetime import timedelta
from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from .store import now
from .teams import allowed


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def client_ip(request: Request):
    # Behind Azure the front end appends the real client to X-Forwarded-For (sometimes "ip:port").
    # Use the right-most entry (added by Azure, not the browser) and drop any port.
    forwarded = request.headers.get('x-forwarded-for', '')
    if request.app.state.settings.environment != 'production' or not forwarded:
        return str(request.client.host)
    last = forwarded.split(',')[-1].strip()
    if last.startswith('['):
        return last[1:last.find(']')]
    return last.split(':')[0] if last.count(':') == 1 else last


def check_team_code(cfg, name: str, code: str) -> bool:
    expected = cfg.team_codes.get(name, '')
    # Always compare (even for unknown names) so timing does not reveal which team names exist.
    ok = secrets.compare_digest(code.encode(), (expected or '\0' * 24).encode())
    return bool(expected) and ok


def count_failure(request: Request, kind: str, limit: int):
    """Count a failed sign-in for this client; refuse after `limit` failures in five minutes."""
    from pymongo import ReturnDocument
    window = int(now().timestamp()) // 300
    bucket = digest(f'{kind}:{client_ip(request)}:{window}')
    doc = request.app.state.store.db.limits.find_one_and_update(
        {'_id': bucket}, {'$inc': {'attempts': 1}, '$setOnInsert': {'expires_at': now() + timedelta(minutes=10)}},
        upsert=True, return_document=ReturnDocument.AFTER)
    if doc['attempts'] > limit:
        raise HTTPException(429, 'Too many failed attempts. Try again in five minutes')


def team_actor(request: Request, header: str):
    """HTTP Basic auth: username = team name, password = team code. No cookie, so no CSRF."""
    cfg = request.app.state.settings
    try:
        name, _, code = base64.b64decode(header[6:].strip(), validate=True).decode().partition(':')
    except (binascii.Error, UnicodeDecodeError):
        name, code = '', ''
    if not check_team_code(cfg, name, code):
        count_failure(request, 'basic', 20)
        raise HTTPException(401, 'Invalid team name or code')
    if not allowed(name, request.method, request.url.path):
        raise HTTPException(403, f'Team {name} is not allowed to call this endpoint')
    return 'team:' + name


def actor(request: Request):
    authorization = request.headers.get('authorization', '')
    if authorization[:6].lower() == 'basic ':
        return team_actor(request, authorization)
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

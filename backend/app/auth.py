import base64
import binascii
import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import unquote
from pymongo import ReturnDocument
from .domain import Conflict
from .store import now
from .teams import READ_RPCS, allowed

ADMIN, AUDITOR = 'Hardware.Admin', 'Hardware.Auditor'
REQUEST_NUMBER = re.compile(r'^[A-Za-z0-9_.:-]{1,100}$')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def metadata(context):
    return {k: v for k, v in context.invocation_metadata() if isinstance(v, str)}


def peer_host(peer):
    """gRPC peers look like 'ipv4:10.0.0.5:4455' or 'ipv6:%5B::1%5D:4455'."""
    kind, _, rest = unquote(peer).partition(':')
    if rest.startswith('['):
        return rest[1:rest.find(']')]
    return rest.rpartition(':')[0] if kind == 'ipv4' else rest


def client_ip(context, environment):
    # Behind a proxy the right-most X-Forwarded-For entry is the one the platform added, not the caller.
    forwarded = metadata(context).get('x-forwarded-for', '')
    if environment != 'production' or not forwarded:
        return peer_host(context.peer())
    last = forwarded.split(',')[-1].strip()
    if last.startswith('['):
        return last[1:last.find(']')]
    return last.split(':')[0] if last.count(':') == 1 else last


def check_team_code(cfg, name: str, code: str) -> bool:
    expected = cfg.team_codes.get(name, '')
    # Always compare (even for unknown names) so timing does not reveal which team names exist.
    ok = secrets.compare_digest(code.encode(), (expected or '\0' * 24).encode())
    return bool(expected) and ok


class Authenticator:
    def __init__(self, cfg, store):
        self.cfg, self.db = cfg, store.db

    def hit(self, context, kind: str, limit: int):
        """Count an attempt by this client; refuse after `limit` in a five-minute window."""
        bucket = digest(f'{kind}:{client_ip(context, self.cfg.environment)}:{int(now().timestamp()) // 300}')
        doc = self.db.limits.find_one_and_update(
            {'_id': bucket}, {'$inc': {'attempts': 1}, '$setOnInsert': {'expires_at': now() + timedelta(minutes=10)}},
            upsert=True, return_document=ReturnDocument.AFTER)
        if doc['attempts'] > limit:
            raise Conflict('Too many failed attempts. Try again in five minutes', 'RESOURCE_EXHAUSTED')

    def authorization(self, context):
        return metadata(context).get('authorization', '')

    def team(self, context, header, method):
        """Basic credentials: team name + team code. Nothing is stored, so there is no session to steal."""
        try:
            name, _, code = base64.b64decode(header[6:].strip(), validate=True).decode().partition(':')
        except (binascii.Error, UnicodeDecodeError):
            name, code = '', ''
        if not check_team_code(self.cfg, name, code):
            self.hit(context, 'basic', 20)
            raise Conflict('Invalid team name or code', 'UNAUTHENTICATED')
        if not allowed(name, method):
            raise Conflict(f'Team {name} is not allowed to call this RPC', 'PERMISSION_DENIED')
        return 'team:' + name

    def session(self, context):
        """Bearer token from AuthService.Login. Returns (token digest, user)."""
        header = self.authorization(context)
        token = header[7:].strip() if header[:7].lower() == 'bearer ' else ''
        doc = None
        if token and len(token) <= 100:
            doc = self.db.sessions.find_one({'_id': digest(token), 'expires_at': {'$gt': now()}})
        if not doc:
            raise Conflict('Sign in required', 'UNAUTHENTICATED')
        return doc['_id'], doc['data']['user']

    def authenticate(self, context, method):
        """Who is calling `method` (a full RPC name)? Raises Conflict if they may not."""
        header = self.authorization(context)
        if header[:6].lower() == 'basic ':
            return self.team(context, header, method)
        _, user = self.session(context)
        roles = set(user.get('roles', []))
        if not roles.intersection({ADMIN, AUDITOR}):
            raise Conflict('Administrative role required', 'PERMISSION_DENIED')
        if method.rpartition('/')[2] not in READ_RPCS and ADMIN not in roles:
            raise Conflict('Hardware.Admin required', 'PERMISSION_DENIED')
        return user['sub']

    def open_session(self, user):
        token = secrets.token_urlsafe(32)
        expires = now() + timedelta(seconds=self.cfg.session_seconds)
        self.db.sessions.insert_one({'_id': digest(token), 'data': {'user': user}, 'expires_at': expires})
        return token, expires

    def close_session(self, session_id):
        self.db.sessions.delete_one({'_id': session_id})

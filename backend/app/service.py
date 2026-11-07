"""gRPC service implementations. Business rules live in domain.py/store.py; this layer only
authenticates, validates, calls the store and shapes the response."""
import functools
import logging
import re
import secrets
import time
from datetime import timezone
import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc
from . import convert, models
from .auth import REQUEST_NUMBER, check_team_code, client_ip, metadata
from .domain import Conflict
from .store import clean
from .teams import HARDWARE_SERVICE

log = logging.getLogger('hardware')
MAX_OFFSET = 20000


class HardwareId(models.Model):
    hardware_id: models.Identifier


def describe(error: ValidationError) -> str:
    return '; '.join(f"{'.'.join(map(str, e['loc'])) or 'request'}: {e['msg']}" for e in error.errors()[:5])


def parse(model, message):
    """Validate the fields the caller actually set (proto3 `optional` tells unset from zero)."""
    fields = {}
    for field, value in message.ListFields():
        fields[field.name] = value.ToDatetime(tzinfo=timezone.utc) if isinstance(value, Timestamp) else value
    try:
        return model.model_validate(fields)
    except ValidationError as e:
        raise Conflict(describe(e), 'INVALID_ARGUMENT') from None


def request_number(context, data):
    """The request_id field or the idempotency-key metadata entry (they must agree if both are sent)."""
    header = metadata(context).get('idempotency-key')
    if header and not REQUEST_NUMBER.match(header):
        raise Conflict('idempotency-key must be 1-100 letters, digits or _ . : -', 'INVALID_ARGUMENT')
    if header and data.request_id and header != data.request_id:
        raise Conflict('idempotency-key metadata and request_id differ', 'INVALID_ARGUMENT')
    if not (header or data.request_id):
        raise Conflict('Send a unique request_id (or idempotency-key metadata)', 'INVALID_ARGUMENT')
    return header or data.request_id


def guarded(authenticated=True):
    """Authenticate, turn the service's errors into gRPC status codes, and log one line per call.

    The log line holds the RPC name, caller label, address, request number, status and duration. It never
    holds the authorization header, team codes, tokens or message bodies; text that came from the caller is
    written with repr() so it cannot forge extra log lines."""
    def decorate(fn):
        @functools.wraps(fn)
        def handler(self, request, context):
            started, actor = time.monotonic(), '-'

            def finish(code, detail=''):
                level = logging.ERROR if code in (grpc.StatusCode.INTERNAL, grpc.StatusCode.UNAVAILABLE)                     else logging.INFO if code == grpc.StatusCode.OK else logging.WARNING
                request_id = getattr(request, 'request_id', '')
                log.log(level, 'rpc=%s actor=%s peer=%s status=%s ms=%d%s%s', fn.__name__, actor,
                        client_ip(context, 'production'), code.name, (time.monotonic() - started) * 1000,
                        f' request_id={request_id!r}' if request_id else '',
                        f' detail={detail[:200]!r}' if detail else '')

            try:
                if authenticated:
                    actor = self.auth.authenticate(context, f'/{HARDWARE_SERVICE}/{fn.__name__}')
                    result = fn(self, request, context, actor)
                else:
                    result = fn(self, request, context)
                finish(grpc.StatusCode.OK)
                return result
            except Conflict as e:
                code, message = getattr(grpc.StatusCode, e.code), str(e)
            except PyMongoError as e:
                log.error('Database operation failed: %s', type(e).__name__)
                code, message = grpc.StatusCode.UNAVAILABLE, 'Storage temporarily unavailable; retry with the same request_id'
            except Exception:
                log.exception('Unhandled error in %s', fn.__name__)
                code, message = grpc.StatusCode.INTERNAL, 'Internal error'
            finish(code, message)
            context.abort(code, message)
        return handler
    return decorate


class HardwareServicer(rpc.HardwareServiceServicer):
    def __init__(self, auth, store, sql_store):
        self.auth, self.store, self.sql_store = auth, store, sql_store

    def mutate(self, context, actor, command, entity, data, **dump):
        """Run a command once per request number and return the stored (possibly replayed) document."""
        body = data.model_dump(exclude={'request_id', *dump.pop('exclude', ())}, **dump)
        return self.store.mutate(actor, request_number(context, data), command, entity, body)

    @guarded()
    def CreateHardware(self, request, context, actor):
        data = parse(models.Create, request)
        return convert.hardware(self.mutate(context, actor, 'create', '', data))

    @guarded()
    def UpdateHardware(self, request, context, actor):
        data = parse(models.Edit, request)
        return convert.hardware(self.mutate(context, actor, 'edit', data.hardware_id, data,
                                            exclude=['hardware_id'], exclude_none=True))

    @guarded()
    def RetireHardware(self, request, context, actor):
        data = parse(models.Retire, request)
        return convert.hardware(self.mutate(context, actor, 'retire', data.hardware_id, data, exclude=['hardware_id']))

    @guarded()
    def AdjustHardware(self, request, context, actor):
        data = parse(models.Adjust, request)
        if data.delta == 0:
            raise Conflict('Delta must be nonzero', 'INVALID_ARGUMENT')
        return convert.hardware(self.mutate(context, actor, 'adjust', data.hardware_id, data, exclude=['hardware_id']))

    @guarded()
    def AllocateHardware(self, request, context, actor):
        data = parse(models.Allocate, request)
        return convert.hardware(self.mutate(context, actor, 'allocate', data.hardware_id, data,
                                            exclude=['hardware_id'], exclude_none=True))

    @guarded()
    def CreateCheckout(self, request, context, actor):
        data = parse(models.Checkout, request)
        return convert.checkout(self.mutate(context, actor, 'checkout', data.hardware_id, data, exclude_none=True))

    @guarded()
    def ReturnCheckout(self, request, context, actor):
        data = parse(models.Return, request)
        return convert.checkout(self.mutate(context, actor, 'return', data.checkout_id, data,
                                            exclude=['checkout_id'], exclude_none=True))

    @guarded()
    def CorrectCheckout(self, request, context, actor):
        data = parse(models.Correction, request)
        return convert.checkout(self.mutate(context, actor, 'correct', data.checkout_id, data, exclude=['checkout_id']))

    def listing(self, collection, query, page, size, sort=None):
        offset = (page - 1) * size
        if offset > MAX_OFFSET:
            raise Conflict(f'Narrow filters; maximum offset is {MAX_OFFSET}', 'INVALID_ARGUMENT')
        db = self.store.db[collection]
        cursor = db.find(query).sort(sort or [('_id', 1)]).skip(offset).limit(size).max_time_ms(5000)
        return clean(list(cursor)), db.count_documents(query, maxTimeMS=5000)

    @guarded()
    def ListHardware(self, request, context, actor):
        q = parse(models.HardwareQuery, request)
        query = {}
        if q.q.strip():
            prefix = '^' + re.escape(q.q.strip().casefold())
            query['$or'] = [{'name_key': {'$regex': prefix}}, {'asset_key': {'$regex': prefix}}]
        if q.status:
            query['status'] = q.status
        if q.category:
            query['category'] = q.category
        way = 1 if q.direction == 'asc' else -1
        items, total = self.listing('hardware', query, q.page, q.size, [(q.sort, way), ('_id', way)])
        return pb.ListHardwareResponse(items=[convert.hardware(i) for i in items], total=total, page=q.page, size=q.size)

    @guarded()
    def GetHardware(self, request, context, actor):
        data = parse(HardwareId, request)
        return convert.hardware(clean(self.store.get('hardware', data.hardware_id)))

    @guarded()
    def GetAvailability(self, request, context, actor):
        data = parse(HardwareId, request)
        h = self.store.get('hardware', data.hardware_id)
        reply = pb.AvailabilityResponse(hardware=convert.hardware(clean(h)))
        reply.availability = 'available' if reply.hardware.available > 0 else 'unavailable'
        if reply.hardware.available == 0:
            try:
                rows = self.sql_store.find_vendor_alternatives(h['name'], h.get('category'))
                reply.alternatives.extend(convert.to_pb(pb.VendorAlternative, row) for row in rows)
            except Exception:
                log.exception('Azure SQL alternative lookup failed')
                reply.ClearField('alternatives')
                reply.alternative_lookup_status = 'temporarily_unavailable'
        return reply

    @guarded()
    def ListAllocations(self, request, context, actor):
        q = parse(models.AllocationQuery, request)
        query = {k: v for k, v in {'project_id': q.project_id, 'hardware_id': q.hardware_id}.items() if v}
        items, total = self.listing('allocations', query, q.page, q.size)
        return pb.ListAllocationsResponse(items=[convert.to_pb(pb.Allocation, i) for i in items],
                                          total=total, page=q.page, size=q.size)

    @guarded()
    def ListCheckouts(self, request, context, actor):
        q = parse(models.CheckoutQuery, request)
        query = {k: v for k, v in {'project_id': q.project_id, 'hardware_id': q.hardware_id,
                                  'user_id': q.user_id}.items() if v}
        if q.outstanding:
            query['$expr'] = {'$lt': ['$returned', '$quantity']}
        items, total = self.listing('checkouts', query, q.page, q.size, [('created_at', -1), ('_id', -1)])
        return pb.ListCheckoutsResponse(items=[convert.checkout(i) for i in items], total=total, page=q.page, size=q.size)

    @guarded()
    def ListTransactions(self, request, context, actor):
        q = parse(models.TransactionQuery, request)
        if q.start and q.end and q.start > q.end:
            raise Conflict('Start must precede end', 'INVALID_ARGUMENT')
        query = {k: v for k, v in {'project_id': q.project_id, 'hardware_id': q.hardware_id, 'actor': q.actor_id,
                                  'user_id': q.user_id, 'operation': q.operation}.items() if v}
        if q.start or q.end:
            query['timestamp'] = {k: v for k, v in {'$gte': q.start, '$lte': q.end}.items() if v}
        items, total = self.listing('audit', query, q.page, q.size, [('timestamp', -1), ('_id', -1)])
        return pb.ListTransactionsResponse(items=[convert.to_pb(pb.AuditEvent, i) for i in items],
                                           total=total, page=q.page, size=q.size)

    @guarded()
    def GetSummary(self, request, context, actor):
        from pymongo.read_concern import ReadConcern
        db = self.store.db

        def read(session):
            totals = list(db.hardware.aggregate([{'$group': {'_id': '$status', 'records': {'$sum': 1},
                'total': {'$sum': '$total'}, 'out': {'$sum': '$out'}, 'reserved': {'$sum': '$reserved'}}}],
                session=session))
            projects = list(db.allocations.aggregate([
                {'$group': {'_id': '$project_id', 'quantity': {'$sum': '$quantity'}, 'out': {'$sum': '$out'}}},
                {'$sort': {'_id': 1}}, {'$limit': 100}], session=session))
            return totals, projects
        # One snapshot across both collections gives consistent dashboard totals.
        with self.store.client.start_session() as s:
            totals, projects = s.with_transaction(read, read_concern=ReadConcern('snapshot'))
        return pb.SummaryResponse(
            statuses=[pb.StatusSummary(status=t['_id'], **{k: t[k] for k in ('records', 'total', 'out', 'reserved')})
                      for t in totals],
            projects=[pb.ProjectSummary(project_id=p['_id'], quantity=p['quantity'], out=p['out']) for p in projects],
            project_limit=100)


class AuthServicer(rpc.AuthServiceServicer):
    def __init__(self, auth, cfg):
        self.auth, self.cfg = auth, cfg

    def signed_in(self, user):
        token, expires = self.auth.open_session(user)
        reply = pb.LoginResponse(token=token, name=user['name'], roles=user['roles'])
        reply.expires_at.FromDatetime(expires)
        return reply

    @guarded(authenticated=False)
    def GetAuthMethods(self, request, context):
        return pb.AuthMethodsResponse(password=bool(self.cfg.ui_teams()),
                                      local=self.cfg.environment != 'production' and bool(self.cfg.dev_code))

    @guarded(authenticated=False)
    def Login(self, request, context):
        """Website sign-in with a team name and that team's code (only teams with ui=True)."""
        if not self.cfg.ui_teams():
            raise Conflict('Team sign-in is not enabled', 'NOT_FOUND')
        data = parse(models.TeamLogin, request)
        self.auth.hit(context, 'password', 10)
        name = data.name.strip().lower()
        if not check_team_code(self.cfg, name, data.code) or name not in self.cfg.ui_teams():
            log.warning('Team sign-in failed')
            raise Conflict('Wrong team name or code', 'UNAUTHENTICATED')
        return self.signed_in({'sub': 'team:' + name, 'name': name, 'roles': ['Hardware.Admin']})

    @guarded(authenticated=False)
    def DevLogin(self, request, context):
        if self.cfg.environment == 'production' or not self.cfg.dev_code:
            raise Conflict('Development sign-in is not enabled', 'NOT_FOUND')
        data = parse(models.DevLogin, request)
        self.auth.hit(context, 'dev', 5)
        if not secrets.compare_digest(data.code, self.cfg.dev_code):
            raise Conflict('Invalid development code', 'UNAUTHENTICATED')
        return self.signed_in({'sub': 'local:admin', 'name': 'Local administrator', 'roles': ['Hardware.Admin']})

    @guarded(authenticated=False)
    def Logout(self, request, context):
        session_id, _ = self.auth.session(context)
        self.auth.close_session(session_id)
        return pb.LogoutResponse()

    @guarded(authenticated=False)
    def Me(self, request, context):
        if self.auth.authorization(context)[:6].lower() == 'basic ':
            raise Conflict('Me is for signed-in website users; team callers have no session', 'PERMISSION_DENIED')
        _, user = self.auth.session(context)
        return pb.MeResponse(sub=user['sub'], name=user['name'], roles=user['roles'])


class OpsServicer(rpc.OpsServiceServicer):
    def __init__(self, store):
        self.store = store

    @guarded(authenticated=False)
    def Ready(self, request, context):
        hello = self.store.client.admin.command('hello')
        if not (hello.get('setName') or hello.get('msg') == 'isdbgrid'):
            raise Conflict('Transactional replica set required', 'UNAVAILABLE')
        return pb.ReadyResponse(status='ready')

"""Real MongoDB transaction and gRPC tests. No in-memory substitute."""
import base64
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import pytest

pytestmark = pytest.mark.integration
if not os.getenv('RUN_INTEGRATION'):
    pytest.skip('Set RUN_INTEGRATION=1 with a provisioned replica set', allow_module_level=True)

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from google.protobuf.timestamp_pb2 import Timestamp
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from app.auth import digest
from app.config import Settings
from app.server import Application
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc

CODES = {'hardware-admin': 'admin-code-' + 'a' * 16, 'projects-team': 'projects-code-' + 'p' * 16,
         'front-end': 'frontend-code-' + 'f' * 16, 'users-team': 'users-code-' + 'u' * 16}
S = grpc.StatusCode


class Caller:
    """One gRPC channel with one set of credentials; every service stub sends them as metadata."""

    def __init__(self, port, authorization=None):
        self.channel = grpc.insecure_channel(f'127.0.0.1:{port}')
        self.metadata = (('authorization', authorization),) if authorization else ()
        self.hw = self.wrap(rpc.HardwareServiceStub(self.channel))
        self.auth = self.wrap(rpc.AuthServiceStub(self.channel))
        self.ops = self.wrap(rpc.OpsServiceStub(self.channel))

    def wrap(self, stub):
        class Bound:
            def __getattr__(_, name):
                call = getattr(stub, name)
                return lambda request, metadata=(), **kw: call(request, metadata=self.metadata + metadata, timeout=30, **kw)
        return Bound()

    def close(self):
        self.channel.close()


def basic(name, code=None):
    return 'Basic ' + base64.b64encode(f'{name}:{code or CODES[name]}'.encode()).decode()


@contextmanager
def fails(code):
    with pytest.raises(grpc.RpcError) as caught:
        yield caught
    assert caught.value.code() == code, f'{caught.value.code()}: {caught.value.details()}'


def rid():
    return uuid4().hex


@pytest.fixture
def app(tmp_path):
    cfg = Settings(environment='test', database='hardware_test', dev_code=secrets.token_urlsafe(32),
                   team_codes=dict(CODES), sql_mock_data_file=str(tmp_path / 'vendors.json'))
    (tmp_path / 'vendors.json').write_text(json.dumps([
        {'source_unit': 'Austin', 'vendor': 'Dell', 'commodity': 'Laptop computers', 'category': 'Computer',
         'quantity': 42, 'unit_price': 1250.0}]))
    assert cfg.database.endswith('_test')
    root_password = Path(os.environ['ROOT_PASSWORD_FILE']).read_text().strip()
    root = MongoClient('mongodb://mongo:27017/?replicaSet=rs0', username='root', password=root_password)
    db = root[cfg.database]
    for c in ('hardware', 'allocations', 'checkouts', 'audit', 'requests', 'sessions', 'limits'):
        db[c].delete_many({})
    server = Application(cfg, '127.0.0.1:0').start()
    server.root_db = db
    callers = []
    server.caller = lambda authorization=None: callers.append(Caller(server.port, authorization)) or callers[-1]
    yield server
    for c in callers:
        c.close()
    server.stop()
    root.close()


@pytest.fixture
def admin(app):
    """The website's way in: a bearer token from the local development sign-in."""
    anonymous = app.caller()
    token = anonymous.auth.DevLogin(pb.DevLoginRequest(code=app.cfg.dev_code)).token
    caller = app.caller('Bearer ' + token)
    caller.token = token
    return caller


def new_hardware(admin, quantity=5):
    return admin.hw.CreateHardware(pb.CreateHardwareRequest(
        name='Laptop', category='Computer', asset_tag=uuid4().hex, total=quantity, reason='Test stock', request_id=rid()))


def allocate(admin, h, quantity=5, project='p'):
    return admin.hw.AllocateHardware(pb.AllocateHardwareRequest(
        hardware_id=h.id, project_id=project, quantity=quantity, reason='Project reservation',
        expected_version=h.version, request_id=rid()))


def checkout(caller, h, quantity=3, project='p', user='user1', request_id=None):
    return caller.hw.CreateCheckout(pb.CreateCheckoutRequest(
        hardware_id=h.id, project_id=project, user_id=user, quantity=quantity, reason='Lab work',
        request_id=request_id or rid()))


def get(admin, h):
    return admin.hw.GetHardware(pb.GetHardwareRequest(hardware_id=h.id))


def test_crud_and_adjustments(admin):
    h = new_hardware(admin)
    assert (h.status, h.total, h.reserved, h.out, h.version) == ('active', 5, 0, 0, 1)
    edited = admin.hw.UpdateHardware(pb.UpdateHardwareRequest(
        hardware_id=h.id, name='Updated', expected_version=1, reason='Rename asset', request_id=rid()))
    assert edited.name == 'Updated' and edited.version == 2
    with fails(S.FAILED_PRECONDITION):
        admin.hw.AdjustHardware(pb.AdjustHardwareRequest(
            hardware_id=h.id, delta=-6, expected_version=2, confirmed=True, reason='Invalid removal', request_id=rid()))
    adjusted = admin.hw.AdjustHardware(pb.AdjustHardwareRequest(
        hardware_id=h.id, delta=2, expected_version=2, confirmed=True, reason='Purchase units', request_id=rid()))
    assert adjusted.total == 7
    retired = admin.hw.RetireHardware(pb.RetireHardwareRequest(
        hardware_id=h.id, expected_version=3, confirmed=True, reason='End of life', request_id=rid()))
    assert retired.status == 'retired' and get(admin, h).status == 'retired'


def test_checkout_partial_return_and_correction(admin):
    h = new_hardware(admin)
    allocate(admin, h)
    c = checkout(admin, h)
    assert (c.quantity, c.returned, c.outstanding, c.version) == (3, 0, 3, 1)
    assert c.created_at.seconds > 0
    r = admin.hw.ReturnCheckout(pb.ReturnCheckoutRequest(
        checkout_id=c.id, quantity=1, expected_version=1, reason='Partial return', request_id=rid()))
    assert (r.returned, r.outstanding) == (1, 2)
    fixed = admin.hw.CorrectCheckout(pb.CorrectCheckoutRequest(
        checkout_id=c.id, returned=0, expected_version=2, confirmed=True, reason='Return entered by mistake',
        request_id=rid()))
    assert fixed.returned == 0
    with fails(S.FAILED_PRECONDITION):
        admin.hw.ReturnCheckout(pb.ReturnCheckoutRequest(
            checkout_id=c.id, quantity=4, expected_version=3, reason='Too many', request_id=rid()))
    assert get(admin, h).available == 2


def test_concurrent_checkout_never_oversells(app, admin):
    h = new_hardware(admin)
    allocate(admin, h)

    def attempt(i):
        try:
            checkout(admin, h, quantity=1, user=f'u{i}')
            return S.OK
        except grpc.RpcError as e:
            return e.code()
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(attempt, range(20)))
    assert codes.count(S.OK) == 5, codes
    assert set(codes) <= {S.OK, S.FAILED_PRECONDITION, S.ABORTED}, codes
    assert get(admin, h).available == 0
    assert app.root_db.audit.count_documents({'operation': 'checkout'}) == 5


def test_idempotency_and_conflicting_reuse(app, admin):
    h = new_hardware(admin)
    request = pb.AdjustHardwareRequest(hardware_id=h.id, delta=1, expected_version=1, reason='One purchase',
                                       confirmed=True, request_id=rid())
    a, b = admin.hw.AdjustHardware(request), admin.hw.AdjustHardware(request)
    assert a == b and a.total == 6
    different = pb.AdjustHardwareRequest()
    different.CopyFrom(request)
    different.delta = 2
    with fails(S.ALREADY_EXISTS):
        admin.hw.AdjustHardware(different)
    assert app.root_db.audit.count_documents({'operation': 'adjust'}) == 1


def test_request_number_may_travel_as_metadata(admin):
    h = new_hardware(admin)
    request = pb.AdjustHardwareRequest(hardware_id=h.id, delta=1, expected_version=1, reason='Via metadata',
                                       confirmed=True)
    key = (('idempotency-key', rid()),)
    assert admin.hw.AdjustHardware(request, metadata=key) == admin.hw.AdjustHardware(request, metadata=key)
    assert get(admin, h).total == 6


def test_stale_version_confirmation_validation_and_duplicate_asset(admin):
    h = new_hardware(admin)
    stale = pb.AdjustHardwareRequest(hardware_id=h.id, delta=1, expected_version=9, confirmed=True,
                                     reason='Purchase', request_id=rid())
    with fails(S.ABORTED):
        admin.hw.AdjustHardware(stale)
    unconfirmed = pb.AdjustHardwareRequest(hardware_id=h.id, delta=1, expected_version=1, reason='Purchase',
                                           request_id=rid())
    with fails(S.INVALID_ARGUMENT) as e:
        admin.hw.AdjustHardware(unconfirmed)
    assert 'confirmed' in e.value.details()
    with fails(S.ALREADY_EXISTS):
        admin.hw.CreateHardware(pb.CreateHardwareRequest(
            name='Other', category='Computer', asset_tag=h.asset_tag.upper(), total=2, reason='Duplicate', request_id=rid()))
    with fails(S.INVALID_ARGUMENT):
        admin.hw.CreateHardware(pb.CreateHardwareRequest(
            name='Bad', category='Computer', asset_tag='bad', total=-1, reason='Invalid', request_id=rid()))
    with fails(S.INVALID_ARGUMENT):
        admin.hw.CreateHardware(pb.CreateHardwareRequest(
            name='Bad', category='Computer', asset_tag='bad2', total=1, reason='No request number'))


def test_authentication_and_roles(app, admin):
    h = new_hardware(admin)
    anonymous = app.caller()
    with fails(S.UNAUTHENTICATED):
        anonymous.hw.ListHardware(pb.ListHardwareRequest())
    with fails(S.UNAUTHENTICATED):
        app.caller('Bearer not-a-real-token').hw.ListHardware(pb.ListHardwareRequest())
    retire = pb.RetireHardwareRequest(hardware_id=h.id, expected_version=1, reason='Retire', confirmed=True, request_id=rid())
    session = {'_id': digest(admin.token)}
    app.root_db.sessions.update_one(session, {'$set': {'data.user.roles': ['Hardware.Auditor']}})
    assert admin.hw.ListHardware(pb.ListHardwareRequest()).total == 1
    with fails(S.PERMISSION_DENIED):
        admin.hw.RetireHardware(retire)
    app.root_db.sessions.update_one(session, {'$set': {'data.user.roles': []}})
    with fails(S.PERMISSION_DENIED):
        admin.hw.ListHardware(pb.ListHardwareRequest())


def test_session_expiry_and_logout(app, admin):
    new_hardware(admin)
    admin.auth.Logout(pb.LogoutRequest())
    with fails(S.UNAUTHENTICATED):
        admin.hw.ListHardware(pb.ListHardwareRequest())
    token = app.caller().auth.DevLogin(pb.DevLoginRequest(code=app.cfg.dev_code)).token
    caller = app.caller('Bearer ' + token)
    app.root_db.sessions.update_one({'_id': digest(token)}, {'$set': {'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    with fails(S.UNAUTHENTICATED):
        caller.hw.ListHardware(pb.ListHardwareRequest())


def test_audit_immutable_at_database_and_no_delete_rpc(app, admin):
    new_hardware(admin)
    event = admin.hw.ListTransactions(pb.ListTransactionsRequest()).items[0]
    assert event.actor == 'local:admin' and event.operation == 'create' and event.reason == 'Test stock'
    assert event.timestamp.seconds > 0 and event.after['hardware']['total'] == 5
    assert not event.HasField('project_id') and event.request_id
    raw = admin.channel.unary_unary('/hardware.v1.HardwareService/DeleteTransaction')
    with fails(S.UNIMPLEMENTED):
        raw(b'', metadata=admin.metadata, timeout=10)
    with pytest.raises(OperationFailure):
        app.store.db.audit.update_one({'_id': event.id}, {'$set': {'reason': 'tamper'}})
    with pytest.raises(OperationFailure):
        app.store.db.audit.delete_one({'_id': event.id})


def test_search_pagination_filters_and_missing_entity(admin):
    new_hardware(admin)
    new_hardware(admin)
    page = admin.hw.ListHardware(pb.ListHardwareRequest(q='lap', size=1))
    assert (page.total, len(page.items), page.page, page.size) == (2, 1, 1, 1)
    assert admin.hw.ListHardware(pb.ListHardwareRequest(q='nomatch')).total == 0
    assert admin.hw.ListHardware(pb.ListHardwareRequest(status='retired')).total == 0
    with fails(S.NOT_FOUND):
        admin.hw.GetHardware(pb.GetHardwareRequest(hardware_id='missing'))
    with fails(S.INVALID_ARGUMENT):
        admin.hw.ListHardware(pb.ListHardwareRequest(sort='$where'))
    with fails(S.INVALID_ARGUMENT):
        admin.hw.ListHardware(pb.ListHardwareRequest(page=10000, size=100))


def test_allocation_floor_and_audit_filter(admin):
    h = new_hardware(admin)
    h = allocate(admin, h)
    checkout(admin, h, quantity=3, user='u')
    h = get(admin, h)
    with fails(S.FAILED_PRECONDITION):
        admin.hw.AllocateHardware(pb.AllocateHardwareRequest(
            hardware_id=h.id, project_id='p', quantity=2, reason='Too small', expected_version=h.version, request_id=rid()))
    assert admin.hw.ListTransactions(pb.ListTransactionsRequest(project_id='p')).total == 2
    start = Timestamp()
    start.FromDatetime(datetime.now(timezone.utc) + timedelta(days=1))
    assert admin.hw.ListTransactions(pb.ListTransactionsRequest(start=start)).total == 0
    end = Timestamp()
    end.FromDatetime(datetime.now(timezone.utc) - timedelta(days=1))
    with fails(S.INVALID_ARGUMENT):
        admin.hw.ListTransactions(pb.ListTransactionsRequest(start=start, end=end))


def test_concurrent_duplicate_submission_commits_once(app, admin):
    h = new_hardware(admin)
    request = pb.AdjustHardwareRequest(hardware_id=h.id, delta=1, expected_version=1,
                                       reason='Single physical delivery', confirmed=True, request_id=rid())
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: admin.hw.AdjustHardware(request), range(10)))
    assert all(r == responses[0] for r in responses)
    assert get(admin, h).total == 6
    assert app.root_db.audit.count_documents({'operation': 'adjust'}) == 1


def test_failure_to_append_audit_rolls_back_stock_and_receipt(app, admin):
    h = new_hardware(admin)
    root = app.root_db
    role = root.command('rolesInfo', 'hardwareApp', showPrivileges=True)['roles'][0]
    original = role['privileges']
    without_audit = [p for p in original if p['resource'].get('collection') != 'audit']
    try:
        root.command('updateRole', 'hardwareApp', privileges=without_audit, roles=[])
        with fails(S.UNAVAILABLE):
            admin.hw.AdjustHardware(pb.AdjustHardwareRequest(
                hardware_id=h.id, delta=1, expected_version=1, reason='Audit permission failure', confirmed=True,
                request_id=rid()))
    finally:
        root.command('updateRole', 'hardwareApp', privileges=original, roles=[])
    assert root.hardware.find_one({'_id': h.id})['total'] == 5
    assert root.audit.count_documents({}) == 1
    assert root.requests.count_documents({}) == 1


def test_projects_team_needs_no_login_and_request_number_is_applied_once(app, admin):
    h = new_hardware(admin)
    svc = app.caller(basic('projects-team'))
    allocation = pb.AllocateHardwareRequest(hardware_id=h.id, project_id='proj-1', quantity=3,
                                            reason='Project request', request_id='ALLOC-1001')
    first = svc.hw.AllocateHardware(allocation)
    assert svc.hw.AllocateHardware(allocation) == first and first.reserved == 3
    order = pb.CreateCheckoutRequest(hardware_id=h.id, project_id='proj-1', user_id='student-7', quantity=2,
                                     reason='Lab laptops', request_id='REQ-1001')
    c, retry = svc.hw.CreateCheckout(order), svc.hw.CreateCheckout(order)
    assert c == retry
    assert app.root_db.checkouts.count_documents({}) == 1
    assert get(admin, h).out == 2
    ret = pb.ReturnCheckoutRequest(checkout_id=c.id, quantity=1, reason='One returned', request_id='RET-1001')
    a, b = svc.hw.ReturnCheckout(ret), svc.hw.ReturnCheckout(ret)
    assert a == b and b.returned == 1
    assert get(admin, h).out == 1
    events = list(app.root_db.audit.find({'operation': {'$in': ['checkout', 'return', 'allocate']}}))
    assert len(events) == 3 and {e['actor'] for e in events} == {'team:projects-team'}


def test_team_permissions_and_request_number_validation(app, admin):
    h = new_hardware(admin)
    projects, front, users = (app.caller(basic(n)) for n in ('projects-team', 'front-end', 'users-team'))
    assert projects.hw.ListHardware(pb.ListHardwareRequest()).total == 1
    assert front.hw.ListHardware(pb.ListHardwareRequest()).total == 1
    assert users.hw.ListHardware(pb.ListHardwareRequest()).total == 1
    create = pb.CreateHardwareRequest(name='X', category='Y', asset_tag=uuid4().hex, total=1, reason='Not allowed', request_id=rid())
    with fails(S.PERMISSION_DENIED):
        projects.hw.CreateHardware(create)
    with fails(S.PERMISSION_DENIED):
        projects.auth.Me(pb.MeRequest())
    with fails(S.PERMISSION_DENIED):
        users.hw.AllocateHardware(pb.AllocateHardwareRequest(
            hardware_id=h.id, project_id='p', quantity=1, reason='Read only team', request_id='X-0'))
    # The front-end team may allocate and check out.
    assert front.hw.AllocateHardware(pb.AllocateHardwareRequest(
        hardware_id=h.id, project_id='p', quantity=1, reason='Reserve one', request_id='ALLOC-1')).reserved == 1
    created = app.caller(basic('hardware-admin')).hw.CreateHardware(
        pb.CreateHardwareRequest(name='Y', category='Y', asset_tag=uuid4().hex, total=1, reason='Admin via API'),
        metadata=(('idempotency-key', 'ADMIN-1'),))
    assert created.total == 1
    with fails(S.UNAUTHENTICATED):
        app.caller(basic('projects-team', CODES['front-end'])).hw.ListHardware(pb.ListHardwareRequest())
    with fails(S.UNAUTHENTICATED):
        app.caller(basic('nobody', 'x' * 24)).hw.ListHardware(pb.ListHardwareRequest())
    with fails(S.UNAUTHENTICATED):
        app.caller('Basic !!!not-base64').hw.ListHardware(pb.ListHardwareRequest())
    body = pb.CreateCheckoutRequest(hardware_id=h.id, project_id='p', user_id='u', quantity=1, reason='Missing number')
    with fails(S.INVALID_ARGUMENT):
        front.hw.CreateCheckout(body)
    mismatch = pb.CreateCheckoutRequest()
    mismatch.CopyFrom(body)
    mismatch.request_id = 'A-1'
    with fails(S.INVALID_ARGUMENT):
        front.hw.CreateCheckout(mismatch, metadata=(('idempotency-key', 'B-2'),))
    body.request_id = 'SAME-1'
    assert front.hw.CreateCheckout(body).quantity == 1
    body.quantity = 2
    with fails(S.ALREADY_EXISTS):
        front.hw.CreateCheckout(body)


def test_failed_basic_logins_are_rate_limited(app):
    wrong = app.caller(basic('front-end', 'wrong-code-' + 'x' * 16))
    codes = []
    for _ in range(22):
        try:
            wrong.hw.ListHardware(pb.ListHardwareRequest())
        except grpc.RpcError as e:
            codes.append(e.code())
    assert codes[:20] == [S.UNAUTHENTICATED] * 20 and codes[20:] == [S.RESOURCE_EXHAUSTED] * 2


def test_website_sign_in_only_for_ui_teams(app):
    browser = app.caller()
    methods = browser.auth.GetAuthMethods(pb.GetAuthMethodsRequest())
    assert methods.password and methods.local
    with fails(S.UNAUTHENTICATED):
        browser.auth.Login(pb.LoginRequest(name='hardware-admin', code='wrong-code-wrong-code'))
    with fails(S.UNAUTHENTICATED):
        browser.auth.Login(pb.LoginRequest(name='projects-team', code=CODES['projects-team']))
    with fails(S.UNAUTHENTICATED):
        browser.auth.Me(pb.MeRequest())
    ok = browser.auth.Login(pb.LoginRequest(name='Hardware-Admin', code=CODES['hardware-admin']))
    assert ok.token and list(ok.roles) == ['Hardware.Admin'] and ok.expires_at.seconds > datetime.now(timezone.utc).timestamp()
    me = app.caller('Bearer ' + ok.token).auth.Me(pb.MeRequest())
    assert me.name == 'hardware-admin' and me.sub == 'team:hardware-admin' and 'Hardware.Admin' in me.roles
    with fails(S.UNAUTHENTICATED):
        browser.auth.DevLogin(pb.DevLoginRequest(code='wrong-development-code-0000'))


def test_responses_explain_stock_numbers(admin):
    h = allocate(admin, new_hardware(admin, 10), 3)
    assert (h.total, h.reserved, h.out, h.available, h.unallocated) == (10, 3, 0, 10, 7)
    c = checkout(admin, h, quantity=2)
    h = get(admin, h)
    assert (h.out, h.available, h.unallocated) == (2, 8, 7)
    allocation = admin.hw.ListAllocations(pb.ListAllocationsRequest(project_id='p', hardware_id=h.id)).items[0]
    assert (allocation.quantity, allocation.out, allocation.id) == (3, 2, f'{h.id}:p')
    outstanding = admin.hw.ListCheckouts(pb.ListCheckoutsRequest())
    assert outstanding.total == 1 and outstanding.items[0].id == c.id
    admin.hw.ReturnCheckout(pb.ReturnCheckoutRequest(checkout_id=c.id, quantity=2, reason='All back', request_id=rid()))
    assert admin.hw.ListCheckouts(pb.ListCheckoutsRequest()).total == 0
    assert admin.hw.ListCheckouts(pb.ListCheckoutsRequest(outstanding=False)).total == 1
    summary = admin.hw.GetSummary(pb.GetSummaryRequest())
    status = {s.status: s for s in summary.statuses}['active']
    assert (status.records, status.total, status.out, status.reserved) == (1, 10, 0, 3)
    assert [(p.project_id, p.quantity, p.out) for p in summary.projects] == [('p', 3, 0)]
    assert summary.project_limit == 100


def test_availability_and_vendor_alternatives(admin):
    h = allocate(admin, new_hardware(admin, 2), 2)
    stocked = admin.hw.GetAvailability(pb.GetHardwareRequest(hardware_id=h.id))
    assert stocked.availability == 'available' and not stocked.alternatives
    checkout(admin, h, quantity=2)
    empty = admin.hw.GetAvailability(pb.GetHardwareRequest(hardware_id=h.id))
    assert empty.availability == 'unavailable' and empty.hardware.available == 0
    assert [a.vendor for a in empty.alternatives] == ['Dell'] and empty.alternatives[0].unit_price == 1250.0
    assert not empty.HasField('alternative_lookup_status')


def test_oversized_requests_are_refused(admin):
    with fails(S.RESOURCE_EXHAUSTED):
        admin.hw.CreateHardware(pb.CreateHardwareRequest(
            name='x', category='y', asset_tag='z', total=1, reason='Too big', request_id=rid(), description='d' * 20000))


def test_health_and_readiness(app):
    anonymous = app.caller()
    assert anonymous.ops.Ready(pb.ReadyRequest()).status == 'ready'
    stub = health_pb2_grpc.HealthStub(anonymous.channel)
    for service in ('', 'hardware.v1.HardwareService'):
        assert stub.Check(health_pb2.HealthCheckRequest(service=service)).status == health_pb2.HealthCheckResponse.SERVING

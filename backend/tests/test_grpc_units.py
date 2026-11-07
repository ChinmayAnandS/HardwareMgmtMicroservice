"""gRPC-layer rules that need no database: permissions, validation, request numbers, conversion."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from hardware.v1 import hardware_pb2 as pb
from app import convert, models
from app.auth import check_team_code, client_ip, peer_host
from app.domain import Conflict
from app.service import parse, request_number
from app.teams import OPERATIONS, TEAMS, allowed

SERVICE = '/hardware.v1.HardwareService/'
ALL_RPCS = {m.name for m in pb.DESCRIPTOR.services_by_name['HardwareService'].methods}


class Context:
    def __init__(self, *metadata, peer='ipv4:10.0.0.5:4455'):
        self._metadata, self._peer = metadata, peer

    def invocation_metadata(self):
        return self._metadata

    def peer(self):
        return self._peer


def test_every_rpc_is_covered_by_a_permission_decision():
    covered = {name for names in OPERATIONS.values() for name in names}
    admin_only = {'CreateHardware', 'UpdateHardware', 'RetireHardware', 'AdjustHardware', 'CorrectCheckout'}
    assert covered | admin_only == ALL_RPCS
    assert not covered & admin_only


def test_admin_team_may_call_everything():
    assert all(allowed('hardware-admin', SERVICE + name) for name in ALL_RPCS)


@pytest.mark.parametrize('team, granted', [
    ('projects-team', {'read', 'allocate', 'checkout', 'return'}),
    ('front-end', {'read', 'allocate', 'checkout', 'return'}),
    ('users-team', {'read'}),
])
def test_team_permissions(team, granted):
    expected = {name for op in granted for name in OPERATIONS[op]}
    assert {name for name in ALL_RPCS if allowed(team, SERVICE + name)} == expected


def test_users_team_is_read_only_and_does_not_crash():
    assert allowed('users-team', SERVICE + 'ListHardware')
    assert not allowed('users-team', SERVICE + 'CreateCheckout')


def test_other_services_are_not_open_to_teams():
    assert not allowed('hardware-admin', '/hardware.v1.AuthService/Me')
    assert not allowed('hardware-admin', '/hardware.v1.OpsService/Ready')


def test_every_team_declares_its_code_variable():
    assert all(team['env'].startswith('TEAM_CODE_') for team in TEAMS.values())


def test_team_code_check_is_exact_and_rejects_unknown_teams():
    cfg = SimpleNamespace(team_codes={'front-end': 'f' * 24})
    assert check_team_code(cfg, 'front-end', 'f' * 24)
    assert not check_team_code(cfg, 'front-end', 'g' * 24)
    assert not check_team_code(cfg, 'nobody', '\0' * 24)
    assert not check_team_code(cfg, 'projects-team', 'f' * 24)


@pytest.mark.parametrize('peer, host', [
    ('ipv4:172.18.0.5:41234', '172.18.0.5'),
    ('ipv6:%5B::1%5D:55', '::1'),
    ('ipv6:[2001:db8::7]:443', '2001:db8::7'),
])
def test_peer_host(peer, host):
    assert peer_host(peer) == host


def test_client_ip_trusts_only_the_proxy_added_entry_in_production():
    ctx = Context(('x-forwarded-for', '6.6.6.6, 203.0.113.9:5000'))
    assert client_ip(ctx, 'production') == '203.0.113.9'
    assert client_ip(ctx, 'local') == '10.0.0.5'


def test_parse_reports_missing_fields_as_invalid_argument():
    with pytest.raises(Conflict) as e:
        parse(models.Adjust, pb.AdjustHardwareRequest(hardware_id='h', expected_version=1, reason='Purchase', delta=1))
    assert e.value.code == 'INVALID_ARGUMENT' and 'confirmed' in str(e.value)


def test_parse_keeps_zero_and_false_because_optional_fields_track_presence():
    data = parse(models.Allocate, pb.AllocateHardwareRequest(hardware_id='h', project_id='p', quantity=0, reason='Release'))
    assert data.quantity == 0 and data.expected_version is None
    with pytest.raises(Conflict):
        parse(models.Retire, pb.RetireHardwareRequest(hardware_id='h', expected_version=1, reason='Retire', confirmed=False))


def test_parse_rejects_bad_values():
    for request in (pb.CreateHardwareRequest(name='x', category='y', asset_tag='z', total=-1, reason='Invalid'),
                    pb.CreateHardwareRequest(name='x', category='y', asset_tag='z', total=1, reason='no')):
        with pytest.raises(Conflict) as e:
            parse(models.Create, request)
        assert e.value.code == 'INVALID_ARGUMENT'


def test_hardware_queries_have_defaults_and_validate_choices():
    q = parse(models.HardwareQuery, pb.ListHardwareRequest())
    assert (q.page, q.size, q.sort, q.direction) == (1, 25, 'name_key', 'asc')
    for bad in (pb.ListHardwareRequest(sort='$where'), pb.ListHardwareRequest(size=101), pb.ListHardwareRequest(page=0)):
        with pytest.raises(Conflict):
            parse(models.HardwareQuery, bad)


def test_checkout_query_defaults_to_outstanding_only():
    assert parse(models.CheckoutQuery, pb.ListCheckoutsRequest()).outstanding is True
    assert parse(models.CheckoutQuery, pb.ListCheckoutsRequest(outstanding=False)).outstanding is False


def test_transaction_timestamps_become_aware_datetimes():
    start = Timestamp()
    start.FromDatetime(datetime(2026, 1, 1, tzinfo=timezone.utc))
    q = parse(models.TransactionQuery, pb.ListTransactionsRequest(start=start))
    assert q.start == datetime(2026, 1, 1, tzinfo=timezone.utc) and q.end is None


class Body(SimpleNamespace):
    pass


def test_request_number_rules():
    assert request_number(Context(), Body(request_id='REQ-1')) == 'REQ-1'
    assert request_number(Context(('idempotency-key', 'K-1')), Body(request_id=None)) == 'K-1'
    assert request_number(Context(('idempotency-key', 'SAME')), Body(request_id='SAME')) == 'SAME'
    for ctx, body in ((Context(), Body(request_id=None)),
                      (Context(('idempotency-key', 'A')), Body(request_id='B')),
                      (Context(('idempotency-key', 'has space')), Body(request_id=None))):
        with pytest.raises(Conflict) as e:
            request_number(ctx, body)
        assert e.value.code == 'INVALID_ARGUMENT'


def test_hardware_conversion_adds_derived_stock_numbers_and_drops_internal_keys():
    h = convert.hardware({'_id': 'h1', 'name': 'Laptop', 'category': 'Computer', 'description': '', 'asset_tag': 'A',
                          'serial': '', 'status': 'active', 'total': 10, 'reserved': 3, 'out': 1, 'version': 4,
                          'name_key': 'laptop', 'asset_key': 'a'})
    assert (h.id, h.total, h.reserved, h.out, h.available, h.unallocated) == ('h1', 10, 3, 1, 9, 7)


def test_checkout_conversion_has_outstanding_and_timestamp():
    c = convert.checkout({'_id': 'c1', 'project_id': 'p', 'hardware_id': 'h', 'user_id': 'u', 'quantity': 3,
                          'returned': 1, 'version': 2, 'created_at': '2026-11-04T15:30:00+00:00'})
    assert c.outstanding == 2 and c.created_at.seconds == int(datetime(2026, 11, 4, 15, 30, tzinfo=timezone.utc).timestamp())


def test_audit_conversion_keeps_null_project_unset_and_snapshots_as_structs():
    event = convert.to_pb(pb.AuditEvent, {
        '_id': 'e', 'actor': 'team:x', 'operation': 'create', 'entity': 'h', 'hardware_id': 'h', 'project_id': None,
        'user_id': None, 'reason': 'Because', 'timestamp': datetime(2026, 1, 1, tzinfo=timezone.utc),
        'before': {}, 'after': {'hardware': {'_id': 'h', 'total': 5}}, 'request_id': 'abc'})
    assert not event.HasField('project_id') and not event.HasField('user_id')
    assert event.after['hardware']['total'] == 5


def test_vendor_rows_accept_decimals_and_missing_columns():
    row = convert.to_pb(pb.VendorAlternative, {'vendor': 'Dell', 'quantity': 42, 'unit_price': Decimal('1250.50')})
    assert row.vendor == 'Dell' and row.unit_price == 1250.5 and not row.HasField('monthly_charges')


class LoggedContext(Context):
    def abort(self, code, message):
        raise RuntimeError(f'aborted {code.name}: {message}')


class FakeAuth:
    def __init__(self, actor=None, error=None):
        self.actor, self.error = actor, error

    def authenticate(self, context, method):
        if self.error:
            raise self.error
        return self.actor


def servicer(auth, behaviour):
    from app.service import guarded

    class Servicer:
        def __init__(self):
            self.auth = auth

        @guarded()
        def Probe(self, request, context, actor):
            return behaviour()

    return Servicer()


def test_each_call_logs_one_line_with_actor_status_and_request_id(caplog):
    import logging
    secret = 'Basic aGFyZHdhcmUtYWRtaW46dG9wLXNlY3JldC1jb2Rl'
    context = LoggedContext(('authorization', secret))
    request = SimpleNamespace(request_id='REQ-1')
    with caplog.at_level(logging.INFO, logger='hardware'):
        assert servicer(FakeAuth('team:projects-team'), lambda: 'ok').Probe(request, context) == 'ok'
    [line] = [r for r in caplog.records if r.name == 'hardware']
    text = line.getMessage()
    assert line.levelno == logging.INFO
    assert 'rpc=Probe' in text and 'actor=team:projects-team' in text and 'status=OK' in text
    assert "request_id='REQ-1'" in text and 'peer=10.0.0.5' in text and ' ms=' in text
    assert secret not in text and 'aGFyZHdhcmUt' not in text


def test_failures_log_the_status_and_reason_but_never_forge_extra_lines(caplog):
    import logging
    context = LoggedContext(('authorization', 'Basic c2VjcmV0'))
    hostile = Conflict('Team evil\nrpc=Fake actor=admin status=OK is not allowed', 'PERMISSION_DENIED')
    with caplog.at_level(logging.INFO, logger='hardware'):
        with pytest.raises(RuntimeError, match='aborted PERMISSION_DENIED'):
            servicer(FakeAuth(error=hostile), lambda: None).Probe(SimpleNamespace(request_id=''), context)
    [line] = [r for r in caplog.records if r.name == 'hardware']
    text = line.getMessage()
    assert line.levelno == logging.WARNING
    assert 'status=PERMISSION_DENIED' in text and 'actor=-' in text
    assert '\n' not in text and 'c2VjcmV0' not in text


def test_server_side_failures_log_at_error_level(caplog):
    import logging

    def boom():
        raise ValueError('kaboom')
    with caplog.at_level(logging.INFO, logger='hardware'):
        with pytest.raises(RuntimeError, match='aborted INTERNAL'):
            servicer(FakeAuth('team:front-end'), boom).Probe(SimpleNamespace(request_id=''), LoggedContext())
    rpc_lines = [r for r in caplog.records if r.name == 'hardware' and 'rpc=Probe' in r.getMessage()]
    assert rpc_lines and rpc_lines[0].levelno == logging.ERROR and 'status=INTERNAL' in rpc_lines[0].getMessage()

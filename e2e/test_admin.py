import base64
import os
import sys
from pathlib import Path
from uuid import uuid4
import grpc
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc  # noqa: E402


def grpc_channel():
    """The API under test: plaintext inside Compose, TLS (TEST_GRPC_TLS=1) against a deployed app."""
    target = os.environ.get('TEST_GRPC_TARGET', 'api:50051')
    if os.environ.get('TEST_GRPC_TLS') == '1':
        return grpc.secure_channel(target, grpc.ssl_channel_credentials())
    return grpc.insecure_channel(target)


def test_admin_ui_grpc_web_roundtrip():
    """Browser -> Nginx -> Envoy (gRPC-Web) -> gRPC API -> MongoDB, driven through the real UI."""
    base = os.environ.get('TEST_BASE_URL', 'http://localhost:8080')
    code = Path(os.environ['DEV_LOGIN_CODE_FILE']).read_text().strip()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1100, 'height': 800})
        page.on('dialog', lambda dialog: dialog.accept())
        rpc_paths = []
        page.on('request', lambda r: rpc_paths.append(r.url) if '/hardware.v1.' in r.url else None)
        page.goto(base + '/?local=1')
        page.get_by_label('Local development code').fill(code)
        page.get_by_role('button', name='Sign in locally').click()
        expect(page.get_by_role('heading', name='Inventory dashboard')).to_be_visible()
        page.get_by_role('button', name='Hardware', exact=True).click()
        page.get_by_role('button', name='Add hardware', exact=True).click()
        tag = 'E2E-' + uuid4().hex[:8]
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('Name', exact=True).fill(tag)
        dialog.get_by_label('Category', exact=True).fill('Testing')
        dialog.get_by_label('Unique asset tag').fill(tag)
        dialog.get_by_label('Total units').fill('4')
        dialog.get_by_label('Reason (required)').fill('Browser inventory test')
        dialog.get_by_role('button', name='Review and confirm').click()
        expect(dialog).not_to_be_visible()
        page.get_by_label('Name / asset tag prefix').fill(tag)
        page.get_by_role('button', name='Search / filter').click()
        row = page.locator('tr').filter(has_text=tag)
        expect(row).to_have_count(1)
        row.get_by_role('button', name='Adjust stock').click()
        dialog.get_by_label('Change in total').fill('-1')
        dialog.get_by_label('Reason (required)').fill('Remove damaged unit')
        dialog.get_by_role('button', name='Review and confirm').click()
        expect(dialog).not_to_be_visible()
        expect(row.locator('td').nth(2)).to_have_text('3')
        expect(row.locator('td').nth(3)).to_have_text('3 / 3')
        row.get_by_role('button', name='Audit').click()
        expect(page.get_by_text('adjust', exact=False).first).to_be_visible()
        # Every call went to a gRPC method, and no credentials or tokens persist in web storage or cookies.
        assert rpc_paths and all('/hardware.v1.' in url for url in rpc_paths)
        assert not any('/api/' in url for url in rpc_paths)
        assert page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length') == 0
        assert page.context.cookies() == []
        page.get_by_role('button', name='Sign out', exact=True).click()
        expect(page.get_by_role('heading', name='Administrator sign in')).to_be_visible()
        browser.close()


def test_native_grpc_client_reaches_the_container():
    """What other teams do: a plain gRPC client with team credentials against the published port."""
    team = Path(os.environ['TEAM_CODE_HARDWARE_ADMIN_FILE']).read_text().strip()
    credentials = 'Basic ' + base64.b64encode(f'hardware-admin:{team}'.encode()).decode()
    with grpc_channel() as channel:
        stub = rpc.HardwareServiceStub(channel)
        metadata = (('authorization', credentials),)
        tag = 'GRPC-' + uuid4().hex[:8]
        created = stub.CreateHardware(pb.CreateHardwareRequest(
            name=tag, category='Testing', asset_tag=tag, total=2, reason='Native gRPC smoke test',
            request_id='e2e-' + tag), metadata=metadata, timeout=30)
        assert (created.total, created.available, created.unallocated) == (2, 2, 2)
        found = stub.ListHardware(pb.ListHardwareRequest(q=tag), metadata=metadata, timeout=30)
        assert [h.id for h in found.items] == [created.id]
        try:
            stub.ListHardware(pb.ListHardwareRequest(), timeout=30)
            raise AssertionError('anonymous call should be refused')
        except grpc.RpcError as e:
            assert e.code() == grpc.StatusCode.UNAUTHENTICATED
        ready = rpc.OpsServiceStub(channel).Ready(pb.ReadyRequest(), timeout=30)
        assert ready.status == 'ready'


def test_admin_can_check_out_and_return_from_the_ui():
    """An admin checks out reserved stock from the Checkout management screen, then returns part of it."""
    base = os.environ.get('TEST_BASE_URL', 'http://localhost:8080')
    code = Path(os.environ['DEV_LOGIN_CODE_FILE']).read_text().strip()
    team = Path(os.environ['TEAM_CODE_HARDWARE_ADMIN_FILE']).read_text().strip()
    credentials = 'Basic ' + base64.b64encode(f'hardware-admin:{team}'.encode()).decode()
    metadata = (('authorization', credentials),)
    tag = 'CO-' + uuid4().hex[:8]
    with grpc_channel() as channel:
        stub = rpc.HardwareServiceStub(channel)
        pool = stub.CreateHardware(pb.CreateHardwareRequest(
            name=tag, category='Testing', asset_tag=tag, total=5, reason='Checkout UI test',
            request_id='e2e-create-' + tag), metadata=metadata, timeout=30)
        stub.AllocateHardware(pb.AllocateHardwareRequest(
            hardware_id=pool.id, project_id='proj-' + tag, quantity=3, reason='Checkout UI test',
            request_id='e2e-alloc-' + tag), metadata=metadata, timeout=30)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1100, 'height': 800})
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto(base + '/?local=1')
        page.get_by_label('Local development code').fill(code)
        page.get_by_role('button', name='Sign in locally').click()
        page.get_by_role('button', name='Hardware', exact=True).click()
        page.get_by_label('Hardware ID (exact)').fill(pool.id)
        page.get_by_role('button', name='Search / filter').click()
        expect(page.locator('tr').filter(has_text=tag)).to_have_count(1)
        page.get_by_label('Hardware ID (exact)').fill('does-not-exist')
        page.get_by_role('button', name='Search / filter').click()
        expect(page.get_by_text('No matching records.')).to_be_visible()
        page.get_by_role('button', name='Checkout management').click()
        page.get_by_role('button', name='Check out stock', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('Hardware ID').fill(pool.id)
        dialog.get_by_label('External project ID').fill('proj-' + tag)
        dialog.get_by_label('Borrower ID').fill('student-1')
        dialog.get_by_label('Quantity to check out').fill('2')
        dialog.get_by_label('Reason (required)').fill('Lab session')
        dialog.get_by_role('button', name='Review and confirm').click()
        expect(dialog).not_to_be_visible()
        row = page.locator('tr').filter(has_text='proj-' + tag)
        expect(row).to_have_count(1)
        expect(row.locator('td').nth(2)).to_have_text('2 / 0')
        row.get_by_role('button', name='Return').first.click()
        dialog.get_by_label('Quantity to return').fill('1')
        dialog.get_by_label('Reason (required)').fill('Returned after lab')
        dialog.get_by_role('button', name='Review and confirm').click()
        expect(dialog).not_to_be_visible()
        expect(row.locator('td').nth(2)).to_have_text('2 / 1')
        browser.close()


def test_slow_list_load_does_not_add_a_second_table_after_a_faster_search():
    """Two overlapping screen loads must leave one table (the later one), however the replies are ordered.

    The Hardware tab starts a list load; an exact-ID search right after starts a second one. Against a
    deployed API the list reply can arrive last, which used to append a second table under the first.
    The list request is held back here so that order is guaranteed.
    """
    base = os.environ.get('TEST_BASE_URL', 'http://localhost:8080')
    code = Path(os.environ['DEV_LOGIN_CODE_FILE']).read_text().strip()
    team = Path(os.environ['TEAM_CODE_HARDWARE_ADMIN_FILE']).read_text().strip()
    credentials = 'Basic ' + base64.b64encode(f'hardware-admin:{team}'.encode()).decode()
    tag = 'RACE-' + uuid4().hex[:8]
    with grpc_channel() as channel:
        pool = rpc.HardwareServiceStub(channel).CreateHardware(pb.CreateHardwareRequest(
            name=tag, category='Testing', asset_tag=tag, total=2, reason='Render race test',
            request_id='e2e-race-' + tag), metadata=(('authorization', credentials),), timeout=30)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1100, 'height': 800})
        page.goto(base + '/?local=1')
        page.get_by_label('Local development code').fill(code)
        page.get_by_role('button', name='Sign in locally').click()
        expect(page.get_by_role('heading', name='Inventory dashboard')).to_be_visible()
        held = []
        page.route('**/hardware.v1.HardwareService/ListHardware', lambda route: held.append(route))
        page.get_by_role('button', name='Hardware', exact=True).click()
        page.get_by_label('Hardware ID (exact)').fill(pool.id)
        page.get_by_role('button', name='Search / filter').click()
        expect(page.locator('tr').filter(has_text=tag)).to_have_count(1)
        assert held, 'the list request should have been held back'
        for route in held:
            route.continue_()
        page.wait_for_timeout(1500)
        expect(page.locator('table')).to_have_count(1)
        expect(page.locator('tr').filter(has_text=tag)).to_have_count(1)
        browser.close()

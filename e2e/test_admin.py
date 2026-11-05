import base64
import os
import sys
from pathlib import Path
from uuid import uuid4
import grpc
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc  # noqa: E402


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
    with grpc.insecure_channel(os.environ.get('TEST_GRPC_TARGET', 'api:50051')) as channel:
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

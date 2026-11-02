import os
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect


def test_admin_ui_api_roundtrip():
    base=os.environ.get('TEST_BASE_URL','http://localhost:8080')
    code=Path(os.environ['DEV_LOGIN_CODE_FILE']).read_text().strip()
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1100,'height':800})
        page.on('dialog',lambda dialog:dialog.accept())
        page.goto(base+'/?local=1')
        page.get_by_label('Local development code').fill(code)
        page.get_by_role('button',name='Sign in locally').click()
        expect(page.get_by_role('heading',name='Inventory dashboard')).to_be_visible()
        page.get_by_role('button',name='Hardware',exact=True).click()
        page.get_by_role('button',name='Add hardware',exact=True).click()
        tag='E2E-'+uuid4().hex[:8]
        dialog=page.get_by_role('dialog')
        dialog.get_by_label('Name',exact=True).fill(tag)
        dialog.get_by_label('Category',exact=True).fill('Testing')
        dialog.get_by_label('Unique asset tag').fill(tag)
        dialog.get_by_label('Total units').fill('4')
        dialog.get_by_label('Reason (required)').fill('Browser inventory test')
        dialog.get_by_role('button',name='Review and confirm').click()
        expect(dialog).not_to_be_visible()
        page.get_by_label('Name / asset tag prefix').fill(tag)
        page.get_by_role('button',name='Search / filter').click()
        row=page.locator('tr').filter(has_text=tag)
        expect(row).to_have_count(1)
        row.get_by_role('button',name='Adjust stock').click()
        dialog.get_by_label('Change in total').fill('-1')
        dialog.get_by_label('Reason (required)').fill('Remove damaged unit')
        dialog.get_by_role('button',name='Review and confirm').click()
        expect(dialog).not_to_be_visible()
        expect(row.locator('td').nth(2)).to_have_text('3')
        row.get_by_role('button',name='Audit').click()
        expect(page.get_by_text('adjust',exact=False).first).to_be_visible()
        # No credentials or bearer tokens persist in web storage.
        assert page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length')==0
        cookie=next(c for c in page.context.cookies() if c['name']=='hardware_session')
        assert cookie['httpOnly'] and cookie['sameSite']=='Lax'
        page.get_by_role('button',name='Sign out',exact=True).click()
        expect(page.get_by_role('heading',name='Administrator sign in')).to_be_visible()
        browser.close()

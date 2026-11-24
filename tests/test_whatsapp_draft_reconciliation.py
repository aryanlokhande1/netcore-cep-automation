import json
import os
from io import StringIO
from unittest.mock import AsyncMock

import pytest

from modules.whatsapp import reconciliation
from modules.whatsapp.campaign_list_diagnostic import CampaignListDiagnostic


@pytest.mark.asyncio
@pytest.mark.parametrize(('status', 'answer', 'expected'), [
    ('CAMPAIGN_NAME_UNUSED', 'no', 'CAMPAIGN_NAME_UNUSED'),
    ('CAMPAIGN_LIST_READINESS_FAILED', 'yes', 'CAMPAIGN_NAME_OPERATOR_CONFIRMED_UNUSED'),
    ('CAMPAIGN_SEARCH_CONTROL_NOT_ACTIONABLE', 'no', 'CAMPAIGN_NAME_OPERATOR_DECLINED'),
    ('CAMPAIGN_LIST_READINESS_FAILED', 'timeout', 'CAMPAIGN_NAME_OPERATOR_CONFIRMATION_TIMEOUT'),
    ('CAMPAIGN_NAME_ALREADY_USED', 'yes', 'CAMPAIGN_NAME_ALREADY_USED'),
])
async def test_draft_name_confirmation_only_for_unavailable_list_or_search(status, answer, expected):
    check = AsyncMock(return_value=answer)
    result = await reconciliation.resolve_draft_name_check(status, check)
    assert result == expected
    assert check.await_count == (status in {'CAMPAIGN_LIST_READINESS_FAILED',
                                          'CAMPAIGN_SEARCH_CONTROL_NOT_ACTIONABLE'})


@pytest.mark.asyncio
async def test_manual_name_confirmation_shows_exact_name_and_requires_yes():
    from browser.operator_console import OperatorConsole

    keys = iter('y\n')
    output = StringIO()
    console = OperatorConsole(read_key=lambda: next(keys), output=output)
    assert await console.confirm_unused_campaign_name('exact_test_name', 30) == 'yes'
    assert 'exact_test_name' in output.getvalue()


def test_campaigns_reload_uses_only_observed_get_document():
    diagnostic = CampaignListDiagnostic('tenant.invalid')
    for method, expected in [('GET', '/campaign/unified'), ('POST', None)]:
        diagnostic.observe({'method': 'Network.requestWillBeSent', 'params': {
            'type': 'Document', 'requestId': 'doc', 'request': {
                'url': 'https://tenant.invalid/campaign/unified?token=private', 'method': method}}})
        assert diagnostic.last_document_get_path == expected


def test_passive_campaign_diagnostic_records_only_sanitized_metadata(tmp_path):
    diagnostic = CampaignListDiagnostic('tenant.invalid')
    diagnostic.observe({'method': 'Network.requestWillBeSent', 'params': {
        'type': 'XHR', 'requestId': 'internal-id',
        'request': {'url': 'https://tenant.invalid/campaign/123?token=private-value',
                    'headers': {'Cookie': 'private-cookie'}, 'postData': 'private-body'}}})
    diagnostic.observe({'method': 'Network.responseReceived', 'params': {
        'requestId': 'internal-id', 'response': {'status': 503, 'headers': {'Secret': 'private-header'}}}})
    diagnostic.observe({'method': 'Network.loadingFailed', 'params': {
        'requestId': 'internal-id', 'errorText': 'net::ERR_CONNECTION_RESET'}})
    diagnostic.observe({'method': 'Runtime.consoleAPICalled', 'params': {
        'type': 'error', 'args': [{'value': 'private-console'}]}})
    path = tmp_path / 'diagnostic.json'
    diagnostic.save(path)
    serialized = path.read_text(encoding='utf-8')
    assert 'private' not in serialized and 'internal-id' not in serialized
    result = json.loads(serialized)
    assert result['requests'][0]['path'] == '/campaign/[redacted]'
    assert result['requests'][0]['status'] == 503
    assert result['requests'][0]['reason'] == 'net::ERR_CONNECTION_RESET'
    assert result['browser_errors'][0]['category'] == 'CONSOLE_ERROR'


def test_campaign_list_readiness_requires_rendered_list_and_usable_search():
    ready = reconciliation.campaign_list_ready
    state = dict(busy=False, table_count=1, visible_rows=1, no_results=False,
                 search_count=1, search="input", shape={"ready_state": "complete"})
    assert ready(state)
    assert not ready({**state, "table_count": 0})
    assert not ready({**state, "search_count": 0})
    assert not ready({**state, "busy": True})
    assert not ready({**state, "visible_rows": 0})
    assert ready({**state, "visible_rows": 0, "no_results": True})
    assert ready({**state, "table_count": 0, "visible_rows": 0, "explicitly_empty_campaigns": True})


def test_campaign_match_requires_timestamp_when_id_is_unavailable():
    from datetime import datetime, timezone
    saved = datetime(2026, 11, 21, 10, 50, tzinfo=timezone.utc)
    row = {"id": "123", "created": "2026-11-21 10:50:25"}
    assert reconciliation.select_record([row], saved, None) is None
    assert reconciliation.select_record([row], saved, "UTC") == row
    assert reconciliation.select_record([row], saved, None, campaign_id="123") == row
    assert reconciliation.select_record([row], saved, "UTC", campaign_id="456") is None


async def test_readiness_rechecked_after_operator_confirmation(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    url = 'https://tprappin2r.netcoresmartech.com/bajajfinservmarket/admin/index.php/campaign/unified'
    events = []
    state = dict(busy=False, table_count=1, visible_rows=1, no_results=False,
                 search_count=1, search='input', shape={'ready_state': 'complete'},
                 search_trigger_count=0, campaigns_link_count=0, search_trigger=None)

    class Page:
        async def url(self):
            return url

        async def evaluate(self, script):
            return dict(state)

        def locator(self, selector):
            pytest.fail('Search must not be invoked after readiness is lost')

    class Guard:
        session_states = {'session': SimpleNamespace(document_generation=1)}

        async def send(self, method, *, session):
            return {'frameTree': {'frame': {'url': url}}}

    class Binding:
        session = 'session'

        async def require_current(self, browser):
            pass

    async def healthy(browser):
        pass

    async def confirm():
        assert events == ['campaign_list_ready']
        state.update(search_count=0, search=None)
        return 'yes'

    monkeypatch.setattr(reconciliation, 'reject_security_negatives', healthy)
    browser = SimpleNamespace(page=Page(), guard=Guard(), budget=lambda: None,
                              policy=SimpleNamespace(permits=lambda value: value == url),
                              trace=SimpleNamespace(agent_event=lambda name, **facts: events.append(name)))
    result = await reconciliation.reconcile_campaign(
        browser, Binding(), SimpleNamespace(campaign_name='draft-name'), 'source',
        datetime(2026, 11, 21, 10, 50, tzinfo=timezone.utc), confirm=confirm)
    assert result['status'] == 'CAMPAIGN_LIST_READINESS_CHANGED_AFTER_CONFIRMATION'
    assert 'campaign_list_view_confirmed' not in events



@pytest.mark.skipif(os.getenv('RUN_DRAFT_DOM_FIXTURE') != '1', reason='Explicit loopback Chromium test')
async def test_real_chromium_campaign_navigation_readiness_and_preview_readback():
    from app.config import Config, OriginPolicy
    from app.trace import Trace
    from browser.stagehand_client import StagehandClient
    from test_whatsapp_navigation_repair import navigation_server

    with navigation_server('valid') as server:
        browser = StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'], headless=False,
            login_url='about:blank', expected_host='127.0.0.1', max_actions=50, timeout_seconds=45),
            OriginPolicy({server.origin}), Trace(), fixture=True)
        try:
            await browser.start()
            await browser.page.goto(server.dashboard)
            async def render(html):
                await browser.page.evaluate('document.body.innerHTML = ' + json.dumps(html))

            script = reconciliation.CAMPAIGN_LIST.replace('SETTINGS', json.dumps(
                {'name': 'draft-name', 'campaignsPath': '/campaign/unified'}))
            await render('<nav><button type="button">Engage</button>'
                         '<a href="/campaign/unified">Campaigns</a></nav>')
            root = await browser.page.evaluate(script)
            assert root['campaigns_link_count'] == 1
            assert root['engage_count'] == 1 and root['sidebar_campaigns_link']
            assert not reconciliation.campaign_list_ready(root)

            await render('<main><h1>Campaigns</h1><button type="button"><mat-icon>search</mat-icon></button>'
                         '<table><thead><tr><th>Campaign name</th></tr></thead>'
                         '<tbody><tr><td>draft-name</td></tr></tbody></table></main>')
            trigger = await browser.page.evaluate(script)
            assert reconciliation.campaign_list_ready(trigger, allow_trigger=True)
            assert trigger['shape']['campaigns_heading']
            assert trigger['shape']['table_headers'] == ['campaign name']
            assert not reconciliation.campaign_list_ready(trigger)

            await render('<main><h1>Campaigns</h1><input type="search" aria-label="Search campaigns">'
                         '<table><thead><tr><th>Campaign name</th><th>Status</th></tr></thead>'
                         '<tbody><tr><td>draft-name</td><td>Draft</td></tr></tbody></table></main>')
            ready = await browser.page.evaluate(script)
            assert reconciliation.campaign_list_ready(ready)
            assert ready['visible_rows'] == 1

            await render('<main><input type="search" aria-label="Search campaigns">'
                         '<table><thead><tr><th>Campaign name</th></tr></thead>'
                         '<tbody><tr><td>No records found</td></tr></tbody></table></main>')
            empty = await browser.page.evaluate(script)
            assert empty['no_results'] and empty['visible_rows'] == 0

            await render('<main><header><h1>draft-name</h1><span>DRAFT</span></header><div>Campaign ID: 18023</div>'
                         '<section><h2>Schedule</h2><div>When to send:</div><div>Send later</div>'
                         '<p>Nov 25, 2026 06:00 PM</p><p>Timezone: Asia/Calcutta</p></section></main>')
            detail = await browser.page.evaluate(reconciliation.DETAIL.replace(
                'SETTINGS', json.dumps({'name': 'draft-name'})))
            assert not detail['preview_visible'] and detail['campaign_name_visible'] and detail['draft_badge']
            assert detail['ids'] == ['18023'] and detail['statuses'] == ['Draft']
            assert detail['dates'] == ['Nov 25, 2026 06:00 PM'] and detail['send_later_label_visible']
            assert detail['zones'] == ['Asia/Calcutta']

            await render('<main><h1>draft-name</h1><p>Campaign ID: 18023</p><div role="alert">Draft saved</div></main>')
            toast = await browser.page.evaluate(reconciliation.DETAIL.replace('SETTINGS', json.dumps({'name': 'draft-name'})))
            assert not toast['draft_badge'] and toast['statuses'] == [] and not toast['send_later_label_visible']
        finally:
            await browser.close()


def test_campaigns_handoff_requires_usable_list_on_exact_tenant_route():
    from urllib.parse import urlsplit
    from modules.whatsapp.contract import SETUP_URL
    from modules.whatsapp.reconciliation import campaigns_handoff_facts

    entry = urlsplit(SETUP_URL)
    admin = entry.path.split('/campaign/', 1)[0]
    path = admin + '/campaign/unified'
    url = entry._replace(path=path, query='sensitive=test-value', fragment='').geturl()
    blank = {'shape': {'ready_state': 'complete', 'campaigns_heading': True}, 'busy': False,
             'table_count': 0, 'visible_rows': 0, 'no_results': False, 'search_count': 0, 'search': None}
    assert not campaigns_handoff_facts(url, path, blank)['ready']
    usable = {**blank, 'table_count': 1, 'visible_rows': 1,
              'search_trigger_count': 1, 'search_trigger': 'opaque-css'}
    observed = campaigns_handoff_facts(url, path, usable)
    assert observed['ready'] and observed['visible_rows'] == 1
    assert 'test-value' not in str(observed) and 'opaque-css' not in str(observed)
    assert not campaigns_handoff_facts(entry._replace(path=admin + '/').geturl(), path, usable)['ready']
    assert not campaigns_handoff_facts(url.replace(entry.netloc, 'unowned.invalid'), path, usable)['ready']
    assert not campaigns_handoff_facts(url, path, {**usable, 'busy': True})['ready']

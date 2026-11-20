from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from browser.protected_tabs import TabProtectionError, bind_confirmed_primary_tab
from modules.whatsapp.workflow import human_assisted_authorization


def test_operator_confirmation_enables_only_explicit_manual_mode():
    args = SimpleNamespace(authorized_live=True, authorized_inference=True, manual_form_test=True)
    assert not human_assisted_authorization(args, verified=False, operator_confirmed=True).edits
    args.operator_confirmed_login = True
    assert not human_assisted_authorization(args, verified=False).edits
    authorization = human_assisted_authorization(args, verified=False, operator_confirmed=True)
    assert authorization.edits and authorization.manual_form_test and not authorization.persistence


@pytest.mark.asyncio
@pytest.mark.parametrize('change,reason', [
    ('active_page', 'OPERATOR_CONFIRMED_TAB_OR_SESSION_CHANGED'),
    ('session', 'OPERATOR_CONFIRMED_TAB_OR_SESSION_CHANGED'),
    ('browser', 'OPERATOR_CONFIRMED_TAB_OR_SESSION_CHANGED'),
    ('redirect', 'OPERATOR_CONFIRMED_SESSION_REDIRECTED_TO_SIGN_IN'),
])
async def test_confirmed_tab_fails_closed_after_identity_change(change, reason):
    owner = SimpleNamespace(target='target', session='session', generation=1)
    guard = SimpleNamespace(primary_session='session', blocking_hosts=[], failed=False,
                            session_states={'session': owner}, current_sessions={'target': 'session'},
                            primary_observable=lambda: True, navigation_observer=None)
    page = SimpleNamespace(page_id='owned')
    read_active = AsyncMock(return_value=page)
    browser = SimpleNamespace(guard=guard, page=page, browser=SimpleNamespace(
        context=SimpleNamespace(active_page=read_active)), trace=SimpleNamespace(lifecycle=Mock()),
        config=SimpleNamespace(login_url='https://login.netcoresmartech.com/'))
    binding = bind_confirmed_primary_tab(browser)
    await binding.require_current(browser)
    if change == 'active_page':
        read_active.return_value = SimpleNamespace(page_id='other')
    elif change == 'session':
        guard.current_sessions['target'] = 'other'
    elif change == 'browser':
        browser.browser = object()
    elif change == 'redirect':
        guard.navigation_observer('DOCUMENT_REQUEST', 'session', browser.config.login_url)
        guard.navigation_observer('DOCUMENT_COMMITTED', 'session', 'https://expected.invalid/')
    with pytest.raises(TabProtectionError, match=reason):
        await binding.require_current(browser)


@pytest.mark.asyncio
async def test_confirmed_batch_completes_teacher_before_next_run(monkeypatch, capsys):
    from app.cli import build_parser, run
    from modules.whatsapp import workflow
    calls = []
    outcomes = iter([('STUCK', 'ANALYZED'), ('COMPLETE', 'ANALYZED'), ('COMPLETE', 'ANALYZED')])

    async def attempt(_args):
        status, teacher = next(outcomes)
        calls.append((status, teacher))
        return {'status': status, 'teacher_status': teacher, 'cleanup': 'CLOSED_AND_PROFILE_REMOVED'}

    monkeypatch.setattr(workflow, 'run_human_assisted', attempt)
    args = build_parser().parse_args(['whatsapp-human-assisted', '--operator-confirmed-login',
                                      '--operator-confirmed-runs', '3'])
    await run(args)
    assert len(calls) == 3
    assert capsys.readouterr().out.count('OPERATOR_RUN_SUMMARY') == 3


@pytest.mark.parametrize('path,allowed', [
    ('/bajajfinservmarket/admin/index.php', True),
    ('/bajajfinservmarket/admin/index.php/', True),
    ('/bajajfinservmarket/admin/index.php/campaign', True),
    ('/bajajfinservmarket/other', False),
    ('/bajajfinservmarket/admin/index.php-other', False),
    ('/bajajfinservmarket/admin/index.php/%2e%2e/other', False),
    ('/other/admin/index.php', False),
])
def test_operator_tenant_route_has_exact_admin_boundary(path, allowed):
    from modules.whatsapp.workflow import operator_tenant_route_allowed

    host = 'tprappin2r.netcoresmartech.com'
    assert operator_tenant_route_allowed('https://' + host + path) is allowed
    assert not operator_tenant_route_allowed('http://' + host + path)
    assert not operator_tenant_route_allowed('https://' + host + '.invalid' + path)


@pytest.mark.asyncio
async def test_write_guard_surfaces_missing_security_scope_without_installing_write_guard(monkeypatch):
    from modules.whatsapp import tools as implementation
    from modules.whatsapp.contract import V2Authorization, V2Stop
    from modules.whatsapp.safety import NEGATIVES

    tools = object.__new__(implementation.V2BrowserTools)
    tools.browser = SimpleNamespace(trace=SimpleNamespace(agent_event=Mock()))
    tools.authorization = V2Authorization(live=True, inference=True, edits=True, manual_form_test=True)
    tools.read_only = False
    tools.stage = 'setup'
    tools.manual_authentication_verified = True
    tools._last_protected_route = {'fragment_class': 'SETUP'}
    tools.protect = AsyncMock(return_value='current_document')
    tools.install_guard = AsyncMock()
    snapshot = dict.fromkeys(NEGATIVES, False)
    snapshot.update(negative_scope_complete=False, _scope_diagnostics={'inaccessible_shadow_roots': 1})
    monkeypatch.setattr(implementation, 'read_safety_snapshot', AsyncMock(return_value=snapshot))

    with pytest.raises(V2Stop, match='WRITE_SECURITY_SCOPE_NOT_CERTIFIED'):
        await tools.authorize_write()
    tools.install_guard.assert_not_awaited()
    event = tools.browser.trace.agent_event.call_args
    assert event.args == ('setup_write_security_gate',)
    assert event.kwargs['failed_predicates'] == ['negative_scope_complete']
    assert event.kwargs['scope_reason_counts']['inaccessible_shadow_roots'] == 1


@pytest.mark.asyncio
async def test_readonly_preflight_checks_current_setup_document_without_installing_write_guard(monkeypatch, capsys):
    from modules.whatsapp import tools as implementation
    from modules.whatsapp.contract import V2Stop
    from modules.whatsapp.safety import NEGATIVES

    tools = object.__new__(implementation.V2BrowserTools)
    guard = SimpleNamespace(target_aliases={'tab': 4}, session_aliases={'cdp': 1})
    tools.browser = SimpleNamespace(guard=guard, trace=SimpleNamespace(agent_event=Mock()))
    tools.read_only = True
    tools._last_protected_route = {'fragment_class': 'SETUP'}
    current = ('tab', 'cdp', 1, 4, 'frame', 'setup')
    tools.protect = AsyncMock(return_value=current)
    tools.install_guard = AsyncMock()
    snapshot = dict.fromkeys(NEGATIVES, False)
    snapshot.update(negative_scope_complete=False, _scope_diagnostics={'inaccessible_shadow_roots': 1})
    read = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(implementation, 'read_safety_snapshot', read)

    with pytest.raises(V2Stop, match='WRITE_DOCUMENT_CHANGED'):
        await tools.check_write_scope(require_setup_route=True, preflight=True,
                                      expected_document=('tab', 'cdp', 1, 3, 'frame', 'login'))
    read.assert_not_awaited()
    with pytest.raises(V2Stop, match='WRITE_SECURITY_SCOPE_NOT_CERTIFIED'):
        await tools.check_write_scope(require_setup_route=True, preflight=True, expected_document=current)
    read.assert_awaited_once_with(tools.browser, include_inventory=True)
    tools.install_guard.assert_not_awaited()
    output = capsys.readouterr().out
    assert '"document_generation": 4' in output
    assert '"failed_predicates": ["negative_scope_complete"]' in output
    assert '"inaccessible_shadow_roots": 1' in output


@pytest.mark.asyncio
async def test_bound_operator_setup_preflight_keeps_protection_without_whole_page_inventory(monkeypatch):
    from modules.whatsapp import tools as implementation

    tools = object.__new__(implementation.V2BrowserTools)
    tools.browser = SimpleNamespace(trace=SimpleNamespace(agent_event=Mock()))
    tools.operator_setup_binding = object()
    tools._last_protected_route = {'fragment_class': 'SETUP'}
    current = ('tab', 'cdp', 1, 4, 'frame', 'setup')
    tools.protect = AsyncMock(return_value=current)
    tools.install_guard = AsyncMock()
    inventory = AsyncMock(side_effect=AssertionError('full inventory outside scoped path'))
    monkeypatch.setattr(implementation, 'read_safety_snapshot', inventory)

    assert await tools.check_write_scope(require_setup_route=True, preflight=True,
                                        expected_document=current) == current
    assert tools.protect.await_count == 2
    inventory.assert_not_awaited()
    tools.install_guard.assert_not_awaited()


def test_exhausted_tag_does_not_prevent_independent_ga_tracking():
    from modules.whatsapp.workflow import setup_attempt_verdict
    tools = SimpleNamespace(stage='setup', human_assisted_setup=True, operator_setup_binding=object(),
        terminal=None, pending_write=False,
        failed_approaches={('setup', 'tag'): {'discovery:dom', 'discovery:semantic'}},
        events=[{'stage': 'setup', 'action': 'type', 'field': 'campaign_name',
                 'attempted': True, 'ok': True, 'verification': {'actual_match': True}}])
    fields = {'campaign_name': True, 'waba': True, 'business_number': True, 'tag': False, 'ga_tracking': False}
    assert setup_attempt_verdict(tools, {'fields': fields})['next_required_field'] == 'ga_tracking'
    fields['ga_tracking'] = True
    assert setup_attempt_verdict(tools, {'fields': fields})['status'] == 'STUCK'

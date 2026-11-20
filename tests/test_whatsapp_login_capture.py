import pytest

from browser.login_signal_capture import inspect_identity_branches, safe_request_locator


def test_large_unrelated_array_does_not_hide_bounded_identity_object():
    private_tenant = 'TEST-ACCOUNT-DO-NOT-PERSIST'
    data = {'results': [{}] * 100, 'data': {'session': {'authenticated': True, 'tenantId': private_tenant}}}
    facts = inspect_identity_branches(data, private_tenant)
    assert facts['status'] == 'EXPLICIT_AUTH_AND_EXACT_TENANT_PAIR_OBSERVED'
    assert facts['pairs'] == [{'authenticated_path': '/data/session/authenticated',
                               'authenticated_type': 'boolean', 'tenant_path': '/data/session/tenantId',
                               'tenant_type': 'string', 'authenticated_true': True,
                               'tenant_exact': True}]
    assert private_tenant not in repr(facts)


def test_generic_success_wrong_tenant_and_cross_record_fields_are_insufficient():
    assert inspect_identity_branches({'success': True, 'tenantId': 'expected'}, 'expected')['pairs'] == []
    assert inspect_identity_branches({'authenticated': True, 'tenantId': 'other'}, 'expected')['pairs'] == []
    data = {'session': {'authenticated': True}, 'tenant': {'tenantId': 'expected'}}
    assert inspect_identity_branches(data, 'expected')['pairs'] == []


def test_explicit_denial_rejects_positive_pair():
    data = {'authenticated': True, 'tenantId': 'expected', 'session': {'authenticated': False}}
    assert inspect_identity_branches(data, 'expected')['status'] == 'EXPLICIT_AUTHENTICATION_DENIAL'


def test_request_locator_omits_unknown_names_and_all_query_values():
    facts = safe_request_locator('/api/private-path/session?tenantId=private-value&private-name=secret')
    assert facts['path_template'] == '/api/:redacted/session'
    assert facts['query_names'] == ['tenantId']
    assert facts['other_query_parameters'] == 1
    assert all(value not in repr(facts) for value in ('private-path', 'private-value', 'private-name', 'secret'))


@pytest.mark.asyncio
@pytest.mark.parametrize('dashboard_only,security_stop', [(False, False), (True, False), (False, True)])
async def test_capture_listener_starts_before_login_and_never_navigates_to_setup(
        tmp_path, monkeypatch, dashboard_only, security_stop):
    import json
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.config import Config
    from modules.whatsapp import login_capture

    from modules.whatsapp.browser_baseline import BaselineStop

    steps = []
    doc = object()

    class Browser:
        def __init__(self, config, policy, trace, **kwargs):
            self.trace = trace
            self.guard = SimpleNamespace(failed=False, blocking_hosts=[])
            self.page = doc

        async def start(self):
            steps.append('start')

        async def navigate(self, url):
            assert url == Config.login_url
            assert steps == ['start', 'listen']
            steps.append('login')

        async def close(self):
            steps.append('close')

    class Discovery:
        def __init__(self, browser, *, targeted_capture):
            assert targeted_capture
            assert browser.page is browser.login_page is doc
            self.enabled = False

        async def start(self):
            steps.append('listen')
            self.enabled = True

        def targeted_resource_diagnostics(self, opened):
            steps.append('retain_passive_metadata')
            return [{'resource': 10, 'document_status': 'SPA_CONTEXT_CHANGED', 'state': 'CAPTURED'}]

        async def prepare(self, *, calibration_only, operator_window_started):
            assert calibration_only
            assert steps[-1] == ('login' if dashboard_only else 'identity_panel')
            steps.append('capture')
            return dict(targeted_dom={}, targeted_resources=[], scope_certificate={},
                        document_context_stable=True, passive_transport_available=True,
                        page_context={}, dom_diagnostics={})

    async def open_panel(*args, **kwargs):
        steps.append('identity_panel')

    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setenv('NETCORE_ACCOUNT_NAME', 'TEST-ACCOUNT-DO-NOT-PERSIST')
    monkeypatch.setattr(Config, 'load', lambda: Config('unused', live_login_enabled=True))
    monkeypatch.setattr(login_capture, 'ROOT', tmp_path)
    monkeypatch.setattr(login_capture, 'PANEL_OPEN_SECONDS', 0)
    monkeypatch.setattr(login_capture, 'DASHBOARD_SETTLE_SECONDS', 0)
    monkeypatch.setattr(login_capture, 'StagehandClient', Browser)
    monkeypatch.setattr(login_capture, 'ReadOnlyDiscovery', Discovery)
    monkeypatch.setattr(login_capture, 'inspection_hold', open_panel)
    monkeypatch.setattr(login_capture, 'human_assisted_session_safety',
                        AsyncMock(return_value={'diagnostic_session_safe': True, 'page_context': {}}))
    negative = BaselineStop('AUTHENTICATION_OR_SECURITY_STOP', failed_predicates=['credential_form'])
    monkeypatch.setattr(login_capture, 'reject_security_negatives',
                        AsyncMock(side_effect=negative if security_stop else None))
    await login_capture.run_login_capture(True, dashboard_only=dashboard_only)
    assert steps == (['start', 'listen', 'login'] + ([] if dashboard_only else ['identity_panel'])
                     + ['retain_passive_metadata' if security_stop else 'capture', 'close'])
    path, = (tmp_path / 'credentials' / 'whatsapp-login-capture').glob('*.json')
    content = path.read_text()
    assert 'TEST-ACCOUNT-DO-NOT-PERSIST' not in content
    report = json.loads(content)
    assert report['authentication_verified'] is False
    assert report['network_requests_replayed'] == 0
    if security_stop:
        assert report['failed_predicates'] == ['credential_form']
        assert report['last_verified_step'] == 'CURRENT_PROTECTED_TENANT_ROUTE_CONFIRMED'
        assert report['panel_inspected'] is False
        assert report['resources'][0]['resource'] == 10
    else:
        assert report['checks']['authenticated_resource'] == 'NO_CURRENT_DOCUMENT_JSON_GET'


def test_targeted_dom_captures_only_structural_locators_and_comparisons():
    from modules.login.discovery import DOM_DISCOVERY
    from test_evidence_discovery_repair import BASE, dom_expression

    # Extend the existing DOM double with the few APIs used by the targeted branch.
    expression = r'''(() => {
      const originalMatches=Element.prototype.matches;
      Element.prototype.matches=function(query){
        if(query==='[aria-expanded=true][aria-controls]')return this.getAttribute('aria-expanded')==='true'&&this.hasAttribute('aria-controls');
        if(query===':hover')return this.hasAttribute('data-test-hover');
        const nth=query.match(/^:nth-child\((\d+)\)$/);
        if(nth)return (this.parentElement?.children.indexOf(this)??0)+1===Number(nth[1]);
        return originalMatches.call(this,query);
      };
      Element.prototype.getRootNode=function(){return document;};
      Element.prototype.contains=function(target){for(let e=target;e;e=e.parentElement)if(e===this)return true;return false;};
      Object.defineProperties(Element.prototype,{
        ownerDocument:{get:()=>document},
        previousElementSibling:{get:function(){return this.parentElement?.children[this.parentElement.children.indexOf(this)-1]||null;}}
      });
      document.getElementById=id=>document.querySelector('#'+id);
      global.NodeFilter={SHOW_ELEMENT:1};
      document.createTreeWalker=panel=>{const children=panel.querySelectorAll('body *');let i=0;return {nextNode:()=>children[i++]||null};};
      return SNAPSHOT;
    })()'''.replace('SNAPSHOT', DOM_DISCOVERY)
    tenant = 'TEST-ACCOUNT-DO-NOT-PERSIST'
    doc = {'url': BASE + '/', 'children': [
        {'tag': 'button', 'attrs': {'aria-expanded': 'true', 'aria-controls': 'private-panel-id'}},
        {'tag': 'div', 'attrs': {'id': 'private-panel-id'}, 'children': [
            {'tag': 'span', 'attrs': {'data-test-hover': 'yes', 'aria-label': tenant}, 'text': 'private-display-name'},
        ]},
    ]}
    result = dom_expression(expression, doc, account=tenant, targeted_capture=True)['targeted_capture']
    assert result['hover_is_in_bound_panel']
    assert result['hovered_row']['accessible_label_exact']
    assert not result['hovered_row']['rendered_text_exact']
    assert result['hovered_row']['locator']['selector'].endswith(':nth-child(1)')
    assert all(value not in repr(result) for value in (tenant, 'private-panel-id', 'private-display-name'))

"""One operator-started read-only capture in a fresh, owned Netcore tab."""
import asyncio
from dataclasses import replace
import json
import os
import sys
from time import monotonic

from app.config import Config, ConfigurationError, OriginPolicy, ROOT
from app.safety import authorize_live
from app.trace import Trace
from browser.discovery import ReadOnlyDiscovery
from browser.evidence_diagnostics import EvidenceFailure, evidence_diagnostic
from browser.inspection import inspection_hold
from browser.stagehand_client import StagehandClient
from modules.whatsapp.browser_baseline import reject_security_negatives
from modules.whatsapp.workflow import human_assisted_session_safety
from pydantic import SecretStr

PANEL_OPEN_SECONDS = 20
DASHBOARD_SETTLE_SECONDS = 12


def capture_checks(report):
    resources = report['targeted_resources']
    dom = report['targeted_dom']
    scope = report['scope_certificate']
    current = [item for item in resources if item['document_status'] == 'CURRENT_DOCUMENT']
    resource_pairs = [item for item in current
                      if item.get('status') == 'EXPLICIT_AUTH_AND_EXACT_TENANT_PAIR_OBSERVED']
    if not report['passive_transport_available']:
        resource_status = 'PASSIVE_RESOURCE_TRANSPORT_UNAVAILABLE'
    elif not current:
        resource_status = 'NO_CURRENT_DOCUMENT_JSON_GET'
    elif not resource_pairs:
        resource_status = 'NO_EXPLICIT_AUTH_AND_EXACT_TENANT_PAIR_SEE_RESOURCE_REASONS'
    elif len(resource_pairs) != 1:
        resource_status = 'MULTIPLE_CURRENT_RESOURCE_PAIRS_REQUIRE_REVIEW'
    else:
        resource_status = 'PAIR_OBSERVED_RESOURCE_SEMANTICS_REQUIRE_REVIEW'
    dashboard = dom.get('dashboard') or {}
    if not dashboard.get('candidate_count'):
        dashboard_status = 'NO_VISIBLE_DASHBOARD_LANDMARK'
    elif dashboard['candidate_count'] != 1:
        dashboard_status = 'MULTIPLE_VISIBLE_DASHBOARD_LANDMARKS'
    elif not dashboard.get('locator'):
        dashboard_status = 'UNIQUE_DASHBOARD_LOCATOR_UNAVAILABLE'
    else:
        dashboard_status = 'UNIQUE_VISIBLE_DASHBOARD_LOCATOR_OBSERVED'
    rows = dom.get('panel_rows', [])
    hover = dom.get('hovered_row')
    if dom.get('hover_is_in_bound_panel') and hover:
        rows = [hover]
    exact = [row for row in rows if row['rendered_text_exact'] or row['accessible_label_exact']]
    if not dom.get('panel'):
        tenant_status = dom.get('status', 'ACCOUNT_CONTROL_NOT_RETAINED')
        if hover:
            tenant_status = ('HOVERED_ROW_EXACT_BUT_PANEL_OWNER_UNPROVEN'
                             if hover['rendered_text_exact'] or hover['accessible_label_exact']
                             else 'HOVERED_ROW_DOES_NOT_MATCH_EXPECTED_ACCOUNT')
    elif not exact:
        tenant_status = 'VISIBLE_ACCOUNT_PANEL_NO_EXACT_TEXT_OR_LABEL_MATCH'
    elif len(exact) > 1:
        tenant_status = 'MULTIPLE_EXACT_TENANT_ROWS_REQUIRE_BINDING'
    elif not exact[0].get('locator'):
        tenant_status = 'EXACT_TENANT_ROW_LOCATOR_UNAVAILABLE'
    else:
        tenant_status = 'EXACT_VISIBLE_TENANT_ROW_LOCATOR_OBSERVED'
    return {
        'current_protected_document': ('PASS' if report['document_context_stable']
                                       else 'PROTECTED_DOCUMENT_CHANGED_DURING_CAPTURE'),
        'dashboard_marker': dashboard_status,
        'visible_exact_tenant': tenant_status,
        'authenticated_resource': resource_status,
        'scope': scope.get('state', 'LOGIN_SCOPE_UNAVAILABLE'),
        'resource_observed_after_panel_instruction': any(
            item['observed_during_operator_window'] for item in resource_pairs),
    }


async def run_login_capture(authorized, *, dashboard_only=False):
    if authorized is not True or not sys.stdin.isatty():
        raise ConfigurationError('Login signal capture requires live authorization and an interactive terminal')
    config = replace(Config.load(), headless=False, max_actions=120, timeout_seconds=480, evidence_file=None)
    account = os.getenv('NETCORE_ACCOUNT_NAME')
    if not account:
        raise ConfigurationError('Private Netcore account configuration is incomplete')
    permit = authorize_live(config, True, 1)
    trace = Trace()
    browser = StagehandClient(config, OriginPolicy(set(permit.origins)), trace, live_permit=permit)
    browser.expected_account = SecretStr(account)
    result = {'run_id': trace.run_id, 'status': 'CAPTURE_INCOMPLETE', 'authentication_verified': False,
              'network_requests_replayed': 0, 'response_bodies_saved': False,
              'capture_mode': 'DASHBOARD_ONLY' if dashboard_only else 'ACCOUNT_PANEL',
              'last_verified_step': 'NOT_STARTED', 'panel_inspected': False}
    phase = 'BROWSER_START'
    opened = None
    ownership = None
    try:
        await browser.start()
        browser.login_page = browser.page
        phase = 'PASSIVE_LISTENER_START'
        browser.discovery = ReadOnlyDiscovery(browser, targeted_capture=True)
        await browser.discovery.start()
        result['last_verified_step'] = 'PASSIVE_LISTENER_STARTED'
        phase = 'LOGIN_ENTRY_NAVIGATION'
        await browser.navigate(config.login_url)
        result['last_verified_step'] = 'SIGN_IN_ENTRY_OPENED'
        phase = 'OPERATOR_SIGN_IN'
        print('Sign in once in the headed Chromium tab. Waiting up to 300 seconds for the intended tenant route.',
              flush=True)
        sign_in = asyncio.timeout(300)
        try:
            async with sign_in:
                while True:
                    if browser.guard.failed or browser.guard.blocking_hosts:
                        raise EvidenceFailure('LOGIN_NAVIGATION_GUARD_STOP')
                    ownership = await human_assisted_session_safety(browser)
                    if ownership['diagnostic_session_safe']:
                        break
                    await asyncio.sleep(2)
        except TimeoutError:
            if not sign_in.expired():
                raise
            result['session_failed_predicates'] = ownership['failed_predicates'] if ownership is not None else ['protected_document_not_observed']
            raise EvidenceFailure('OPERATOR_SIGN_IN_TIMEOUT') from None
        result['last_verified_step'] = 'PROTECTED_TENANT_ROUTE_OBSERVED'
        result['initial_page_context'] = ownership['page_context']
        if dashboard_only:
            phase = 'DASHBOARD_SETTLE'
            print(f'Keep the signed-in dashboard open. Do not open Profile or account settings. '
                  f'Collecting passive resource evidence for {DASHBOARD_SETTLE_SECONDS} seconds.', flush=True)
            await asyncio.sleep(DASHBOARD_SETTLE_SECONDS)
        else:
            phase = 'ACCOUNT_CONTROL_WINDOW'
            print('Return to this terminal before opening the account panel. After you press Enter, switch back to '
                  'the SAME Chromium tab and open the account name or avatar control in the Netcore header that shows '
                  'the current account or tenant. Leave the panel open without selecting or switching tenants. '
                  'If it shows the current tenant name, leave the pointer over that name. Stay in Chromium for the capture.',
                  flush=True)
            try:
                await inspection_hold(60, prompt='Press Enter when ready to start the read-only capture countdown.')
            except TimeoutError:
                raise EvidenceFailure('ACCOUNT_CONTROL_WINDOW_EXPIRED') from None
            opened = monotonic()
            print(f'Open the account panel now. Capture begins in {PANEL_OPEN_SECONDS} seconds.', flush=True)
            await asyncio.sleep(PANEL_OPEN_SECONDS)
        phase = 'TARGETED_SIGNAL_CAPTURE'
        async with asyncio.timeout(30):
            ownership = await human_assisted_session_safety(browser)
            if not ownership['diagnostic_session_safe']:
                result['session_failed_predicates'] = ownership['failed_predicates']
                raise EvidenceFailure('CURRENT_PROTECTED_TENANT_DOCUMENT_UNPROVEN')
            result['last_verified_step'] = 'CURRENT_PROTECTED_TENANT_ROUTE_CONFIRMED'
            result['capture_page_context'] = ownership['page_context']
            await reject_security_negatives(browser, audit=True)
            result['last_verified_step'] = 'SECURITY_NEGATIVE_CHECK_PASSED'
            report = await browser.discovery.prepare(calibration_only=True, operator_window_started=opened)
        result.update(
            last_verified_step='TARGETED_CAPTURE_COMPLETED',
            panel_inspected=not dashboard_only and bool(report['targeted_dom'].get('panel')),
            status='CAPTURED_FOR_REVIEW',
            checks=capture_checks(report),
            protected_page=report['page_context'],
            target=report['targeted_dom'],
            resources=report['targeted_resources'],
            scope={key: report['scope_certificate'].get(key) for key in
                   ('state', 'code', 'inaccessible_shadow_roots', 'unknown_shadow_roots',
                    'inventory_complete', 'relevant_evidence_stable', 'negative_scope_complete')},
            dashboard_visibility_rejections=report['dom_diagnostics'].get('dashboard_visibility_rejections', {}),
            tenant_rejections=report['dom_diagnostics'].get('rejections', {}).get('tenant', {}),
        )
    except Exception as error:
        details = evidence_diagnostic(error, phase, browser)
        result.update(failed_phase=phase, reason=getattr(error, 'code', None) or details['code'],
                      failed_predicates=getattr(error, 'diagnostic', {}).get('failed_predicates', []))
        discovery = getattr(browser, 'discovery', None)
        if discovery and discovery.enabled:
            # Preserve the safe reduction already captured passively. No DOM read
            # or browser request may follow a security negative.
            result['resources'] = discovery.targeted_resource_diagnostics(opened)
        trace.lifecycle('LOGIN_CAPTURE_STOP', {
            'state': result['reason'], 'phase': phase, 'failed_predicates': result['failed_predicates'],
            'last_verified_step': result['last_verified_step'], 'panel_inspected': result['panel_inspected'],
        })
    finally:
        await browser.close()
        result['cleanup'] = trace.cleanup_result
    output = ROOT / 'credentials' / 'whatsapp-login-capture' / f'{trace.run_id}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('LOGIN_CAPTURE_REPORT ' + str(output), flush=True)
    print('LOGIN_CAPTURE_CHECKS ' + json.dumps(result.get('checks', {
        'stage': result.get('failed_phase'), 'reason': result.get('reason'),
        'failed_predicates': result.get('failed_predicates', []),
        'session_checks': result.get('session_failed_predicates', []),
        'last_verified_step': result['last_verified_step'],
        'panel_inspected': result['panel_inspected'],
    }), sort_keys=True), flush=True)
    for item in result.get('resources', []):
        print(f"LOGIN_CAPTURE_RESOURCE #{item['resource']} "
              f"{item['document_status']} {item.get('status', item['state'])} "
              f"traversal_limit={item.get('legacy_traversal_limit') or 'NONE'}", flush=True)
    for check in ('scope', 'dashboard_visibility_rejections', 'tenant_rejections'):
        if check in result:
            print('LOGIN_CAPTURE_DETAIL ' + json.dumps({check: result[check]}, sort_keys=True), flush=True)
    print('LOGIN_CAPTURE_STATUS ' + result['status'], flush=True)
    trace.write(ROOT / 'outputs')

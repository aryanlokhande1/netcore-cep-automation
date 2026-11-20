"""Collect Login signal candidates in one owned session without authorizing Setup."""

import asyncio
import json
from time import monotonic

from app.config import ROOT
from browser.evidence_diagnostics import EvidenceFailure, evidence_diagnostic
from browser.inspection import inspection_hold


OPERATOR_WINDOW_SECONDS = 90
CAPTURE_SECONDS = 15


async def collect_login_calibration(browser, tools, result):
    # Login has finished and the controller still owns the original browser.
    # This callback has no path to a campaign action or evidence approval.
    browser.begin_readonly_review(OPERATOR_WINDOW_SECONDS + CAPTURE_SECONDS)
    discovery = getattr(browser, 'discovery', None)
    if discovery is None or browser.page is None:
        report = {'status': 'BLOCKED', 'failed_checks': ['PROTECTED_LOGIN_OBSERVER_UNAVAILABLE']}
    elif tools.terminal_error:
        report = {'status': 'BLOCKED', 'failed_checks': ['TERMINAL_LOGIN_TOOL_STOP']}
    else:
        from modules.whatsapp.browser_baseline import BaselineStop, reject_security_negatives
        from modules.whatsapp.workflow import human_assisted_session_safety

        browser.login_page = browser.page
        try:
            async with asyncio.timeout(10):
                safety = await human_assisted_session_safety(browser)
                if safety['diagnostic_session_safe']:
                    await reject_security_negatives(browser)
            if not safety['diagnostic_session_safe']:
                report = {'status': 'BLOCKED', 'failed_checks': safety['failed_predicates']}
            else:
                operator_window_started = monotonic()
                try:
                    await inspection_hold(OPERATOR_WINDOW_SECONDS, prompt=(
                        'In the same Chromium tab, open the existing read-only account or tenant identity panel in place. '
                        'Keep the intended tenant selected, leave the panel visible, and press Enter here to capture. '
                        'The capture window closes after 90 seconds.'))
                except TimeoutError:
                    raise EvidenceFailure('OPERATOR_CALIBRATION_WINDOW_EXPIRED') from None

                async with asyncio.timeout(CAPTURE_SECONDS):
                    safety = await human_assisted_session_safety(browser)
                    if not safety['diagnostic_session_safe']:
                        report = {'status': 'BLOCKED', 'failed_checks': safety['failed_predicates']}
                    else:
                        await reject_security_negatives(browser)
                        report = await discovery.prepare(calibration_only=True,
                                                         operator_window_started=operator_window_started)
        except BaselineStop as error:
            report = {'status': 'BLOCKED', 'failed_checks': [error.code, *error.diagnostic.get('failed_predicates', [])]}
        except Exception as error:
            failure = evidence_diagnostic(error, 'DISCOVERY', browser)
            report = {'status': 'BLOCKED', 'failed_checks': [failure['code']], 'failure': failure}

    report['authentication_verified'] = False
    report['run_id'] = browser.trace.run_id
    path = ROOT / 'credentials' / 'whatsapp-login-calibration' / (browser.trace.run_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    result['login_calibration'] = {
        'run_id': browser.trace.run_id,
        'status': report['status'],
        'failed_checks': report['failed_checks'],
        'authentication_verified': False,
        'artifact': str(path),
    }
    browser.trace.lifecycle('LOGIN_CALIBRATION', {
        'state': report['status'], 'failed_checks': report['failed_checks'], 'trusted': False,
    })

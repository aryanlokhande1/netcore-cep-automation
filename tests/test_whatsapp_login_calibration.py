"""Offline checks for the explicitly operator-started calibration path."""
import json
from unittest.mock import AsyncMock

from test_evidence_discovery_repair import ACCOUNT, captured, network, setup_discovery


async def test_calibration_collects_candidates_without_writing_evidence(tmp_path):
    browser, discovery = setup_discovery(tmp_path)
    network(discovery)
    await captured(discovery)
    report = await discovery.prepare(calibration_only=True)

    assert report['status'] == 'CANDIDATES_PRESENT'
    assert report['authentication_verified'] is False
    assert report['read_only_resources'][0]['readonly_path'] == '/observed'
    assert report['tenant_candidates'][0]['selector'] == '#tenant'
    assert ACCOUNT not in json.dumps(report)
    assert 'NEVER-PERSIST-RESPONSE' not in json.dumps(report)
    assert list(tmp_path.iterdir()) == []


async def test_calibration_cli_uses_single_login_and_no_evidence_review(monkeypatch):
    from app import cli
    from app.config import Config

    runner = AsyncMock(return_value={})
    monkeypatch.setattr(cli, 'run_live', runner)
    monkeypatch.setattr(cli.Config, 'load', lambda: Config('unused', evidence_file='unopened.json'))
    args = cli.build_parser().parse_args([
        'live', '--login-only', '--authorized-live', '--calibrate-whatsapp-login',
        '--api-request-budget', '6',
    ])
    await cli.run(args)

    config = runner.call_args.args[0]
    assert config.max_actions == 120
    assert config.evidence_file is None
    assert config.max_login_submissions == 1
    assert runner.call_args.kwargs['login_calibration'] is True
    assert runner.call_args.kwargs['evidence_review'] is False
    assert runner.call_args.kwargs['review'] is False


async def test_calibration_does_not_scan_after_security_negative(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from modules.whatsapp import login_calibration, browser_baseline, workflow

    collector = AsyncMock()
    browser = SimpleNamespace(
        page=object(), discovery=SimpleNamespace(prepare=collector),
        trace=SimpleNamespace(run_id='controlled', lifecycle=Mock()),
        begin_readonly_review=Mock(),
    )
    monkeypatch.setattr(login_calibration, 'ROOT', tmp_path)
    monkeypatch.setattr(workflow, 'human_assisted_session_safety', AsyncMock(
        return_value={'diagnostic_session_safe': True}))
    monkeypatch.setattr(browser_baseline, 'reject_security_negatives', AsyncMock(
        side_effect=browser_baseline.BaselineStop('AUTHENTICATION_OR_SECURITY_STOP',
                                                 failed_predicates=['challenge'])))
    result = {}
    await login_calibration.collect_login_calibration(browser, SimpleNamespace(terminal_error=False), result)

    collector.assert_not_awaited()
    browser.begin_readonly_review.assert_called_once_with(105)
    summary = result['login_calibration']
    assert summary['failed_checks'] == ['AUTHENTICATION_OR_SECURITY_STOP', 'challenge']
    assert summary['authentication_verified'] is False
    assert (tmp_path / 'credentials/whatsapp-login-calibration/controlled.json').is_file()


def test_calibration_reports_visible_exact_accessible_label_as_separate_clue():
    from modules.login.discovery import DOM_DISCOVERY
    from test_evidence_discovery_repair import BASE, dom_expression

    report = dom_expression(DOM_DISCOVERY, {'url': BASE + '/', 'children': [
        {'tag': 'button', 'attrs': {'id': 'visible', 'aria-label': ACCOUNT}},
        {'tag': 'button', 'hidden': True, 'attrs': {'id': 'hidden', 'aria-label': ACCOUNT}},
    ]}, account=ACCOUNT, calibration=True)
    assert report['tenant_candidates'] == []
    assert [item['selector'] for item in report['calibration_tenant_labels']] == ['#visible']


async def test_calibration_distinguishes_truncated_resource_from_observed_false(tmp_path):
    from time import monotonic

    browser, discovery = setup_discovery(tmp_path)
    discovery.channel.send.return_value = {'body': json.dumps({'rows': [{'key': 1}] * 33})}
    start = monotonic()
    network(discovery)
    await captured(discovery)
    report = await discovery.prepare(calibration_only=True, operator_window_started=start)
    resource, = report['resource_diagnostics']
    assert resource['observed_during_operator_window']
    assert resource['support'] == 'AUTH_TENANT_INDICATORS_UNCHECKED_TRAVERSAL_LIMIT'
    assert resource['authenticated_indicator_count'] is None
    assert resource['exact_tenant_indicator_count'] is None
    assert report['read_only_resources'] == []

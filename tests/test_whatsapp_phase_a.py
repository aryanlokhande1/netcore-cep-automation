"""Phase A configuration/skill checks: no Chromium, credentials or providers."""
import hashlib
import json
from unittest.mock import patch

import pytest

from app.config import ROOT, ConfigurationError
from modules.whatsapp import contract


def test_runtime_task_is_configured_and_preserves_approved_values():
    assert hasattr(contract, 'load_page1_task'), 'Page 1 needs a validated configuration loader'
    task = contract.load_page1_task()
    assert task.workflow == 'whatsapp_campaign_page1'
    assert task.entry_url == contract.SETUP_URL
    assert task.values.model_dump() == contract.VALUES == {
        'campaign_name': 'agent_test', 'waba': '1730519403808933',
        'business_number': 'Bajaj Markets (+91 8422973254)', 'tags': 'test'}


@pytest.mark.parametrize('change', [
    {'entry_url': 'https://unapproved.example/campaign'},
    {'entry_url': 'http://tprappin2r.netcoresmartech.com/campaign'},
    {'entry_url': 'https://user@tprappin2r.netcoresmartech.com/campaign'},
    {'entry_url': None}, {'workflow': 'send'}, {'save': True},
    {'tenant': 'another_tenant'},
    {'entry_url': contract.SETUP_URL.replace('/bajajfinservmarket/', '/another_tenant/')},
    *[{'entry_url': contract.SETUP_URL + suffix} for suffix in ('?next=send', '#setup', '/audience', '\\')],
    *[{'values': {**contract.VALUES, field: value}} for field, value in
      [('campaign_name', 'unapproved'), ('waba', 1730519403808933),
       ('business_number', '+91 8422973254'), ('tags', 'new-tag'), ('audience', 'all')]],
])
def test_invalid_configuration_is_sanitized_and_never_changes_authority(tmp_path, change):
    assert hasattr(contract, 'load_page1_task'), 'Missing validated task configuration'
    data = contract.load_page1_task().model_dump()
    data.update(change)
    path = tmp_path / 'task.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError, match='Invalid WhatsApp Page 1 configuration') as error:
        contract.load_page1_task(path)
    assert 'unapproved.example' not in str(error.value)
    assert contract.VALUES['tags'] == 'test'


def test_missing_or_malformed_explicit_configuration_never_falls_back(tmp_path):
    assert hasattr(contract, 'load_page1_task'), 'Missing validated task configuration'
    path = tmp_path / 'task.json'
    for data in (None, '{', '[]'):
        if data is not None:
            path.write_text(data)
        with pytest.raises(ConfigurationError):
            contract.load_page1_task(path)
    with pytest.raises(ConfigurationError):
        contract.load_page1_task('')


def test_offline_contract_exposes_exact_scope_verification_and_skill():
    assert hasattr(contract, 'page1_contract'), 'Missing reusable Page 1 contract'
    data = contract.page1_contract()
    assert list(data['fields']) == list(contract.ORDER)
    for field, spec in data['fields'].items():
        assert spec['value'] == contract.VALUES[field]
        assert spec['fact'] == contract.FIELD_FACTS[field]
        assert spec['verification']
        assert spec['actions'] == [n for n, c in contract.CAPABILITIES.items() if c.exposed and field in c.fields]
    assert set(data['forbidden_actions']) == {'Save', 'Finish Later', 'Next Step', 'Audience modification',
        'Schedule', 'Publish', 'Activate', 'Send', 'Campaign launch', 'Create tag'}
    assert data['skill']['path'] == 'skills/whatsapp/SKILL.md'
    assert data['skill']['approved'] is False
    assert data['skill']['status'] == 'UNVERIFIED_BASELINE'
    assert data['completion_status'] == 'SETUP_FILLED_UNSAVED'
    for action in data['forbidden_actions']:
        with pytest.raises(contract.CapabilityError, match='TOOL_FORBIDDEN'):
            contract.validate_capability(action)


def test_whatsapp_baseline_resolution_is_hash_checked_and_login_is_isolated(tmp_path):
    from learning.skill_storage import registry
    assert hasattr(registry, 'module_baseline'), 'Missing scoped canonical baseline resolver'
    snapshot = registry.module_baseline('whatsapp')
    assert snapshot.approved is False
    assert snapshot.digest == hashlib.sha256((ROOT/'skills/whatsapp/SKILL.md').read_bytes()).hexdigest()
    assert snapshot.metadata['provenance']['graduated'] is False
    assert snapshot.metadata['provenance']['live_skill_load_provenance'] == 'NOT_ESTABLISHED'
    for value in contract.VALUES.values():
        assert value not in snapshot.content
    assert registry.SkillStore().snapshot() is None
    with pytest.raises(ValueError):
        registry.module_baseline('../login')
    directory = tmp_path/'skills/whatsapp'
    directory.mkdir(parents=True)
    (directory/'SKILL.md').write_text(snapshot.content + '\nChanged without metadata')
    (directory/'version.json').write_text(json.dumps(snapshot.metadata))
    with pytest.raises(ConfigurationError):
        registry.module_baseline('whatsapp', root=tmp_path)


@pytest.mark.parametrize('change', [{'approved': True}, {'status': 'LIVE_CERTIFIED'},
    {'identity': 'netcore-login'}, {'source_version': 2},
    {'provenance': {'graduated': True}}, {'content_sha256': 'unreviewed'}])
def test_baseline_metadata_cannot_claim_approval_or_graduation(tmp_path, change):
    from learning.skill_storage.registry import module_baseline
    snapshot = module_baseline('whatsapp')
    directory = tmp_path/'skills/whatsapp'
    directory.mkdir(parents=True)
    (directory/'SKILL.md').write_bytes(snapshot.content.encode('utf-8'))
    (directory/'version.json').write_text(json.dumps({**snapshot.metadata, **change}))
    with pytest.raises(ConfigurationError):
        module_baseline('whatsapp', root=tmp_path)


def test_setup_factory_uses_actual_skill_bytes_without_claiming_approval():
    from app.trace import Trace
    from deep_agents.offline import SyntheticChatModel
    from deep_agents.permissions import InferenceBudget
    from modules.whatsapp.agent import create_setup_doer
    from learning.skill_storage import registry
    assert hasattr(registry, 'module_baseline'), 'Missing canonical baseline skill'
    trace = Trace()
    tools = type('Tools', (), {'registered_tools': lambda self: []})()
    with patch('modules.whatsapp.agent.create_deep_agent') as factory:
        create_setup_doer(SyntheticChatModel(), tools, trace, InferenceBudget(2), offline=True)
    assert registry.module_baseline('whatsapp').content in factory.call_args.kwargs['system_prompt']
    event = next(e['diagnostic'] for e in trace.events
        if e.get('diagnostic', {}).get('state') == 'BASELINE_SKILL_LOADED')
    assert event['approved'] is False
    assert event['content_sha256'] == registry.module_baseline('whatsapp').digest


async def test_browser_first_adapter_selects_same_canonical_skill(tmp_path):
    from unittest.mock import AsyncMock
    from modules.whatsapp.learning_loop import run_loop
    from learning.skill_storage.registry import module_baseline
    with patch('learning.browser_runtime.run_task', new_callable=AsyncMock) as runtime:
        await run_loop(None, None, None, None, tmp_path)
    assert runtime.call_args.kwargs['baseline_skill'].content == module_baseline('whatsapp').content


async def test_initial_iteration_preserves_exact_canonical_bytes_and_hash(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.trace import Trace
    from deep_agents.permissions import InferenceBudget
    from learning.browser_runtime import run_task
    from learning.skill_storage.registry import module_baseline
    from modules.whatsapp.learning_loop import LoopReport, Correction, GUIDANCE
    from modules.whatsapp.browser_baseline import BaselineStop
    snapshot = module_baseline('whatsapp')
    browser = SimpleNamespace(trace=Trace(), process=object(), login_submissions=0,
        browser=SimpleNamespace(context=object()), guard=SimpleNamespace(failed=False))
    baseline = SimpleNamespace(browser=browser, protect=AsyncMock(), verified=set(contract.ORDER),
        last_discovery={'setup_active': True})
    tools = SimpleNamespace(blocked=False, terminal=None, events=[], current=None,
        widgets=SimpleNamespace(actions=[]), readiness=AsyncMock(return_value={}),
        invoke=AsyncMock(), registered=lambda: [], verify=AsyncMock(return_value=True))
    model = SimpleNamespace(ainvoke=AsyncMock(return_value={'structured_response': {}}))
    task = (lambda b: tools, contract.VALUES, GUIDANCE, LoopReport, Correction,
        AsyncMock(return_value={'status': 'SKIPPED'}), BaselineStop)
    with patch('learning.browser_runtime.agent', return_value=model):
        result = await run_task(baseline, None, None, InferenceBudget(2), tmp_path/'run',
            task=task, authorized=True, offline=True, iterations=1, baseline_skill=snapshot)
    saved = (tmp_path/'run/iteration-1/SKILL.md').read_bytes()
    assert saved == snapshot.content.encode('utf-8')
    assert result['iterations'][0]['loaded_skill_sha256'] == hashlib.sha256(saved).hexdigest() == snapshot.digest
    assert result['iterations'][0]['baseline_skill']['approved'] is False


async def test_offline_cli_contract_does_not_load_private_configuration_or_start_runtime(capsys):
    from app.cli import build_parser, run
    assert 'whatsapp-contract' in build_parser()._subparsers._group_actions[0].choices
    with patch('app.config.Config.load', side_effect=AssertionError('Private config forbidden')):
        await run(build_parser().parse_args(['whatsapp-contract']))
    assert json.loads(capsys.readouterr().out)['workflow'] == 'whatsapp_campaign_page1'

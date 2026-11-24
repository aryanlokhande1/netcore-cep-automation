from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.whatsapp import page1_fields
from modules.whatsapp.contract import PAGE1_SETUP_FIELDS, SetupStop


@pytest.mark.asyncio
async def test_page1_runs_four_known_fields_in_order(monkeypatch):
    fields = PAGE1_SETUP_FIELDS
    payload = SimpleNamespace(**dict.fromkeys(fields, 'fixed'))
    tools = SimpleNamespace(browser=SimpleNamespace(trace=SimpleNamespace(agent_event=Mock())),
        terminal=None, pending_write=False, payload=payload, events=[], last_observation={},
        last_independent_readback={'fields': {}}, failed_approaches={}, human_assisted_setup=True,
        stage='setup', observe=AsyncMock())
    visited = []
    async def known(current, field):
        visited.append(field)
        current.last_independent_readback['fields'][field] = True
        if field != 'ga_tracking':
            current.events.append({'stage': 'setup', 'field': field,
                'action': 'type' if field == 'campaign_name' else 'select',
                'ok': True, 'attempted': True, 'verification': {'actual_match': True}})
        return {'ok': True}
    monkeypatch.setattr(page1_fields, 'apply_known_field', known)
    monkeypatch.setattr(page1_fields, 'deterministic_readback', AsyncMock(
        return_value={'fields': dict.fromkeys(fields, True), 'stage_visible': True}))
    recover = AsyncMock()
    result = await page1_fields.run_page1_fields(tools, recover)
    assert visited == list(fields)
    assert result['status'] == 'COMPLETE'
    assert 'ga_tracking' not in result['page1_fields']
    recover.assert_not_awaited()


@pytest.mark.asyncio
async def test_page1_blocks_business_number_if_waba_unresolved_and_continues_independent_fields(monkeypatch):
    fields = PAGE1_SETUP_FIELDS
    tools = SimpleNamespace(browser=SimpleNamespace(trace=SimpleNamespace(agent_event=Mock())),
        terminal=None, pending_write=False, payload=SimpleNamespace(**dict.fromkeys(fields, 'fixed')),
        events=[], last_observation={}, last_independent_readback={'fields': {}},
        failed_approaches={}, human_assisted_setup=True, stage='setup', observe=AsyncMock())
    visited = []
    async def known(current, field):
        visited.append(field)
        if field == 'waba':
            return {'ok': False, 'error': 'OWNED_DROPDOWN_NOT_OPEN'}
        current.last_independent_readback['fields'][field] = True
        if field == 'campaign_name':
            current.events.append({'stage': 'setup', 'field': field, 'action': 'type',
                'ok': True, 'attempted': True, 'verification': {'actual_match': True}})
        return {'ok': True}
    monkeypatch.setattr(page1_fields, 'apply_known_field', known)
    monkeypatch.setattr(page1_fields, 'deterministic_readback', AsyncMock(
        return_value={'fields': {field: field not in {'waba', 'business_number'} for field in fields},
                      'stage_visible': True}))
    recover = AsyncMock()
    result = await page1_fields.run_page1_fields(tools, recover)
    assert visited == ['campaign_name', 'waba', 'tag']
    assert result['page1_fields']['business_number']['dependency'] == 'waba'
    assert result['status'] == 'STUCK'
    recover.assert_awaited_once()


@pytest.mark.asyncio
async def test_owned_dropdown_uses_bound_search_and_selects_only_exact_existing_option(monkeypatch):
    control = {'reference': 'tag-control', 'field': 'tag', 'part': 'control',
               'enabled': True, 'expanded': True, 'tag': 'INPUT', 'type': 'text'}
    tools = SimpleNamespace(last_observation={'controls': [control]},
                            binding=lambda field: SimpleNamespace(kind='combobox'))
    async def fresh():
        tools.last_observation = {'controls': [control, {
            'reference': 'exact-tag', 'field': 'tag', 'part': 'option',
            'enabled': True, 'exact': True, 'create': False}, {
            'reference': 'create-tag', 'field': 'tag', 'part': 'option',
            'enabled': True, 'exact': True, 'create': True}]}
    tools.observe = AsyncMock(side_effect=fresh)
    tools.browser = SimpleNamespace(trace=SimpleNamespace(agent_event=lambda *args, **kw: None))
    action = AsyncMock(side_effect=[{'ok': True, 'search_text_verified': True},
                                   {'ok': True, 'fields': {'tag': True}}])
    monkeypatch.setattr(page1_fields, 'field_action', action)

    assert (await page1_fields.choose_dropdown(tools, 'tag', control))['fields']['tag']
    assert [call.args[2:] for call in action.await_args_list] == [
        ('search', 'tag-control'), ('select', 'exact-tag')]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['observation', 'binding', 'changed_picker', 'after_click'])
async def test_template_rebind_is_bounded_and_never_repeats_a_click(monkeypatch, failure):
    from browser.navigation_guard import GuardProtocolError

    picker = ('document', 'trigger', 'panel')
    snapshot = AsyncMock(return_value=picker)
    if failure == 'changed_picker':
        snapshot.side_effect = [picker, ('document', 'trigger', 'different-panel')]
    monkeypatch.setattr(page1_fields, 'template_picker_identity', snapshot, raising=False)
    monkeypatch.setattr(page1_fields.asyncio, 'sleep', AsyncMock())
    tools = SimpleNamespace(browser=SimpleNamespace(trace=SimpleNamespace(agent_event=Mock())),
        last_observation={}, references={}, terminal=None, pending_write=False)
    references = iter(['unsettled', 'old-1', 'old-2', 'fresh-1', 'fresh-2'])

    async def observe():
        reference = next(references)
        if reference == 'unsettled' and failure in {'observation', 'changed_picker'}:
            cause = GuardProtocolError({'code': -32000, 'message': 'Could not find node with given id'})
            raise SetupStop('CONTROL_IDENTITY_UNAVAILABLE') from cause
        tools.last_observation = {'controls': [{'field': 'template', 'part': 'option',
            'reference': reference, 'enabled': True, 'exact': True}], 'scoped_option_evidence': {'visible_owned': 1}}
        tools.references = {reference: {'identity': ('panel', reference.split('-')[0])}}

    tools.observe = AsyncMock(side_effect=observe)
    outcomes = {'binding': [{'ok': False, 'template_rebind_required': True}, {'ok': True}],
                'after_click': [{'ok': False, 'error': 'FIELD_WRITE_UNCERTAIN_NO_RETRY'}]}
    tools.action = AsyncMock(side_effect=[(result, None) for result in outcomes.get(failure, [{'ok': True}])])
    if failure == 'changed_picker':
        with pytest.raises(Exception, match='CONTENT_TEMPLATE_PICKER_CHANGED'):
            await page1_fields.choose_settled_template(tools, picker)
        tools.action.assert_not_awaited()
    else:
        result = await page1_fields.choose_settled_template(tools, picker)
        assert result['ok'] is (failure != 'after_click')
        assert tools.action.await_count == (2 if failure == 'binding' else 1)
        if failure == 'binding':
            assert tools.action.await_args.args[0]['reference'] == 'fresh-2'


def test_tag_chip_keeps_add_marker_out_of_exact_case_sensitive_label():
    import json
    import shutil
    import subprocess
    from modules.whatsapp.observation import TAG_CHIP_HELPERS

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the DOM expression check')
    test_dom = r'''
    global.getComputedStyle=()=>({visibility:'visible',display:'inline'});
    const text=value=>({nodeType:3,textContent:value});
    const make=(name,root=false)=>({nodeType:1,name,childNodes:[text(name)],textContent:name,
      getClientRects:()=>[{}],closest:q=>q==='button,[role=button]'&&!root?null:null,
      matches:()=>false,contains:n=>root&&n===action,querySelectorAll:q=>root&&q==='*'?[action]:[],
      getAttribute:()=>null});
    const action=make('+');
    function chip(label) {
      const root=make(label+' +',true);root.childNodes=[text(label),action];return root;
    }
    const lower=chipParts(chip('test'),'+'), upper=chipParts(chip('Test'),'+');
    const row=chip('  test  ');
    row.tagName='BUTTON';
    action.tagName='I';
    action.childNodes=[];
    action.textContent='';
    action.getAttribute=q=>q==='class'?'fa fa-plus':null;
    action.closest=q=>q===chipOwnerSelector?row:null;
    const buttonChip=chipParts(row,'+');
    const create=chip('Create New');
    action.closest=q=>q===chipOwnerSelector?create:null;
    console.log(JSON.stringify({lower:lower.label,upper:upper.label,clickedNode:lower.action===action,
        iconLabel:buttonChip.label,iconCue:buttonChip.cue,clickOwner:buttonChip.action===row,
        createBlocked:chipParts(create,'+').action===null}));
    '''
    completed = subprocess.run([node, '-'], input=TAG_CHIP_HELPERS + test_dom, text=True,
                               capture_output=True, check=True, timeout=5)
    assert json.loads(completed.stdout) == {'lower': 'test', 'upper': 'Test', 'clickedNode': True, 'iconLabel': 'test',
                                            'iconCue': 'icon_class', 'clickOwner': True, 'createBlocked': True}

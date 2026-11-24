import json
import os
from datetime import datetime
from typing import get_args
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from deep_agents.offline import SyntheticChatModel
from modules.sms.contract import SMSPreviewPayload
from modules.sms.executor import CAPABILITIES, STAGES
from modules.sms.skill import TASK_PROMPT
from modules.sms.workflow import execute_workflow, preview_claim, run_stage_doer
from modules.whatsapp.contract import V2Stop


class SMSModelDouble(SyntheticChatModel):
    def _generate(self, messages, **kwargs):
        assert next(message.content for message in messages if isinstance(message, HumanMessage)) == TASK_PROMPT
        system = next(message for message in messages if isinstance(message, SystemMessage)).text
        requested = json.loads(system.split('SMS stage context ', 1)[1].splitlines()[0])
        previous = [message for message in messages if isinstance(message, ToolMessage)]
        fields = requested['fields_in_order']
        if any(not json.loads(message.content).get('ok') for message in previous):
            name, args = 'SMSStageReport', {'complete': False}
        elif not fields and 'observe_sms_stage' in self.bound_tool_names and len(previous) < 2:
            name, args = 'observe_sms_stage', {'approach': ('semantic', 'dom')[len(previous)]}
        elif len(previous) >= len(fields):
            name, args = 'SMSStageReport', {'complete': True}
        else:
            field = fields[len(previous)]
            name = next(action for stage, action, key in CAPABILITIES if stage == requested['stage'] and field == key)
            assert name in self.bound_tool_names
            args = {'field': field}
        self.calls += 1
        call = {'name': name, 'args': args, 'id': uuid4().hex, 'type': 'tool_call'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[call]))])


FIXTURE = r'''(payload=>{
 const select = (label, wanted) => `<label>${label}<select><option value="none">--</option><option value="exact">${wanted}</option></select></label>`;
 const radio = label => `<label><input type="radio">${label}</label>`;
 const button = (name, more='') => `<button type="button" ${more}>${name}</button>`;
 const el = key => `${payload[key]}`;
 const advancing = button('Next Step','data-action="advance"');
 const tags = payload.custom_tags ? `<div><div><div><div><mat-label>Add tags</mat-label></div></div></div>
   <mat-chip-grid></mat-chip-grid><input role="combobox" aria-haspopup="listbox"
     aria-expanded="false" data-action="tag-open"></div>` : select('Add tags',el('tag'));
 const setup = `<label>Campaign name<input></label>${tags}${radio('GA tracking')}${advancing}`;
 const audience = `${radio(el('audience_type'))}${select('Included audience',el('include_audience'))}${radio('Exclude')}${advancing}`;
 const card = id => `<article data-template-id="${id}" aria-label="${el('template')}">
   <h3>${el('template')}</h3>${button('⋮','aria-label="More options" class="more" data-action="menu"')}
   ${button('Use','hidden data-action="use"')}</article>`;
 const content = `${select('Channel',el('channel'))}${select('Sender ID',el('sender_id'))}${select('Custom domain',el('custom_domain'))}
   <div id="selected"></div>${advancing}<div style="height:900px"></div>${card(19)}${card(payload.template_id)}`;
 const schedule = `${radio(el('send_type'))}<label>Date<input type="date"></label><label>Time<input type="time"></label>
   ${payload.view_timezone ? `<label>Timezone<select><option>${el('view_timezone')}</option></select></label>`:''}${radio('Frequency cap')}
   ${button('Apply','data-action="apply"')}<section aria-label="Schedule summary"></section>${button('Preview','data-action="preview"')}`;
 const preview = `<dl>${[['Campaign name','campaign_name'],['Tag','tag'],['Audience mode','audience_type'],
   ['Included audience','include_audience'],['Channel','channel'],['Sender ID','sender_id'],['Custom domain','custom_domain'],
   ['Template','template'],['Template ID','template_id'],['Send mode','send_type'],['Scheduled datetime','schedule_datetime'],
   ['Timezone','timezone']].map(([label,key])=>`<dt>${label}</dt><dd>${el(key)}</dd>`).join('')}
   <dt>Campaign ID</dt><dd>91458</dd></dl>${button('Save & Publish','data-action="prohibited"')}`;
 const steps = [['Setup',setup],['Audience',audience],['Content',content],['Schedule',schedule],['Preview',preview]];
 let step=0; window.smsActions=[];
 document.addEventListener('pointermove',event=>{window.smsPointer=[event.target.closest('article')?.dataset.templateId??null,
   event.clientX,event.clientY]});
 const render = ()=>{document.body.innerHTML=step===0 ? `<h1>Setup</h1><form>${steps[0][1]}</form>` :
   `<main><h1>${steps[step][0]}</h1>${steps[step][1]}</main>`;
   document.querySelector('main,form').style.cssText='display:grid;gap:8px;justify-items:start';
   window.scrollTo(0,0);
 };
 const styles=document.createElement('style');
 styles.textContent='article .more {visibility:hidden} article:hover .more {visibility:visible} article {border:1px solid black}';
 document.head.append(styles);
 document.addEventListener('click',event=>{
   const action=event.target.dataset.action;
   if (!action) return;
   if (action==='tag-open') {
     event.target.setAttribute('aria-controls','tag-options');
     event.target.setAttribute('aria-expanded','true');
     document.body.insertAdjacentHTML('beforeend',`<div id="tag-options" role="listbox">
       <mat-option role="option" data-action="tag-pick">${el('tag')}</mat-option></div>`);
     return;
   }
   if (action==='tag-pick') {
     document.querySelector('mat-chip-grid').innerHTML=`<mat-chip-row>${el('tag')}</mat-chip-row>`;
     document.querySelector('#tag-options').remove();
     document.querySelector('[role=combobox]').setAttribute('aria-expanded','false');
     return;
   }
   const card=event.target.closest('article');
   window.smsActions.push([action,card?.dataset.templateId ?? null]);
   if (action==='advance'||action==='preview') {step++; render();}
   if (action==='menu') card.querySelector('[data-action=use]').hidden=false;
   if (action==='use') { const selected=document.getElementById('selected');
     selected.dataset.selectedTemplateId=card.dataset.templateId;
     selected.textContent=card.getAttribute('aria-label'); }
   if (action==='apply') { const date=document.querySelector('input[type=date]').value;
     const time=document.querySelector('input[type=time]').value;
     document.querySelector('[aria-label="Schedule summary"]').textContent=`${date}T${time}:00+05:30`; }
 });
 render();
})(PAYLOAD)'''


@pytest.mark.asyncio
async def test_workflow_never_opens_scope_with_pending_preflight():
    with pytest.raises(V2Stop, match='SMS_LIVE_PREFLIGHT_REQUIRED'):
        await execute_workflow(None, {'status': 'SMS_SETUP_VERIFIED_EXACT_NAME_PENDING'},
                               stage_doer=None, before_preview=None)


def test_preview_claim_blocks_duplicate_dispatch(tmp_path, monkeypatch):
    from app.trace import Trace
    from modules.sms import workflow

    monkeypatch.setattr(workflow, 'verify_future_schedule', lambda: None)
    payload = SMSPreviewPayload.model_validate({name: get_args(field.annotation)[0]
                                                for name, field in SMSPreviewPayload.model_fields.items()})
    trace = Trace()
    preview_claim(tmp_path, payload, 'd' * 64, trace)
    with pytest.raises(V2Stop, match='PRIOR_SMS_PREVIEW_REQUIRES_READ_ONLY_RECONCILIATION'):
        preview_claim(tmp_path, payload, 'd' * 64, Trace())


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_SMS_DOM_FIXTURE') != '1', reason='Explicit loopback Chromium test')
@pytest.mark.parametrize(('view_timezone', 'expected_stop', 'custom_tags'), [
    ('Asia/Calcutta', None, False),
    ('Asia/Calcutta', None, True),
    ('Asia/Colombo', 'SMS_SCHEDULING_TIMEZONE_MISMATCH', False),
    (None, 'SMS_SCHEDULING_TIMEZONE_UNVERIFIED', False),
])
async def test_sms_model_harness_through_preview_in_headed_chromium(monkeypatch, view_timezone, expected_stop, custom_tags):
    from app.browser_mcp_client import StepTools, open_step
    from app.config import Config, OriginPolicy
    from app.trace import Trace
    from browser.protected_tabs import bind_confirmed_primary_tab
    from browser.stagehand_client import StagehandClient
    from deep_agents.permissions import InferenceBudget
    from modules.sms.executor import SMSExecutor, SMSObserver
    from modules.sms.vision import sms_screenshot
    from modules.sms import workflow
    from test_whatsapp_navigation_repair import navigation_server

    trace = Trace()
    trace.enable_agent_trace()
    payload = SMSPreviewPayload.model_validate({name: get_args(field.annotation)[0]
                                                for name, field in SMSPreviewPayload.model_fields.items()})
    model = SMSModelDouble()
    budget = InferenceBudget(20)
    claims = []
    monkeypatch.setattr(workflow, 'verify_future_schedule', lambda: None)
    with navigation_server('valid') as server:
        browser = StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'], headless=False,
            login_url='about:blank', expected_host='127.0.0.1', max_actions=300, timeout_seconds=90),
            OriginPolicy({server.origin}), trace, fixture=True)
        try:
            await browser.start()
            await browser.page.goto(server.dashboard)
            await browser.page.evaluate(FIXTURE.replace('PAYLOAD', json.dumps(
                {**payload.model_dump(), 'view_timezone': view_timezone, 'custom_tags': custom_tags})))
            executor = SMSExecutor(browser, bind_confirmed_primary_tab(browser), payload, route=server.dashboard,
                                   clock=lambda: datetime.fromisoformat('2026-11-24T16:00:00+05:30'))
            executor.early_progression_approved = True
            if not expected_stop:
                scope = StepTools('doer', 'setup', frozenset({'observe_sms_stage'}))
                async with open_step(executor, scope, task_doer=True) as client:
                    assert list(client.tools) == ['observe_sms_stage']
                    probe_model = SMSModelDouble()
                    assert await run_stage_doer(probe_model, executor, client, trace, budget, read_only=True)
                    assert probe_model.calls == 3
                observer = SMSObserver(executor, tag_investigation=True)
                scope = StepTools('observer', 'setup', frozenset({'inspect_sms_tag'}))
                async with open_step(observer, scope, independent_observer=True) as client:
                    tag = await client.read('inspect_sms_tag')
                assert tag['complete']
                expected_control = 'input' if custom_tags else 'select'
                assert tag['controls'][0]['tag'] == expected_control
                assert expected_control in {item['tag'] for item in tag['nearby_controls'][0]['controls']}
                assert tag['binding_kind'] == ('chip_combobox' if custom_tags else 'native_select')
                assert tag['native_binding_found'] is not custom_tags
                if custom_tags:
                    checks = tag['binding_checks']
                    assert checks['effective_text_type'] and not checks['legacy_explicit_text_type']
                    assert checks['setup_form_owner'] and checks['chip_grid_owner'] and checks['target_fresh']
                    assert checks['initial_binding'] and not checks['popup_owned'] and not checks['exact_option_visible']
                    scope = StepTools('doer', 'setup', frozenset({'inspect_sms_tag'}))
                    async with open_step(executor, scope, task_doer=True) as client:
                        assert list(client.tools) == ['inspect_sms_tag']
                        assert (await client.read('inspect_sms_tag'))['binding_checks'] == checks
                assert executor.mutation_count == 0
                assert (await sms_screenshot(observer)).startswith(b'\x89PNG\r\n\x1a\n')
                await browser.page.evaluate("""(() => {
                    const host = document.body.appendChild(document.createElement('div'));
                    host.id = 'closed-crop-fixture';
                    host.attachShadow({mode:'closed'}).textContent = 'Private fixture';
                })()""")
                assert await sms_screenshot(observer) is None
                denial = [e['agent_execution'] for e in trace.events if
                          e.get('agent_execution', {}).get('code') == 'SMS_PIXEL_SCOPE_UNVERIFIED'][-1]
                assert (denial['complete'], denial['unknown_roots'], denial['denied_roots']) == (True, 0, 1)
                await browser.page.evaluate("document.getElementById('closed-crop-fixture').remove()")
                if custom_tags:
                    await browser.page.evaluate("""(() => {
                        const input=document.querySelector('[role=combobox]');
                        input.value='test';
                        input.setAttribute('aria-expanded','true');
                        document.body.insertAdjacentHTML('beforeend',
                          '<div id="unowned-fixture-list" role="listbox"><mat-option role="option">test</mat-option></div>');
                    })()""")
                    tag = await observer.inspect_tag()
                    assert not tag['owned_exact_option_visible'] and not tag['selected_tag_matches_payload']
                    assert not (await observer.executor.readback())['fields']['tag']
                    await browser.page.evaluate("document.querySelector('[role=combobox]').value = ''")
                    scope = StepTools('doer', 'setup', frozenset({'set_sms_field'}))
                    async with open_step(executor, scope, task_doer=True) as client:
                        blocked = await client.read('set_sms_field', {'field': 'tag'})
                    assert blocked['code'] == 'SMS_TAG_OWNED_EXISTING_OPTION_UNVERIFIED'
                    assert executor.mutation_count == 0 and not executor.terminal
                    await browser.page.evaluate("""(() => {
                        document.querySelector('[role=combobox]').setAttribute('aria-expanded','false');
                        document.getElementById('unowned-fixture-list').remove();
                    })()""")
            proof = {'status': 'SMS_PREFLIGHT_VERIFIED', 'name_check': {'status': 'CAMPAIGN_LIST_READINESS_FAILED'},
                     'payload_approval': {'accepted': True, 'duplicate_uncertainty_accepted': True,
                                          'automated_duplicate_check': 'CAMPAIGN_LIST_READINESS_FAILED',
                                          'campaign_name': payload.campaign_name, 'tenant': payload.tenant}}
            async def task(current, client):
                assert 'read_sms_stage' not in client.tools
                assert 'advance' not in client.tools
                if current.stage == 'setup':
                    with pytest.raises(V2Stop, match='MCP_BROWSER_DISPATCH_FAILED'):
                        await client.read('set_sms_field', {'field': 'channel'})
                    with pytest.raises(V2Stop, match='MCP_BROWSER_DISPATCH_FAILED'):
                        await client.read('set_sms_field', {'field': 'campaign_name', 'value': 'unapproved'})
                if current.stage == 'schedule' and 'set_sms_field' in client.tools:
                    assert 'apply_sms_schedule' not in client.tools
                return await run_stage_doer(model, current, client, trace, budget)
            if expected_stop:
                with pytest.raises(V2Stop, match=expected_stop):
                    await execute_workflow(executor, proof, stage_doer=task, before_preview=lambda: claims.append(True))
                assert executor.stage == 'schedule'
                assert proof['timezone_evidence']['displayed_iana'] == view_timezone
                assert await browser.page.evaluate("Array.from(document.querySelectorAll('input[type=date],input[type=time]'), e=>e.value)") == ['', '']
                assert claims == []
                return
            try:
                results = await execute_workflow(executor, proof, stage_doer=task, before_preview=lambda: claims.append(True))
            except V2Stop as failure:
                blocked = [event['agent_execution']['code'] for event in trace.events
                           if event.get('agent_execution', {}).get('kind') == 'sms_task_action_blocked']
                pointer = await browser.page.evaluate('window.smsPointer ?? null')
                pytest.fail(f'{failure.code} in {executor.stage}: {await executor.readback()!r}, {blocked!r}, pointer={pointer!r}')
            assert list(results) == list(STAGES)
            assert all(all(facts['fields'].values()) for facts in results.values())
            assert results['preview']['preview']['campaign_id'] == '91458'
            assert all(value['matched'] for key, value in results['preview']['preview'].items() if key != 'campaign_id')
            assert claims == [True]
            assert await browser.page.evaluate('window.smsActions') == [
                ['advance', None], ['advance', None], ['menu', '16683'], ['use', '16683'],
                ['advance', None], ['apply', None], ['preview', None]]
            assert model.calls == 15
            execution = [event['agent_execution'] for event in trace.events if 'agent_execution' in event]
            assert len([event for event in execution if event['kind'] == 'sms_task_readback']) == 10
            png = await sms_screenshot(executor)
            assert png and png.startswith(b'\x89PNG\r\n\x1a\n'), json.dumps([e['agent_execution'] for e in trace.events
                if e.get('agent_execution', {}).get('kind') == 'sms_teacher_screenshot_unavailable'])
        finally:
            await browser.close()

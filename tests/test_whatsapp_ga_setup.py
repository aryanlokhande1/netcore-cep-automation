import importlib.util
import json
import os
from types import SimpleNamespace

import pytest

from modules.whatsapp.contract import V2Payload, V2GATrackingAction


GA = {'utm_source': 'Netcore', 'utm_medium': 'WhatsApp', 'utm_campaign': 'campaign-id-1',
      'utm_content': '', 'utm_term': 'Campaign'}


def fixture_payload():
    return {'campaign_name': 'ga-test-one', 'waba': 'fixture', 'business_number': 'fixture', 'tag': 'fixture',
            'ga_tracking': True, 'audience_type': 'Lists/Segments', 'include_audience': 'fixture',
            'exclude_audience': (), 'template': 'fixture', 'send_type': 'Send Later', 'schedule_datetime': None,
            'timezone': 'Asia/Kolkata', 'frequency_cap_enabled': True, 'contact_limit_enabled': False,
            'final_action': 'SAVE_AND_PREVIEW', 'publish_allowed': False, 'send_test_allowed': False}


def test_tracking_contract_preserves_empty_content_and_rejects_disabled_utm():
    from modules.whatsapp.contract import ga_setup_fields, ga_strategy_scope

    body = fixture_payload()
    enabled = V2Payload.model_validate({**body, **GA})
    assert enabled.utm_content == ''
    assert ga_setup_fields(enabled) == ('campaign_name', 'ga_tracking', *GA)
    assert V2GATrackingAction.model_validate({'action': 'type', 'field': 'utm_content', 'reference': 'fresh'}).field == 'utm_content'
    disabled = V2Payload.model_validate({**body, 'ga_tracking': False})
    assert ga_setup_fields(disabled) == ('campaign_name', 'ga_tracking')
    with pytest.raises(ValueError):
        V2Payload.model_validate({**body, 'ga_tracking': False, **GA})
    with pytest.raises(ValueError):
        V2Payload.model_validate({**body, 'utm_source': 'Netcore'})
    assert ga_strategy_scope(enabled) == ga_strategy_scope(enabled.model_copy(update={'campaign_name': 'ga-test-two'}))
    assert ga_strategy_scope(enabled) != ga_strategy_scope(enabled.model_copy(update={'utm_medium': 'SMS'}))


@pytest.mark.skipif(os.getenv('RUN_GA_DOM_FIXTURE') != '1', reason='Explicit headed loopback Chromium')
@pytest.mark.asyncio
async def test_ga_card_and_exact_input_value_readback_in_chromium(tmp_path, monkeypatch):
    from app import config
    from app.config import Config, OriginPolicy
    from app.trace import Trace
    from browser.observed_control import protected_document
    from browser.protected_tabs import bind_confirmed_primary_tab
    from browser.stagehand_client import StagehandClient
    from browser import lifecycle, stagehand_client
    from evals.fixture import fixture_server
    from modules.whatsapp.contract import ReviewedUI, SETUP_URL, V2Authorization, ga_setup_fields
    from modules.whatsapp.observer import deterministic_readback
    from modules.whatsapp.tools import V2BrowserTools

    from urllib.parse import urlsplit
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setattr(stagehand_client, 'ROOT', tmp_path)
    monkeypatch.setattr(lifecycle, 'ROOT', tmp_path)
    monkeypatch.setattr(lifecycle, 'PROFILE_ROOT', tmp_path/'profiles')
    trace = Trace()
    trace.enable_agent_trace()
    trace.agent_phase = 'observer'
    with fixture_server() as server:
        browser = StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'], headless=False),
                                  OriginPolicy({server.base}), trace, fixture=True)
        try:
            await browser.start()
            await browser.navigate(server.base + '/start?scenario=already')
            browser.setup_binding = bind_confirmed_primary_tab(browser)
            enabled = V2Payload.model_validate({**fixture_payload(), **GA})
            tools = V2BrowserTools(browser, enabled, ReviewedUI(origin='https://' + urlsplit(SETUP_URL).netloc),
                                   V2Authorization(), tmp_path)
            tools.ga_setup_fields = ga_setup_fields(enabled)
            async def protect():
                return protected_document(browser)

            tools.protect = protect
            markers = ''.join(f'<label for="{key}">{key}</label><input id="{key}" name="{key}">' for key in GA)
            await browser.page.evaluate('document.body.innerHTML = ' + json.dumps(
                '<form><label for="campaign">Campaign Name</label><input id="campaign" value="ga-test-one">'
                '<section><h2>Unrelated</h2><label for="decoy">utm_source</label><input id="decoy" name="utm_source">'
                '</section><details><summary>Advanced</summary><section><h3>Google Analytics tracking</h3>'
                '<input id="ga" type="checkbox"><div id="utms" hidden>' + markers + '</div></section></details></form>'))
            closed = await tools.observe(strategy='dom')
            assert closed['ga_card_evidence']['advanced_closed'] == 1 and 'ga_tracking' not in closed['field_evidence']
            await browser.page.evaluate("document.querySelector('details').open = true")
            off = await tools.observe(strategy='dom')
            assert 'ga_tracking' in off['field_evidence'] and 'utm_content' not in off['field_evidence']
            await browser.page.evaluate("document.querySelector('#ga').checked = true; document.querySelector('#utms').hidden = false")
            await browser.page.evaluate('document.querySelector("#utm_source").placeholder = "Netcore"')
            bound = await tools.observe(strategy='dom')
            assert set(bound['field_evidence']) == set(ga_setup_fields(enabled))
            assert not (await deterministic_readback(tools))['fields']['utm_source']
            for key, expected in GA.items():
                await browser.page.evaluate(f'document.querySelector("#{key}").value = {json.dumps(expected)}')
            read = await deterministic_readback(tools)
            assert all(read['fields'].values()) and read['ga_readback'] == {'ga_tracking': True, **GA}
            from modules.whatsapp.ga_teacher_evidence import GATeacherEvidence

            evidence = GATeacherEvidence(tools, await protect())
            dom = await evidence.inspect()
            assert dom['status'] == 'OBSERVED' and dom['evidence_ref']
            assert {'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'} <= {
                key for row in dom['controls'] if row['visible'] for _, key in row['names']}
            assert 'campaign-id-1' not in json.dumps(dom) and 'selector' not in json.dumps(dom)
            assert (await evidence.view('layout_ambiguous'))[0]['status'] == 'VISION_UNNECESSARY_DOM_ESTABLISHED_INPUTS'
            await browser.page.evaluate('''(() => {
                const card = document.querySelector('details > section');
                const replacement = document.createElement('div');
                replacement.innerHTML = card.innerHTML;
                card.replaceWith(replacement);
                const form = document.querySelector('form');
                const wrapper = document.createElement('section');
                form.replaceWith(wrapper); wrapper.append(form);
            })()''')
            fallback = await GATeacherEvidence(tools, await protect()).inspect()
            assert fallback['status'] == 'OBSERVED' and fallback['group_tag'] == 'DETAILS'
            disabled = V2Payload.model_validate({**fixture_payload(), 'ga_tracking': False})
            tools.payload, tools.ga_setup_fields = disabled, ga_setup_fields(disabled)
            await browser.page.evaluate("document.querySelector('#ga').checked = false; document.querySelector('#utms').hidden = true")
            await tools.observe(strategy='dom')
            read = await deterministic_readback(tools)
            assert all(read['fields'].values()) and read['ga_readback'] == {'ga_tracking': False}
            tools.payload, tools.ga_setup_fields = enabled, ga_setup_fields(enabled)
            adjacent = ''.join(
                f'<label for="{key}">Parameter {index}</label>'
                f'<input type="text" id="{key}" formcontrolname="{key}" value="{value}">'
                for index, (key, value) in enumerate(GA.items(), 1)
            )
            await browser.page.evaluate('document.body.innerHTML = ' + json.dumps(
                '<form><label for="campaign">Campaign Name</label><input id="campaign" value="ga-test-one">'
                '<details open><summary>Advanced</summary><div><div><h3>Google Analytics tracking</h3>'
                '<button type="button" role="switch" aria-checked="true">Tracking</button></div>'
                '<div>' + adjacent + '</div></div></details></form>'))
            before = await tools.observe(strategy='dom')
            assert set(before['field_evidence']) == {'campaign_name', 'ga_tracking'}
            tools.ga_adjacent_keys = True
            after = await tools.observe(strategy='dom')
            assert set(after['field_evidence']) == set(ga_setup_fields(enabled))
            assert (await deterministic_readback(tools))['ga_readback'] == {'ga_tracking': True, **GA}
            await browser.page.evaluate("document.querySelector('label[for=utm_content]').hidden = true")
            refused = await tools.observe(strategy='dom')
            assert set(refused['field_evidence']) == {'campaign_name', 'ga_tracking'}
            if importlib.util.find_spec('mcp'):
                from app.browser_mcp_client import open_step
                from modules.whatsapp.mcp_steps import OBSERVER_STEPS

                tools.ga_setup_fields = None
                tools.mcp_ga_scope = True
                await browser.page.evaluate('''document.querySelector('form').insertAdjacentHTML('beforeend',
                    '<label for="waba">WABA</label><select id="waba"><option>fixture</option></select>')''')
                async with open_step(tools, OBSERVER_STEPS['setup']) as client:
                    observed = await client.read('browser_observe')
                    readback = await client.read('independent_readback')
                assert {'campaign_name', 'waba', 'ga_tracking'} <= set(observed['field_evidence'])
                assert readback['fields']['ga_tracking'] is True

                from hashlib import sha256
                from app.browser_mcp_client import StepTools

                await browser.page.evaluate("document.querySelector('#campaign').value = ''")
                tools.mcp_discovery = tools.mcp_campaign_name_write = True
                async with open_step(tools, StepTools('doer', 'setup', frozenset({'observe'}))) as client:
                    await client.read('browser_observe')
                observer = StepTools('observer', 'setup', frozenset({'readback'}))
                async with open_step(tools, observer) as client:
                    before = await client.read('independent_readback')
                assert before['stage_visible'] is True and before['observed_states'] == {
                    'campaign_name': {'state': 'EMPTY', 'payload_match': False,
                                      'value_sha256': sha256(b'').hexdigest()}}

                reference = next(c['reference'] for c in tools.last_observation['controls']
                                 if c.get('field') == 'campaign_name' and c['part'] == 'control')
                # The fixture URL differs from Netcore. Supply the approved
                # document and route facts while retaining the real executor.
                tools._last_protected_route = {'fragment_class': 'SETUP'}
                tools.operator_setup_binding = browser.setup_binding
                tools.operator_approved_setup_document = await protect()
                tools.operator_approved_payload = tools.payload
                tools.authorization = V2Authorization(live=True, inference=True, edits=True, manual_form_test=True)
                tools.read_only = False
                scope = StepTools('doer', 'setup', frozenset({'type'}), campaign_name_reference=reference)
                async with open_step(tools, scope) as client:
                    write = await client.read('browser_type', {'field': 'campaign_name', 'reference': reference})
                assert write['ok'] is True, write
                tools.read_only = True
                async with open_step(tools, observer) as client:
                    after = await client.read('independent_readback')
                assert after['stage_visible'] is True and after['observed_states'] == {
                    'campaign_name': {'state': 'NONEMPTY', 'payload_match': True,
                                      'value_sha256': sha256(b'ga-test-one').hexdigest()}}

                # Real nested scroll container and an independently read switch.
                await browser.page.evaluate('document.body.innerHTML = ' + json.dumps(
                    '<form><label for="campaign">Campaign Name</label>'
                    '<input id="campaign"><div id="ga-spacer"></div><details open><summary>Advanced</summary>'
                    '<section><h3>Google Analytics tracking</h3><input id="ga-only" type="checkbox" checked>'
                    '</section></details></form><form><input aria-label="unrelated"></form>'))
                await browser.page.evaluate("""Object.assign(document.querySelector('form').style,
                    {height:'220px',overflow:'auto'});
                    document.querySelector('#ga-spacer').style.height='520px'""")
                tools = V2BrowserTools(browser, disabled, ReviewedUI(origin='https://' + urlsplit(SETUP_URL).netloc),
                                       V2Authorization(live=True, inference=True, edits=True, manual_form_test=True), tmp_path)
                tools.protect = protect
                tools.operator_setup_binding = browser.setup_binding
                tools.operator_approved_setup_document = await protect()
                tools.operator_approved_payload = tools.payload
                tools._last_protected_route = {'fragment_class': 'SETUP'}
                from modules.whatsapp.mcp_steps import mcp_ga_switch

                held = GATeacherEvidence(tools, await protect())
                await tools.observe(strategy='dom')
                selected = []

                async def choose(client, reference):
                    tool, = client.agent_tools(image_content=False)
                    selected.append(tool.name)
                    return await tool.ainvoke({'field': 'ga_tracking', 'reference': reference})

                verdict = await mcp_ga_switch(tools, held, select_action=choose)
                assert selected == ['browser_type']
                assert verdict == {'status': 'MCP_GA_VERIFIED', 'actual': False, 'written': True}
                event = next(row['agent_execution'] for row in trace.events
                             if row.get('agent_execution', {}).get('kind') == 'mcp_ga_viewport')
                assert event['scroll_dispatched'] and event['before']['scrollable_ancestors'] == 1, json.dumps(event.get('before')) + ' ' + str(event.get('status'))
                assert event['before']['heading']['clipped'] and event['after']['heading']['in_view']
                assert event['after']['switch']['in_view']
        finally:
            await browser.close()
        assert trace.cleanup_result == 'CLOSED_AND_PROFILE_REMOVED'

"""Focused scoped interactions in actual local Chromium; no tenant conclusions."""
import json
import os
from unittest.mock import AsyncMock
import pytest

from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab
from evals.local_browser_baseline import settled
from evals import local_remaining_fields as fixtures
from modules.whatsapp.browser_baseline import BrowserBaseline,BaselineStop
from modules.whatsapp.interactive_widgets import InteractiveWidgets
from modules.whatsapp.contract import VALUES


async def test_interactive_permission_is_required_before_open_or_selection():
    widgets=InteractiveWidgets(AsyncMock())
    for operation in (widgets.open,widgets.complete):
        with pytest.raises(BaselineStop,match='OPERATOR_REMAINING_FIELDS_AUTHORIZATION_REQUIRED'):
            await operation('waba')
    widgets.baseline._snapshot.assert_not_awaited()


def interactive_html(case):
    # Hypothetical widgets: semantic trigger absent from the old principal()
    # filter; popup is mounted only after opening; duplicate dependent labels.
    def widget(field,label):
        semantic='' if case=='demonstration' and field=='waba' else ' aria-haspopup="listbox" role="combobox"'
        if field=='waba':semantic=semantic.replace(' role="combobox"','')
        return '<fieldset><label>'+label+'</label><x-local-widget><div id="local_'+field+'" tabindex="0"'+semantic+'>Choose</div></x-local-widget></fieldset>'
    body='<!doctype html><h2 aria-current="step">Setup</h2><label for="campaign">Campaign name</label><input id="campaign">'
    body+=widget('waba','WABA ID')+'<div id="dependent"></div>'+widget('tags','Add tags')
    body+='<button type="button" id="save">Save</button><script>const values='+json.dumps(VALUES)+';'
    body+='const fixtureCase='+json.dumps(case)+';'
    body+='''window.localCounts={opens:0,choices:0,save:0};
    document.getElementById('save').onclick=()=>localCounts.save++;
    function wire(field){const trigger=document.getElementById('local_'+field);
      trigger.onclick=()=>{if(trigger.nextElementSibling)return;localCounts.opens++;
        const list=document.createElement('div');list.setAttribute('role','listbox');
        for(const value of [values[field],field==='tags'?'Test':'OTHER_OPTION_CANARY']){
          const option=document.createElement('div');option.setAttribute('role','option');option.textContent=value;
          if(fixtureCase==='create_only'&&field==='tags')option.setAttribute('data-create','true');
          option.onclick=e=>{e.stopPropagation();localCounts.choices++;trigger.textContent=value;
            list.querySelectorAll('[role=option]').forEach(n=>n.setAttribute('aria-selected',String(n===option)));
            if(field==='waba'&&!document.getElementById('local_business_number')){
              document.getElementById('dependent').innerHTML=BUSINESS;
              wire('business_number');}};
          list.append(option);}
        if(fixtureCase==='unowned_portal'&&field==='waba')document.body.append(list);else trigger.after(list);};}
    wire('waba');wire('tags');</script>'''
    business=widget('business_number','Business number').replace('<x-local-widget>','<x-local-widget><span>Business number</span>')
    return body.replace('BUSINESS',json.dumps(business))


@pytest.mark.skipif(os.getenv('RUN_REAL_REMAINING_FIELDS')!='1',reason='Explicit headed local acceptance')
async def test_scoped_interactive_widgets_headed(monkeypatch):
    monkeypatch.setattr(fixtures,'widget_html',interactive_html)
    report={'scope':'LOCAL_INTERACTIVE_WIDGET_FIXTURES','live_netcore':False,'paid_inference':0,'cases':[]}
    trace=Trace()
    with fixtures.widget_server() as (server,origin):
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=False,login_url='about:blank',
            expected_host='127.0.0.1',timeout_seconds=90,max_actions=800),OriginPolicy({origin}),trace,fixture=True)
        try:
            await browser.start();original=browser.page;context=browser.browser.context;process=browser.process
            await original.goto('about:blank');await settled(browser,browser.guard.primary_session)
            page,binding=await create_protected_tab(browser);browser.page=page;browser.setup_binding=binding
            for case in ('semantic','demonstration','unowned_portal','create_only'):
                server.case=case;await page.goto(origin+'/regular');await settled(browser,binding.session)
                baseline=BrowserBaseline(browser,origin+'/regular')
                discovery=await baseline.discover()
                campaign=next(c['reference'] for c in discovery['controls'] if c['field_hint']=='campaign_name')
                await baseline.fill_campaign(campaign,authorized=True)
                async def demonstrate(field):
                    # Test-only simulation of the human's single demonstrated
                    # opening. No fixture selector is available to production.
                    assert field=='waba'
                    await page.locator('#local_waba').click(timeout=2000)
                    return True
                baseline.widget_prompt=demonstrate
                if case=='demonstration':
                    widgets=InteractiveWidgets(baseline,demonstrate)
                    state=await widgets.demonstrate('waba')
                    assert state['public']['option_count']==2
                    assert state['public']['approved_option_count']==1
                    assert not state['trigger']  # observation never invents a trigger
                    assert await page.evaluate('localCounts.choices')==0
                    report['cases'].append({'case':case,'evidence_captured':True,'selection_attempted':False,
                        'unsupported_reason':'CUSTOM_TRIGGER_NOT_ESTABLISHED'})
                elif case in ('unowned_portal','create_only'):
                    expected_code='DROPDOWN_OPEN_NOT_VERIFIED' if case=='unowned_portal' else 'INTERACTIVE_EXISTING_OPTION_UNESTABLISHED'
                    with pytest.raises(BaselineStop,match=expected_code):
                        await baseline.complete_remaining(authorized=True)
                    count=await page.evaluate('localCounts.choices')
                    assert count==(0 if case=='unowned_portal' else 2)
                    assert 'tags' not in baseline.verified
                    report['cases'].append({'case':case,'blocked':True,'unintended_selection':False})
                else:
                    result=await baseline.complete_remaining(authorized=True)
                    assert result['status']=='FOUR_FIELDS_VERIFIED_UNSAVED'
                    assert all(x['negative_reason'] is None or x['negative_reason']=='OPTIONS_NOT_RENDERED_OR_UNOWNED' for x in baseline.interactive.evidence)
                    assert await page.evaluate('localCounts.choices')==3
                    await page.locator('#save').click(timeout=2000)
                    assert await page.evaluate('localCounts.save')==0
                    public=json.dumps(baseline.interactive.evidence)
                    assert 'OTHER_OPTION_CANARY' not in public and VALUES['waba'] not in public and 'local_waba' not in public
                    report['cases'].append({'case':case,'all_four_verified':True,'selections':3,'save_blocked':True})
                assert await original.evaluate('!!document.documentElement')
                assert browser.browser.context is context and browser.process is process
                assert not browser.guard.failed
            assert trace.openai_api_calls==browser.login_submissions==server.posts==0
        finally:
            await browser.close();report['cleanup_result']=trace.cleanup_result
            (ROOT/'outputs/real-local-interactive-widgets.json').write_text(json.dumps(report,indent=2))
        assert trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'

import json
import os
from uuid import uuid4
from pydantic import Field
import pytest
from langchain_core.messages import AIMessage,HumanMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget
from app.config import Config,OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab
from evals.local_browser_baseline import settled
from evals import local_remaining_fields as fixtures
from test_interactive_widgets import interactive_html
from modules.whatsapp.browser_baseline import BrowserBaseline,BaselineStop
from modules.whatsapp.learning_loop import run_loop,Correction
from modules.whatsapp.interactive_widgets import InteractiveWidgets,INSPECT
from modules.whatsapp.contract import VALUES

class Double(SyntheticChatModel):
    steps:list=Field(default_factory=list)
    teacher_inputs:list=Field(default_factory=list)
    loaded_prompts:list=Field(default_factory=list)
    successive:bool=False
    def _generate(self,messages,**kwargs):
        self.calls+=1
        if self.role=='teacher':
            data=json.loads(next(m.content for m in messages if isinstance(m,HumanMessage)))
            self.teacher_inputs.append(data)
            name='Correction';args=dict(owner='model',observations=['Dependent field selected before WABA verification.'],
                hypothesis='Tool order failed.',evidence_indices=[data['attempt']['events'][-1]['index']],
                next_action=('OPEN_BEFORE_INSPECT' if self.successive and self.calls==2 else 'VERIFY_BEFORE_ACTION'),rationale='Verify WABA prerequisite before dependent selection.',risk='scoped_edit',confidence=.95,
                code_fix_required=False,safe_next_iteration=True,expected_evidence='EXACT_SELECTED_STATE')
        else:
            self.loaded_prompts.extend(m.content for m in messages if m.type=='system' and m.content not in self.loaded_prompts)
            name,args=self.steps.pop(0)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{'id':uuid4().hex,'name':name,'args':args}]))])

async def test_scope_and_iteration_bounds_precede_browser_access(tmp_path):
    with pytest.raises(BaselineStop,match='SETUP_SCOPE_APPROVAL_REQUIRED'):
        await run_loop(None,None,None,None,tmp_path/'no',authorized=False)
    with pytest.raises(ValueError):await run_loop(None,None,None,None,tmp_path/'no',authorized=True,iterations=6)

@pytest.mark.skipif(os.getenv('RUN_REAL_REMAINING_FIELDS')!='1',reason='Actual headed local Chromium acceptance')
async def test_real_deep_agents_teacher_loop_same_browser(monkeypatch,tmp_path):
    monkeypatch.setattr(fixtures,'widget_html',interactive_html)
    trace=Trace()
    with fixtures.widget_server() as (server,origin):
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=False,login_url='about:blank',expected_host='127.0.0.1',max_actions=1500,timeout_seconds=120),OriginPolicy({origin}),trace,fixture=True)
        try:
            await browser.start();original=browser.page;context=browser.browser.context;process=browser.process
            await original.goto('about:blank');await settled(browser,browser.guard.primary_session)
            page,binding=await create_protected_tab(browser);browser.page=page;browser.setup_binding=binding
            server.case='semantic';await page.goto(origin+'/regular');await settled(browser,binding.session)
            baseline=BrowserBaseline(browser,origin+'/regular')
            doer=Double(steps=[('scroll_to_field',{'field':'campaign_name'}),('fill_approved_field',{'field':'campaign_name'}),('select_existing_option',{'field':'business_number'}),('LoopReport',{'status':'BLOCKED'}),
                ('observe_page',{}),('open_dropdown',{'field':'waba'}),('inspect_options',{'field':'waba'}),('select_existing_option',{'field':'waba'}),
                ('select_existing_option',{'field':'business_number'}),('select_existing_option',{'field':'tags'}),('LoopReport',{'status':'COMPLETE'})])
            teacher=Double(role='teacher');budget=InferenceBudget(16,role_limits={'doer':14,'teacher':2})
            result=await run_loop(baseline,doer,teacher,budget,tmp_path/'loop',authorized=True,offline=True)
            assert result['status']=='FOUR_FIELDS_VERIFIED_UNSAVED',result
            assert len(result['iterations'])==2
            first,second=result['iterations'];assert first['error']=='PREREQUISITE_NOT_VERIFIED'
            assert first['loaded_skill_sha256']!=second['loaded_skill_sha256']
            assert second['strategy']=='VERIFY_BEFORE_ACTION' and teacher.teacher_inputs
            assert any('Verify the current selection first.' in p for p in doer.loaded_prompts)
            assert first['screenshot']['status']=='FULL_FRAME_REDACTED'
            assert (tmp_path/'loop'/first['screenshot']['file']).read_bytes().startswith(b'\x89PNG')
            assert await page.evaluate('localCounts.choices')==3
            assert await original.evaluate('!!document.documentElement')
            assert context is browser.browser.context and process is browser.process
            assert browser.login_submissions==trace.openai_api_calls==server.posts==0
            await page.locator('#save').click(timeout=2000);assert await page.evaluate('localCounts.save')==0
            # Exact saved failure shape: approved ID occurs inside a longer
            # selected display. Observe it; do not issue an unnecessary click.
            await page.evaluate("(()=>{const t=document.getElementById('local_waba'),o=t.nextElementSibling.querySelector('[aria-selected=true]');t.textContent=o.textContent='Business prefix ('+"+json.dumps(VALUES['waba'])+"+')';})()")
            await page.evaluate("document.getElementById('local_waba').nextElementSibling.querySelectorAll('[role=option]')[1].remove()")
            legacy=INSPECT.replace("idMatch(o.value)||idMatch(o.getAttribute('data-value'))||idMatch(o.textContent)","o.value===desired||o.getAttribute('data-value')===desired||norm(o.textContent)===desired")
            old=await page.evaluate(legacy.replace('SETTINGS',json.dumps({'field':'waba','value':VALUES['waba']})))
            assert old['public']['option_count']==1 and old['public']['selected_option_count']==1
            assert old['public']['approved_option_count']==0 and not old['selected']
            widgets=InteractiveWidgets(baseline);state=await widgets.inspect('waba')
            assert state['selected'] and state['public']['approved_option_count']==1
            before=await page.evaluate('localCounts.choices');await widgets.complete('waba',authorized=True)
            assert await page.evaluate('localCounts.choices')==before
            for bad in ('1'+VALUES['waba'],VALUES['waba']+'9','x'+VALUES['waba'],VALUES['waba']+' '+VALUES['waba']):
                await page.evaluate('(s=>{const t=document.getElementById("local_waba");t.textContent=t.nextElementSibling.querySelector("[aria-selected=true]").textContent=s})('+json.dumps(bad)+')')
                assert not (await widgets.inspect('waba'))['selected']
            await page.evaluate('(s=>{const t=document.getElementById("local_waba"),o=t.nextElementSibling.querySelector("[role=option]");t.textContent=o.textContent=s;const duplicate=o.cloneNode(true);duplicate.setAttribute("aria-selected","false");o.after(duplicate);})('+json.dumps(VALUES['waba'])+')')
            ambiguous=await widgets.inspect('waba')
            assert ambiguous['public']['approved_option_count']==2 and not ambiguous['selected']
            # One justified initialization event only when an actual dependent
            # control is observed disabled, never just because an attempt stopped.
            await page.goto(origin+'/regular');await settled(browser,binding.session)
            await page.locator('#local_waba').click(timeout=2000)
            await page.evaluate("""(v=>{const t=document.getElementById('local_waba'),o=t.nextElementSibling.querySelector('[role=option]');
                t.textContent=o.textContent;o.setAttribute('aria-selected','true');
                document.getElementById('dependent').innerHTML='<fieldset><label for="local_business_number">Business number</label><select id="local_business_number" disabled><option value="">Choose</option></select></fieldset>';
                o.addEventListener('click',()=>document.getElementById('local_business_number').disabled=false);
                t.nextElementSibling.hidden=true;
                t.addEventListener('click',()=>t.nextElementSibling.hidden=false);})("""+json.dumps(VALUES['waba'])+")")
            initializer=InteractiveWidgets(BrowserBaseline(browser,origin+'/regular'))
            await initializer.complete('waba',authorized=True,initialize_dependency=True)
            assert await page.evaluate('localCounts.choices')==1
            with pytest.raises(BaselineStop,match='INTERACTIVE_WRITE_RETRY_FORBIDDEN'):
                await initializer.complete('waba',authorized=True,initialize_dependency=True)
            # Three failed attempts consume only two Teacher reviews, no login.
            await page.goto(origin+'/regular');await settled(browser,binding.session)
            bounded=BrowserBaseline(browser,origin+'/regular')
            repeated=Double(steps=[('fill_approved_field',{'field':'campaign_name'}),('select_existing_option',{'field':'business_number'}),('LoopReport',{'status':'BLOCKED'}),
                ('select_existing_option',{'field':'business_number'}),('LoopReport',{'status':'BLOCKED'}),('select_existing_option',{'field':'business_number'}),('LoopReport',{'status':'BLOCKED'})])
            coach=Double(role='teacher',successive=True)
            capped=await run_loop(bounded,repeated,coach,InferenceBudget(16),tmp_path/'capped',authorized=True,offline=True)
            assert len(capped['iterations'])==3 and coach.calls==3 and capped['status']=='BLOCKED'
            assert await page.evaluate('localCounts.choices')==0
            assert browser.login_submissions==0
            # Empty but authorized/protected page must reach the real Doer and
            # final Teacher, rather than failing in pre-agent field verification.
            await page.evaluate('document.body.replaceChildren()')
            empty=BrowserBaseline(browser,origin+'/regular')
            observer=Double(steps=[('observe_page',{}),('LoopReport',{'status':'BLOCKED'})])
            final_teacher=Double(role='teacher')
            empty_result=await run_loop(empty,observer,final_teacher,InferenceBudget(8),tmp_path/'empty',authorized=True,offline=True,iterations=1)
            assert observer.calls==2 and final_teacher.calls==1
            assert empty_result['iterations'][0]['readiness']['same_protected_document']
            assert empty_result['iterations'][0]['events'][0]['tool']=='observe_page'
        finally:await browser.close()
        assert trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'

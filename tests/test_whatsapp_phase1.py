"""Phase 1 preflight: model/DOM doubles only; no browser, HTTP or paid inference."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from app.config import Config,ROOT,OriginPolicy,ConfigurationError
from app.safety import authorize_live
from app.state import Observation,Result
from app.trace import Trace
from modules.whatsapp.contract import VALUES,ORDER,SETUP_URL,Phase1Limits,SetupStop
from modules.whatsapp.session import TaskSession,PocGrant,Phase1Budget,healthy_observation,source_signature
from modules.whatsapp.tools import SetupTools
from modules.whatsapp.agent import create_setup_doer
from deep_agents.offline import SyntheticChatModel


@pytest.fixture(autouse=True)
def protocol_metadata_transport(monkeypatch):
    """Only these imported protocol-double tests substitute the transport.

    Actual Chromium modules do not import this fixture and use the real channel.
    """
    import browser.metadata_channel as module
    original=module.metadata_channel
    @asynccontextmanager
    async def transport(browser,session,**kwargs):
        if not isinstance(browser.browser,SimpleNamespace):
            async with original(browser,session,**kwargs) as channel:yield channel
            return
        class Channel:
            mutation_epoch=0;bytes_received=0;protocol_commands=1
            async def send(self,method,params,owned_session):
                self.protocol_commands+=1
                return await browser.guard.send(method,params,owned_session)
        yield Channel()
    monkeypatch.setattr(module,'metadata_channel',transport)


class PageDouble:
    def __init__(self):
        self.location=SETUP_URL;self.values={};self.actions=[];self.guard_installed=True
        self.fail_field=None;self.negative=None;self.forbidden=0;self.option_count={k:1 for k in ORDER}
        self.setup_active=True;self.page_id=uuid4().hex;self.marker=False;self.owner=None
        self.guard_installable=True
        selectors=['#setup-form',*['#'+k for k in ORDER],*['#'+k+'-option' for k in ORDER],
            '#tags-open','#tags-search','#tags-option']
        self.node_ids={selector:i+10 for i,selector in enumerate(selectors)}
    async def url(self):return self.location
    async def goto(self,url):
        self.actions.append(('navigate',url));self.location=url
        if self.owner:self.owner.document_generation+=1
    async def wait_for_load_state(self,*args,**kwargs):pass
    async def evaluate(self,source):
        if 'document.documentElement.setAttribute' in source:self.marker=True;return True
        if 'document.documentElement.removeAttribute' in source:self.marker=False;return True
        if 'const alerts' in source:
            flags={**{k:k==self.negative for k in ('credential_form','challenge','rejected','expired','account_restricted')},'negative_scope_complete':True}
            if '_scope_diagnostics' in source:flags['_scope_diagnostics']={'open_shadow_roots_inspected':0,'shadow_roots_uninspected':0,'documents_inspected':1}
            return flags
        if 'Object.defineProperty' in source:self.guard_installed=self.guard_installable;return self.guard_installed
        if '({installed:!!window.__netcoreSetupGuard' in source:return {'installed':self.guard_installed,'attempts':self.forbidden}
        return {'same_form':True,'setup_active':self.setup_active,'page_identity':True,
            'form_selector':'#setup-form','form_recognized':True,'observation_complete':True,
            'guard_installed':self.guard_installed,'forbidden_attempts':self.forbidden,
            'fields':{k:{'discovered':True,'selector':'#'+k,'kind':'TEXT_INPUT' if k=='campaign_name' else 'NATIVE_SELECT',
                'selected':self.values.get(k)==VALUES[k],'value':VALUES[k],'exact_option_count':self.option_count[k],
                'selector_unique':True,'same_form':True,'ownership_verified':True,'visible':True,'enabled':True,
                'action_safe':True,'unobscured':True,'option_selector':'#'+k+'-option' if k!='campaign_name' else None,
                'option_selector_unique':k!='campaign_name','option_visible':True,'option_enabled':True,'option_unobscured':True,
                'multiple':k=='tags','has_other_tags':False,'disabled':False,'open_selector':None,'search_selector':None} for k in ORDER}}
    def locator(self,selector):
        page=self;field=selector.removeprefix('#')
        class Locator:
            async def count(self,**kwargs):
                assert selector in page.node_ids;return 1
            async def fill(self,value,**kwargs):
                page.actions.append(('fill',field))
                if page.fail_field==field:raise RuntimeError('PRIVATE-UI-ERROR')
                page.values[field]=value
            async def select_option(self,values,**kwargs):
                page.actions.append(('select',field))
                if page.fail_field==field:raise RuntimeError('PRIVATE-UI-ERROR')
                page.values[field]=values[0]
            async def click(self,**kwargs):raise AssertionError('No generic click permitted in native-control double')
        return Locator()


class ContextDouble:
    def __init__(self,browser):self.browser=browser;self.items=[browser.page];self.creations=0;self.active=None;self.requested_urls=[]
    async def pages(self):return list(self.items)
    async def new_page(self,url=None):
        assert url=='about:blank','The installed SDK default is an extension bootstrap, not an about tab'
        self.requested_urls.append(url)
        self.creations+=1;page=PageDouble();page.location='about:blank'
        guard=self.browser.guard
        owner=guard.register_attachment({'method':'Target.attachedToTarget','params':{
            'sessionId':'setup-session','targetInfo':{'targetId':'setup-target','type':'page'}}})
        owner.intercepting=owner.protected=True;owner.frame_id='setup-frame';owner.loader_id='setup-loader';owner.document_generation=1
        page.owner=owner;guard.sessions.add(owner.target);guard.page_targets.add(owner.target)
        guard.session_frames[owner.session]=('page',owner.frame_id)
        self.items.append(page);return page
    async def set_active_page(self,page):self.active=page


def session():
    cfg=Config('unused',live_login_enabled=True,max_actions=73,timeout_seconds=520)
    permit=authorize_live(cfg,True,8);permit.started=True
    browser=TaskSession(cfg,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    from browser.navigation_guard import NavigationGuard
    guard=NavigationGuard(browser.policy,diagnostic=browser.trace.lifecycle)
    owner=guard.register_attachment({'method':'Target.attachedToTarget','params':{'sessionId':'source-session',
        'targetInfo':{'targetId':'source-target','type':'page'}}})
    owner.protected=owner.intercepting=True;owner.frame_id='source-frame';owner.loader_id='source-loader';owner.document_generation=1
    guard.sessions.add(owner.target);guard.page_targets.add(owner.target)
    guard.session_frames[owner.session]=('page',owner.frame_id)
    browser.guard=guard;browser.page=PageDouble();browser.page.owner=owner;browser.login_submissions=1
    context=ContextDouble(browser)
    browser.browser=SimpleNamespace(context=context)
    async def send(method,params=None,session=None):
        if method=='DOM.getDocument':return {'root':{'nodeType':9,'nodeId':1,'backendNodeId':1,'childNodeCount':0}}
        if method=='DOM.querySelectorAll':
            page=next(p for p in context.items if p.owner.session==session)
            return {'nodeIds':[page.node_ids[params['selector']]] if params['selector'] in page.node_ids else []}
        if method=='DOM.describeNode':
            page=next(p for p in context.items if p.owner.session==session)
            assert params['nodeId'] in page.node_ids.values()
            return {'node':{'backendNodeId':params['nodeId']+1000}}
        if method=='Target.getTargets':
            return {'targetInfos':[{'targetId':p.owner.target,'type':'page','url':p.location,'browserContextId':'same-context'} for p in context.items]}
        if method=='Runtime.evaluate':
            return {'result':{'value':next(p.marker for p in context.items if p.owner.session==session)}}
        if method=='Page.getFrameTree':
            page=next(p for p in context.items if p.owner.session==session)
            return {'frameTree':{'frame':{'id':page.owner.frame_id,'loaderId':page.owner.loader_id,'url':page.location}}}
        if method=='Target.activateTarget':return {}
        raise AssertionError('Unexpected protocol command '+method)
    guard.send=send
    browser.trace.add('entry');browser.trace.fact('LOGIN_RECOGNIZED');browser.trace.fact('SUBMITTED_ONCE')
    return browser


def setup_tools():
    browser=session();browser.begin_phase('checkpoint',90,4);browser.begin_phase('setup',120,36)
    grant=PocGrant(browser);grant.route='SUBMITTED_ONCE_POC';grant.source_signature=source_signature(browser);grant.source_url=SETUP_URL
    return browser,SetupTools(browser,grant,authorized_mutations=True)


async def bound_setup_tools():
    """Arrange a real-shaped protected tab; session() stays unopened for lifecycle tests."""
    browser,tools=setup_tools()
    await browser.open_setup_tab(tools.grant)
    await browser.navigate(SETUP_URL)
    # Subsequent action assertions concern the operation under test; the owned
    # fixture navigation remains recorded in the production lifecycle trace.
    browser.page.actions.clear()
    return browser,tools


async def fill_all(tools):
    data=await tools.inspect()
    for field in ORDER:
        assert data['ok'],data
        ref=next(d['reference'] for d in data['fields'] if d['field']==field)
        data=await tools.fill(ref)
    assert data['ok'],data
    return await tools.verify()


def test_real_cli_arguments_are_parsed_and_unauthorized_actions_are_absent():
    from app.cli import build_parser
    args=build_parser().parse_args(['whatsapp-setup','--authorized-live','--authorized-setup','--total-api-budget','16'])
    assert args.login_api_budget==6 and args.setup_api_budget==8 and args.teacher_request_budget==2
    assert args.checkpoint_seconds==90 and args.hold_seconds==120
    with pytest.raises(SystemExit):build_parser().parse_args(['whatsapp-setup','--url','https://other.example'])
    with pytest.raises(SystemExit):build_parser().parse_args(['whatsapp-setup','--task','send a campaign'])


async def test_authorization_and_budgets_precede_env_or_credentials(monkeypatch):
    import app.cli as cli
    def forbidden(*args,**kwargs):raise AssertionError('No private configuration access permitted')
    monkeypatch.setattr(cli.Config,'load',forbidden)
    args=cli.build_parser().parse_args(['whatsapp-setup'])
    with pytest.raises(ConfigurationError):await cli.run(args)
    args=cli.build_parser().parse_args(['whatsapp-setup','--authorized-live','--authorized-setup','--total-api-budget','15'])
    with pytest.raises(ConfigurationError):await cli.run(args)


@pytest.mark.parametrize('field,value', [('login_requests',7),('setup_requests',9),('teacher_requests',3),('total_requests',0),('hold_seconds',301),('checkpoint_seconds',121)])
def test_stage_limits_fail_closed(field,value):
    with pytest.raises(ConfigurationError):replace(Phase1Limits(),**{field:value}).validate()


async def test_fixed_fields_verified_and_default_waba_reused_without_submission():
    browser,tools=await bound_setup_tools();browser.page.values['waba']=VALUES['waba']
    result=await fill_all(tools)
    assert result['status']=='SETUP_FILLED_UNSAVED' and browser.login_submissions==1
    assert ('select','waba') not in browser.page.actions
    assert browser.page.values==VALUES and all(action[0] in {'fill','select'} for action in browser.page.actions)
    assert set(result['gates'].values())=={'PASS'}
    assert VALUES['business_number'] not in json.dumps(browser.trace.events)


@pytest.mark.parametrize('negative',['credential_form','challenge','rejected','expired','account_restricted'])
async def test_negative_authentication_state_stops_setup_without_action(negative):
    browser,tools=await bound_setup_tools();browser.page.negative=negative
    result=await tools.inspect()
    assert not result['ok'] and result['error']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert not browser.page.actions


@pytest.mark.parametrize('condition',['wrong_url','wrong_step','guard_failure','forbidden_transition','missing_guard'])
async def test_unexpected_page_and_guard_failure_stop_safely(condition):
    browser,tools=await bound_setup_tools()
    if condition=='wrong_url':browser.page.location=browser.config.login_url
    elif condition=='wrong_step':browser.page.setup_active=False
    elif condition=='guard_failure':browser.guard.failed=True
    elif condition=='forbidden_transition':browser.page.forbidden=1;browser.page.values.update(VALUES)
    elif condition=='missing_guard':browser.page.guard_installed=False;browser.page.guard_installable=False
    observed=await tools.inspect()
    if condition in {'wrong_step','forbidden_transition','missing_guard'}:
        # Read-only observation remains available; these conditions still block
        # action authorization or the final no-transition certificate.
        result=await tools.verify() if condition=='forbidden_transition' else await tools.fill_text(observed['fields'][0]['reference'])
        assert not result['ok']
        expected={'wrong_step':'SETUP_PAGE1_FORM_NOT_READY','forbidden_transition':'FORBIDDEN_TRANSITION_ATTEMPTED',
            'missing_guard':'SETUP_GUARD_UNAVAILABLE'}
        assert result['error']==expected[condition]
    else:
        assert not observed['ok']
    assert not browser.page.actions


async def test_tag_unavailable_never_creates_an_option(monkeypatch):
    import modules.whatsapp.tools as module
    monkeypatch.setattr(module.asyncio,'sleep',AsyncMock())
    browser,tools=await bound_setup_tools();data=await tools.inspect()
    for field in ORDER[:3]:data=await tools.fill(next(d['reference'] for d in data['fields'] if d['field']==field))
    browser.page.option_count['tags']=0
    result=await tools.fill(next(d['reference'] for d in data['fields'] if d['field']=='tags'))
    assert result['error']=='EXACT_EXISTING_OPTION_UNAVAILABLE' and 'tags' not in browser.page.values
    assert not any(a[0] in {'create','click'} for a in browser.page.actions)


async def test_ambiguous_waba_and_opaque_reference_fail_closed():
    browser,tools=await bound_setup_tools();data=await tools.inspect()
    data=await tools.fill(next(d['reference'] for d in data['fields'] if d['field']=='campaign_name'))
    browser.page.option_count['waba']=2
    result=await tools.fill(next(d['reference'] for d in data['fields'] if d['field']=='waba'))
    assert result['error']=='EXACT_EXISTING_OPTION_UNAVAILABLE' and 'waba' not in browser.page.values
    assert not any(action==('select','waba') for action in browser.page.actions)
    other,other_tools=await bound_setup_tools()
    assert (await other_tools.fill('not-observed'))['error']=='UNOBSERVED_SETUP_ELEMENT'


async def test_uncertain_write_is_terminal_and_never_retried():
    browser,tools=await bound_setup_tools();data=await tools.inspect();browser.page.fail_field='campaign_name'
    ref=data['fields'][0]['reference']
    assert not (await tools.fill(ref))['ok']
    assert not (await tools.fill(ref))['ok']
    assert browser.page.actions==[('fill','campaign_name')]
    assert 'PRIVATE-UI-ERROR' not in json.dumps(browser.trace.events)


async def test_field_order_and_inspection_readonly_phase():
    browser,tools=await bound_setup_tools();data=await tools.inspect()
    result=await tools.fill(next(d['reference'] for d in data['fields'] if d['field']=='tags'))
    assert result['error']=='SETUP_FIELD_ORDER_REQUIRED'
    browser,tools=await bound_setup_tools();await fill_all(tools)
    browser.begin_phase('inspection',130,3)
    assert (await tools.verify())['ok']
    before=list(browser.page.actions)
    browser.page.values['campaign_name']='operator-edited'
    data=await tools.present(await tools.scan())
    result=await tools.fill(data['fields'][0]['reference'])
    assert result['error']=='SETUP_WRITES_CLOSED'
    assert browser.page.values['campaign_name']=='operator-edited' and browser.page.actions==before
    with pytest.raises(PermissionError):browser.claim_login_submission()
    with pytest.raises(SetupStop):await browser.navigate(SETUP_URL)
    assert browser.login_submissions==1


async def test_exact_destination_once_no_arbitrary_same_origin_path():
    browser,tools=setup_tools()
    with pytest.raises(SetupStop):await browser.navigate(SETUP_URL+'/other')
    await browser.open_setup_tab(tools.grant)
    await browser.navigate(SETUP_URL)
    with pytest.raises(SetupStop):await browser.navigate(SETUP_URL)
    assert browser.setup_navigation_count==1


def test_poc_grant_and_stage_request_budgets_are_separate_from_auth_certification():
    browser=session()
    good=Observation(expected_host=True)
    healthy_observation(browser,good)
    assert not good.dashboard and not good.evidence_configured
    with pytest.raises(SetupStop):SetupTools(browser,PocGrant(session()))
    budget=Phase1Budget(Phase1Limits())
    for _ in range(6):budget.claim('doer')
    with pytest.raises(RuntimeError):budget.claim('doer')
    budget.phase='setup'
    for _ in range(8):budget.claim('doer')
    for _ in range(2):budget.claim('teacher')
    assert budget.requests==16 and budget.stage_requests=={'login':6,'setup':8}
    with pytest.raises(RuntimeError):budget.claim('teacher')


class SetupModelDouble(SyntheticChatModel):
    cursor:int=0
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.calls+=1
        last=next((m for m in reversed(messages) if isinstance(m,ToolMessage)),None)
        if last is None:name,args='observe_page',{}
        else:
            data=json.loads(last.content)
            if self.cursor<4:
                field=ORDER[self.cursor];self.cursor+=1
                target=next(d for d in data['fields'] if d['field']==field)
                if target['matches_required_value']:name,args='verify_field',{'reference':target['reference']}
                elif field=='campaign_name':name,args='fill_text',{'reference':target['reference']}
                else:name,args='select_observed_option',{'reference':target['reference'],'option_reference':target['options'][0]['reference']}
            else:name,args='SetupReport',{'status':'SETUP_FILLED_UNSAVED','reason':'fields_verified'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{
            'name':name,'args':args,'id':uuid4().hex,'type':'tool_call'}]))])


async def test_genuine_deep_agents_setup_doer_uses_only_scoped_tools_with_local_model_double():
    browser,tools=await bound_setup_tools();budget=Phase1Budget(Phase1Limits());budget.phase='setup'
    model=SetupModelDouble();agent,permissions=create_setup_doer(model,tools,browser.trace,budget,offline=True)
    result=await agent.ainvoke({'messages':[{'role':'user','content':'Fill only Setup and stop unsaved.'}]},
        {'configurable':{'thread_id':browser.trace.run_id},'recursion_limit':40})
    assert result['structured_response'].status=='SETUP_FILLED_UNSAVED'
    assert model.calls==6 and browser.trace.openai_api_calls==0 and browser.page.values==VALUES
    assert (await tools.verify())['ok']
    assert permissions.allowed=={'observe_page','inspect_control','scroll_to_control','click_observed_control','fill_text','select_observed_option','verify_field','SetupReport'}
    assert not permissions.allowed.intersection({'submit_login','execute','task','click','navigate_approved'})


async def test_persistence_metadata_does_not_claim_autosave_or_retain_private_request_data():
    from modules.whatsapp.persistence import PersistenceObserver
    observer=PersistenceObserver(session());observer.active=True
    observer.channel.ready=True
    observer.event({'method':'Network.requestWillBeSent','sessionId':'PRIVATE_SESSION','params':{'requestId':'PRIVATE_REQUEST',
        'request':{'method':'POST','url':'https://PRIVATE/PATH','postData':'PRIVATE_BODY'}}})
    observer.event({'method':'Network.responseReceived','sessionId':'PRIVATE_SESSION','params':{'requestId':'PRIVATE_REQUEST','response':{'status':200}}})
    result=observer.summary()
    assert result['non_get_requests_during_setup']=={'POST':1} and result['successful_non_get_responses']==1
    assert result['server_persistence']=='UNDETERMINED' and 'PRIVATE' not in json.dumps(result)


def test_preserved_login_browser_teacher_and_graduation_implementations():
    manifest=json.loads((ROOT/'outputs/whatsapp-handoff-preservation.json').read_text(encoding='utf-8'))
    assert len(manifest)>=14
    for name,digest in manifest.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest


def dom_fixture():
    labels={'campaign_name':'Campaign name','waba':'WABA ID','business_number':'Business number','tags':'Add tags'}
    controls=[]
    for key,label in labels.items():
        controls.append({'tag':'label','attrs':{'for':key},'text':label})
        if key=='campaign_name':controls.append({'tag':'input','attrs':{'id':key,'value':'agent_test'}})
        else:
            option_value=VALUES[key] if key=='waba' else 'actual-observed-option-id'
            controls.append({'tag':'select','attrs':{'id':key,'multiple':key=='tags'},'children':[
                {'tag':'option','attrs':{'value':option_value,'selected':True},'text':VALUES[key] if key!='waba' else 'Business display name'}]})
    buttons=[{'tag':'button','attrs':{'id':key,'type':'button'},'text':label} for key,label in
        [('next','NEXT STEP'),('later','FINISH LATER'),('save','Save'),('send','Send'),('audience','Audience')]]
    return [{'tag':'h1','text':'WhatsApp campaign'},{'tag':'li','attrs':{'class':'active'},'text':'Setup'},
        {'tag':'form','attrs':{'id':'form'},'children':controls+buttons}]


def run_dom(expression,children=None,events=None):
    import shutil,subprocess
    node=shutil.which('node')
    if not node:pytest.skip('Node required for deterministic DOM expressions')
    payload={'expression':expression,'children':children or dom_fixture(),'installed':events is None,'events':events}
    result=subprocess.run([node,str(Path(__file__).with_name('whatsapp_dom_double.js'))],input=json.dumps(payload),
        text=True,capture_output=True,check=True,timeout=5)
    return json.loads(result.stdout)


def scan_dom(children=None):
    from modules.whatsapp.dom import SCAN
    from modules.whatsapp.contract import LABELS
    return run_dom(SCAN.replace('SETTINGS',json.dumps({'prefix':'test-only','labels':LABELS,'values':VALUES})),children)


def test_actual_dom_script_maps_real_labels_native_values_and_setup_identity():
    result=scan_dom()
    assert result['same_form'] and result['setup_active'] and result['page_identity']
    assert all(f['discovered'] and f['selected'] for f in result['fields'].values())
    assert result['fields']['waba']['value']==VALUES['waba']
    assert result['fields']['business_number']['value']=='actual-observed-option-id'
    assert all('data-netcore-setup' in f['selector'] for f in result['fields'].values())


def test_dom_waba_display_similarity_is_not_identifier_evidence():
    children=dom_fixture()
    waba=children[2]['children'][3]
    waba['children'][0]['attrs']['value']='DIFFERENT-WABA-ID'
    waba['children'][0]['text']='Business display name '+VALUES['waba']
    result=scan_dom(children)
    assert not result['fields']['waba']['selected'] and result['fields']['waba']['exact_option_count']==0


def test_dom_existing_tag_is_case_sensitive_and_creatable_options_are_excluded():
    children=dom_fixture();tags=children[2]['children'][7]
    tags['children'][0]['text']='Test'
    assert scan_dom(children)['fields']['tags']['exact_option_count']==0
    tags['children'][0]['text']='test';tags['children'][0]['attrs']['data-select2-tag']='true'
    assert not scan_dom(children)['fields']['tags']['selected']


def test_dom_duplicate_label_or_wrong_active_step_is_not_certified():
    children=dom_fixture();children[2]['children'].append({'tag':'input','attrs':{'id':'duplicate','aria-label':'Campaign name'}})
    assert not scan_dom(children)['fields']['campaign_name']['discovered']
    children=dom_fixture();children.append({'tag':'li','attrs':{'class':'active'},'text':'Audience'})
    assert not scan_dom(children)['setup_active']


def test_capture_guard_prevents_commit_step_clicks_enter_and_submit():
    from modules.whatsapp.dom import INSTALL_GUARD
    events=[{'type':'click','id':key} for key in ['next','later','save','send','audience']]
    events += [{'type':'submit','id':'form'},{'type':'keydown','id':'campaign_name','key':'Enter'},
        {'type':'click','id':'waba'}]
    result=run_dom(INSTALL_GUARD,events=events)
    assert result['denied']==[True]*7+[False] and result['attempts']==7


async def test_rejected_unknown_tool_attempt_prevents_false_no_forbidden_transition_fact():
    browser,tools=await bound_setup_tools()
    browser.trace.tool('save_campaign','doer','requested')
    result=await tools.inspect()
    assert result['error']=='FORBIDDEN_TOOL_ATTEMPTED' and not browser.page.actions


async def test_checkpoint_refusal_has_no_navigation_no_setup_and_preserves_auth_result():
    from modules.whatsapp.workflow import continue_setup
    browser=session();browser.page.location='https://'+browser.config.expected_host+'/'
    browser.observe=AsyncMock(return_value=Observation(expected_host=True))
    login_tools=SimpleNamespace(terminal_error=False)
    result={'verification':'UNEXPECTED_PAGE','reason_code':'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED','missing_evidence':['dashboard'],'error':None}
    async def declined(*args):raise SetupStop('POC_CHECKPOINT_DECLINED')
    await continue_setup(browser,login_tools,result,None,Phase1Budget(Phase1Limits()),Phase1Limits(),checkpoint=declined)
    assert result['poc_checkpoint']=='NOT_ACCEPTED' and result['authentication_verification']=='UNEXPECTED_PAGE'
    assert result['setup_stop_reason']=='POC_CHECKPOINT_DECLINED' and not browser.page.actions


@pytest.mark.parametrize('mode',['success','inspection_edit','checkpoint_declined','existing_verified','existing_unverified','outer_cleanup'])
async def test_composed_login_setup_hold_cleanup_use_one_owned_session(monkeypatch,tmp_path,mode):
    import deep_agents.controller as controller
    import modules.whatsapp.workflow as workflow
    from deep_agents.schemas import DoerReport
    from modules.whatsapp.contract import SetupReport
    browser=session();browser.login_submissions=0;browser.page.location=browser.config.login_url
    original_page=browser.page;original_profile=browser.profile
    async def start():browser.trace.add('initialize')
    async def close():
        assert browser.profile is original_profile
        assert browser.login_page is original_page if browser.setup_tab_attempted else browser.page is original_page
        browser.trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED';browser.trace.add('close')
    browser.start=AsyncMock(side_effect=start);browser.close=AsyncMock(side_effect=close)
    current=Observation(expected_host=True)
    if mode=='existing_verified':
        current=Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True)
        browser.evidence=SimpleNamespace(reviewed=True)
    browser.observe=AsyncMock(return_value=current)
    browser.verification_details={'reason':'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED','missing':['dashboard','tenant_matches','readonly_access','evidence_configured']}
    login_tools=SimpleNamespace(terminal_error=False,navigation_count=1,entry_url=browser.config.login_url)
    class LoginAgent:
        async def ainvoke(self,*args):
            browser.login_submissions=0 if mode.startswith('existing_') else 1;browser.page.location='https://'+browser.config.expected_host+'/'
            browser.trace.step_facts=[]
            if browser.login_submissions:
                browser.trace.add('entry');browser.trace.fact('LOGIN_RECOGNIZED');browser.trace.fact('SUBMITTED_ONCE')
            return {'structured_response':DoerReport(status='blocked',reason='wrong_context')}
    monkeypatch.setattr(controller,'create_doer',lambda *args,**kwargs:(LoginAgent(),None))
    monkeypatch.setattr(controller,'ROOT',tmp_path)
    class Observer:
        def __init__(self,browser):self.active=False
        async def start(self):pass
        async def close(self):pass
        def summary(self):return {'server_persistence':'UNDETERMINED'}
    monkeypatch.setattr(workflow,'PersistenceObserver',Observer)
    real_factory=create_setup_doer
    monkeypatch.setattr(workflow,'create_setup_doer',lambda model,tools,trace,budget:real_factory(model,tools,trace,budget,offline=True))
    async def checkpoint(session,seconds):
        assert session is browser and browser.start.await_count==1 and browser.close.await_count==0
        if mode=='checkpoint_declined':raise SetupStop('POC_CHECKPOINT_DECLINED')
        return PocGrant(browser)
    async def hold(seconds):
        assert browser.page.values==VALUES and browser.close.await_count==0
        assert browser.task_phase=='inspection'
        if mode=='inspection_edit':browser.page.values['campaign_name']='operator-edited'
    budget=Phase1Budget(Phase1Limits());model=SetupModelDouble()
    async def continuation(session,tools,result):
        await workflow.continue_setup(session,tools,result,model,budget,Phase1Limits(),checkpoint=checkpoint,hold=hold,authorized_mutations=True)
    result=await controller._execute_doer(browser,login_tools,'login',None,budget,True,after_login=continuation,login_timeout=1,close_session=mode!='outer_cleanup')
    if mode=='outer_cleanup':
        assert browser.close.await_count==0 and browser.trace.cleanup_result=='NOT_RUN'
        await browser.close();result['cleanup_result']=browser.trace.cleanup_result
    assert browser.start.await_count==browser.close.await_count==1
    assert browser.login_submissions==(0 if mode.startswith('existing_') else 1)
    assert result['verification']==('SETUP_FILLED_UNSAVED' if mode in {'success','existing_verified','outer_cleanup'} else 'UNEXPECTED_PAGE')
    assert result['authentication_verification']==('AUTHENTICATED_AND_VERIFIED' if mode=='existing_verified' else 'UNEXPECTED_PAGE')
    assert (browser.trace.verification_snapshot is not None)==(mode=='existing_verified')
    if mode.startswith('existing_'):assert not any(f['kind']=='SUBMITTED_ONCE' for f in browser.trace.step_facts)
    assert result['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert sum(a[0]=='navigate' for a in browser.page.actions)==(0 if mode in {'checkpoint_declined','existing_unverified'} else 1)
    assert result['poc_checkpoint']==('NOT_ACCEPTED' if mode in {'checkpoint_declined','existing_unverified'} else 'HUMAN_CONFIRMED_FOR_POC')
    if mode=='inspection_edit':assert not result['setup_gates_current'] and result['setup_stop_reason']=='FIELD_VALUE_NOT_VERIFIED'
    assert browser.trace.events[-1]['action_category']==('close' if mode=='outer_cleanup' else 'outcome')
    assert browser.guard.primary_session=='source-session'
    assert original_page in browser.browser.context.items
    if mode not in {'checkpoint_declined','existing_unverified'}:assert browser.page is not original_page and browser.browser.context.creations==1
    assert not any('NEXT STEP' in str(a) for a in browser.page.actions)


def review_fixture():
    from deep_agents.evidence import compile_evidence
    from modules.whatsapp.review import WhatsAppReviewAdapter
    trace=Trace()
    for kind in ['LOGIN_RECOGNIZED','SUBMITTED_ONCE','HUMAN_CONFIRMED_FOR_POC','SETUP_FIELDS_VERIFIED']:
        trace.add('entry');trace.fact(kind)
    trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED';trace.add('close')
    result={'run_id':trace.run_id,'source':'test_double','verification':'SETUP_FILLED_UNSAVED',
        'reason_code':'SETUP_VALUES_VERIFIED_UNSAVED','authentication_verification':'UNEXPECTED_PAGE',
        'poc_checkpoint':'HUMAN_CONFIRMED_FOR_POC','setup_status':'SETUP_FILLED_UNSAVED',
        'setup_gates':{k:'PASS' for k in ORDER},'setup_gates_current':True,'cleanup_result':trace.cleanup_result,'step_facts':trace.step_facts,
        'login_submissions':1,'submission_state':'COMPLETED'}
    adapter=WhatsAppReviewAdapter(result,trace.events);package=compile_evidence(result,trace.events,adapter)
    return trace,result,adapter,package


def test_teacher_package_distinguishes_poc_setup_from_auth_and_campaign_creation():
    from deep_agents.review_contract import ReviewDecision,EvidenceError
    trace,result,adapter,package=review_fixture()
    assert package.outcome=='success' and package.verified_result=='SETUP_FILLED_UNSAVED'
    assert package.module_evidence['authentication_verification']=='UNEXPECTED_PAGE'
    assert package.workflow['supported_rules']==[]
    decision=ReviewDecision(outcome='success',decision='no_change',finding='Four Setup fields verified; remains unsaved.',
        evidence_events=list(range(5)),strategy_rule=None,proposed_change='')
    adapter.validate(decision,package)
    with pytest.raises(EvidenceError):adapter.validate(decision.model_copy(update={'finding':'Campaign was published successfully.'}),package)
    with pytest.raises(EvidenceError):adapter.validate(decision.model_copy(update={'finding':'AUTHENTICATED_AND_VERIFIED'}),package)
    with pytest.raises(EvidenceError):adapter.validate(decision.model_copy(update={'evidence_events':[0,1,2,3]}),package)


def test_learning_artifact_is_draft_only_and_retains_test_provenance(tmp_path):
    from modules.whatsapp.workflow import write_draft
    trace,result,adapter,package=review_fixture()
    result['teacher_status']='ANALYZED'
    artifact=write_draft(result,trace.events,tmp_path)
    data=json.loads((Path(artifact['path'])/'DRAFT.json').read_text())
    assert data['state']=='DRAFT' and data['source']=='test_double'
    assert not data['live_certified'] and not data['runtime_loading_allowed'] and not data['graduation_allowed']
    assert data['server_creation_state']=='UNDETERMINED' and data['explicit_commit_actions']==0
    assert len(data['evidence_events'])==4 and not (Path(artifact['path'])/'SKILL.md').exists()


async def test_matching_prefix_can_be_reused_without_any_field_mutation():
    browser,tools=await bound_setup_tools();browser.page.values=dict(VALUES)
    data=await tools.inspect()
    for item in data['fields']:assert (await tools.verify_field(item['reference']))['ok']
    assert tools.completed==set(ORDER)
    assert (await tools.verify())['status']=='SETUP_FILLED_UNSAVED'
    assert not browser.page.actions


async def test_disabled_dependent_business_option_is_read_until_available_then_selected(monkeypatch):
    import modules.whatsapp.tools as module
    monkeypatch.setattr(module.asyncio,'sleep',AsyncMock())
    browser,tools=await bound_setup_tools();browser.page.values={'campaign_name':VALUES['campaign_name'],'waba':VALUES['waba']}
    original=browser.page.evaluate;reads=0
    async def delayed(source):
        nonlocal reads
        data=await original(source)
        if isinstance(data,dict) and 'fields' in data:
            reads+=1
            if reads<=3:
                data['fields']['business_number']['exact_option_count']=0
                data['fields']['business_number']['disabled']=True
        return data
    browser.page.evaluate=delayed
    data=await tools.inspect()
    for name in ORDER[:2]:assert (await tools.verify_field(next(d['reference'] for d in data['fields'] if d['field']==name)))['ok']
    # Waiting is now explicit read-only tool progress, rather than an implicit
    # mutation helper loop. No selection occurs while the option is unavailable.
    while reads<4:data=await tools.inspect()
    result=await tools.fill(next(d['reference'] for d in data['fields'] if d['field']=='business_number'))
    assert result['ok'] and browser.page.values['business_number']==VALUES['business_number'] and reads>=4
    assert browser.page.actions==[('select','business_number')]


async def test_inspection_edit_invalidates_completion_and_reports_current_failed_gate():
    browser,tools=await bound_setup_tools();await fill_all(tools)
    browser.begin_phase('inspection',130,3);browser.page.values['campaign_name']='operator-edited'
    result=await tools.verify()
    assert not result['ok'] and result['error']=='FIELD_VALUE_NOT_VERIFIED' and not tools.final_gates_current


def test_custom_tag_option_without_existing_provenance_is_not_selectable():
    children=dom_fixture();form=children[2]
    form['children'][7]={'tag':'div','attrs':{'id':'tags','role':'combobox','aria-label':'Add tags','aria-controls':'tag-popup'},'text':'test'}
    choice={'tag':'div','attrs':{'role':'option','aria-selected':'true'},'text':'test'}
    form['children'].append({'tag':'div','attrs':{'id':'tag-popup','role':'listbox'},'children':[choice]})
    first=scan_dom(children)['fields']['tags']
    assert first['exact_option_count']==0 and not first['selected']
    choice['attrs']['data-existing']='true'
    verified=scan_dom(children)['fields']['tags']
    assert verified['exact_option_count']==1 and verified['selected'] and verified['expanded']


def test_numeric_waba_display_text_cannot_override_a_different_actual_identifier():
    children=dom_fixture();waba=children[2]['children'][3]
    waba['children'][0]['attrs']['value']='DIFFERENT-ID';waba['children'][0]['text']=VALUES['waba']
    assert not scan_dom(children)['fields']['waba']['selected']


async def test_owned_searchable_dropdown_selects_only_proven_existing_option():
    browser,tools=await bound_setup_tools();browser.page.values={k:VALUES[k] for k in ORDER[:3]}
    original=browser.page.evaluate;opened=False;searched=False;calls=[]
    async def custom(source):
        data=await original(source)
        if isinstance(data,dict) and 'fields' in data:
            d=data['fields']['tags'];d.update(kind='OWNED_COMBOBOX',open_selector='#tags',search_selector='#tags-search' if opened else None,
                search_selector_unique=opened,search_visible=opened,search_enabled=opened,search_unobscured=opened,
                option_selector='#tags-option' if searched else None,option_selector_unique=searched,
                value='#tags-option',exact_option_count=1 if searched else 0,expanded=opened)
        return data
    browser.page.evaluate=custom
    def locator(selector):
        class Locator:
            async def count(self,**kwargs):
                assert selector in {'#tags','#tags-search','#tags-option'};return 1
            async def click(self,**kwargs):
                nonlocal opened
                calls.append(('click',selector))
                if selector=='#tags':opened=True
                elif selector=='#tags-option' and searched:browser.page.values['tags']=VALUES['tags']
                else:raise AssertionError('Unowned option')
            async def fill(self,value,**kwargs):
                nonlocal searched
                assert selector=='#tags-search' and value=='test';searched=True;calls.append(('search',selector))
            async def input_value(self,**kwargs):
                assert selector=='#tags-search'
                return VALUES['tags'] if searched else ''
        return Locator()
    browser.page.locator=locator
    data=await tools.inspect()
    for name in ORDER[:3]:assert (await tools.verify_field(next(d['reference'] for d in data['fields'] if d['field']==name)))['ok']
    target=next(d for d in data['fields'] if d['field']=='tags')
    data=await tools.click_observed_control(target['reference'])
    target=next(d for d in data['fields'] if d['field']=='tags')
    data=await tools.fill_text(target['search_reference'])
    target=next(d for d in data['fields'] if d['field']=='tags')
    result=await tools.select_observed_option(target['reference'],target['options'][0]['reference'])
    assert result['ok'] and calls==[('click','#tags'),('search','#tags-search'),('click','#tags-option')]
    assert (await tools.verify())['status']=='SETUP_FILLED_UNSAVED'

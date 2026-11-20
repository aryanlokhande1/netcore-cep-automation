"""Real LangGraph/Deep Agents route with controlled offline boundary adapters."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration

from app.config import Config
from app.model_settings import ModelSettings
from app.state import Observation
from deep_agents.offline import SyntheticChatModel
from learning.skill_storage.backend import SkillSnapshot
from modules.whatsapp.contract import V2Payload, GoalFinding
from modules.whatsapp.tools import V2BrowserTools


class TaskModel(SyntheticChatModel):
    max_tokens:int=1536
    def _generate(self,messages,**kwargs):
        self.calls+=1
        observed=any(isinstance(m,ToolMessage) and m.name=='browser_action' for m in messages)
        message=AIMessage(content='',tool_calls=[{'name':'V2Turn' if observed else 'browser_action',
            'args':{'status':'VERIFY'} if observed else {'action':'observe'},'id':'offline-'+str(self.calls)}],
            usage_metadata={'input_tokens':20,'output_tokens':10,'total_tokens':30})
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.mark.parametrize('authenticated',[False,True])
async def test_actual_graph_requires_independent_login_before_same_browser_task(monkeypatch,tmp_path,authenticated):
    import modules.whatsapp.workflow as workflow
    import modules.whatsapp.tools as tools_module
    import modules.whatsapp.observer as observer_module
    import modules.login.evidence as evidence_module
    import modules.login.credentials as credentials_module
    import browser.stagehand_client as client_module
    import browser.protected_tabs as tabs_module
    import deep_agents.teacher as teacher_module
    import learning.skill_storage.registry as registry
    from app.cli import build_parser

    monkeypatch.setattr(workflow,'ROOT',tmp_path)
    monkeypatch.setattr(Config,'load',classmethod(lambda cls:Config('OFFLINE_NOT_LAUNCHED',expected_host='allowed.invalid')))
    monkeypatch.setattr(ModelSettings,'load',classmethod(lambda cls:ModelSettings(api_key='OFFLINE_NOT_SENT')))
    fields=dict(campaign_name='fixture',waba='fixture',business_number='fixture',tag='fixture',
        ga_tracking=False,audience_type='Lists/Segments',include_audience='fixture',exclude_audience='fixture',
        template='fixture',send_type='Send Later',schedule_datetime='2099-11-18T18:00:00+05:30',
        timezone='Asia/Kolkata',frequency_cap_enabled=True,contact_limit_enabled=False,
        final_action='SAVE_AND_PREVIEW',publish_allowed=False,send_test_allowed=False)
    payload=tmp_path/'payload.json';raw=json.dumps(fields).encode();payload.write_bytes(raw)
    args=build_parser().parse_args(['whatsapp','--payload',str(payload),'--approve-payload-sha256',
        hashlib.sha256(raw).hexdigest(),'--authorized-live','--authorized-inference','--authorized-candidate-evaluation'])
    content='# OFFLINE UNAPPROVED STRATEGY\nRead-only observation only.'
    skill=SkillSnapshot(content,hashlib.sha256(content.encode()).hexdigest(),{'status':'OFFLINE_UNAPPROVED','source_version':2},approved=False)
    monkeypatch.setattr(registry,'whatsapp_skill',lambda **kw:skill)
    monkeypatch.setattr(registry.SkillStore,'snapshot',lambda self:None)
    instances=[];constructed=[]

    class Browser:
        def __init__(self,config,policy,trace,**kw):
            self.config=config;self.trace=trace;self.live_permit=kw['live_permit'];self.login_submissions=1
            self.page=SimpleNamespace(url=AsyncMock(return_value='https://allowed.invalid/observed'))
            self.close_started=False;self.verification_details={'reason':'OFFLINE_CONTROLLED_VERIFIER'}
            self.navigate=AsyncMock();instances.append(self)
        async def close(self):
            self.close_started=True;self.trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED'
    monkeypatch.setattr(client_module,'StagehandClient',Browser)
    monkeypatch.setattr(workflow,'authorize_live',lambda *a:SimpleNamespace(origins={'https://allowed.invalid'},started=False))
    monkeypatch.setattr(credentials_module.LocalCredentials,'load',classmethod(lambda cls:object()))
    monkeypatch.setattr(credentials_module,'CredentialBroker',lambda *a:object())
    monkeypatch.setattr(workflow,'HandoffLoginTools',lambda *a:SimpleNamespace(terminal_error=None))
    async def login(browser,*a,**kw):
        assert kw['close_session'] is False
        return {'run_id':browser.trace.run_id,'verification':'NOT_TESTED','step_facts':[]}
    monkeypatch.setattr(workflow,'_execute_doer',login)
    monkeypatch.setattr(workflow,'openai_model',lambda *a,**kw:TaskModel())
    observation=Observation(allowed=True,expected_host=True,dashboard=authenticated,
        tenant_matches=authenticated,readonly_access=authenticated,evidence_configured=authenticated)
    async def verify(browser):
        assert browser is instances[0]
        return observation
    monkeypatch.setattr(evidence_module,'stable_authentication',verify)
    async def protected_tab(browser):
        assert browser is instances[0]
        return SimpleNamespace(),SimpleNamespace()
    monkeypatch.setattr(tabs_module,'create_protected_tab',protected_tab)

    class Tools(V2BrowserTools):
        def __init__(self,browser,payload,ui,authorization,directory):
            assert authenticated and browser is instances[0] and not browser.close_started
            self.browser=browser;self.payload=payload;self.ui=ui;self.authorization=authorization
            self.stage='setup';self.events=[];self.failed_approaches={};self.preparations={}
            self.last_observation=None
            self.lock=asyncio.Lock();self.pending_write=False;self.terminal=None
            self.protect=AsyncMock();self.install_guard=AsyncMock();constructed.append(self)
        async def observe(self):return {'read_only_discovery':self.read_only,'fields':{},'fixture':True}
    monkeypatch.setattr(tools_module,'V2BrowserTools',Tools)
    async def observer(*a,**kw):
        return GoalFinding(goal='setup',status='UNKNOWN',observed_state='UNKNOWN',
            expected_state='EXACT_STAGE_VALUES',progress=False,recoverable=False,
            blocker_category='recognition',evidence_refs=[]),{'fields':{},'stage':'setup'}
    monkeypatch.setattr(observer_module,'observe_goal',observer)
    teacher_configs=[];teacher_contents=[]
    def no_teacher(*a,**kw):
        async def invoke(message,config):
            teacher_configs.append(config)
            teacher_contents.append(message['messages'][0]['content'])
            raise RuntimeError('OFFLINE_TEACHER_TRANSPORT_DISABLED')
        return SimpleNamespace(ainvoke=invoke),None
    monkeypatch.setattr(teacher_module,'create_teacher',no_teacher)
    result=await workflow.run_v2(args)
    trace=instances[0].trace
    routes=[e['agent_execution'] for e in trace.events if e.get('agent_execution',{}).get('kind')=='graph_route']
    assert len(instances)==1 and instances[0].close_started
    assert any(r['destination']=='teacher' for r in routes)
    assert any(r['destination']=='cleanup' for r in routes)
    assert result['live_certified'] is False
    selected=next(e['agent_execution'] for e in trace.events if e.get('agent_execution',{}).get('kind')=='skill_selected')
    assert selected['version']==2 and selected['approved'] is False
    assert teacher_configs and teacher_configs[0].get('configurable',{}).get('thread_id')
    from deep_agents.evidence import canonical
    assert teacher_contents[0]==canonical(json.loads(teacher_contents[0]))
    if authenticated:
        assert any(r['source']=='start' and r['destination']=='doer' for r in routes)
        assert any(r['source']=='doer' and r['destination']=='observer' for r in routes)
        assert constructed[0].read_only
        assert any(e['action']=='observe' and e['ok'] for e in constructed[0].events)
        denied,_=await constructed[0].action({'action':'type','field':'campaign_name','value':'UNAUTHORIZED'})
        assert not denied['ok']
        assert any(e.get('agent_execution',{}).get('kind')=='skill_loaded' for e in trace.events)
        assert any(f['kind']=='SKILL_READ' for f in trace.step_facts)
    else:
        assert not constructed
        assert not any(r['destination']=='doer' for r in routes)

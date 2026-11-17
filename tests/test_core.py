import json
import os
from dataclasses import replace
import pytest
from app.state import Observation, Result
from app.config import Config, OriginPolicy
from app.trace import Trace
from app.model import ReplayReasoner
from app.graph import LoginWorkflow
from langgraph.types import Command
from modules.login.verifier import verify

@pytest.mark.parametrize('flags,expected', [
    ({}, Result.UNEXPECTED_PAGE),
    ({'credential_form':True},Result.HUMAN_ACTION_REQUIRED),
    ({'challenge':True},Result.HUMAN_ACTION_REQUIRED),
    ({'rejected':True,'credential_form':True},Result.AUTH_FAILED),
    ({'expired':True},Result.SESSION_EXPIRED),
    ({'allowed':False,'challenge':True},Result.UNEXPECTED_PAGE),
    ({'dashboard':True,'expected_host':True},Result.UNEXPECTED_PAGE),
    ({'dashboard':True,'expected_host':True,'tenant_matches':True,'readonly_access':True,'evidence_configured':True},Result.AUTHENTICATED_AND_VERIFIED),
])
def test_verifier(flags,expected):
    assert verify(Observation(**flags)) == expected

@pytest.mark.parametrize('missing', ['expected_host','dashboard','tenant_matches','readonly_access','evidence_configured'])
def test_each_positive_evidence_is_required(missing):
    flags = dict(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True)
    flags[missing] = False
    assert verify(Observation(**flags)) == Result.UNEXPECTED_PAGE

@pytest.mark.parametrize('url', ['http://login.netcoresmartech.com/','https://login.netcoresmartech.com.evil.test/',
    'https://login.netcoresmartech.com@evil.test/','https://login.netcoresmartech.com:444/',
    'javascript:alert(1)','https://evil.test/','https://user:secret@login.netcoresmartech.com/'])
def test_origin_policy(url):
    assert not OriginPolicy({'https://login.netcoresmartech.com'}).permits(url)

def test_trace_privacy_schema(tmp_path):
    trace=Trace()
    trace.add('observe',Observation(credential_form=True))
    path=trace.write(tmp_path)
    data=json.loads(path.read_text())
    assert all(isinstance(value,bool) for value in data[0]['observation'].values())
    with pytest.raises(Exception):
        Observation(password='must-not-pass')
    with pytest.raises(ValueError):
        trace.add('raw HTML')

class FakeBrowser:
    def __init__(self):
        self.trace=Trace()
        self.config=Config('unused',max_handoffs=1)
        self.authenticated=False
        self.start_count=0
    async def start(self): self.start_count+=1
    async def navigate(self,url): pass
    async def observe(self):
        return Observation(credential_form=not self.authenticated, expected_host=True,
            dashboard=self.authenticated,tenant_matches=self.authenticated,
            readonly_access=self.authenticated,evidence_configured=True)

async def test_checkpoint_resume_does_not_repeat_initialization():
    browser=FakeBrowser()
    workflow=LoginWorkflow(browser,ReplayReasoner())
    workflow.url='unused'
    config={'configurable':{'thread_id':'test'}}
    paused=await workflow.graph.ainvoke({},config)
    assert paused['__interrupt__']
    assert paused['verification']==Result.HUMAN_ACTION_REQUIRED
    browser.authenticated=True
    done=await workflow.graph.ainvoke(Command(resume='completed'),config)
    assert done['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert browser.start_count==1
    snapshot=workflow.graph.get_state(config)
    assert 'password' not in json.dumps(snapshot.values)

async def test_cancel_and_invalid_resume_payload():
    browser=FakeBrowser()
    workflow=LoginWorkflow(browser,ReplayReasoner())
    workflow.url='unused'
    config={'configurable':{'thread_id':'cancel'}}
    await workflow.graph.ainvoke({},config)
    done=await workflow.graph.ainvoke(Command(resume={'password':'not-accepted'}),config)
    assert done['cancelled']
    assert done['verification']==Result.HUMAN_ACTION_REQUIRED

async def test_handoff_limit_stops_without_retrying():
    browser=FakeBrowser()
    workflow=LoginWorkflow(browser,ReplayReasoner())
    workflow.url='unused'
    config={'configurable':{'thread_id':'limit'}}
    await workflow.graph.ainvoke({},config)
    done=await workflow.graph.ainvoke(Command(resume='completed'),config)
    assert not done.get('__interrupt__')
    assert done['verification']==Result.HUMAN_ACTION_REQUIRED

async def test_model_budget():
    browser=FakeBrowser()
    browser.config=replace(browser.config,max_model_turns=0)
    workflow=LoginWorkflow(browser,ReplayReasoner())
    workflow.url='unused'
    done=await workflow.graph.ainvoke({}, {'configurable':{'thread_id':'budget'}})
    assert not done.get('__interrupt__')
    assert browser.trace.model_calls==0

def test_approval_binds_exact_configuration():
    from app.approval import ReviewedApproval, configuration_digest
    reviewed={'channel':'future','recipients':['synthetic'],'content':'reviewed'}
    approval=ReviewedApproval(configuration_digest(reviewed),True)
    assert approval.permits(dict(reviewed))
    assert not approval.permits({**reviewed,'content':'changed'})
    assert not ReviewedApproval(approval.digest,False).permits(reviewed)

async def test_remaining_password_form_does_not_request_second_submission():
    browser=FakeBrowser()
    browser.config=replace(browser.config,max_handoffs=2)
    workflow=LoginWorkflow(browser,ReplayReasoner())
    workflow.url='unused'
    config={'configurable':{'thread_id':'no-retry'}}
    await workflow.graph.ainvoke({},config)
    done=await workflow.graph.ainvoke(Command(resume='completed'),config)
    assert not done.get('__interrupt__')
    assert done['verification']==Result.HUMAN_ACTION_REQUIRED

def test_login_entry_without_password_field_requires_human():
    assert verify(Observation(login_entry=True)) == Result.HUMAN_ACTION_REQUIRED

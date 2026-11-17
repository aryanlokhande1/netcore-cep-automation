import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from langchain_core.messages import HumanMessage,AIMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from app.trace import Trace
from app.state import Observation,Result
from deep_agents.controller import review_completed
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget
from evals.teacher_smoke import synthetic_fixture
from learning.three_run import safety_stop

class CanonicalTeacher(SyntheticChatModel):
    wrong_index:bool=False
    wrong_outcome:bool=False
    def _generate(self,messages,**kwargs):
        self.calls+=1
        data=json.loads(next(m.content for m in reversed(messages) if isinstance(m,HumanMessage)))
        assert [e['event_index'] for e in data['events']]==list(range(len(data['events'])))
        assert not any('source_event_index' in e for e in data['events'])
        assert data['evidence_index_contract']['maximum']==len(data['events'])-1
        args={'outcome':'failure' if self.wrong_outcome else data['outcome'],'change_required':False,
            'finding':'Supported steps were inspected; preserve the supplied verification outcome.',
            'proposed_change':'','strategy_rule':None,'analysis_kind':'no_change','evidence_events':[999 if self.wrong_index else data['events'][-1]['event_index']]}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{'id':uuid4().hex,'name':'TeacherProposal','args':args,'type':'tool_call'}]))])

@pytest.mark.parametrize('verification',[Result.AUTHENTICATED_AND_VERIFIED,Result.UNEXPECTED_PAGE])
async def test_canonical_teacher_indices_and_outcomes_are_unambiguous(verification):
    source,events=synthetic_fixture()
    trace=Trace();trace.events=events
    result={'verification':str(verification),'reason_code':'VERIFIED_SUCCESS' if verification==Result.AUTHENTICATED_AND_VERIFIED else 'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED','teacher_proposal':None}
    model=CanonicalTeacher(role='teacher')
    await review_completed(result,trace,model,InferenceBudget(2,role_limits={'doer':0,'teacher':2}),True)
    assert result['teacher_status']=='ANALYZED' and model.calls==1
    assert result['teacher_validation_summary']['out_of_range_citations']==0

@pytest.mark.parametrize('kwargs,code',[({'wrong_index':True},'EVIDENCE_INDEX_OUT_OF_RANGE'),({'wrong_outcome':True},'OUTCOME_MISMATCH')])
async def test_rejection_is_not_silently_accepted_and_has_an_exact_safe_code(kwargs,code):
    trace=Trace();trace.add('verify',Observation(),Result.AUTHENTICATED_AND_VERIFIED)
    result={'verification':str(Result.AUTHENTICATED_AND_VERIFIED),'teacher_proposal':None}
    await review_completed(result,trace,CanonicalTeacher(role='teacher',**kwargs),InferenceBudget(2),True)
    assert result['teacher_status']=='ANALYSIS_UNAVAILABLE' and result['teacher_proposal'] is None
    assert result['teacher_diagnostic']['validation_code']==code
    assert result['teacher_rejected_proposal'] and result['teacher_validation_summary']['event_count']==1

def test_failed_teacher_stops_an_otherwise_verified_live_iteration():
    result={'verification':str(Result.AUTHENTICATED_AND_VERIFIED),'login_submissions':1,'submission_state':'COMPLETED',
        'cleanup_result':'CLOSED_AND_PROFILE_REMOVED','teacher_status':'ANALYSIS_UNAVAILABLE','_events':[]}
    assert safety_stop(result,live=True)=='TEACHER_ANALYSIS_FAILED_NO_FURTHER_SUBMISSION'
    assert safety_stop(result,live=False) is None

async def test_private_discovery_uses_only_observed_cached_json_without_network_replay(tmp_path):
    from app.config import Config,OriginPolicy
    from app.safety import authorize_live
    from browser.stagehand_client import StagehandClient
    from browser.discovery import ReadOnlyDiscovery
    from pydantic import SecretStr
    config=Config('unused',live_login_enabled=True,evidence_file=str(tmp_path/'evidence.json'))
    permit=authorize_live(config,True,8);permit.started=True
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    browser.expected_account=SecretStr('SYNTHETIC-PRIVATE-ACCOUNT')
    class Page:
        async def url(self):return 'https://tprappin2r.netcoresmartech.com/'
        async def evaluate(self,expression):return {
            'dashboard_candidates':[{'selector':'main','frame_selectors':[],'kind':'CONTENT_HEADING'}],
            'tenant_candidates':[{'selector':'#tenant','frame_selectors':[],'kind':'EXACT_EXPECTED_ACCOUNT_TEXT'}]}
    browser.page=Page();commands=[]
    async def send(method,params,session):
        commands.append(method)
        return {'body':json.dumps({'authenticated':True,'account':'SYNTHETIC-PRIVATE-ACCOUNT','unused_private_token':'SYNTHETIC-PRIVATE-TOKEN'})}
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set(),send=send,primary_session='guard-session',
        session_states={'guard-session':SimpleNamespace(loader_id='private-loader',frame_id='private-frame')})
    discovery=ReadOnlyDiscovery(browser)
    discovery.channel=SimpleNamespace(ready=True,failure=None,active_sessions={'private-session'},send=send)
    url='https://tprappin2r.netcoresmartech.com/observed-fixture-context'
    discovery.observe_network({'method':'Network.requestWillBeSent','sessionId':'private-session','params':{'requestId':'private-request','loaderId':'private-loader','frameId':'private-frame','request':{'url':url,'method':'GET'}}})
    discovery.observe_network({'method':'Network.responseReceived','sessionId':'private-session','params':{'requestId':'private-request','response':{'status':200,'mimeType':'application/json'}}})
    discovery.observe_network({'method':'Network.loadingFinished','sessionId':'private-session','params':{'requestId':'private-request'}})
    summary=await discovery.prepare()
    assert summary['unreviewed_draft_written'] and summary['operator_approval_required'] and summary['network_requests_replayed']==0
    assert commands==['Network.getResponseBody']
    text=json.dumps(summary)+(tmp_path/'evidence.json').read_text()+(tmp_path/'netcore-evidence-discovery.json').read_text()
    assert 'SYNTHETIC-PRIVATE' not in text and 'private-session' not in text and 'private-request' not in text
    assert not json.loads((tmp_path/'evidence.json').read_text())['reviewed']

async def test_guard_failure_is_categorized_without_private_protocol_text():
    from browser.navigation_guard import NavigationGuard
    from app.config import OriginPolicy
    guard=NavigationGuard(OriginPolicy(set()))
    async def reject(*a,**k):raise RuntimeError('SYNTHETIC-PRIVATE-PROTOCOL-MARKER')
    guard.send=reject
    await guard._event({'method':'Target.attachedToTarget','params':{'sessionId':'private-session','targetInfo':{'type':'page'}}})
    assert guard.failed and guard.failure_diagnostic=={'stage':'TARGET_ATTACH','category':'COMMAND_OR_PROTOCOL_FAILURE','code':'PROTOCOL_COMMAND_FAILED'}
    assert 'PRIVATE' not in json.dumps(guard.failure_diagnostic)

async def test_post_submit_inspection_does_not_erase_recorded_field_attribution():
    from app.config import Config,OriginPolicy
    from app.safety import authorize_live
    from browser.stagehand_client import StagehandClient
    from deep_agents.live_tools import LiveBrowserTools
    config=Config('unused',live_login_enabled=True)
    permit=authorize_live(config,True,8);permit.started=True
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    browser.trace.role_evidence={'password':['PASSWORD_TYPE']}
    browser.verification_details={}
    class Page:
        async def evaluate(self,*a,**k):return []
    browser.page=Page()
    async def observed():return Observation(expected_host=True)
    browser.observe=observed
    tools=LiveBrowserTools(browser,None)
    await tools._inspect()
    assert browser.trace.role_evidence=={'password':['PASSWORD_TYPE']}

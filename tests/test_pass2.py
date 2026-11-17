import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from pydantic import SecretStr
import pytest
from app.config import Config,OriginPolicy,ConfigurationError
from app.safety import authorize_live,require_live_browser
from app.trace import Trace
from app.state import Observation,Result
from browser.stagehand_client import StagehandClient
from browser.diagnostics import ScreenshotDiagnostics
from modules.login.credentials import LocalCredentials,CredentialBroker
from modules.login.evidence import EvidenceConfig,observe_live
from learning.policies import Strategy
from learning.skill_storage.registry import SkillStore,validate_skill
from learning.skill_storage.generation import render_skill,REQUIRED_FACTS
from learning.skill_storage.backend import ApprovedSkillBackend,SKILL_PATH

@pytest.mark.parametrize('authorized,budget,enabled,headless',[
    (False,8,True,False),(True,0,True,False),(True,9,True,False),
    (True,8,False,False),(True,8,True,True)])
def test_live_authorization_requires_every_gate(authorized,budget,enabled,headless):
    with pytest.raises(ConfigurationError):
        authorize_live(Config('unused',headless=headless,live_login_enabled=enabled),authorized,budget)


def test_live_capability_is_one_launch_and_config_bound():
    config=Config('unused',live_login_enabled=True)
    permit=authorize_live(config,True,8)
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    require_live_browser(browser,starting=True)
    with pytest.raises(PermissionError):
        require_live_browser(browser,starting=True)
    browser.claim_login_submission()
    with pytest.raises(PermissionError):
        browser.claim_login_submission()
    other=StagehandClient(Config('unused',live_login_enabled=True),OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    with pytest.raises(PermissionError):
        require_live_browser(other)

@pytest.mark.parametrize('origin',['http://auth.example','https://user@auth.example','https://auth.example/path','https://auth.example?session=x'])
def test_extra_origin_requires_exact_reviewed_https(origin):
    with pytest.raises(ConfigurationError):
        authorize_live(Config('unused',live_login_enabled=True),True,8,[origin])


def test_private_credentials_are_redacted_and_never_model_arguments(monkeypatch):
    marker='SYNTHETIC-PRIVATE-CREDENTIAL'
    for key in ('NETCORE_ACCOUNT_NAME','NETCORE_USERNAME','NETCORE_PASSWORD'):
        monkeypatch.setenv(key,marker)
    credentials=LocalCredentials.load()
    assert marker not in repr(credentials)
    from deep_agents.live_tools import LiveBrowserTools
    config=Config('unused',live_login_enabled=True)
    permit=authorize_live(config,True,8)
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    tools=LiveBrowserTools(browser,CredentialBroker(browser,credentials)).registered_tools()
    schemas=json.dumps([t.args_schema.model_json_schema() for t in tools])
    assert marker not in schemas
    submit=next(t for t in tools if t.name=='submit_login')
    assert set(submit.args_schema.model_fields)=={'account_reference','username_reference','password_reference','submit_reference'}

class BrokerPage:
    def __init__(self,valid=True,url='https://login.netcoresmartech.com/'):
        self.valid,self.current_url=valid,url
        self.fills=[];self.clicks=0
    async def url(self):return self.current_url
    async def evaluate(self,expression):return self.valid
    def locator(self,selector):
        page=self
        class Locator:
            async def fill(self,value):page.fills.append((selector,value))
            async def click(self):page.clicks+=1
        return Locator()


def broker_fixture(valid=True,url='https://login.netcoresmartech.com/'):
    config=Config('unused',live_login_enabled=True)
    permit=authorize_live(config,True,8)
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    permit.started=True
    browser.page=BrokerPage(valid,url)
    async def observe():
        return Observation(credential_form=not browser.login_submissions,expected_host=bool(browser.login_submissions))
    browser.observe=observe
    mapping={key:{'key':key,'selector':f'[data-ref="{key}"]'} for key in ('account','username','password','submit')}
    credentials=LocalCredentials('synthetic-private-account','synthetic-private-user','synthetic-private-pass')
    return browser,CredentialBroker(browser,credentials),mapping

async def test_broker_submits_once_without_credential_trace():
    browser,broker,mapping=broker_fixture()
    await broker.submit(mapping)
    with pytest.raises(PermissionError):
        await broker.submit(mapping)
    assert browser.page.clicks==1 and browser.login_submissions==1
    assert len(browser.page.fills)==3
    text=json.dumps(browser.trace.events)+json.dumps(browser.trace.summary())
    assert 'synthetic-private-' not in text

@pytest.mark.parametrize('valid,url',[(False,'https://login.netcoresmartech.com/'),(True,'https://evil.example/')])
async def test_broker_rejects_stale_mapping_and_unapproved_origin(valid,url):
    browser,broker,mapping=broker_fixture(valid,url)
    with pytest.raises(PermissionError):
        await broker.submit(mapping)
    assert browser.page.fills==[] and browser.page.clicks==0 and browser.login_submissions==0

async def test_broker_rejects_unknown_roles():
    browser,broker,mapping=broker_fixture()
    mapping['username']['key']='account'
    with pytest.raises(PermissionError):await broker.submit(mapping)
    assert browser.login_submissions==0


def test_private_readiness_does_not_expose_values(monkeypatch):
    from app.cli import readiness
    marker='SYNTHETIC-PRIVATE-CREDENTIAL'
    for key in ('NETCORE_ACCOUNT_NAME','NETCORE_USERNAME','NETCORE_PASSWORD','OPENAI_API_KEY'):
        monkeypatch.setenv(key,marker)
    report=readiness(Config('unused',live_login_enabled=True))
    assert marker not in json.dumps(report)
    assert report['live_execution']=='NOT_EXECUTED'
    assert report['private_credentials_present']
    assert not report['reviewed_evidence_configured']

class EvidencePage:
    def __init__(self,positive=None,rejected=False):self.positive=positive;self.rejected=rejected
    async def evaluate(self,expression):
        if 'SETTINGS' in expression:raise AssertionError('Interpolation missing')
        if 'const alerts' in expression:
            return {'credential_form':False,'challenge':False,'rejected':self.rejected,'expired':False}
        return self.positive


def evidence_fixture(positive=None):
    evidence=EvidenceConfig(reviewed=True,dashboard_selector='main',tenant_selector='[data-tenant]',readonly_path='/confirmed-context',tenant_json_key='tenant',authenticated_json_key='authenticated')
    return SimpleNamespace(config=Config('unused'),page=EvidencePage(positive),evidence=evidence,
        expected_account=SecretStr('synthetic-private-account'),verification_details=None)

async def test_live_verification_requires_reviewed_authenticated_context():
    from modules.login.verifier import verify
    browser=evidence_fixture({'dashboard':True,'tenant_matches':True,'readonly_access':True,'evidence_configured':True,'mismatch':False})
    result=await observe_live(browser,'https://tprappin2r.netcoresmartech.com/')
    assert verify(result)==Result.AUTHENTICATED_AND_VERIFIED
    assert browser.verification_details['reason']=='VERIFIED_SUCCESS'
    assert 'synthetic-private-account' not in json.dumps(result.model_dump())+json.dumps(browser.verification_details)

async def test_wrong_tenant_and_missing_evidence_stay_unverified():
    from modules.login.verifier import verify
    browser=evidence_fixture({'dashboard':True,'tenant_matches':False,'readonly_access':False,'evidence_configured':True,'mismatch':True})
    result=await observe_live(browser,'https://tprappin2r.netcoresmartech.com/')
    assert verify(result)==Result.UNEXPECTED_PAGE
    assert browser.verification_details['reason']=='INCORRECT_TENANT'
    browser.evidence=None
    result=await observe_live(browser,'https://tprappin2r.netcoresmartech.com/')
    assert verify(result)!=Result.AUTHENTICATED_AND_VERIFIED
    assert 'evidence_configured' in browser.verification_details['missing']

@pytest.mark.parametrize('path',['//evil.example/context','https://evil.example/context','/context?secret=x','/context#x'])
def test_evidence_configuration_cannot_invent_external_probes(tmp_path,path):
    data={'reviewed':True,'dashboard_selector':'main','tenant_selector':'span','readonly_path':path,'tenant_json_key':'tenant','authenticated_json_key':'authenticated'}
    file=tmp_path/'evidence.json';file.write_text(json.dumps(data))
    with pytest.raises(ValueError):EvidenceConfig.load(file)

async def test_live_screenshot_is_skipped_without_capture():
    class Page:
        async def screenshot(self,**kwargs):raise AssertionError('Must not capture live pixels')
    result=await ScreenshotDiagnostics(SimpleNamespace(fixture=False,page=Page())).capture(enabled=True)
    assert result=={'captured':False,'reason':'SCREENSHOT_SKIPPED_PRIVACY'}


def test_cleanup_events_do_not_replace_historical_verification():
    trace=Trace()
    trace.add('verify',Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True),Result.AUTHENTICATED_AND_VERIFIED)
    snapshot=trace.verification_snapshot
    trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED';trace.add('close')
    trace.add('outcome',result=Result.AUTHENTICATED_AND_VERIFIED)
    assert trace.events[-1]['observation'] is None
    assert trace.events[-2]['observation'] is None
    assert trace.verification_snapshot==snapshot


def skill_data(rules=()):
    strategy=Strategy(version=uuid4().hex,rules=rules)
    records=[{'run_id':uuid4().hex,'step_facts':[{'kind':kind,'event_index':0} for kind in REQUIRED_FACTS]}]
    return render_skill(strategy,records),{'passed':True,'skill_loading_verified':True}, {'verified_run_ids':[records[0]['run_id']]}


def make_candidate(store,rules=()):
    text,evaluation,provenance=skill_data(rules)
    version=uuid4().hex
    return store.candidate(version,text,evaluation,provenance)


def test_candidate_skill_validity_and_provenance(tmp_path):
    store=SkillStore(tmp_path)
    artifact=make_candidate(store)
    path=store.version_path('candidates',artifact['version'])
    metadata=validate_skill((path/'SKILL.md').read_text())
    assert metadata['name']=='netcore-login'
    assert metadata['evidence_scope']=='synthetic_only'
    assert json.loads((path/'provenance.json').read_text())['verified_run_ids']
    assert store.snapshot() is None
    with pytest.raises(PermissionError):store.snapshot(artifact['version'])


def test_promotion_exact_approval_and_rollback(tmp_path):
    store=SkillStore(tmp_path)
    first=make_candidate(store)
    with pytest.raises(PermissionError):store.promote(first['version'],first['digest'])
    with pytest.raises(PermissionError):store.promote(first['version'],'0'*64,True)
    store.promote(first['version'],first['digest'],True)
    assert store.snapshot().digest==first['digest']
    second=make_candidate(store,('verify_after_action',))
    store.promote(second['version'],second['digest'],True)
    assert store.snapshot().digest==second['digest']
    with pytest.raises(PermissionError):store.rollback(first['digest'],first['digest'])
    store.rollback(first['digest'],first['digest'],True)
    assert store.snapshot().digest==first['digest']


def test_approved_content_is_checked_for_tampering(tmp_path):
    store=SkillStore(tmp_path);artifact=make_candidate(store)
    store.promote(artifact['version'],artifact['digest'],True)
    file=store.approved_directory()/'SKILL.md';file.write_text(file.read_text()+'\nchanged\n')
    with pytest.raises(PermissionError):store.snapshot()

async def test_skill_backend_never_reads_host_or_candidates(tmp_path):
    store=SkillStore(tmp_path);artifact=make_candidate(store)
    snapshot=store.snapshot(artifact['version'],evaluation=True)
    backend=ApprovedSkillBackend(snapshot)
    assert backend.read('/.env').error=='permission_denied'
    assert backend.read('/candidates/SKILL.md').error=='permission_denied'
    assert backend.write(SKILL_PATH,'forbidden').error=='permission_denied'
    assert (await backend.adownload_files([SKILL_PATH]))[0].content==snapshot.content.encode()
    assert (await backend.adownload_files(['C:/private/.env']))[0].error=='permission_denied'

@pytest.mark.parametrize('version',['../approved','C:/secret','x'*32])
def test_skill_versions_cannot_escape_store(tmp_path,version):
    with pytest.raises(ValueError):SkillStore(tmp_path).version_path('candidates',version)


def test_insufficient_evidence_never_generates_instructions():
    with pytest.raises(ValueError):render_skill(Strategy(),[{'step_facts':[]}])

async def test_cleanup_never_claims_an_unremoved_profile_is_removed(tmp_path):
    browser=StagehandClient(Config('unused'),OriginPolicy({'http://127.0.0.1:1234'}),Trace(),fixture=True)
    browser.profile=tmp_path/'unconfirmed-profile'
    browser.profile.mkdir()
    with pytest.raises(RuntimeError,match='could not be confirmed'):
        await browser.close()
    assert browser.trace.cleanup_result!='CLOSED_AND_PROFILE_REMOVED'

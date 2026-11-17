"""Exact shared destination policy and same-document SPA regressions; no network."""
import hashlib
import json
import pytest
from app.config import ROOT
from modules.whatsapp.contract import SETUP_URL
from modules.whatsapp.destination import validate_setup_destination,DestinationRejected
from test_setup_agent_refactor import protected_factory
from test_whatsapp_phase1 import protocol_metadata_transport

IDENTITY={'page_identity':True,'setup_active':True,'observation_complete':True}


@pytest.mark.parametrize('suffix',['','#setup'])
def test_canonical_setup_with_fresh_identity(suffix):
    assert not validate_setup_destination(SETUP_URL+suffix,identity=IDENTITY)['identity_pending']


def test_fragment_is_only_a_probe_without_actual_identity():
    assert validate_setup_destination(SETUP_URL+'#setup',allow_identity_probe=True)['identity_pending']
    with pytest.raises(DestinationRejected,match='UNEXPECTED_SETUP_DESTINATION'):
        validate_setup_destination(SETUP_URL+'#setup')


@pytest.mark.parametrize('bad',[
    SETUP_URL.replace('https:','http:'),SETUP_URL.replace('tprappin2r','unexpected'),
    SETUP_URL.replace('.com/','.com:444/'),SETUP_URL+'/other',SETUP_URL+'/',
    SETUP_URL+'?next=audience',SETUP_URL+'?step=setup',SETUP_URL+'#audience',
    SETUP_URL+'#content',SETUP_URL+'#schedule',SETUP_URL+'#Setup',SETUP_URL+'#%73etup',
    SETUP_URL.replace('https://','https://user@'),SETUP_URL.replace('/regular','/%72egular'),
    ' '+SETUP_URL,SETUP_URL+'\n',SETUP_URL.replace('.com/','.com:0/'),
])
def test_unapproved_components_cannot_be_hidden_by_setup_identity(bad):
    with pytest.raises(DestinationRejected):validate_setup_destination(bad,identity=IDENTITY,allow_identity_probe=True)


@pytest.mark.parametrize('field',list(IDENTITY))
@pytest.mark.parametrize('suffix',['','#setup'])
def test_wrong_step_identity_or_uninspected_dom_is_not_accepted(field,suffix):
    with pytest.raises(DestinationRejected) as error:
        validate_setup_destination(SETUP_URL+suffix,identity={**IDENTITY,field:False})
    assert error.value.predicate=='SETUP_IDENTITY'


def test_effective_https_port_is_canonical_and_query_is_exact():
    assert validate_setup_destination(SETUP_URL.replace('.com/','.com:443/'),identity=IDENTITY)
    with pytest.raises(DestinationRejected):validate_setup_destination(SETUP_URL+'?x=1',identity=IDENTITY)


async def test_real_controller_factory_is_reached_when_hash_changes_during_observation(protected_factory,monkeypatch,tmp_path):
    import modules.whatsapp.tools as module
    from test_setup_agent_refactor import test_composed_controller_starts_doer_across_global_gap_then_cleans_owned_session
    original=module.observe_setup;seen=[]
    async def transition(browser):
        data=await original(browser);browser.page.location=SETUP_URL+'#setup';seen.append(browser)
        return data
    monkeypatch.setattr(module,'observe_setup',transition)
    await test_composed_controller_starts_doer_across_global_gap_then_cleans_owned_session(protected_factory,monkeypatch,tmp_path)
    states={e.get('diagnostic',{}).get('state') for e in seen[0].trace.events}
    assert {'SETUP_PAGE_OBSERVED','SETUP_DOER_STARTED'}<=states
    assert seen[0].page.location.endswith('#setup')


def assert_session_only_destination_policy_changed(expected_sha):
    """Retain historical login/handoff pins except the exact authorized URL edit."""
    raw=(ROOT/'modules/whatsapp/session.py').read_bytes()
    newline=b'\r\n' if b'\r\n' in raw else b'\n'
    raw=raw.replace(b'from modules.whatsapp.destination import validate_setup_destination'+newline,b'')
    raw=raw.replace(b"            print('SETUP_TAB_PROTECTED',flush=True)"+newline,b'')
    raw=raw.replace(b'            validate_setup_destination(url)'+newline+
        b'            if self.setup_navigation_count or self.setup_binding is None:raise SetupStop(\'DESTINATION_NOT_AUTHORIZED\')',
        b'            if url!=SETUP_URL or self.setup_navigation_count or self.setup_binding is None:raise SetupStop(\'DESTINATION_NOT_AUTHORIZED\')')
    assert hashlib.sha256(raw).hexdigest()==expected_sha


def test_unrelated_components_and_saved_real_evidence_are_frozen():
    pins=json.loads((ROOT/'outputs/setup-destination-preservation.json').read_text(encoding='utf-8'))
    intentional={'modules/whatsapp/observation.py','modules/whatsapp/safety.py','modules/whatsapp/workflow.py','modules/whatsapp/session.py'}
    for name,sha in pins.items():
        if name not in intentional:assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
    for name,sha in json.loads((ROOT/'outputs/setup-execution-preservation.json').read_text()).items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name

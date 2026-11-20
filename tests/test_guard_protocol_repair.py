"""Sanitized protocol diagnostics; cancellation recovery tests use independent events."""
import json
import pytest
from app.config import OriginPolicy
from browser.guard_state import GuardSession
from browser.navigation_guard import NavigationGuard

from browser.navigation_guard import GuardProtocolError


def test_protocol_error_retains_only_fixed_classification_numeric_code_and_digest():
    error=GuardProtocolError({'code':-32602,'message':'Invalid InterceptionId.','data':'PRIVATE_TOKEN'})
    assert error.protocol_code==-32602 and error.protocol_reason=='INVALID_INTERCEPTION_ID'
    assert len(error.protocol_error_sha256)==64
    assert 'PRIVATE_TOKEN' not in str(error)
    unknown=GuardProtocolError({'code':-32000,'message':'PRIVATE_TOKEN https://private.invalid/'})
    assert unknown.protocol_reason=='UNCLASSIFIED_PROTOCOL_ERROR'
    assert 'PRIVATE_TOKEN' not in str(unknown)


@pytest.mark.parametrize('proof',['cancelled','finished','responded','unknown','different_generation','top_level','replaced_network','unknown_protocol','missing_network'])
async def test_invalid_interception_requires_matching_independent_cancellation(proof):
    guard=NavigationGuard(OriginPolicy({'http://127.0.0.1:9000'}))
    owner=GuardSession('PRIVATE_SESSION','PRIVATE_TARGET','page',1,intercepting=True,protected=True,frame_id='ROOT')
    guard.session_states[owner.session]=owner;guard.current_sessions[owner.target]=owner.session
    guard.primary_session=owner.session;guard.frame_parents['CHILD']='ROOT';guard.frame_owners['CHILD']=owner.session
    guard.session_frames[owner.session]=('page','ROOT')
    frame='ROOT' if proof=='top_level' else 'CHILD'
    event={'method':'Fetch.requestPaused','sessionId':owner.session,'params':{
        'requestId':'PRIVATE_FETCH','networkId':'PRIVATE_NETWORK','frameId':frame,
        'resourceType':'Document','request':{'url':'http://127.0.0.1:9001/denied'}}}
    guard.network_event({'method':'Network.requestWillBeSent','sessionId':owner.session,
        'params':{'requestId':'PRIVATE_NETWORK','type':'Document','frameId':frame}})
    guard.register_request(event)
    if proof=='missing_network':guard.paused_requests[(owner.session,'PRIVATE_FETCH')]['network_record']=None
    if proof=='replaced_network':guard.network_event({'method':'Network.requestWillBeSent','sessionId':owner.session,
        'params':{'requestId':'PRIVATE_NETWORK','type':'Document','frameId':frame}})
    if proof=='responded':guard.network_event({'method':'Network.responseReceived','sessionId':owner.session,
        'params':{'requestId':'PRIVATE_NETWORK'}})
    if proof!='unknown':guard.network_event({'method':'Network.loadingFinished' if proof=='finished' else 'Network.loadingFailed',
        'sessionId':owner.session,'params':{'requestId':'PRIVATE_NETWORK','canceled':True}})
    if proof=='different_generation':owner.generation+=1
    async def reject(*a,**kw):raise GuardProtocolError({'code':-32602,
        'message':'Unknown private error' if proof=='unknown_protocol' else 'Invalid InterceptionId.'})
    guard.send=reject
    await guard._event(event)
    assert guard.failed is (proof!='cancelled')
    if proof=='cancelled':
        assert not guard.paused_requests
        assert any(e.get('completion')=='CANCELLED_NETWORK_PROVEN' for e in guard.lifecycle)
    else:assert guard.failure_diagnostic['protocol_reason']==('UNCLASSIFIED_PROTOCOL_ERROR' if proof=='unknown_protocol' else 'INVALID_INTERCEPTION_ID')
    assert 'PRIVATE' not in json.dumps(guard.lifecycle)

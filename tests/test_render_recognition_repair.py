"""Bounded readiness and sanitized rendering diagnostics; no provider calls."""
import re
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_login_robustness import browser


async def test_readiness_expands_bounded_scan_and_resets_after_navigation():
    b=browser();owner=SimpleNamespace(target='target',session='session',generation=1,
        document_generation=1,frame_id='frame',loader_id='loader')
    b.guard.ensure_observable=AsyncMock();b.guard.primary_session=owner.session
    b.guard.session_states={owner.session:owner}
    url='https://'+b.config.expected_host+'/'
    seen=[]
    async def evaluate(expression):
        match=re.search(r'const limit=(\d+);',expression)
        limit=int(match.group(1)) if match else 512
        seen.append(limit)
        return {'shell':True if limit>=2048 else None,'scan_limited':limit<2048,
                'loading':False,'credential_form':False}
    b.page=SimpleNamespace(url=AsyncMock(return_value=url),evaluate=evaluate)
    for _ in range(8):await b.readiness_sample()
    assert seen[:3]==[512,1024,2048] and max(seen)<=12000
    owner.document_generation+=1
    await b.readiness_sample()
    assert seen[-1]==512
    async def limited(expression):return {'shell':None,'scan_limited':True}
    b.page.evaluate=limited
    for _ in range(10):await b.readiness_sample()
    assert b.readiness_scan_budget==12000


async def test_readiness_cannot_reuse_a_scan_across_document_generation_change():
    b=browser();owner=SimpleNamespace(target='t',session='s',generation=1,
        document_generation=1,frame_id='f',loader_id='l')
    b.guard.ensure_observable=AsyncMock();b.guard.primary_session='s';b.guard.session_states={'s':owner}
    async def evaluate(expression):
        owner.document_generation+=1
        return {'shell':True,'scan_limited':False,'loading':False}
    b.page=SimpleNamespace(url=AsyncMock(return_value='https://'+b.config.expected_host+'/'),evaluate=evaluate)
    result=await b.readiness_sample()
    assert result['context'] is None and result['unknown_reason']=='READINESS_DOCUMENT_CHANGED'


async def test_unknown_closed_scope_prevents_screenshot_and_ax_names_are_not_retained(monkeypatch):
    from browser.diagnostics import ScreenshotDiagnostics
    from app.config import OriginPolicy
    import browser.discovery as discovery
    b=browser();b.fixture=True;b.policy=OriginPolicy({'http://127.0.0.1:9000'})
    owner=SimpleNamespace(target='target',session='session',generation=1,
        document_generation=1,frame_id='frame',loader_id='loader')
    calls=[]
    async def send(method,params=None,session=None):
        calls.append(method)
        if method=='Runtime.evaluate':return {'result':{'objectId':'PRIVATE_OBJECT'}}
        if method=='Accessibility.getPartialAXTree':
            assert params['fetchRelatives'] is False
            return {'nodes':[{'backendDOMNodeId':77,'name':{'value':'PRIVATE_CUSTOMER_TEXT'},
                'role':{'value':'heading'},'ignored':True,'ignoredReasons':[{'name':'notRendered','value':{'value':'PRIVATE_TEXT'}}]}]}
        if method=='Runtime.callFunctionOn':return {'result':{'value':{'visible':False,'reason':'ANCESTOR_DISPLAY_NONE'}}}
        return {}
    b.guard.primary_session='session';b.guard.session_states={'session':owner};b.guard.send=send
    async def context(*a):return {'primary_document_coherent':True,'expected_host':True}
    monkeypatch.setattr(discovery,'login_page_context',context)
    dom={'_render_targets':[{'element_ref':'e1','selector':'#actual-observed','frame_selectors':[],'shadow_selectors':[]}],
        'dom_diagnostics':{'semantic_samples':[{'element_ref':'e1','visible':False,'collector_gate':'NOT_VISIBLE'}]}}
    result=await ScreenshotDiagnostics(b).compare(dom,{'inaccessible_shadow_roots':1},enabled=True)
    assert result['screenshot_status']=='SKIPPED_PRIVACY_INACCESSIBLE_SCOPE'
    assert 'Page.captureScreenshot' not in calls
    assert result['targets_compared']==1 and result['ax_ignored']==1
    assert 'PRIVATE' not in json.dumps(result)
    assert result['targets'][0]['stable_node_ref'].startswith('node-')

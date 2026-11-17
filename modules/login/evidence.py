"""Reviewed evidence is private input. Discovery hints never certify success."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, model_validator
from typing import Annotated
from modules.login.discovery import NEGATIVE_STATES, POSITIVE_DOM
from app.state import Observation, Result
from modules.login.verifier import verify
from browser.evidence_diagnostics import EvidenceFailure,gate_summary

class ReviewedIdentityMapping(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewed: bool=False
    login_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    application_identity: SecretStr
    evidence_reference: str=Field(min_length=1,max_length=256)

    @field_serializer('application_identity',when_used='json')
    def private_config_identity(self,value):return value.get_secret_value()

class EvidenceConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewed: bool=False
    identity_mapping: ReviewedIdentityMapping | None=None
    dashboard_shadow: list[str]=Field(default_factory=list,max_length=8)
    tenant_shadow: list[str]=Field(default_factory=list,max_length=8)
    dashboard_selector: str=Field(min_length=1,max_length=256)
    tenant_selector: str=Field(min_length=1,max_length=256)
    dashboard_frame: list[Annotated[str,Field(min_length=1,max_length=256)]]=Field(default_factory=list,max_length=3)
    tenant_frame: list[Annotated[str,Field(min_length=1,max_length=256)]]=Field(default_factory=list,max_length=3)
    readonly_path: str=Field(min_length=1,max_length=256)
    tenant_json_key: str=Field(min_length=1,max_length=80)
    authenticated_json_key: str=Field(min_length=1,max_length=80)
    authenticated_value_sha256: str | None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    authentication_contract_reference: str | None=Field(default=None,min_length=1,max_length=256)

    @model_validator(mode='after')
    def explicit_alternative_contract(self):
        if bool(self.authenticated_value_sha256)!=bool(self.authentication_contract_reference):
            raise ValueError('Alternative resource interpretation requires explicit review provenance')
        return self

    @classmethod
    def load(cls,path,allow_unreviewed=False):
        if not path:
            return None
        try:raw=Path(path).read_bytes()
        except FileNotFoundError:raise EvidenceFailure('MISSING_EVIDENCE_CONFIGURATION') from None
        return cls.parse_document(raw,allow_unreviewed)

    @classmethod
    def parse_document(cls,raw,allow_unreviewed=False):
        if isinstance(raw,bytes):raw=raw.decode('utf-8-sig')
        data=cls.model_validate_json(raw)
        p=urlsplit(data.readonly_path)
        if (not data.reviewed and not allow_unreviewed) or p.scheme or p.netloc or not data.readonly_path.startswith('/') or data.readonly_path.startswith('//') or p.query or p.fragment or '\\' in data.readonly_path:
            raise EvidenceFailure('INVALID_EVIDENCE_SCHEMA')
        if data.identity_mapping and not data.identity_mapping.reviewed:raise EvidenceFailure('UNREVIEWED_IDENTITY_MAPPING')
        return data

    @classmethod
    def for_session(cls,path,review_mode=False):
        if not review_mode:return cls.load(path)
        try:return cls.load(path)
        except (OSError,ValueError):
            # Discovery happens in the active authorized session; no guessed placeholder file.
            return None

async def observe_live(browser,url):
    expected_host=urlsplit(url).hostname==browser.config.expected_host
    origins=sorted(browser.policy.origins) if hasattr(browser,'policy') else ['https://'+browser.config.expected_host]
    flags=await browser.page.evaluate(NEGATIVE_STATES.replace('SETTINGS',json.dumps({'origins':origins})))
    flags.update(getattr(browser,'login_security_negatives',{}))
    negative_scope_complete=flags.pop('negative_scope_complete',True)
    flags['login_entry']=urlsplit(url).hostname=='login.netcoresmartech.com'
    mismatch=False
    issues=[]
    page_context=None;scope_certificate=None
    if expected_host:
        from browser.discovery import login_page_context
        page_context=await login_page_context(browser)
    evidence=browser.evidence
    if evidence is not None and evidence.reviewed and expected_host and browser.expected_account is not None:
        settings={**evidence.model_dump(exclude={'identity_mapping'}),'origins':origins,'account':reviewed_account(evidence,browser.expected_account.get_secret_value())}
        # Exact tenant comparison and frame scoping stay inside Chromium.
        expression=POSITIVE_DOM.replace('SETTINGS',json.dumps(settings))
        positive=await browser.page.evaluate(expression)
        if 'CUSTOM_HOST_SCOPE_REQUIRES_PROTECTED_INVENTORY' in positive.get('issues',[]):
            from browser.discovery import certify_custom_host_scope
            positive,scope_certificate=await certify_custom_host_scope(browser,positive,expression)
            if scope_certificate.get('certified') is True:
                positive['issues']=[issue for issue in positive.get('issues',[]) if issue!='CUSTOM_HOST_SCOPE_REQUIRES_PROTECTED_INVENTORY']
            else:positive.setdefault('issues',[]).append('PERMITTED_DOCUMENT_UNOBSERVABLE')
            for key,value in scope_certificate.get('negative_flags',{}).items():
                if value is True:flags[key]=True
            if scope_certificate.get('negative_scope_complete') is not True:negative_scope_complete=False
        positive.pop('_scope_diagnostics',None)
        discovery=getattr(browser,'discovery',None)
        if discovery is not None:
            readonly,resource_status=discovery.check_resource(evidence)
            positive['readonly_access']=readonly
            if not readonly:positive.setdefault('issues',[]).append(resource_status)
        elif evidence.authenticated_value_sha256:
            # Alternative contracts use only observed passive facts, never replay.
            positive['readonly_access']=False
            positive.setdefault('issues',[]).append('PASSIVE_REVIEWED_RESOURCE_REQUIRED')
        else:
            # Preserve the existing explicitly reviewed normal-verification GET. Discovery
            # and interactive review always use the passive observer, with no HTTP replay.
            probe=await browser.page.evaluate('''(async e => {
              try {
                const r=await fetch(e.readonly_path,{method:'GET',credentials:'same-origin',redirect:'error',cache:'no-store',signal:AbortSignal.timeout(5000)});
                const d=r.ok?await r.json():{};
                const get=(d,k)=>k.startsWith('/')?k.slice(1).split('/').reduce((v,p)=>v&&Object.hasOwn(v,p.replace(/~1/g,'/').replace(/~0/g,'~'))?v[p.replace(/~1/g,'/').replace(/~0/g,'~')]:undefined,d):d[k];
                return {readonly_access:r.ok&&get(d,e.authenticated_json_key)===true&&get(d,e.tenant_json_key)===e.account};
              }catch{return {readonly_access:false};}
            })(SETTINGS)'''.replace('SETTINGS',json.dumps(settings)))
            positive['readonly_access']=probe.get('readonly_access',False)
            if not positive['readonly_access']:positive.setdefault('issues',[]).append('READONLY_RESOURCE_NOT_VERIFIED')
        if 'PERMITTED_DOCUMENT_UNOBSERVABLE' in positive.get('issues',[]):positive['readonly_access']=False
        positive['evidence_configured']=True
        mismatch=positive.pop('mismatch')
        issues=positive.pop('issues',[])
        flags.update(positive)
    else:issues=['MISSING_AUTHENTICATED_READONLY_RESOURCE','MISSING_OR_UNREVIEWED_EVIDENCE_CONFIGURATION']
    if expected_host and not (page_context or {}).get('primary_document_coherent'):
        issues.append('LOGIN_PAGE_CONTEXT_UNVERIFIED');flags['readonly_access']=False
    if not negative_scope_complete:
        if 'PERMITTED_DOCUMENT_UNOBSERVABLE' not in issues:issues.append('PERMITTED_DOCUMENT_UNOBSERVABLE')
        flags['readonly_access']=False
    if flags.get('expired'):issues.append('BROWSER_SESSION_EXPIRED')
    observation=Observation(**flags,expected_host=expected_host)
    result=verify(observation)
    if observation.account_restricted:
        reason='ACCOUNT_RESTRICTED'
    elif mismatch:
        reason='INCORRECT_TENANT'
    elif observation.rejected:
        reason='AUTHENTICATION_REJECTED'
    elif observation.challenge:
        reason='HUMAN_ACTION_REQUIRED'
    elif result==Result.AUTHENTICATED_AND_VERIFIED:
        reason='VERIFIED_SUCCESS'
    elif expected_host:
        readiness=getattr(browser,'login_readiness',None) or {}
        reason=readiness['code'] if readiness.get('code') in {'POST_LOGIN_READINESS_TIMEOUT','POST_LOGIN_READINESS_SCOPE_INCOMPLETE'} else 'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED'
    elif observation.login_entry:
        reason='LOGIN_NAVIGATION_INCOMPLETE'
    else:
        reason='UNEXPECTED_PAGE'
    missing=[name for name in ('expected_host','dashboard','tenant_matches','readonly_access','evidence_configured') if not getattr(observation,name)]
    gate_status=gate_summary(observation)
    if not observation.evidence_configured:
        for key in ('dashboard','tenant_matches','readonly_access'):gate_status[key]='NOT_CHECKED'
    browser.verification_details={'reason':reason,'missing':missing,'tenant_mismatch':mismatch,
        'gate_status':gate_status,'issues':issues,'evidence_review_required':evidence is None or not evidence.reviewed,
        'page_context':page_context,'scope_certificate':scope_certificate}
    return observation


async def stable_authentication(browser,*,prospective=False):
    """Two fresh independent observations in one protected document/SPA context.

    Public Result semantics stay unchanged. Missing review/proof remains incomplete;
    approval cannot transfer a positive observation across a changed document.
    """
    import asyncio
    before=await browser.authentication_context()
    first=await browser.observe()
    if verify(first)!=Result.AUTHENTICATED_AND_VERIFIED:
        browser.trace.lifecycle('LOGIN_READINESS',{'state':'AUTHENTICATION_EVIDENCE_PENDING',
            'gate_status':(browser.verification_details or {}).get('gate_status',{})})
        return first
    await asyncio.sleep(browser.config.login_stable_seconds)
    middle=await browser.authentication_context()
    second=await browser.observe()
    after=await browser.authentication_context()
    if before is None or before!=middle or middle!=after:
        browser.verification_details={**(browser.verification_details or {}),
            'reason':'LOGIN_EVIDENCE_CONTEXT_CHANGED','issues':['LOGIN_EVIDENCE_CONTEXT_CHANGED']}
        return second.model_copy(update={'readonly_access':False})
    if verify(second)==Result.AUTHENTICATED_AND_VERIFIED:
        browser.trace.lifecycle('LOGIN_READINESS',{
            'state':'AUTHENTICATION_EVIDENCE_PENDING' if prospective else 'AUTHENTICATED_AND_VERIFIED',
            'verification_basis':'PROSPECTIVE_APPROVAL_CHECK' if prospective else 'INDEPENDENT_CURRENT_EVIDENCE',
            'trusted':not prospective,'observations':2,'document_current':True})
    return second

def reviewed_account(evidence,account):
    import hashlib
    mapping=evidence.identity_mapping
    if mapping is None:return account
    if not mapping.reviewed or hashlib.sha256(account.encode()).hexdigest()!=mapping.login_sha256:
        raise EvidenceFailure('IDENTITY_MAPPING_CONTEXT_MISMATCH')
    return mapping.application_identity.get_secret_value()

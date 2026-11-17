"""Validated application schemas, not model-generated executable instructions."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.config import ConfigurationError
from deep_agents.review_contract import MAX_EVIDENCE_CITATIONS

DEFAULT_TASK = 'Log in to Netcore Customer Engagement Platform and verify the authenticated dashboard.'
_WORDS = set('please log login in into sign signin to netcore cep and verify confirm check the authenticated authentication dashboard expected authorized account tenant context access synthetic local fixture read only complete reach browser successfully session existing customer engagement platform'.split())


def validate_task(task: str) -> str:
    # A free-form login request within a deliberately narrow vocabulary. No pasted secrets,
    # URLs, arbitrary instructions, identifiers or workflow authorization enter the model.
    if not isinstance(task,str) or not 1 <= len(task) <= 256 or re.search(r'[^A-Za-z .,!?-]',task):
        raise ConfigurationError('Pass 1 accepts only a short login/dashboard task without credentials or URLs')
    words = set(re.findall(r'[a-z]+',task.lower()))
    if not words <= _WORDS or not words & {'login','log','signin','sign'} or not words & {'verify','confirm','check'} or not words & {'dashboard','tenant','context'}:
        raise ConfigurationError('Task must request login and read-only dashboard verification only')
    return task.strip()

class DoerReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['success','failure','blocked']
    reason: Literal['verified','authentication_rejected','session_expired','wrong_context','budget_exhausted','unrecognized_page','tool_error']

class TeacherProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['success','failure','blocked']
    change_required: bool
    finding: str = Field(min_length=1,max_length=200)
    proposed_change: str = Field(max_length=300)
    evidence_events: list[int] = Field(max_length=MAX_EVIDENCE_CITATIONS)
    strategy_rule: Literal['prefer_semantic_roles','reinspect_before_submit','verify_after_action','wait_for_stable_controls'] | None = None
    analysis_kind: Literal['policy','evidence_gap','no_change']='policy'
    worked: str=Field(default='',max_length=200)
    inefficiency: str=Field(default='',max_length=200)
    comparison: str=Field(default='',max_length=200)

    @model_validator(mode='after')
    def safe_candidate(self):
        text = ' '.join((self.finding,self.proposed_change,self.worked,self.inefficiency,self.comparison))
        if re.search(r'sk-|https?://|\.env|[A-Za-z]:[/\\]|(?:password|token|secret)\s*[:=]',text,re.I):
            raise ValueError('Unsafe candidate text')
        if self.change_required and (not self.proposed_change.strip() or not self.evidence_events):
            raise ValueError('An improvement requires one proposal and evidence')
        if not self.change_required and self.proposed_change.strip():
            raise ValueError('No-change proposals must leave proposed_change empty')
        if not self.change_required and self.strategy_rule is not None:
            raise ValueError('No-change proposals cannot carry a rule')
        if self.change_required and re.search(r'live|bypass|disable|allowlist|retry.*credential',self.proposed_change,re.I):
            raise ValueError('Candidate cannot relax security policy')
        return self

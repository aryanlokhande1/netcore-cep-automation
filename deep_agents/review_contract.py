"""Versioned, module-independent machine review contract."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

MAX_EVIDENCE_CITATIONS=6

class ReviewDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    outcome:Literal['success','failure','blocked']
    decision:Literal['no_change','optimize','repair','evidence_gap','reject_optimization','insufficient_evidence']
    finding:str=Field(min_length=1,max_length=200)
    evidence_events:list[int]=Field(min_length=1,max_length=MAX_EVIDENCE_CITATIONS)
    strategy_rule:str|None
    proposed_change:str=Field(max_length=200)

    @model_validator(mode='after')
    def consistent(self):
        if re.search(r'sk-|https?://|\.env|[A-Za-z]:[/\\]|(?:password|token|secret)\s*[:=]',self.finding+' '+self.proposed_change,re.I):
            raise ValueError('Unsafe review content')
        if self.decision=='optimize' and (not self.strategy_rule or not self.proposed_change):
            raise ValueError('Optimization requires an explicit rule and proposal')
        if self.decision!='optimize' and self.strategy_rule is not None:
            raise ValueError('Only optimization carries an executable rule')
        if self.decision not in {'optimize','repair'} and self.proposed_change:
            raise ValueError('No unsupported change')
        return self

class EvidenceCard(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:str
    events:list[int]
    data:dict

class ReviewPackage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    schema_version:Literal['teacher-evidence/v1']='teacher-evidence/v1'
    run_id:str
    workflow:dict
    verified_result:str
    outcome:Literal['success','failure','blocked']
    reason_code:str
    module_evidence:dict
    facts:list[dict]
    stages:list[dict]
    evidence:list[EvidenceCard]
    metrics:dict
    previous_strategy:dict
    comparison:list[dict]
    source_event_count:int

class EvidenceError(ValueError):
    pass

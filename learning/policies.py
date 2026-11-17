"""Bounded executable policies, not arbitrary Teacher-generated code or selectors."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Rule=Literal['prefer_semantic_roles','reinspect_before_submit','verify_after_action','wait_for_stable_controls']

class Strategy(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    version: str='baseline'
    rules: tuple[Rule,...]=()
    source_run: str | None=None

    def instructions(self):
        text={
            'prefer_semantic_roles':'Prefer observed accessible input roles over changing field captions.',
            'reinspect_before_submit':'Reinspect controls immediately before the one trusted submission.',
            'verify_after_action':'Perform a read-only verification after every important action.',
            'wait_for_stable_controls':'Wait for two bounded matching control observations before using references.'}
        return '\n'.join(text[r] for r in self.rules)


def proposal_rule(proposal):
    explicit=getattr(proposal,'strategy_rule',None)
    if explicit:
        return explicit
    return None  # Prose is never an executable strategy.

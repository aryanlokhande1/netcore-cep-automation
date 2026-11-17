from types import SimpleNamespace
import json
import pytest
from pydantic import ValidationError
from app.state import Observation
from app.model import OpenAIReasoner, Decision, Proposal
from app.config import ConfigurationError

async def test_openai_structured_calls_receive_only_sanitized_data(monkeypatch):
    import app.model as module
    calls=[]
    async def retrieve(model):
        calls.append(('retrieve',model))
    async def parse(**kwargs):
        calls.append(('parse',kwargs))
        schema=kwargs['text_format']
        value=Decision(action='handoff') if schema is Decision else Proposal(change='no_change')
        return SimpleNamespace(output_parsed=value)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-unit-key')
    monkeypatch.setenv('OPENAI_MODEL','synthetic-account-model')
    monkeypatch.setattr(module,'AsyncOpenAI',lambda **kw: SimpleNamespace(
        models=SimpleNamespace(retrieve=retrieve),responses=SimpleNamespace(parse=parse)))
    reasoner=OpenAIReasoner()
    await reasoner.validate_model()
    assert await reasoner.decide(Observation(credential_form=True),False)=='handoff'
    assert await reasoner.propose([])=='no_change'
    assert calls[0]==('retrieve','synthetic-account-model')
    for kind,payload in calls[1:]:
        assert payload['store'] is False
        assert payload['model']=='synthetic-account-model'
        assert 'synthetic-unit-key' not in json.dumps(payload['input'])
    assert reasoner.calls==2

@pytest.mark.parametrize('schema,payload',[(Decision,{'action':'create_campaign'}),
    (Decision,{'action':'handoff','password':'not-permitted'}),
    (Proposal,{'change':'broaden_allowlist'})])
def test_structured_outputs_cannot_expand_permissions(schema,payload):
    with pytest.raises(ValidationError):
        schema(**payload)

def test_missing_api_configuration_is_explicit(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('OPENAI_MODEL',raising=False)
    with pytest.raises(ConfigurationError,match='OPENAI_API_KEY and OPENAI_MODEL'):
        OpenAIReasoner()

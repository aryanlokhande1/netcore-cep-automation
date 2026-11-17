"""Native Teacher schema checks with local doubles and immutable saved evidence.

These doubles do not reconstruct the unpersisted response from the actual run.
"""
import hashlib
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from app.config import ROOT
from app.trace import Trace
from deep_agents.evidence import canonical, compile_evidence, digest
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget
from deep_agents.review import review_evidence
from deep_agents.telemetry import UsageBoundary
from modules.whatsapp.review import WhatsAppReviewAdapter


RUN = 'bd3fd9cdf01d4c8e80b5a64c08ee3dd1'


def saved_source():
    paths = [ROOT / 'outputs' / (RUN + suffix)
             for suffix in ('.json', '-result.json', '-summary.json')]
    events = json.loads(paths[0].read_text(encoding='utf-8'))
    result = json.loads(paths[1].read_text(encoding='utf-8'))
    return result, events, paths


def checksums(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


class NativeTeacherDouble(SyntheticChatModel):
    """Return JSON through the genuine production ProviderStrategy parser."""
    response: dict
    expected_package: dict
    bound_format: dict = Field(default_factory=dict)
    _trace = PrivateAttr(default=None)
    _role = PrivateAttr(default='teacher')

    def bind_meter(self, trace, budget, role):
        self._trace = trace
        self._role = role
        # Use the same pre-parser metadata callback as the provider model. The
        # local response below supplies only a terminal status, never paid usage.
        self.callbacks = [UsageBoundary(self)]
        return self

    def bind_tools(self, tools, **kwargs):
        super().bind_tools(tools, **kwargs)
        assert not tools  # The Teacher has no browser or execution tools.
        self.bound_format = kwargs['response_format']
        return self

    def _generate(self, messages, **kwargs):
        self.calls += 1
        package = json.loads(next(m.content for m in reversed(messages)
                                  if isinstance(m, HumanMessage)))
        assert canonical(package) == canonical(self.expected_package)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=json.dumps(self.response),
            response_metadata={'status': 'completed'}))])


def local_decision(**changes):
    return {
        'outcome': 'blocked', 'decision': 'evidence_gap',
        'finding': 'Login submitted once; Setup observation coverage was incomplete. '
                   'No fields were filled; cleanup completed.',
        'evidence_events': [62, 97, 175, 291],
        'strategy_rule': None, 'proposed_change': '', **changes,
    }


async def local_review(response):
    result, events, paths = saved_source()
    before = checksums(paths)
    adapter = WhatsAppReviewAdapter(result, events)
    package = compile_evidence(result, events, adapter)
    # offline=False deliberately exercises the native production contract; this
    # supplied local double has no network implementation or provider client.
    reviewed = dict(result)
    trace = Trace()
    trace.events = list(events)
    model = NativeTeacherDouble(role='teacher', response=response,
                                expected_package=package.model_dump())
    budget = InferenceBudget(1, role_limits={'doer': 0, 'teacher': 1})
    await review_evidence(reviewed, trace, model, budget, False, adapter=adapter)
    assert checksums(paths) == before
    assert model.calls == budget.requests == 1
    assert trace.openai_api_calls == 0 and trace.role_usage['doer']['requests'] == 0
    assert model.bound_format['type'] == 'json_schema'
    assert model.bound_format['json_schema']['strict'] is True
    return reviewed, trace, model


def test_saved_failure_does_not_establish_an_offending_schema_field():
    result, events, _ = saved_source()
    assert result['teacher_diagnostic'] == {
        'stage': 'MODEL_EXECUTION', 'category': 'STRUCTURED_OUTPUT_VALIDATION',
        'finish_status': 'completed', 'model_request_started': True,
        'request_count': 1,
    }
    assert 'teacher_rejected_decision' not in result
    assert 'teacher_decision' not in result
    teacher = [item for item in result['model_diagnostics'] if item['role'] == 'teacher']
    assert len(teacher) == 1 and teacher[0]['finish_status'] == 'completed'
    assert teacher[0]['output_tokens'] == 127
    boundary = [item for item in result['teacher_boundary'] if item['role'] == 'teacher']
    assert len(boundary) == 1 and boundary[0]['max_output_tokens'] == 2000
    assert result['role_usage']['teacher']['requests'] == 1
    assert not any(event.get('action_category') == 'teacher' for event in events)


def test_actual_saved_package_recompiles_without_adapter_or_citation_conflict():
    result, events, _ = saved_source()
    package = compile_evidence(result, events, WhatsAppReviewAdapter(result, events))
    assert digest(package.model_dump()) == result['teacher_evidence']['digest']
    assert package.outcome == 'blocked'
    assert package.reason_code == 'SAFETY_OBSERVATION_INCOMPLETE'
    assert package.module_evidence['required_citation_groups'] == {
        'LOGIN_RECOGNIZED': [62], 'SUBMITTED_ONCE': [97],
        'HUMAN_CONFIRMED_FOR_POC': [175], 'CLEANUP': [291],
    }
    assert package.module_evidence['setup_preflight_failure']['failed_predicates'] == ['negative_scope_complete']


async def test_valid_local_evidence_gap_passes_native_schema_and_whatsapp_adapter():
    reviewed, trace, model = await local_review(local_decision())
    assert reviewed['teacher_status'] == 'ANALYZED'
    assert reviewed['teacher_decision'] == local_decision()
    assert reviewed['teacher_proposal']['analysis_kind'] == 'evidence_gap'
    assert reviewed['teacher_proposal']['change_required'] is False
    assert reviewed['teacher_validation_summary']['canonical_citations_valid'] is True
    schema = model.bound_format['json_schema']['schema']
    assert schema['properties']['finding']['maxLength'] == 200


@pytest.mark.parametrize('response', [
    local_decision(finding='x' * 201),
    local_decision(proposed_change='An advisory change with an evidence-gap decision.'),
    local_decision(evidence_events=[62, 97, 175, 219, 221, 291, 292]),
])
async def test_distinct_local_schema_errors_share_the_saved_failure_category(response):
    reviewed, _, _ = await local_review(response)
    assert reviewed['teacher_status'] == 'ANALYSIS_UNAVAILABLE'
    assert reviewed['teacher_proposal'] is None
    assert reviewed['teacher_diagnostic']['stage'] == 'MODEL_EXECUTION'
    assert reviewed['teacher_diagnostic']['category'] == 'STRUCTURED_OUTPUT_VALIDATION'
    assert reviewed['teacher_diagnostic']['finish_status'] == 'completed'
    assert 'validation_errors' not in reviewed['teacher_diagnostic']


async def test_adapter_attribution_failure_is_distinct_from_saved_model_parser_failure():
    reviewed, _, _ = await local_review(local_decision(
        finding='The setup form was submitted once; cleanup completed.'))
    assert reviewed['teacher_status'] == 'ANALYSIS_UNAVAILABLE'
    assert reviewed['teacher_diagnostic']['stage'] == 'PROPOSAL_VALIDATION'
    assert reviewed['teacher_diagnostic']['validation_code'] == 'UNSUPPORTED_SETUP_SUBMISSION_CLAIM'

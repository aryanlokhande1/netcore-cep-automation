from dataclasses import dataclass
from pathlib import Path
from app.config import ROOT, OriginPolicy
from app.trace import Trace
from app.state import Result
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server
from modules.login.authentication import SyntheticHandoff
from modules.login.workflow import attempt

@dataclass(frozen=True)
class Candidate:
    iteration: int
    change: str

class Teacher:
    def __init__(self, reasoner):
        self.reasoner = reasoner

    async def review(self, trace, iteration):
        change = await self.reasoner.propose(trace.events)
        if change not in {'recognize_alternate_entry','no_change'}:
            raise ValueError('Unsupported candidate')
        return Candidate(iteration, change)

async def local_attempt(config, reasoner, server, scenario, iteration=0, strategy=False):
    from app.safety import require_fixture_origin
    require_fixture_origin(server.base)
    trace = Trace(iteration, 'candidate-alternate-v1' if strategy else 'baseline-v1')
    browser = StagehandClient(config, OriginPolicy({server.base}), trace, fixture=True)
    result = await attempt(browser, reasoner, server.base+'/start?scenario='+scenario, SyntheticHandoff(), strategy)
    return result['verification'], trace

async def learn(config, reasoner, max_iterations=2, strategy_path=None):
    if config.teacher_live_replay_enabled:
        raise ValueError('Live Teacher replay is forbidden')
    if not 1 <= max_iterations <= 3:
        raise ValueError('Teacher iterations must be between 1 and 3')
    path = strategy_path or ROOT / 'learning' / 'strategy.md'
    teacher = Teacher(reasoner)
    records = []
    # No live URL is accepted by this learning interface.
    with fixture_server() as server:
        baseline, trace = await local_attempt(config, reasoner, server, 'changed')
        original_baseline = baseline
        for iteration in range(1, max_iterations+1):
            candidate = await teacher.review(trace, iteration)
            entry = (f'\n## Iteration {iteration}\n\n'
                f'Observed outcome: {baseline}; alternate login entry observed.\n'
                f'Proposed change: {candidate.change}.\n'
                f'Evidence: sanitized baseline run {trace.run_id}.\n'
                f'Reasoner: {reasoner.label}.\n'
                'Evaluation: pending. Accepted: pending. Skill promotion: prohibited without explicit approval.\n')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as f:
                f.write(entry)
            candidate_path = path.parent / f'candidate-{iteration}.md'
            candidate_path.write_text(
                '# Candidate strategy\n\n'
                f'recognize_alternate_entry: {str(candidate.change == "recognize_alternate_entry").lower()}\n'
                f'Baseline trace: {trace.run_id}\n'
                'Scope: local synthetic fixture only. No policy or verifier changes.\n', encoding='utf-8')
            # Fresh Doers consume the restricted candidate file, not arbitrary Teacher prose.
            enabled = 'recognize_alternate_entry: true' in candidate_path.read_text(encoding='utf-8').splitlines()
            comparisons = []
            # Candidate must repeat successfully and avoid regression on all fixture states.
            for scenario in ('changed','changed','valid','invalid','already','expired','challenge','wrong_tenant','unknown'):
                outcome, fresh_trace = await local_attempt(config, reasoner, server, scenario, iteration, enabled)
                expected = {'invalid': Result.AUTH_FAILED, 'expired': Result.SESSION_EXPIRED,
                    'wrong_tenant': Result.UNEXPECTED_PAGE, 'unknown': Result.UNEXPECTED_PAGE}.get(scenario, Result.AUTHENTICATED_AND_VERIFIED)
                comparisons.append({'scenario':scenario, 'result':outcome, 'expected':str(expected), 'run_id':fresh_trace.run_id})
            accepted = original_baseline != Result.AUTHENTICATED_AND_VERIFIED and all(x['result']==x['expected'] for x in comparisons)
            record = {'iteration':iteration,'baseline':original_baseline,'previous_attempt':baseline,'candidate':candidate.change,
                'accepted':accepted,'comparisons':comparisons,'teacher_model_calls':0 if reasoner.is_mock else iteration,
                'teacher_reasoning_turns':iteration,
                'reasoner':reasoner.label,
                'teacher_codex_calls':iteration if reasoner.mode == 'codex_cli' else 0,
                'teacher_openai_api_calls':iteration if reasoner.mode == 'OpenAI' else 0}
            records.append(record)
            with path.open('a', encoding='utf-8') as f:
                f.write(f'\nEvaluation result: {"accepted for local testing" if accepted else "rejected"}. Accepted: {accepted}.\n')
            if accepted or not enabled:
                break
            baseline, trace = await local_attempt(config, reasoner, server, 'changed', iteration, enabled)
    return records

"""Headed loopback acceptance through the actual Phase 1 Setup controller.

No login model, credentials, Netcore request, Teacher or paid provider is used.
The fixture owns its explicit local destination and simulated one-submit handoff.
Only sanitized classifications, counters and exact-value booleans are saved.
"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.config import Config, OriginPolicy, ROOT
from app.trace import Trace
from browser.observed_control import protected_document
from browser.protected_tabs import bound_tab_observable
from browser.stagehand_client import StagehandClient
from deep_agents.offline import SyntheticChatModel
from evals.local_setup_agent_acceptance import fixture_html, fixture_facts
from modules.whatsapp.agent import create_setup_doer
from modules.whatsapp.contract import FIELD_FACTS, VALUES, Phase1Limits, SetupStop
from modules.whatsapp.destination import validate_setup_destination
from modules.whatsapp.session import Phase1Budget, PocGrant, TaskSession
from modules.whatsapp.tools import SetupTools
from modules.whatsapp.workflow import continue_setup


def _html(scenario, origin):
    rendered = fixture_html('challenge' if scenario == 'challenge' else 'closed')
    # All four controls fit in the actual viewport; scrolling is tested separately.
    rendered = rendered.replace('<div style="height:150vh" aria-hidden="true"></div>', '')
    if scenario == 'wrong_step':
        rendered = rendered.replace('>Setup</h2>', '>Audience</h2>')
    suffix = {'wrong_path': '/wrong#setup', 'wrong_query': '/regular?unexpected=1#setup',
              'wrong_fragment': '/regular#audience'}.get(scenario, '/regular#setup')
    if scenario == 'wrong_origin':
        # The real guard rejects this different-origin document request. No policy
        # origin is added, and no alternate server is contacted successfully.
        rendered = rendered.replace('<script>', '<script>location.replace(' +
            json.dumps(origin.replace('127.0.0.1', 'localhost') + '/regular') + ');')
    if scenario == 'dynamic_setup':
        # Preserve the complete app script while delayed content is absent from
        # the live DOM. Rendering and hash transition occur after the Doer starts.
        start = rendered.index('<body>') + len('<body>')
        end = rendered.index('<script>', start)
        body = rendered[start:end]
        rendered = rendered[:start] + '<h1>Local application loading</h1>' + rendered[end:]
        rendered = rendered.replace('<script>', '''<script>
          const loadingAmbient=document.createElement('span');loadingAmbient.setAttribute('aria-hidden','true');
          document.body.append(loadingAmbient);window.__localLoadingPulseCount=0;
          window.__localLoadingAmbient=setInterval(()=>{
            loadingAmbient.textContent=String(++window.__localLoadingPulseCount)},40);
          window.__localStartRender=()=>{
            if(window.__localRenderStarted)return;window.__localRenderStarted=true;
            setTimeout(()=>{clearInterval(window.__localLoadingAmbient);document.body.innerHTML=''' +
            json.dumps(body) + ';history.replaceState(null,"",' + json.dumps(suffix) + ');', 1)
        rendered = rendered.replace('</script>', '''
          const ambient=document.createElement('span');ambient.setAttribute('aria-hidden','true');
          document.body.append(ambient);let pulse=0;
          window.__localAmbient=setInterval(()=>{ambient.textContent=String(++pulse)},80);
        },650);};</script>''', 1)
    else:
        rendered = rendered.replace('<script>', '<script>history.replaceState(null,"",' + json.dumps(suffix) + ');', 1)
    return rendered


@contextmanager
def execution_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.gets += 1
            if self.path == '/handoff':
                body = '<!doctype html><h1>Local simulated one-submit login handoff</h1>'
            elif self.path == '/regular':
                body = _html(self.server.scenario, self.server.origin)
            else:
                self.send_error(404); return
            data = body.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            self.server.posts += 1; self.send_error(405)
        def log_message(self, *args):
            pass
    service = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    service.origin = 'http://127.0.0.1:' + str(service.server_port)
    service.scenario = 'dynamic_setup'; service.gets = service.posts = 0
    worker = Thread(target=lambda: service.serve_forever(poll_interval=.05), daemon=True)
    worker.start()
    try:
        yield service
    finally:
        service.shutdown(); service.server_close(); worker.join(2)


class FixtureTaskSession(TaskSession):
    """Fixture-only destination capability; the production login path is untouched."""
    fixture_destination: str
    fixture_scenario: str

    async def navigate(self, url):
        if not self.fixture:
            raise SetupStop('FIXTURE_AUTHORITY_REQUIRED')
        if self.task_phase != 'setup':
            return await super().navigate(url)
        validate_setup_destination(url, self.fixture_destination)
        if self.setup_navigation_count or self.setup_binding is None:
            raise SetupStop('DESTINATION_NOT_AUTHORIZED')
        before = protected_document(self)
        binding = self.setup_binding
        if before[3:] != (binding.blank_document_generation, binding.blank_frame_id, binding.blank_loader_id):
            raise SetupStop('SETUP_BLANK_DOCUMENT_CHANGED')
        if await self.page.url() != 'about:blank':
            raise SetupStop('SETUP_BLANK_DOCUMENT_CHANGED')
        self.setup_navigation_count += 1
        self.trace.lifecycle('TAB_BINDING', {'state': 'SETUP_NAVIGATION_STARTED', 'code': 'AUTHORIZED_LOCAL_FIXTURE'})
        await StagehandClient.navigate(self, url)
        if self.fixture_scenario == 'dynamic_setup':
            # Fixture instrumentation executes the genuine observer unchanged.
            # Its first completed DOM read arms the app-owned delayed render,
            # proving a loading-state observation even on slow test machines.
            # The local model has no browser object or page-mutation capability.
            evaluate = self.page.evaluate
            self.fixture_observations = []
            async def tracked_read(source, *args, **kwargs):
                data = await evaluate(source, *args, **kwargs)
                if 'const controls=all.filter' in source:
                    self.fixture_observations.append({
                        'setup_identity': data.get('page_identity') is True,
                        'form_recognized': data.get('form_recognized') is True,
                        'campaign_discovered': data.get('fields', {}).get('campaign_name', {}).get('discovered') is True})
                    if len(self.fixture_observations) == 1:
                        await evaluate('window.__localStartRender();true')
                return data
            self.page.evaluate = tracked_read


def _field(data, name):
    return next((f for f in data.get('fields', []) if f['field'] == name), {})


class ExecutionModel(SyntheticChatModel):
    """Deterministic tool decisions; the model never accesses a browser object."""
    scenario: str = 'dynamic_setup'
    requested: list[str] = Field(default_factory=list)
    observed_read_only: bool = False
    observed_incomplete: bool = False
    actual_observation_ok: bool = False
    action_errors: list[str] = Field(default_factory=list)

    def _generate(self, messages, **kwargs):
        self.calls += 1
        results = [m for m in messages if isinstance(m, ToolMessage)]
        try:
            data = json.loads(results[-1].content) if results else None
        except (ValueError, TypeError):
            # LangGraph may refuse an unregistered commit tool before reaching
            # application middleware. Record only that fixed classification.
            data = {'ok': False, 'error': 'PROHIBITED_TOOL_UNAVAILABLE' if results[-1].name == 'Save' else 'LOCAL_TOOL_RESULT_INVALID'}
        if data and results[-1].name == 'observe_page':
            self.actual_observation_ok = data.get('ok') is True
            self.observed_read_only = data.get('mutation_authorization', {}).get('status') == 'READ_ONLY'
            self.observed_incomplete = data.get('global_safety', {}).get('code') == 'SAFETY_OBSERVATION_INCOMPLETE'
        if data and not data.get('ok'):
            self.action_errors.append(data.get('error', 'LOCAL_TOOL_ERROR'))
        if not results:
            name, args = 'observe_page', {}
        elif data and (not data.get('ok') or data.get('terminal')):
            name, args = 'SetupReport', {'status': 'BLOCKED', 'reason': 'tool_error'}
        elif self.scenario == 'prohibited_action' and self.calls == 2:
            name, args = 'Save', {}
        elif self.scenario not in {'dynamic_setup', 'declined_permission'}:
            reference = _field(data, 'campaign_name').get('reference')
            if self.calls == 2 and reference:
                name, args = 'fill_text', {'reference': reference}
            else:
                name, args = 'SetupReport', {'status': 'BLOCKED', 'reason': 'unexpected_page'}
        elif self.calls == 2:
            name, args = 'inspect_control', {'reference': _field(data, 'campaign_name')['reference']}
        elif self.calls == 3:
            name, args = 'fill_text', {'reference': _field(data, 'campaign_name')['reference']}
        elif self.calls == 4:
            name, args = 'verify_field', {'reference': _field(data, 'waba')['reference']}
        elif self.calls == 5:
            f = _field(data, 'business_number')
            name, args = 'select_observed_option', {'reference': f['reference'], 'option_reference': f['options'][0]['reference']}
        elif self.calls == 6:
            name, args = 'click_observed_control', {'reference': _field(data, 'tags')['reference']}
        elif self.calls == 7:
            f = _field(data, 'tags')
            name, args = 'select_observed_option', {'reference': f['reference'], 'option_reference': f['options'][0]['reference']}
        else:
            name, args = 'SetupReport', {'status': 'SETUP_FILLED_UNSAVED', 'reason': 'fields_verified'}
        self.requested.append(name)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[{
            'name': name, 'args': args, 'id': uuid4().hex, 'type': 'tool_call'}]))])


async def _settled(browser, session, url):
    async with asyncio.timeout(5):
        while True:
            browser.guard.lifecycle_changed.clear()
            await browser.guard.ensure_observable()
            owner = browser.guard.session_states[session]
            frame = (await browser.guard.send('Page.getFrameTree', session=session))['frameTree']['frame']
            if frame.get('url') == url and frame.get('id') == owner.frame_id and (frame.get('loaderId') or None) == owner.loader_id:
                return
            await browser.guard.lifecycle_changed.wait()


async def accept_setup_execution(executable, *, output=None):
    """Eight real headed sessions; only loopback documents and local model doubles."""
    if not Path(executable).is_file():
        raise ValueError('Installed Chromium executable required')
    report = {'scope': 'ACTUAL_LOCAL_SETUP_CONTROLLER_EXECUTION', 'headless': False,
        'paid_inference': 0, 'credential_access': False, 'live_login': False,
        'operator_decision_is_fixture_double': True, 'passed': False, 'cases': []}
    started = monotonic()
    scenarios = ('dynamic_setup', 'wrong_path', 'wrong_query', 'declined_permission',
                 'wrong_step', 'challenge', 'prohibited_action', 'wrong_origin')
    with execution_server() as server:
        for scenario in scenarios:
            server.scenario = scenario
            trace = Trace(); trace.reasoner_mode = 'GENUINE_DEEP_AGENTS_LOCAL_MODEL_CONTROLLER_FIXTURE'
            config = Config(str(executable), headless=False, login_url=server.origin + '/handoff',
                expected_host='127.0.0.1', timeout_seconds=90, max_actions=120)
            browser = FixtureTaskSession(config, OriginPolicy({server.origin}), trace, fixture=True)
            browser.fixture_destination = server.origin + '/regular'
            browser.fixture_scenario = scenario
            model = ExecutionModel(scenario=scenario)
            budget = Phase1Budget(Phase1Limits())
            limits = Phase1Limits(checkpoint_seconds=5, setup_seconds=45, hold_seconds=1)
            item = {'case': scenario, 'passed': False}
            result = {'verification': 'UNEXPECTED_PAGE', 'reason_code': 'FIXTURE_AUTH_NOT_CERTIFIED',
                'error': None, 'missing_evidence': []}
            reviews = []; held = []; created = []
            try:
                await browser.start()
                await browser.navigate(config.login_url)
                await _settled(browser, browser.guard.primary_session, config.login_url)
                original = browser.page; context = browser.browser.context; process = browser.process
                # Explicit application-owned synthetic login handoff. No credentials
                # or submit control exists, and no authentication certificate is made.
                browser.claim_login_submission(); trace.fact('SUBMITTED_ONCE')

                async def checkpoint(session, seconds):
                    return PocGrant(session)

                async def approve(payload):
                    reviews.append(payload)
                    return scenario != 'declined_permission'

                async def hold(seconds):
                    if scenario == 'dynamic_setup':
                        held.append(await fixture_facts(browser.page))
                    else:
                        held.append({'read_only': True})

                def factory(session, grant, **kwargs):
                    tools = SetupTools(session, grant, **kwargs)
                    tools.destination = browser.fixture_destination
                    expected = {'observe_page', 'inspect_control', 'scroll_to_control', 'click_observed_control',
                        'fill_text', 'select_observed_option', 'verify_field'}
                    if {t.name for t in tools.registered_tools()} != expected:
                        raise SetupStop('SEVEN_TOOL_CONTRACT_MISMATCH')
                    created.append(tools)
                    return tools

                def agent_factory(model, tools, trace, budget):
                    return create_setup_doer(model, tools, trace, budget, offline=True)

                with patch('modules.whatsapp.workflow.SetupTools', factory), patch('modules.whatsapp.workflow.create_setup_doer', agent_factory):
                    await continue_setup(browser, SimpleNamespace(terminal_error=False), result, model, budget, limits,
                        checkpoint=checkpoint, hold=hold, authorized_mutations=True, mutation_review=approve)
                states = [e.get('diagnostic', {}).get('state') for e in trace.events]
                facts = {f['kind'] for f in trace.step_facts}
                item.update(local_model_requests=model.calls, stage_requests=budget.stage_requests,
                    actual_doer_started='SETUP_DOER_STARTED' in states,
                    actual_observe_tool_requested='observe_page' in model.requested,
                    actual_observation_ok=model.actual_observation_ok,
                    page_observed='SETUP_PAGE_OBSERVED' in states,
                    form_recognized='SETUP_FORM_RECOGNIZED' in states,
                    setup_status=result['setup_status'], stop_reason=result.get('setup_stop_reason'),
                    failed_predicate=result.get('setup_failure_diagnostic'),
                    fixture_login_submissions=browser.login_submissions,
                    same_context=browser.browser.context is context,
                    same_owned_process=browser.process is process,
                    original_retained=browser.login_page is original and await original.evaluate('document.documentElement!==null') is True,
                    protected_tab=bool(browser.setup_binding and bound_tab_observable(browser.guard, browser.setup_binding)),
                    operator_review_calls=len(reviews), hold_calls=len(held),
                    paid_requests=trace.openai_api_calls, action_errors=model.action_errors)
                if scenario == 'dynamic_setup':
                    current = await fixture_facts(browser.page)
                    item.update(all_four_match=all(current[k] for k in ('campaign_matches', 'waba_matches', 'business_matches', 'tag_matches')),
                        all_four_fact_events=set(FIELD_FACTS.values()) <= facts,
                        field_fact_order=[f['kind'] for f in trace.step_facts if f['kind'] in FIELD_FACTS.values()],
                        read_only_before_permission=model.observed_read_only,
                        coverage_remains_incomplete=model.observed_incomplete,
                        mutation_route=result.get('mutation_authorization', {}).get('route'),
                        controller_gates_current=result.get('setup_gates_current') is True,
                        no_commit=current['event_counts']['forbidden_clicks'] == current['event_counts']['submissions'] == 0,
                        hold_values_unchanged=bool(held and held[0] == current),
                        ambient_dom_observed=await browser.page.evaluate('!!window.__localAmbient') is True,
                        ambient_pulses_during_loading=await browser.page.evaluate('window.__localLoadingPulseCount>=5') is True,
                        genuine_first_observation_before_render=bool(browser.fixture_observations and not any(browser.fixture_observations[0].values())),
                        genuine_later_observation_recognized_form=any(o['form_recognized'] for o in browser.fixture_observations[1:]),
                        loading_identity_before_form_event=states.index('SETUP_PAGE_OBSERVED') < states.index('SETUP_FORM_RECOGNIZED')
                            if 'SETUP_FORM_RECOGNIZED' in states else False)
                    item['passed'] = all(item[k] for k in ('actual_doer_started', 'actual_observe_tool_requested', 'actual_observation_ok',
                        'page_observed', 'form_recognized', 'all_four_match', 'all_four_fact_events', 'read_only_before_permission',
                        'coverage_remains_incomplete', 'controller_gates_current', 'no_commit', 'hold_values_unchanged', 'ambient_dom_observed',
                        'ambient_pulses_during_loading', 'genuine_first_observation_before_render',
                        'genuine_later_observation_recognized_form', 'loading_identity_before_form_event'))
                    item['passed'] &= result['setup_status'] == 'SETUP_FILLED_UNSAVED' and len(reviews) == 1 and model.calls == 8
                elif scenario == 'wrong_origin':
                    item['passed'] = result['setup_status'] == 'BLOCKED' and not set(FIELD_FACTS.values()) & facts
                    item['passed'] &= bool(browser.guard.failed or browser.guard.blocking_hosts)
                else:
                    current = await fixture_facts(browser.page)
                    counts = current['event_counts']
                    no_writes = not any(counts[k] for k in ('input_events', 'change_events', 'forbidden_clicks', 'submissions'))
                    item['no_field_writes_or_commit'] = no_writes
                    item['passed'] = item['actual_doer_started'] and item['actual_observe_tool_requested'] and result['setup_status'] == 'BLOCKED'
                    item['passed'] &= no_writes and not current['campaign_matches']
                    if scenario == 'challenge':
                        item['passed'] &= 'AUTHENTICATION_OR_SECURITY_STOP' in model.action_errors and not reviews
                    elif scenario == 'prohibited_action':
                        item['passed'] &= bool({'TOOL_FORBIDDEN', 'PROHIBITED_TOOL_UNAVAILABLE'} & set(model.action_errors)) and not reviews
                    elif scenario == 'declined_permission':
                        item['passed'] &= 'MUTATION_REVIEW_DECLINED' in model.action_errors and len(reviews) == 1
                    else:
                        item['passed'] &= model.actual_observation_ok and not reviews
                item['passed'] &= browser.login_submissions == 1 and trace.openai_api_calls == 0 and browser.setup_navigation_count == 1
                item['passed'] &= browser.process is process and browser.browser.context is context and not browser.close_started
                item['passed'] &= len(await context.pages()) == 2
            except Exception as error:
                item.update(error_category=type(error).__name__, error_code=getattr(error, 'code', None))
            finally:
                try:
                    await browser.close()
                except Exception:
                    item['cleanup_exception'] = True
                item['cleanup_result'] = trace.cleanup_result
                item['passed'] = bool(item['passed'] and trace.cleanup_result == 'CLOSED_AND_PROFILE_REMOVED')
                # Preserve event ordering with fixed categories only; no page URL,
                # selector, control reference, text, target/session identity or DOM.
                item['milestones'] = [{'event_index': i, 'state': e['diagnostic']['state']}
                    for i, e in enumerate(trace.events) if e.get('diagnostic_stage') == 'WHATSAPP' and 'state' in e.get('diagnostic', {})]
                report['cases'].append(item)
        report['fixture_posts'] = server.posts
    report['duration_seconds'] = round(monotonic() - started, 3)
    report['passed'] = len(report['cases']) == len(scenarios) and all(c['passed'] for c in report['cases']) and report['fixture_posts'] == 0
    path = Path(output or ROOT / 'outputs' / 'real-local-setup-execution.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report

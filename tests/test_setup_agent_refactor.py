"""Composed protected-tab/agent permission tests; local doubles, never live work."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.state import Observation
from deep_agents.offline import SyntheticChatModel
from deep_agents.schemas import DoerReport
from modules.whatsapp.agent import create_setup_doer
from modules.whatsapp.contract import FIELD_FACTS, ORDER, SETUP_URL, VALUES, Phase1Limits
from modules.whatsapp.scope_evidence import HOST_FACTS
from modules.whatsapp.session import Phase1Budget, PocGrant, source_signature
from modules.whatsapp.tools import SetupTools
from modules.whatsapp.contract import SetupStop
from test_whatsapp_phase1 import PageDouble, protocol_metadata_transport, session


TOOLS = {'observe_page', 'inspect_control', 'scroll_to_control', 'click_observed_control',
         'fill_text', 'select_observed_option', 'verify_field', 'SetupReport'}
FORM = '#PRIVATE_FORM'


class RefactorPage(PageDouble):
    def configure(self):
        self.guard_installed = False
        self.guard_installs = 0
        self.values['waba'] = VALUES['waba']
        self.closed_scope = True
        self.closed_root_id = 502
        self.overrides = {}
        self.same_form = True
        self.form_recognized = True
        self.locator_matches = {}
        self.observation_reads = 0
        selectors = [FORM]
        selectors += ['#PRIVATE_' + field for field in ORDER]
        selectors += ['#PRIVATE_OPTION_' + field for field in ORDER[1:]]
        self.nodes = {selector: 1000 + index for index, selector in enumerate(selectors)}
        self.backends = {selector: 10000 + index for index, selector in enumerate(selectors)}

    def descriptor(self):
        fields = {}
        for field in ORDER:
            native = field != 'campaign_name'
            count = self.option_count[field] if native else 0
            fields[field] = {
                'discovered': True, 'selector': '#PRIVATE_' + field, 'selector_unique': True,
                'form_selector': FORM, 'kind': 'NATIVE_SELECT' if native else 'TEXT_INPUT',
                'action_safe': True, 'selected': self.values.get(field) == VALUES[field],
                'exact_option_count': count, 'existing_exact_option_count': count,
                'value': VALUES[field], 'option_selector': '#PRIVATE_OPTION_' + field if native else None,
                'open_selector': None, 'search_selector': None, 'expanded': False,
                'visible': True, 'enabled': True, 'disabled': False, 'ownership_verified': True,
                'unobscured': True, 'in_viewport': True, 'geometry_status': 'UNOBSCURED',
                'option_visible': False, 'option_enabled': True, 'option_selector_unique': native,
                'option_unobscured': False, 'search_visible': False, 'search_selector_unique': False,
                'multiple': field == 'tags', 'has_other_tags': False, 'same_form': True,
                **self.overrides.get(field, {}),
            }
        return {'fields': fields, 'form_selector': FORM, 'form_recognized': self.form_recognized,
                'same_form': self.same_form, 'setup_active': self.setup_active, 'page_identity': True,
                'observation_complete': True, 'observation_scope': 'DOCUMENT_LIGHT_DOM',
                'closed_shadow_scope_certified': False}

    async def evaluate(self, source):
        if 'const controls=all.filter' in source:
            self.observation_reads += 1
            return self.descriptor()
        if '({installed:!!window.__netcoreSetupGuard' in source:
            return {'installed': self.guard_installed, 'attempts': self.forbidden}
        if 'Object.defineProperty' in source:
            self.guard_installs += 1
        return await super().evaluate(source)

    def locator(self, selector):
        page = self
        field = selector.removeprefix('#PRIVATE_')
        class Locator:
            async def count(self, **kwargs):
                return page.locator_matches.get(selector, 1)
            async def fill(self, value, **kwargs):
                assert field == 'campaign_name' and value == VALUES[field]
                page.actions.append(('fill', field))
                if page.fail_field == field:
                    raise RuntimeError('PRIVATE_UNCERTAIN_WRITE')
                page.values[field] = value
            async def select_option(self, values, **kwargs):
                assert field in ORDER[1:] and values == [VALUES[field]]
                page.actions.append(('select', field))
                page.values[field] = values[0]
            async def click(self, **kwargs):
                raise AssertionError('This native-control fixture exposes no generic click')
        return Locator()


@pytest.fixture
def protected_factory(monkeypatch, protocol_metadata_transport):
    # Both production readers use the same fake-only transport fixture. No local
    # HTTP, websocket, credentials, browser launch or model client is constructed.
    import browser.metadata_channel as metadata
    import modules.whatsapp.scope_evidence as scope
    monkeypatch.setattr(scope, 'metadata_channel', metadata.metadata_channel)

    def make(*, negative=None):
        browser = session()
        context = browser.browser.context
        original_new = context.new_page
        original_send = browser.guard.send
        async def new_page(url):
            page = await original_new(url)
            page.__class__ = RefactorPage
            page.configure()
            page.negative = negative
            return page
        context.new_page = new_page
        async def send(method, params=None, session=None):
            page = next((p for p in context.items if p.owner.session == session), None)
            if method == 'DOM.getDocument':
                root = {'nodeType': 9, 'nodeName': '#document', 'nodeId': 1, 'backendNodeId': 1,
                        'childNodeCount': 0}
                if params.get('depth', 0) and getattr(page, 'closed_scope', False):
                    root.update(childNodeCount=1, children=[{'nodeType': 1, 'nodeName': 'PRIVATE-COMPONENT',
                        'backendNodeId': 501, 'childNodeCount': 0, 'shadowRoots': [
                            {'nodeType': 11, 'backendNodeId': page.closed_root_id,
                             'shadowRootType': 'closed', 'children': [{'private': 'NEVER_TRAVERSE'}]}]}])
                return {'root': root}
            if method == 'DOM.querySelectorAll':
                assert params['nodeId'] == 1
                return {'nodeIds': [page.nodes[params['selector']]] if params['selector'] in page.nodes else []}
            if method == 'DOM.describeNode':
                selector = next(key for key, value in page.nodes.items() if value == params['nodeId'])
                return {'node': {'backendNodeId': page.backends[selector]}}
            if method == 'DOM.resolveNode':
                assert params == {'backendNodeId': 501}
                return {'object': {'objectId': 'PRIVATE_HOST_OBJECT'}}
            if method == 'Runtime.callFunctionOn':
                assert params['functionDeclaration'] == HOST_FACTS
                return {'result': {'value': {'host_kind': 'CUSTOM', 'role_category': 'UNESTABLISHED',
                    'visibility': 'UNKNOWN', 'form_relation': 'UNKNOWN', 'same_form': 'UNKNOWN',
                    'document_scope': 'PERMITTED_FRAME', 'component': 'UNESTABLISHED', 'function': 'UNESTABLISHED'}}}
            if method == 'Runtime.releaseObject':
                assert params == {'objectId': 'PRIVATE_HOST_OBJECT'}
                return {}
            if method == 'DOM.scrollIntoViewIfNeeded':
                assert params['backendNodeId'] in page.backends.values()
                page.actions.append(('scroll', 'observed_control'))
                return {}
            return await original_send(method, params, session)
        browser.guard.send = send
        return browser
    return make


async def prepared(factory, *, authorized=False, review=None, negative=None):
    browser = factory(negative=negative)
    browser.begin_phase('checkpoint', 90, 4)
    browser.begin_phase('setup', 120, 36)
    grant = PocGrant(browser)
    grant.route = 'SUBMITTED_ONCE_POC'
    grant.source_signature = source_signature(browser)
    grant.source_url = await browser.page.url()
    tools = SetupTools(browser, grant, authorized_mutations=authorized, mutation_review=review)
    data = await tools.open()
    assert data['page_state']=='SETUP_CONFIRMED' and data['destination_ready']
    assert tools.safety_status['code']=='SAFETY_OBSERVATION_INCOMPLETE'
    assert browser.browser.context.creations == 1 and browser.login_page is not browser.page
    return browser, tools, data


def field(data, name):
    return next(item for item in data['fields'] if item['field'] == name)


def writes(browser):
    return [action for action in browser.page.actions if action[0] in {'fill', 'select', 'click'}]


class ReadOnlyModel(SyntheticChatModel):
    def _generate(self, messages, **kwargs):
        self.calls += 1
        last = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
        data = json.loads(last.content) if last else None
        if self.calls == 1:
            name, args = 'observe_page', {}
        elif self.calls == 2:
            name, args = 'inspect_control', {'reference': field(data, 'campaign_name')['reference']}
        elif self.calls == 3:
            name, args = 'fill_text', {'reference': field(data, 'campaign_name')['reference']}
        elif self.calls == 4:
            assert data['error'] == 'MUTATION_AUTHORIZATION_REQUIRED' and not data['terminal']
            name, args = 'observe_page', {}
        else:
            assert data['ok']
            name, args = 'SetupReport', {'status': 'BLOCKED', 'reason': 'unsupported_control'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
            {'name': name, 'args': args, 'id': uuid4().hex, 'type': 'tool_call'}]))])


class CompleteModel(SyntheticChatModel):
    def _generate(self, messages, **kwargs):
        self.calls += 1
        last = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
        data = json.loads(last.content) if last else None
        if self.calls == 1:
            name, args = 'observe_page', {}
        elif self.calls == 2:
            name, args = 'fill_text', {'reference': field(data, 'campaign_name')['reference']}
        elif self.calls == 3:
            name, args = 'verify_field', {'reference': field(data, 'waba')['reference']}
        elif self.calls in (4, 5):
            target = field(data, 'business_number' if self.calls == 4 else 'tags')
            name, args = 'select_observed_option', {'reference': target['reference'],
                'option_reference': target['options'][0]['reference']}
        else:
            assert set(data['verified_fields']) == set(ORDER)
            name, args = 'SetupReport', {'status': 'SETUP_FILLED_UNSAVED', 'reason': 'fields_verified'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
            {'name': name, 'args': args, 'id': uuid4().hex, 'type': 'tool_call'}]))])


async def test_genuine_doer_observes_global_gap_and_reads_after_denied_unauthorized_write(protected_factory):
    browser, tools, _ = await prepared(protected_factory)
    budget = Phase1Budget(Phase1Limits())
    budget.phase = 'setup'
    model = ReadOnlyModel()
    agent, permissions = create_setup_doer(model, tools, browser.trace, budget, offline=True)
    result = await agent.ainvoke({'messages': [{'role': 'user', 'content': 'Observe Setup; separate permission is required.'}]},
        {'configurable': {'thread_id': browser.trace.run_id}, 'recursion_limit': 40})
    assert result['structured_response'].status == 'BLOCKED'
    assert permissions.allowed == TOOLS and not permissions.allowed.intersection({'submit_login', 'execute', 'task', 'navigate_approved'})
    calls = [event['tool_name'] for event in browser.trace.events if event.get('tool_phase') == 'requested']
    assert calls == ['observe_page', 'inspect_control', 'fill_text', 'observe_page', 'SetupReport']
    assert tools.safety_status['code'] == 'SAFETY_OBSERVATION_INCOMPLETE'
    assert model.calls == budget.stage_requests['setup'] == 5
    assert budget.stage_caps['setup'] == 8 and browser.trace.openai_api_calls == 0
    assert not writes(browser) and not browser.page.guard_installed and not tools.terminal_error
    assert 'PRIVATE_' not in json.dumps(browser.trace.events)


async def test_explicit_operator_review_binds_actual_risk_without_certifying_it(protected_factory):
    payloads = []
    async def approve(payload):
        payloads.append(payload)
        assert payload['complete_negative_coverage'] is False
        assert payload['independent_authentication_certified'] is False
        assert payload['closed_root_investigation']['risks'][0]['function'] == 'UNESTABLISHED'
        assert 'binding' not in payload['closed_root_investigation']
        return True
    browser, tools, data = await prepared(protected_factory, authorized=True, review=approve)
    result = await tools.fill_text(field(data, 'campaign_name')['reference'])
    assert result['ok'] and writes(browser) == [('fill', 'campaign_name')]
    assert tools.mutation_grant.route == 'OPERATOR_REVIEWED_SCOPED_POC'
    assert tools.mutation_grant.risk_binding is not None and len(payloads) == 1
    summary = result['mutation_authorization']
    assert summary['remaining_uncertainty_acknowledged'] and not summary['complete_negative_coverage']
    assert not summary['independent_authentication_certified'] and not summary['campaign_commit_authorized']
    assert browser.page.guard_installs == 1
    assert 'PRIVATE_' not in json.dumps(payloads + [result] + browser.trace.events)


async def test_declined_review_does_not_authorize_or_repeat_writes_but_keeps_read_path(protected_factory):
    review = AsyncMock(return_value=False)
    browser, tools, data = await prepared(protected_factory, authorized=True, review=review)
    result = await tools.fill_text(field(data, 'campaign_name')['reference'])
    assert result['error'] == 'MUTATION_REVIEW_DECLINED' and not result['terminal']
    assert (await tools.observe_page())['ok'] and not writes(browser)
    assert review.await_count == 1 and tools.mutation_grant is None


@pytest.mark.parametrize('negative', ['credential_form', 'challenge', 'rejected', 'expired', 'account_restricted'])
async def test_actual_negative_is_a_hard_stop_even_with_operator_authorization(protected_factory, negative):
    review = AsyncMock(return_value=True)
    browser, tools, data = await prepared(protected_factory, authorized=True, review=review)
    browser.page.negative = negative
    result = await tools.fill_text(field(data, 'campaign_name')['reference'])
    assert result['error'] == 'AUTHENTICATION_OR_SECURITY_STOP' and result['terminal']
    assert tools.terminal_error and not writes(browser)
    review.assert_not_awaited()


@pytest.mark.parametrize('change,expected', [
    ('challenge', 'AUTHENTICATION_OR_SECURITY_STOP'),
    ('control', 'STALE_CONTROL_REFERENCE'),
    ('risk', 'MUTATION_SCOPE_CHANGED'),
])
async def test_changes_during_operator_review_prevent_the_first_write(protected_factory, change, expected):
    async def approve(_):
        if change == 'challenge':
            browser.page.negative = 'challenge'
        elif change == 'control':
            browser.page.backends['#PRIVATE_campaign_name'] += 100
        else:
            browser.page.closed_root_id += 100
        return True
    browser, tools, data = await prepared(protected_factory, authorized=True, review=approve)
    result = await tools.fill_text(field(data, 'campaign_name')['reference'])
    assert result['error'] == expected and not writes(browser) and tools.mutation_grant is None


async def test_changed_verified_prerequisite_blocks_next_field_and_retains_previous_fact(protected_factory):
    browser, tools, data = await prepared(protected_factory, authorized=True, review=AsyncMock(return_value=True))
    data = await tools.fill_text(field(data, 'campaign_name')['reference'])
    assert data['ok']
    data = await tools.verify_field(field(data, 'waba')['reference'])
    assert data['ok']
    browser.page.values['campaign_name'] = 'LOCAL_OPERATOR_EDIT'
    target = field(data, 'business_number')
    result = await tools.select_observed_option(target['reference'], target['options'][0]['reference'])
    assert result['error'] == 'SETUP_PREREQUISITE_CHANGED'
    assert writes(browser) == [('fill', 'campaign_name')]
    assert {'CAMPAIGN_NAME_VERIFIED', 'WABA_VERIFIED'} <= {fact['kind'] for fact in browser.trace.step_facts}
    assert 'BUSINESS_NUMBER_VERIFIED' not in {fact['kind'] for fact in browser.trace.step_facts}


async def test_existing_scoped_grant_is_revoked_when_closed_root_binding_changes(protected_factory):
    review = AsyncMock(return_value=True)
    browser, tools, data = await prepared(protected_factory, authorized=True, review=review)
    data = await tools.fill_text(field(data, 'campaign_name')['reference'])
    data = await tools.verify_field(field(data, 'waba')['reference'])
    browser.page.closed_root_id += 1
    target = field(data, 'business_number')
    result = await tools.select_observed_option(target['reference'], target['options'][0]['reference'])
    assert result['error'] == 'MUTATION_SCOPE_CHANGED' and tools.mutation_grant is None
    assert review.await_count == 1 and writes(browser) == [('fill', 'campaign_name')]
    assert {'CAMPAIGN_NAME_VERIFIED', 'WABA_VERIFIED'} <= {fact['kind'] for fact in browser.trace.step_facts}


async def test_stagehand_ambiguous_locator_blocks_action_after_unique_dom_observation(protected_factory):
    browser, tools, data = await prepared(protected_factory, authorized=True, review=AsyncMock(return_value=True))
    browser.page.locator_matches['#PRIVATE_campaign_name'] = 2
    result = await tools.fill_text(field(data, 'campaign_name')['reference'])
    assert result['error'] == 'ACTION_LOCATOR_NOT_UNIQUE' and result['terminal']
    assert not writes(browser) and tools.mutation_grant is None


@pytest.mark.parametrize('property', ['ownership_verified', 'same_form', 'visible', 'selector_unique'])
async def test_final_field_verification_requires_current_owned_mapping(protected_factory, property):
    browser, tools, _ = await prepared(protected_factory)
    browser.page.values.update(VALUES)
    browser.page.overrides['tags'] = {property: False}
    result = await tools.verify()
    assert not result['ok'] and result['error'] == 'FIELD_MAPPING_NOT_VERIFIED'
    assert not tools.final_gates_current
    assert 'SETUP_FIELDS_VERIFIED' not in {fact['kind'] for fact in browser.trace.step_facts}
    assert not writes(browser)


async def test_actual_agent_records_each_field_and_controller_verifier_checks_all_four(protected_factory):
    browser, tools, _ = await prepared(protected_factory, authorized=True, review=AsyncMock(return_value=True))
    budget = Phase1Budget(Phase1Limits())
    budget.phase = 'setup'
    model = CompleteModel()
    agent, _ = create_setup_doer(model, tools, browser.trace, budget, offline=True)
    state = await agent.ainvoke({'messages': [{'role': 'user', 'content': 'Observe and fill the fixed Setup fields only.'}]},
        {'configurable': {'thread_id': browser.trace.run_id}, 'recursion_limit': 50})
    assert state['structured_response'].status == 'SETUP_FILLED_UNSAVED'
    verified = await tools.verify()
    assert verified['ok'] and browser.page.values == VALUES
    facts = [fact['kind'] for fact in browser.trace.step_facts]
    assert all(facts.count(kind) == 1 for kind in FIELD_FACTS.values())
    assert 'SETUP_FIELDS_VERIFIED' in facts and 'NO_FORBIDDEN_TRANSITION_ATTEMPTED' in facts
    assert writes(browser) == [('fill', 'campaign_name'), ('select', 'business_number'), ('select', 'tags')]
    assert model.calls == 6 <= budget.stage_caps['setup'] == 8
    assert browser.login_submissions == 1 and browser.trace.openai_api_calls == 0


async def test_campaign_does_not_require_current_mapping_of_other_fields(protected_factory,monkeypatch):
    import modules.whatsapp.tools as module
    inventory_requests=[];reader=module.read_safety_snapshot
    async def record(browser,*,include_inventory=True):
        inventory_requests.append(include_inventory)
        return await reader(browser,include_inventory=include_inventory)
    monkeypatch.setattr(module,'read_safety_snapshot',record)
    browser,tools,_=await prepared(protected_factory,authorized=True,review=AsyncMock(return_value=True))
    original=browser.page.descriptor
    def campaign_only():
        data=original();data['fields']={'campaign_name':data['fields']['campaign_name']}
        data['same_form']=False
        return data
    browser.page.descriptor=campaign_only
    data=await tools.observe_page()
    ref=field(data,'campaign_name')['reference']
    assert data['form_recognized'] and all(field(data,k)['reference'] is None for k in ORDER[1:])
    assert (await tools.inspect_control(ref))['ok'] and not any(inventory_requests)
    data=await tools.fill_text(ref)
    assert data['ok'] and data['verified_fields']==['campaign_name']
    assert browser.page.values['campaign_name']==VALUES['campaign_name']
    assert 'CAMPAIGN_NAME_VERIFIED' in tools.completed_facts()
    assert any(inventory_requests) and writes(browser)==[('fill','campaign_name')]


async def test_composed_controller_starts_doer_across_global_gap_then_cleans_owned_session(protected_factory, monkeypatch, tmp_path):
    import deep_agents.controller as controller
    import modules.whatsapp.workflow as workflow
    browser = protected_factory()
    source_page = browser.page
    browser.start = AsyncMock()
    browser.observe = AsyncMock(return_value=Observation(expected_host=True))
    browser.verification_details = {'reason': 'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED',
        'missing': ['dashboard', 'tenant_matches', 'readonly_access', 'evidence_configured']}
    async def close():
        browser.trace.cleanup_result = 'CLOSED_AND_PROFILE_REMOVED'
        browser.trace.add('close')
    browser.close = AsyncMock(side_effect=close)
    class LoginAgent:
        async def ainvoke(self, *args):
            return {'structured_response': DoerReport(status='blocked', reason='wrong_context')}
    monkeypatch.setattr(controller, 'create_doer', lambda *args, **kwargs: (LoginAgent(), None))
    monkeypatch.setattr(controller, 'ROOT', tmp_path)
    class Observer:
        def __init__(self, browser): self.active = False
        async def start(self): pass
        async def close(self): pass
        def summary(self): return {'server_persistence': 'UNDETERMINED'}
    monkeypatch.setattr(workflow, 'PersistenceObserver', Observer)
    factories = []
    def factory(model, tools, trace, budget):
        factories.append(tools)
        assert tools.authorized_mutations and tools.safety_status['code']=='SAFETY_OBSERVATION_INCOMPLETE'
        assert 'SETUP_PAGE_OBSERVED' in tools.milestones and tools.form_identity is not None
        assert tools.destination_ready and not writes(browser)
        return create_setup_doer(model, tools, trace, budget, offline=True)
    monkeypatch.setattr(workflow, 'create_setup_doer', factory)
    async def checkpoint(actual, seconds): return PocGrant(actual)
    async def hold(seconds):
        assert browser.close.await_count == 0 and browser.page.values == VALUES
    budget = Phase1Budget(Phase1Limits())
    model = CompleteModel()
    async def continuation(actual, tools, result):
        await workflow.continue_setup(actual, tools, result, model, budget, Phase1Limits(),
            checkpoint=checkpoint, hold=hold, authorized_mutations=True, mutation_review=AsyncMock(return_value=True))
    login = SimpleNamespace(terminal_error=False, navigation_count=1, entry_url=browser.config.login_url)
    result = await controller._execute_doer(browser, login, 'login', None, budget, True,
        after_login=continuation, login_timeout=1)
    assert len(factories) == 1 and result['verification'] == 'SETUP_FILLED_UNSAVED'
    assert result['authentication_verification'] == 'UNEXPECTED_PAGE' and not result['live_certified']
    assert result['setup_verified_fields'] == sorted(ORDER)
    assert result['mutation_authorization']['route'] == 'OPERATOR_REVIEWED_SCOPED_POC'
    assert result['setup_safety_status']['code'] == 'SAFETY_OBSERVATION_INCOMPLETE'
    assert browser.start.await_count == browser.close.await_count == 1
    assert result['cleanup_result'] == 'CLOSED_AND_PROFILE_REMOVED'
    assert source_page in browser.browser.context.items and browser.browser.context.creations == 1
    assert browser.login_submissions == 1 and model.calls == 6
    assert 'PRIVATE_' not in json.dumps(result)
    from deep_agents.evidence import compile_evidence,tokens
    from modules.whatsapp.review import WhatsAppReviewAdapter
    package=compile_evidence(result,browser.trace.events,WhatsAppReviewAdapter(result,browser.trace.events))
    assert tokens(package.model_dump())<=6000
    assert package.module_evidence['mutation_authorization']['complete_negative_coverage'] is False
    assert package.module_evidence['scoped_mutation_review']['operator_approved'] is True
    assert package.module_evidence['authentication_verification']=='UNEXPECTED_PAGE'


async def test_scroll_rejects_option_references_before_any_scroll_command(protected_factory):
    browser,tools,data=await prepared(protected_factory)
    calls=[];send=browser.guard.send
    async def tracked(method,*args,**kwargs):
        calls.append(method);return await send(method,*args,**kwargs)
    browser.guard.send=tracked
    hidden_option=field(data,'waba')['options'][0]['reference']
    denied=await tools.scroll_to_control(hidden_option)
    assert denied['error']=='UNOBSERVED_SETUP_ELEMENT'
    assert 'DOM.scrollIntoViewIfNeeded' not in calls and not writes(browser)


async def test_controller_finalization_protects_preexisting_values_without_field_writes(protected_factory):
    browser,tools,data=await prepared(protected_factory)
    browser.page.values.update(VALUES)
    assert browser.page.guard_installs==0
    observed=await tools.observe_page()
    assert observed['ok'] and browser.page.guard_installs==0
    result=await tools.verify()
    assert result['status']=='SETUP_FILLED_UNSAVED' and browser.page.guard_installs==1
    assert not writes(browser) and tools.mutation_grant is None


async def test_timeout_denies_mutations_but_keeps_reads_and_cannot_be_overridden_by_later_global_certificate(protected_factory):
    async def timeout(payload):raise SetupStop('MUTATION_REVIEW_TIMEOUT')
    browser,tools,data=await prepared(protected_factory,authorized=True,review=timeout)
    denied=await tools.fill_text(field(data,'campaign_name')['reference'])
    assert denied['error']=='MUTATION_REVIEW_TIMEOUT' and not denied['terminal']
    browser.page.closed_scope=False
    observed=await tools.observe_page()
    assert observed['ok'] and tools.safety_status['code']=='SAFETY_OBSERVATION_INCOMPLETE'
    assert (await tools.scan(include_inventory=True)) and tools.safety_status['code'] is None
    again=await tools.fill_text(field(observed,'campaign_name')['reference'])
    assert again['error']=='MUTATION_REVIEW_TIMEOUT' and not writes(browser)


async def test_actual_terminal_review_distinguishes_missing_terminal_and_deadline(monkeypatch):
    import modules.whatsapp.authorization as module
    monkeypatch.setattr(module.sys.stdin,'isatty',lambda:False)
    with pytest.raises(SetupStop) as unavailable:await module.confirm_scoped_mutation({})
    assert unavailable.value.code=='MUTATION_REVIEW_UNAVAILABLE'
    monkeypatch.setattr(module.sys.stdin,'isatty',lambda:True)
    if module.sys.platform=='win32':
        import msvcrt
        monkeypatch.setattr(msvcrt,'kbhit',lambda:False)
    else:
        import select
        monkeypatch.setattr(select,'select',lambda *args:([],[],[]))
    with pytest.raises(SetupStop) as deadline:await module.confirm_scoped_mutation({},seconds=0)
    assert deadline.value.code=='MUTATION_REVIEW_TIMEOUT'

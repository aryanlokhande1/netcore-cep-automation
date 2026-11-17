"""Read-only host investigation with local protocol doubles; no browser or model."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from modules.whatsapp import scope_evidence as module


ORIGIN = 'https://scope-fixture.invalid'
FORM = '#PRIVATE_OBSERVED_FORM'


def node(key, **fields):
    return {'backendNodeId': key, 'nodeType': 1, 'nodeName': 'DIV', **fields}


def root_with_closed_host():
    # Invalid descendants prove closed and UA content is never traversed.
    return node(1, nodeType=9, nodeName='#document', children=[
        node(2, nodeName='PRIVATE-COMPONENT', attributes=['role', 'PRIVATE_ROLE'], shadowRoots=[
            node(3, nodeType=11, shadowRootType='closed', children=[{'private': 'CLOSED_CONTENT'}])]),
        node(4, shadowRoots=[node(5, nodeType=11, shadowRootType='user-agent',
                                children=[{'private': 'UA_CONTENT'}])]),
    ])


def observed_facts(**updates):
    return {'host_kind': 'CUSTOM', 'role_category': 'UNESTABLISHED',
            'visibility': 'VISIBLE', 'form_relation': 'OUTSIDE_FORM', 'same_form': 'NO',
            'document_scope': 'TOP_LEVEL_ALLOWED', 'component': 'UNESTABLISHED',
            'function': 'UNESTABLISHED', **updates}


class Reader:
    def __init__(self, root):
        self.root = root
        self.mutation_epoch = 0
        self.calls = []
        self.expansions = {}
        self.closed = False
        self.block = False

    async def send(self, method, params, session):
        self.calls.append((method, params, session))
        if self.block:
            await asyncio.Future()
        if method == 'DOM.getDocument':
            assert params in ({'depth': 12, 'pierce': True}, {'depth': 0, 'pierce': False})
            return {'root': deepcopy(self.root) if params['depth'] else
                    {'backendNodeId': self.root['backendNodeId'], 'nodeType': 9}}
        assert method == 'DOM.describeNode' and params['depth'] == 12 and params['pierce'] is True
        return {'node': deepcopy(self.expansions[params['backendNodeId']])}


def fixture(monkeypatch, *, root=None, facts=None):
    source = SimpleNamespace(target='PRIVATE_SOURCE_TARGET', session='PRIVATE_SOURCE_SESSION',
        kind='page', generation=1, document_generation=1, frame_id='PRIVATE_SOURCE_FRAME',
        loader_id='PRIVATE_SOURCE_LOADER')
    owner = SimpleNamespace(target='PRIVATE_SETUP_TARGET', session='PRIVATE_SETUP_SESSION',
        kind='page', generation=1, document_generation=2, frame_id='PRIVATE_SETUP_FRAME',
        loader_id='PRIVATE_SETUP_LOADER')
    guard = SimpleNamespace(failed=False, closing=False, blocking_hosts=set(),
        primary_session=source.session, session_states={source.session: source, owner.session: owner},
        current_sessions={source.target: source.session, owner.target: owner.session},
        explicit_page_roots={owner.session}, calls=[], mutate_on=None,
        observation=observed_facts() if facts is None else facts, release_fails=False)
    guard.primary_observable = lambda: not guard.failed
    guard.session_observable = lambda session: not guard.failed and session in guard.session_states
    async def ensure():
        return None
    guard.ensure_observable = ensure
    binding = SimpleNamespace(guard=guard, target=owner.target, session=owner.session,
        generation=owner.generation, source_target=source.target, source_session=source.session,
        source_generation=source.generation, source_document_generation=source.document_generation)
    browser = SimpleNamespace(guard=guard, setup_binding=binding, close_started=False,
                              policy=SimpleNamespace(origins={ORIGIN}))
    reader = Reader(root if root is not None else root_with_closed_host())

    async def send(method, params, session):
        assert session == owner.session
        guard.calls.append((method, deepcopy(params), session))
        if method == 'DOM.resolveNode':
            assert params == {'backendNodeId': 2}
            response = {'object': {'objectId': 'PRIVATE_REMOTE_OBJECT'}}
        elif method == 'Runtime.callFunctionOn':
            assert params['functionDeclaration'] == module.HOST_FACTS
            assert params['objectId'] == 'PRIVATE_REMOTE_OBJECT'
            assert params['arguments'] == [{'value': {'form_selector': FORM, 'origins': [ORIGIN]}}]
            assert params['returnByValue'] is True and params['silent'] is True
            response = {'result': {'value': deepcopy(guard.observation)}}
        else:
            assert method == 'Runtime.releaseObject' and params == {'objectId': 'PRIVATE_REMOTE_OBJECT'}
            if guard.release_fails:
                raise RuntimeError('PRIVATE_RELEASE_ERROR')
            response = {}
        if guard.mutate_on == method:
            owner.document_generation += 1
        return response
    guard.send = send

    @asynccontextmanager
    async def channel(actual_browser, session):
        assert actual_browser is browser and session == owner.session
        try:
            yield reader
        finally:
            reader.closed = True
    monkeypatch.setattr(module, 'metadata_channel', channel)
    return browser, guard, reader


def public(result):
    return {key: value for key, value in result.items() if key != 'binding'}


async def test_closed_host_observation_is_scoped_not_a_claim_that_outside_form_is_safe(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    result = await module.investigate_closed_scope(browser, FORM)
    assert result['complete'] is True and result['closed_roots'] == 1
    assert result['risks'] == [observed_facts()]
    assert result['risks'][0]['function'] == result['risks'][0]['component'] == 'UNESTABLISHED'
    assert [call[0] for call in guard.calls] == ['DOM.resolveNode', 'Runtime.callFunctionOn', 'Runtime.releaseObject']
    assert len(reader.calls) == 2 and reader.closed
    assert 'PRIVATE' not in json.dumps(public(result))
    assert 'CLOSED_CONTENT' not in repr(result) and 'UA_CONTENT' not in repr(result)
    assert result['binding'][0][1] == 'PRIVATE_SETUP_SESSION'


@pytest.mark.parametrize('facts', [
    observed_facts(form_relation='INSIDE_FORM', same_form='YES'),
    observed_facts(form_relation='INTERSECTS_FORM', role_category='DIALOG'),
    observed_facts(visibility='NOT_VISIBLE'),
])
async def test_actual_canonical_host_relationships_are_preserved_without_safety_inference(monkeypatch, facts):
    browser, _, _ = fixture(monkeypatch, facts=facts)
    result = await module.investigate_closed_scope(browser, FORM)
    assert result['complete'] and result['risks'] == [facts]
    assert set(result) == {'complete', 'risks', 'binding', 'closed_roots', 'ambient_mutation_observed'}


@pytest.mark.parametrize('facts', [
    observed_facts(document_scope='UNAPPROVED'),
    observed_facts(document_scope='UNKNOWN'),
    {**observed_facts(), 'private_label': 'PRIVATE_HOST_TEXT'},
    observed_facts(role_category='PRIVATE_ROLE'),
])
async def test_unapproved_or_malformed_observations_remain_incomplete_and_private(monkeypatch, facts):
    browser, guard, reader = fixture(monkeypatch, facts=facts)
    result = await module.investigate_closed_scope(browser, FORM)
    assert result['complete'] is False and result['closed_roots'] == 1 and reader.closed
    assert guard.calls[-1][0] == 'Runtime.releaseObject'
    assert 'PRIVATE' not in json.dumps(public(result))


@pytest.mark.parametrize('document_scope', ['TOP_LEVEL_ALLOWED', 'PERMITTED_FRAME'])
async def test_unknown_form_relationship_in_allowed_document_remains_reviewable_evidence(monkeypatch, document_scope):
    facts = observed_facts(document_scope=document_scope, form_relation='UNKNOWN',
                           same_form='UNKNOWN', visibility='UNKNOWN')
    browser, guard, reader = fixture(monkeypatch, facts=facts)
    result = await module.investigate_closed_scope(browser, FORM)
    assert result['complete'] and result['closed_roots'] == 1 and result['risks'] == [facts]
    assert facts['function'] == facts['component'] == 'UNESTABLISHED'
    assert guard.calls[-1][0] == 'Runtime.releaseObject' and reader.closed
    assert not any('authorized' in key or 'safe' in key for key in result)


@pytest.mark.parametrize('method', ['DOM.resolveNode', 'Runtime.callFunctionOn'])
async def test_document_change_after_a_read_prevents_further_stale_reads_or_release(monkeypatch, method):
    browser, guard, reader = fixture(monkeypatch)
    guard.mutate_on = method
    result = await module.investigate_closed_scope(browser, FORM)
    assert not result['complete'] and reader.closed
    assert guard.calls[-1][0] == method
    assert not any(call[0] == 'Runtime.releaseObject' for call in guard.calls)


async def test_guard_failure_prevents_channel_or_remote_access(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    guard.failed = True
    result = await module.investigate_closed_scope(browser, FORM)
    assert not result['complete'] and result['binding'] is None
    assert not reader.calls and not guard.calls


@pytest.mark.parametrize('mode', ['MISSING', None, '', 'PRIVATE_UNKNOWN_MODE'])
async def test_unrecognized_shadow_modes_are_not_traversed_or_assumed_complete(monkeypatch, mode):
    shadow = node(3, nodeType=11, children=[node(4)])
    if mode != 'MISSING':
        shadow['shadowRootType'] = mode
    root = node(1, nodeType=9, children=[node(2, shadowRoots=[shadow])])
    browser, guard, reader = fixture(monkeypatch, root=root)
    result = await module.investigate_closed_scope(browser, FORM)
    assert not result['complete'] and result['closed_roots'] == 0 and result['risks'] == []
    # Fail on the shadow descriptor, before visiting its ordinary-looking child
    # or reaching the final success read; no remote host operation is allowed.
    assert len(reader.calls) == 1 and reader.closed and not guard.calls
    assert 'PRIVATE' not in json.dumps(public(result))


async def test_ambient_mutations_remain_reviewable_without_revoking_same_scoped_binding(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    first = await module.investigate_closed_scope(browser, FORM)
    assert first['complete'] and not first['ambient_mutation_observed']
    original = guard.send
    async def change_metadata(method, params, session):
        response = await original(method, params, session)
        if method == 'Runtime.callFunctionOn':
            reader.mutation_epoch += 1
        return response
    guard.send = change_metadata
    result = await module.investigate_closed_scope(browser, FORM)
    assert result['complete'] and result['ambient_mutation_observed'] and reader.closed
    assert result['binding'] == first['binding']
    assert result['risks'][0]['function'] == result['risks'][0]['component'] == 'UNESTABLISHED'


async def test_final_document_root_identity_change_still_invalidates_evidence(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    original = guard.send
    async def replace_document_root(method, params, session):
        response = await original(method, params, session)
        if method == 'Runtime.callFunctionOn':
            reader.root['backendNodeId'] += 100
        return response
    guard.send = replace_document_root
    result = await module.investigate_closed_scope(browser, FORM)
    assert not result['complete'] and reader.closed
    assert guard.calls[-1][0] == 'Runtime.releaseObject'


async def test_private_binding_changes_for_root_identity_and_observed_risk_changes(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    first = await module.investigate_closed_scope(browser, FORM)
    reader.root['children'][0]['shadowRoots'][0]['backendNodeId'] = 33
    second = await module.investigate_closed_scope(browser, FORM)
    assert first['binding'] != second['binding'] and first['risks'] == second['risks']
    guard.observation = observed_facts(form_relation='INTERSECTS_FORM')
    third = await module.investigate_closed_scope(browser, FORM)
    assert second['binding'] != third['binding']


async def test_read_command_budget_is_bounded_without_fetching_closed_contents(monkeypatch):
    root = node(1, nodeType=9, children=[node(key, childNodeCount=1) for key in range(2, 66)])
    browser, guard, reader = fixture(monkeypatch, root=root)
    reader.expansions = {key: node(key, childNodeCount=1, children=[node(key + 100)]) for key in range(2, 66)}
    result = await module.investigate_closed_scope(browser, FORM)
    assert not result['complete'] and len(reader.calls) == 64
    assert not guard.calls and reader.closed


async def test_release_failure_is_not_reported_as_a_complete_investigation(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    guard.release_fails = True
    result = await module.investigate_closed_scope(browser, FORM)
    assert not result['complete'] and reader.closed
    assert 'PRIVATE' not in json.dumps(public(result))


async def test_investigation_deadline_closes_the_local_channel(monkeypatch):
    browser, guard, reader = fixture(monkeypatch)
    reader.block = True
    result = await asyncio.wait_for(module.investigate_closed_scope(browser, FORM), 4)
    assert not result['complete'] and reader.closed and not guard.calls

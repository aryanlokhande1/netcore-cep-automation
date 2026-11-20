from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from modules.whatsapp import page1_transition
from modules.whatsapp.contract import SETUP_URL, PAGE1_SETUP_FIELDS, V2Stop


def test_audience_route_requires_exact_setup_path_origin_and_step():
    assert page1_transition.audience_route(SETUP_URL + '#audience')
    assert not page1_transition.audience_route(SETUP_URL + '#setup')
    assert not page1_transition.audience_route(SETUP_URL + '?different=1#audience')
    assert not page1_transition.audience_route(SETUP_URL.replace('bajajfinservmarket', 'other') + '#audience')


@pytest.mark.asyncio
@pytest.mark.parametrize('fields_match', [False, True])
async def test_page1_uses_fresh_readback_then_clicks_once_and_observes_audience(monkeypatch, fields_match):
    payload, binding = object(), object()
    document = ('tab', 'session', 1, 2, 'frame', 'loader')
    readback = AsyncMock(return_value={
        'stage_visible': True, 'fields': dict.fromkeys(PAGE1_SETUP_FIELDS, fields_match),
    })
    node = AsyncMock(side_effect=lambda _, selector: document + (selector,))
    page = SimpleNamespace(page_id='setup-tab', url=AsyncMock(side_effect=[SETUP_URL + '#setup',
        SETUP_URL + '#audience', SETUP_URL + '#audience']),
        evaluate=AsyncMock(side_effect=[{'selector': '#next', 'header_pair': True}, True,
            {'active_steps': ['audience'], 'control': 'LISTS_SEGMENTS', 'validation': []}]))
    browser = SimpleNamespace(page=page, setup_page=page, setup_binding=binding,
        guard=SimpleNamespace(send=AsyncMock(return_value={'frameTree': {'frame': {
            'id': 'frame', 'loaderId': 'loader', 'url': SETUP_URL, 'urlFragment': '#audience'}}})),
        browser=SimpleNamespace(context=SimpleNamespace(active_page=AsyncMock(return_value=page))),
        trace=SimpleNamespace(agent_event=Mock()))
    tools = SimpleNamespace(browser=browser, stage='setup', operator_setup_binding=binding,
        operator_approved_payload=payload, payload=payload, protect=AsyncMock(return_value=document),
        observe=AsyncMock(return_value={'stage_visible': True}), install_guard=AsyncMock(),
        operator_setup_stop=lambda: None, operator_login_binding=SimpleNamespace(stop_code=None),
        ui=SimpleNamespace(next_selectors={'setup': '#next'}, stage_selectors={'setup': '#form'}))
    monkeypatch.setattr(page1_transition, 'observed_node', node)
    monkeypatch.setattr(page1_transition, 'deterministic_readback', readback)
    monkeypatch.setattr(page1_transition, 'protected_document', lambda _: document)
    monkeypatch.setattr(page1_transition, 'reject_security_negatives', AsyncMock())
    mark = Mock()
    if fields_match:
        result = await page1_transition.advance_setup_to_audience(tools, mark)
        readback.assert_awaited_once_with(tools, fields=PAGE1_SETUP_FIELDS)
        assert result['status'] == 'AUDIENCE_VISIBLE'
        assert result['click_attempted'] is True
        mark.assert_called_once()
        assert page.evaluate.await_count == 3
    else:
        with pytest.raises(V2Stop, match='PAGE1_ALL_FIELDS_READBACK_REQUIRED'):
            await page1_transition.advance_setup_to_audience(tools, mark)
        mark.assert_not_called()
        page.evaluate.assert_not_awaited()


@pytest.mark.parametrize(('stage', 'field', 'blocked_field'), [
    ('audience', 'audience_type', 'template'),
    ('content', 'template', 'exclude_audience'),
])
def test_campaign_step_uses_only_its_own_manual_field_schema(stage, field, blocked_field):
    from pydantic import ValidationError
    from modules.whatsapp.tools import V2BrowserTools

    tools = SimpleNamespace(stage=stage, campaign_verified_stage=stage, operator_setup_binding=object(),
                            read_only=False, authorization=SimpleNamespace(manual_form_test=True))
    schema = V2BrowserTools.action_schema(tools)
    assert schema.model_validate({'action': 'select', 'reference': 'current', 'field': field}).field == field
    with pytest.raises(ValidationError):
        schema.model_validate({'action': 'select', 'reference': 'current', 'field': blocked_field})
    with pytest.raises(ValidationError):
        schema.model_validate({'action': 'advance', 'reference': 'header'})

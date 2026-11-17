"""Small offline contract checks; no browser, credentials, .env or paid provider."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import pytest
from app.config import ConfigurationError
from modules.whatsapp.contract import SETUP_URL
from modules.whatsapp.browser_baseline import route_allowed,sanitized_action_error,BaselineStop


def test_fixed_route_accepts_only_setup_and_equivalent_default_port():
    assert route_allowed(SETUP_URL) and route_allowed(SETUP_URL+'#setup')
    assert route_allowed(SETUP_URL.replace('.com/','.com:443/'))
    for bad in (SETUP_URL+'?x=1',SETUP_URL+'#audience',SETUP_URL+'/next',SETUP_URL.replace('https:','http:'),
        SETUP_URL.replace('.com/','.com:444/'),SETUP_URL.replace('https://','https://user@'),
        SETUP_URL.replace('tprappin2r','other'),SETUP_URL+'\n'):
        assert not route_allowed(bad)
    from app.whatsapp_baseline import BaselinePolicy
    policy=BaselinePolicy({'https://tprappin2r.netcoresmartech.com','https://login.netcoresmartech.com'})
    assert policy.permits('https://login.netcoresmartech.com/')
    policy.setup_only=True
    assert policy.permits(SETUP_URL) and not policy.permits(SETUP_URL+'/next')
    assert not policy.permits('https://login.netcoresmartech.com/')


def test_actual_rpc_code_is_retained_without_exception_data_or_locator_text():
    error=RuntimeError('Timeout locating #PRIVATE_SELECTOR with PRIVATE_VALUE and PRIVATE_TOKEN')
    error.code='TIMEOUT';error.data={'private':'PRIVATE_SESSION'}
    result=sanitized_action_error(error,'CAMPAIGN_FILL')
    assert result['rpc_code']=='TIMEOUT' and result['operation']=='CAMPAIGN_FILL'
    assert 'PRIVATE_' not in json.dumps(result)
    assert sanitized_action_error(BaselineStop('LOCATOR_NOT_UNIQUE'),'LOCATE')['code']=='LOCATOR_NOT_UNIQUE'


async def test_no_live_authorization_never_launches_or_loads_private_configuration(monkeypatch):
    import app.whatsapp_baseline as module
    forbidden=AsyncMock(side_effect=AssertionError('Must not launch'))
    monkeypatch.setattr(module.StagehandClient,'start',forbidden)
    monkeypatch.setattr(module.Config,'load',lambda:pytest.fail('Must not load private .env'))
    with pytest.raises(ConfigurationError):await module.run_baseline(SimpleNamespace(authorized_live=False))
    forbidden.assert_not_awaited()

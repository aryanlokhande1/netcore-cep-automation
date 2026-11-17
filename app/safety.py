"""Trusted live grants are never model arguments; evaluation remains loopback-only."""
from urllib.parse import urlsplit
from app.config import ConfigurationError

LOGIN_URL='https://login.netcoresmartech.com/'
EXPECTED_HOST='tprappin2r.netcoresmartech.com'


def require_fixture_origin(origin):
    try:
        value=urlsplit(origin)
        valid=(value.scheme=='http' and value.hostname=='127.0.0.1' and value.port is not None
            and 1<=value.port<=65535 and value.netloc==f'127.0.0.1:{value.port}'
            and not value.path and not value.query and not value.fragment)
    except (TypeError,ValueError):
        valid=False
    if not valid:
        raise ConfigurationError('Evaluation requires an exact loopback fixture origin')


def require_fixture_browser(browser):
    if not browser.fixture or len(browser.policy.origins)!=1:
        raise PermissionError('SYNTHETIC_ONLY')
    require_fixture_origin(next(iter(browser.policy.origins)))

class LivePermit:
    """Controller capability, bound to one configuration and one browser launch."""
    def __init__(self,config,origins):
        self.config=config
        self.origins=frozenset(origins)
        self.started=False


def authorize_live(config,authorized=False,api_request_budget=0,extra_origins=()):
    if not authorized or not 1<=api_request_budget<=8:
        raise ConfigurationError('Explicit live authorization and an API request budget from 1 to 8 are required')
    if not config.live_login_enabled or config.headless or config.max_login_submissions!=1 or config.teacher_live_replay_enabled:
        raise ConfigurationError('Live configuration must enable headed single-submission login and disable Teacher live replay')
    if config.login_url!=LOGIN_URL or config.expected_host!=EXPECTED_HOST:
        raise ConfigurationError('Unexpected Netcore entry or destination configuration')
    origins={LOGIN_URL.rstrip('/'),'https://'+EXPECTED_HOST}
    for origin in extra_origins:
        parsed=urlsplit(origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None,443):
            raise ConfigurationError('Additional origins require exact reviewed HTTPS origins')
        origins.add(origin)
    return LivePermit(config,origins)


def require_live_browser(browser,starting=False):
    permit=getattr(browser,'live_permit',None)
    if browser.fixture or not isinstance(permit,LivePermit) or permit.config is not browser.config:
        raise PermissionError('LIVE_AUTHORIZATION_REQUIRED')
    if frozenset(browser.policy.origins)!=permit.origins or not browser.config.live_login_enabled or browser.config.headless:
        raise PermissionError('LIVE_POLICY_MISMATCH')
    if starting:
        if permit.started:
            raise PermissionError('LIVE_ATTEMPT_ALREADY_STARTED')
        permit.started=True
    elif not permit.started:
        raise PermissionError('LIVE_ATTEMPT_NOT_STARTED')

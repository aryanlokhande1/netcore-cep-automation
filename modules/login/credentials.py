"""Only trusted code loads credentials. Neither agent can receive this object."""
import json
import os
from urllib.parse import urlsplit
from pydantic import SecretStr
from app.config import ConfigurationError
from app.safety import require_live_browser, LOGIN_URL
from browser.live_dom import VALIDATE_MAPPING

class LocalCredentials:
    def __init__(self,account,username,password):
        if not all(isinstance(v,str) and v for v in (account,username,password)):
            raise ConfigurationError('Private Netcore credential configuration is incomplete')
        self.account,self.username,self.password=map(SecretStr,(account,username,password))
    def __repr__(self):
        return 'LocalCredentials(<redacted>)'
    @classmethod
    def load(cls):
        return cls(*(os.getenv(k,'') for k in ('NETCORE_ACCOUNT_NAME','NETCORE_USERNAME','NETCORE_PASSWORD')))

class CredentialBroker:
    def __init__(self,browser,credentials):
        self.browser,self._credentials=browser,credentials
        browser.expected_account=credentials.account

    async def submit(self,mapping):
        browser=self.browser
        require_live_browser(browser)
        current=urlsplit(await browser.page.url())
        if f'{current.scheme}://{current.netloc}'!=LOGIN_URL.rstrip('/'):
            raise PermissionError('CREDENTIAL_ORIGIN_NOT_APPROVED')
        if set(mapping)!={'account','username','password','submit'}:
            raise PermissionError('UNKNOWN_FIELD_MAPPING')
        if any(mapping[k].get('key')!=k for k in mapping):
            raise PermissionError('FIELD_ROLE_MISMATCH')
        browser.budget()
        if not await browser.page.evaluate(VALIDATE_MAPPING.replace('MAPPING',json.dumps(mapping))):
            raise PermissionError('FIELD_MAPPING_NOT_CURRENT')
        observed=await browser.observe()
        if not observed.allowed or observed.challenge or observed.rejected or observed.expired or observed.account_restricted:
            raise PermissionError('HUMAN_ACTION_OR_REJECTED_LOGIN')
        # The latch is claimed before filling; any later exception permanently consumes it.
        browser.claim_login_submission()
        browser.trace.fact('FIELD_ROLES_VALIDATED')
        for key,secret in [('account',self._credentials.account),('username',self._credentials.username),('password',self._credentials.password)]:
            browser.budget()
            await browser.page.locator(mapping[key]['selector']).fill(secret.get_secret_value())
        browser.budget()
        from time import monotonic
        browser.login_submitted_at=monotonic()
        await browser.page.locator(mapping['submit']['selector']).click()
        browser.trace.fact('SUBMITTED_ONCE')
        # Never retry this click. Poll locally until relevant readiness is stable;
        # reaching the application host alone does not complete this wait.
        await browser.wait_for_application_ready()

from app.state import Observation, Result


def verify(o: Observation) -> Result:
    if not o.allowed:
        return Result.UNEXPECTED_PAGE
    if o.rejected or o.account_restricted:
        return Result.AUTH_FAILED
    if o.expired:
        return Result.SESSION_EXPIRED
    if o.challenge or o.credential_form or o.login_entry:
        return Result.HUMAN_ACTION_REQUIRED
    if all((o.expected_host, o.dashboard, o.tenant_matches,
            o.readonly_access, o.evidence_configured)):
        return Result.AUTHENTICATED_AND_VERIFIED
    return Result.UNEXPECTED_PAGE

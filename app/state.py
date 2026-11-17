from enum import StrEnum
from typing import TypedDict
from pydantic import BaseModel, ConfigDict


class Result(StrEnum):
    AUTHENTICATED_AND_VERIFIED = "AUTHENTICATED_AND_VERIFIED"
    HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
    AUTH_FAILED = "AUTH_FAILED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    UNEXPECTED_PAGE = "UNEXPECTED_PAGE"
    NOT_TESTED = "NOT_TESTED"
    SETUP_FILLED_UNSAVED = "SETUP_FILLED_UNSAVED"


class Observation(BaseModel):
    """Only fixed categories and booleans cross the browser/privacy boundary."""
    model_config = ConfigDict(extra="forbid", strict=True)
    allowed: bool = True
    expected_host: bool = False
    login_entry: bool = False
    credential_form: bool = False
    challenge: bool = False
    rejected: bool = False
    expired: bool = False
    account_restricted: bool = False
    dashboard: bool = False
    tenant_matches: bool = False
    readonly_access: bool = False
    evidence_configured: bool = False
    standard_entry: bool = False
    alternate_entry: bool = False
    role_labels_changed: bool = False


class State(TypedDict, total=False):
    observation: dict
    verification: str
    action: str
    handoffs: int
    cancelled: bool

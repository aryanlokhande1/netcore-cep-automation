"""Fixed approval for the first SMS Preview validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from modules.whatsapp.contract import load_v2_payload


CREATE_URL = ('https://tprappin2r.netcoresmartech.com/bajajfinservmarket/admin/index.php/'
              'campaign/sms_campaign/create/sms/regular')
CAMPAIGN_NAME = '24nov_testing'
TIMEZONE = 'Asia/Kolkata'
SCHEDULE = '2026-11-25T16:00:00+05:30'
DUPLICATE_CHECK_UNAVAILABLE = frozenset({
    'CAMPAIGN_LIST_READINESS_FAILED', 'CAMPAIGN_SEARCH_CONTROL_NOT_ACTIONABLE',
    'CAMPAIGN_SEARCH_INPUT_NOT_VISIBLE', 'CAMPAIGN_SEARCH_TRIGGER_NOT_ACTIONABLE',
    'CAMPAIGN_SEARCH_RESULTS_NOT_VERIFIED', 'CAMPAIGN_NAME_AVAILABILITY_UNCONFIRMED',
    'CAMPAIGN_NAME_CHECK_REQUIRES_POPULATED_UNFILTERED_LIST', 'CAMPAIGN_SEARCH_INPUT_READBACK_FAILED',
})


class SMSPreviewPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    tenant: Literal['bajajfinservmarket']
    create_url: Literal[CREATE_URL]
    campaign_name: Literal[CAMPAIGN_NAME]
    tag: Literal['test']
    audience_type: Literal['Segments/Lists']
    include_audience: Literal['agent_test']
    channel: Literal['bajajfinservmarket_bajajfinservdir']
    sender_id: Literal['BFDLPS']
    custom_domain: Literal['http://m.bmkts.in/']
    template: Literal['LP_SEC_HLF_SMS_RTCS_SEPT_24NOV']
    template_id: Literal[16683]
    send_type: Literal['Send later']
    schedule_datetime: Literal[SCHEDULE]
    timezone: Literal[TIMEZONE]
    final_action: Literal['Preview']


def load_sms_payload(path, *, approved_digest):
    return load_v2_payload(path, approved_digest=approved_digest, model=SMSPreviewPayload)

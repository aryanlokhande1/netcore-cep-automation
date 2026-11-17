"""Composed login guidance; submission readiness is shared with standalone login."""
from deep_agents.live_tools import LiveBrowserTools, LoginCompletionGate

class HandoffLoginTools(LiveBrowserTools):
    handoff_instructions='\nComposed workflow login handoff: a normal recognized credential form has\ncredential_form=true and may be labeled HUMAN_ACTION_REQUIRED by the independent\nlogin verifier. Without a challenge, rejection, expired/restricted state or guard\nfailure this is the authorized submit_login action state, not wrong_context.\nUse its four inspected opaque references and the existing trusted broker exactly once.\nNever retry a consumed submission. An existing session can finish without submission\nonly when current independent verification establishes AUTHENTICATED_AND_VERIFIED.\nMissing independent evidence after one completed submission stays unverified and may\nbe offered to the separate human POC checkpoint. Never claim authentication yourself.\n'

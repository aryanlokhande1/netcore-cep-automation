> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Standalone login stability investigation

Run: b77f29eac26f48098f2570c676bfebd4. Offline investigation only.

## Confirmed root cause

The Doer prematurely completed with wrong_context on a normal, authorized login
form. No submit_login call reached the trusted credential broker. This is a model
decision plus a deterministic controller-admission gap: the existing normal-form
completion gate was installed only for HandoffLoginTools, not standalone
LiveBrowserTools used by app.cli live. It is CODE_FIX_REQUIRED at that boundary;
a Teacher skill correction would not repair the missing middleware.

expected_host compares against the post-login application host, not the approved
login entry host. False is normal before submission. Neither the credential
broker nor submit_login rejects the normal form merely for expected_host=false
or evidence_configured=false. The model's private reasoning was not persisted;
we cannot prove which flag caused its decision.

## Recorded sequence (zero-based original event indices)

| Event | Evidence |
| --- | --- |
| 33 | Guard allowed the top-level document request; ORIGIN_APPROVED |
| 35 | DOCUMENT_NAVIGATED, attached and observable |
| 37 | NAVIGATION_COMPLETED |
| 62?65 | inspect_page requested, started, observed, returned; LOGIN_RECOGNIZED fact references 64 |
| 64 | allowed/login_entry/credential_form true; expected_host false; all security negatives false; verifier HUMAN_ACTION_REQUIRED |
| 66?69 | verify_dashboard requested, started, observed, returned |
| 68 | Same normal pre-login observation, HUMAN_ACTION_REQUIRED |
| 70 | Doer requested structured DoerReport, blocked/wrong_context |
| 72 | Deterministic final verification; unchanged normal login form |
| 75 | Subsequent verification unchanged; no submission |

No submit_login request, broker role validation, fill or submission is recorded.
Final login_submissions=0; submission_state=NOT_ATTEMPTED; guard failed=false;
approval_required_hosts=[]; cleanup=CLOSED_AND_PROFILE_REMOVED.

wrong_context is a DoerReport reason chosen by the model, not a broker exception.
LOGIN_NAVIGATION_INCOMPLETE comes from modules/login/evidence.py:observe_live:
no security-specific result or verified success, not the authenticated expected
host, but login_entry=true. It describes remaining login progress, not a failed
navigation command. modules/login/verifier.py intentionally classifies a login
entry/credential form as HUMAN_ACTION_REQUIRED for authentication verification.
That verifier result must not prohibit the separately authorized normal submit.

## Actual path and comparison

app.cli.run -> controller.run_live -> _execute_doer -> create_doer ->
LiveBrowserTools.navigate/inspect -> browser.observe -> observe_live -> verify.
The model called verify_dashboard and DoerReport, skipping LiveBrowserTools.submit
-> CredentialBroker.submit entirely. The broker would still enforce exact login
origin, fresh mapping, security states and its pre-write single-submission latch.

Earlier successful run c416f931253644efb7062e5a168f5dfb has the identical observation
at event 69 to failed event 64, followed by submit_login requested at 71, started
at 72, returned at 135. Thus these flags alone are not evidence of a wrong page.
Another saved run 0d2ae6937f0c4efbabd0f1b2ad691382 records one submission and
expected_host=true while evidence_configured remains false: submission and
independent authentication certification are distinct.

Before repair, controller/live_tools/evidence/credentials/login_handoff and Doer
prompt matched the pre-migration rollback archive after normalizing line endings
and relocated imports. The storage migration did not introduce the admission gap.

## Missing authentication evidence configuration

Current NETCORE_EVIDENCE_FILE is unset in the process/project configuration
(only selected non-credential configuration was inspected; .env was unchanged).
EvidenceConfig therefore resolves to None. No reviewed tenant/dashboard/resource
configuration can run. Even when a configuration exists, evidence_configured is
only set by observe_live on the authenticated expected host with an expected
account, so its pre-login false value alone does not diagnose missing config.

The missing configuration is a reviewed EvidenceConfig JSON selected via
NETCORE_EVIDENCE_FILE: reviewed=true, dashboard_selector, tenant_selector,
optional dashboard_frame/tenant_frame, readonly_path, tenant_json_key and
authenticated_json_key. Values must come from reviewed actual tenant evidence;
none were invented. The expected account is supplied privately by CredentialBroker
from the existing account credential, not a new guessed tenant setting. The
reviewed read-only resource must independently establish authentication and exact
tenant match. A URL transition remains insufficient. Existing evidence review
and supervised observation remain available; this repair does not configure them.

## Why Teacher and a draft ran

TEACHER_LIVE_REPLAY_ENABLED=false prohibits live-browser evaluation replay;
it does not disable Teacher analysis of sanitized records. run_live previously
always reviewed noncancelled attempts and defaulted learning=True.
review_completed persisted the proposal; process_learning called partial_candidate
even for NO_CHANGE/evidence-gap outcomes. The saved result records ANALYZED,
evidence_gap/NO_CHANGE, no fixture evaluation, draft 9e0de6646ef5450997a4b53a517fa1db,
live_certified=false. This is explainable behavior, not evidence that the Teacher
controlled the browser or that the draft was approved. Historical artifacts remain.

## Minimal repair and contract impact

- deep_agents/live_tools.py: relocate the existing LoginCompletionGate and
  submission_pending predicate into shared live login tools. It requires observed
  four-role mapping, normal authorized login form, healthy observable guard,
  no blocked hosts or security negatives, and zero prior submissions. Failed
  guards are explicitly excluded. The genuine Doer still supplies opaque refs;
  the unchanged broker revalidates before writes. No new browser adapter.
- modules/whatsapp/login_handoff.py: inherit the shared implementation, retain
  composed workflow guidance and compatibility import of LoginCompletionGate.
- app/cli.py: add --login-only, explicitly setting review=False, learning=False,
  teacher_request_budget=0.
- deep_agents/controller.py: support that explicit no-review execution, construct
  no Teacher model, skip review and learning, persist DISABLED_LOGIN_ONLY.
  Reject contradictory no-review/learning/budget combinations before credentials.
- tests/test_standalone_login_repair.py: saved evidence, genuine Deep Agents with
  model/broker doubles, all negative predicates, CLI and controller isolation.

Browser tool names/arguments, credential handler, host authorization, verifier,
skill approval, tenant requirements and one-submit latch are unchanged.

## Executed tests

python -B -m pytest tests/test_standalone_login_repair.py tests/test_whatsapp_handoff_repair.py tests/test_pass2.py -q --tb=short -p no:cacheprovider --junitxml=outputs/login-stability-offline.xml

72 passed, 1 failed. The failure is the previously recorded frozen source-hash
assertion test_original_records_and_accepted_teacher_replays_are_unchanged;
its manifest was not rebased. All 15 new regression tests passed. app.cli live
--help also exited 0. The regression was first observed failing before code repair.
No broad suite was repeated and no historical baseline result was overwritten.

Validation level: offline genuine harness plus deterministic doubles, not a new
Chromium or Netcore success claim. Browser interaction algorithms were unchanged.
No live authentication, paid requests, promotion or new candidate occurred.
This investigation's provider usage: zero requests/tokens. The historical failed
run used Doer 4 requests, 4108 input/362 output/4470 total tokens; Teacher 1 request,
1309 input/240 output/1549 total; combined 5 requests, 5417 input/602 output/6019
total. Cached input 1077 and reasoning 413 are included subsets, not additions.

## Next supervised login-only CMD command (prepared, not executed)

```cmd
cd /d C:\Aryan\Attributics\Browser_automation\Netcore_agent
.\.venv\Scripts\python.exe -m app.cli live --authorized-live --login-only --api-request-budget 6 --hold-seconds 120 --task "Log in to Netcore Customer Engagement Platform and verify the authenticated dashboard."
```

This allows at most six Doer inference requests (existing 500 output-token cap
per request; actual spend reported at runtime), one credential submission, no
WhatsApp workflow, no Teacher inference, no learning/candidate generation and no
promotion. Existing model-availability preflight remains. Enter closes the owned
inspection hold; expiry also cleans up. Without reviewed authentication evidence,
a successful submission may still end independently unverified. That is an honest
remaining configuration blocker, not authorization to relax verification.

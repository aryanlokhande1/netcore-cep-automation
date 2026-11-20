# WhatsApp P0 execution repair — 19 November 2026

Implemented and checked offline only. No Chromium, Login, live provider, campaign
write or paid learning iteration was executed during this repair. Live acceptance
remains open until the operator records a real permitted interaction and readback.

## Confirmed causes

The cited run `db7328d0f6dd4c0bb85994ae51310770` contains 18 observed controls at
agent event 442, with `stage_visible=false`. Event 443 records
`WHATSAPP_SETUP_FORM_NOT_VERIFIED`; event 444 identifies `setup_observation` as the
failing stage. Task Doer initialization/invocation never occurred. The controller
recognition gate, rather than task-model reasoning, owns that failure. Its Teacher
artifact explicitly says `NOT_INVOKED_DIAGNOSTIC_ONLY`.

Current source also showed these independent defects:

- Manual-form CLI authorization short-circuited the c/x checkpoint.
- The manual controller invoked Doer once while tools allowed one mutation per turn.
- An eight-call middleware allowance accumulated across controller turns.
- `observe` and `dom` dispatched identically. Bound ambiguous controls could abort
  read-only observation of the entire form.
- Campaign Name invoked the legacy recognizer inside the V2 write branch.
- Human-assisted inference reserved zero Teacher requests and never routed to Teacher.
- Existing Windows candidate storage hashed LF text but wrote translated CRLF bytes.
- Teacher artifacts always recorded `invoked=false`, regardless of actual events.
- Some unrelated non-null action arguments were admitted by the tool schema.
- Discovered comboboxes supplied popup options where selection required an owned
  persistent noneditable display. Discovery and independent readback now agree;
  ambiguous or editable displays remain blocked.
- Teacher contradiction checks conflated task invocation with actual tool execution.
  Explicit invocation events now establish invocation, while tool counts establish
  browser execution. Terminal defects retain their owner and get analysis-only
  review in a branch that cannot call candidate storage.

## Repair and capability audit

`V2_BROWSER_CONTRACT` in `contract.py` now derives action enums, argument checks,
JSON argument branches and the field-kind/part matrix consumed by dispatch. All
targeted actions use opaque current references. Values/selectors/URLs are not inputs.
Known unsupported widget/action pairs are rejected before browser interaction.

| Tool/action | Arguments | Executor and prerequisites | Independent result |
| --- | --- | --- | --- |
| browser_action observe | none | Existing protected Stagehand page.snapshot, then semantic labels; no complete-form prerequisite | Structural controls, opaque references, unresolved predicates |
| dom | none | Fresh Stagehand page.evaluate with bounded structural label/placeholder discovery | Current identity-bound controls, no completion assertion |
| inspect | reference | Current visible owned node and property query | Redacted value presence, validation/error flags, enabled state |
| click | reference, optional field; reviewed image coordinates only with vision | Text/search focus or owned native/combobox opening; current identity, ownership, edit/security checks | Actual focus or opened/options evidence, never selected-state proof |
| type | reference, field | Fixed text, checkbox or supported split-date/time executor; manual mode is Setup only | Separately queried exact value/checked-state facts |
| search | reference, field | Owned popup search input or input combobox control and approved fixed search text | Search text readback; selection remains unverified |
| wait | none | Existing bounded DOM-change wait, then fresh observation | Current results/references; no guessed sleep-based success |
| options | reference, optional field | Owned native select or combobox only | Existing exact-match options and fresh references |
| select | reference, field | Existing exact native/combobox option or approved radio; no tag creation | Independent selected/checked-state readback |
| scroll | reference, optional bounded delta_y | Current target, protected CDP/Stagehand scroll | Actual position change |
| verify | reference, field | Existing independent deterministic_readback | Exact fixed value/selection predicates |
| screenshot | none | Existing separate vision authorization and reviewed region | Transient scoped image; unavailable in the manual command below |
| Next Step inspection | inspect its observed reference | No click, field assignment or persistence permission | Current control state only |
| Next Step execution | existing controller advance only | Separate persistence authorization, current stage certificate, reviewed control, security and record proof | Existing persistence journal and independent record verification |

Read-only discovery additionally retains its observed-link-only navigation action.
Manual Setup tools expose no navigation action. Unsupported fields, widget kinds,
argument combinations, stale references and uncertain writes remain gated.

Native model-backed Stagehand.observe/act is still not enabled: it is a separate
provider/metering boundary. The new observation uses the installed v4.4.0
deterministic accessibility snapshot. This distinction must remain explicit.

Automatic review rejected adding a new model-callable Next Step action because
separate authorization for that restricted operation was absent. No such action
was added; existing controller-only progression is preserved. Audience/Content/
Schedule progression on this tenant remains outside the manual test.

## Routing, evidence and learning

Login retains its existing implementation, broker and one-submission limit. The
terminal now asks `Login completed. Type c to continue or x to abort:` even for
`--manual-form-test`. The callback occurs once per session. `x` returns to owned
cleanup; `c` does not supply independent authentication evidence.

Approved navigation and protected browser/session/security verification precede
genuine create_deep_agent construction. Setup recognition now belongs to the
Doer's registered browser_action. The full V2 Doer also no longer pre-observes the
form before construction. Unknown/ambiguous controls remain observable without a
write grant. Identity, ownership, visibility, enabled-state and security checks
still gate every field edit.

The manual controller runs bounded turns in the same session. Each turn resets
the one-mutation allowance, invokes the real Deep Agent and independently reads
current values. Model VERIFY/STUCK reports cannot establish those facts. Semantic
snapshot and structural DOM discovery record distinct failures. DOM click recovery
uses the installed Stagehand send_click_event primitive rather than repeating the
same semantic click call. Both remain subject to the same authorization and target
checks. Repeated identical approaches and successful preparation do not count as
two valid failures. Verified field progress clears its failure history.

Two distinct failed approaches on one unresolved field produce deterministic STUCK.
Eligible failures in `--authorized-learning` route to the shared V2 Teacher function
after owned browser cleanup, with a separately bounded 60-second final review.
Teacher receives compiled Login evidence, actual execution counts, redacted task
actions/readbacks, failure owner/code, loaded strategy hash and evidence IDs.
Uncertain writes, unsafe context and cancellation do not authorize retries.

The genuine Teacher factory and existing WhatsApp registry are reused. Candidates
have version/hash lineage, citations and typed procedure checks. Tool/controller/
security failures cannot become executable skill corrections. Manual candidates
cannot extend into future campaign stages or unauthorized screenshots. Candidate
files are written as exact hashed bytes, never promoted or loaded automatically.
`--candidate VERSION` explicitly evaluates a stored version during a separately
launched session, with the original task digest and scope checks.

Every new live iteration requires a new CLI launch/operator decision. The full V2
command rejects `--iterations` other than 1. No automatic live retry was added.

Action events retain attempted action, outcome, field, strategy, failed predicates,
boolean expected/actual match, evidence references, duration and remaining browser,
session, model, tool-call and turn limits. Missing usage remains null. Raw payload
strings are registered with the existing trace redactor. Loaded strategy bytes and
hash are retained per attempt. Historical traces and approvals were not rewritten.

## Files touched by this repair

- app/cli.py; browser/operator_console.py; deep_agents/permissions.py (V2-specific
  tool-budget error; Login defaults unchanged).
- modules/whatsapp/workflow.py, agent.py, tools.py, contract.py, observation.py,
  observer.py, diagnostic_artifacts.py.
- learning/skill_storage/registry.py.
- tests/test_whatsapp_execution_p0.py (new), test_whatsapp_manual_form_tools.py
  (guard dependency in its offline double), test_human_assisted_artifacts.py
  (new generic failure-code expectation).
- This report, docs/operations.md, docs/known_limitations.md and the repair plan.

The checkout already contained extensive uncommitted changes. This list describes
this repair, not the entire diff against HEAD. No commit or historical rebase occurred.

## Manual launch

From VS Code PowerShell in this repository:

```powershell
.\.venv\Scripts\python.exe -m app.cli whatsapp-human-assisted `
  --authorized-live --authorized-inference --authorized-candidate-evaluation `
  --manual-form-test --authorized-learning `
  --operator-review-seconds 300 --session-seconds 900 `
  --browser-actions 300 --login-api-budget 6 --task-api-budget 12 `
  --total-api-budget 20 --max-turns 20 --doer-tool-budget 160 `
  --browser-operation-seconds 30
```

Type c or x at the checkpoint. This command permits fixed-payload Setup edits and
one eligible Teacher review/candidate generation. It grants no persistence or
campaign submission permission. It does not enter or validate the expired schedule.
Configured model/token limits may stop the attempt earlier than the session timer;
the corresponding code and consumed counters must be reviewed.

The existing pinned private payload must match its existing approved digest. No
payload values, environment settings or credentials were changed during this repair.

## Validation limits

Final focused run: **130 passed, 1 pre-existing trace-double failure** across eight
selected files. The new P0 file has **42 passing tests**. Focused offline checks exercise
genuine Deep Agents construction and registered-tool invocation, bounded controller
turns, distinct failure routing, genuine Teacher construction, candidate storage/
explicit reload, field dispatch, uncertain-write rejection, operator routing,
authentication separation and artifact metadata. They do not establish live success.
Python AST and observation JavaScript syntax checks pass. CLI parsing is checked
without invoking the workflow. A JSON Schema validation package is unavailable;
Pydantic/schema-shape checks are used without installing a dependency.

One existing focused test has an incomplete trace double:
`test_diagnostic_readback_redacts_unexpected_private_value` in
tests/test_human_assisted_tools.py omits agent_phase, which the pre-existing
control_state function reads. The resulting AttributeError is sanitized as
CODE_FIX_REQUIRED. This is not browser compatibility evidence; production Trace
provides the attribute. The unrelated double was left intact.
The same existing test passes when the required agent_phase is supplied to its
double in memory; no test file or production behavior was changed for that diagnosis.

Verification command (offline only):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_whatsapp_execution_p0.py tests/test_whatsapp_manual_form_tools.py tests/test_human_assisted_graph.py tests/test_human_assisted_operator.py tests/test_human_assisted_tools.py tests/test_human_assisted_artifacts.py tests/test_teacher_evidence_budget_repair.py tests/test_whatsapp_teacher_attribution.py -q
```

AST parsing covered 14 touched Python files. `node --check` validated the actual
V2 observation expression. Production discovery/readback JavaScript was exercised
in the existing Node DOM double, including empty, true, plaintext-only and inherited
editable states. `git diff --check` passed. Independent source review found no
remaining critical/important issue after its three findings were corrected.

Real tenant rendering, SDK snapshot/provider compatibility, dynamic dropdown
ownership and persistent selected-value evidence, security inventory availability,
real entered values, response budgets and cleanup must still be checked live.
No screenshots were captured. No live cleanup claim is made for this repair.

Current repair session runtime-provider accounting: Login Doer 0 requests, task
Doer 0, Teacher 0; input/output/total/cached/reasoning tokens unavailable/null.
Offline models are not paid inference. Historical run accounting is separate:
the cited run recorded 6 Login requests, input 8279, output 217, total 8496,
cached input 3091 and reasoning output 11 (subsets); task/Teacher usage unavailable.

## Follow-up: protected route mismatch, run 5181fe3cbb45498c85d60c23741b4f92

This follow-up was investigated and corrected using saved evidence and deterministic
checks only. No browser, Login, provider request or live retry was executed.

The former guard at `modules/whatsapp/tools.py:847-850` combined URL credentials,
any fragment, origin-policy rejection and tenant-path mismatch into the same
PROTECTED_TENANT_ROUTE_MISMATCH error. This contradicted the existing destination
contract, which explicitly accepts the Setup fragment. A deterministic test of
`SETUP_URL + '#setup'` reproduced that error before the correction.

The historical trigger cannot be identified more precisely from this archive:
neither raw SDK/CDP URLs nor component-level failure predicates were retained.
The requested URL is
`https://<approved-host>/<approved-tenant>/admin/index.php/campaign/whatsapp_campaign/create/whatsapp/regular`.
The admitted fragment forms are no fragment and `#setup`. The actual URL at failure
is **not captured**, so this report does not assert that the tenant was correct or
that this particular run contained `#setup`.

Evidence sequence, using original canonical event IDs:

| Event | Confirmed evidence |
| --- | --- |
| 438-440 | Source session safety passed, tenant route admitted, operator consent recorded; independent authentication remained false |
| 489, 527 | Protected task target alias 13 / session alias 6, generation 1; task document navigation recorded |
| 533-545 | Navigation returned and two initial protect calls passed; current origin, tenant path and absence of fragment admitted at those points |
| 549-550 | Genuine task Doer constructed and invoked |
| 625-636 | Page.getFrameTree on the same session/target; target-change notifications occurred; guard advanced past contemporaneous SDK/CDP/loader coherence |
| 637 | observe rejected before its observation body: attempted=false, valid_approach=false, owner=authorization, retry_safe=false |
| 645 | Terminal route failure retained as authorization, not a reasoning/control failure |
| 719 | BLOCKED_SECURITY_OR_SCOPE; owned browser/profile cleanup confirmed |

No dom_observation or Setup page observation returned. Target-change notifications
alone cannot prove a redirect or identify changed URL components. They also cannot
establish a wrong tenant. A stale/wrong active target was not the recorded failing
predicate: the earlier active-page/coherence checks had passed. The exact route or
fragment transition remains a live-only uncertainty.

Correction: the existing fragment classification is shared by the protected route
check. Same-tenant canonical paths and query changes do not need to equal the
initial requested URL. Contemporaneous SDK/CDP URL, frame/loader, owned target and
active-page checks remain required. Unknown fragments, wrong tenants, URL credentials
and unapproved origins remain terminal authorization failures with distinct codes.
Origin-only ReviewedUI still uses the fixed approved Setup URL's tenant for route
admission; this does not supply missing reviewed record/authentication evidence.
Permitted route/document changes invalidate control, image and discovered-binding
references and the controller progression certificate. Its existing consumer now
rechecks revocation after authorization and final awaited protections, before any
journal or click. New documents also discard old widget preparation/selection flags.
Fresh target evidence is required afterward.

New protected_task_route events retain redacted initial/previous/current route
hashes, fragment categories, changed component names, failed predicates, target and
session aliases and document generations. No raw URL, tenant name, query contents,
credentials or authentication artifacts are recorded. Route admission expressly
does not certify authentication.

Authentication remains unverified. Saved observations 389, 404 and 419 show
expected_host=true but dashboard, tenant_matches, readonly_access and
evidence_configured=false. Event 439 records evidence_configured=FAIL and the
other three positive gates NOT_CHECKED. The reviewed evidence configuration was
missing/unavailable to verification. One credential submission and c/x confirmation
do not satisfy these independent predicates. Login and its verifier were not changed.
No selectors, account indicators or authenticated endpoints were invented.

Teacher behavior is unchanged: unsafe authorization failures cannot generate or
load candidate guidance; ordinary recoverable interaction failures retain the
two-approach deterministic STUCK-to-Teacher path. Artifact classification now treats
authorization ownership as a security/scope failure even if an upstream status
label incorrectly says browser tools.

Files changed by this follow-up: `modules/whatsapp/destination.py`, `tools.py`,
`diagnostic_artifacts.py`, `tests/test_whatsapp_tenant_route_p0.py`, and this report.
Existing dirty changes, payload/environment values and historical manifests remain.

Validation: **18 new route tests pass**, covering allowed Setup/canonical transitions,
wrong tenant/origin/tab/loader, unsupported fragments, paired-read races, reference
invalidation, redaction and retained unverified authentication. The broader selected
destination suite also exposes two historical failures: its retired continue_setup
factory expectation (`test_real_controller_factory_is_reached_when_hash_changes_during_observation`)
and the old browser/navigation_guard.py preservation hash
(`test_unrelated_components_and_saved_real_evidence_are_frozen`).
The final extended focused run was **134 passed, 2 historical failures**. Neither
failure was weakened or rebased. Four changed Python files passed AST parsing,
manual-command parsing passed without workflow execution, and git diff --check
passed. Independent source review found the in-flight certificate consumer issue;
both regression cases failed before the recheck and passed afterward without any
journal/click. The source recheck found no remaining important issue in this scope.

Historical run accounting: Login 6 recorded requests, input/output/total
8352/220/8572, cached input 3129, reasoning 0; task Doer 2 recorded requests,
4730/103/4833, cached input 2193, reasoning 36. Total input/output/total is
13082/323/13405; cached and reasoning are subsets. The provider-dispatch count is 8;
the separate actual-provider-request counter is unavailable. Teacher has 0 requests
and unavailable token usage. No fields were independently verified. The recorded
workflow lasted about 38.5 seconds and confirmed CLOSED_AND_PROFILE_REMOVED.
Current repair-session live provider requests are zero; token usage and live elapsed/
cleanup are unavailable/not applicable because no live session was started.

Use the same manual PowerShell command above for the next separately authorized
run. It retains c/x and all action/request/session limits and permits no persistence.
Success in the real tenant remains unverified until that run captures actual page
observation, permitted field interaction and independent readback.

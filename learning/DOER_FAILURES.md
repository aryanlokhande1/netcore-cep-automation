# Doer failure knowledge

These are engineering summaries, not active strategies or Teacher approvals.
Original traces/results remain unchanged in `outputs/`; run IDs below resolve to
`<id>.json` plus `<id>-result.json` or `browser-baseline-<id>-result.json`.
Event numbers are zero-based trace positions. Corrections are historical code
changes, not new fixes in consolidation. Unproved hypotheses stay unproved.

## Origin gate before credential entry

Run: `9dada91260cb4accb6efd46f25c6bdce`. Component/phase: live login policy.
Classification: authorization failure, not model reasoning failure. Doer invoked:
yes (recorded actual model requests); browser showed the entry but policy did not
admit an additional origin. Requested action: observe/recognize login; expected:
approved-page observation. Actual: `UNAPPROVED_ORIGIN_APPROVAL_REQUIRED`, zero
submissions, Teacher unavailable, owned cleanup confirmed. Responsible subsystem:
origin admission. Cause: additional origin not approved; that does not justify
widening the allowlist. Correction: retain explicit origin review and fail-closed
policy. Regression: `tests/test_live_failure_regression.py`. Live validation:
that run blocked; subsequent login success is not approval of every origin.

## Submitted login without independent destination evidence

Run: `e4278bfeb61a4bec8c4adb9ba24ac104`. Component/phase: independent login
verification and completed-trace Teacher review. Classification:
authentication/session evidence gap; Teacher subsystem failure separately.
Doer invoked: yes. Browser state: operator reported authenticated Campaigns;
saved result alone does not certify it. Requested action: submit login then verify;
expected: all independent gates. Actual: one submission, eight recorded requests,
`AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED`, Teacher
`ANALYSIS_UNAVAILABLE`, cleanup confirmed. Cause: missing reviewed verification
evidence; precise historical Teacher exception was not saved. Do not attribute
it to tokens/budget/parsing without evidence. Correction: reviewed evidence
workflow and versioned trace review contracts were subsequently introduced.
Regression: `tests/test_final_readiness.py`, `tests/test_real_teacher_reliability.py`;
older Teacher tests have drift, see consolidation results. Live validation:
independent authentication remained incomplete for this run.

## Target attachment blocked final observation

Run: `e0612bc79b084dd0a411ccce8e9fed78`. Component/phase: CDP guard target attach.
Classification: browser interaction/lifecycle failure. Doer invoked: yes; trusted
login submitted once. Requested action: fresh final observation; expected: current
protected target. Actual: `NAVIGATION_GUARD_UNAVAILABLE`, recorded
`TARGET_ATTACH / TIMEOUT`; Teacher `ANALYZED`, cleanup confirmed. Responsible:
browser target/session attachment. Exact historical stale-target/race cause is
not established by the result code alone. Corrections: later lifecycle/timing
instrumentation, separate observation transport and bounded guarded recovery.
Regression: `tests/test_target_attach_repair.py`, `tests/test_metadata_channel.py`.
Live validation: later handoff succeeds, but this historical verification failure
remains unchanged. Teacher did not open the post-run fixture; controller-triggered
offline evaluation is a separate browser operation, not another live iteration.

## Global coverage gates prevented Setup Doer startup

Runs: `bd3fd9cdf01d4c8e80b5a64c08ee3dd1` and
`cfe8d6bc2ce0467d984bae3bdc97e06d`. Component/phase: Setup safety preflight.
Classification: controller/orchestration and scanner failure, not model reasoning.
Login Doer ran; Setup Doer was not invoked (zero Setup requests). Browser state:
authorized Setup destination, healthy guard, no observed security negatives.
Requested action: read/recognize Setup; expected: read-only startup.
Actual: `SAFETY_OBSERVATION_INCOMPLETE` (first run events 221-224).
First cause: uninspected shadow scope. Second: event 1274 records exhausted 512
CDP commands, partial 12-root metadata inventory against 14 accessible roots;
events 220-1273 contain one getDocument plus 511 describeNode calls. This
incomplete traversal does not prove concurrent DOM mutation. Corrections:
recursive open-root observation, batched bounded metadata and later separation
of read-only admission from strict mutation coverage. Regression:
`test_shadow_safety.py`, `test_inventory_batching.py`, `test_setup_observation.py`
and corresponding actual-browser fixtures. Historical hash assertions have known
drift. Live validation: later Setup Doer starts; this is not four-field live success.

## Conflicting destination checks

Run: `b02efa696ec3460aa889ebc96a146547`. Component/phase: post-observation Setup
route check. Classification: controller/orchestration failure. Login Doer ran;
Setup Doer not invoked. Browser state: destination opened event 217; read-only
admission event 250; event 251 rejects `UNEXPECTED_SETUP_DESTINATION`.
Requested action: observe Setup; expected: canonical approved destination.
Cause: duplicated literal URL equality at different times. The historical changed
URL was not persisted; `/regular#setup` reproduces the defect but is not proven
to be that run's exact URL. Correction: shared canonical scheme/host/port/path/
query/fragment validation with actual step identity for the legacy Setup path.
Regression: `test_setup_destination.py`, `test_real_setup_destination.py`.
Live validation: later navigation/Doer startup observed; field compatibility pending.

## Correct Setup rejected by legacy identity probe

Run: `dc93dce075d4433dbfa335bf4acdf178`. Component/phase: field-action preflight.
Classification: controller/form-recognition failure, not missing model ability.
Setup Doer invoked at event 219; three Setup requests. Browser state: accepted
route/Setup step and completed observation, but identity/form booleans false.
Requested action: `fill_text` at 296; expected: direct Campaign field interaction.
Actual: event 306 rejects `SETUP_IDENTITY`, no field changed; top-level result
reason remains `UNEXPECTED_SETUP_DESTINATION` (do not replace the original).
Cause: restricted legacy heading probe failed; actual heading content/form
implementation was not retained. Correction: independent browser baseline uses
fresh route/active-step/direct-control evidence, not the legacy heading boolean.
Regression: `test_browser_baseline.py`, `test_real_browser_baseline.py`.
Live validation: Campaign success demonstrated in the next record only; legacy
seven-tool flow is not thereby certified.

## Real Campaign success; remaining widget mapping failure

Run: `256ef8fd0c8b439bbdd086feef6afc89`. Component/phase: remaining-field mapping
before actions. Classification: browser interaction/discovery limitation.
Login Doer invoked (six requests), one submission and human C accepted; Setup
baseline is model-free. Browser state: same-session Setup, real Campaign
`agent_test` entered/read back; Add tags combobox recognized. Requested action:
F remaining fields. Expected: uniquely map WABA then dependent Business and tag.
Actual: `VISIBLE_FIELD_NOT_UNIQUELY_MAPPED`, field waba, matches 0; Business/tag
not attempted; no commit, cleanup confirmed. Responsible: discovery/executor.
Cause: no WABA candidate survives existing recognition; saved evidence cannot
identify its widget type or the precise exclusion. Correction: additional
associated-control support and scoped interactive fallback, without guessed
selectors. Regression: remaining-field/interactive tests and real local fixtures.
Live validation: Campaign PASS; WABA/Business/tag still unverified.

## Deliberate diagnostic exit is not a mutation failure

Run: `4b69212b8c734089b481be4c9df84da5`. Component/phase: baseline diagnostic
branch. Classification: expected authorization/read-only exit, not model failure.
Login Doer invoked (six requests), one submission; no Setup model. Browser state:
Setup active, traversal complete; unique Campaign input; WABA one label/custom
host but no control; Business two labels/custom host but no control; tag combobox
ownership unestablished. Requested action: diagnose-only observation; expected:
no edit authorization. Actual: `READ_ONLY_COMPLETE_NO_FIELD_EDITS`, both mutation
results NOT_RUN, verified fields empty, cleanup confirmed. Cause: deliberate
`--diagnose-only` branch before A/F. Correction: clearer C?automatic discovery,
separate A/F prompts, redacted widget reasons and supervised dropdown observation.
Regression: diagnostic authorization test and `test_interactive_widgets.py`.
Live validation: actual widget interactions remain unverified, not regressed.

## Interactive fallback limitations

Run ID: no new live run; evidence `outputs/real-local-interactive-widgets.json`.
Component: scoped widget executor. Classification: local fixture/compatibility
boundary, not external-application or model reasoning failure. No Doer, inference
or credentials. Observed/requested: open actual scoped semantic trigger, inspect
new options, select approved existing value, exact readback; alternatively record
one operator opening. Expected/actual: semantic/dependent fixtures verify all four;
non-semantic demonstration records classifications without selection. Unowned
portal and create-only tag fixtures block. Codes: `CUSTOM_TRIGGER_NOT_ESTABLISHED`
or `INTERACTIVE_EXISTING_OPTION_UNESTABLISHED`. Cause: insufficient ownership/
existing-choice proof, deliberately preserved. Local dependent insertion also
exposed shortened path ambiguity; fixed only in the new private path generator
by unique actual IDs/full root-local ancestry. Regression: actual headed
`test_interactive_widgets.py`. Live status: WABA, Business and existing tag unknown.

## Classification rules

A controller gate before invocation is not a model failure. A failed selected-state
readback is not success because search text matches. A URL transition is not
independent authentication. A model-double fixture is not live validation.
Teacher proposals remain distinct from accepted strategies and approved skills.
Original records and hash manifests are never edited to conceal a failure.

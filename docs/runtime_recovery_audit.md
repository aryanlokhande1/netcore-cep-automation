> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Runtime root-cause audit and recovery

## Recurring architectural failures

Historical IDs identify immutable evidence, not rules for interpreting future pages.

| Area | Evidence | Recurring defect and correction |
| --- | --- | --- |
| Controller admission | c871727991284bf8b73ccc014f666bae: no tool events, zero Setup/Teacher requests | Field verification ran before agent construction. Missing Campaign raised FIELD_CONTROL_UNRESOLVED; broad exception made it terminal. Removed pre-agent form prerequisite; bounded MutationObserver readiness then genuine Doer observation. |
| Overlapping page classifiers | dc93dce075d4433dbfa335bf4acdf178; b02efa696ec3460aa889ebc96a146547 | Accepted navigation was later rejected by another route/heading predicate. Read-only observation now needs authorized protected document, not complete form identity. Writes retain active-step/current-target proof. |
| Global observation versus action safety | bd3fd9cdf01d4c8e80b5a64c08ee3dd1; cfe8d6bc2ce0467d984bae3bdc97e06d | Global shadow/inventory gaps blocked agent startup. No inventory is added to this path. Scoped ownership remains mandatory for actions. |
| Browser lifecycle | e0612bc79b084dd0a411ccce8e9fed78 | Target attachment failures obscured independent verification. Guard stays fail-closed; current readiness records document state, protected-document continuity, route classification, control presence and timing. Historical timeout alone does not establish a stale target. |
| Tool semantics | 9604460832b844d393cf37f08f47be73 | Full display-label equality rejected selected numeric WABA. Existing bounded-ID correction is retained, with ambiguous/conflicting/partial IDs refused. A failure alone never justifies a click. |
| Authorization and outcome reporting | 256ef8fd0c8b439bbdd086feef6afc89; 4b69212b8c734089b481be4c9df84da5 | Campaign success, unresolved remaining controls and deliberate diagnose-only exits are distinct. Missing mapping is not model stupidity; no-op read-only is not failed mutation. |
| Model/Teacher lifecycle | c871727991284bf8b73ccc014f666bae and prior final-attempt policy | Controllers could prevent both roles from seeing evidence; final attempts skipped Teacher. Final review now occurs within finite reserved role budgets; unknown/terminal errors may receive forensic review, never automatic unsafe retry. |
| Feedback and metrics | Previous sessions | Proposal, candidate, loaded skill and approved skill were conflated. Candidates are versioned, cited, hash-loaded and unapproved; journal appends preserve evidence. Missing usage is null, not zero. |

The c871 record does NOT establish why the first observation was empty. Delayed
SPA rendering, another workflow state and incorrect tab selection are hypotheses;
new diagnostics distinguish current observations without inventing old evidence.

## Shared runtime and boundaries

`learning/browser_runtime.py` owns the common attempt scheduler, genuine Deep
Agents factories using the existing permission middleware/checkpointer, Teacher
contract execution, finite attempts/budgets, versioned working-skill loading,
append-only journal, candidate validation and structured usage. A task adapter
supplies scoped tools, fields, guidance, schemas, screenshot handling and stop type.
`modules/whatsapp/learning_loop.py` remains the WhatsApp adapter and compatibility
entry. This extracts the existing loop rather than introducing another framework.

Login still uses the existing proven Login Doer/controller and credential broker;
it is not rewritten to use the new scheduler. Future RCS/SMS adapters and shared
scheduling of login learning are not implemented/verified in this change. Browser
ownership/cleanup stays with the existing app lifecycle and runs once at the end.

Each attempt uses a fresh LangGraph thread and actual candidate bytes, recorded
by hash. Candidate templates can change bounded tool ordering only. Teacher free
text never becomes executable policy. Citations must refer to real events. Duplicate
strategies, unsafe proposals and tool-owned/code defects cannot trigger a blind retry.
Known uncertain writes or security/transport failure remain terminal even when
Teacher records a forensic lesson. Final review does not authorize another attempt.

Default maximum is three, configurable 1..5. Reserve one Teacher request per
possible reviewed attempt, including the final one. Success receives a review for
future-only optimization; an already-filled live page is never treated as an
equivalent clean optimization trial. Proposed candidates remain unapproved.

The journal appends attempt and completed-review records. Candidate metadata binds
previous hash, evidence, new hash and pending validation. Next attempts read the
saved candidate bytes. There is no automatic permanent skill promotion. The
correction vocabulary is deliberately finite; not every unforeseen tool defect
can be repaired by prompting. Unknown failures are preserved and may require code.

## Automatic handoff

New `--automatic-handoff` requires authorized Login Doer plus autonomous Setup.
After the sole login attempt, fresh browser observation must pass the existing
independent verifier and negative-state checks. Only then does a six-second guarded
settling interval precede protected same-context Setup creation. It does not equate
a screenshot, hostname or elapsed time with authentication. Missing proof stops
HUMAN_ACTION_REQUIRED; original human C fallback remains when the flag is omitted.
No actual tenant indicators/endpoints were invented. Reviewed evidence is still
required; automatic verification cannot fabricate account identity from empty logs.

## Authorized live result

One live attempt, no retries: `80078016c2734962a1795a21b8651df0`.
Trusted credential submissions: 1. Login Doer requests: 6. Automatic handoff stopped
at LOGIN_CHECKPOINT with HUMAN_ACTION_REQUIRED / INDEPENDENT_AUTHENTICATION_EVIDENCE_MISSING.
Missing gates: dashboard, tenant_matches, readonly_access, evidence_configured.
No Setup Doer, Teacher or Setup field edits. Cleanup CLOSED_AND_PROFILE_REMOVED.
Original result remains unchanged under outputs/browser-baseline-<run_id>-result.json.

| Role | API requests | Input | Output | Provider total | Cached input subset | Reasoning subset |
| --- | --- | --- | --- | --- | --- | --- |
| Login Doer | 6 | 8023 | 356 | 8379 | 3006 | 150 |
| Setup Doer | 0 | unavailable | unavailable | unavailable | unavailable | unavailable |
| Teacher | 0 | unavailable | unavailable | unavailable | unavailable | unavailable |

Session provider total: 8379 tokens. Cached/reasoning subsets are not added again.
No cost estimate is asserted. The early-stop record has no Setup iteration usage.
New results additionally split login/setup/Teacher roles; per-iteration snapshots
and deltas preserve null when usage is unavailable, plus remaining requests,
reserved tokens, elapsed time and browser actions. Existing live evidence was not
rewritten to retrofit fields introduced afterward.

## Focused validation

9 focused tests passed (actual local Chromium plus unit contracts), including:
empty authorized page reaches real Doer observe_page and final Teacher; genuine
tool-order failure reaches Teacher; changed candidate bytes reach next Doer;
maximum attempts/final reviews; independent four-field readback; exact prefixed
WABA matching; same context/original tab; no login resubmission; uncertain selection
retry refusal; prohibited Save blocked; cleanup confirmed. XML:
outputs/runtime-recovery-focused.xml. No broad fixture campaign was rerun.

Screenshots are captured transiently and completely redacted before local storage;
they supply no visual diagnostic proof. Sanitized structural evidence remains the
usable input; capture/transport unavailability stays explicit. No image is sent to
a remote vision model.

## Verified command and finite inference scope

The command executed once (not permission to retry) was:

```cmd
cd /d C:\Aryan\Attributics\Browser_automation\Netcore_agent
.\.venv\Scripts\python.exe -m app.whatsapp_baseline --authorized-live --login-mode doer --login-api-budget 6 --autonomous-setup --automatic-handoff --iterations 3 --setup-api-budget 12 --teacher-api-budget 3 --chromium "C:\Users\Aryan\AppData\Local\ms-playwright\chromium-1248\chrome-win64\chrome.exe" --login-seconds 180 --hold-seconds 1
```

At default model caps: up to 21 inference requests across the whole session
(6 login, 12 Setup, 3 Teacher), and 15000 output tokens (500 per Doer, 2000 per
Teacher), subject to lower shared configured token caps. The model-availability
lookup is separate non-inference. Configured Teacher/request caps may require
operator review; private .env was not changed. The exact actual spend above was
only six requests, because independent authentication failed. No second live run
was made after source/report refinements. Human fallback omits automatic-handoff
and still needs informed C before edits.

## Remaining blockers

Reliable reviewed tenant/authenticated-resource evidence is missing. Consequently
unattended live Setup, remaining field compatibility, real iterative improvement,
equivalent-state optimization, permanent skill promotion and generalized future
channel adapters remain unverified. Full-suite pre-existing failures remain
separate engineering debt. Local recovery success is not live Netcore success.

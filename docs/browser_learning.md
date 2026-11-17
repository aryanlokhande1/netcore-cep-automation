> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Autonomous browser-first Setup learning

Implemented as an opt-in mode of `app.whatsapp_baseline`; existing manual/A/F
baseline remains available. No Netcore or paid inference was used in development.
This is locally verified integration, not live four-field certification.

## Flow and changed files

- `app/whatsapp_baseline.py`: opt-in autonomous arguments, informed C scope notice,
  same-browser handoff, finite shared role budgets, loop invocation and one final
  cleanup. Original Login Doer, checkpoint function and Campaign fill implementation
  remain unchanged. Autonomous mode removes R/A/F decisions after the informed C.
- `modules/whatsapp/learning_loop.py`: genuine `create_deep_agent` Doer and Teacher,
  existing LangGraph checkpointer, harness restrictions and permission middleware;
  scoped tools adapting the existing deterministic executor. Typed structured
  Teacher corrections, failure trace, working-skill loading and maximum-three loop.
- `modules/whatsapp/interactive_widgets.py`: numeric token-boundary WABA matching,
  action facts and uncertain-write latch; one independently justified dependency
  initialization selection. No arbitrary selectors/navigation/scripts are exposed.
- `tests/test_browser_learning_loop.py`: actual headed protected Chromium and local
  model doubles exercising failure, Teacher, revised skill, recovery and caps.

One C after login explicitly authorizes the fixed Campaign name, WABA, Business
number and existing tag, unsaved only. Same original browser/context is retained;
no iteration performs login or resets the session. Every iteration re-observes and
verifies current fields. The Doer selects inspect/scroll/open/options/fill/select/
verify tools; deterministic code executes and verifies them. The first failed tool
ends action execution for that attempt. Security, target identity, prohibited action,
transport/budget failures or uncertain writes stop the entire loop. Only explicitly
classified no-write failures may receive another attempt. No blind action retry.

The Teacher has no browser or execution tools. Its structured schema records owner,
observations, hypothesis, cited event indices, bounded correction, rationale, risk,
confidence, code-fix flag, safe-next-iteration flag and expected evidence. Citations
must include an actual failed event. Tool-owned failures or CODE_FIX_REQUIRED stop.
Unchanged/repeated strategies and unsafe proposals are rejected. Free-form Teacher
prose is retained as a proposal; it is never executed or injected as policy.

Each attempt writes/reads a session-only `iteration-N/SKILL.md`, hashes its actual
bytes, and supplies those bytes to a fresh Doer's system prompt. The next attempt
uses an application-owned guidance template selected by the validated Teacher enum.
The trace records the loaded hash and strategy diff/evidence. This is explicit
controller loading, not promotion into the approved login skill registry or native
approved-skill catalog. Existing approved skills are immutable. Successful working
candidates remain unapproved for separately reviewed promotion.

## WABA finding

Run `9604460832b844d393cf37f08f47be73` records real Campaign success and a WABA
observation with one option, one selected option, zero approved options and
`exact_selected=false`. Screenshot evidence supplied by the operator describes a
business-name prefix plus approved ID. Raw label/option values were not persisted,
so the exact live label cannot be reconstructed from the trace alone.

Code inspection confirms both numeric matching and existing-option recognition
used full-label equality. The headed fixture reproduces the exact recorded count
pattern with a prefixed selected label under the former matcher. Corrected matching
accepts exactly one bounded numeric token inside an owned option, rejecting longer,
partial, alphanumeric-embedded, repeated or competing IDs. It still requires actual
selected-state and concordant control display. A verified existing selection is
preserved without clicking. Business is rediscovered afterward, then the existing
exact lowercase tag. No text search/create operation is added.

Optional `select_existing_option(initialize_dependency=true)` is allowed only for
already verified WABA with a uniquely scoped actual disabled dependent control,
no verified Business value, and one uniquely observed existing approved option.
It consumes the same one-selection latch. A prior failure alone cannot justify it;
missing/unknown controls cannot. Its readback still verifies WABA, and the Doer must
rediscover and verify the dependent Business state. This does not prove the actual
Netcore tenant requires such an event.

## Failure evidence and screenshots

`outputs/setup-loop-<run_id>/result.json` contains iteration/run ID, current field,
last successful/attempted tool, argument classification, result/code, route/step
classifications, verified fields, sanitized widget/option relationships, actual
open/selection facts, failed predicate, health, request usage, retry safety,
Teacher proposal, strategy diff and loaded version hash. Provider metering remains
with existing role/token controls. Browser transport failures may prevent new
observation/capture; those remain explicit uncertainty.

On failure, a screenshot is captured transiently and **every pixel is replaced**
before writing an ignored local PNG. The raw frame is never saved or sent remotely.
This intentionally contains no visual diagnostic information; structural evidence
is the review input. Capture failure yields SCREENSHOT_SKIPPED_PRIVACY_OR_TRANSPORT.
No partially redacted screenshot is claimed safe, no remote vision call is used,
and screenshot absence is not invented evidence.

## Validation

Actual installed headed Chromium, Stagehand, protected new tab and genuine Deep
Agents with local model doubles: first attempt produces a real prerequisite tool
failure; Teacher sees the trace and cites it; iteration two loads changed guidance
(the model receives the new prompt) and all four fields independently verify.
Separate case caps at three failed attempts/two Teacher reviews. Same context,
original tab and credential-submission count retained; cleanup confirmed once at
end. Prefixed selected WABA verifies without a click; bad token boundaries fail.
Observed disabled Business permits one initialization event; repeat is refused.
Save is blocked. Existing remaining-field fixtures also pass, including unowned
portal/create-only refusal. See `outputs/browser-learning-final.xml` and
`outputs/browser-learning-headed.xml`. Existing full-suite historical failures
from consolidation were not rebaselined or claimed fixed.

## Separately authorized command

Arguments were verified with CLI help. NOT executed against Netcore:

```cmd
cd /d C:\Aryan\Attributics\Browser_automation\Netcore_agent
.\.venv\Scripts\python.exe -m app.whatsapp_baseline --authorized-live --login-mode doer --login-api-budget 6 --autonomous-setup --iterations 3 --setup-api-budget 12 --teacher-api-budget 2 --chromium "C:\Users\Aryan\AppData\Local\ms-playwright\chromium-1248\chrome-win64\chrome.exe" --login-seconds 180 --hold-seconds 120
```

With default settings: at most 6 login + 12 Setup Doer + 2 Teacher inference
requests across the entire session, not per iteration. Default output caps are
500/Doer and 2000/Teacher: at most 13,000 output tokens including login (10,000
Setup/Teacher). Shared configured token caps may stop earlier. Configured model
limits are printed; changed private output settings change this bound. Login's
availability lookup is a separate non-inference request. C is the one informed
post-login edit approval. Enter cancels. No A/F/R follows in autonomous mode.
The final healthy-session inspection hold accepts Enter; it grants no new action.
Manual-login fallback uses `--login-mode manual` without `--login-api-budget`, but
Setup/Teacher inference still requires separate authorization for these budgets.

Actual WABA/Business/tag tenant behavior, full live autonomous success and learning
effectiveness remain unverified. Fully redacted screenshots are not visual proof.
The correction vocabulary is deliberately small; deterministic incompatibilities
require code review, not invented Teacher selectors. There is no automatic permanent
promotion and no Save/Next/Publish/Schedule/Send capability.

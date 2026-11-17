> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Live authentication and Setup execution

Run `2542bcb3ce604a4b8c63a999a7ac7050`, 13 November 2026. Exactly one live
session and one credential submission. No authentication retry or second live run.
Original results, loop journal, working skill and Teacher candidate remain unchanged.

## Authentication evidence

Existing passive discovery was enabled before login navigation, then sampled the
post-login DOM three times after six seconds of protected stabilization. Transport
was available. It found one dashboard candidate classified UNSCOPED_HEADING, no
tenant candidates and no navigation candidates. Two observed GET JSON resources
were captured in the current document; neither contained the supported explicit
true authentication indicator, and neither supplied supported exact tenant evidence.
There were no unavailable permitted frames; SHADOW_ROOTS_UNINSPECTED limits the
DOM observation. No network requests were replayed. A candidate is not verification.
The private structural inventory stays under credentials; selectors, resource paths,
account values and response bodies are not reproduced here.

Independent verifier: UNEXPECTED_PAGE; missing dashboard, tenant_matches,
readonly_access and evidence_configured. No evidence draft was justified/written.
The existing operator C checkpoint was accepted as HUMAN_CONFIRMED_FOR_POC and
OPERATOR_CONFIRMED_ONLY, not independent authentication. No challenge/security
stop was recorded. There were no R/A/F approvals. Same context retained.

## Actual model/tool execution

The genuine Setup Doer made eight provider requests. Recorded tool events:

| Event | Caller | Tool | Result |
| --- | --- | --- | --- |
| 0 | Controller | observe_page | returned |
| 1 | Doer | observe_page | returned |
| 2 | Doer | inspect_field(campaign_name) | returned observed Campaign input |
| 3 | Doer | inspect_field(waba) | returned scoped widget facts, no rendered options |
| 4 | Doer | inspect_field(business_number) | returned scoped widget facts, no rendered options |
| 5 | Doer | inspect_field(tags) | returned scoped widget facts, no rendered options |
| 6 | Doer | scroll_to_field(campaign_name) | INTERACTIVE_FIELD_OUT_OF_SCOPE |

The controller correctly preserved the terminal stop and never blindly retried.
No fill or selection operation occurred. All four fields remain NOT VERIFIED in
this run: Campaign was inspected but not filled; WABA/Business/tag were inspected
but not selected. Earlier Campaign success is historical and is not this run's
outcome. No Save/Next/Audience/Schedule/Publish/Send occurred.

Confirmed defect: the shared tool schema permits campaign_name for scroll, but
BrowserTools.invoke dispatched every scroll to InteractiveWidgets.scroll, whose
inspector supports only WABA, Business and tags. This is an adapter code defect,
not missing authorization or insufficient model reasoning.

Teacher ran once and cited events 2?6, but assigned unknown owner and proposed
INSPECT_BEFORE_SELECT rather than CODE_FIX_REQUIRED. That hypothesis did not
identify the deterministic dispatch defect. Candidate-1/SKILL.md was generated,
hash 246911034e78759962c961ca77bd8975315e9b9da0ba47f04b545d246ea25bd8,
but NOT loaded: no iteration two occurred. It remains PENDING_NEXT_ATTEMPT and
unapproved, not a validated lesson. The live record was not edited to improve it.

## Narrow repairs after the live stop

- `modules/whatsapp/learning_loop.py`: Campaign scroll uses its observed Campaign
  reference, existing `_target` identity checks and fixed scroll operation, then
  rechecks identity. Campaign inspection filters the actual Campaign descriptors
  rather than returning whichever control is first. Existing fill code is unchanged.
- `learning/browser_runtime.py`: known INTERACTIVE_FIELD_OUT_OF_SCOPE is classified
  CODE_FIX_REQUIRED; terminal failures cannot generate executable working guidance
  even if Teacher proposes a read-only strategy. First-use Teacher usage deltas use
  zero only when the prior request count actually was zero; unavailable usage stays null.
- `app/whatsapp_baseline.py`: optional passive authentication discovery/evidence path
  and six-second stabilization; existing credential broker, verifier and C fallback.
  Private config is loaded once. No automatic evidence approval or origin widening.
- `tests/test_browser_learning_loop.py`: real local Deep Agents sequence scrolls the
  Campaign control before filling; retains Teacher/reload/cap/cleanup regressions.

Focused handoff/baseline tests: 7 passed before live. Post-repair focused actual
headed Chromium tests: 2 passed in 9.40 seconds, using loopback fixtures and local
model doubles only. No broad fixture suite or further live login was run.

## Actual usage

| Role | Requests | Input | Output | Total | Cached input subset | Reasoning subset |
| --- | --- | --- | --- | --- | --- | --- |
| Login Doer | 6 | 7989 | 314 | 8303 | 2993 | 110 |
| Setup Doer, iteration 1 | 8 | 9617 | 405 | 10022 | 4317 | 250 |
| Teacher, iteration 1 | 1 | 1753 | 240 | 1993 | 0 | 0 |
| Session | 15 | 19359 | 959 | 20318 | 7310 | 360 |

Cached/reasoning columns are already included in provider totals. No cost estimate
is asserted. Loop elapsed 23.891 seconds; complete session trace elapsed 81.968
seconds including checkpoint/inspection/cleanup. Browser actions at loop end: 98.
Six Setup/Teacher request slots remained; finite budgets were not the stop reason.
Cleanup CLOSED_AND_PROFILE_REMOVED: process exit, profile removal and transport
closure all confirmed, no cleanup warning. No field was independently verified.

## Command actually used

The visible terminal ran this command once. It is recorded for reproducibility,
not authorization for another login:

```cmd
cd /d C:\Aryan\Attributics\Browser_automation\Netcore_agent
set "TEACHER_REQUEST_BUDGET=3"
.\.venv\Scripts\python.exe -m app.whatsapp_baseline --authorized-live --login-mode doer --login-api-budget 6 --discover-authentication --auth-evidence-file credentials\netcore-supervised-evidence.json --autonomous-setup --iterations 3 --setup-api-budget 12 --teacher-api-budget 3 --chromium "C:\Users\Aryan\AppData\Local\ms-playwright\chromium-1248\chrome-win64\chrome.exe" --login-seconds 180 --hold-seconds 120
```

The process-only Teacher cap matches the authorized budget; private .env was not
modified. Maximum budgets were 6/12/3 requests; actual spend is reported above,
not replaced by configured limits.

## Remaining blockers

Independent tenant/authentication evidence still requires actual supported reviewed
indicators. Discovery's shadow-root limitation is not proof that the real account
indicator is absent. The confirmed Campaign scroll defect is locally repaired but
not live-retested. Actual WABA, dependent Business and existing tag operations,
next-iteration candidate effectiveness and successful four-field live readback are
still unverified. Fully redacted images are privacy artifacts, not visual proof.
The live execution acceptance criterion was NOT achieved; no fixture result is
represented as live success.

Sources: outputs/browser-baseline-2542bcb3ce604a4b8c63a999a7ac7050-result.json,
outputs/setup-loop-2542bcb3ce604a4b8c63a999a7ac7050/result.json and journal.jsonl,
outputs/2542bcb3ce604a4b8c63a999a7ac7050-summary.json and trace; local regression XML
outputs/live-campaign-scroll-repair.xml. Original evidence remains unchanged.

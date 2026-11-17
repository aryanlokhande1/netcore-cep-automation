> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# WhatsApp Setup execution boundary repair

Local protected-browser execution is demonstrated. Real Netcore field filling is
not yet verified. No real login, credentials, Netcore request or paid inference was
used during this repair.

## Confirmed old startup barrier

Saved run `0d2ae6937f0c4efbabd0f1b2ad691382`:

| Event | Elapsed seconds | Evidence |
| --- | ---: | --- |
| 179 | 39.890 | Human POC checkpoint accepted |
| 214 | 40.328 | Same-context Setup tab protected |
| 215 | 40.343 | Authorized Setup navigation started |
| 255 | 47.484 | Global safety incomplete; read-only observation allowed |
| 256 | 47.515 | Destination rejected: `SETUP_IDENTITY` |
| 257 | 47.515 | `UNEXPECTED_SETUP_DESTINATION` |

The old call path was `continue_setup -> tools.open -> scan -> observe_setup ->
destination_check(data) -> validate_setup_destination`. The validator required
`page_identity`, `setup_active` and `observation_complete` before
`create_setup_doer` was reached. The precise false flag was not saved. This run
does not establish a URL/hash failure. Login requests were six, Setup requests
zero, credential submissions one, and owned cleanup was confirmed.

## Corrected execution path

`continue_setup -> open protected tab -> navigate once -> protected_document +
authorized origin + unchanged handoff -> create genuine Setup Doer ->
observe_page -> bounded page understanding -> separate action authorization ->
fixed field operation -> actual value readback -> controller verification ->
unsaved inspection -> owned cleanup`.

The single page classifier distinguishes:

- **ALLOWED_ORIGIN:** healthy guard, admitted same-context target and exact
  authorized scheme, host and effective port. Paths, queries, unknown steps and
  missing form recognition can be inspected without writes.
- **SETUP_CONFIRMED:** approved pathname/query, only the empty or literal `setup`
  fragment, fresh WhatsApp/Setup identity, recognized form and complete bounded
  form observation. Campaign-only mapping is sufficient to begin its field path.
- **ACTION_AUTHORIZED:** separate explicit four-field permission, current unique
  control/document evidence, strict destination/form identity, negative-state
  checks and either complete safety evidence or the existing actual scoped
  operator review. Origin alone grants no field action.

Read-only tools reuse the existing bounded security-negative scanner without
requesting CDP inventory. They explicitly report incomplete global coverage,
including `inventory_requested=false`. All detected security negatives and invalid
scans still stop. Full inventory remains bounded and unchanged at the write
boundary; relevant unreviewed risks cannot authorize writes.

Unknown identity triggers at most three observations with two event-aware waits,
each capped at 500 ms. Form/heading/control/hash changes wake the observer;
unrelated ambient spans do not consume its readiness attempts. A still-loading
page is returned as read-only uncertainty, not a false Setup certificate.

## Files changed

| Runtime file | Purpose |
| --- | --- |
| `modules/whatsapp/workflow.py` | Start the Doer after read admission; start passive persistence observation concurrently with a two-second bound; report handoff and exact stop predicate. |
| `modules/whatsapp/tools.py` | Separate origin admission, read-only page understanding and the existing mutation boundary; refresh safety/identity before writes; report sanitized classifications and authorization result. |
| `modules/whatsapp/destination.py` | Shared origin/route/DOM classification, preserving strict route checks for navigation and actions. |
| `modules/whatsapp/observation.py` | Fixed active-step classification and bounded read-only rendering waits. |
| `modules/whatsapp/safety.py` | Optional inventory-free read path using the unchanged negative scanner; never certify omitted coverage. |
| `modules/whatsapp/session.py` | Print `SETUP_TAB_PROTECTED` immediately after existing protection proof. |

Acceptance code: new `evals/local_setup_execution.py` and
`tests/test_real_setup_execution.py`; existing `evals/local_setup_destination.py`
updated to assert readable wrong pages still reject writes. Existing tests migrated
in `test_setup_agent_refactor.py`, `test_setup_destination.py`,
`test_whatsapp_phase1.py`, and `test_whatsapp_preflight_repair.py`.

Navigation guard, target creation, login, credential broker, inventory algorithm,
authorization/review implementation, seven-tool interface, fixed values, model
budgets, Teacher core/adapter and no-save/no-send guard remain unchanged. Twenty-one
source/artifact hashes are checked in `outputs/setup-execution-preservation.json`;
original live artifacts were not rewritten.

## Actual headed Chromium evidence

Installed Chromium ran with Stagehand, the real navigation guard, genuine Deep
Agents and local model doubles. The login handoff is explicitly a fixture-owned
one-submit simulation with no credentials or login model. Operator C/A decisions
are local fixture callbacks, not a live operator approval.

`outputs/real-local-setup-execution.json`: **eight cases passed in 30.813 seconds**.
The valid case follows the actual `continue_setup` controller, not a manually
invoked series of browser tools:

- The genuine Doer starts at local event 86; its first actual observer read sees
  no Setup identity, form or Campaign control. `SETUP_PAGE_OBSERVED` is event 98.
- Ambient DOM changes occur while the form is absent. Rendering starts 650 ms
  after that first real read; `/regular` becomes `/regular#setup`.
- A later actual read recognizes the form; `SETUP_FORM_RECOGNIZED` is event 113.
- Incomplete global coverage and a genuine closed root require one explicit
  scoped fixture review. `SCOPED_AUTHORIZATION_RESULT` is event 274.
- Eight local model requests drive observe, inspect Campaign, fill Campaign,
  verify WABA, select Business number, open tags, select the existing tag and
  return the unsaved report. Each field is independently recorded in order:
  `CAMPAIGN_NAME_VERIFIED`, `WABA_VERIFIED`, `BUSINESS_NUMBER_VERIFIED`,
  `EXISTING_TAG_VERIFIED` (printed as `TAG_VERIFIED`).
- All four actual values match; controller verification passes before and after
  the inspection callback. Original tab, process and context remain present.
  No commit click, form submission or fixture POST occurs.
- Wrong path, query and Audience step are readable but reject writes with
  `PATH`, `QUERY` and `SETUP_IDENTITY` diagnostics. Declined review, challenge,
  prohibited Save tool and wrong-origin guard rejection also pass their checks.
- Every session reports `CLOSED_AND_PROFILE_REMOVED`.

The updated destination acceptance adds **six passing headed cases** in 7.250
seconds, including unexpected fragments. Combined pytest result: **2 passed in
39.46 seconds**. Focused offline regressions: **284 passed in 10.74 seconds**,
including all five security negatives, stale controls, changed review risks,
single-submit/cleanup, accepted Teacher replays and Campaign-only mapping.
See `outputs/real-local-setup-execution.xml` and `outputs/setup-execution-offline.xml`.

## Remaining live uncertainties and one separately authorized test

Actual Netcore widget compatibility and the historical false identity flag remain
unverified. Slow rendering beyond the bounded observation window can remain
unknown; the Doer receives that uncertainty and cannot write. A failed full
inventory or incomplete relevant risk review can still block mutations. No
authentication certificate, skill promotion, campaign creation or absence of
automatic persistence is claimed.

Only after separate authorization for real login and paid inference, run once in
an interactive CMD terminal:

```cmd
cd /d C:\Aryan\Attributics\Browser_automation\Netcore_agent
.\.venv\Scripts\python.exe -m app.cli whatsapp-setup --authorized-live --authorized-setup --login-api-budget 6 --setup-api-budget 8 --teacher-request-budget 2 --total-api-budget 16 --checkpoint-seconds 90 --setup-seconds 120 --hold-seconds 120
```

Inspect the existing authenticated account context before C. If scoped review is
required, A authorizes only the four fixed fields after reviewing the stated
uncertainty; Enter declines. Stop unsaved. Do not retry credentials or approve a
challenge. The command was prepared, not executed.

> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Comprehensive browser tool contract audit and repair

Date: 13 November 2026. Offline implementation and local fixture validation only.
No tenant execution, credentials, audience changes or paid inference. Fixed values,
login/context handling, guard protections, Campaign fill implementation, approved
skills, strategy.md, historical manifests and execution records are preserved.

## Confirmed roots and corrections

1. Shared FieldArgs admitted all four fields for partial-domain operations. Fill,
   dropdown opening, option inspection and selection had different implementations.
   CAPABILITIES now defines authority once in existing contract.py. Generated
   strict per-action schemas and pre-dispatch validation reject unsupported fields,
   extra arguments, coercible booleans and invalid tool names before browser access.
2. Unsupported Campaign dropdowns reached the remaining-widget inspector.
   Dropdown/options/selection are now limited to remaining fields, while Campaign
   inspect/scroll/fill/verify retain their established explicit routes. No selector
   was guessed. Internal search capability cannot be invoked through browser-first
   public tools.
3. initialize_dependency was accepted for all remaining fields and checked after
   open. Only WABA may set it true; current selected WABA plus unique disabled,
   unverified Business evidence must be established before any dropdown action.
   Review found that selected/collapsed WABA returned early without opening options;
   a failing regression reproduced this, then the already-validated initialization
   path was allowed to open the selected widget. Normal matching selections remain
   untouched; selection and uncertain-readback latches still prevent retries.
4. verify_field and post-action paths could report ok=true with exact_verified=false.
   Failed readback now reports explicit failure. Pending verification is a read-only
   observation, not a global action blocker; a first authorized fill can follow it.
   Post-write uncertainty remains terminal. Missing Campaign discovery also clears
   stale verified-field state.
5. Dropdown open returned success without evidence of owned expansion. Custom
   triggers now require expansion, or a documented native/already-selected no-op;
   otherwise DROPDOWN_OPEN_NOT_VERIFIED. The unowned-portal fixture now expects this
   earlier exact refusal, still asserting zero unintended selection.
6. Field-label aliases differed across scanners. FIELD_ALIASES is embedded from one
   immutable contract mapping into the baseline, interactive and legacy observation
   scripts. Model arguments remain exact canonical field keys; aliases are label
   classifications only. Ownership and numeric-ID safety policies were not relaxed.
7. Opaque-reference tools silently accepted surplus arguments through default schemas.
   REFERENCE_TOOLS now derives strict reference schemas; field authority is checked
   after current reference resolution against the same canonical registry. Existing
   role, kind, form, authorization and security gates remain stricter prerequisites.
   Search fill gets exact input readback; it never counts as tag selection.
8. A validated action lacking dispatch is now CAPABILITY_DISPATCH_MISSING, classified
   CODE_FIX_REQUIRED by the evaluator. Invalid model arguments remain distinct.
   Teacher cannot turn deterministic defects or terminal errors into executable
   strategy candidates. Original failure records were not changed or promoted.

## Authoritative inventory

Machine-readable inventory with actual generated JSON Schemas:
[browser_contract_inventory.json](browser_contract_inventory.json).

| Browser-first tool | Fields | Postcondition/error contract |
| --- | --- | --- |
| observe_page | none | protected current-page observation; incomplete form permitted |
| inspect_field | all four | fresh scoped control facts or unresolved classification |
| scroll_to_field | all four | current observed identity retained after scroll |
| open_dropdown | WABA, Business, tags | owned expansion or native/selected no-op; otherwise explicit failure |
| inspect_options | WABA, Business, tags | observed owned option facts/counts; never selection proof |
| select_existing_option | WABA, Business, tags | exact independent selected-state readback; no uncertain retry |
| fill_approved_field | Campaign only | unchanged existing Campaign fill plus exact input/readback |
| verify_field | all four | exact state or FIELD_NOT_VERIFIED pending observation |
| capture_failure | none | redacted local image or explicit privacy/transport skip |

Nine public tools, 24 field/action combinations including two no-field utilities.
The internal search_existing_option capability covers remaining fields through the
legacy observed-search reference only. It is not a tenth public browser-first tool.
No action accepts caller-supplied values, URLs, selectors, scripts or commit commands.

Legacy seven-tool aliases:

| Tool | Canonical authority after current reference resolution |
| --- | --- |
| observe_page | observe_page |
| inspect_control | inspect_field (control/search/option roles) |
| scroll_to_control | scroll_to_field (control role) |
| click_observed_control | open_dropdown, observed owned-combobox kind only |
| fill_text | fill_approved_field for Campaign control; search_existing_option for observed dropdown search |
| select_observed_option | select_existing_option with same-field observed option |
| verify_field | verify_field plus existing strict form/ownership predicates |

Opaque schemas cannot encode a field that is known only after resolution. They
encode strict nonempty bounded references and reject extra arguments; the resolved
field is then checked against the canonical contract before execution. Dynamic
kind/visibility/ownership prerequisites do not grant another field or capability.

Login tools were inventoried but not folded into the WhatsApp field registry:
live navigate_approved/inspect_page/submit_login/verify_dashboard; synthetic uses
interact_observed instead of submit_login. Optional immutable read_approved_skill
is unchanged. These retain their trusted authentication boundary. Teacher has no
browser-action tools. No RCS/SMS capability or audience operation is added.

## End-to-end path

Real Deep Agents -> generated schema -> BrowserTools.invoke validation -> existing
explicit dispatcher -> identity-pinned Stagehand locator/evaluate operation ->
independent readback -> shared scheduler verification -> Teacher/proposal validation.
The seven-tool path uses strict reference schemas, resolves observed identities,
then validates canonical authority before its existing executor and readback.
The evaluator cannot substitute a model report for all four actual verified facts.
Read-only admission continues without form recognition or global CDP certification;
origin/tab/negative authentication checks are unchanged.

## Files changed

Runtime: modules/whatsapp/contract.py, learning_loop.py, interactive_widgets.py,
browser_baseline_dom.py, observation.py, tools.py; learning/browser_runtime.py.
Tests: new tests/test_capability_contract.py; existing test_setup_observation.py
adds alias cases, test_whatsapp_phase1.py extends its search fixture with actual
input readback, test_interactive_widgets.py pins the earlier failure predicate,
and test_browser_learning_loop.py adds selected/collapsed initialization behavior.
Documentation: this report, generated inventory and the architecture registry link.
No public module entry point, browser ownership or existing Campaign-fill source
was replaced. Campaign function hash still matches its original preservation record.

## Tests and actual validation levels

Test-first evidence: registry tests initially failed because CAPABILITIES did not
exist. The review-discovered selected/collapsed initialization case failed with
INTERACTIVE_EXISTING_OPTION_UNESTABLISHED before correction, then passed.
Intermediate alias-script syntax and fixture readback gaps were found and corrected,
not hidden; legacy observation/ownership/security tests subsequently passed.

Executed focused command:

```cmd
.\.venv\Scripts\python.exe -B -m pytest tests/test_capability_contract.py tests/test_setup_observation.py tests/test_whatsapp_phase1.py tests/test_setup_agent_refactor.py tests/test_browser_baseline.py tests/test_baseline_login_handoff.py -q --tb=short -p no:cacheprovider --junitxml=outputs/capability-focused-final.xml
```

Its recorded run passed 282 tests. Subsequent boundary cases were added and the
final matrix-only command passed 149 tests (outputs/capability-matrix-final.xml).
Matrix coverage includes every advertised browser-first combination, strict flags,
no-field/unknown/extra arguments, all seven wrapper schemas, explicit routing,
false verification, missing Campaign state, disabled-dependent evidence, unowned
popup refusal and uncertain-write latch behavior. Mock-based routing tests prove
contracts, not tenant compatibility.

Executed actual headed local Chromium command (RUN_REAL_REMAINING_FIELDS=1,
RUN_REAL_BROWSER_BASELINE=1 and the existing executable exported):

```cmd
.\.venv\Scripts\python.exe -B -m pytest tests/test_browser_learning_loop.py tests/test_interactive_widgets.py tests/test_real_browser_baseline.py -q --tb=short -p no:cacheprovider --junitxml=outputs/capability-local-chromium-final.xml
```

5 passed in 24.65 s: actual loopback Chromium/Stagehand, real Deep Agents/local
model doubles, field interactions/readback, revised guidance loading, bounded
iterations, selected/collapsed initialization, original context retention,
no-save restrictions and owned cleanup. The count includes two non-browser
contract checks alongside three actual-browser tests. No live acceptance claim.

Python compilation, schema export, entry-point parser and unchanged Campaign
implementation checks passed. The full offline run disables all RUN_* flags and
uses an empty process API key; browser tests are skipped and no provider is called.
Final full-suite details and failure IDs are recorded below after completion.

## Readiness and token usage

No unconditional live-readiness claim. Contract and local Chromium coverage are
stronger, but the full suite still contains historical failures and deliberate
frozen-file assertions. Independent tenant/authentication evidence remains unresolved;
any later live run requires separate authorization or the explicit supervised
checkpoint. Actual four-field tenant compatibility, clean-state learning
improvement and approved candidate promotion remain unverified.

No live or paid API request was made in this phase. Actual provider token usage is
unavailable/not applicable; local model-double calls are not provider-reported
usage and are not reported as paid tokens. Prior live usage is preserved in its
original records, not substituted for this task's validation.

## Final full-suite result and unresolved failures

Final exact-source full offline run: **750 passed, 17 failed, 56 skipped**, 23.76 s.
Command: `python -B -m pytest -q --tb=short -p no:cacheprovider --junitxml=outputs/capability-release-offline.xml` with all RUN_* flags cleared and an empty process API key.
Fourteen failing test IDs match the preserved consolidation baseline; three additional frozen-file checks now reject the intentional contract.py extension. No historical manifest was rebased or test removed. The additional failures are:

- `tests.test_remaining_field_mapping::test_working_campaign_login_core_and_saved_live_evidence_unchanged`
- `tests.test_setup_destination::test_unrelated_components_and_saved_real_evidence_are_frozen`
- `tests.test_whatsapp_preflight_repair::test_shared_teacher_security_contract_and_saved_real_run_are_unchanged`

All failing test IDs (release remains non-green):

- `tests.test_evidence_discovery_repair::test_preserved_login_teacher_ownership_and_graduation_hashes`
- `tests.test_inventory_batching::test_login_handoff_contract_guard_teacher_and_saved_artifacts_are_unchanged`
- `tests.test_pass1::test_both_roles_call_real_factory_and_bind_distinct_tools`
- `tests.test_pass1::test_explicit_openai_models_responses_and_output_bounds`
- `tests.test_post_login_block_repair::test_uncertain_teardown_remains_fail_closed[not_destroyed]`
- `tests.test_post_login_block_repair::test_uncertain_teardown_remains_fail_closed[unresolved_parent_document]`
- `tests.test_post_login_block_repair::test_uncertain_teardown_remains_fail_closed[live_descendant]`
- `tests.test_remaining_field_mapping::test_working_campaign_login_core_and_saved_live_evidence_unchanged`
- `tests.test_setup_destination::test_unrelated_components_and_saved_real_evidence_are_frozen`
- `tests.test_shadow_safety::test_security_teacher_contract_saved_sources_and_positive_indicators_are_preserved`
- `tests.test_teacher_live_repair::test_canonical_teacher_indices_and_outcomes_are_unambiguous[AUTHENTICATED_AND_VERIFIED]`
- `tests.test_teacher_live_repair::test_canonical_teacher_indices_and_outcomes_are_unambiguous[UNEXPECTED_PAGE]`
- `tests.test_teacher_live_repair::test_rejection_is_not_silently_accepted_and_has_an_exact_safe_code[kwargs0-EVIDENCE_INDEX_OUT_OF_RANGE]`
- `tests.test_teacher_live_repair::test_rejection_is_not_silently_accepted_and_has_an_exact_safe_code[kwargs1-OUTCOME_MISMATCH]`
- `tests.test_teacher_live_repair::test_guard_failure_is_categorized_without_private_protocol_text`
- `tests.test_three_run::test_categorical_teacher_validation_budget_and_finish_failures`
- `tests.test_whatsapp_preflight_repair::test_shared_teacher_security_contract_and_saved_real_run_are_unchanged`

The final standalone contract matrix is **149 passed** (capability-matrix-final.xml); the full final run includes these cases. Original authentication/application/browser/Deep Agents/runtime-skill source and learning/strategy.md hashes were verified unchanged against the prior audit baseline. The Campaign fill function is byte-equivalent to its original source-segment hash.

Read-only review found the selected/collapsed WABA gap, which was reproduced/fixed and re-reviewed; no further important findings were reported for those corrections. This source review is not additional browser or live evidence.

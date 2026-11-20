# WhatsApp Doer startup repair

**Goal:** Implement the supplied P0 request using the existing Stagehand, Deep Agents,
LangGraph and candidate registry. No live browser or provider execution is authorized.

**Design:** Keep protected session/security checks before task invocation. Move Setup
recognition into browser_action. Use distinct semantic-label and fresh DOM-label
discovery, retaining per-target checks before edits. A bounded controller turn resets
the existing one-mutation allowance and independently reads state. Two distinct valid
failed approaches produce STUCK; model claims alone never do. Reuse the existing V2
Teacher review and unapproved candidate storage after eligible failures. One human
c/x decision authorizes this session only; never automatically repeat live sessions.

**Constraints:** Preserve current checkout changes, approved private payload, Login,
ownership, protected tabs, security, one credential submission and persistence gates.
No provider calls, Chromium launch, dependency installation or candidate promotion.
The manual Setup test does not consume the expired schedule or authorize progression.

**Implementation and verification:**

- [x] Add offline regressions for unknown-form invocation, distinct failures,
  per-turn readback, c/x and independent limits; run and observe failures.
- [x] Repair observation/argument registry and execution; preserve identity checks.
- [x] Repair human-assisted routing and reuse V2 Teacher/candidate functions.
- [x] Run focused offline tests, AST parsing and diff review; document limitations
  and a runnable PowerShell command with separate learning authorization.

**Review focus:** Empty observations; ambiguous identity; stale references; uncertain
writes; missing model usage; request/token exhaustion before Teacher; cancellation;
unverified operator-confirmed sessions; persistence blocked despite model guidance.

User supplied the execution design and explicitly requested implementation; proceed
in this checkout without adding an approval round. Historical manifests stay intact.

Validation: 130 passed; one pre-existing incomplete trace-double failure remains
reported. AST/JavaScript syntax, diff whitespace and command parsing pass. Independent
review findings (combobox persistent display, invocation facts, terminal ownership)
were resolved and re-reviewed. Auto-review blocked a new model-callable Next Step
action; it remains unimplemented. Code-defect review was made explicitly analysis
only with no candidate-storage branch after automatic review rejected a combined
eligibility/storage change. No live execution or provider call occurred.

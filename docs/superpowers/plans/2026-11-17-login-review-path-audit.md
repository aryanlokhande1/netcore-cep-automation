# Focused readiness and review-path audit

Authorized scope: current-source audit, focused fixes and controlled offline tests.
No live browser, provider inference, campaign edits or skill changes.

Confirmed defects: non-boolean observed resources have no operator path from
discovery to an unreviewed candidate; requiring a global-scope certificate before
creating that candidate also prevents later scoped verification. Partial readiness
coverage is not reported explicitly. Existing production authentication gates stay.

1. Preserve current source/evidence/payload hashes.
2. Add failing regressions for partial-scan UNKNOWN, observed scalar selection,
   stale selection rejection and explicit digest approval to independent verification.
3. Extend the existing discovery/review D/P/A mechanism with explicit selection of
   an actual observed scalar option; never guess an endpoint, mapping or contract.
   A selection writes only unreviewed evidence. Current full verification controls A.
4. Verify stable scoped predicates can survive unrelated inventory changes while
   unresolved inaccessible roots and negative/security gaps remain blockers.
5. Repair the three obsolete positive test doubles to supply coherent protected
   contexts. Update the outdated custom-host diagnostic assertion without removing
   its fail-closed test. Leave both historical manifest checks and files untouched.
6. Run affected offline tests, exact JS/Python checks, source diff and integrity
   comparison. Classify every residual failure and publish a bounded GO/NO-GO.

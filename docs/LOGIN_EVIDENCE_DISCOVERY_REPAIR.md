> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Login evidence discovery repair

Scope: discovery/review and sanitized errors only; no live browser/provider execution.
Strict authentication verification remains the only executable resource assertion mode.
No evidence was approved, no candidate generated, and skills/login/SKILL.md was untouched.

Changes:
- modules/login/discovery.py: shared bounded traversal of open shadow roots for
  discovery and positive DOM verification, host-chain selectors, node/root/depth
  limits and fail-closed unknown custom hosts. Unknown hosts are not classified as
  proven closed roots. DOM inspection cannot certify absence of closed roots;
  closed_root_coverage explicitly reports NOT_ESTABLISHED_BY_DOM. No closed root
  is opened or bypassed. Existing frame restrictions remain.
- browser/discovery.py: passive authorized GET capture now includes query-bearing
  responses transiently, without persisting their URLs/query values or selecting
  them automatically. Nested dictionary identity paths use JSON Pointer syntax;
  legacy top-level keys retain their interpretation. Bounded reduction retains
  boolean matches and transient identity hashes, never response bodies/values.
  Session/document/account ownership and expiry checks remain required.
- modules/login/evidence.py: clear MISSING_EVIDENCE_CONFIGURATION; optional exact
  reviewed identity mapping bound to SHA256 of the login account and an operator
  evidence reference. Mapping affects UI/resource comparisons, not credentials.
  No guessed mapping is generated. Mapping configuration is private, not telemetry.
  Nested JSON Pointer lookup supports strict boolean-and-exact-identity checks.
- browser/evidence_diagnostics.py: sanitized coverage counts.
- app/cli.py: fixed evidence error codes instead of the generic error message.
- tests/test_login_evidence_extension.py, tests/evidence_dom_double.js and the
  query-capture expectation in tests/test_evidence_discovery_repair.py: deterministic
  regression coverage; frozen integrity manifests unchanged.

Validation command:
python -B -m pytest tests/test_login_evidence_extension.py tests/test_evidence_discovery_repair.py tests/test_standalone_login_repair.py -q --tb=short -p no:cacheprovider --junitxml=outputs/login-evidence-repair-offline.xml

63 passed, 1 failed: the existing frozen source-hash assertion
 test_preserved_login_teacher_ownership_and_graduation_hashes. No broad suite was
rerun. JavaScript expressions executed against Node DOM doubles; no Chromium ran.
Local tests are not real Netcore compatibility or authentication certification.

## Separately reviewed alternative assertion design (not implemented/activated)

Keep strict explicit authenticated-boolean verification as default. An alternative
must be a versioned, explicitly selected configuration with independent operator
approval, evidence references proving authentication-dependent resource semantics,
a fresh authorized observed read-only response, exact tenant identity (or reviewed
context-bound mapping), matching session/document ownership, and separately
reviewed authenticated UI. Negative states, stale ownership, coverage gaps or
unproven semantics must fail closed. A response status, heading or hostname alone
cannot satisfy it. Query-bearing resources need explicit reviewed eligibility and
transient exact request binding; never replay arbitrary discovered URLs or persist
secret query parameters. No endpoint or semantics have been established for the
real tenant, so no alternative configuration is created.

Remaining limitations: JSON arrays are not searched; oversized/deep dictionaries
produce no usable facts. Query-bearing resources are discovery-only and cannot
enter default strict configuration. Closed-root contents remain inaccessible;
unknown custom hosts block positive completeness. Real tenant/account indicators
and suitable authentication-dependent resources are still unestablished. No
MISSING_EVIDENCE_CONFIGURATION is converted to a success result.

## Prepared supervised command (not executed)

```cmd
cd /d C:\Aryan\Attributics\Browser_automation\Netcore_agent
.\.venv\Scripts\python.exe -m app.cli live --authorized-live --login-only --api-request-budget 6 --hold-seconds 300 --review-evidence --evidence-review-file credentials\netcore-evidence.json
```

Separate operator execution authorizes one login submission and up to six Doer
requests; no Teacher learning or promotion. D discovers, P previews, A approves
only exact reviewed evidence with all verification gates passing; Enter closes.
If no suitable resource/tenant evidence is observed, leave the configuration
unapproved and preserve incomplete status. This change does not justify another
unchanged automated run or unverified unattended mutation.

---
name: netcore-login
description: Use when running or reviewing the Netcore Login Doer, its brokered sign-in, and its protected dashboard handoff.
---

# Netcore Login runbook

## Purpose and scope

Sign in once and establish what the protected session proves. The Login Doer
uses the live tools in deep_agents/live_tools.py. The controller owns the
browser, operator checkpoint, and handoff. The credential broker alone
accesses private values. The independent Login verifier is deterministic.

SkillStore.snapshot() in learning/skill_storage/registry.py requires approved
canonical metadata and matching bytes from the approved bundle. Without an
approved snapshot, the Login Doer uses deep_agents/prompts/doer.md and the
application-validated strategy. The read_approved_skill tool is registered
only when an approved snapshot is supplied. It reads the virtual catalog
path /approved-skills/netcore-login/SKILL.md. Never supply a host path.

## Login recognition

### 1. Open the approved sign-in entry ? Doer

**Start.** The controller has created an isolated protected browser and
provided the approved login entry. No navigation has been attempted.

**Tool.** Call navigate_approved.

~~~json
{"url":"https://login.netcoresmartech.com/"}
~~~

**Binding and helper.** No control reference is needed.
deep_agents/browser_tools.py BrowserTools.navigate checks the input URL
against the approved entry and checks the navigation counter and terminal
state. browser/stagehand_client.py StagehandClient.navigate checks the
browser budget and origin policy. The tool returns navigated and
must_inspect.

**Readback and recovery.** Inspect the rendered entry in step 2. If
navigation fails or origin policy blocks it, report the sanitized stop and
let the controller inspect or close the owned session. Do not navigate
again or substitute a destination.

### 2. Observe one complete sign-in form ? Doer

**Start.** Entry navigation succeeded. The owned tab is current.

**Tool.** Call inspect_page.

~~~json
{}
~~~

**Binding and helper.** LiveBrowserTools._inspect supplies a fresh
observation prefix to browser/live_dom.py LIVE_DOM. The script classifies
visible inputs and buttons from labels, roles, types, and autocomplete.
The tool returns sanitized flags, verification details, and opaque
references with canonical label, role, kind, and sensitivity. Require one
reference each for account, username, the password-field role, and Login
or Sign in. The four references must come from the same result. The broker
independently checks enabled state and common form ownership before filling.

**Readback and recovery.** Require the normal credential form on the
allowed origin. Inspect again within budget if the form is still rendering.
Stop before submission if the roles remain missing or ambiguous, or a
challenge, rejection, expiry, or restriction is observed.

## Authentication boundary

### 3. Enter and submit credentials through the broker ? Doer and broker

**Start.** One current normal sign-in form is recognized. The credential
latch is unused and security checks are clear.

**Tool.** Call submit_login once with the four opaque references from step 2.

~~~json
{"account_reference":"<fresh-account-ref>","username_reference":"<fresh-username-ref>","password_reference":"<fresh-password-ref>","submit_reference":"<fresh-submit-ref>"}
~~~

**Binding and helper.** LiveBrowserTools.submit checks each reference
against its observed role. BrowserTools._resolve can refresh a stale
mapping once before the broker runs. The tool then rebinds all four roles
together. modules/login/credentials.py CredentialBroker.submit passes
its private mapping to browser/live_dom.py VALIDATE_MAPPING. The script
returns a boolean for four distinct visible enabled nodes, a common form,
one input per credential role, recognized submit text, and a same-origin
form action. The broker independently checks the credential origin and
security flags, claims the latch before filling, fills the three fields
privately, and clicks the submit control once.

**Readback and recovery.** The tool can return acted, must_inspect, and the
submission count. Discard old references and inspect the resulting page.
Never read back entered credentials. For failed mapping, report the
sanitized error. For a possibly dispatched fill or submit, leave the latch
unchanged and use read-only observation. SUBMISSION_FAILED_NO_RETRY or an
ambiguous submission never permits a second submission.

### 4. Observe redirect and readiness ? broker and Doer

**Start.** Submission was dispatched or its outcome is uncertain, and the
controller still owns the browser.

**Tool.** Call inspect_page when the tool returns and protected observation
is available.

~~~json
{}
~~~

**Binding and helper.** No action reference is needed.
browser/stagehand_client.py StagehandClient.wait_for_application_ready
runs inside the broker path and polls bounded redirect, loading, and
application-ready states. LiveBrowserTools._inspect makes a fresh read
after submission.

**Readback and recovery.** Check the protected application destination and
negative flags. URL change, loss of the form, or a visible shell leaves
authentication unverified. Report HUMAN_ACTION_REQUIRED for a challenge
and let the controller's existing human-review path decide whether to hold
the session. Do not solve it, enter an OTP, or resubmit. On readiness
timeout, report the last observed state and use only authorized read-only
observation.

## Destination verification

### 5. Verify exact tenant and dashboard ? Doer and independent verifier

**Start.** The app is ready for protected read-only observation. No
credential action is pending.

**Tool.** Call verify_dashboard.

~~~json
{}
~~~

**Binding and helper.** No action reference is needed. The tool delegates
to BrowserTools.verify and modules/login/evidence.py observe_live. With
the current URL and reviewed configuration, NEGATIVE_STATES and POSITIVE_DOM
check security flags and scoped positive markers. browser/discovery.py
login_page_context checks primary-document coherence. Reviewed resource
proof comes from observed passive facts or the verifier's existing reviewed
GET branch. No tool accepts a resource URL. modules/login/verifier.py verify
returns the outcome; the tool includes missing predicates and sanitized
verification details.

**Readback and recovery.** AUTHENTICATED_AND_VERIFIED requires an allowed
observation, expected host, visible reviewed dashboard evidence, exact
reviewed tenant match, authenticated read-only resource proof, reviewed
configuration, and no contradictory security or login state. The
controller's stable_authentication path checks two positive observations
with unchanged protected context. INCORRECT_TENANT stops the handoff.
Missing configuration or resource proof stays unverified. Do not invent
selectors, endpoints, or evidence.

### 6. Report and hand off ? Doer and controller

**Start.** The Doer has the independent result or a specific unresolved check.

**Report.** Use DoerReport. This example reports an unverified context.
Use status success with reason verified only when the verifier supports it.

~~~json
{"status":"blocked","reason":"wrong_context"}
~~~

**Binding and helper.** No control reference is used for reporting.
deep_agents/controller.py capture_verification records the final
read-only observation. The authorized operator-confirmed flow uses
modules/whatsapp/workflow.py confirm_operator_tenant and
browser/protected_tabs.py bind_confirmed_primary_tab. The controller
checks the approved HTTPS host and tenant route, tab and session identity,
and security negatives. It shows the sanitized URL, accepts terminal
confirmation, and rechecks current context. A permitted same-tab document
change can require fresh confirmation.

**Readback and recovery.** Record OPERATOR_CONFIRMED separately with
authentication_verified false. It is a handoff for the authorized operator
flow. Decline, timeout, a sign-in redirect, wrong route, or changed tab or
session stops. Neither a Doer report nor terminal confirmation upgrades
independent authentication.

## Recovery

Before any later action distinguish a rendering delay, missing form
mapping, a negative security state, and unavailable browser protection.
Use only fresh references from the protected page. Report the sanitized
failure, submission count, and last verified step. The independent verifier
and controller own final evidence; there is no Login Observer action tool.

## Stop conditions

Stop on an unexpected tenant or origin, an unresolved challenge, rejection,
account restriction, expiry, lost protection, or exhausted budget. Never
repeat a credential submission. Keep credential values and authentication
artifacts out of tool calls, DOM outputs, and logs. Login grants no campaign
permission.

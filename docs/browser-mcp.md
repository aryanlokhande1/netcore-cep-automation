# Private Netcore browser MCP

The server uses the local MCP stdio transport. The trusted Python host binds a
single role and step by constructing `ToolScope` and `BrowserRouter`. The role,
run ID, browser object, owned session, stage, and enabled operations are host
inputs. Tool requests cannot change them. Supply the existing browser ownership
check as `require_current`, and a `current` callback that returns `None` when
the step ends. A scope with no operations lists no tools. Keep normal runtime
logs away from the MCP stdout pipe.

`python -m app.browser_mcp` starts the protocol server without an active run.
It lists an empty catalog and does not create or adopt a browser. The next
integration can pass a live router to `serve`, or pass it to `create_server`
for a private transport managed by the host. Install with `pip install -e
".[mcp]"`. Use `python -m evals.browser_mcp_smoke` to start a synthetic
private server, negotiate MCP, list a typed tool, call it, and test a rejected
mutation. Neither command contacts Netcore.

## Tool inventory

| MCP names | Existing implementation | Role and scope |
|---|---|---|
| `navigate_approved`, `inspect_page`, `submit_login`, `verify_dashboard` | `LiveBrowserTools.registered_tools` | Doer on Login. Observer may use `verify_dashboard`. The broker takes opaque references for the single guarded submission. |
| `browser_observe`, `browser_dom`, `browser_wait`, `browser_inspect` | `V2BrowserTools.action` | Doer. Semantic and accessibility-backed observation, structural DOM, bounded wait, or a fresh control reference. |
| `browser_navigate`, `browser_scroll`, `browser_options`, `browser_screenshot` | `V2BrowserTools.action` | Doer, subject to the active mode and arguments. Navigation uses an observed reference. Screenshots require existing vision authorization. |
| `browser_click`, `browser_type`, `browser_search`, `browser_select` | `V2BrowserTools.action` | Doer. Fixed values and existing scoped targets only. |
| `browser_verify`, `browser_readback`, `independent_readback` | V2 action handler or `deterministic_readback` | Observer. Doer may use the V2 verification actions where supported. `browser_readback` is diagnostic-mode only. |
| `redacted_trace`, `inspect_ga_setup`, `view_ga_setup` | Active `Trace` event buffer and `GATeacherEvidence.registered` | Teacher. GA tools require the existing held Setup evidence object. The focused image is an MCP PNG image result. The existing handler requires DOM evidence first and enforces screenshot privacy checks. |

The action schemas are split by name from the runtime V2 schema. Both the
original Pydantic model and action boundary still validate requests. All
operations pass through the existing session, tenant, field authorization and
readback logic. Campaign transitions, Save and Preview, and save-claim writes
remain controller calls. There is no MCP tool for them.

## Gaps and inactive handlers

- `BrowserTools.interact_observed` and its synthetic Login routes are fixture
  tools. The MCP fixture supplies no credential or browser mutation tools.
- `SetupTools.registered_tools` and `learning_loop.BrowserTools.registered`
  deliberately reject execution. The split V2 actions provide their active
  successors. No deprecated launcher or handler is enabled here.
- The current focused Teacher DOM, accessibility, and screenshot handler covers
  the held GA Setup area. Other stages do not have a generic held-tab crop or
  arbitrary accessibility-tree export. Those capabilities are not synthesized.
- Live Netcore verification is separate. There is no live validation of the
  scope across an SSO redirect or of the GA switch recovery.

## WhatsApp graph mode

Select `--browser-mode mcp` on `app.cli whatsapp`. For a separately authorized
run, pass the approved payload and reviewed evidence as usual. For example:

~~~powershell
.\.venv\Scripts\python.exe -m app.cli whatsapp --browser-mode mcp --payload $payload --approve-payload-sha256 $payloadSha256 --login-evidence-file $loginEvidence --authorized-live --authorized-inference --authorized-candidate-evaluation
~~~

This example permits read-only discovery. Draft testing requires the existing
reviewed record evidence and the explicit edit and persistence authorizations.
An automatic second attempt requires `--iterations 2` and explicit learning,
fresh-iteration, and candidate-evaluation authorization. The direct launcher
continues to use its existing mode.

The graph uses the SDK's in-process client and server memory streams. The host
creates the server with the same browser object and its protected session
identity. Browser objects never cross the wire. Login and WhatsApp agent tools
use the MCP client. The controller still owns independent Login handoff,
browser lifecycle, transition clicks, and persistence. Abort a step if its
scope or transport fails. The graph does not retry through the direct tools.

The Setup Doer can use `reveal_ga_advanced` when the existing scoped disclosure
is available. The Observer independently compares browser state through MCP.
After two valid failed GA switch approaches, a revoked Doer scope precedes a
held-tab Teacher scope. The controller fetches the bounded redacted trace, then
the Teacher uses DOM inspection before any focused image request. Other
failures receive analysis with the held-evidence capability gap recorded.

An accepted Teacher procedure creates an isolated candidate with `strategy.md`,
`strategy.diff`, parent hashes, and evidence references. Before the next attempt
can enter Login, the graph checks the exact candidate skill and strategy bytes.
No candidate is promoted. If Save and Preview may have landed, the workflow
uses the reviewed read-only record handler and stops without another write.

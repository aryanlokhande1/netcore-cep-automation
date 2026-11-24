# Local Linux Docker workflow

Use Docker Desktop's Linux engine from the repository root. The tested target
is linux/amd64 with Python 3.11.18, Stagehand 4.4.0, and Debian Chromium
156.0.8078.79. The container uses the Python CLI and bundled Stagehand
browser extension. Node is unnecessary.

## Build and prepare

The default Docker seccomp profile blocked Chromium's sandbox namespace
operations in the tested environment. Generate the compatibility profile
from the checksum-verified Moby 28.3.3 default. It adds the three Chromium
namespace syscalls and retains the other filtering. Use a non-root user
with no-new-privileges and keep CI unset or empty.

These commands use Bash, including Bash on WSL with Docker Desktop integration.
The host Python invocation only generates the Docker policy file.

~~~bash
mkdir -p outputs/docker-smoke
curl --fail --silent --show-error \
  https://raw.githubusercontent.com/moby/moby/v28.3.3/profiles/seccomp/default.json \
  -o outputs/docker-smoke/moby-default-28.3.3.json
python3 -m evals.docker_seccomp outputs/docker-smoke/moby-default-28.3.3.json \
  outputs/docker-smoke/chromium-seccomp.json
docker build --platform linux/amd64 -t netcore-agent:linux-local-156 .

image=netcore-agent:linux-local-156
mounts=(
  --mount "type=bind,src=$PWD/skills/login,dst=/app/skills/login,readonly"
  --mount "type=bind,src=$PWD/skills/whatsapp,dst=/app/skills/whatsapp,readonly"
  --mount "type=bind,src=$PWD/outputs/skill_history/login/approved,dst=/approved-login/approved,readonly"
)
browser_flags=(
  --init --shm-size 256m
  --security-opt "seccomp=$PWD/outputs/docker-smoke/chromium-seccomp.json"
  --security-opt no-new-privileges=true
  --tmpfs /app/profiles:uid=10001,gid=10001,mode=0700
)
~~~

The output bind mount must be writable by UID 10001. The image excludes
private configuration, payloads, profiles, run outputs, and skill bundles.
The mount points supply the reviewed bytes at runtime.

## Local checks

~~~bash
docker run --rm --network none "$image" whatsapp-contract
docker run --rm --network none "${mounts[@]}" "$image" skill status
docker run --rm --network none "${mounts[@]}" --entrypoint python "$image" -c \
  'from learning.skill_storage.registry import module_baseline; print(module_baseline("whatsapp").digest)'
docker run --rm --network none --entrypoint python "$image" \
  -m pytest -q -p no:cacheprovider tests/test_linux_process_and_tty.py
docker run --rm --network none "${browser_flags[@]}" \
  --mount "type=bind,src=$PWD/outputs/docker-smoke,dst=/app/outputs" \
  --entrypoint xvfb-run "$image" -a python -m evals.docker_smoke
~~~

The browser smoke is headed Chromium on a virtual display. It uses a
loopback fixture, verifies attachment, and checks owned process exit and
profile removal before returning. The run trace remains in
outputs/docker-smoke after the container exits.

## Proposed live Login command

Require separate live approval before running this command. The browser
profiles remain temporary, and the existing dotenv loader supplies the
broker credentials only at runtime. The flags limit the session to Login
and a dashboard hold.

~~~bash
mkdir -p outputs/docker-live
docker run --rm -it --name netcore-login-review "${browser_flags[@]}" "${mounts[@]}" \
  -e CI= -e HEADLESS=false -e TEACHER_LIVE_REPLAY_ENABLED=false \
  --mount "type=bind,src=$PWD/.env,dst=/app/.env,readonly" \
  --mount "type=bind,src=$PWD/outputs/docker-live,dst=/app/outputs" \
  -p 127.0.0.1:5900:5900 \
  --entrypoint xvfb-run "$image" -n 99 -f /tmp/netcore.Xauthority \
  python -m app.cli whatsapp-human-assisted \
  --dashboard-handoff-only --operator-confirmed-login --authorized-live \
  --authorized-inference --login-api-budget 8 --task-api-budget 0 \
  --total-api-budget 8 --operator-review-seconds 300 --session-seconds 900 \
  --browser-actions 120
~~~

For private viewing, start the viewer from a second terminal while that
container is running. Enter a temporary password at the prompt and connect
your local viewer to 127.0.0.1:5900. The viewer is read-only and the tenant
decision stays in the original terminal. No Chromium debugging port is
published.

~~~bash
docker exec -it netcore-login-review x11vnc -storepasswd /tmp/netcore.vnc
docker exec netcore-login-review x11vnc -display :99 -auth /tmp/netcore.Xauthority \
  -rfbauth /tmp/netcore.vnc -rfbport 5900 -viewonly
~~~

Mount any separately approved campaign payload read-only under
/app/credentials when a later campaign run is approved. The Login command
does not require a payload.

## Unattended read-only verification and draft creation

Build the image using the command above. From the repository root, use the
same `image`, `mounts`, and `browser_flags` definitions. Prepare three private
host files. The operator supplies the reviewed Login evidence, the WhatsApp
payload, and a separate approval JSON. The approval for the existing record
has this shape and authorizes a read-only lookup only.

~~~json
{
  "origin": "https://tprappin2r.netcoresmartech.com",
  "tenant": "bajajfinservmarket",
  "action": "READ_DRAFT",
  "campaign_id": "18023",
  "payload_sha256": "400ec255dc298472b5944d1e88a9ea87a3271144131c815cdf2a6506f80a252e"
}
~~~

Use `credentials/whatsapp-draft-preview-validation-payload.json` for that
lookup. A fresh draft needs a separately approved payload with an unused
campaign name and a future Send later time. Its approval uses `SAVE_AND_PREVIEW`
and its own payload digest, with `campaign_id` omitted. Keep the output root
stable across runs. It holds the trace and the exclusive save-attempt claim.

~~~bash
mkdir -p outputs/docker-live outputs/docker-private/whatsapp-v2
approval="$PWD/credentials/your-operator-approval.json"
payload="$PWD/credentials/whatsapp-draft-preview-validation-payload.json"
evidence="$PWD/credentials/login-evidence-reviewed.json"

docker run --rm "${browser_flags[@]}" "${mounts[@]}" \
  -e CI= -e HEADLESS=false -e TEACHER_LIVE_REPLAY_ENABLED=false \
  -e TIKTOKEN_CACHE_DIR=/tmp/netcore-tiktoken \
  -e LEARNING_MAX_REQUESTS=24 -e LEARNING_MAX_INPUT_TOKENS=240000 -e LEARNING_MAX_OUTPUT_TOKENS=47000 \
  --mount "type=bind,src=$PWD/.env,dst=/app/.env,readonly" \
  --mount "type=bind,src=$approval,dst=/run/netcore/approval.json,readonly" \
  --mount "type=bind,src=$payload,dst=/run/netcore/payload.json,readonly" \
  --mount "type=bind,src=$evidence,dst=/run/netcore/login-evidence.json,readonly" \
  --mount "type=bind,src=$PWD/outputs/docker-live,dst=/app/outputs" \
  --mount "type=bind,src=$PWD/outputs/docker-private/whatsapp-v2,dst=/app/credentials/whatsapp-v2" \
  --entrypoint xvfb-run "$image" -a python -m app.cli whatsapp-human-assisted \
  --unattended-config /run/netcore/approval.json --unattended-payload /run/netcore/payload.json \
  --login-evidence-file /run/netcore/login-evidence.json \
  --authorized-live --authorized-inference --login-api-budget 8 --task-api-budget 15 \
  --total-api-budget 24 --session-seconds 900 --browser-actions 600 </dev/null
~~~

Use the command without `-i` or `-t`. A missing or unreviewed Login contract
blocks startup. A reviewed contract still has to pass independent checks in
the current owned session. A blank Campaigns view is a readiness failure.
A previously claimed save requires read-only reconciliation. A zero exit
requires verified Draft identity and schedule plus browser/profile cleanup.
See [the Login and WhatsApp runbooks](operations.md#unattended-whatsapp-draft-runbook)
for the tool sequence and stop conditions.

### Run from PowerShell

Use an immutable Docker image ID. For `READ_DRAFT`, pin the canonical
WhatsApp baseline mounted at `/app/skills/whatsapp`. A write requires the
graduated WhatsApp snapshot in `outputs/docker-live/skill_history/whatsapp/approved`.
The unattended CLI checks its exact approval scope and the hash given through
`--baseline-sha256`. The current graduation record binds the snapshot to one
payload digest. Verify the approved Login bundle digest before mounting its
registry. Keep the GA candidate in its evaluation registry.

~~~powershell
function Invoke-NetcoreDraft {
  param(
    [Parameter(Mandatory)] [string] $ImageId,
    [Parameter(Mandatory)] [string] $WhatsAppSkillSha256,
    [Parameter(Mandatory)] [string] $LoginEvidenceFile,
    [Parameter(Mandatory)] [string] $ApprovalFile,
    [Parameter(Mandatory)] [string] $PayloadFile
  )
  $approval = Get-Content -Raw -LiteralPath $ApprovalFile | ConvertFrom-Json
  $payloadDigest = (Get-FileHash -Algorithm SHA256 -LiteralPath $PayloadFile).Hash
  if ($payloadDigest -ne $approval.payload_sha256) { throw 'Payload digest mismatch' }
  $taskBudget = if ($approval.action -eq 'READ_DRAFT') { '0' } else { '15' }
  $totalBudget = if ($approval.action -eq 'READ_DRAFT') { '8' } else { '23' }
  $mounts = @(
    @('skills/login', '/app/skills/login', ',readonly'),
    @('skills/whatsapp', '/app/skills/whatsapp', ',readonly'),
    @('outputs/skill_history/login/approved', '/approved-login/approved', ',readonly'),
    @('.env', '/app/.env', ',readonly'),
    @($ApprovalFile, '/run/netcore/approval.json', ',readonly'),
    @($PayloadFile, '/run/netcore/payload.json', ',readonly'),
    @($LoginEvidenceFile, '/run/netcore/login-evidence.json', ',readonly'),
    @('outputs/docker-live', '/app/outputs', ''),
    @('outputs/docker-private/whatsapp-v2', '/app/credentials/whatsapp-v2', '')
  )
  $seccomp = (Resolve-Path -LiteralPath 'outputs/docker-smoke/chromium-seccomp.json' -ErrorAction Stop).Path
  $dockerArgs = @('run', '--rm', '--init', '--shm-size', '256m', '--security-opt', "seccomp=$seccomp",
    '--security-opt', 'no-new-privileges=true', '--tmpfs', '/app/profiles:uid=10001,gid=10001,mode=0700')
  foreach ($mount in $mounts) {
    $source = (Resolve-Path -LiteralPath $mount[0] -ErrorAction Stop).Path
    $dockerArgs += @('--mount', "type=bind,source=$source,target=$($mount[1])$($mount[2])")
  }
  $dockerArgs += @('-e', 'CI=', '-e', 'HEADLESS=false', '-e', 'TEACHER_LIVE_REPLAY_ENABLED=false',
    '-e', 'TIKTOKEN_CACHE_DIR=/tmp/netcore-tiktoken', '-e', 'LEARNING_MAX_REQUESTS=24',
    '-e', 'LEARNING_MAX_INPUT_TOKENS=240000', '-e', 'LEARNING_MAX_OUTPUT_TOKENS=47000',
    '--entrypoint', 'xvfb-run', $ImageId, '-n', '99', '-f', '/tmp/netcore.Xauthority',
    'python', '-m', 'app.cli', 'whatsapp-human-assisted',
    '--unattended-config', '/run/netcore/approval.json', '--unattended-payload', '/run/netcore/payload.json',
    '--login-evidence-file', '/run/netcore/login-evidence.json', '--baseline-sha256', $WhatsAppSkillSha256,
    '--authorized-live', '--authorized-inference', '--login-api-budget', '8', '--task-api-budget', $taskBudget,
    '--total-api-budget', $totalBudget, '--session-seconds', '900', '--browser-actions', '600')
  & docker @dockerArgs
  if ($LASTEXITCODE -ne 0) { throw "Netcore draft command exited with code $LASTEXITCODE" }
}
~~~

The command leaves stdin unattached and allocates no TTY. Start with the
`READ_DRAFT` approval for `18023`. Check the exact tenant, campaign ID, Draft
status, and persisted Send Later schedule. For draft creation, use only the
approval file bound to the exact payload. A release acceptance check must
also verify sender and template from the reopened record. Setup and Content
readbacks before Save and Preview provide separate evidence. When the
WhatsApp Doer makes a model request, the run directory contains
`whatsapp-doer-instructions.jsonl` with matched, redacted instruction excerpts.
The deterministic path can complete without a WhatsApp Doer model request.

### Watch unattended drafts in one PowerShell window

Create `credentials/whatsapp-drafts-launch.json` with the reviewed paths and
pins. Configure the viewer to use the same password as the VNC password file.
Use the argument format supported by that viewer. The launcher expands
`{host}` and `{port}` and opens the viewer anew for the second session.

~~~json
{
  "image_id": "sha256:<verified draft-launch image ID>",
  "whatsapp_skill_sha256": "<graduated skill SHA-256>",
  "login_evidence": "credentials/login-evidence-reviewed.json",
  "viewer_path": "C:/Program Files/TigerVNC/vncviewer.exe",
  "viewer_arguments": ["{host}::{port}"],
  "vnc_password_file": "credentials/vnc-password",
  "vnc_port": 5900
}
~~~

Build the working image with `docker build --platform linux/amd64 -t netcore-agent:draft-launch .`
and use its immutable ID in the configuration. Use the existing reviewed
approval files `credentials/whatsapp-draft-a-approval.json` and
`credentials/whatsapp-draft-b-approval.json`, each bound to its exact payload.

Run `./run-whatsapp-drafts.ps1 -ReadOnly` first. This only looks up draft
18023. Once that passes, run `./run-whatsapp-drafts.ps1` to process A and B.
Docker allocates no TTY and leaves stdin unattached. VNC is view-only and
published only on loopback. The CLI waits for an authenticated viewer before
starting Chrome. B starts only after A exits zero with verified saved values.
An exact existing record is reopened read-only. An ambiguous record or
uncertain prior save stops the run before another write.

The launcher writes `outputs/docker-live/whatsapp-doer-instructions.md`. It
uses only request-boundary excerpts and records when a deterministic run made
no task-model request.

[CmdletBinding()]
param(
    [switch] $ReadOnly,
    [string] $ConfigPath = 'credentials/whatsapp-drafts-launch.json'
)

$ErrorActionPreference = 'Stop'
$tenant = 'bajajfinservmarket'
$origin = 'https://tprappin2r.netcoresmartech.com'
$baselineHash = '4fbd8076b1b2ed67d767c71abbce7e95ad6831094d128bac972f1f9697b0a809'
$runs = [System.Collections.Generic.List[object]]::new()
$report = 'outputs/docker-live/whatsapp-doer-instructions.md'
$failure = $null

function Resolve-Input([string] $Path, [string] $Code) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw $Code }
    (Resolve-Path -LiteralPath $Path).Path
}

function Read-Json([string] $Path) {
    try { Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json }
    catch { throw 'DRAFT_LAUNCH_JSON_UNREADABLE_OR_INVALID' }
}

function Read-Launch([string] $Approval, [string] $Payload, [string] $Action, [string] $CampaignId) {
    $approvalFile = Resolve-Input $Approval 'EXACT_PAYLOAD_APPROVAL_MISSING'
    $payloadFile = Resolve-Input $Payload 'PAYLOAD_FILE_MISSING'
    $grant = Read-Json $approvalFile
    $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $payloadFile).Hash.ToLowerInvariant()
    if ($grant.origin -cne $origin -or $grant.tenant -cne $tenant -or $grant.action -cne $Action -or
        $grant.payload_sha256 -cne $digest -or [string]$grant.campaign_id -cne $CampaignId) {
        throw 'UNATTENDED_APPROVAL_SCOPE_OR_PAYLOAD_DIGEST_MISMATCH'
    }
    [pscustomobject]@{ Approval = $approvalFile; File = $payloadFile; Digest = $digest; Payload = (Read-Json $payloadFile); Action = $Action }
}

function Add-CodeBlock($Lines, [string] $Text, [string] $Language = 'text') {
    $fence = '`' * 3
    while ($Text.Contains($fence)) { $fence += '`' }
    $Lines.Add($fence + $Language)
    $Lines.Add($Text)
    $Lines.Add($fence)
    $Lines.Add('')
}

function Update-InstructionReport {
    if ($ReadOnly -and (Test-Path -LiteralPath $report)) { return }
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('# WhatsApp Doer instructions')
    $lines.Add('')
    if ($failure) { $lines.Add("Launcher stopped with $failure."); $lines.Add('') }
    if ($runs.Count -eq 0) { $lines.Add('No run reached the WhatsApp Doer. No runtime instructions were captured.') }
    foreach ($run in $runs) {
        $summary = $run.Summary
        $id = $summary.run_id
        $lines.Add("## $($run.Action) $($run.Payload.campaign_name)")
        $lines.Add('')
        $lines.Add("Workflow: whatsapp-human-assisted unattended. Run ID: $id.")
        $lines.Add("Payload SHA-256: $($run.Digest).")
        $privateFields = 'business_number', 'waba', 'include_audience', 'exclude_audience'
        $safePayload = [ordered]@{}
        $allowedFields = 'campaign_name', 'waba', 'business_number', 'tag', 'ga_tracking', 'audience_type', 'include_audience',
            'exclude_audience', 'template', 'send_type', 'schedule_datetime', 'timezone', 'frequency_cap_enabled',
            'contact_limit_enabled', 'final_action', 'publish_allowed', 'send_test_allowed'
        foreach ($property in $run.Payload.PSObject.Properties | Where-Object Name -in $allowedFields) {
            $safePayload[$property.Name] = if ($property.Name -in $privateFields) { '[redacted]' } else { $property.Value }
        }
        Add-CodeBlock $lines ($safePayload | ConvertTo-Json -Depth 12) 'json'
        if ($id -notmatch '^[a-f0-9]{32}$') {
            $lines.Add('No run ID or loaded skill was recorded.'); continue
        }
        $directory = "outputs/docker-live/human-assisted/login-whatsapp-$id"
        $skillFile = Join-Path $directory 'loaded-task-skill.md'
        $loadedHash = if (Test-Path -LiteralPath $skillFile) { (Get-FileHash -LiteralPath $skillFile).Hash.ToLowerInvariant() } else { 'not loaded' }
        $lines.Add("Loaded skill SHA-256: $loadedHash.")
        $capture = Join-Path $directory 'whatsapp-doer-instructions.jsonl'
        if (-not (Test-Path -LiteralPath $capture)) {
            if ($summary.task_doer_invoked -eq $false) { $lines.Add('The run made no WhatsApp Doer model request.') }
            else { $lines.Add('Exact runtime instruction text is unavailable. No reconstruction is included.') }
            continue
        }
        $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        foreach ($line in (Get-Content -Encoding UTF8 -LiteralPath $capture)) {
            $entry = $line | ConvertFrom-Json
            if ($entry.run_id -cne $id -or $entry.skill_sha256 -cne $loadedHash) { continue }
            $key = $entry | Select-Object system_instructions, skill_instructions, task_instructions | ConvertTo-Json -Compress
            if (-not $seen.Add($key)) { continue }
            $lines.Add("Request SHA-256: $($entry.request_sha256).")
            foreach ($label in 'system_instructions', 'skill_instructions', 'task_instructions') {
                if ($null -eq $entry.$label) { $lines.Add("$label unavailable at the request boundary."); continue }
                $lines.Add("### $label")
                Add-CodeBlock $lines $entry.$label
            }
        }
    }
    $path = Join-Path (Get-Location).Path $report
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
    [IO.File]::WriteAllLines($path, $lines, [Text.UTF8Encoding]::new($false))
}

function Write-DraftEvent([string] $Label, [string] $Line) {
    if ($Line -notmatch '^(?:\[[^\]]+\]\[[^\]]+\]\[[^\]]+\] [A-Z_]+ )?(?<event>[A-Z][A-Z0-9_]+) (?<json>\{.*\})$') { return }
    $event = $Matches.event
    try { $data = $Matches.json | ConvertFrom-Json } catch { return }
    switch ($event) {
        'UNATTENDED_TENANT_VERIFIED' { if ($data.authentication_verified -eq $true) { Write-Host "[$Label] Login | Tenant verified" } }
        'BROWSER_ACTION_STARTED' {
            if ($data.action -cmatch '^(type|select|click)$' -and $data.field -cmatch '^(campaign_name|waba|business_number|tag|ga_tracking)$') {
                Write-Host "[$Label] Doer | $($data.action) attempt - $($data.field.Replace('_',' '))"
            }
        }
        'PAGE1_FIELD' { if ($data.readback -ceq 'MATCH' -and $data.field -cmatch '^(campaign_name|waba|business_number|tag|ga_tracking)$') { Write-Host "[$Label] Observer | $($data.field.Replace('_',' ')) matched" } }
        'CAMPAIGN_STEP_VERIFIED' { if ($data.step -cmatch '^(audience|content)$') { Write-Host "[$Label] Observer | $($data.step) verified" } }
        'SCHEDULE_FIELDS_VERIFIED' { Write-Host "[$Label] Observer | Send Later fields verified" }
        'SCHEDULE_DRAFT_CLICK_RESULT' { if ($data.click_result -cmatch '^(DISPATCHED|BLOCKED|UNCERTAIN)$') { Write-Host "[$Label] Controller | Save and Preview click attempt - $($data.click_result)" } }
        'CAMPAIGN_RECONCILIATION' {
            if ($data.status -ceq 'PERSISTED_DRAFT_MATCH') { Write-Host "[$Label] Observer | Reopened draft and verified saved fields" }
            if ($data.status -ceq 'PERSISTED_SCHEDULE_MATCH') { Write-Host "[$Label] Observer | Reopened draft and verified identity, status and schedule" }
        }
        'TEACHER_INVOCATION' { Write-Host "[$Label] Teacher | Invoked" }
    }
}

function Invoke-DraftSession($Launch, [string] $SkillSha, [string] $Label) {
    $taskBudget = if ($Launch.Action -ceq 'READ_DRAFT') { '0' } else { '15' }
    $totalBudget = if ($Launch.Action -ceq 'READ_DRAFT') { '8' } else { '23' }
    $mounts = @(
        @('skills/login', '/app/skills/login', ',readonly'),
        @('skills/whatsapp', '/app/skills/whatsapp', ',readonly'),
        @('outputs/skill_history/login/approved', '/approved-login/approved', ',readonly'),
        @('.env', '/app/.env', ',readonly'),
        @($Launch.Approval, '/run/netcore/approval.json', ',readonly'),
        @($Launch.File, '/run/netcore/payload.json', ',readonly'),
        @($evidenceFile, '/run/netcore/login-evidence.json', ',readonly'),
        @($vncPasswordFile, '/run/netcore/vnc-password', ',readonly'),
        @('scripts/whatsapp-draft-session.sh', '/run/netcore/draft-session.sh', ',readonly'),
        @('outputs/docker-live', '/app/outputs', ''),
        @('outputs/docker-private/whatsapp-v2', '/app/credentials/whatsapp-v2', '')
    )
    $arguments = @('run', '-d', '--init', '--shm-size', '256m', '--security-opt', "seccomp=$seccomp",
        '--security-opt', 'no-new-privileges=true', '--tmpfs', '/app/profiles:uid=10001,gid=10001,mode=0700',
        '-p', "127.0.0.1:${port}:5900")
    foreach ($mount in $mounts) {
        $source = (Resolve-Path -LiteralPath $mount[0] -ErrorAction Stop).Path
        $arguments += @('--mount', "type=bind,source=$source,target=$($mount[1])$($mount[2])")
    }
    $arguments += @('-e', 'CI=', '-e', 'HEADLESS=false', '-e', 'TEACHER_LIVE_REPLAY_ENABLED=false',
        '-e', 'TIKTOKEN_CACHE_DIR=/tmp/netcore-tiktoken', '-e', 'LEARNING_MAX_REQUESTS=24',
        '-e', 'LEARNING_MAX_INPUT_TOKENS=240000', '-e', 'LEARNING_MAX_OUTPUT_TOKENS=47000',
        '--entrypoint', 'xvfb-run', $image, '-n', '99', '-f', '/tmp/netcore.Xauthority',
        '/bin/sh', '/run/netcore/draft-session.sh', '--unattended-config', '/run/netcore/approval.json',
        '--unattended-payload', '/run/netcore/payload.json', '--login-evidence-file', '/run/netcore/login-evidence.json',
        '--baseline-sha256', $SkillSha, '--authorized-live', '--authorized-inference', '--login-api-budget', '8',
        '--task-api-budget', $taskBudget, '--total-api-budget', $totalBudget, '--session-seconds', '900', '--browser-actions', '600')
    $container = (& docker @arguments | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or $container -notmatch '^[a-f0-9]{64}$') { throw 'DRAFT_CONTAINER_START_FAILED' }
    $viewerReady = $false
    $rawLog = Join-Path (Get-Location).Path "outputs/docker-live/whatsapp-$Label-$($container.Substring(0,12)).log"
    try {
        for ($i = 0; $i -lt 60; $i++) {
            $state = & docker inspect --format '{{.State.Running}}' $container
            if ($state -ne 'true') { break }
            $probe = [Net.Sockets.TcpClient]::new()
            try {
                $probe.Connect('127.0.0.1', $port)
                $probe.ReceiveTimeout = 300
                $banner = [IO.StreamReader]::new($probe.GetStream()).ReadLine()
                if ($banner -match '^RFB \d{3}\.\d{3}$') { $viewerReady = $true; break }
            } catch [System.IO.IOException] {} catch [Net.Sockets.SocketException] {}
            finally { $probe.Dispose() }
            Start-Sleep -Milliseconds 250
        }
        if ($viewerReady) {
            $viewerArgs = @($config.viewer_arguments | ForEach-Object {
                $arg = ([string]$_).Replace('{host}', '127.0.0.1').Replace('{port}', [string]$port)
                if ($arg.Contains('"')) { throw 'VNC_VIEWER_ARGUMENT_INVALID' }
                '"' + $arg + '"'
            })
            Start-Process -FilePath $viewerPath -ArgumentList ($viewerArgs -join ' ')
            Write-Host "[$Label] Viewer | Opened configured viewer"
        }
        $log = [System.Collections.Generic.List[string]]::new()
        try {
            $ErrorActionPreference = 'Continue'
            & docker logs --follow $container 2>&1 | ForEach-Object {
                $line = [string]$_
                $log.Add($line)
                Add-Content -Encoding UTF8 -LiteralPath $rawLog -Value $line
                Write-DraftEvent $Label $line
            }
        } finally { $ErrorActionPreference = 'Stop' }
        $exitCode = & docker wait $container
        $match = [regex]::Matches(($log -join "`n"), '(?ms)^\{\n\s*"run_id"\s*:.*?^\}') | Select-Object -Last 1
        $summary = if ($match) { $match.Value | ConvertFrom-Json } else { $null }
        $runs.Add([pscustomobject]@{ Action = $Launch.Action; Payload = $Launch.Payload; Digest = $Launch.Digest; Summary = $summary })
        if ($exitCode -ne '0' -or -not $summary) {
            $code = if ($summary.failure.code) { $summary.failure.code } elseif ($summary.status) { $summary.status } else { $log | Select-Object -Last 1 }
            if ($code -notmatch '^[A-Z][A-Z0-9_]+$') { $code = 'DRAFT_SESSION_FAILED' }
            $stage = if ($summary.failure.failing_stage -cmatch '^[A-Za-z0-9_]+$') { $summary.failure.failing_stage } else { 'startup or workflow' }
            $last = if ($summary.last_verified_step -cmatch '^[A-Z0-9_]+$') { $summary.last_verified_step } else { 'none recorded' }
            throw "[$Label] Result | FAIL - stage $stage - $code - last verified $last (Docker exit $exitCode)"
        }
        if (-not $viewerReady) { throw 'VNC_SESSION_NOT_VERIFIED' }
        if ($Launch.Action -ceq 'READ_DRAFT') {
            $record = $summary.reconciliation
            if ($record.status -cne 'PERSISTED_SCHEDULE_MATCH' -or $record.campaign_id -cne '18023' -or $record.netcore_status -cne 'Draft') {
                throw 'READ_ONLY_DRAFT_NOT_VERIFIED'
            }
        } else {
            $record = $summary.schedule_draft_preview
            if ($summary.selected_skill_sha256 -cne $SkillSha -or $record.status -cne 'PERSISTED_DRAFT_MATCH' -or
                $record.configuration_source -cne 'SAVED_RECORD' -or $record.campaign_id -notmatch '^\d+$' -or
                $record.netcore_status -cne 'Draft' -or $record.campaign_name -cne $Launch.Payload.campaign_name -or
                $record.sender -cne $Launch.Payload.business_number -or $record.template -cne $Launch.Payload.template) {
                throw 'PERSISTED_DRAFT_READBACK_NOT_VERIFIED'
            }
        }
        $schedule = [DateTimeOffset]::Parse($Launch.Payload.schedule_datetime)
        if ($record.date -cne $schedule.ToString('yyyy-MM-dd') -or $record.time -cne $schedule.ToString('HH:mm') -or
            ($record.timezone -cne $Launch.Payload.timezone -and $record.timezone -cnotin @('Asia/Calcutta', 'Asia/Kolkata', 'IST'))) {
            throw 'PERSISTED_SCHEDULE_READBACK_NOT_VERIFIED'
        }
        $kind = if ($record.existing_record -or $Launch.Action -ceq 'READ_DRAFT') { 'existing draft' } else { 'draft' }
        Write-Host "[$Label] Result | PASS - $kind ID $($record.campaign_id) (Docker exit $exitCode)"
    } finally {
        & docker rm -f $container | Out-Null
        if (Test-Path -LiteralPath $rawLog) { Write-Host "[$Label] Raw log | $rawLog" }
    }
}

Push-Location $PSScriptRoot
try {
    $config = if (Test-Path -LiteralPath $ConfigPath) { Read-Json $ConfigPath } else { [pscustomobject]@{} }
    $evidencePath = if ($config.login_evidence) { $config.login_evidence } elseif ($env:NETCORE_EVIDENCE_FILE) { $env:NETCORE_EVIDENCE_FILE } else { 'credentials/login-evidence-reviewed.json' }
    $evidenceFile = Resolve-Input $evidencePath 'MISSING_EVIDENCE_CONFIGURATION'
    if ((Read-Json $evidenceFile).reviewed -ne $true) { throw 'LOGIN_EVIDENCE_DOCUMENT_UNREVIEWED' }
    $loginApproval = Read-Json (Resolve-Input 'outputs/skill_history/login/approved/netcore-login/approval.json' 'LOGIN_BUNDLE_NOT_APPROVED')
    if ($loginApproval.state -cne 'APPROVED' -or $loginApproval.reviewed_approval -ne $true -or
        $loginApproval.digest -cne 'cf7b1f0ff40f45b9460348d840b3bc1afa141f868b8b116fa275e1853bc7b0e2') { throw 'LOGIN_BUNDLE_APPROVAL_MISMATCH' }
    $imageRef = if ($config.image_id) { $config.image_id } elseif ($ReadOnly) { 'netcore-agent:draft-launch' } else { throw 'PINNED_DRAFT_IMAGE_ID_REQUIRED' }
    if (-not $ReadOnly -and $imageRef -notmatch '^sha256:[a-f0-9]{64}$') { throw 'PINNED_DRAFT_IMAGE_ID_REQUIRED' }
    $image = & docker image inspect --format '{{.Id}}' $imageRef
    if ($LASTEXITCODE -ne 0 -or $image -notmatch '^sha256:[a-f0-9]{64}$') { throw 'PINNED_DRAFT_IMAGE_UNAVAILABLE' }
    $lookup = Read-Launch 'outputs/docker-smoke/unattended/read-18023.json' 'credentials/whatsapp-draft-preview-validation-payload.json' 'READ_DRAFT' '18023'
    $jobs = @($lookup)
    if (-not $ReadOnly) {
        $skillRoot = 'outputs/docker-live/skill_history/whatsapp/approved'
        $skillApproval = Read-Json (Resolve-Input "$skillRoot/approval.json" 'WHATSAPP_SKILL_NOT_APPROVED')
        $skillSha = (Get-FileHash -LiteralPath "$skillRoot/SKILL.md").Hash.ToLowerInvariant()
        if ($skillApproval.explicit_approval -ne $true -or $skillApproval.content_sha256 -cne $skillSha -or
            $config.whatsapp_skill_sha256 -cne $skillSha) { throw 'UNATTENDED_APPROVED_WHATSAPP_SKILL_DIGEST_REQUIRED' }
        $jobs = @((Read-Launch 'credentials/whatsapp-draft-a-approval.json' 'credentials/whatsapp-draft-a.json' 'SAVE_AND_PREVIEW' $null),
                  (Read-Launch 'credentials/whatsapp-draft-b-approval.json' 'credentials/whatsapp-draft-b.json' 'SAVE_AND_PREVIEW' $null))
        if ($jobs[0].Digest -cne 'd2597209e82151c3bcc5d4196a8da7df7285e1bd34d87a02b475af2fc5e8effe' -or
            $jobs[1].Digest -cne 'e63c559a3d6e427686806d43eb307f5ed416b7c6ca2501ffb20fe85fef149bdf') {
            throw 'PINNED_DRAFT_PAYLOAD_DIGEST_MISMATCH'
        }
        $provenance = Read-Json "$skillRoot/provenance.json"
        foreach ($job in $jobs) {
            if ($provenance.task_sha256 -cne $job.Digest) { throw 'CANDIDATE_TASK_SCOPE_CHANGED' }
        }
    } else { $skillSha = $baselineHash }
    $viewerPath = Resolve-Input $config.viewer_path 'VNC_VIEWER_NOT_CONFIGURED'
    if (-not $config.viewer_arguments -or -not (($config.viewer_arguments -join ' ') -like '*{port}*')) { throw 'VNC_VIEWER_ARGUMENTS_NOT_CONFIGURED' }
    $vncPasswordFile = Resolve-Input $config.vnc_password_file 'VNC_PASSWORD_FILE_NOT_CONFIGURED'
    $port = if ($config.vnc_port) { [int]$config.vnc_port } else { 5900 }
    if ($port -lt 1024 -or $port -gt 65535) { throw 'VNC_LOOPBACK_PORT_INVALID' }
    $seccomp = Resolve-Input 'outputs/docker-smoke/chromium-seccomp.json' 'CHROMIUM_SECURITY_PROFILE_MISSING'
    New-Item -ItemType Directory -Force 'outputs/docker-live','outputs/docker-private/whatsapp-v2' | Out-Null
    for ($index = 0; $index -lt $jobs.Count; $index++) {
        $label = if ($ReadOnly) { 'Read-only' } else { @('A','B')[$index] }
        Invoke-DraftSession $jobs[$index] $skillSha $label
    }
} catch {
    $failure = $_.Exception.Message
    if ($failure.StartsWith('[')) { Write-Host $failure }
    else {
        $label = if ($label) { $label } elseif ($ReadOnly) { 'Read-only' } else { 'A' }
        Write-Host "[$label] Result | FAIL - preflight or launcher - $failure - last verified none recorded"
    }
} finally {
    try { Update-InstructionReport } finally { Pop-Location }
}
if ($failure) { exit 1 }

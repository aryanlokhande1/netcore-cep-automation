[CmdletBinding(DefaultParameterSetName = 'Setup')]
param(
    [Parameter(ParameterSetName = 'Calibration')][switch]$Calibrate,
    [Parameter(ParameterSetName = 'Setup')][switch]$ManualLogin,
    [Parameter(ParameterSetName = 'Page1', Mandatory)][switch]$Page1,
    [Parameter(ParameterSetName = 'ThroughSchedule', Mandatory)][switch]$ThroughSchedule,
    [Parameter(ParameterSetName = 'ThroughSchedule')][switch]$SaveDraft,
    [Parameter(ParameterSetName = 'ThroughAudience', Mandatory)][switch]$ThroughAudience,
    [Parameter(ParameterSetName = 'ThroughAudience')][switch]$CaptureAudienceFailure,
    [Parameter(ParameterSetName = 'OperatorConfirmed', Mandatory)][switch]$OperatorConfirmed,
    [Parameter(ParameterSetName = 'OperatorConfirmed')][ValidateRange(1, 3)][int]$Runs = 1,
    [Parameter(ParameterSetName = 'Capture')][switch]$CaptureLogin,
    [Parameter(ParameterSetName = 'DashboardCapture')][switch]$CaptureDashboard
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'The Netcore Python environment is missing. Restore the repository .venv before launching.'
}

$settings = @{
    TEACHER_LIVE_REPLAY_ENABLED = 'false'
    LEARNING_MAX_REQUESTS = '24'
    LEARNING_MAX_INPUT_TOKENS = '240000'
    LEARNING_MAX_OUTPUT_TOKENS = '47000'
}
$previous = @{}
Push-Location -LiteralPath $PSScriptRoot
try {
    foreach ($name in $settings.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }

    if ($CaptureLogin -or $CaptureDashboard) {
        $launchArguments = @('-m', 'app.cli', 'whatsapp-login-capture', '--authorized-live')
        if ($CaptureDashboard) {
            $launchArguments += '--dashboard-only'
        }
    } elseif ($Calibrate) {
        $launchArguments = @(
            '-m', 'app.cli', 'live', '--login-only', '--authorized-live',
            '--calibrate-whatsapp-login', '--api-request-budget', '6'
        )
    } else {
        $browserActions = if ($SaveDraft) { '600' } elseif ($ThroughSchedule) { '450' } else { '300' }
        $launchArguments = @(
            '-m', 'app.cli', 'whatsapp-human-assisted',
            '--authorized-live', '--authorized-inference', '--authorized-candidate-evaluation',
            '--manual-form-test', '--authorized-learning',
            '--baseline-sha256', '2278dd124ab929bcd7aa893448d937938e3b72d8be4fd8430b85799f52e6ccd6',
            '--login-api-budget', '6', '--task-api-budget', '17', '--total-api-budget', '24',
            '--operator-review-seconds', '300', '--session-seconds', '900', '--browser-actions', $browserActions,
            '--max-turns', '20', '--doer-tool-budget', '160', '--browser-operation-seconds', '30'
        )
        if ($ManualLogin) {
            $launchArguments += '--operator-login'
        } elseif ($ThroughAudience) {
            $launchArguments += @('--operator-confirmed-login', '--page1-through-audience', '--through-audience')
            if ($CaptureAudienceFailure) {
                $launchArguments += '--capture-audience-failure'
            }
        } elseif ($ThroughSchedule) {
            $launchArguments += @('--operator-confirmed-login', '--page1-through-audience', '--through-schedule')
            if ($SaveDraft) {
                $launchArguments += '--save-schedule-draft'
            }
        } elseif ($Page1) {
            $launchArguments += @('--operator-confirmed-login', '--page1-through-audience')
        } elseif ($OperatorConfirmed) {
            $launchArguments += @('--operator-confirmed-login', '--operator-confirmed-runs', "$Runs")
        }
    }
    & $python @launchArguments
    $launchExitCode = $LASTEXITCODE
}
finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    Pop-Location
}
exit $launchExitCode

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$stateFile = ".\.agenttrace\server.json"
$pidFile = ".\.agenttrace\server.pid"

function Get-Identity([int]$ProcessId) {
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return [pscustomobject]@{ pid=$ProcessId; start_time=$process.StartTime.ToUniversalTime().ToString("o"); executable=$process.Path; command_line=[string]$cim.CommandLine }
    } catch { return $null }
}
function Test-AgentTraceIdentity($Identity, [int]$ExpectedPort) {
    if (-not $Identity) { return $false }
    $cmd = [string]$Identity.command_line
    return $cmd -match "agenttrace\.api:app" -and $cmd -match "--port\s+$ExpectedPort(\s|$)"
}

if (-not (Test-Path -LiteralPath $stateFile)) {
    if (Test-Path -LiteralPath $pidFile) { Write-Host "PID file exists without identity state; no process was stopped." }
    else { Write-Host "No AgentTrace state file; no process was stopped." }
    exit 0
}
try { $saved = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json } catch { Write-Host "State file is invalid; no process was stopped."; exit 1 }
$identity = Get-Identity ([int]$saved.pid)
if (-not $identity) {
    Write-Host "Saved process is gone or PID is stale; no process was stopped."
    Remove-Item -LiteralPath $stateFile,$pidFile -Force -ErrorAction SilentlyContinue
    exit 0
}
if ($identity.start_time -ne $saved.start_time -or -not (Test-AgentTraceIdentity $identity ([int]$saved.port))) {
    Write-Host "PID identity does not match AgentTrace; no process was stopped."
    exit 1
}
Stop-Process -Id ([int]$saved.pid) -Force
Remove-Item -LiteralPath $stateFile,$pidFile -Force -ErrorAction SilentlyContinue
Write-Host "AgentTrace stopped safely."

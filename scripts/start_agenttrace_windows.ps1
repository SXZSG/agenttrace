$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = ".\.venv\Scripts\python.exe"
$port = if ($env:AGENTTRACE_PORT) { [int]$env:AGENTTRACE_PORT } else { 8001 }
$stateDir = ".\.agenttrace"
$stateFile = Join-Path $stateDir "server.json"
$pidFile = Join-Path $stateDir "server.pid"
$log = Join-Path $stateDir "startup.log"
$out = Join-Path $stateDir "server.out.log"
$err = Join-Path $stateDir "server.err.log"

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
function Save-State($Identity, [int]$ExpectedPort) {
    [pscustomobject]@{ pid=$Identity.pid; start_time=$Identity.start_time; executable=$Identity.executable; command_line=$Identity.command_line; port=$ExpectedPort } |
        ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    $Identity.pid | Set-Content -LiteralPath $pidFile -Encoding ASCII
}
function Remove-State { Remove-Item -LiteralPath $stateFile,$pidFile -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $python)) { "Virtual environment not found." | Set-Content $log; throw "Virtual environment not found." }

if (Test-Path -LiteralPath $stateFile) {
    try { $saved = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json } catch { $saved = $null }
    $identity = if ($saved) { Get-Identity ([int]$saved.pid) } else { $null }
    if ($identity -and [int]$saved.port -eq $port -and $identity.start_time -eq $saved.start_time -and (Test-AgentTraceIdentity $identity $port)) {
        try { $running = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2; if ($running.status) { Start-Process "http://127.0.0.1:$port/"; Write-Host "AgentTrace already running; opened workbench."; exit 0 } } catch { }
        Add-Content -LiteralPath $log -Value "Existing AgentTrace identity failed health check."
        Stop-Process -Id $identity.pid -Force -ErrorAction SilentlyContinue
    }
    Remove-State
}

$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $owner = Get-Identity ([int]$existing[0].OwningProcess)
    if ($owner -and (Test-AgentTraceIdentity $owner $port)) { Write-Host "AgentTrace is using this port but has no verified state file; no process was stopped." }
    throw "Port $port is already in use. Set AGENTTRACE_PORT to another port; no other process was modified."
}

$args = @("-m", "uvicorn", "agenttrace.api:app", "--host", "127.0.0.1", "--port", "$port")
if (Test-Path -LiteralPath ".env") { $args += @("--env-file", ".env") }
$proc = Start-Process -FilePath $python -ArgumentList $args -WorkingDirectory (Get-Location) -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
$identity = Get-Identity $proc.Id
if (-not $identity) { Remove-State; throw "Could not capture process identity." }
Save-State $identity $port

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 300
    $current = Get-Identity $proc.Id
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($response.status -and $current -and $current.start_time -eq $identity.start_time -and (Test-AgentTraceIdentity $current $port)) { $healthy = $true; break }
    } catch { }
}
if (-not $healthy) {
    Add-Content -LiteralPath $log -Value "Health check failed for PID $($proc.Id) on port $port."
    Get-Content -LiteralPath $err -ErrorAction SilentlyContinue
    $current = Get-Identity $proc.Id
    if ($current -and $current.start_time -eq $identity.start_time -and (Test-AgentTraceIdentity $current $port)) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Remove-State
    throw "Health check failed. State was cleaned; inspect $err."
}
Write-Host "AgentTrace running at http://127.0.0.1:$port/"
Write-Host "Logs: $out and $err"
Start-Process "http://127.0.0.1:$port/"
Write-Host "Run scripts\stop_agenttrace_windows.ps1 to stop this service safely."

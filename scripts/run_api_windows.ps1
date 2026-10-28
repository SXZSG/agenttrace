$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Virtual environment not found. Run scripts\setup_windows.ps1 first." }
$port = if ($env:AGENTTRACE_PORT) { $env:AGENTTRACE_PORT } else { "8001" }
Write-Host "Starting AgentTrace on http://127.0.0.1:$port/" -ForegroundColor Cyan
Write-Host "No auto reload is used. Press Ctrl+C to stop." -ForegroundColor DarkGray
$serverArgs = @("-m", "uvicorn", "agenttrace.api:app", "--host", "127.0.0.1", "--port", $port)
if (Test-Path -LiteralPath ".env") { $serverArgs += @("--env-file", ".env") }
& $python @serverArgs
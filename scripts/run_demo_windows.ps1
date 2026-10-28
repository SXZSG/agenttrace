$ErrorActionPreference = "Stop"
$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Virtual environment not found. Run .\scripts\setup_windows.ps1 first." }
& $python -m agenttrace demo --task "Diagnose the checkout service incident and explain the likely root cause."
exit $LASTEXITCODE

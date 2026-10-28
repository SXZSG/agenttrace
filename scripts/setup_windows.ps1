$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    py -3.11 -m venv .venv
}

$python = ".\.venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
& $python -m compileall -q src tests
if ($LASTEXITCODE -ne 0) { throw "Compile check failed" }
Write-Host "AgentTrace setup and P0 checks passed." -ForegroundColor Green

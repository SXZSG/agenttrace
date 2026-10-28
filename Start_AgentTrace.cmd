@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0scripts\start_agenttrace_windows.ps1"
if errorlevel 1 (
  echo.
  echo AgentTrace failed to start. Read .agenttrace\startup.log for details.
  pause
  exit /b 1
)
endlocal

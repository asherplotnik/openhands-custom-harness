@echo off
setlocal
chcp 65001 >nul
set "BANK_AGENT_LAUNCH_DIRECTORY=%CD%"
for %%I in ("%~dp0.") do set "project_dir=%%~fI"
cd /d "%project_dir%"

REM Reads BANK_AGENT_PROVIDER and provider settings from .env.
REM Gemini and Groq both connect directly; no local bridge is started.
if "%~1"=="" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%project_dir%\scripts\start-bank-agent.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%project_dir%\scripts\start-bank-agent.ps1" %*
)

set "exit_code=%ERRORLEVEL%"
if "%exit_code%"=="3" (
  echo.
  echo Bank Agent exited. Run Open Bank Agent.cmd again.
  cmd /k
  goto :end
)
if not "%exit_code%"=="0" (
  echo.
  echo Bank Agent stopped with exit code %exit_code%.
  pause
)

:end
endlocal & exit /b %exit_code%

@echo off
REM ============================================================
REM  YONO 3.0 - run BACKEND (FastAPI)
REM  Double-click this file, or run it from a terminal.
REM
REM  The backend runs in THIS window. Press Ctrl+C to stop it.
REM  Start the frontend separately (run_react.bat).
REM ============================================================
setlocal enabledelayedexpansion

REM ---------- config ----------
REM Ports live in ports.config (single source of truth); edit them there.
set "BACKEND_PORT=8000"
if exist "%~dp0ports.config" (
  for /f "usebackq eol=# tokens=1,2 delims==" %%A in ("%~dp0ports.config") do (
    if /i "%%A"=="BACKEND_PORT" set "BACKEND_PORT=%%B"
  )
) else (
  echo [!] ports.config not found next to this script -- using default backend port 8000.
)
REM Onboarding engine: "rule_based" = deterministic (no live Ollama needed for
REM onboarding); "llm" = use Ollama with rule-based fallback. FinGuru always
REM uses Ollama regardless of this setting.
set "ONBOARDING_ENGINE_MODE=rule_based"
REM -----------------------------------------------------

REM Work from the repo root (the folder this script lives in).
cd /d "%~dp0"

REM If the chosen port is already in use, fall back to 8001.
netstat -ano | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [!] Port %BACKEND_PORT% is already in use -- switching backend to 8001.
  set "BACKEND_PORT=8001"
)

echo.
echo === Starting backend: http://localhost:!BACKEND_PORT!  (engine=%ONBOARDING_ENGINE_MODE%) ===
echo     Press Ctrl+C to stop.
python -m uvicorn backend.main:app --host 0.0.0.0 --port !BACKEND_PORT!

endlocal
pause

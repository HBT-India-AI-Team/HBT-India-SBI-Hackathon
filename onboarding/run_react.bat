@echo off
REM ============================================================
REM  YONO 3.0 - run the REACT frontend (frontend\app, Vite 8)
REM  Double-click this file, or run it from a terminal.
REM  Opens the app in your browser (Vite dev server, default
REM  http://localhost:5173). Press Ctrl+C here to stop.
REM
REM  FRONTEND ONLY - start the backend separately; it must be
REM  reachable at the VITE_API_BASE URL below.
REM
REM  REQUIRES Node 20.19+ or Node 22+ (Vite 8 / rolldown).
REM ============================================================
setlocal enabledelayedexpansion

REM ---- load ports from ports.config (single source of truth) ----
set "BACKEND_PORT=8080"
set "FRONTEND_PORT=5173"
if exist "%~dp0ports.config" (
  for /f "usebackq eol=# tokens=1,2 delims==" %%A in ("%~dp0ports.config") do (
    if /i "%%A"=="BACKEND_PORT" set "BACKEND_PORT=%%B"
    if /i "%%A"=="FRONTEND_PORT" set "FRONTEND_PORT=%%B"
  )
) else (
  echo [X] ports.config not found next to this script -- using defaults ^(backend 8000, frontend 5173^).
)

REM Derived from BACKEND_PORT above; overrides frontend\app\.env.
set "VITE_API_BASE=http://localhost:!BACKEND_PORT!"
REM FRONTEND_PORT is read by vite.config.js (server.port).
REM ------------------------------------------------------

REM --- make sure Node is installed ---
where node >nul 2>&1
if errorlevel 1 (
  echo [X] Node.js was not found on PATH.
  echo     Install Node 22 LTS from https://nodejs.org and re-run this file.
  pause
  exit /b 1
)

REM --- check Node version is new enough (need 20.19+ or 22+) ---
for /f "tokens=1,2 delims=." %%a in ('node -v') do (
  set "NODE_MAJOR=%%a"
  set "NODE_MINOR=%%b"
)
set "NODE_MAJOR=!NODE_MAJOR:v=!"
set "NODE_OK="
if !NODE_MAJOR! GEQ 22 set "NODE_OK=1"
if !NODE_MAJOR! EQU 20 if !NODE_MINOR! GEQ 19 set "NODE_OK=1"
if !NODE_MAJOR! EQU 21 set "NODE_OK=1"
if not defined NODE_OK (
  echo [X] This app ^(Vite 8^) needs Node 20.19+ or Node 22+, but you have:
  node -v
  echo     Install Node 22 LTS from https://nodejs.org, then re-run this file.
  pause
  exit /b 1
)

cd /d "%~dp0frontend\app"

if not exist "node_modules" (
  echo Installing dependencies ^(first run only^)...
  call npm install
  if errorlevel 1 (
    echo [X] npm install failed. See the error above.
    pause
    exit /b 1
  )
)

echo.
echo === Starting React (Vite) dev server on port !FRONTEND_PORT! -- API_BASE=!VITE_API_BASE! ===
echo     A browser tab will open automatically; press Ctrl+C to stop.
call npm run dev -- --open --port !FRONTEND_PORT!

endlocal
pause

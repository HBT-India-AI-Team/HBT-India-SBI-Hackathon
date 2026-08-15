@echo off
REM One-time (or re-run-anytime) client setup: create a venv, install the
REM lightweight client dependencies, and create client\.env from the example
REM if it doesn't exist yet. Run this on your Windows laptop.
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "VENV_DIR=.venv"

if not exist "%VENV_DIR%" (
    call :pick_python
    if errorlevel 1 exit /b 1
    echo [client setup] creating venv with !PYTHON_CMD! at %VENV_DIR% ...
    !PYTHON_CMD! -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [client setup] venv creation failed 1>&2
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

echo [client setup] installing dependencies ...
python -m pip install --upgrade pip -q
pip install -r requirements.txt
if errorlevel 1 (
    echo [client setup] dependency install failed 1>&2
    exit /b 1
)

if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo [client setup] created client\.env -- edit it now:
    echo     YONO_SERVER_URL      the LAN base URL the server printed on startup
    echo     YONO_SERVER_API_KEY  copy from the server's own .env
) else (
    echo [client setup] .env already exists, leaving it as-is
)

echo [client setup] done. Try: run_live_call.bat
exit /b 0

:pick_python
where py >nul 2>nul
if !errorlevel! EQU 0 (
    py -3.12 -c "1" >nul 2>nul
    if !errorlevel! EQU 0 (
        set "PYTHON_CMD=py -3.12"
        exit /b 0
    )
    py -3.11 -c "1" >nul 2>nul
    if !errorlevel! EQU 0 (
        set "PYTHON_CMD=py -3.11"
        exit /b 0
    )
    py -3 -c "1" >nul 2>nul
    if !errorlevel! EQU 0 (
        set "PYTHON_CMD=py -3"
        exit /b 0
    )
)
where python >nul 2>nul
if !errorlevel! EQU 0 (
    set "PYTHON_CMD=python"
    exit /b 0
)
echo error: no Python found -- install Python 3.11 or 3.12 from python.org 1>&2
exit /b 1

@echo off
REM Start a live voice call: streams your mic to the server and plays back
REM replies. Runs setup.bat automatically if the venv is missing.
REM Usage: run_live_call.bat [--list-devices] [--input-device N] [--output-device N]
setlocal

cd /d "%~dp0"

if not exist ".venv" (
    echo [run_live_call] no venv found, running setup.bat first ...
    call setup.bat
    if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
python live_call.py %*
exit /b %errorlevel%

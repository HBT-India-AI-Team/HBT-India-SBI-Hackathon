@echo off
REM Send text to the server and save/play the synthesized audio.
REM Runs setup.bat automatically if the venv is missing.
REM Usage: run_synthesize.bat --text "hello" --language ta --play
setlocal

cd /d "%~dp0"

if not exist ".venv" (
    echo [run_synthesize] no venv found, running setup.bat first ...
    call setup.bat
    if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
python synthesize_text.py %*
exit /b %errorlevel%

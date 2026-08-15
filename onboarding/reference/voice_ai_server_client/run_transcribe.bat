@echo off
REM Upload a local audio file and print the transcript.
REM Runs setup.bat automatically if the venv is missing.
REM Usage: run_transcribe.bat --file sample.wav [--language ta]
setlocal

cd /d "%~dp0"

if not exist ".venv" (
    echo [run_transcribe] no venv found, running setup.bat first ...
    call setup.bat
    if errorlevel 1 exit /b 1
)

call ".venv\Scripts\activate.bat"
python transcribe_file.py %*
exit /b %errorlevel%

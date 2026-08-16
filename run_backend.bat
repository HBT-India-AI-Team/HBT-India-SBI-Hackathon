@echo off
rem Runs run_backend.ps1, which starts uvicorn and tees output to uvicorn_out.log
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_backend.ps1"

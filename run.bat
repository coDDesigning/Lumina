@echo off
title Lumina Launcher
echo Starting Lumina...
python run_local.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo If python command failed, trying with .venv...
    .\.venv\Scripts\python.exe run_local.py %*
)
pause

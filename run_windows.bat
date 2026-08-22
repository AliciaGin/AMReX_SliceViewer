@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python 3 was not found. Please install Python 3.10 or newer.
        pause
        exit /b 1
    )
    set "PYTHON=py -3"
)

%PYTHON% main.py
if errorlevel 1 pause
endlocal

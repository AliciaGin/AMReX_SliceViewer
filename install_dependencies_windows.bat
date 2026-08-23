@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3 was not found. Please install Python 3.10 or newer first.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo CPU dependencies installed.
echo For optional NVIDIA GPU support, run:
echo   python -m pip install -r requirements-gpu.txt
pause
endlocal

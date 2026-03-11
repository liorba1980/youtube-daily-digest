@echo off
title YouTube Daily Digest
echo.
echo  ============================================
echo   YouTube Daily Digest - Starting...
echo  ============================================
echo.

:: Check if venv exists, create if not
if not exist "venv\" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    echo.
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install / upgrade dependencies
echo [2/3] Installing dependencies...
pip install -r requirements.txt --quiet

echo.
echo [3/3] Launching app at http://localhost:5000
echo       Press Ctrl+C to stop.
echo.

:: Open browser after 2 seconds
start "" cmd /c "timeout /t 2 >nul && start http://localhost:5000"

python app.py

pause

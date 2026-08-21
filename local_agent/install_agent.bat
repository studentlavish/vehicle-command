@echo off
setlocal
cd /d "%~dp0"

echo === Vashu Local Camera Agent — Windows installer ===

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10+ from https://www.python.org/downloads/ and re-run.
  exit /b 1
)

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv || goto :err
)

call .venv\Scripts\activate.bat || goto :err
echo Upgrading pip...
python -m pip install --upgrade pip

echo Installing requirements...
pip install -r requirements.txt || goto :err

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example — edit it before starting the agent.
)

if not exist "logs" mkdir logs

echo.
echo === Installation complete ===
echo Next steps:
echo   1. Edit .env  (set VASHU_AGENT_SECRET and CAMERA_SOURCE)
echo   2. Run start_agent.bat
exit /b 0

:err
echo Installation failed.
exit /b 1

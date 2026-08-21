@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Virtual env not found. Run install_agent.bat first.
  exit /b 1
)

call .venv\Scripts\activate.bat
if not exist "logs" mkdir logs

echo Starting Vashu Local Camera Agent — press Ctrl+C to stop.
python agent.py %*

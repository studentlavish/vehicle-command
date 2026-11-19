@echo off
rem Installs the camera agent as a Windows service that starts at boot and
rem restarts itself if it ever stops. Run from an Administrator command prompt.
rem
rem Needs NSSM (https://nssm.cc) — put nssm.exe on PATH or next to this file.
rem Usage:   install_service.bat            install + start
rem          install_service.bat remove     stop + uninstall
setlocal
cd /d "%~dp0"
set SERVICE=VashuCameraAgent

where nssm >nul 2>nul
if errorlevel 1 (
  if exist "%~dp0nssm.exe" (
    set "PATH=%~dp0;%PATH%"
  ) else (
    echo nssm.exe not found. Download it from https://nssm.cc and put nssm.exe next to this file.
    exit /b 1
  )
)

if /i "%1"=="remove" (
  nssm stop %SERVICE% >nul 2>nul
  nssm remove %SERVICE% confirm
  exit /b %errorlevel%
)

if not exist ".venv\Scripts\python.exe" (
  echo Virtual env not found. Run install_agent.bat first.
  exit /b 1
)
if not exist ".env" (
  echo .env not found. Copy .env.example to .env and fill it in first.
  exit /b 1
)
if not exist "logs" mkdir logs

nssm install %SERVICE% "%~dp0.venv\Scripts\python.exe" "%~dp0agent.py" || goto :err
nssm set %SERVICE% AppDirectory "%~dp0"
nssm set %SERVICE% DisplayName "Vashu Camera Agent"
nssm set %SERVICE% Description "Streams showroom cameras and reads number plates for Vehicle Command."
nssm set %SERVICE% Start SERVICE_AUTO_START
rem Restart 10 s after any exit; give it 15 s to stop cleanly.
nssm set %SERVICE% AppExit Default Restart
nssm set %SERVICE% AppRestartDelay 10000
nssm set %SERVICE% AppStopMethodConsole 15000
rem Console output (the agent also writes rotating logs\agent.log itself).
nssm set %SERVICE% AppStdout "%~dp0logs\service.log"
nssm set %SERVICE% AppStderr "%~dp0logs\service.log"
nssm set %SERVICE% AppRotateFiles 1
nssm set %SERVICE% AppRotateBytes 5242880
nssm start %SERVICE% || goto :err

echo.
echo Installed and started %SERVICE%. Check:  nssm status %SERVICE%
echo Logs: %~dp0logs\agent.log
exit /b 0

:err
echo Service setup failed.
exit /b 1

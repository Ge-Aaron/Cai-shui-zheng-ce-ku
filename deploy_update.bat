@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo   ===========================================
echo    taxdb - deploy update
echo    rebuild db + upload Release + trigger deploy
echo   ===========================================
echo.

REM prefer project venv python; fall back to system python
set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set PY=python

"%PY%" scripts\deploy_update.py

echo.
echo   Press any key to close...
pause >nul

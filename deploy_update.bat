@echo off
cd /d "%~dp0"

echo.
echo   ===========================================
echo    taxdb - 一键更新发布
echo    重新生成数据库 + 上传 Release + 触发部署
echo   ===========================================
echo.

REM 优先用本项目 venv 解释器；找不到则退回系统 python
set PY="C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist %PY% set PY=python

%PY% scripts/deploy_update.py

echo.
echo   按任意键关闭窗口...
pause >nul

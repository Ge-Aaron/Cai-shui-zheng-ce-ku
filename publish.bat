@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM Locate Python: prefer WorkBuddy managed env, fallback to PATH.
set "PYTHON=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo.
echo   ===========================================
echo    taxdb - push code to GitHub
echo   ===========================================
echo.

REM Repository URL is baked in. Edit the next line to change target repo.
set "REPO_URL=https://github.com/Ge-Aaron/Cai-shui-zheng-ce-ku.git"
echo   Target repository: %REPO_URL%
echo.

REM Proxy self-heal: browser reaches GitHub via a local proxy (e.g. Clash on
REM 127.0.0.1:7897); git (libcurl) does NOT read the browser proxy, so it must
REM be set explicitly or pushes fail with "Failed to connect github.com:443".
REM Leave PROXY empty (set "PROXY=") only if your network has direct access.
set "PROXY=http://127.0.0.1:7897"
if not "%PROXY%"=="" (
    git config --global http.proxy %PROXY% >nul 2>&1
    git config --global https.proxy %PROXY% >nul 2>&1
    echo   [proxy] git http/https proxy set to %PROXY%
)

if not exist ".git" (
    echo   [init] No .git found, initializing and linking remote...
    git init -b master >nul 2>&1
)

git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

echo   [sync] Fetching remote history...
git fetch origin >nul 2>&1
git merge origin/master --allow-unrelated-histories -m "sync with remote" --no-edit >nul 2>&1

REM Ensure Git has an author identity for this repo, otherwise commit fails on
REM fresh machines / portable Git installs that have no global user.name/email.
git config user.email "taxdb@local" >nul 2>&1
git config user.name "taxdb" >nul 2>&1

echo   [stage] Adding changes (respecting .gitignore)...
git add -A

git diff --cached --quiet >nul 2>&1
if errorlevel 1 (
    git commit -m "update: %date% %time%"
)

echo   [push] Pushing to GitHub (master)...
echo   If a browser login pops up, authorize with GitHub.
echo.
"%PYTHON%" "%~dp0scripts\push_helper.py"
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo.
echo   Render will auto-redeploy if Auto-Deploy is enabled.
echo   Otherwise: Render dashboard - Manual Deploy - Deploy latest commit.
echo.
pause

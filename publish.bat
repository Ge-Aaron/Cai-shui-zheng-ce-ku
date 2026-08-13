@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo   ===========================================
echo    taxdb - push code to GitHub
echo   ===========================================
echo.

REM Repository URL is baked in. Edit the next line to change target repo.
set "REPO_URL=https://github.com/Ge-Aaron/Cai-shui-zheng-ce-ku.git"
echo   Target repository: %REPO_URL%
echo.

if not exist ".git" (
    echo   [init] No .git found, initializing and linking remote...
    git init -b master >nul 2>&1
)

git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

echo   [sync] Fetching remote history...
git fetch origin >nul 2>&1
git merge origin/master --allow-unrelated-histories -m "sync with remote" --no-edit >nul 2>&1

echo   [stage] Adding changes (respecting .gitignore)...
git add -A

git diff --cached --quiet >nul 2>&1
if errorlevel 1 (
    git commit -m "update: %date% %time%"
)

echo   [push] Pushing to GitHub (master)...
echo   If a browser login pops up, authorize with GitHub.
echo.
git push -u origin master
set "PUSH_RC=%errorlevel%"

REM Some credential helpers / Git versions return a non-zero exit code even when
REM objects were written successfully. Double-check by fetching the remote HEAD
REM and comparing it with the local HEAD.
set "PUSH_OK=0"
if "%PUSH_RC%" equ "0" set "PUSH_OK=1"
if "%PUSH_OK%" equ "0" (
    git fetch origin >nul 2>&1
    for /f "delims=" %%H in ('git rev-parse HEAD 2^>nul') do set "LOCAL_HEAD=%%H"
    for /f "delims=" %%R in ('git ls-remote origin HEAD 2^>nul') do (
        for /f "tokens=1" %%A in ("%%R") do (
            if /i "%%A"=="%LOCAL_HEAD%" set "PUSH_OK=1"
        )
    )
)

if "%PUSH_OK%" equ "0" (
    echo.
    echo   [FAILED] Push did not succeed ^(exit code %PUSH_RC%^).
    echo   Common reasons:
    echo     1^) Not logged in to GitHub (browser did not pop up / was cancelled)
    echo     2^) Network problem
    echo     3^) Diverged history - run manually: git pull origin master
    echo.
    echo   Fix the issue and run this file again.
    pause
    exit /b 1
)

echo.
echo   [OK] Code pushed successfully.
echo.
echo   Render will auto-redeploy if Auto-Deploy is enabled.
echo   Otherwise: Render dashboard - Manual Deploy - Deploy latest commit.
echo.
pause

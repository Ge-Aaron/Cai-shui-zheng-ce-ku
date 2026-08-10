@echo off
cd /d "%~dp0"

echo.
echo   ===========================================
echo    taxdb - push code to GitHub
echo   ===========================================
echo.
echo   Before running, create an EMPTY repository on GitHub.
echo   Do NOT check "Add a README file" or ".gitignore".
echo   Example URL: https://github.com/yourname/taxdb.git
echo.
set /p REPO_URL="Paste your GitHub repository URL: "

if "%REPO_URL%"=="" (
    echo [ERROR] No URL entered. Exiting.
    pause
    exit /b 1
)

git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

echo.
echo   [1/1] Pushing code to GitHub (master branch)...
echo   If a browser login pops up, authorize with GitHub.
echo.
git push -u origin master

if errorlevel 1 (
    echo.
    echo   [FAILED] Push did not succeed.
    echo   Common reasons:
    echo     1) Wrong repository URL / repository is not empty
    echo     2) Not logged in to GitHub
    echo     3) Network problem
    echo.
    echo   Fix the issue and run this file again.
    pause
    exit /b 1
)

echo.
echo   [OK] Code pushed successfully.
echo.
echo   Next steps (see DEPLOY.md for details):
echo     1) Upload data/tax_policy.db (~156MB) to a GitHub Release.
echo     2) Open https://render.com, connect this repository,
echo        set env vars TAXDB_LLM_KEY and TAXDB_DB_URL, then Deploy.
echo.
pause

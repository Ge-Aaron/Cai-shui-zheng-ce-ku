@echo off
chcp 65001 >nul
REM ============================================================
REM  taxdb 一键发布到 GitHub（你需要先自建一个【空】GitHub 仓库）
REM  用法：双击本文件，按提示粘贴你的仓库地址即可
REM ============================================================
cd /d "%~dp0"

echo.
echo  =========================================================
echo   taxdb 一键推送代码到 GitHub
echo  =========================================================
echo.
echo  请先在 GitHub 新建一个【空仓库】（不要勾选 README/.gitignore）
echo  然后复制它的地址，形如：
echo    https://github.com/你的用户名/taxdb.git
echo.
set /p REPO_URL="请粘贴你的 GitHub 仓库地址: "

if "%REPO_URL%"=="" (
    echo [错误] 未输入地址，已取消。
    pause
    exit /b 1
)

REM 设置远程仓库（已存在则覆盖）
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

echo.
echo  [1/2] 推送代码到 GitHub（master 分支）...
echo  若弹出浏览器登录，请用你的 GitHub 账号授权。
echo.
git push -u origin master

if errorlevel 1 (
    echo.
    echo  [失败] 推送未成功。常见原因：
    echo   1) 仓库地址填错 / 仓库不是空的
    echo   2) 未登录 GitHub（请先在浏览器登录，或配置 Git 凭据）
    echo   3) 网络问题
    echo.
    echo  请解决后重新双击本文件。
    pause
    exit /b 1
)

echo.
echo  [2/2] 代码已推送成功 ✓
echo.
echo  =========================================================
echo   接下来还需你手动做 2 步（详见 DEPLOY.md）：
echo  =========================================================
echo.
echo  ① 上传数据库：把 data/tax_policy.db (约156M) 传到该仓库的
echo     Release，复制它的下载直链。
echo.
echo  ② 打开 https://render.com → New → Blueprint → 连该仓库
echo     环境变量填：TAXDB_LLM_KEY=你的硅基流动key
echo                   TAXDB_DB_URL=上面复制的数据库下载直链
echo     点 Deploy，等几分钟拿到公网链接。
echo.
echo  完成后别人就能通过该链接直接访问了。
echo.
pause

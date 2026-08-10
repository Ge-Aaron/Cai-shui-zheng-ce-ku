# 发布到云端（让别人通过链接访问）

目标：把 taxdb 部署成 24/7 公网服务，把链接发给同事/客户，他们的电脑直接打开就能用。

---

## 一、我已经在本地帮你做好的（你不用管）

- ✅ 代码已 `git commit` 到 `master` 分支（共 20 个文件：后端 `scripts/`、前端 `web/`、部署配置）
- ✅ **数据库(156M)、`.env`(密钥)、`nssm.exe` 已全部排除，不会进仓库、不会泄露**
- ✅ 新增 `scripts/fetch_db.py`：云端构建时自动下载数据库（绕开 GitHub 100M 单文件限制，无需 Git LFS）
- ✅ `render.yaml` 改造：构建时先拉数据库、区域选 `singapore`（离国内近）、云端不弹浏览器
- ✅ `.env.example` 环境变量模板（无真实 key）

---

## 二、你需要完成的（因为要你的 GitHub / Render 账号，我无法替你登录）

### 第 1 步：GitHub 账号
没有就去 https://github.com 免费注册一个。

### 第 2 步：建空仓库 + push 代码
1. GitHub 网页 → New repository → 名字随意（如 `taxdb`）→ **建议选 Public**（政策数据本就公开，且无任何密钥进仓库）→ 不要勾 README → Create。
2. 在你**自己电脑的 PowerShell / CMD**（不是 WorkBuddy 沙箱）里执行：

```powershell
cd C:\Users\Administrator\WorkBuddy\2026-08-06-16-20-04\taxdb
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

> push 时若要求登录：GitHub 现已不支持密码，请用 **Personal Access Token（PAT）** 当密码（token 在 GitHub → Settings → Developer settings → PAT 生成，勾 `repo` 权限）。或用 `gh auth login` 登录。

### 第 3 步：上传数据库，拿到下载链接
把本地 `data/tax_policy.db`（~156M）上传到该仓库的 **Release**：
- 仓库页 → Releases → Draft a new release → 随便填个 Tag（如 `db-v1`）→ 把 `tax_policy.db` 拖进去 → Publish release。
- 发布后点该文件的 **Download** 按钮，复制地址栏 URL（形如 `https://github.com/<用户>/<仓库>/releases/download/db-v1/tax_policy.db`）。

> 公开仓库：这个 URL 直接可下载，第 4 步直接用。
> 私有仓库：需带 token，把 URL 里的 `github.com` 换成 `你的token@github.com`，或在 Render 里额外填 `TAXDB_DB_TOKEN`。

### 第 4 步：Render 部署
1. 打开 https://render.com 注册（可用 GitHub 账号登录）。
2. New → **Web Service** → 连你的 GitHub 仓库。
3. 环境变量（Environment）填这三项：
   | Key | Value |
   |---|---|
   | `TAXDB_LLM_KEY` | 你的硅基流动 API key（真实值，只在控制台填） |
   | `TAXDB_DB_URL` | 第 3 步复制的数据库下载 URL |
   | `TAXDB_DB_TOKEN` | 仅私有仓库需要：你的 GitHub PAT |
4. 其它变量（`TAXDB_LLM_URL` / `MODEL` / `EMBED_MODEL` / `NO_BROWSER`）已在 `render.yaml` 里配好，不用填。
5. 点 **Deploy**。约 2–5 分钟后状态变绿。
6. 拿到公网地址：`https://taxdb-xxxx.onrender.com`

### 第 5 步：分享
把这个 URL 发给任何人，他们的电脑/手机直接打开即可查政策、问 AI。

---

## 三、注意事项
- **免费层限制**：15 分钟无访问会休眠，首次打开慢几秒（冷启动），之后正常。
- **密钥安全**：`TAXDB_LLM_KEY` 只存在于 Render 控制台环境变量，永不进代码/仓库。
- **数据公开**：政策库是公开信息，公开仓库无隐私问题；如介意可建私有仓库并按上面配 token。
- **不想用 Render？** Railway 流程类似（`railway up` 直接部署当前目录，无需先建 GitHub 仓库），但需注册 Railway 账号。
- **本地仍可用**：本地 `taxdb` 服务照常（Windows 服务开机自启），云端只是多一个公网入口。

---

## 四、需要更新代码时
改完代码后，在你本机终端：
```powershell
cd C:\Users\Administrator\WorkBuddy\2026-08-06-16-20-04\taxdb
git add -A && git commit -m "update" && git push
```
Render 会自动重新部署（需在 Render 里开启 Auto-Deploy，默认开）。

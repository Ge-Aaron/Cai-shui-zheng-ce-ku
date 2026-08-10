# taxdb 发布到公网（GitHub + Render）手把手教程

本教程假设你**第一次用 GitHub**。每一步都标了「在哪点、填什么」，跟着做大约 20 分钟（大部分是等上传 / 部署，不用一直盯着）。

最终效果：拿到一个 `https://taxdb-xxxx.onrender.com` 公网链接，发给任何人，他们的电脑 / 手机直接打开就能查政策、用 AI 场景解答，和你本机一模一样。

---

## 你提前需要准备的东西
1. 一个能收邮件的邮箱（注册 GitHub、Render 用）
2. 你的**硅基流动 API key**（就是之前配在 `.env` 里的 `TAXDB_LLM_KEY` 的值；在 https://cloud.siliconflow.cn 的账号里能找到）
3. 这台电脑能上网

> 本项目目录就在这台机器上：
> `C:\Users\Administrator\WorkBuddy\2026-08-06-16-20-04\taxdb`
> 下面所有「在本机操作」都是在这个目录上做。

---

## 第 1 步：注册 GitHub（免费）
1. 浏览器打开 https://github.com
2. 点右上角 **Sign up**（注册）
3. 填：邮箱 → 设密码 → 取一个**用户名（username）**→ 通过人机验证 → 点 **Create account**
   - ⚠️ 这个用户名后面经常要用，先记下来
4. 去邮箱收验证邮件，点里面的验证链接
5. 注册完成，你有了 GitHub 账号。

---

## 第 2 步：在本机安装 Git（只需装一次）
> 要把代码传到 GitHub，本机需要 Git 工具。已经装过的（开始菜单能搜到 "Git Bash"）可跳过。

1. 下载：浏览器打开 https://git-scm.com/download/win ，会自动下载 `Git-xxx-64-bit.exe`
2. 双击它安装 → 一路点 **Next（下一步）** 即可，**全部用默认选项**
3. 装完验证：按 `Win` 键，输入 `Git Bash`，能搜到该程序 = 成功

---

## 第 3 步：在 GitHub 建一个空仓库
1. 登录 GitHub，点右上角 **+** 号 → 选 **New repository**（新建仓库）
2. 填写：
   - **Repository name（仓库名）**：填 `taxdb`（英文，记下来）
   - **Description**：留空即可
   - 选 **Public**（公开。政策数据本就公开，且仓库里没有任何密钥，安全）
   - ⚠️ **不要**勾 "Add a README file"、**不要**勾 "Add .gitignore"、**不要**勾 License
   - 其余保持默认
3. 点绿色 **Create repository**
4. 创建后页面会显示仓库地址，形如：
   `https://github.com/<你的用户名>/taxdb.git`
   👉 **复制这个地址**（点地址右侧复制按钮），第 4 步要用

---

## 第 4 步：把代码一键推送到 GitHub
1. 打开本机文件管理器，进入：
   `C:\Users\Administrator\WorkBuddy\2026-08-06-16-20-04\taxdb`
2. 在该目录里找到 **`publish.bat`**，**双击它**
3. 弹出黑色窗口，提示「粘贴 GitHub 仓库地址」：
   - 右键粘贴（或 `Ctrl+V`）刚才复制的地址，按回车
4. 接下来二选一：
   - **情况 A（推荐）**：弹出浏览器让你登录 GitHub → 用账号登录并授权即可
   - **情况 B**：没弹浏览器、黑窗要求输入用户名 / 密码：
     - 用户名 = 你的 GitHub 用户名
     - 密码 = **不是账号密码**，是一串 token。去 GitHub 网页生成：
       1. 右上角头像 → **Settings**
       2. 左侧最底下 **Developer settings** → **Personal access tokens** → **Tokens (classic)**
       3. 点 **Generate new token (classic)**
       4. Note 随便填（如 `taxdb`）；Expiration 选 **No expiration**（或 90 days）
       5. **只勾选 `repo` 这一项**，其它都不勾
       6. 最底下点 **Generate token**
       7. 复制生成的那串 `ghp_xxxxxxxx` 密令，当作「密码」粘进黑窗
5. 等待推送，窗口显示「代码已推送成功 ✓」即完成
6. 回 GitHub 仓库网页刷新，能看到 `scripts/`、`web/`、`render.yaml` 等一堆文件 = 成功

> 习惯用命令行的，也可不用 publish.bat，在该目录打开 Git Bash 执行：
> ```
> git remote add origin https://github.com/<用户名>/taxdb.git
> git push -u origin master
> ```
> 注意分支名是 **master**（别改成 main，否则和已提交内容对不上）。

---

## 第 5 步：把数据库上传到 Release（拿到下载直链）
> 数据库约 156MB，不能进代码（GitHub 限制单文件 ≤100MB），所以单独传到 Release，让云端部署时自动下载。

1. GitHub 仓库页面，点右侧 **Releases**（或上方导航栏）
2. 点 **Draft a new release**（新建发布）
3. **Choose a tag**：输入 `db-v1`，回车
4. **Title**（标题）：随便填，如 `database`
5. 把本机文件拖进 "Attach binaries..." 区域：
   `C:\Users\Administrator\WorkBuddy\2026-08-06-16-20-04\taxdb\data\tax_policy.db`
   - 156MB 上传要一点时间，等进度条走完
6. 最底下点 **Publish release**（发布）
7. 发布后，在 Release 页面找到刚上传的 `tax_policy.db`，点它的 **Download**（下载）按钮
8. 浏览器地址栏出现 URL，形如：
   `https://github.com/<用户名>/taxdb/releases/download/db-v1/tax_policy.db`
   👉 **完整复制这个 URL**（它就是第 6 步的 `TAXDB_DB_URL`）
   - 验证：把这个 URL 单独粘到浏览器地址栏能下载 = 正确

---

## 第 6 步：在 Render 部署
1. 打开 https://render.com ，点 **Sign Up** → 选 **Sign up with GitHub**（用 GitHub 账号一键登录最方便）
2. 进入控制台，点右上角 **New** → **Blueprint**
   > 选 Blueprint 是因为仓库里已有 `render.yaml`，Render 会自动读取里面的配置（构建、启动、区域、大部分变量都配好了）
3. 若提示授权连 GitHub，允许；在仓库列表里找到 `taxdb` → 点 **Connect**
4. Render 读取 render.yaml 后显示配置预览，直接点 **Apply**
5. 部署前需手动填 **2 个**变量（其余已自动配好）：
   - 在 **Environment** 区域添加下面两行（Key / Value）：
     | Key | Value |
     |---|---|
     | `TAXDB_LLM_KEY` | 你的硅基流动 API key（真实值，只在这里填，绝不进代码） |
     | `TAXDB_DB_URL` | 第 5 步复制的数据库下载 URL |
   - （只有私有仓库才需额外填 `TAXDB_DB_TOKEN`，我们用 Public，不用填）
6. 点 **Deploy taxdb**（或 Create Web Service）
7. 等待部署：约 2–8 分钟（要下载 156MB 数据库 + 装依赖），进度在 **Logs** 里可见
8. 状态变绿（Live）后，Render 给你公网地址，形如：
   `https://taxdb-xxxx.onrender.com`
   👉 这就是你要的公网链接

---

## 第 7 步：分享
把这个 `https://taxdb-xxxx.onrender.com` 发给任何人，他们电脑 / 手机浏览器打开，就能查政策、用 AI 场景解答，和你本机一样。

---

## 常见问题 / 排查
- **第一次打开很慢 / 偶尔打不开**：Render 免费层 15 分钟无人访问会休眠，首次打开需等几秒冷启动，之后正常。
- **场景解答报错 / 显示没数据**：多半是 `TAXDB_DB_URL` 填错或数据库没传成功。把该 URL 单独粘到浏览器，能下载 = 正确。
- **部署失败**：看 Render 的 **Deploy Logs** 里红色报错，把报错贴给我。
- **密钥安全**：`TAXDB_LLM_KEY` 只存在于 Render 控制台环境变量，永不进代码 / 仓库。
- **内存不够崩溃（少见）**：免费层 512MB 内存，156MB 数据库 + 向量索引偏紧。若频繁崩溃，可升级 Render 付费档，或改用自备云服务器。
- **本地照常可用**：本机 taxdb 服务（Windows 服务开机自启）不受影响，云端只是多一个入口。

---

## 以后更新代码
改完代码后，双击 `publish.bat` 重新推送，Render 会自动重新部署（默认开启 Auto-Deploy）。

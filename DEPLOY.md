# 财税政策数据库 · 云端部署指引（不开机也能访问）

本项目是**前后端一体的单进程 Python 服务**：`scripts/server.py` 同时托管 `web/` 前端
（同源访问）并提供 `/api/*` 接口。因此一个 Python 进程即可对外提供完整网站，
非常适合 Render / Railway 这类免费 PaaS。

部署后你会得到一个公网 URL（如 `https://taxdb.onrender.com`），本地电脑关机/不开机也能打开。

---

## 一、准备工作

1. 安装 Git LFS（数据库约 156M，超过 GitHub 单文件 100M 限制，必须用 LFS）：
   ```bash
   git lfs install
   git lfs track "data/tax_policy.db"
   ```
2. 把本目录初始化为 Git 仓库并推到 **GitHub 私有仓库**：
   ```bash
   git init
   git add -A
   git commit -m "taxdb deploy"
   git remote add origin <你的私有仓库地址>
   git push -u origin main
   ```
   `.env` 已被 `.gitignore` 排除，**真实 API key 永远不会进仓库**。
   记得把仓库里现有的 `.env` 删掉或确保它未被提交（检查：`git ls-files | grep .env`）。

## 二、在 Render 部署（推荐，免费层）

1. 打开 https://render.com ，用 GitHub 登录。
2. New → Web Service → 选择刚才的私有仓库。
3. 配置：
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python scripts/server.py`
   - **Plan**: Free
   - **Health Check Path**: `/api/stats`
4. 在 **Environment** 里添加变量（务必手动填，**不要**写进代码）：
   - `TAXDB_LLM_URL` = `https://api.siliconflow.cn/v1`
   - `TAXDB_LLM_MODEL` = `deepseek-ai/DeepSeek-V3`
   - `TAXDB_EMBED_MODEL` = `BAAI/bge-m3`
   - `TAXDB_LLM_KEY` = `<你的硅基流动真实密钥>` ← 只在这里填
   （render.yaml 已预设前三个，`TAXDB_LLM_KEY` 标了 `sync:false`，需在控制台手填）
5. 点 Create Web Service。构建完成后 Render 分配一个 `*.onrender.com` 公网地址。

## 三、Railway 备选（免费额度，对大文件更友好）

1. 打开 https://railway.app ，GitHub 登录。
2. New Project → Deploy from GitHub repo → 选仓库。
3. 在 Variables 里填上面同样的 4 个环境变量（含真实 key）。
4. 部署自动完成，Railway 给出公网域名。Railway 对仓库大文件限制更宽松，156M 数据库通常可直接上传。

## 四、验证

打开分配的公网地址，应该能看到财税政策库首页。
再访问 `https://<你的域名>/api/stats`，返回 JSON 且 `ai_enabled: true`、`total: 5004` 即成功。

场景解答测试：`https://<你的域名>/api/ask?q=小型微利企业年应纳税所得额200万元怎么算`
应返回「应缴纳企业所得税 10 万元」。

## 五、注意事项

- **API key 安全**：key 只存在于平台控制台的环境变量里（加密存储），不进代码/仓库/镜像。
  如需轮换，直接在平台改即可。
- **数据库**：`data/tax_policy.db` 随仓库部署。免费层每次重新部署会重建容器，
  知识库(`ask_kb`)的本地新增记录会在重新部署时被覆盖；政策库本身是只读的，不受影响。
  如需知识库持久化，请挂载持久卷（Render Disk / Railway Volume，可能需付费）。
- **免费层休眠**：Render 免费 Web Service 在一段时间无访问后会休眠，首次访问需冷启动几秒，
  属正常现象。Railway 免费层通常不休眠。
- **本地使用**：云端部署不影响本地。本地仍可用 `start.bat` 启动（无黑窗）。

## 六、文件清单（部署必需）

```
taxdb/
├── scripts/server.py      # 主服务（已支持 0.0.0.0 / $PORT / 云端无浏览器）
├── scripts/db.py
├── scripts/embeddings.py
├── scripts/keepalive.py
├── web/                   # 前端
├── data/tax_policy.db     # 数据库（Git LFS）
├── requirements.txt       # numpy
├── Procfile
├── render.yaml
├── .env.example           # 环境变量模板（无真实 key）
└── .gitignore             # 已排除 .env / __pycache__ / logs
```

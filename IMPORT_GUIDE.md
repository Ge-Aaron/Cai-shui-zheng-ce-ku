# 财税政策数据库 · 新电脑导入指南

适用于把 `taxdb-portable.zip` 迁移到另一台电脑后，在 WorkBuddy 中导入、编辑、运行、发布。

## 一、环境要求
- 已安装 WorkBuddy（本包启动脚本会自动找到其内置 Python）；若未安装 WorkBuddy，需自备 Python 3.11+ 并 `pip install -r requirements.txt`
- 系统：Windows（启动脚本为 `.bat` / `.vbs`）

## 二、解压与打开
1. 把 `taxdb-portable.zip` 解压到任意目录，例如 `D:\taxdb`
2. 打开 WorkBuddy → 「打开文件夹」→ 选择 `taxdb` 目录，即可直接编辑源码与配置

## 三、配置密钥（首次必做）
1. 复制 `taxdb/.env.example` 为 `taxdb/.env`
2. 用记事本打开 `.env`，填入：
   - `TAXDB_LLM_KEY=sk-xxx`（硅基流动 DeepSeek 的 API Key，用于「场景解答」AI 问答）
   - 其余项保持默认即可
3. 保存

> 注意：`.env` 含真实密钥，已通过 `.gitignore` 排除，绝不会进 GitHub。

## 四、本地运行
- 双击 `taxdb/start.bat` → 浏览器打开 `http://127.0.0.1:8765`
- 如需后台常驻（开机自启），以管理员身份运行 `nssm install taxdb ...`（详见 `DEPLOY.md`）

## 五、功能模块一览
- **政策检索**：多条件筛选（关键词 / 税种 / 领域 / 效力级次 / 状态 / 源站时效 / 年份 / 关注度 / 排序）
- **场景解答**：描述业务场景，AI 从全量库匹配最相关文件并给出处理要点
- **电商政策**：从全量库自动筛选「跨境电商 / 国内电商」两类，支持与「政策检索」完全一致的多条件筛选，两个列表同步筛选；点击卡片可看原文与智能解读
- **最新动态 / 时效预警 / 知识库 / 概览**：变更巡检、临期提醒、政策关系图谱等

## 六、更新数据库内容（一键）
1. 双击 `taxdb/deploy_update.bat`
2. 首次需填两个凭证（只填一次，自动存到 `.env.local`，不进 GitHub）：
   - `GITHUB_TOKEN`：GitHub → 头像 → Settings → Developer settings → Personal access tokens → Tokens (classic)，只勾 `repo`
   - `RENDER_DEPLOY_HOOK`：Render 控制台 → 你的 `taxdb` 服务 → Settings → Deploy Hook → Generate Deploy Hook
3. 选 `1` 增量更新 → 脚本自动：本地抓取+重建向量 → 覆盖上传数据库到 GitHub Release v1.0.0 → 触发 Render 重新部署
   - 选 `3` 可跳过生成、直接上传当前数据库

## 七、发布 / 推送代码（仓库地址已内置）
- 双击 `taxdb/publish.bat` → 弹出浏览器登录 GitHub 授权 → 自动 push
- Render 检测到新 commit 会自动重新部署（约 1–2 分钟）
- 新电脑首次运行会自动 `git init` 并关联远程 `https://github.com/Ge-Aaron/Cai-shui-zheng-ce-ku.git`

## 八、云端链接
- 公网：`https://taxdb-y44n.onrender.com`
- 免费层特性：15 分钟无人访问会休眠，首次打开约等 1 分钟唤醒；750 免费小时/月用完会自动暂停（不扣费）
- 云端是只读展示，数据源头在本地；改完本地 → 重新发布即可同步

## 九、常见问题
- 双击 `.bat` 报「不是内部或外部命令」：说明文件被记事本改过编码，请用原 zip 里的英文版 `.bat`
- 本地改了代码想同步到云端：双击 `publish.bat`
- 电商模块筛选项下拉为空：刷新页面或检查 `/api/facets` 是否正常返回

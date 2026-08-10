# -*- coding: utf-8 -*-
"""
taxdb 一键更新发布脚本（零第三方依赖，仅标准库）。

把「重新生成数据库 -> 上传 GitHub Release -> 触发 Render 部署」串成一步。
双击 deploy_update.bat 运行；首次会要求填入两个凭证并保存到 .env.local（绝不进 git）。

凭证（只需填一次）：
  GITHUB_TOKEN        GitHub 个人访问令牌（PAT，勾 repo 权限），用于上传数据库到 Release
  RENDER_DEPLOY_HOOK  Render 的 Deploy Hook URL，用于触发云端重新部署

设计要点：
  - 数据库走「固定 tag 覆盖」：下载地址 .../v1.0.0/tax_policy.db 永远不变，
    因此不用改 Render 的环境变量 TAXDB_DB_URL；每次覆盖上传同名资产，云端重新部署
    时由 fetch_db.py 重新下载（已加时间戳绕过 CDN 缓存）拿到最新库。
"""
import os
import sys
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
ENVLOCAL = os.path.join(ROOT, ".env.local")
ASSET_NAME = "tax_policy.db"
DB_PATH = os.path.join(ROOT, "data", "tax_policy.db")
REPO_DEFAULT = "Ge-Aaron/Cai-shui-zheng-ce-ku"
TAG_DEFAULT = "v1.0.0"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_local_env():
    d = {}
    if os.path.exists(ENVLOCAL):
        with open(ENVLOCAL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip("'\"")
    return d


def save_local_env(d):
    with open(ENVLOCAL, "w", encoding="utf-8") as f:
        for k, v in sorted(d.items()):
            f.write(f"{k}={v}\n")


def ensure_credentials(cfg):
    changed = False
    if not cfg.get("GITHUB_TOKEN"):
        print("\n[需要] GitHub 个人访问令牌（PAT），用于上传数据库到 Release：")
        print("   GitHub 网页 -> 头像 -> Settings -> Developer settings ->")
        print("   Personal access tokens -> Tokens (classic) -> Generate new token (classic)")
        print("   Note 随便填；Expiration 选 No expiration；只勾 repo 一项；生成后复制 ghp_xxx")
        while True:
            t = input("GITHUB_TOKEN: ").strip()
            if t:
                cfg["GITHUB_TOKEN"] = t
                changed = True
                break
            print("   不能为空，请重新输入")
    if not cfg.get("RENDER_DEPLOY_HOOK"):
        print("\n[需要] Render Deploy Hook（用于触发云端重新部署）：")
        print("   Render 控制台 -> 你的 taxdb 服务 -> Settings -> Deploy Hook -> Generate Deploy Hook")
        print("   复制那个 https://api.render.com/deploy/... 链接")
        while True:
            t = input("RENDER_DEPLOY_HOOK: ").strip()
            if t:
                cfg["RENDER_DEPLOY_HOOK"] = t
                changed = True
                break
            print("   不能为空，请重新输入")
    if not cfg.get("REPO"):
        cfg["REPO"] = REPO_DEFAULT
        changed = True
    if not cfg.get("TAG"):
        cfg["TAG"] = TAG_DEFAULT
        changed = True
    if changed:
        save_local_env(cfg)
        log("凭证已保存到 .env.local（已加入 .gitignore，不会上传 GitHub）")
    return cfg


def gh_api(method, url, token, data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "taxdb-deploy/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {method} 失败 {e.code}: {e.read().decode('utf-8', 'ignore')}")


def upload_asset(token, cfg):
    api = f"https://api.github.com/repos/{cfg['REPO']}/releases/tags/{cfg['TAG']}"
    rel = json.loads(gh_api("GET", api, token))
    rid = rel["id"]
    # 删除同名旧资产（GitHub 不允许同 Release 下存在同名资产）
    for a in rel.get("assets", []):
        if a["name"] == ASSET_NAME:
            log(f"删除旧资产 {ASSET_NAME} (id={a['id']})")
            gh_api("DELETE", f"https://api.github.com/repos/{cfg['REPO']}/releases/assets/{a['id']}", token)
    size_mb = os.path.getsize(DB_PATH) // 1024 // 1024
    log(f"上传 {ASSET_NAME} ({size_mb}MB) 到 Release {cfg['TAG']} ...")
    upload_url = (f"https://uploads.github.com/repos/{cfg['REPO']}/releases/{rid}"
                  f"/assets?name={ASSET_NAME}")
    if shutil.which("curl"):
        r = subprocess.run([
            "curl", "-L", "-X", "POST",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/octet-stream",
            "--data-binary", f"@{DB_PATH}",
            upload_url,
        ], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"curl 上传失败: {r.stderr}")
        out = r.stdout
    else:
        with open(DB_PATH, "rb") as f:
            data = f.read()
        req = urllib.request.Request(
            upload_url, data=data,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/octet-stream",
                     "User-Agent": "taxdb-deploy/1.0"},
            method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            out = resp.read().decode("utf-8")
    j = json.loads(out)
    log(f"上传完成 -> {j.get('browser_download_url')}")
    return j.get("browser_download_url")


def trigger_render(hook):
    log("触发 Render 重新部署 ...")
    req = urllib.request.Request(hook, data=b"", method="POST",
                                 headers={"User-Agent": "taxdb-deploy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        log("已发送部署请求。Render 将在 1-2 分钟内重新构建（会重新下载最新数据库）")
    except Exception as e:
        raise RuntimeError(f"触发部署失败：{e}（可到 Render 控制台手动 Manual Deploy）")


def regenerate(kind):
    if kind == "1":
        log("运行增量更新：抓取最新政策 + 重建向量 ...")
        return subprocess.run([PY, os.path.join(ROOT, "scripts", "update_daily.py")],
                              cwd=ROOT).returncode
    if kind == "2":
        log("全量重建向量 ...")
        return subprocess.run([PY, os.path.join(ROOT, "scripts", "build_embeddings.py")],
                              cwd=ROOT).returncode
    log("跳过生成，直接上传当前数据库")
    return 0


def main():
    print("=" * 54)
    print("  taxdb 一键更新发布：重新生成 -> 上传 Release -> 触发部署")
    print("=" * 54)
    cfg = ensure_credentials(load_local_env())
    if os.path.getsize(DB_PATH) < 1024 * 1024:
        raise RuntimeError("data/tax_policy.db 异常（过小），请确认数据库存在")
    print(f"\n数据库：{os.path.getsize(DB_PATH) // 1024 // 1024} MB")
    print("选择「重新生成」方式：")
    print("  1) 增量更新（推荐：抓取最新政策 + 重建向量）")
    print("  2) 全量重建向量（改了 analyzer/embeddings 逻辑时用）")
    print("  3) 跳过生成（已手动改好数据库，直接上传）")
    kind = input("输入 1/2/3（默认 1）：").strip() or "1"
    rc = regenerate(kind)
    if rc != 0:
        raise RuntimeError(f"生成步骤返回非零退出码 {rc}，已停止发布（云端未改动）")
    if os.path.getsize(DB_PATH) < 1024 * 1024:
        raise RuntimeError("生成后数据库过小，疑似失败，已停止发布")
    upload_asset(cfg["GITHUB_TOKEN"], cfg)
    trigger_render(cfg["RENDER_DEPLOY_HOOK"])
    print("\n" + "=" * 54)
    print("  全部完成！稍后打开 https://taxdb-y44n.onrender.com")
    print("  即可看到更新后的数据（首次约等 1-2 分钟部署）")
    print("=" * 54)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        sys.exit(1)

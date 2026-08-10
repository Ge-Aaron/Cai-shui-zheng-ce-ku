#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署时下载政策数据库（绕开 Git LFS / 100M 单文件限制）。

用法：在 Render（或任意 PaaS）的 build 阶段运行：
    python scripts/fetch_db.py

环境变量：
    TAXDB_DB_URL    数据库下载地址（必需，除非本地已存在 data/tax_policy.db）
    TAXDB_DB_TOKEN  可选，私有仓库 Release 资产下载所需的 Bearer Token
                    （公开仓库 / 公开 Release 资产无需设置）

数据库本体（~156M）不进 git 仓库，由本脚本在构建时拉取。
政策数据本身为公开信息，可放公开可下载地址；API key 绝不进仓库。
"""
import os
import sys
import urllib.request
import urllib.error

DB_PATH = os.path.join("data", "tax_policy.db")


def main():
    url = os.environ.get("TAXDB_DB_URL", "").strip()
    # 本地已存在则跳过（便于本地直接运行 / 避免重复下载）
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 1_000_000:
        print(f"[fetch_db] 已存在 {DB_PATH}（{os.path.getsize(DB_PATH)//1024//1024}MB），跳过下载。")
        return 0

    if not url:
        print("[fetch_db] 未设置 TAXDB_DB_URL，且本地无数据库，退出（服务将无数据）。")
        # 不致命退出，让后续 build 暴露问题更清晰；但返回非 0 以提示
        return 2

    os.makedirs("data", exist_ok=True)
    token = os.environ.get("TAXDB_DB_TOKEN", "").strip()
    req = urllib.request.Request(url, headers={"User-Agent": "taxdb-fetch/1.0"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    print(f"[fetch_db] 下载数据库 -> {DB_PATH}")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp, open(DB_PATH, "wb") as f:
            total = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        mb = total // 1024 // 1024
        print(f"[fetch_db] 下载完成：{mb}MB")
        return 0
    except urllib.error.HTTPError as e:
        print(f"[fetch_db] HTTP 错误 {e.code}: {e.reason}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[fetch_db] 下载失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

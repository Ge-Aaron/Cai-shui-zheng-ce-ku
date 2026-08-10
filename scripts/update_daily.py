# -*- coding: utf-8 -*-
"""
财税政策数据库 · 每日自动更新编排器

执行顺序：
  1) 增量抓取  —— crawler.py --mode incr（只扫最新若干页，检测新增/变更，写入 changes 表）
  2) 解读分析  —— analyzer.py --link（新文件解读 + 挂接官方解读 + 全库时效推断）
  3) 语义向量重建（默认开启）—— build_embeddings.py --incremental
                  只把新增/正文变更的政策重新编码进 policy_vectors，使「场景解答」的
                  语义检索能覆盖当天新抓到的政策（免费额度，增量仅几十次调用）
  4) 刷新运行中的服务内存索引 —— 调用 http://127.0.0.1:8765/api/reload
                  （若查询服务未启动则跳过，下次启动会自动加载最新向量）

全库时效推断会重算每一件政策的「当前效力结论」，因此新抓到的废止文件会
自动把被它废止的旧文件状态翻转为「已废止」。

用法：
  python update_daily.py                  # 日常增量（默认，含向量重建+服务刷新）
  python update_daily.py --full           # 先全量回抓再分析（大修后兜底）
  python update_daily.py --no-embeddings  # 跳过向量重建（仅抓取+分析）

退出码：各步均成功返回 0，任一步失败返回非 0。
"""
import os
import sys
import json
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable  # 由调用方传入的 venv 解释器运行，子进程沿用同一解释器
LOG_DIR = os.path.join(ROOT, "logs")
DATA_DIR = os.path.join(ROOT, "data")


def log(msg):
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def step(name, args):
    log(f">>> 开始：{name}")
    rc = subprocess.run([PY] + args, cwd=ROOT).returncode
    if rc != 0:
        log(f"!!! {name} 返回非零退出码 {rc}")
    else:
        log(f"<<< 完成：{name}")
    return rc


def summarize():
    try:
        sys.path.insert(0, HERE)
        from db import get_conn
        c = get_conn()
        cur = c.cursor()
        new24 = cur.execute(
            "SELECT COUNT(*) FROM changes WHERE detected_at >= datetime('now','localtime','-1 day')"
        ).fetchone()[0]
        sc = {r[0] or "-": r[1] for r in cur.execute(
            "SELECT status_final,COUNT(*) FROM analysis GROUP BY status_final").fetchall()}
        c.close()
        valid = sc.get("有效", 0) + sc.get("推定有效", 0)
        dead = sc.get("已废止", 0) + sc.get("已失效", 0) + sc.get("已到期", 0)
        return (f"近24小时变更记录 {new24} 条\n"
                f"全库效力分布 → 现行在用(含推定) {valid} / 已废止·失效 {dead} / "
                f"已修改 {sc.get('已修改',0)} / 未见废止记录 {sc.get('未见废止记录',0)} / "
                f"将废止 {sc.get('将废止',0)} / 尚未生效 {sc.get('尚未生效',0)}")
    except Exception as e:
        return f"汇总失败：{e}"


def main():
    full = "--full" in sys.argv
    no_embed = "--no-embeddings" in sys.argv
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    started = datetime.datetime.now()

    log("=" * 52)
    log(f"财税政策数据库每日更新启动（模式：{'全量' if full else '增量'}"
        f"{' / 跳过向量重建' if no_embed else ''}）")
    log("=" * 52)

    rc1 = step("增量抓取 国家税务总局政策法规库 + 官方解读",
               [os.path.join(HERE, "crawler.py"), "--mode", "incr",
                "--columns", "政策法规,政策解读"])

    rc2 = step("解读分析（关系挂接 + 全库时效推断）",
               [os.path.join(HERE, "analyzer.py"), "--link"])

    # 3) 语义向量索引增量重建（默认开启）
    rc3 = 0
    reload_note = "未执行（--no-embeddings）"
    if not no_embed:
        rc3 = step("语义向量索引增量重建",
                   [os.path.join(HERE, "build_embeddings.py"), "--incremental"])
        # 4) 刷新运行中的服务进程内存索引（若服务未运行则先拉起，确保语义索引生效）
        try:
            import urllib.request
            import keepalive as ka
            if not ka.alive()[0]:
                log(">>> 查询服务未运行，先尝试拉起后再刷新…")
                if not ka.ensure_alive(timeout=15):
                    raise RuntimeError("查询服务未能成功拉起")
                log("<<< 查询服务已拉起")
            req = urllib.request.Request("http://127.0.0.1:8765/api/reload")
            with urllib.request.urlopen(req, timeout=20) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            reload_note = f"已刷新：{j}"
            log(f">>> 服务内存索引刷新：{j}")
        except Exception as e:
            reload_note = f"跳过（查询服务拉起/刷新失败）：{e}"
            log(f">>> （跳过）服务内存索引刷新未执行：{e}")

    summary = summarize()
    finished = datetime.datetime.now()
    log("=" * 52)
    log("每日更新结束")
    log(f"向量重建退出码 {rc3}；服务刷新：{reload_note}")
    log(summary)
    log(f"耗时 {round((finished - started).total_seconds(), 1)} 秒")
    log("=" * 52)

    # 写状态文件，供网页/外部监控读取
    status = {
        "finished_at": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "full" if full else "incr",
        "crawl_rc": rc1,
        "analyze_rc": rc2,
        "embed_rc": rc3,
        "reload_note": reload_note,
        "ok": (rc1 == 0 and rc2 == 0 and (rc3 == 0 or no_embed)),
        "summary": summary,
    }
    with open(os.path.join(DATA_DIR, "last_update.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

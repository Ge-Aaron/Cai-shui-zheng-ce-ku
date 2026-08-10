# -*- coding: utf-8 -*-
"""
财税政策数据库 · 查询服务保活看门狗（keepalive）

幂等逻辑：
  1. 探测 http://127.0.0.1:<PORT>/api/stats 是否可达（HTTP 200 即视为存活）
  2. 已存活 → 不做任何动作，直接返回
  3. 不存活 → 用当前 Python 解释器后台拉起 scripts/server.py（脱离父进程常驻），
     并等待最多 ~15s 确认恢复；仍不恢复则报告失败

可直接运行（python keepalive.py）供自动化调用，也提供 alive()/ensure_alive()/launch()
供 update_daily.py 等复用。

用法：
  python scripts/keepalive.py
  python scripts/keepalive.py --timeout 20
"""
import os
import sys
import time
import json
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(HERE, "server.py")
LOG = os.path.join(ROOT, "data", "server.log")
PORT = int(os.environ.get("TAXDB_PORT", "8765"))
HOST = "127.0.0.1"


def alive():
    """返回 (是否存活, 详情)。HTTP /api/stats 200 视为存活。"""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/stats", timeout=4) as r:
            if r.status == 200:
                return True, None
    except Exception as e:
        return False, repr(e)[:120]
    return False, "non-200"


def launch():
    """后台静默拉起查询服务，脱离父进程常驻且不弹控制台窗口。返回子进程 pid。"""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    # Windows 下让子进程彻底脱离当前控制台与作业(job)，避免父进程（含调用它的
    # 工具/自动化进程）退出时被作业对象连带回收。CREATE_BREAKAWAY_FROM_JOB 是关键：
    # 沙箱把工具调用放进一个 job，普通 DETACHED_PROCESS 仍会被 job 终止，必须 break away。
    # CREATE_NO_WINDOW + STARTUPINFO(SW_HIDE) 保证不弹出可见的黑窗。
    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    with open(LOG, "ab") as lf:
        proc = subprocess.Popen(
            [sys.executable, "-u", SERVER],
            cwd=ROOT,
            stdout=lf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            startupinfo=startupinfo,
        )
    return proc.pid


def ensure_alive(timeout=15):
    """若已存活直接返回 True；否则拉起并等待恢复。"""
    if alive()[0]:
        return True
    launch()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if alive()[0]:
            return True
        time.sleep(1)
    return False


def main():
    timeout = 15
    if "--timeout" in sys.argv:
        try:
            idx = sys.argv.index("--timeout")
            timeout = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            pass

    was, detail = alive()
    if was:
        out = {"was_alive": True, "action": "none", "now_alive": True,
               "pid": None, "detail": detail}
        print(json.dumps(out, ensure_ascii=False))
        return 0

    pid = None
    try:
        pid = launch()
    except Exception as e:
        print(json.dumps({"was_alive": False, "action": "launch_failed",
                          "now_alive": False, "error": repr(e)[:200]},
                         ensure_ascii=False))
        return 1

    ok = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if alive()[0]:
            ok = True
            break
        time.sleep(1)

    out = {"was_alive": False, "action": "restart" if ok else "failed",
           "now_alive": ok, "pid": pid}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

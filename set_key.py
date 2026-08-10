# -*- coding: utf-8 -*-
"""
安全写入 AI 密钥到 .env（不会把密钥显示在屏幕上，也不进命令行历史）。
用法（在 taxdb 目录下执行）：
    python set_key.py
然后按提示粘贴你的智谱 / 硅基流动 APIKey 即可。
也可指定服务商（仅作提示）：python set_key.py --provider zhipu
"""
import os
import re
import sys
import getpass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def main():
    provider = "智谱(默认)"
    if "--provider" in sys.argv:
        i = sys.argv.index("--provider")
        if i + 1 < len(sys.argv):
            provider = sys.argv[i + 1]

    if not os.path.exists(ENV_PATH):
        print("未找到 .env，请先确认在 taxdb 目录下运行。")
        return

    print(f"当前要写入的密钥将用于：{provider}")
    print("（粘贴后回车即可，输入内容不会回显）")
    try:
        key = getpass.getpass("请粘贴你的 APIKey：").strip()
    except Exception:
        key = input("请粘贴你的 APIKey：").strip()

    if not key:
        print("未输入密钥，已取消。")
        return

    with open(ENV_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 只替换生效中的 TAXDB_LLM_KEY= 行（保留注释块不动）
    new_content, n = re.subn(
        r"^TAXDB_LLM_KEY=.*$",
        f"TAXDB_LLM_KEY={key}",
        content,
        count=1,
        flags=re.M,
    )
    if n == 0:
        # 兜底：没有生效行就追加
        new_content = content.rstrip() + f"\nTAXDB_LLM_KEY={key}\n"

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print("✅ 密钥已写入 .env（TAXDB_LLM_KEY）。")
    print("下一步：重启服务 —— 关掉当前服务窗口，重新运行 start.bat 或 python scripts/server.py")
    print("启动日志出现 '[AI] 已启用' 即表示接入成功。")


if __name__ == "__main__":
    main()

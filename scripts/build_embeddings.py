# -*- coding: utf-8 -*-
"""
为全库政策生成语义向量索引。
运行：
  python scripts/build_embeddings.py              # 全量重建（首次或政策大改后）
  python scripts/build_embeddings.py --incremental  # 仅嵌入新增/变更的政策（每日更新用）
向量存入 data/tax_policy.db 的 policy_vectors 表（base64 编码的 float32 向量）。
"""
import os
import sys
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true",
                   help="只嵌入 policy_vectors 中缺失或正文变更过的政策")
    args = ap.parse_args()
    import embeddings
    embeddings.build_index(incremental=args.incremental)

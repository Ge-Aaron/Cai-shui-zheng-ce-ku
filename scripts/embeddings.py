# -*- coding: utf-8 -*-
"""
财税政策库 - 语义向量检索（RAG 核心）
================================================================
思路：把每条政策用中文嵌入模型编码成向量，存入本地 SQLite（policy_vectors）。
查询时把问题也编码成向量，做余弦相似度召回最相关的政策，再交给大模型生成方案。
这样模型看到的是"意思最贴合"的政策，而非只能靠关键词字面匹配。

嵌入服务：复用 .env 里的硅基流动密钥（TAXDB_LLM_KEY / TAXDB_LLM_URL），
调用其 /v1/embeddings 接口（默认模型 BAAI/bge-m3，1024 维）。
整库只需离线嵌入一次；政策不变无需重做。numpy 负责内存里的快速余弦计算。
"""
import os
import sys
import json
import base64
import sqlite3
import urllib.request
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from db import DB_PATH, jload  # noqa: E402

ENV_FILE = os.path.join(os.path.dirname(HERE), ".env")

# 单条政策送入嵌入的文本上限（字符），保证语义覆盖又不过长
TEXT_LIMIT = 1200
# 召回候选数（语义阶段多取一些，再与状态/文号加权共同排序）
RECALL_K = 60
# 内存索引缓存（进程级，避免每次查询都读库）
_INDEX_CACHE = None


def load_env():
    """读取项目根目录 .env，把键值注入 os.environ（不覆盖已有键）。"""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k:
                continue
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if k not in os.environ:
                os.environ[k] = v


def _embed_one_batch(texts, key, base, model):
    """调用一次 embedding 接口，返回与输入顺序一致的向量列表。"""
    endpoint = base.rstrip("/") + "/embeddings"
    data = {"model": model, "input": texts}
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(data).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            items = j.get("data") or []
            # 按接口返回的 index 字段还原顺序（保险）
            by_idx = {}
            for it in items:
                i = it.get("index", len(by_idx))
                by_idx[i] = it.get("embedding")
            return [by_idx[i] for i in range(len(texts))]
        except Exception as e:  # 超时/网络抖动/5xx 自动重试
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"embedding 调用失败：{last_err}")


def embed_texts(texts, key=None, url=None, model=None):
    """批量把文本列表编码为向量（list[list[float]]）。"""
    load_env()
    key = key or os.environ.get("TAXDB_EMBED_KEY") or os.environ.get("TAXDB_LLM_KEY")
    base = url or os.environ.get("TAXDB_EMBED_URL") or os.environ.get("TAXDB_LLM_URL")
    model = model or os.environ.get("TAXDB_EMBED_MODEL") or "BAAI/bge-m3"
    if not key or not base:
        raise RuntimeError("未配置 embedding 密钥（TAXDB_LLM_KEY / TAXDB_EMBED_KEY）")
    out = []
    B = 32
    for i in range(0, len(texts), B):
        out.extend(_embed_one_batch(texts[i:i + B], key, base, model))
    return out


def compose_text(row):
    """把一条政策的标题/文号/摘要/正文前段/结构化要点(规则条文)拼成用于嵌入的文本。

    关键改进：把 analyzer 提取出的 key_points / obligations（即"减按25%计入、按20%
    税率"这类规则条文）一并纳入向量。之前只用了标题+摘要+正文，导致"含计算规则的
    核心文件"与"仅讲征管/申报表的边角文件"语义分扎堆，核心文件排不到前面。
    """
    parts = []
    if row["title"]:
        parts.append(row["title"])
    if row["doc_num"]:
        parts.append("文号：" + row["doc_num"])
    if row["summary"]:
        parts.append(row["summary"])
    # 结构化规则条文：税负计算类查询命中的关键信号
    kp = jload(row["key_points"], []) if row["key_points"] else []
    if kp:
        parts.append("要点：" + "；".join(str(x) for x in kp[:6]))
    ob = jload(row["obligations"], []) if row["obligations"] else []
    if ob:
        parts.append("义务：" + "；".join(str(x) for x in ob[:4]))
    content = (row["content"] or "")[:TEXT_LIMIT]
    if content:
        parts.append(content)
    return "\n".join(p for p in parts if p).strip()


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS policy_vectors (
        id       TEXT PRIMARY KEY,
        vec      TEXT,            -- base64(float32) 向量
        text     TEXT,            -- 用于嵌入的原文（便于排查）
        built_at TEXT
    )""")


def build_index(db_path=None, verbose=True, incremental=False):
    """离线为全库政策生成向量并落库 policy_vectors（UPSERT，可重复跑更新）。
    incremental=True 时只处理 policy_vectors 中缺失、或正文发生过变更（updated_at
    晚于 built_at）的政策，用于每日更新，避免每次全量重算（BAAI/bge-m3 免费额度有限）。
    """
    load_env()
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    rows = conn.execute(
        "SELECT p.id, p.title, p.doc_num, p.content, p.updated_at, "
        "a.summary, a.key_points, a.obligations "
        "FROM policies p LEFT JOIN analysis a ON a.policy_id=p.id").fetchall()
    if incremental:
        have = {r["id"]: (r["built_at"] or "") for r in
                conn.execute("SELECT id, built_at FROM policy_vectors").fetchall()}
        rows = [r for r in rows
                if r["id"] not in have
                or (r["updated_at"] or "") > have.get(r["id"], "")]
        if verbose:
            print(f"[embed] 增量模式：库内已有 {len(have)} 条，本次需新嵌入/更新 {len(rows)} 条")
    else:
        if verbose:
            print(f"[embed] 待嵌入政策数：{len(rows)}")
    texts = [compose_text(r) for r in rows]
    # 跳过完全无内容的（极少），避免空向量
    valid = [(r["id"], t) for r, t in zip(rows, texts) if t.strip()]
    ids = [v[0] for v in valid]
    texts = [v[1] for v in valid]
    t0 = time.strftime("%Y-%m-%d %H:%M:%S")
    done = 0
    B = 32
    for i in range(0, len(texts), B):
        batch_ids = ids[i:i + B]
        batch_texts = texts[i:i + B]
        vecs = embed_texts(batch_texts)
        for pid, vec in zip(batch_ids, vecs):
            blob = base64.b64encode(
                np.asarray(vec, dtype=np.float32).tobytes()).decode("ascii")
            conn.execute(
                "INSERT INTO policy_vectors(id, vec, text, built_at) "
                "VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "vec=excluded.vec, text=excluded.text, built_at=excluded.built_at",
                (pid, blob, compose_text_by_id(rows, pid), t0))
        conn.commit()
        done += len(batch_ids)
        if verbose:
            print(f"[embed] 已嵌入 {done}/{len(texts)}")
    conn.close()
    if verbose:
        print(f"[embed] 完成，向量库已写入 {db_path}")
    return done


def compose_text_by_id(rows, pid):
    for r in rows:
        if r["id"] == pid:
            return compose_text(r)
    return ""


class PolicyVecIndex:
    """内存中的归一化向量矩阵，支持快速余弦 Top-K。"""

    def __init__(self, ids, matrix):
        self.ids = ids
        self.M = matrix  # (n, dim) float32, 已 L2 归一化

    @classmethod
    def load(cls, db_path=None):
        db_path = db_path or DB_PATH
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, vec FROM policy_vectors").fetchall()
        conn.close()
        if not rows:
            return None
        ids = []
        vecs = []
        for r in rows:
            ids.append(r["id"])
            vecs.append(np.frombuffer(base64.b64decode(r["vec"]), dtype=np.float32))
        M = np.stack(vecs).astype(np.float32)
        # L2 归一化，使点积即余弦
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        M = M / norms
        return cls(ids, M)

    def search(self, qvec, top_k=RECALL_K):
        q = np.asarray(qvec, dtype=np.float32)
        n = np.linalg.norm(q)
        if n == 0:
            return []
        q = q / n
        sims = self.M @ q  # (n,)
        idx = np.argsort(-sims)[:top_k]
        return [(self.ids[i], float(sims[i])) for i in idx]


def get_index(db_path=None):
    """进程级缓存，懒加载。加载失败则返回 False 并缓存，避免反复读库。"""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE if _INDEX_CACHE else None
    try:
        idx = PolicyVecIndex.load(db_path)
    except Exception:
        idx = None
    _INDEX_CACHE = idx if idx else False
    return idx


def reset_cache():
    global _INDEX_CACHE
    _INDEX_CACHE = None


def search_semantic(q, top_k=RECALL_K, db_path=None):
    """语义召回：返回 [(policy_id, score), ...]，按相似度降序。失败返回 []。"""
    idx = get_index(db_path)
    if not idx:
        return []
    try:
        ev = embed_texts([q])[0]
        if not ev:
            return []
    except Exception:
        return []
    return idx.search(ev, top_k)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    build_index()

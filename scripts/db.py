# -*- coding: utf-8 -*-
"""
财税政策数据库 - 数据层
定义 schema、连接管理与通用读写工具。
"""
import os
import sqlite3
import hashlib
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "tax_policy.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 政策主表
CREATE TABLE IF NOT EXISTS policies (
    id              TEXT PRIMARY KEY,       -- 源站唯一ID
    title           TEXT NOT NULL,
    doc_num         TEXT,                   -- 文号 如 财税〔2016〕36号
    doc_type        TEXT,                   -- 文种 如 国家税务总局公告
    doc_year        TEXT,
    pub_name        TEXT,                   -- 发文机关
    cwrq            TEXT,                   -- 成文日期
    pub_date        TEXT,                   -- 发布日期
    effect_level    TEXT,                   -- 效力级次: 法律/行政法规/财税文件/税务规范性文件...
    aging           TEXT,                   -- 时效性: 全文有效/全文废止/部分失效/尚未生效
    abolish_date    TEXT,                   -- 废止日期
    revise_type     TEXT,                   -- 修订类型
    tax_types       TEXT,                   -- 税种 JSON数组
    policy_cat      TEXT,                   -- 政策大类 JSON: 税收政策/税费征管
    labels          TEXT,                   -- 原站标签
    related_names   TEXT,                   -- 源站给出的关联政策文件名
    content         TEXT,                   -- 全文正文
    short_content   TEXT,
    url             TEXT,
    appendix        TEXT,                   -- 附件 JSON
    channel         TEXT,                   -- 所属栏目 如 税务规范性文件
    clicknum        INTEGER DEFAULT 0,
    content_hash    TEXT,                   -- 正文哈希，用于变更检测
    source          TEXT DEFAULT '国家税务总局政策法规库',
    first_seen      TEXT,                   -- 首次入库时间
    last_seen       TEXT,                   -- 最近一次抓取见到
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_pol_cwrq       ON policies(cwrq DESC);
CREATE INDEX IF NOT EXISTS idx_pol_aging      ON policies(aging);
CREATE INDEX IF NOT EXISTS idx_pol_level      ON policies(effect_level);
CREATE INDEX IF NOT EXISTS idx_pol_year       ON policies(doc_year);
CREATE INDEX IF NOT EXISTS idx_pol_docnum     ON policies(doc_num);
CREATE INDEX IF NOT EXISTS idx_pol_firstseen  ON policies(first_seen DESC);

-- 官方政策解读
CREATE TABLE IF NOT EXISTS interpretations (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    content         TEXT,
    pub_date        TEXT,
    cwrq            TEXT,
    pub_name        TEXT,
    url             TEXT,
    channel         TEXT,
    ref_doc_num     TEXT,                   -- 从标题/正文解析出的被解读文号
    ref_policy_id   TEXT,                   -- 匹配到的政策主表ID
    content_hash    TEXT,
    first_seen      TEXT,
    last_seen       TEXT
);
CREATE INDEX IF NOT EXISTS idx_interp_ref  ON interpretations(ref_policy_id);
CREATE INDEX IF NOT EXISTS idx_interp_date ON interpretations(pub_date DESC);

-- 自动解读分析结果（规则引擎 + AI）
CREATE TABLE IF NOT EXISTS analysis (
    policy_id       TEXT PRIMARY KEY,
    summary         TEXT,                   -- 一句话摘要
    domains         TEXT,                   -- 六大关注领域 JSON
    tax_types_x     TEXT,                   -- 增强后的税种 JSON
    effective_date  TEXT,                   -- 解析出的生效日期
    expire_date     TEXT,                   -- 执行截止日期
    exec_period     TEXT,                   -- 执行期间描述
    is_retroactive  INTEGER DEFAULT 0,      -- 是否溯及既往
    key_points      TEXT,                   -- 核心要点 JSON数组
    obligations     TEXT,                   -- 纳税人义务/动作 JSON
    risk_level      TEXT,                   -- 高/中/低 关注度
    risk_reason     TEXT,
    abolished_docs  TEXT,                   -- 本文废止的旧文件 JSON
    cited_docs      TEXT,                   -- 本文引用的文号 JSON
    amount_items    TEXT,                   -- 涉及的税率/金额/比例 JSON
    ai_summary      TEXT,                   -- AI 深度解读
    ai_impact       TEXT,                   -- AI 影响分析
    ai_action       TEXT,                   -- AI 应对建议
    ai_model        TEXT,
    ai_at           TEXT,
    rule_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ana_risk ON analysis(risk_level);

-- 政策关系图谱（新旧对比核心）
CREATE TABLE IF NOT EXISTS relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id          TEXT,                   -- 来源政策ID（新文）
    src_doc_num     TEXT,
    tgt_doc_num     TEXT,                   -- 目标文号（旧文），正文常为简写
    tgt_title       TEXT DEFAULT '',        -- 目标标题（书名号内），比文号更可靠
    tgt_key         TEXT DEFAULT '',        -- 去重键：归一化标题优先，否则文号
    tgt_id          TEXT,                   -- 匹配到的旧文ID，可能为空
    rel_type        TEXT,                   -- abolish废止 / revise修订 / cite引用 / supersede替代
    evidence        TEXT,                   -- 原文依据片段
    confidence      INTEGER DEFAULT 0,      -- 0-100 置信度
    created_at      TEXT,
    UNIQUE(src_id, tgt_key, rel_type)
);
CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(src_id);
CREATE INDEX IF NOT EXISTS idx_rel_tgt ON relations(tgt_id);
CREATE INDEX IF NOT EXISTS idx_rel_tgtnum ON relations(tgt_doc_num);

-- 变更追踪
CREATE TABLE IF NOT EXISTS changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id       TEXT,
    title           TEXT,
    doc_num         TEXT,
    change_type     TEXT,                   -- new新增 / aging时效变更 / content内容修订 / abolish废止
    field           TEXT,
    old_value       TEXT,
    new_value       TEXT,
    detected_at     TEXT,
    notified        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chg_time ON changes(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_chg_type ON changes(change_type);

-- 抓取日志
CREATE TABLE IF NOT EXISTS crawl_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT,
    finished_at     TEXT,
    mode            TEXT,                   -- full / incr
    column_name     TEXT,
    pages           INTEGER,
    fetched         INTEGER,
    new_count       INTEGER,
    updated_count   INTEGER,
    status          TEXT,
    message         TEXT
);

-- 全文检索
CREATE VIRTUAL TABLE IF NOT EXISTS policies_fts USING fts5(
    id UNINDEXED, title, doc_num, content, tokenize='unicode61'
);
"""


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return DB_PATH


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def md5(text):
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False)


def jload(s, default=None):
    if not s:
        return default if default is not None else []
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else []


def stats():
    """返回数据库概览统计"""
    conn = get_conn()
    c = conn.cursor()
    out = {}
    out["policies"] = c.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
    out["interpretations"] = c.execute("SELECT COUNT(*) FROM interpretations").fetchone()[0]
    out["analysis"] = c.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
    out["relations"] = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    out["changes"] = c.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
    out["by_aging"] = {r[0] or "未标注": r[1] for r in c.execute(
        "SELECT aging, COUNT(*) FROM policies GROUP BY aging ORDER BY 2 DESC").fetchall()}
    out["by_level"] = {r[0] or "未分类": r[1] for r in c.execute(
        "SELECT effect_level, COUNT(*) FROM policies GROUP BY effect_level ORDER BY 2 DESC").fetchall()}
    yr = c.execute("SELECT MIN(doc_year), MAX(doc_year) FROM policies WHERE doc_year != ''").fetchone()
    out["year_range"] = [yr[0], yr[1]]
    conn.close()
    return out


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    p = init_db()
    print("数据库已初始化:", p)
    print(jdump(stats()))

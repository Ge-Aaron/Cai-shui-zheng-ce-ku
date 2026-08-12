# -*- coding: utf-8 -*-
"""
财税政策数据库 - 本地查询服务（零第三方依赖，仅用标准库）
启动后浏览器访问 http://127.0.0.1:8765
"""
import os
import re
import sys
import json
import sqlite3
import webbrowser
import threading
from urllib.parse import urlparse, parse_qs
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB_PATH, jload, now
from embeddings import search_semantic, RECALL_K, get_index, reset_cache  # 语义向量召回（RAG）

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_env_file():
    """读取项目根目录下的 .env 文件，把键值注入 os.environ。
    已存在于 shell 环境变量中的键不会被覆盖（shell 优先级更高）。
    支持 # 注释、空行、以及带单/双引号的取值。"""
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
    )
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if not k:
                continue
            # 去掉首尾引号
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            if k not in os.environ:
                os.environ[k] = v


load_env_file()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")
# 端口优先级：PaaS 注入的 $PORT → TAXDB_PORT → 默认 8765（本地）
_port_env = os.environ.get("PORT") or os.environ.get("TAXDB_PORT")
PORT = int(_port_env) if _port_env else 8765
# 云端部署时监听所有网卡；本地仍是 127.0.0.1 也可（PaaS 会注入 0.0.0.0 场景）
BIND_HOST = os.environ.get("TAXDB_BIND", "0.0.0.0")


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def fts_escape(q):
    """保留旧名以便兼容；现搜索改用中文友好的 LIKE 方案，本函数不再被调用。"""
    return q


def ensure_kb():
    """场景解答知识库表（幂等）"""
    c = conn()
    c.execute("""CREATE TABLE IF NOT EXISTS ask_kb (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scene TEXT NOT NULL,
        lead TEXT,
        points TEXT,
        keywords TEXT,
        refs TEXT,
        llm TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    # 兼容旧库：补充 llm 列（早期版本无此字段）
    cols = [r[1] for r in c.execute("PRAGMA table_info(ask_kb)").fetchall()]
    if "llm" not in cols:
        c.execute("ALTER TABLE ask_kb ADD COLUMN llm TEXT")
    c.commit()
    c.close()


def api_stats():
    c = conn()
    cur = c.cursor()
    out = {}
    out["total"] = cur.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
    out["interp"] = cur.execute("SELECT COUNT(*) FROM interpretations").fetchone()[0]
    out["relations"] = cur.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    # 效力以「官方标注 + 引擎推断」的最终结论 status_final 为准
    sc = {r[0] or "-": r[1] for r in cur.execute(
        "SELECT status_final,COUNT(*) FROM analysis GROUP BY status_final").fetchall()}
    out["by_status"] = sc
    out["valid"] = sc.get("有效", 0) + sc.get("推定有效", 0)
    out["abolished"] = sc.get("已废止", 0) + sc.get("已失效", 0) + sc.get("已到期", 0)
    out["partial"] = sc.get("部分有效", 0) + sc.get("部分失效", 0) + sc.get("已修改", 0)
    out["pending"] = sc.get("尚未生效", 0)
    out["doubt"] = sc.get("存疑", 0)
    out["official_tagged"] = cur.execute(
        "SELECT COUNT(*) FROM analysis WHERE status_source='官方标注'").fetchone()[0]
    out["inferred"] = cur.execute(
        "SELECT COUNT(*) FROM analysis WHERE status_source!='官方标注' "
        "AND status_source!=''").fetchone()[0]
    yr = cur.execute("SELECT MIN(doc_year),MAX(doc_year) FROM policies "
                     "WHERE doc_year GLOB '[0-9][0-9][0-9][0-9]'").fetchone()
    out["year_min"], out["year_max"] = yr[0], yr[1]
    out["last_crawl"] = (cur.execute(
        "SELECT finished_at FROM crawl_log ORDER BY id DESC LIMIT 1").fetchone() or [""])[0]
    out["new_30d"] = cur.execute(
        "SELECT COUNT(*) FROM changes WHERE change_type='new' "
        "AND detected_at >= datetime('now','-30 day')").fetchone()[0]
    out["by_year"] = [{"y": r[0], "n": r[1]} for r in cur.execute(
        "SELECT doc_year,COUNT(*) FROM policies WHERE doc_year GLOB '[0-9][0-9][0-9][0-9]' "
        "GROUP BY doc_year ORDER BY doc_year").fetchall()]
    out["by_risk"] = {r[0] or "-": r[1] for r in cur.execute(
        "SELECT risk_level,COUNT(*) FROM analysis GROUP BY risk_level").fetchall()}
    # AI 具体方案是否已接入（配置了 OpenAI 兼容密钥且非预览模式）
    out["ai_enabled"] = bool(os.environ.get("TAXDB_LLM_KEY") and os.environ.get("TAXDB_LLM_URL"))
    # 语义向量检索是否已建索引（policy_vectors 表有数据）
    try:
        out["semantic_ready"] = cur.execute(
            "SELECT COUNT(*) FROM policy_vectors").fetchone()[0]
    except Exception:
        out["semantic_ready"] = 0
    c.close()
    return out


def api_facets():
    c = conn()
    cur = c.cursor()
    out = {}
    out["levels"] = [r[0] for r in cur.execute(
        "SELECT effect_level,COUNT(*) c FROM policies WHERE effect_level!='' "
        "GROUP BY effect_level ORDER BY c DESC").fetchall()]
    out["agings"] = [r[0] for r in cur.execute(
        "SELECT aging,COUNT(*) c FROM policies WHERE aging!='' "
        "GROUP BY aging ORDER BY c DESC").fetchall()]
    out["statuses"] = [r[0] for r in cur.execute(
        "SELECT status_final,COUNT(*) c FROM analysis WHERE status_final!='' "
        "GROUP BY status_final ORDER BY c DESC").fetchall()]
    out["years"] = [r[0] for r in cur.execute(
        "SELECT DISTINCT doc_year FROM policies WHERE doc_year GLOB '[0-9][0-9][0-9][0-9]' "
        "ORDER BY doc_year DESC").fetchall()]
    taxes, doms = {}, {}
    for r in cur.execute("SELECT tax_types_x,domains FROM analysis").fetchall():
        for t in jload(r["tax_types_x"], []):
            taxes[t] = taxes.get(t, 0) + 1
        for d in jload(r["domains"], []):
            doms[d] = doms.get(d, 0) + 1
    out["taxes"] = [k for k, _ in sorted(taxes.items(), key=lambda x: -x[1])]
    out["domains"] = [k for k, _ in sorted(doms.items(), key=lambda x: -x[1])]
    c.close()
    return out


def api_search(p):
    q = (p.get("q", [""])[0] or "").strip()
    tax = p.get("tax", [""])[0]
    level = p.get("level", [""])[0]
    aging = p.get("aging", [""])[0]
    status = p.get("status", [""])[0]
    year = p.get("year", [""])[0]
    domain = p.get("domain", [""])[0]
    risk = p.get("risk", [""])[0]
    sort = p.get("sort", ["date"])[0]
    page = max(1, int(p.get("page", ["1"])[0] or 1))
    size = min(100, int(p.get("size", ["20"])[0] or 20))

    c = conn()
    cur = c.cursor()
    where, args = [], []
    toks = []

    if q:
        # 中文友好检索：按空格分词，逐词在 标题/文号/摘要/正文 做 LIKE（多词为 AND）
        toks = [t for t in re.split(r"\s+", q) if t]
        for t in toks:
            pat = f"%{t}%"
            where.append("(p.title LIKE ? OR p.doc_num LIKE ? OR a.summary LIKE ? OR p.content LIKE ?)")
            args += [pat, pat, pat, pat]
    if level:
        where.append("p.effect_level = ?")
        args.append(level)
    if aging:
        if aging == "废止":
            where.append("p.aging LIKE '%废止%'")
        else:
            where.append("p.aging = ?")
            args.append(aging)
    if status:
        if status == "在用":       # 有效 + 推定有效，日常检索最常用
            where.append("a.status_final IN ('有效','推定有效')")
        elif status == "失效":
            where.append("a.status_final IN ('已废止','已失效','已到期')")
        else:
            where.append("a.status_final = ?")
            args.append(status)
    if year:
        where.append("p.doc_year = ?")
        args.append(year)
    if tax:
        where.append("a.tax_types_x LIKE ?")
        args.append(f'%"{tax}"%')
    if domain:
        where.append("a.domains LIKE ?")
        args.append(f'%"{domain}"%')
    if risk:
        where.append("a.risk_level = ?")
        args.append(risk)

    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    order_args = []
    if q and sort == "rel" and toks:
        # 相关度排序：标题命中权重最高，其次文号、摘要、正文
        first = toks[0]
        order = ("(CASE WHEN p.title LIKE ? THEN 4 ELSE 0 END)"
                 "+ (CASE WHEN p.doc_num LIKE ? THEN 3 ELSE 0 END)"
                 "+ (CASE WHEN a.summary LIKE ? THEN 2 ELSE 0 END)"
                 "+ (CASE WHEN p.content LIKE ? THEN 1 ELSE 0 END) DESC, p.cwrq DESC")
        order_args = [f"%{first}%"] * 4
    else:
        order = {"date": "p.cwrq DESC, p.id DESC",
                 "date_asc": "p.cwrq ASC",
                 "hot": "p.clicknum DESC",
                 "risk": "CASE a.risk_level WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END, p.cwrq DESC",
                 }.get(sort, "p.cwrq DESC")

    base = f"FROM policies p LEFT JOIN analysis a ON a.policy_id=p.id{wsql}"
    total = cur.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    rows = cur.execute(
        f"""SELECT p.id,p.title,p.doc_num,p.cwrq,p.pub_name,p.effect_level,p.aging,
                   p.url,p.doc_year,p.clicknum,
                   a.summary,a.tax_types_x,a.domains,a.risk_level,a.effective_date,
                   a.expire_date,a.abolished_docs,
                   a.status_final,a.status_source,a.status_evidence
            {base} ORDER BY {order} LIMIT ? OFFSET ?""",
        args + order_args + [size, (page - 1) * size]).fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"], "title": r["title"], "doc_num": r["doc_num"],
            "cwrq": r["cwrq"], "pub_name": r["pub_name"],
            "level": r["effect_level"], "aging": r["aging"], "url": r["url"],
            "year": r["doc_year"], "clicks": r["clicknum"],
            "summary": r["summary"] or "", "taxes": jload(r["tax_types_x"], []),
            "domains": jload(r["domains"], []), "risk": r["risk_level"] or "",
            "eff": r["effective_date"] or "", "exp": r["expire_date"] or "",
            "abolished_n": len(jload(r["abolished_docs"], [])),
            "status": r["status_final"] or "", "st_src": r["status_source"] or "",
            "st_ev": r["status_evidence"] or "",
        })
    c.close()
    return {"total": total, "page": page, "size": size, "items": items}


# ---------------------------------------------------------------------------
# 电商相关政策：从全量政策库筛选「跨境电商」与「国内电商」两类
# 说明：库内无现成的电商分类标签，本接口依据标题/正文中的电商相关表述做关键词判定。
#   - 跨境电商：含「跨境电子商务 / 跨境电商 / 跨境零售 / 综试区 / 海外仓」等跨境+电商语义
#   - 国内电商：含「电子商务 / 网络零售 / 直播带货」等表述且不属于跨境电商
# ---------------------------------------------------------------------------
ECROSS_KW = ["跨境电子商务", "跨境电商", "跨境零售", "跨境贸易电子商务",
             "跨境电子商务综合试验区", "海外仓"]
EDOMESTIC_KW = ["电子商务", "网络零售", "网上零售", "网络直播营销", "直播带货"]

_EC_SELECT = ("p.id,p.title,p.doc_num,p.cwrq,p.pub_name,p.effect_level,p.aging,"
              "p.url,p.doc_year,p.clicknum,"
              "a.summary,a.tax_types_x,a.domains,a.risk_level,a.effective_date,"
              "a.expire_date,a.abolished_docs,"
              "a.status_final,a.status_source,a.status_evidence")


def _ec_kw_where(kws):
    """生成 (标题 LIKE ? OR 正文 LIKE ?) 的 OR 组合 WHERE 片段与参数。"""
    parts, args = [], []
    for k in kws:
        parts.append("(p.title LIKE ? OR p.content LIKE ?)")
        args += [f"%{k}%", f"%{k}%"]
    return "(" + " OR ".join(parts) + ")", args


def _ec_rows_to_items(rows):
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "title": r["title"], "doc_num": r["doc_num"],
            "cwrq": r["cwrq"], "pub_name": r["pub_name"],
            "level": r["effect_level"], "aging": r["aging"], "url": r["url"],
            "year": r["doc_year"], "clicks": r["clicknum"],
            "summary": r["summary"] or "", "taxes": jload(r["tax_types_x"], []),
            "domains": jload(r["domains"], []), "risk": r["risk_level"] or "",
            "eff": r["effective_date"] or "", "exp": r["expire_date"] or "",
            "abolished_n": len(jload(r["abolished_docs"], [])),
            "status": r["status_final"] or "", "st_src": r["status_source"] or "",
            "st_ev": r["status_evidence"] or "",
        })
    return items


def api_ecommerce(p):
    """电商政策：在 api_search 同款多条件筛选基础上叠加「跨境/国内电商」语义判定。

    筛选参数与 api_search 对齐：q / tax / level / aging / status / year / domain /
    risk / sort，另有电商专属参数：
      - cat   板块：''=跨境+国内都看，'cross'=只看跨境电商，'domestic'=只看国内电商
      - size  每页条数（两个板块共用，默认 10）
      - cpage 跨境电商当前页；dpage 国内电商当前页（两个板块各自独立翻页）
    返回 cross / domestic 两类，各自带 total / items / page；两类计数始终返回
    （便于界面显示板块总量），但被 cat 隐藏的板块不再取明细数据，省一次查询。
    """
    q = (p.get("q", [""])[0] or "").strip()
    tax = p.get("tax", [""])[0]
    level = p.get("level", [""])[0]
    aging = p.get("aging", [""])[0]
    status = p.get("status", [""])[0]
    year = p.get("year", [""])[0]
    domain = p.get("domain", [""])[0]
    risk = p.get("risk", [""])[0]
    sort = p.get("sort", ["date"])[0]
    cat = (p.get("cat", [""])[0] or "").strip()
    if cat not in ("", "cross", "domestic"):
        cat = ""
    size = min(100, max(5, int(p.get("size", ["10"])[0] or 10)))
    # 兼容旧参数 page：未显式给 cpage/dpage 时，用 page 作为两者初值
    page = max(1, int(p.get("page", ["1"])[0] or 1))
    cpage = max(1, int(p.get("cpage", [str(page)])[0] or page))
    dpage = max(1, int(p.get("dpage", [str(page)])[0] or page))

    cross_where, cross_args = _ec_kw_where(ECROSS_KW)
    ec_where, ec_args = _ec_kw_where(EDOMESTIC_KW)

    # ---- 公共筛选条件（与 api_search 对齐）----
    where, args = [], []
    toks = []
    if q:
        toks = [t for t in re.split(r"\s+", q) if t]
        for t in toks:
            pat = f"%{t}%"
            where.append("(p.title LIKE ? OR p.doc_num LIKE ? OR a.summary LIKE ? OR p.content LIKE ?)")
            args += [pat, pat, pat, pat]
    if level:
        where.append("p.effect_level = ?")
        args.append(level)
    if aging:
        if aging == "废止":
            where.append("p.aging LIKE '%废止%'")
        else:
            where.append("p.aging = ?")
            args.append(aging)
    if status:
        if status == "在用":
            where.append("a.status_final IN ('有效','推定有效')")
        elif status == "失效":
            where.append("a.status_final IN ('已废止','已失效','已到期')")
        else:
            where.append("a.status_final = ?")
            args.append(status)
    if year:
        where.append("p.doc_year = ?")
        args.append(year)
    if tax:
        where.append("a.tax_types_x LIKE ?")
        args.append(f'%"{tax}"%')
    if domain:
        where.append("a.domains LIKE ?")
        args.append(f'%"{domain}"%')
    if risk:
        where.append("a.risk_level = ?")
        args.append(risk)

    # ---- 排序（与 api_search 对齐）----
    order_args = []
    if q and sort == "rel" and toks:
        first = toks[0]
        order = ("(CASE WHEN p.title LIKE ? THEN 4 ELSE 0 END)"
                 "+ (CASE WHEN p.doc_num LIKE ? THEN 3 ELSE 0 END)"
                 "+ (CASE WHEN a.summary LIKE ? THEN 2 ELSE 0 END)"
                 "+ (CASE WHEN p.content LIKE ? THEN 1 ELSE 0 END) DESC, p.cwrq DESC")
        order_args = [f"%{first}%"] * 4
    else:
        order = {"date": "p.cwrq DESC, p.id DESC",
                 "date_asc": "p.cwrq ASC",
                 "hot": "p.clicknum DESC",
                 "risk": "CASE a.risk_level WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END, p.cwrq DESC",
                 }.get(sort, "p.cwrq DESC")

    c = conn()
    cur = c.cursor()

    def run(cat_where, cat_args, pg, fetch=True):
        """统计该板块命中总数；fetch=False 时只数不取（板块被隐藏的情况）。"""
        w = list(where) + [cat_where]
        a = list(args) + list(cat_args)
        wsql = (" WHERE " + " AND ".join(w)) if w else ""
        base = f"FROM policies p LEFT JOIN analysis a ON a.policy_id=p.id {wsql}"
        total = cur.execute(f"SELECT COUNT(*) {base}", a).fetchone()[0]
        if not fetch:
            return total, [], pg
        # 翻页越界（如换了筛选条件后页码超出）时回落到最后一页，避免出现空白页
        tp = max(1, -(-total // size))
        pg = min(pg, tp)
        rows = cur.execute(
            f"SELECT {_EC_SELECT} {base} ORDER BY {order} LIMIT ? OFFSET ?",
            a + order_args + [size, (pg - 1) * size]).fetchall()
        return total, _ec_rows_to_items(rows), pg

    ct, ci, cp = run(cross_where, cross_args, cpage, cat != "domestic")
    dt, di, dp = run(f"({ec_where} AND NOT {cross_where})",
                     ec_args + cross_args, dpage, cat != "cross")
    c.close()
    return {"cat": cat, "size": size,
            "cross": {"total": ct, "items": ci, "page": cp},
            "domestic": {"total": dt, "items": di, "page": dp}}


def api_policy(pid):
    c = conn()
    cur = c.cursor()
    r = cur.execute("SELECT * FROM policies WHERE id=?", (pid,)).fetchone()
    if not r:
        c.close()
        return {"error": "not found"}
    a = cur.execute("SELECT * FROM analysis WHERE policy_id=?", (pid,)).fetchone()

    # 本文废止/引用的旧文
    outgoing = []
    for x in cur.execute(
            """SELECT r.tgt_doc_num,r.rel_type,r.evidence,r.tgt_id,p.title,p.aging,p.cwrq
               FROM relations r LEFT JOIN policies p ON p.id=r.tgt_id
               WHERE r.src_id=? ORDER BY
               CASE r.rel_type WHEN 'abolish' THEN 0 WHEN 'supersede' THEN 1
                               WHEN 'revise' THEN 2 ELSE 3 END""", (pid,)).fetchall():
        outgoing.append({"doc_num": x["tgt_doc_num"], "type": x["rel_type"],
                         "evidence": x["evidence"], "id": x["tgt_id"] or "",
                         "title": x["title"] or "", "aging": x["aging"] or "",
                         "cwrq": x["cwrq"] or ""})
    # 哪些新文废止/引用了本文
    incoming = []
    for x in cur.execute(
            """SELECT r.src_id,r.rel_type,r.evidence,p.title,p.doc_num,p.cwrq,p.aging
               FROM relations r JOIN policies p ON p.id=r.src_id
               WHERE r.tgt_id=? ORDER BY p.cwrq DESC""", (pid,)).fetchall():
        incoming.append({"id": x["src_id"], "type": x["rel_type"], "title": x["title"],
                         "doc_num": x["doc_num"], "cwrq": x["cwrq"],
                         "aging": x["aging"], "evidence": x["evidence"]})
    # 官方解读
    interps = [{"id": x["id"], "title": x["title"], "url": x["url"],
                "pub_date": x["pub_date"], "content": x["content"]}
               for x in cur.execute(
                   "SELECT * FROM interpretations WHERE ref_policy_id=? ORDER BY pub_date DESC",
                   (pid,)).fetchall()]
    # 变更历史
    chg = [{"type": x["change_type"], "field": x["field"], "old": x["old_value"],
            "new": x["new_value"], "at": x["detected_at"]}
           for x in cur.execute(
               "SELECT * FROM changes WHERE policy_id=? ORDER BY detected_at DESC LIMIT 20",
               (pid,)).fetchall()]

    out = {
        "id": r["id"], "title": r["title"], "doc_num": r["doc_num"],
        "doc_type": r["doc_type"], "pub_name": r["pub_name"], "cwrq": r["cwrq"],
        "pub_date": r["pub_date"], "level": r["effect_level"], "aging": r["aging"],
        "abolish_date": r["abolish_date"], "content": r["content"], "url": r["url"],
        "appendix": jload(r["appendix"], []), "channel": r["channel"],
        "labels": r["labels"], "related_names": r["related_names"],
        "outgoing": outgoing, "incoming": incoming,
        "interps": interps, "changes": chg,
    }
    if a:
        out["analysis"] = {
            "summary": a["summary"], "domains": jload(a["domains"], []),
            "taxes": jload(a["tax_types_x"], []), "eff": a["effective_date"],
            "exp": a["expire_date"], "period": a["exec_period"],
            "retro": a["is_retroactive"], "key_points": jload(a["key_points"], []),
            "obligations": jload(a["obligations"], []), "risk": a["risk_level"],
            "risk_reason": a["risk_reason"], "amounts": jload(a["amount_items"], []),
            "abolished": jload(a["abolished_docs"], []),
            "cited": jload(a["cited_docs"], []),
            "ai_summary": a["ai_summary"] or "", "ai_impact": a["ai_impact"] or "",
            "ai_action": a["ai_action"] or "", "ai_at": a["ai_at"] or "",
            "status": a["status_final"] or "", "st_src": a["status_source"] or "",
            "st_ev": a["status_evidence"] or "",
        }
    c.close()
    return out


def api_changes(p):
    days = int(p.get("days", ["30"])[0] or 30)
    ctype = p.get("type", [""])[0]
    limit = min(300, int(p.get("limit", ["100"])[0] or 100))
    c = conn()
    cur = c.cursor()
    w = ["c.detected_at >= datetime('now', ?)"]
    args = [f"-{days} day"]
    if ctype:
        w.append("c.change_type = ?")
        args.append(ctype)
    rows = cur.execute(
        f"""SELECT c.*, p.cwrq, p.effect_level, a.risk_level, a.summary, a.tax_types_x
            FROM changes c LEFT JOIN policies p ON p.id=c.policy_id
            LEFT JOIN analysis a ON a.policy_id=c.policy_id
            WHERE {' AND '.join(w)}
            ORDER BY c.detected_at DESC, p.cwrq DESC LIMIT ?""", args + [limit]).fetchall()
    items = [{"id": r["policy_id"], "title": r["title"], "doc_num": r["doc_num"],
              "type": r["change_type"], "field": r["field"], "old": r["old_value"],
              "new": r["new_value"], "at": r["detected_at"], "cwrq": r["cwrq"] or "",
              "level": r["effect_level"] or "", "risk": r["risk_level"] or "",
              "summary": r["summary"] or "", "taxes": jload(r["tax_types_x"], [])}
             for r in rows]
    c.close()
    return {"items": items}


def api_expiring(p):
    """即将到期 / 即将生效的政策"""
    c = conn()
    cur = c.cursor()
    soon = [{"id": r["policy_id"], "title": r["title"], "doc_num": r["doc_num"],
             "exp": r["expire_date"], "taxes": jload(r["tax_types_x"], [])}
            for r in cur.execute(
                """SELECT a.policy_id,a.expire_date,a.tax_types_x,p.title,p.doc_num
                   FROM analysis a JOIN policies p ON p.id=a.policy_id
                   WHERE a.expire_date != '' AND a.expire_date >= date('now')
                     AND a.expire_date <= date('now','+180 day')
                     AND p.aging NOT LIKE '%废止%'
                   ORDER BY a.expire_date LIMIT 60""").fetchall()]
    pending = [{"id": r["id"], "title": r["title"], "doc_num": r["doc_num"],
                "eff": r["effective_date"] or "", "cwrq": r["cwrq"]}
               for r in cur.execute(
                   """SELECT p.id,p.title,p.doc_num,p.cwrq,a.effective_date
                      FROM policies p LEFT JOIN analysis a ON a.policy_id=p.id
                      WHERE p.aging='尚未生效' ORDER BY p.cwrq DESC LIMIT 60""").fetchall()]
    c.close()
    return {"expiring": soon, "pending": pending}


# ---------------------------------------------------------------------------
# 场景解答：基于全量政策库的检索式问答（无需外部 LLM 也能给出结构化方案）
# ---------------------------------------------------------------------------
DOMAIN_TERMS = [
    "增值税", "进项税额", "销项税额", "留抵退税", "留抵", "出口退税", "预缴", "预收款",
    "差额征税", "差额计税", "简易计税", "一般计税", "视同销售", "免征", "免税", "不征税",
    "即征即退", "先征后返", "起征点", "小规模纳税人", "一般纳税人", "专用发票", "普通发票",
    "数电票", "全电发票", "电子发票", "虚开发票", "发票", "开票", "进项抵扣", "抵扣",
    "企业所得税", "应纳税所得额", "小型微利企业", "小微企业", "研发费用", "加计扣除",
    "高新技术企业", "固定资产", "加速折旧", "无形资产", "长期待摊", "个人所得税", "个税",
    "专项附加扣除", "子女教育", "继续教育", "住房贷款", "住房租金", "赡养老人", "3岁以下婴幼儿",
    "全年一次性奖金", "年终奖", "综合所得", "经营所得", "社保费", "社会保险费", "滞纳金",
    "税务稽查", "核定征收", "查账征收", "纳税信用", "汇算清缴", "申报", "建筑服务", "房地产",
    "不动产", "租赁", "融资租赁", "劳务派遣", "人力资源", "金融保险", "再保险", "农产品",
    "环保税", "资源税", "印花税", "契税", "土地增值税", "房产税", "城镇土地使用税", "城市维护建设税",
    "教育费附加", "消费税", "关税", "烟叶税", "车船税", "耕地占用税", "车辆购置税",
    "非居民企业", "居民企业", "关联交易", "转让定价", "股权转让", "企业重组", "合并", "分立",
    "清算", "注销", "跨地区", "总分机构", "汇总纳税", "西部大开发", "技术转让", "创投企业",
    "创业投资", "六税两费", "三免三减半", "税收协定", "预提所得税", "源泉扣缴", "境外所得",
    "税收减免", "减免税", "退税", "代开发票", "二手车", "二手车经销",
    "营改增", "营业税", "金融商品转让", "资管产品", "离境退税", "纳税信用修复",
    "税收滞纳金", "税收罚款", "发票限额", "最高开票限额", "白名单", "异常凭证",
]
STOP2 = {"我们", "可以", "以及", "这个", "那个", "这些", "那些", "是否", "如果", "应该", "需要",
         "进行", "办理", "处理", "对于", "关于", "一种", "通过", "由于", "因为", "所以", "但是",
         "并且", "时候", "情况", "问题", "公司", "个人", "他们", "自己", "什么", "怎么", "如何",
         "还是", "或者", "予以", "按照", "根据", "规定", "下列", "本条", "上述", "以下", "以上",
         "一项", "事项", "业务", "客户", "收到", "支付", "取得", "发生", "属于", "涉及"}
DOCNUM_RE = re.compile(
    r"财税[〔\[]?\d{4}[〕\]]\d+号"
    r"|国家税务总局[公告令]?\d{4}年第\d+号"
    r"|财政部[公告]?\d{4}年第\d+号"
    r"|国税发[〔\[]\d{4}[〕\]]\d+号"
    r"|国税函[〔\[]\d{4}[〕\]]\d+号"
    r"|公告\d{4}年第\d+号"
)


def _norm_docnum(s):
    return s.replace("[", "〔").replace("]", "〕")


def extract_keywords(q):
    kws = set()
    for m in DOCNUM_RE.finditer(q):
        kws.add(_norm_docnum(m.group()))
    for t in DOMAIN_TERMS:
        if t in q:
            kws.add(t)
    if len(kws) == 0:  # 兜底：一个关键词都没命中时才用二元词补充召回
        clean = re.sub(r"[^\u4e00-\u9fa5]", "", q)
        for i in range(len(clean) - 1):
            bg = clean[i:i + 2]
            if bg not in STOP2:
                kws.add(bg)
    return [k for k in kws if len(k) >= 2][:14]


def _excerpt(text, kws, maxlen=150):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    sents = re.split(r"(?<=[。！？；\n])", text)
    best, best_hits = "", 0
    for s in sents:
        s = s.strip(" \n\t；;")
        if len(s) < 6:
            continue
        hits = sum(1 for k in kws if k in s)
        if hits > best_hits or (hits == best_hits and hits > 0 and len(s) > len(best)):
            best_hits, best = hits, s
    if not best:
        best = (sents[0].strip() if sents else text[:maxlen])
    if len(best) > maxlen:
        best = best[:maxlen] + "…"
    return best


def _parse_llm_json(text):
    """从模型输出中尽量解析出 JSON 对象；失败则把整段当 summary。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"summary": text}


def _rule_plan(q, items, lead_title):
    """离线兜底：用检索到的政策结构化字段（要点/义务/金额/风险）合成具体方案。
    仅做可读聚合，不能代入用户数字计算——真正的「算出结果」需 AI。"""
    usable = [it for it in items if it["status"] in ("有效", "推定有效", "未见废止记录")]
    pool = (usable or items)[:3]
    steps, calcs, risks, basis = [], [], [], []
    for it in pool:
        for t in (it.get("obligations") or [])[:3]:
            s = t.strip()
            if len(s) > 72:
                s = s[:72] + "…"
            steps.append(f"{s}（依据：《{it['title']}》{it['doc_num'] or ''}）")
        for t in (it.get("key_points") or [])[:2]:
            s = re.split(r"[。；;]", t.strip())[0]
            if len(s) > 72:
                s = s[:72] + "…"
            steps.append(s)
        amts = it.get("amounts") or []
        if amts:
            calcs.append(f"《{it['title']}》：{'；'.join(str(x) for x in amts[:3])}")
        if it.get("risk") == "高":
            risks.append(f"《{it['title']}》涉及高关注度合规风险，办理时请重点核对原文与主管税务机关口径。")
        basis.append(f"《{it['title']}》（{it['doc_num'] or '—'}，{it['status']}）")
    # 结论：优先取首条可用政策的首句义务/要点，否则回退到通用表述
    summary = ""
    if pool:
        src = (pool[0].get("obligations") or pool[0].get("key_points"))
        if src:
            summary = re.split(r"[。；;]", src[0].strip())[0]
            if len(summary) > 80:
                summary = summary[:80] + "…"
    if not summary:
        summary = f"就「{q}」这类事项，建议按下列现行政策落地执行。"
    return {
        "summary": summary,
        "steps": steps[:6],
        "calc": ("\n".join(calcs) if calcs else ""),
        "deadline": "",
        "risks": risks[:4],
        "basis": basis[:4],
        "_src": "rule",
    }


def _mock_llm(q, items):
    """无密钥时的预览：用规则方案冒充 AI 输出，便于先看版式（标注 _src=ai）。"""
    base = None
    for it in items:
        if it["status"] in ("有效", "推定有效", "未见废止记录"):
            base = f"就「{q}」这类事项，建议按下述现行政策落地执行。"
            break
    plan = _rule_plan(q, items, base or f"就「{q}」这类事项，请按下述政策落地执行。")
    plan["_src"] = "ai"
    return plan


def _call_llm(endpoint, payload, key):
    """带重试的 LLM 调用：超时/网络抖动/5xx/智谱拥挤码(1305) 自动重试；
    密钥或地址错误(4xx/其他业务码)、响应格式非法 不重试，标记 _config_err 交由前端区分提示。"""
    import time, socket, urllib.error
    last_err = "多次重试后仍失败"
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                endpoint, data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {e.code}（服务端繁忙），重试中"
                time.sleep(4 * (attempt + 1))
                continue
            return {"_error": f"HTTP {e.code}: {body[:200]}", "_config_err": True, "_src": "ai"}
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            last_err = f"请求超时/网络错误：{e}"
            time.sleep(4 * (attempt + 1))
            continue
    return {"_error": last_err, "_src": "ai"}


# ============================================================================
# 现行口径（系统核验）知识库
# ----------------------------------------------------------------------------
# 用途：RAG 检索可能把"已过时/仅讲征管"的边角文件当成主依据，导致 AI 算出旧口径
# （典型：小型微利企业所得税本应按"减按25%计入、20%税率=实际税负5%"，却被旧阶梯
# 优惠带偏）。对少数高频、结论确定的计算型场景，这里直接注入经校核的权威结论与
# 公式，作为最高优先级依据；并明确禁止套用已废止的旧阶梯优惠。
# 可随政策更新在此维护（每个条目独立可核验）。
# ============================================================================
CANONICAL_RULES = [
    {
        "id": "smallbiz_itat",
        "label": "小型微利企业所得税优惠（年应纳税所得额≤300万）",
        # 命中条件：triggers 任一出现，且 keywords_any 任一出现（keywords 收紧为所得税专属，
        # 避免"小微企业增值税"等场景误触发；"小微企业"口语化写法也纳入 triggers）
        "triggers": ["小型微利企业", "小微企业所得税", "小型微利企业所得税", "小微企业所得",
                     "小微企业", "小型微利"],
        "keywords_any": ["应纳税所得额", "企业所得税"],
        "rule": ("对小型微利企业年应纳税所得额不超过300万元的部分，减按25%计入应纳税所得额，"
                 "按20%的税率缴纳企业所得税（实际税负5%）。该优惠延续执行至2027年12月31日。"),
        "formula": "应纳税额 = 年应纳税所得额 × 25% × 20%",
        "example": "年应纳税所得额200万元 → 200万 × 25% × 20% = 10万元",
        "source": ("财政部 税务总局公告2023年第12号（延续执行至2027.12.31）；"
                   "2022年第13号、2023年第6号亦明确同一口径"),
        "avoid": ("不得套用已废止/执行期已过的旧阶梯优惠，例如'年应纳税所得额不超过100万元的"
                  "部分减按12.5%计入'等旧口径（如国家税务总局公告2021年第8号第一条已于2022.12.31终止）。"),
        # 含这些字面且本条目命中时，从 AI 上下文中剔除（它们是污染的旧规则条文）
        "stale_markers": ["减按12.5%", "不超过100万元的部分，减按"],
    },
    {
        "id": "selfemp_itat",
        "label": "个体工商户经营所得减半征收个人所得税（年应纳税所得额≤200万）",
        "triggers": ["个体工商户", "个体户", "经营所得"],
        "keywords_any": ["应纳税所得额", "个人所得税", "减半", "怎么算", "多少"],
        "rule": ("对个体工商户年应纳税所得额不超过200万元的部分，在现行优惠政策基础上减半征收"
                 "个人所得税；执行至2027年12月31日。"),
        "formula": "减免税额 = （经营所得应纳税所得额 × 适用税率 - 速算扣除数）× 50%（地对不超过200万部分）",
        "example": ("年应纳税所得额200万元 → 减半后按100万元计税，依经营所得5级超额累进表计算后，"
                    "再就减免部分减半；具体税额需代入税率表。"),
        "source": "财政部 税务总局公告2023年第12号；2021年第12号",
        "avoid": "减半仅针对不超过200万元的部分，超过部分不享受；最终税额须按经营所得税率表计算。",
        "stale_markers": [],
    },
]


def match_canonical(q):
    """返回与用户场景匹配的首条现行口径条目（dict），无匹配返回 None。"""
    ql = (q or "").lower()
    for r in CANONICAL_RULES:
        if not any(t.lower() in ql for t in r.get("triggers", [])):
            continue
        kws = r.get("keywords_any")
        if kws and not any(k.lower() in ql for k in kws):
            continue
        return r
    return None


def _llm_answer(q, items):
    """可选：配置了 OpenAI 兼容 LLM 时，产出「照着做就能落地」的具体方案。
    无密钥且非预览模式时返回 None，由调用方回退到 _rule_plan。
    返回的 dict 结构：summary / steps / calc / deadline / risks / basis / _src。
    """
    key = os.environ.get("TAXDB_LLM_KEY")
    url = os.environ.get("TAXDB_LLM_URL")
    mock = os.environ.get("TAXDB_LLM_MOCK") == "1"
    if not key and not mock:
        return None

    # 现行口径（系统核验）：高频确定型计算直接注入权威结论，作为最高优先级依据
    canon = match_canonical(q)

    # ---- 构造检索上下文：把结构化分析字段喂给模型，避免其凭空编造 ----
    # 净化：剔除已废止/执行期已过/已失效/已到期的文件（不得作为计算依据）；
    # 若命中现行口径且某文件含其 stale_markers（污染的旧规则条文），一并剔除。
    EXPIRED = ("已废止", "已失效", "已到期", "执行期已过")
    ctx_lines = []
    for i in items[:8]:
        st = i.get("status") or ""
        if st in EXPIRED:
            continue
        kp = "；".join(str(x) for x in (i.get("key_points") or []))
        ob = "；".join(str(x) for x in (i.get("obligations") or []))
        blob = (kp + ob)
        if canon and any(m in blob for m in canon.get("stale_markers", [])):
            continue  # 该文件含被取代的旧规则条文，从上下文剔除
        bits = [f"标题：{i['title']}", f"文号：{i['doc_num'] or '-'}", f"效力：{st}"]
        if i.get("key_points"):
            bits.append("要点：" + "；".join(i["key_points"][:4]))
        if i.get("obligations"):
            bits.append("义务：" + "；".join(i["obligations"][:3]))
        if i.get("amounts"):
            bits.append("金额/比例：" + "；".join(str(x) for x in i["amounts"][:3]))
        if i.get("excerpt"):
            bits.append("相关条文：" + i["excerpt"])
        ctx_lines.append("\n".join(bits))
    ctx = "\n\n".join(ctx_lines)

    # 现行口径（系统核验）作为最高优先级依据，置于最前，并明确禁止旧口径
    if canon:
        canon_block = (
            "【现行口径（系统核验，优先级最高，须以其结论为准）】\n"
            f"主题：{canon['label']}\n"
            f"规则：{canon['rule']}\n"
            f"计算公式：{canon['formula']}\n"
            f"计算示例：{canon['example']}\n"
            f"依据：{canon['source']}\n"
            f"注意：{canon['avoid']}"
        )
        ctx = canon_block + "\n\n" + ctx

    if mock:
        return _mock_llm(q, items)

    system = (
        "你是中国资深财税实务顾问，服务对象是企业的财税/会计人员。"
        "用户会描述一个真实工作场景（通常含税种、业务动作、金额、主体身份等）。\n"
        "你的目标不是「罗列政策」，而是给出用户照着做就能落地的【具体方案与结果】：\n"
        "1) 先用一句话给出直接结论（是他想要的结果，例如「应缴纳增值税 X 元」「填写 A 表并于 B 日前申报」），"
        "不要以「依据某政策」开头；\n"
        "2) 拆解可操作步骤（办什么、去哪办/哪个模块、填什么表、带什么资料）；\n"
        "3) 涉及金额或比例必须给出计算公式，并代入用户在场景中给出的数字给出示例；\n"
        "4) 给出办理时限或申报节点；\n"
        "5) 提示常见风险与误区；\n"
        "6) 最后才列出依据的政策名称与文号。\n"
        "严禁编造政策条文、文号或金额；所有结论必须能从下方「政策依据」中找到支撑。\n"
        "若下方出现【现行口径（系统核验）】块，必须以其结论为准——它已比对权威政策校核过；"
        "若「政策依据」中其他文件与其冲突（尤其是已废止/执行期已过的旧阶梯优惠），一律以"
        "【现行口径】为准，并在 basis 中引用其依据文号，不要引用被取代的旧文件。\n"
        "只返回一个 JSON 对象，字段：summary(一句话结论,字符串), steps(字符串数组), "
        "calc(字符串,计算/金额,可空), deadline(字符串,时限,可空), risks(字符串数组), "
        "basis(字符串数组,政策名+文号)。不要输出任何 JSON 以外的解释性文字。"
    )
    user = f"【用户场景】\n{q}\n\n【可用的政策依据（已按相关性排序，含现行与部分历史文件）】\n{ctx}"
    data = {
        "model": os.environ.get("TAXDB_LLM_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    if "json" in os.environ.get("TAXDB_LLM_FEATURES", ""):
        data["response_format"] = {"type": "json_object"}
    # OpenAI 兼容聊天端点为 /chat/completions；若 .env 填的是基地址则自动补齐
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    js = _call_llm(endpoint, data, key)
    # 智谱业务码（如 1305 拥挤）可能夹在 HTTP 200 的 body 里
    if isinstance(js, dict) and js.get("code") not in (None, 0, 200):
        code = js.get("code")
        msg = js.get("message", "")
        if code == "1305" or "访问量过大" in msg:
            return {"summary": None, "_error": f"模型当前访问量过大(智谱{code})，请稍后重试", "_src": "ai"}
        return {"summary": None, "_error": f"智谱业务错误 {code}: {msg}", "_config_err": True, "_src": "ai"}
    if isinstance(js, dict) and js.get("_error"):
        return js  # 已含 _error / _config_err
    try:
        content = js["choices"][0]["message"]["content"]
    except (KeyError, TypeError, IndexError) as e:
        return {"summary": None, "_error": f"响应格式异常：{e}", "_config_err": True, "_src": "ai"}
    parsed = _parse_llm_json(content)
    if isinstance(parsed, dict):
        parsed["_src"] = "ai"
        if not parsed.get("summary") and not parsed.get("steps"):
            rb = _rule_plan(q, items, "")
            rb["_src"] = "rule"
            rb["_ai_failed"] = True
            return rb
        return parsed
    return {"summary": content, "_src": "ai"}


def api_ask(p):
    q = (p.get("q", [""])[0] or "").strip()
    top = min(12, int(p.get("top", ["6"])[0] or 6))
    if not q:
        return {"error": "empty"}
    kws = extract_keywords(q)
    docnum_kws = [k for k in kws if DOCNUM_RE.match(k)]
    topic_kws = [k for k in kws if not DOCNUM_RE.match(k)]

    c = conn()
    cur = c.cursor()
    SEL = ("p.id,p.title,p.doc_num,p.cwrq,p.pub_name,p.effect_level,p.aging,"
           "p.url,p.doc_year,p.clicknum,"
           "a.summary,a.tax_types_x,a.domains,a.risk_level,a.effective_date,"
           "a.expire_date,a.abolished_docs,a.status_final,a.status_source,a.status_evidence,"
           "p.content,a.key_points,a.obligations,a.amount_items")

    def _fetch(wh, whargs):
        return cur.execute(
            f"SELECT {SEL} FROM policies p LEFT JOIN analysis a ON a.policy_id=p.id "
            f"WHERE {wh} LIMIT 200", whargs).fetchall()

    scored = []
    retrieval = "keyword"
    # ---- ① 语义向量召回（优先）----
    # 把问题编码成向量，按余弦相似度召回最相关的政策；模型看到的是"意思贴合"的文件，
    # 而非只能靠关键词字面匹配。失败/未建索引时自动降级到下方关键词召回。
    sem = []
    try:
        sem = search_semantic(q, top_k=RECALL_K)
    except Exception:
        sem = []
    if sem:
        retrieval = "semantic"
        sim_map = {pid: s for pid, s in sem}
        ph = ",".join("?" * len(sem))
        rows = cur.execute(
            f"SELECT {SEL} FROM policies p LEFT JOIN analysis a ON a.policy_id=p.id "
            f"WHERE p.id IN ({ph})", [pid for pid, _ in sem]).fetchall()
        rows_by_id = {r["id"]: r for r in rows}
        for pid, sim in sem:
            r = rows_by_id.get(pid)
            if not r:
                continue
            st = r["status_final"] or ""
            sc = sim * 10.0
            hit_kw = [k for k in kws
                      if (k in (r["title"] or "")) or (k in (r["doc_num"] or ""))]
            for dk in docnum_kws:  # 用户明确给文号时强制置顶
                if dk in (r["doc_num"] or "") or dk in (r["title"] or ""):
                    sc += 10; hit_kw = list(dict.fromkeys(hit_kw + [dk]))
            if st in ("有效", "推定有效"):
                sc += 2.5
            elif st == "未见废止记录":
                sc += 1.5
            elif st in ("已废止", "已失效", "已到期"):
                sc -= 2.5
            # 含"减按X%计入/计算应纳税所得额"等现行税率优惠条文的政策，通常是计算题的
            # 真正答案所在；现行口径(减按25%)重点加权，使其排到仅讲征管/申报表的边角文件之前
            kc = (r["key_points"] or "") + (r["obligations"] or "")
            if "减按25%" in kc:
                sc += 2.0
            elif "减按" in kc:
                sc += 0.5
            matched = 1 if hit_kw else 0
            scored.append((sc, matched, list(dict.fromkeys(hit_kw)), r))
        scored.sort(key=lambda x: (-x[0], -x[1]))
    else:
        # ---- ② 关键词 LIKE 召回（兜底）----
        seen, rowsrc = set(), {}
        # 文号精确优先：用户明确给出文号时，该文件必须置顶（即便已废止也先定位，再提示替代）
        for k in docnum_kws:
            pat = f"%{k}%"
            for r in _fetch("(p.doc_num LIKE ? OR p.title LIKE ?)", [pat, pat]):
                if r["id"] not in seen:
                    seen.add(r["id"]); rowsrc[r["id"]] = (r, 1000)
        # 主题词 OR 召回（补充相关但未点名文号的文件）
        if topic_kws:
            where, args = [], []
            for k in topic_kws:
                pat = f"%{k}%"
                where.append("(p.title LIKE ? OR p.doc_num LIKE ? OR a.summary LIKE ? OR p.content LIKE ?)")
                args += [pat, pat, pat, pat]
            for r in _fetch(" OR ".join(where), args):
                if r["id"] not in seen:
                    seen.add(r["id"]); rowsrc[r["id"]] = (r, 0)
        for r, forced in rowsrc.values():
            title = r["title"] or ""; dn = r["doc_num"] or ""
            sm = r["summary"] or ""; ct = r["content"] or ""
            sc = forced; matched = 0; hit_kw = []
            for k in kws:
                in_t = k in title; in_d = k in dn; in_s = k in sm; in_c = k in ct
                if in_t: sc += 5; hit_kw.append(k)
                if in_d: sc += (10 if DOCNUM_RE.match(k) else 4); hit_kw.append(k)
                if in_s: sc += 3; hit_kw.append(k)
                if in_c: sc += 1; hit_kw.append(k)
                if in_t or in_d or in_s or in_c: matched += 1
            if matched == 0:
                continue
            sc += matched * 0.6
            st = r["status_final"] or ""
            if st in ("有效", "推定有效"):
                sc += 2.5
            elif st == "未见废止记录":
                sc += 1.5
            elif st in ("已废止", "已失效", "已到期"):
                sc -= 2.5
            scored.append((sc, matched, list(dict.fromkeys(hit_kw)), r))
        scored.sort(key=lambda x: (-x[0], -x[1]))

    # 既无关键词也无语义召回结果时，提示用户补充场景
    if not scored and not kws:
        c.close()
        return {"q": q, "keywords": [],
                "answer": {"lead": "未能从您的描述中提取到可检索的关键词，请补充更具体的业务场景，"
                                    "例如涉及哪个税种、何种业务动作（开票 / 申报 / 预缴 / 抵扣等）。"},
                "items": []}
    c.close()
    c.close()

    items = []
    for sc, matched, hit_kw, r in scored[:top]:
        excerpt = _excerpt((r["summary"] or "") + "。" + (r["content"] or ""), kws)
        items.append({
            "id": r["id"], "title": r["title"], "doc_num": r["doc_num"],
            "cwrq": r["cwrq"], "pub_name": r["pub_name"],
            "level": r["effect_level"], "aging": r["aging"], "url": r["url"],
            "year": r["doc_year"], "clicks": r["clicknum"],
            "summary": r["summary"] or "", "taxes": jload(r["tax_types_x"], []),
            "domains": jload(r["domains"], []), "risk": r["risk_level"] or "",
            "eff": r["effective_date"] or "", "exp": r["expire_date"] or "",
            "abolished_n": len(jload(r["abolished_docs"], [])),
            "status": r["status_final"] or "", "st_src": r["status_source"] or "",
            "st_ev": r["status_evidence"] or "",
            "excerpt": excerpt, "matched_kw": hit_kw[:6],
            "key_points": jload(r["key_points"], []),
            "obligations": jload(r["obligations"], []),
            "amounts": jload(r["amount_items"], []),
        })

    # ---- 结构化解答合成 ----
    # 现行有效 / 推定有效 / 未见废止记录（仍推定为施行中）均可作为主依据
    USABLE = ("有效", "推定有效", "未见废止记录")
    valid_top = next((it for it in items if it["status"] in USABLE), None)
    abolish_top = next((it for it in items if it["status"] in ("已废止", "已失效", "已到期")), None)
    high_risk = any(it["risk"] == "高" for it in items[:4])
    # 用户若明确给出文号，优先以其查询到的那份文件作为主依据（即使已废止也先定位，再提示替代）
    docnum_top = next((it for it in items
                       if any(DOCNUM_RE.match(k) and k in (it["doc_num"] or "")
                              for k in it.get("matched_kw", []))), None)
    # 主依据优先用现行可用文件；仅当用户点名的文号本身仍有效时才直接以其为主依据
    lead_base = (docnum_top if (docnum_top and docnum_top["status"] in USABLE) else None) or valid_top

    # AI 具体方案：配置了 LLM 则调用，否则用离线规则方案合成（同样给出可落地步骤）
    ai = _llm_answer(q, items)
    llm = ai if ai is not None else _rule_plan(q, items, lead_base["title"] if lead_base else None)

    lead = ""
    points = []
    if lead_base:
        lead = (f"针对您描述的场景，建议主要依据《{lead_base['title']}》"
                f"（{lead_base['doc_num'] or '—'}，当前{lead_base['status']}）处理；"
                f"同时参考下方其他相关政策。")
    elif items:
        lead = "检索到以下相关政策，请结合具体情形核对（排在前面的最相关，含历史文件请注意其效力状态）："

    for it in items[:4]:
        kp = (it["key_points"] or [])[:3]
        ob = (it["obligations"] or [])[:2]
        if kp or ob or it["excerpt"]:
            points.append({
                "title": it["title"], "doc_num": it["doc_num"],
                "status": it["status"], "st_src": it["st_src"],
                "key_points": kp, "obligations": ob, "excerpt": it["excerpt"],
            })

    note = ""; note_type = "warn"
    if docnum_top and docnum_top["status"] not in USABLE:
        note = (f"您查询的文件《{docnum_top['title']}》（{docnum_top['doc_num'] or '—'}）"
                f"当前为「{docnum_top['status']}」，已不再作为有效依据；"
                f"请以上方列出的现行有效替代 / 修订文件为准。")
        note_type = "danger"
    elif abolish_top and not valid_top:
        note = (f"提示：与您场景最相关的文件《{abolish_top['title']}》（{abolish_top['doc_num'] or '—'}）"
                f"当前为「{abolish_top['status']}」，请进一步查找其替代或修订后的现行文件后再行适用。")
        note_type = "danger"
    elif high_risk:
        note = "提示：匹配到的政策中有关注度「高」的合规风险事项，处理时请重点核对原文及主管税务机关口径。"

    answer = {
        "lead": lead,
        "points": points,
        "note": note,
        "note_type": note_type,
        "retrieval": retrieval,
        "disclaimer": "本解答由系统据政策库自动检索与规则合成，仅供参考；实际适用请以政策原文及主管税务机关口径为准。",
        "llm": llm,
        "ai_enabled": bool(ai),
    }
    return {"q": q, "keywords": kws, "answer": answer,
            "retrieval": retrieval,
            "semantic_ready": bool(get_index()), "items": items}


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # 复用连接，配合多线程避免并行请求被中断成空响应

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        p = parse_qs(u.query)
        try:
            if u.path == "/api/stats":
                return self._json(api_stats())
            if u.path == "/api/facets":
                return self._json(api_facets())
            if u.path == "/api/search":
                return self._json(api_search(p))
            if u.path == "/api/ecommerce":
                return self._json(api_ecommerce(p))
            if u.path == "/api/policy":
                return self._json(api_policy(p.get("id", [""])[0]))
            if u.path == "/api/changes":
                return self._json(api_changes(p))
            if u.path == "/api/watch":
                return self._json(api_expiring(p))
            if u.path == "/api/ask":
                return self._json(api_ask(p))
            if u.path == "/api/reload":
                return self._json(api_reload())
            if u.path == "/api/kb":
                return self._json(api_kb(p, method="GET"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if u.path == "/api/kb":
                return self._json(api_kb({}, body=body, method="POST"))
            if u.path == "/api/reload":
                return self._json(api_reload())
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        u = urlparse(self.path)
        p = parse_qs(u.query)
        try:
            if u.path == "/api/kb":
                return self._json(api_kb(p, method="DELETE"))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)
        return self._json({"error": "not found"}, 404)


def api_reload():
    """刷新服务进程内的语义向量索引缓存（每日更新后调用，无需重启服务）。"""
    try:
        reset_cache()
        idx = get_index()
        return {"ok": True, "vectors": len(idx.ids) if idx else 0,
                "reloaded_at": now()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def api_kb(p, body=None, method="GET"):
    """场景解答知识库：GET 列表 / POST 保存 / DELETE 删除"""
    ensure_kb()
    c = conn()
    try:
        if method == "POST":
            scene = (body or {}).get("scene", "").strip()
            if not scene:
                return {"error": "empty"}
            row = (
                scene,
                (body or {}).get("lead", "") or None,
                json.dumps((body or {}).get("points", []), ensure_ascii=False),
                json.dumps((body or {}).get("keywords", []), ensure_ascii=False),
                json.dumps((body or {}).get("refs", []), ensure_ascii=False),
                json.dumps((body or {}).get("llm", None), ensure_ascii=False) if (body or {}).get("llm") else None,
            )
            cur = c.execute(
                "INSERT INTO ask_kb(scene,lead,points,keywords,refs,llm) VALUES(?,?,?,?,?,?)", row)
            c.commit()
            return {"id": cur.lastrowid, "ok": True}
        if method == "DELETE":
            kid = p.get("id", [""])[0]
            if kid:
                c.execute("DELETE FROM ask_kb WHERE id=?", (kid,))
                c.commit()
            return {"ok": True}
        # GET 列表（支持关键词过滤）
        q = (p.get("q", [""])[0] or "").strip()
        if q:
            rows = c.execute(
                "SELECT id,scene,lead,points,keywords,llm,created_at FROM ask_kb "
                "WHERE scene LIKE ? OR lead LIKE ? ORDER BY id DESC",
                (f"%{q}%", f"%{q}%")).fetchall()
        else:
            rows = c.execute(
                "SELECT id,scene,lead,points,keywords,llm,created_at FROM ask_kb "
                "ORDER BY id DESC LIMIT 200").fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r["id"], "scene": r["scene"], "lead": r["lead"],
                "points": jload(r["points"]) if r["points"] else [],
                "keywords": jload(r["keywords"]) if r["keywords"] else [],
                "llm": jload(r["llm"]) if r["llm"] else None,
                "created_at": r["created_at"],
            })
        return {"items": items, "total": len(items)}
    finally:
        c.close()


def main():
    if not os.path.exists(DB_PATH):
        print("数据库不存在，请先运行 crawler.py")
        return
    if os.environ.get("TAXDB_LLM_MOCK") == "1":
        print("  [AI] 演示模式（mock），未调用真实大模型")
    elif os.environ.get("TAXDB_LLM_KEY") and os.environ.get("TAXDB_LLM_URL"):
        print(f"  [AI] 已启用：{os.environ.get('TAXDB_LLM_MODEL', '默认模型')} @ {os.environ.get('TAXDB_LLM_URL')}")
    else:
        print("  [AI] 未配置密钥，场景解答将使用离线规则方案（无大模型）")
    srv = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    url = f"http://{BIND_HOST}:{PORT}/"
    print("=" * 56)
    print("  财税政策数据库已启动")
    print(f"  访问地址：{url}")
    print("  关闭窗口即可停止服务")
    print("=" * 56)
    # 仅在本地（非 PaaS/无 DISPLAY 环境）自动打开浏览器，云端跳过
    if os.environ.get("TAXDB_NO_BROWSER") != "1" and not os.environ.get("PORT"):
        try:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()

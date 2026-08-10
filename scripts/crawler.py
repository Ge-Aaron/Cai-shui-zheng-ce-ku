# -*- coding: utf-8 -*-
"""
财税政策抓取引擎
数据源：国家税务总局政策法规库 (fgk.chinatax.gov.cn)
后端接口：https://www.chinatax.gov.cn/search5/search/s

模式：
  full  全量回溯（1984年至今全部政策法规 + 官方解读）
  incr  增量更新（只扫最新N页，检测新增与变更）
"""
import os
import sys
import time
import random
import argparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_conn, init_db, now, md5, jdump, jload

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = "https://www.chinatax.gov.cn/search5/search/s"
SITE_CODE = "bm29000002"
PAGE_SIZE = 10          # 服务端强制锁定为10
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://fgk.chinatax.gov.cn/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

COLUMNS = {
    "政策法规": "policies",
    "政策解读": "interpretations",
}


def log(msg):
    print(f"[{now()}] {msg}", flush=True)


def build_session():
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.2,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8))
    s.headers.update(HEADERS)
    return s


def fetch_page(session, column, page_num):
    """抓取一页，返回 (total, items)"""
    params = {
        "siteCode": SITE_CODE, "searchWord": "", "type": "",
        "pageSize": PAGE_SIZE, "pageNum": page_num, "orderBy": 5,
        "column": column, "likeDoc": 0, "wordPlace": 0,
        "indexCode": 1, "searchSiteName": "GSFFK",
    }
    r = session.get(API, params=params, timeout=45)
    r.raise_for_status()
    data = r.json().get("searchResultAll", {}) or {}
    return data.get("total", 0), (data.get("searchTotal") or [])


# ---------------- 字段解析 ----------------

def parse_policy(it):
    gd = it.get("govDoc") or {}
    if not isinstance(gd, dict):
        gd = {}
    appendix = it.get("appendix")
    if not isinstance(appendix, list):
        appendix = []
    channel = ""
    cl = it.get("channel_levels_names") or ""
    if "@@@@@" in cl:
        channel = cl.split("@@@@@")[-1]
    content = (it.get("content") or "").strip()
    try:
        clicks = int(it.get("clicknum") or 0)
    except Exception:
        clicks = 0
    return {
        "id": it.get("id") or "",
        "title": (it.get("title") or "").strip(),
        "doc_num": (gd.get("docNum") or "").strip(),
        "doc_type": (gd.get("docType") or "").strip(),
        "doc_year": (gd.get("docYear") or it.get("xxgk_formulatedYear") or "").strip(),
        "pub_name": (it.get("pubName") or "").strip(),
        "cwrq": (it.get("cwrq") or "")[:10],
        "pub_date": (it.get("pubDate") or "")[:19],
        "effect_level": (it.get("xxgk_effectLevel") or "").strip(),
        "aging": (it.get("xxgk_aging") or "").strip(),
        "abolish_date": (it.get("xxgk_abolishDate") or "").strip(),
        "revise_type": (it.get("xxgk_reviseType") or "").strip(),
        "tax_types": jdump(jload(it.get("xxgk_son_taxPolicy"), [])),
        "policy_cat": jdump(jload(it.get("xxgk_taxPolicy"), [])),
        "labels": (it.get("xxgk_labels") or "").strip(),
        "related_names": (it.get("xxgk_relatedPolicyFileName") or "").strip(),
        "content": content,
        "short_content": (it.get("shortContent") or "")[:500],
        "url": it.get("url") or it.get("snapshotUrl") or "",
        "appendix": jdump([{"name": a.get("appendixName"), "url": a.get("appendixUrl"),
                            "type": a.get("appendixType")} for a in appendix if isinstance(a, dict)]),
        "channel": channel,
        "clicknum": clicks,
        "content_hash": md5(content),
    }


def parse_interp(it):
    channel = ""
    cl = it.get("channel_levels_names") or ""
    if "@@@@@" in cl:
        channel = cl.split("@@@@@")[-1]
    content = (it.get("content") or "").strip()
    return {
        "id": it.get("id") or "",
        "title": (it.get("title") or "").strip(),
        "content": content,
        "pub_date": (it.get("pubDate") or "")[:19],
        "cwrq": (it.get("cwrq") or "")[:10],
        "pub_name": (it.get("pubName") or "").strip(),
        "url": it.get("url") or it.get("snapshotUrl") or "",
        "channel": channel,
        "content_hash": md5(content),
    }


# ---------------- 入库 ----------------

POLICY_COLS = ["id", "title", "doc_num", "doc_type", "doc_year", "pub_name", "cwrq",
               "pub_date", "effect_level", "aging", "abolish_date", "revise_type",
               "tax_types", "policy_cat", "labels", "related_names", "content",
               "short_content", "url", "appendix", "channel", "clicknum", "content_hash"]

WATCH_FIELDS = [("aging", "时效性"), ("abolish_date", "废止日期"),
                ("content_hash", "正文内容"), ("title", "标题")]


def upsert_policy(conn, rec):
    """返回 'new' / 'updated' / 'same'"""
    if not rec["id"] or not rec["title"]:
        return "skip"
    c = conn.cursor()
    old = c.execute("SELECT * FROM policies WHERE id=?", (rec["id"],)).fetchone()
    t = now()
    if old is None:
        cols = POLICY_COLS + ["first_seen", "last_seen", "updated_at"]
        vals = [rec[k] for k in POLICY_COLS] + [t, t, t]
        c.execute(f"INSERT INTO policies ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
        c.execute("INSERT INTO policies_fts (id,title,doc_num,content) VALUES (?,?,?,?)",
                  (rec["id"], rec["title"], rec["doc_num"], rec["content"]))
        c.execute("""INSERT INTO changes (policy_id,title,doc_num,change_type,field,
                     old_value,new_value,detected_at) VALUES (?,?,?,?,?,?,?,?)""",
                  (rec["id"], rec["title"], rec["doc_num"], "new", "", "", "", t))
        return "new"

    diffs = []
    for f, cn in WATCH_FIELDS:
        ov, nv = (old[f] or ""), (rec[f] or "")
        if ov != nv:
            diffs.append((f, cn, ov, nv))
    if not diffs:
        c.execute("UPDATE policies SET last_seen=?, clicknum=? WHERE id=?",
                  (t, rec["clicknum"], rec["id"]))
        return "same"

    sets = ",".join([f"{k}=?" for k in POLICY_COLS if k != "id"])
    vals = [rec[k] for k in POLICY_COLS if k != "id"] + [t, t, rec["id"]]
    c.execute(f"UPDATE policies SET {sets}, last_seen=?, updated_at=? WHERE id=?", vals)
    c.execute("DELETE FROM policies_fts WHERE id=?", (rec["id"],))
    c.execute("INSERT INTO policies_fts (id,title,doc_num,content) VALUES (?,?,?,?)",
              (rec["id"], rec["title"], rec["doc_num"], rec["content"]))
    for f, cn, ov, nv in diffs:
        ct = "abolish" if (f == "aging" and "废止" in nv) else \
             ("aging" if f == "aging" else ("content" if f == "content_hash" else "meta"))
        if f == "content_hash":
            ov, nv = "(旧版本)", "(已修订)"
        c.execute("""INSERT INTO changes (policy_id,title,doc_num,change_type,field,
                     old_value,new_value,detected_at) VALUES (?,?,?,?,?,?,?,?)""",
                  (rec["id"], rec["title"], rec["doc_num"], ct, cn, ov, nv, t))
    return "updated"


def upsert_interp(conn, rec):
    if not rec["id"] or not rec["title"]:
        return "skip"
    c = conn.cursor()
    old = c.execute("SELECT content_hash FROM interpretations WHERE id=?", (rec["id"],)).fetchone()
    t = now()
    if old is None:
        c.execute("""INSERT INTO interpretations (id,title,content,pub_date,cwrq,pub_name,
                     url,channel,content_hash,first_seen,last_seen)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (rec["id"], rec["title"], rec["content"], rec["pub_date"], rec["cwrq"],
                   rec["pub_name"], rec["url"], rec["channel"], rec["content_hash"], t, t))
        return "new"
    if old["content_hash"] != rec["content_hash"]:
        c.execute("""UPDATE interpretations SET title=?,content=?,pub_date=?,cwrq=?,pub_name=?,
                     url=?,channel=?,content_hash=?,last_seen=? WHERE id=?""",
                  (rec["title"], rec["content"], rec["pub_date"], rec["cwrq"], rec["pub_name"],
                   rec["url"], rec["channel"], rec["content_hash"], t, rec["id"]))
        return "updated"
    c.execute("UPDATE interpretations SET last_seen=? WHERE id=?", (t, rec["id"]))
    return "same"


# ---------------- 主流程 ----------------

def crawl_column(conn, session, column, max_pages=None, start_page=0,
                 stop_when_all_same=0, delay=(0.6, 1.3)):
    kind = COLUMNS[column]
    started = now()
    total, _ = fetch_page(session, column, 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pages = total_pages if max_pages is None else min(max_pages, total_pages)
    log(f"【{column}】总计 {total} 条 / {total_pages} 页，本次抓取 {pages} 页（从第 {start_page+1} 页开始）")

    n_new = n_upd = n_fetched = 0
    same_streak = 0
    fails = 0

    for pn in range(start_page, pages):
        try:
            _, items = fetch_page(session, column, pn)
        except Exception as e:
            fails += 1
            log(f"  ! 第 {pn+1} 页失败({fails}): {str(e)[:90]}")
            if fails >= 12:
                log("  连续失败过多，中止本栏目")
                break
            time.sleep(4 + fails * 1.5)
            continue

        if not items:
            log(f"  第 {pn+1} 页无数据，判定到底")
            break

        page_new = page_upd = 0
        for it in items:
            rec = parse_policy(it) if kind == "policies" else parse_interp(it)
            r = upsert_policy(conn, rec) if kind == "policies" else upsert_interp(conn, rec)
            n_fetched += 1
            if r == "new":
                page_new += 1
            elif r == "updated":
                page_upd += 1
        conn.commit()
        n_new += page_new
        n_upd += page_upd

        if page_new == 0 and page_upd == 0:
            same_streak += 1
        else:
            same_streak = 0

        if (pn + 1) % 20 == 0 or page_new or page_upd:
            log(f"  第 {pn+1}/{pages} 页 | 本页新增{page_new} 更新{page_upd} | 累计 新增{n_new} 更新{n_upd}")

        if stop_when_all_same and same_streak >= stop_when_all_same:
            log(f"  连续 {same_streak} 页无变化，增量模式提前结束")
            break

        time.sleep(random.uniform(*delay))

    conn.execute("""INSERT INTO crawl_log (started_at,finished_at,mode,column_name,pages,
                    fetched,new_count,updated_count,status,message)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (started, now(), "full" if stop_when_all_same == 0 else "incr", column,
                  pages, n_fetched, n_new, n_upd, "ok" if fails < 12 else "partial",
                  f"fails={fails}"))
    conn.commit()
    log(f"【{column}】完成：抓取{n_fetched} 新增{n_new} 更新{n_upd}")
    return n_new, n_upd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "incr"], default="incr")
    ap.add_argument("--pages", type=int, default=None, help="最大页数")
    ap.add_argument("--start", type=int, default=0, help="起始页(0基)")
    ap.add_argument("--columns", default="政策法规,政策解读")
    ap.add_argument("--delay", type=float, default=0.9)
    args = ap.parse_args()

    init_db()
    conn = get_conn()
    session = build_session()

    if args.mode == "incr":
        pages = args.pages or 12
        stop = 4
    else:
        pages = args.pages
        stop = 0

    tn = tu = 0
    for col in [c.strip() for c in args.columns.split(",") if c.strip()]:
        if col not in COLUMNS:
            log(f"跳过未知栏目: {col}")
            continue
        try:
            a, b = crawl_column(conn, session, col, max_pages=pages, start_page=args.start,
                                stop_when_all_same=stop,
                                delay=(args.delay * 0.7, args.delay * 1.4))
            tn += a
            tu += b
        except Exception as e:
            log(f"栏目 {col} 异常: {e}")

    log(f"===== 抓取结束 | 总新增 {tn} 总更新 {tu} =====")
    conn.close()
    return tn, tu


if __name__ == "__main__":
    main()

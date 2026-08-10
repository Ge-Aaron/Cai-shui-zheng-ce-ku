# -*- coding: utf-8 -*-
"""时效推断误差诊断：拿官方已标注的记录回测，定位错误来源"""
import os
import sys
import datetime
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_conn
from analyzer import infer_one

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

conn = get_conn()
c = conn.cursor()
today = datetime.date.today().isoformat()

killers = defaultdict(list)
for r in c.execute("""SELECT r.tgt_id,r.rel_type,r.confidence,r.evidence,p.title s_title,
                             p.doc_num s_doc,p.pub_date s_pub,p.effect_level s_level
                      FROM relations r JOIN policies p ON p.id=r.src_id
                      WHERE r.tgt_id!='' AND r.rel_type IN ('abolish','supersede','revise')"""):
    killers[r["tgt_id"]].append((r["rel_type"], r["s_title"] or "", r["s_doc"] or "",
                                 r["confidence"] or 0, r["s_pub"] or "", r["s_level"] or "",
                                 r["evidence"] or ""))

rows = c.execute("""SELECT p.id,p.title,p.doc_num,p.pub_date,p.aging,p.effect_level,
                           a.expire_date,a.effective_date
                    FROM policies p JOIN analysis a ON a.policy_id=p.id
                    WHERE p.aging IN ('全文有效','全文废止')""").fetchall()

buckets = Counter()
samples = defaultdict(list)
miss = []          # 漏报清单，供进一步追因

for r in rows:
    st, src, ev = infer_one(r["id"], r["pub_date"], r["expire_date"],
                            r["effective_date"], killers.get(r["id"], []), today,
                            r["effect_level"] or "")
    off = "废止" if r["aging"] == "全文废止" else "有效"
    pred_dead = (st == "已废止")
    ok = (pred_dead == (off == "废止"))
    key = f"{off}->{st}{'  OK' if ok else '  ERR'}"
    buckets[key] += 1
    if not ok and len(samples[key]) < 4:
        samples[key].append((r["title"][:50], ev[:95]))
    if off == "废止" and not pred_dead:
        miss.append((r["id"], r["title"], r["doc_num"]))

print(f"回测样本：{len(rows)} 件\n")
print("=== 结果分类（ERR 为误判）===")
for k, n in buckets.most_common():
    print(f"  {k:<30} {n:>5}")

print("\n=== 误判样本 ===")
for k in [x for x in buckets if "ERR" in x]:
    print(f"\n--- {k} ({buckets[k]}件) ---")
    for t, e in samples[k]:
        print(f"  · {t}")
        print(f"    依据: {e}")

# ---- 漏报追因：库里到底有没有文件提到它 ----
print(f"\n\n=== 漏报追因（共 {len(miss)} 件，抽查 6 件）===")
import re
for pid, title, dnum in miss[:6]:
    print(f"\n· {title[:52]} | {dnum}")
    key = title[:26]
    hit = c.execute("""SELECT title,doc_num,content FROM policies
                       WHERE content LIKE ? AND id!=? LIMIT 1""",
                    ('%' + key + '%', pid)).fetchone()
    if hit:
        idx = hit["content"].find(key)
        ctx = re.sub(r"\s+", " ", hit["content"][max(0, idx - 130):idx + 110])
        print(f"    被提及于：{hit['doc_num']} | {hit['title'][:36]}")
        print(f"    上下文：...{ctx}...")
    else:
        hit2 = c.execute("""SELECT title,doc_num FROM policies
                            WHERE content LIKE ? AND id!=? LIMIT 1""",
                         ('%' + (dnum or 'zzz') + '%', pid)).fetchone()
        if hit2:
            print(f"    标题未被提及，但文号被 {hit2['doc_num']} 提及")
        else:
            print("    !! 全库无任何文件提及其标题或文号（废止信息可能在附件/目录中）")
conn.close()

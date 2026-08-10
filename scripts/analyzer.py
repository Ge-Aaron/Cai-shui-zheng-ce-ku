# -*- coding: utf-8 -*-
"""
财税政策自动解读分析引擎（规则层）

无需调用大模型即可对每一件政策自动完成：
  1. 文号识别与引用抽取
  2. 生效日期 / 执行期限 / 溯及力判定
  3. 废止·替代·修订关系识别 —— 新旧政策对比的核心
  4. 税种归类增强 + 六大关注领域打标
  5. 核心要点抽取（按条款切分）
  6. 纳税人义务与办税动作提取
  7. 税率/金额/比例等量化要素提取
  8. 关注度分级（高/中/低）
"""
import os
import re
import sys
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import get_conn, init_db, now, jdump, jload

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------------- 文号正则 ----------------
DOC_PATTERNS = [
    r"[\u4e00-\u9fa5]{2,14}〔\d{4}〕\s*\d+\s*号",            # 财税〔2016〕36号
    r"[\u4e00-\u9fa5]{2,14}\[\d{4}\]\s*\d+\s*号",
    r"[\u4e00-\u9fa5]{2,30}公告\s*\d{4}\s*年第\s*\d+\s*号",   # 国家税务总局公告2026年第16号
    r"[（(]\s*\d{2,4}\s*[）)]\s*[\u4e00-\u9fa5]{2,12}字?\s*第?\s*\d+\s*号",  # (89)国税地字第013号
    r"国务院令第\s*\d+\s*号",
    r"中华人民共和国主席令第?\s*[\d一二三四五六七八九十百]+\s*号",
    r"\d{4}\s*年第\s*\d+\s*号",                              # 2020年第35号（简写）
]
DOC_RE = re.compile("|".join(f"(?:{p})" for p in DOC_PATTERNS))

# 书名号内文件名
BOOK_RE = re.compile(r"《([^《》]{4,80})》")

# ---------------- 失效/废止 触发词 ----------------
ABOLISH_WORDS = ["废止", "停止执行", "不再执行", "同时失效", "予以废止", "全文失效", "作废"]
REVISE_WORDS = ["修改为", "修订为", "予以修改", "作如下修改", "修改如下", "删去", "增加一条", "调整为"]
SUPERSEDE_WORDS = ["以本公告为准", "以本通知为准", "按本公告执行", "不一致的，以本"]

# ---------------- 六大关注领域词典 ----------------
DOMAIN_RULES = {
    "增值税·发票管理": [
        "增值税", "进项税", "销项税", "抵扣", "专用发票", "普通发票", "电子发票", "数电",
        "全电发票", "留抵退税", "征收率", "简易计税", "免抵退", "红字发票", "发票开具",
        "一般纳税人", "小规模纳税人", "视同销售", "差额征税", "即征即退", "出口退税",
    ],
    "企业所得税·税收优惠": [
        "企业所得税", "税前扣除", "研发费用", "加计扣除", "高新技术企业", "小型微利",
        "亏损结转", "税收优惠", "减免税", "加速折旧", "应纳税所得额", "税收抵免",
        "西部大开发", "软件企业", "集成电路", "创业投资", "捐赠扣除", "资产损失",
    ],
    "个税·社保·用工": [
        "个人所得税", "专项附加扣除", "综合所得", "经营所得", "劳务报酬", "稿酬",
        "全年一次性奖金", "年终奖", "社会保险费", "社保", "住房公积金", "工资薪金",
        "居民个人", "非居民个人", "股权激励", "汇算清缴",
    ],
    "征管稽查·合规风险": [
        "税收征收管理", "稽查", "偷税", "逃避缴纳税款", "虚开", "骗税", "处罚", "滞纳金",
        "纳税信用", "反避税", "税务检查", "追缴", "金税", "风险管理", "失信", "违法",
        "行政处罚", "移送", "关联交易", "特别纳税调整", "涉税专业服务",
    ],
    "开票类型·票种": [
        "发票种类", "票种核定", "代开发票", "红冲", "发票作废", "机动车发票", "通行费发票",
        "农产品收购发票", "电子发票服务平台", "乐企", "发票额度", "开票限额", "税收分类编码",
    ],
    "纳税申报·办税": [
        "纳税申报", "申报表", "预缴", "申报期限", "延期申报", "更正申报", "报送",
        "税款缴纳", "纳税期限", "扣缴义务", "汇总纳税", "跨区域", "税务登记", "办税",
    ],
}

# ---------------- 税种词典 ----------------
# 只使用税种的“专名”作为判据，避免“应纳税所得额”“税前扣除”这类
# 多税种共用概念造成误判（例如离岸信托个税被误标为企业所得税）。
TAX_KEYWORDS = {
    "增值税": ["增值税"],
    "企业所得税": ["企业所得税"],
    "个人所得税": ["个人所得税", "个税"],
    "消费税": ["消费税"],
    "印花税": ["印花税"],
    "房产税": ["房产税"],
    "城镇土地使用税": ["城镇土地使用税"],
    "土地增值税": ["土地增值税"],
    "契税": ["契税"],
    "车辆购置税": ["车辆购置税"],
    "车船税": ["车船税"],
    "资源税": ["资源税"],
    "环境保护税": ["环境保护税", "环保税"],
    "耕地占用税": ["耕地占用税"],
    "城市维护建设税": ["城市维护建设税", "城建税"],
    "烟叶税": ["烟叶税"],
    "船舶吨税": ["船舶吨税"],
    "关税": ["关税"],
    "社会保险费": ["社会保险费", "社保费", "养老保险费", "失业保险费"],
    "非税收入": ["教育费附加", "残疾人就业保障金", "水利建设基金", "文化事业建设费"],
}

# ---------------- 义务/动作 触发词 ----------------
OBLIGATION_WORDS = [
    "应当", "必须", "不得", "应按", "需要", "应在", "留存备查", "报送", "备案",
    "提交", "填报", "申报", "取得", "建立", "保存",
]

# ---------------- 高关注词 ----------------
HIGH_RISK_WORDS = ["处罚", "偷税", "虚开", "骗税", "追缴", "移送", "刑事", "违法犯罪",
                   "失信", "停止执行", "废止", "取消", "从严", "严厉打击", "专项整治"]
MID_RISK_WORDS = ["调整", "修改", "新增", "提高", "降低", "扩大", "延续", "试点",
                  "优惠", "减免", "退税", "加计", "期限"]

CN_NUM = "一二三四五六七八九十"
CLAUSE_RE = re.compile(r"[　\s]*([" + CN_NUM + r"]{1,3}[十百]?[" + CN_NUM + r"]{0,2})、")


def clean(t):
    return re.sub(r"[\s\u3000\u2002\u00a0]+", " ", t or "").strip()


def extract_doc_nums(text):
    """提取正文中出现的所有文号（去重保序）"""
    out, seen = [], set()
    for m in DOC_RE.finditer(text or ""):
        d = re.sub(r"\s+", "", m.group(0))
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def extract_effective(text):
    """提取生效日期 / 执行期限 / 溯及力"""
    t = text or ""
    eff = exp = period = ""
    retro = 0

    m = re.search(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起?\s*"
                  r"(?:至|到)\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    if m:
        eff = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        exp = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
        period = f"{eff} 至 {exp}"
    if not eff:
        m = re.search(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起"
                      r"[^。；]{0,12}?(?:施行|执行|实施|生效|开始)", t)
        if m:
            eff = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if not eff:
        m = re.search(r"(?:本(?:公告|通知|办法|规定|决定|意见|细则))\s*自\s*"
                      r"(?:发布|印发|公布|签发)之日起\s*(?:施行|执行|实施|生效)", t)
        if m:
            eff = "发布之日起施行"
    if not eff:
        m = re.search(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起", t)
        if m:
            eff = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    if not exp:
        m = re.search(r"(?:执行(?:期限)?(?:截止)?到|有效期至|截至|执行至)\s*"
                      r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
        if m:
            exp = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    if re.search(r"(?:溯及|追溯|自.{0,20}起执行.{0,20}已(?:缴纳|征收)|多缴(?:的)?税款"
                 r"|可(?:予)?退还|重新计算|以前年度)", t):
        retro = 1
    if not period and eff and exp:
        period = f"{eff} 至 {exp}"
    return eff, exp, period, retro


# 「根据/依据/按照…〔X〕Y号…」——这类是立法依据，不是被废止对象
BASIS_RE = re.compile(r"(根据|依据|按照|参照|遵照|按)[^。；！？]{0,80}$")
# 否定式废止表述，出现则该窗口不作废止判定
NEG_ABOLISH_RE = re.compile(r"(不再?\s*(予以)?\s*废止|继续(有效|执行)|仍(然)?(有效|执行|适用)|未(予)?废止)")


def _is_basis_ref(win, pos):
    """判断窗口 win 中位于 pos 的文号是否只是「根据……」式的立法依据引用"""
    head = win[max(0, pos - 90):pos]
    # 去掉夹在中间的书名号标题，避免长标题冲掉「根据」
    head_nb = re.sub(r"《[^》]{0,80}》", "", head)
    return bool(BASIS_RE.search(head_nb) or BASIS_RE.search(head))


# 文号后紧跟这些词说明是「参照执行」而非「被废止」
FOLLOW_CITE_RE = re.compile(r"^\s*[）)]?\s*(的规定|规定|的)?\s*(执行|办理|处理|适用|计算|申报)")


def _crosses_sentence(win, a1, a2, b1, b2):
    """
    触发词区间 [a1,a2) 与文号区间 [b1,b2) 之间是否跨越句号。

    公文里「《A》（文号）的规定执行。……《B》（文号）同时废止。」这种写法，
    前一个文号只是被引用，与「废止」分属两句。不跨句号是判定废止的强约束。
    """
    lo, hi = (a2, b1) if a2 <= b1 else (b2, a1)
    if lo >= hi:
        return False
    return "。" in win[lo:hi]


# 公文标准写法：《标题》（文号）——标题唯一，是最可靠的锚点
TITLE_DOC_RE = re.compile(r"《([^《》]{4,120})》\s*[（(]\s*([^（）()《》]{4,60}?)\s*[）)]")


def norm_title(s):
    """标题归一化：全半角括号、空格、书名号统一，便于跨文匹配"""
    s = (s or "").strip()
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("〔", "[").replace("〕", "]")
    s = re.sub(r"[\s　]+", "", s)
    return s


def extract_relations(text, self_doc):
    """
    识别本文与旧文件的关系 —— 新旧对比核心
    返回 [(tgt_doc_num, tgt_title, rel_type, evidence, confidence), ...]

    关键：正文里的文号常写简写（如「2023年第1号」），而简写在全库内会撞车
    （税务总局公告/通告、财政部公告都可能是「2023年第1号」）。
    因此以书名号中的标题为主锚点，文号仅作辅助。
    """
    t = text or ""
    best = {}   # key -> (conf, doc, title, evidence)

    def add(dn, title, rt, ev, conf):
        dn = re.sub(r"\s+", "", dn or "")
        title = (title or "").strip()
        if dn and dn == self_doc:
            return
        if not dn and not title:
            return
        k = (norm_title(title) or dn, rt)
        old = best.get(k)
        if old and old[0] >= conf:
            return
        best[k] = (conf, dn, title, clean(ev)[:220])

    def scan(words, rel_type, back=180, fwd=120, base=70):
        for w in words:
            for m in re.finditer(re.escape(w), t):
                s = max(0, m.start() - back)
                e = min(len(t), m.end() + fwd)
                win = t[s:e]
                if rel_type == "abolish" and NEG_ABOLISH_RE.search(win):
                    continue
                wpos = m.start() - s              # 触发词在窗口内的位置
                wend = m.end() - s
                covered = []                      # 已被《标题》（文号）配对消费的区间

                # ① 优先识别「《标题》（文号）」配对 —— 置信度最高
                for pm in TITLE_DOC_RE.finditer(win):
                    if _is_basis_ref(win, pm.start()):
                        continue
                    if _crosses_sentence(win, wpos, wend, pm.start(), pm.end()):
                        continue                  # 与触发词分属两句，只是引用
                    if FOLLOW_CITE_RE.match(win[pm.end():pm.end() + 12]):
                        continue                  # 「……（文号）的规定执行」式引用
                    title = pm.group(1)
                    doc = pm.group(2).strip()
                    if not DOC_RE.search(doc):
                        doc = ""                  # 括号内不是文号（可能是"试行"等）
                    dist = abs(pm.start() - wpos)
                    conf = base + 20 + max(0, 10 - dist // 12)   # 配对模式加权
                    add(doc, title, rel_type, win, min(conf, 99))
                    covered.append((pm.start(), pm.end()))

                # ② 再找未被配对覆盖的裸文号
                for dm in DOC_RE.finditer(win):
                    if any(a <= dm.start() < b for a, b in covered):
                        continue
                    if _is_basis_ref(win, dm.start()):
                        continue
                    if _crosses_sentence(win, wpos, wend, dm.start(), dm.end()):
                        continue
                    if FOLLOW_CITE_RE.match(win[dm.end():dm.end() + 12]):
                        continue
                    dist = abs(dm.start() - wpos)
                    conf = base + max(0, 25 - dist // 8)
                    add(dm.group(0), "", rel_type, win, min(conf, 95))

    scan(ABOLISH_WORDS, "abolish", base=70)
    scan(REVISE_WORDS, "revise", back=150, fwd=80, base=58)
    scan(SUPERSEDE_WORDS, "supersede", back=200, fwd=60, base=55)

    # 其余引用
    for dn in extract_doc_nums(t):
        if not any(k[0] == dn for k in best):
            idx = t.find(dn)
            ev = t[max(0, idx - 60):idx + 60] if idx >= 0 else ""
            add(dn, "", "cite", ev, 40)

    return [(doc, title, rt, ev, conf)
            for (_k, rt), (conf, doc, title, ev) in best.items()]


LEAD_WORDS = ("公告如下", "通知如下", "规定如下", "意见如下", "现将", "现就", "现对",
              "为贯彻", "为落实", "为进一步", "根据《", "各省、", "特此公告", "附件")
# 出现在句中任意位置即判定为公文引导段，整段丢弃
LEAD_ANYWHERE = ("公告如下", "通知如下", "规定如下", "意见如下", "办法如下",
                 "现就", "现将", "现对", "特此公告", "特此通知")
LEAD_PREFIX_RE = re.compile(r"^(?:为[^，。；]{2,40}[，,]|根据[^，。；]{2,60}[，,]|各省)")


def _truncate(s, n=150):
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip("，、；,") + "…"


def extract_key_points(text, limit=8):
    """按条款序号切分正文，抽取每条要点（超长截断而非丢弃）"""
    t = clean(text)
    if not t:
        return []
    pts = []
    marks = list(CLAUSE_RE.finditer(t))
    if len(marks) >= 2:
        for i, m in enumerate(marks[:limit]):
            s = m.end()
            e = marks[i + 1].start() if i + 1 < len(marks) else len(t)
            seg = t[s:e].strip()
            first = re.split(r"[。；]", seg)[0].strip()
            if len(first) < 8:
                first = seg[:160]
            if len(first) >= 8:
                pts.append(f"{m.group(1)}、{_truncate(first)}")
    if not pts:
        for seg in re.split(r"[。；]", t):
            seg = seg.strip()
            if len(seg) >= 15 and not seg.startswith(LEAD_WORDS):
                pts.append(_truncate(seg))
            if len(pts) >= 5:
                break
    return pts[:limit]


SUBJ_WORDS = ["纳税人", "扣缴义务人", "缴费人", "企业", "个人", "股东", "受托人",
              "退税商店", "代理机构", "税务机关", "申报", "留存", "报送", "备案",
              "开具", "取得", "建立", "填报", "提交"]


def extract_obligations(text, limit=6):
    """提取纳税人义务与办税动作（剔除公文引导句）"""
    t = clean(text)
    out, seen = [], set()
    for seg in re.split(r"[。；]", t):
        seg = seg.strip()
        # 去掉条款序号前缀
        seg = re.sub(r"^[" + CN_NUM + r"]{1,3}[十百]?[" + CN_NUM + r"]{0,2}、", "", seg).strip()
        seg = re.sub(r"^[（(][" + CN_NUM + r"\d]{1,3}[）)]", "", seg).strip()
        if not (12 <= len(seg) <= 160):
            continue
        if any(w in seg for w in LEAD_ANYWHERE):
            continue
        if LEAD_PREFIX_RE.match(seg):
            continue
        if any(seg.startswith(w) for w in LEAD_WORDS):
            continue
        if not any(w in seg for w in OBLIGATION_WORDS):
            continue
        if not any(w in seg for w in SUBJ_WORDS):
            continue
        k = seg[:24]
        if k in seen:
            continue
        seen.add(k)
        out.append(_truncate(seg))
        if len(out) >= limit:
            break
    return out


def extract_amounts(text, limit=12):
    """提取税率、比例、金额等量化要素"""
    t = text or ""
    out, seen = [], set()
    pats = [
        r"(?:税率|征收率|预征率|扣除率|比例)\s*(?:为|按照|按|是)?\s*\d+(?:\.\d+)?%",
        r"\d+(?:\.\d+)?%\s*(?:的)?(?:税率|征收率|预征率|加计|扣除|计入)",
        r"(?:减按|加计|按照|减征|征收)\s*\d+(?:\.\d+)?%",
        r"(?:不超过|超过|不低于|低于|高于|达到|不足)\s*\d+(?:\.\d+)?%",
        r"(?:不超过|超过|不低于|低于|高于|达到)\s*\d+(?:\.\d+)?\s*(?:万元|亿元|元)",
        r"\d+(?:\.\d+)?\s*(?:万元|亿元)(?:以下|以上)?",
        r"(?:免征|免税额|起征点|扣除标准)[^，。；]{0,10}?\d+(?:\.\d+)?\s*(?:万元|元|%)",
    ]
    for p in pats:
        for m in re.finditer(p, t):
            v = clean(m.group(0))
            if v not in seen:
                seen.add(v)
                out.append(v)
            if len(out) >= limit:
                return out
    return out


# 税种专属领域：主税种不匹配时不得打该标签，
# 防止“消费税征管文件因正文提到增值税专用发票而被标为增值税领域”。
DOMAIN_TAX_GUARD = {
    "增值税·发票管理": {"增值税"},
    "企业所得税·税收优惠": {"企业所得税"},
    "个税·社保·用工": {"个人所得税", "社会保险费"},
}
# 跨税种通用领域，不受税种约束
FREE_DOMAINS = {"征管稽查·合规风险", "开票类型·票种", "纳税申报·办税"}


def tag_domains(title, content, labels, tax_types=None):
    """
    六大关注领域打标。
    规则：标题为强信号；正文命中做饱和处理；税种专属领域施加一致性校验。
    """
    body = content[:6000]
    tax_types = set(tax_types or [])
    scores = {}
    for dom, kws in DOMAIN_RULES.items():
        s = 0
        for kw in kws:
            if kw in title:
                s += 10
            if kw in labels:
                s += 6
            c = body.count(kw)
            if c:
                s += min(c, 4)          # 正文饱和，单词最多计4分
        if not s:
            continue
        guard = DOMAIN_TAX_GUARD.get(dom)
        if guard and tax_types and not (tax_types & guard):
            # 主税种不属于该领域：仅当标题出现该领域标志词才保留，且降权
            flag = {"增值税·发票管理": ("发票", "开票"),
                    "企业所得税·税收优惠": ("税收优惠", "减免税", "加计扣除"),
                    "个税·社保·用工": ("个人所得税", "社保", "工资")}[dom]
            if any(f in title for f in flag):
                s = int(s * 0.5)
            else:
                continue
        scores[dom] = s

    if not scores:
        # 兜底：用主税种生成领域标签，保证每件政策都可归类
        if tax_types:
            return ["其他税种·" + sorted(tax_types)[0]]
        return ["综合·其他"]

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top = ranked[0][1]
    doms = [d for d, v in ranked if v >= top * 0.45][:2]
    # 若命中的全是通用领域而税种明确，补一个税种领域便于检索
    if tax_types and all(d in FREE_DOMAINS for d in doms):
        main = sorted(tax_types)[0]
        if not any(main in d for d in doms):
            doms.append("其他税种·" + main)
    return doms[:3]


def enrich_tax_types(title, content, src_types, labels=""):
    """
    税种标注。优先级：源站税种字段 > 源站标签 > 标题 > 正文强命中。
    源站已给出税种时直接采信，不再用正文猜测。
    """
    src = [t for t in (src_types or []) if t and t not in ("税收政策", "税费征管")]
    if src:
        return src[:6]

    types = []
    for part in re.split(r"[,，、;；]", labels or ""):
        part = part.strip()
        if part in TAX_KEYWORDS and part not in types:
            types.append(part)
    if types:
        return types[:6]

    for tax, kws in TAX_KEYWORDS.items():
        if any(kw in title for kw in kws):
            types.append(tax)
    if types:
        return types[:6]

    body = content[:5000]
    for tax, kws in TAX_KEYWORDS.items():
        if any(body.count(kw) >= 3 for kw in kws):
            types.append(tax)
    return types[:6]


RECENT_LEVELS = ("税务规范性文件", "财税文件", "税务部门规章", "法律", "行政法规", "国务院文件")


def assess_risk(title, content, aging, domains, doc_year="", effect_level=""):
    """关注度分级：结合触发词、时效状态、效力级次与发布年份"""
    body = content[:5000]
    hit_hi_title = [w for w in HIGH_RISK_WORDS if w in title]
    hi = sum(min(body.count(w), 3) for w in HIGH_RISK_WORDS)
    mid = sum(min(body.count(w), 2) for w in MID_RISK_WORDS)

    reasons = []
    level = "低"

    if hit_hi_title or hi >= 6:
        level = "高"
        reasons.append("涉及" + "、".join(hit_hi_title[:3] or ["处罚稽查类要求"]))
    elif "征管稽查·合规风险" in domains and hi >= 3:
        level = "高"
        reasons.append("征管稽查与合规风险相关")
    elif mid >= 4 or any(w in title for w in ["调整", "新增", "提高", "降低", "延续",
                                              "优惠", "试点", "扩大", "完善"]):
        level = "中"
        reasons.append("涉及政策口径调整或优惠变动")

    # 近三年发布的正式效力文件，基线不低于“中”
    try:
        y = int(doc_year) if doc_year else 0
    except ValueError:
        y = 0
    if level == "低" and y >= 2024 and effect_level in RECENT_LEVELS:
        level = "中"
        reasons.append("近期发布的现行有效文件")

    if aging and "废止" in aging:
        level = "低"
        reasons = ["文件已废止，仅供政策沿革参考"]
    elif aging == "尚未生效":
        level = "高"
        reasons.append("尚未生效，需提前准备")
    elif aging == "部分失效":
        if level == "低":
            level = "中"
        reasons.append("部分条款已失效，引用前须核对")

    return level, "；".join(reasons) or "一般性文件"


def make_summary(title, content, eff, exp, domains, tax_types, abolished):
    """生成一句话结构化摘要"""
    parts = []
    if tax_types:
        parts.append("涉及" + "、".join(tax_types[:3]))
    if eff:
        parts.append(f"自{eff}起施行" if eff != "发布之日起施行" else "发布之日起施行")
    if exp:
        parts.append(f"执行至{exp}")
    if abolished:
        parts.append(f"废止{len(abolished)}件旧文")
    if domains:
        parts.append("归属" + domains[0])
    head = clean(content)[:70]
    tail = "；".join(parts)
    return (tail + "。" if tail else "") + (f" 要点：{head}…" if head else "")


# ---------------- 主流程 ----------------

def analyze_one(row):
    title = row["title"] or ""
    content = row["content"] or ""
    doc_num = re.sub(r"\s+", "", row["doc_num"] or "")
    labels = row["labels"] or ""
    aging = row["aging"] or ""

    eff, exp, period, retro = extract_effective(content)
    rels = extract_relations(content, doc_num)
    tax_types = enrich_tax_types(title, content, jload(row["tax_types"], []), labels)
    domains = tag_domains(title, content, labels, tax_types)
    kps = extract_key_points(content)
    obs = extract_obligations(content)
    amts = extract_amounts(content)
    abolished = [(r[0] or r[1]) for r in rels if r[2] in ("abolish", "supersede")]
    cited = [r[0] for r in rels if r[2] == "cite" and r[0]]
    level, reason = assess_risk(title, content, aging, domains,
                                row["doc_year"] or "", row["effect_level"] or "")
    summary = make_summary(title, content, eff, exp, domains, tax_types, abolished)

    return {
        "policy_id": row["id"], "summary": summary, "domains": jdump(domains),
        "tax_types_x": jdump(tax_types), "effective_date": eff, "expire_date": exp,
        "exec_period": period, "is_retroactive": retro, "key_points": jdump(kps),
        "obligations": jdump(obs), "risk_level": level, "risk_reason": reason,
        "abolished_docs": jdump(abolished), "cited_docs": jdump(cited),
        "amount_items": jdump(amts), "rule_at": now(),
    }, rels


ANA_COLS = ["policy_id", "summary", "domains", "tax_types_x", "effective_date", "expire_date",
            "exec_period", "is_retroactive", "key_points", "obligations", "risk_level",
            "risk_reason", "abolished_docs", "cited_docs", "amount_items", "rule_at"]


def ensure_columns(conn):
    """幂等补列：老库升级用"""
    c = conn.cursor()
    adds = [
        ("relations", "confidence", "INTEGER DEFAULT 0"),
        ("relations", "tgt_title", "TEXT DEFAULT ''"),
        ("analysis", "status_final", "TEXT DEFAULT ''"),
        ("analysis", "status_source", "TEXT DEFAULT ''"),
        ("analysis", "status_evidence", "TEXT DEFAULT ''"),
    ]
    for tbl, col, typ in adds:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
        if col not in cols:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}")
    conn.commit()


def run(only_new=True, limit=None):
    init_db()
    conn = get_conn()
    ensure_columns(conn)
    c = conn.cursor()

    if only_new:
        q = """SELECT p.* FROM policies p LEFT JOIN analysis a ON p.id=a.policy_id
               WHERE a.policy_id IS NULL"""
    else:
        q = "SELECT * FROM policies p"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = c.execute(q).fetchall()
    print(f"待分析政策：{len(rows)} 件")

    # ---- 构建挂接索引 ----
    # docmap_full : 完整文号 -> id（唯一，可信）
    # short_map   : 简写文号 -> [id...]（可能撞车，仅当唯一时才用）
    # title_map   : 归一化标题 -> id（唯一时可信，最强锚点）
    docmap_full, short_map, title_map, title_dup = {}, defaultdict(list), {}, set()
    for r in c.execute("SELECT id, doc_num, title FROM policies").fetchall():
        dn = re.sub(r"\s+", "", r["doc_num"] or "")
        if dn:
            docmap_full.setdefault(dn, r["id"])
            m = re.search(r"(\d{4})\s*年第\s*(\d+)\s*号", dn)
            if m:
                short_map[f"{m.group(1)}年第{m.group(2)}号"].append(r["id"])
        nt = norm_title(r["title"] or "")
        if nt:
            if nt in title_map:
                title_dup.add(nt)          # 同名标题，不可作唯一锚点
            else:
                title_map[nt] = r["id"]
    for nt in title_dup:
        title_map.pop(nt, None)

    STRONG = ("abolish", "supersede", "revise")

    def resolve(tgt_doc, tgt_title, rel_type="cite"):
        """
        把废止/修订目标解析为库内 id。

        简写文号（如「2017年第7号」）是最大的误判来源：财政部公告2017年第7号
        与国家税务总局公告2017年第7号是两份完全不同的文件，而本库只收税务口径，
        「库内唯一」并不等于「现实中唯一」。故对废止类强关系，禁止仅凭简写挂接。
        """
        nt = norm_title(tgt_title)
        if nt and nt in title_map:
            return title_map[nt], "title"
        if tgt_doc:
            if tgt_doc in docmap_full:          # 完整文号，含发文机关，安全
                return docmap_full[tgt_doc], "doc"
            ids = short_map.get(tgt_doc, [])
            if ids and nt:                      # 简写 + 标题共同确认
                for i in ids:
                    row = c.execute("SELECT title FROM policies WHERE id=?", (i,)).fetchone()
                    if row and nt in norm_title(row["title"]):
                        return i, "doc_short+title"
            if len(ids) == 1 and rel_type not in STRONG:
                return ids[0], "doc_short"      # 弱关系（引用）容忍简写
            # 强关系且无标题佐证 -> 宁可不挂，避免把无关文件误标为已废止
        return "", ""

    n = 0
    rel_n = 0
    for row in rows:
        try:
            rec, rels = analyze_one(row)
            c.execute(f"""INSERT OR REPLACE INTO analysis ({','.join(ANA_COLS)})
                          VALUES ({','.join(['?']*len(ANA_COLS))})""",
                      [rec[k] for k in ANA_COLS])
            src_doc = re.sub(r"\s+", "", row["doc_num"] or "")
            for tgt, tgt_title, rt, ev, conf in rels:
                tid, how = resolve(tgt, tgt_title, rt)
                if tid == row["id"]:
                    continue                      # 自引用，跳过
                if how == "title":
                    conf = min(99, conf + 8)      # 标题命中，提高置信
                tgt_key = norm_title(tgt_title) or tgt
                try:
                    c.execute("""INSERT OR IGNORE INTO relations
                                 (src_id,src_doc_num,tgt_doc_num,tgt_title,tgt_key,tgt_id,
                                  rel_type,evidence,confidence,created_at)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (row["id"], src_doc, tgt, tgt_title, tgt_key, tid,
                               rt, ev, conf, now()))
                    rel_n += 1
                except Exception:
                    pass
            n += 1
            if n % 400 == 0:
                conn.commit()
                print(f"  已分析 {n}/{len(rows)}")
        except Exception as e:
            print(f"  ! {row['id']} 分析失败: {str(e)[:100]}")
    conn.commit()

    # 回填此前未匹配上的关系目标ID（仅用完整文号，避免简写撞车）
    c.execute("""UPDATE relations SET tgt_id = (
                   SELECT p.id FROM policies p
                   WHERE REPLACE(p.doc_num,' ','') = relations.tgt_doc_num LIMIT 1)
                 WHERE (tgt_id IS NULL OR tgt_id='') AND tgt_doc_num != ''""")
    conn.commit()

    linked = c.execute("SELECT COUNT(*) FROM relations WHERE tgt_id != ''").fetchone()[0]
    total_rel = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    print(f"分析完成：{n} 件 | 关系 {total_rel} 条（成功挂接旧文 {linked} 条）")
    conn.close()


def link_interpretations():
    """把官方解读关联到对应政策"""
    conn = get_conn()
    c = conn.cursor()
    pols = c.execute("SELECT id,title,doc_num FROM policies").fetchall()
    by_title = {clean(p["title"]): p["id"] for p in pols}
    by_doc = {}
    for p in pols:
        dn = re.sub(r"\s+", "", p["doc_num"] or "")
        if dn:
            by_doc.setdefault(dn, p["id"])

    rows = c.execute("SELECT id,title,content FROM interpretations").fetchall()
    hit = 0
    for r in rows:
        pid, dnum = "", ""
        m = BOOK_RE.search(r["title"] or "")
        if m:
            t = clean(m.group(1))
            pid = by_title.get(t, "")
            if not pid:
                for bt, bid in by_title.items():
                    if t and (t in bt or bt in t) and abs(len(t) - len(bt)) < 12:
                        pid = bid
                        break
        if not pid:
            for dn in extract_doc_nums((r["title"] or "") + (r["content"] or "")[:600]):
                if dn in by_doc:
                    pid, dnum = by_doc[dn], dn
                    break
        if pid:
            c.execute("UPDATE interpretations SET ref_policy_id=?, ref_doc_num=? WHERE id=?",
                      (pid, dnum, r["id"]))
            hit += 1
    conn.commit()
    print(f"官方解读挂接：{hit}/{len(rows)} 条已关联到政策原文")
    conn.close()


# ============ 时效推断 ============
# 源站仅对「税务规范性文件/规章/法律/行政法规」标注时效，
# 「工作通知/财税文件」等半数以上没有标注。以下据全库关系图谱反向推断。

OFFICIAL_MAP = {
    "全文有效": "有效", "部分有效": "部分有效", "全文废止": "已废止",
    "全文失效": "已失效", "部分失效": "部分失效", "已修改": "已修改",
    "尚未生效": "尚未生效", "部分废止": "部分废止",
}
# 法律位阶：数字越小位阶越高。下位法不能废止上位法。
LEVEL_RANK = {
    "法律": 1, "行政法规": 2, "国务院文件": 3, "税务部门规章": 4,
    "税务规范性文件": 5, "财税文件": 5, "工作通知": 6, "其他文件": 7,
}
FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


FROM_DATE_RE = re.compile(r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日起")


def _d(s):
    """取日期前10位并归一全角数字，便于字符串比较"""
    return (s or "").translate(FULLWIDTH)[:10]


def future_abolish_date(evidence, today):
    """
    从废止条款证据中找「自YYYY年M月D日起…废止」的将来日期。
    命中说明该文件目前仍有效，只是已被预告废止 —— 对财税人员是重要的提前量。
    """
    ev = (evidence or "").translate(FULLWIDTH)
    best = ""
    for m in FROM_DATE_RE.finditer(ev):
        d = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if d > today and (not best or d < best):
            best = d
    return best


def infer_one(pid, pub_date, expire_date, effective_date, killers, today,
              self_level=""):
    """
    纯推断（不看官方标注），返回 (status, source, evidence)
    killers: [(rel_type, 废止方标题, 废止方文号, confidence, 废止方发布日, 废止方位阶), ...]
    """
    self_rank = LEVEL_RANK.get(self_level or "", 5)
    self_pub = _d(pub_date)

    def valid(k):
        """位阶校验 + 时序校验，滤掉不可能成立的废止关系"""
        _rt, _ttl, _dn, cf, pd, lv, _ev = k
        if cf < 70:
            return False
        # 下位法不能废止上位法（如「国税函」不可能废止《企业所得税法》）
        if LEVEL_RANK.get(lv or "", 5) > self_rank:
            return False
        # 废止方必须发布在被废止方之后
        pd10 = _d(pd)
        if pd10 and self_pub and pd10 < self_pub:
            return False
        return True

    # 1) 被其他文件明文废止/替代
    hard = [k for k in killers if k[0] in ("abolish", "supersede") and valid(k)]
    if hard:
        hard.sort(key=lambda x: -x[3])
        rt, ttl, dn, cf, pd, lv, ev = hard[0]
        word = "废止" if rt == "abolish" else "替代"
        # 废止条款可能约定将来某日才生效（如「自2027年9月1日起…同时废止」）
        fut = future_abolish_date(ev, today)
        if fut:
            return ("将废止", "关联推定",
                    f"《{ttl[:36]}》（{dn}）规定自 {fut} 起{word}本文，届时失效")
        return ("已废止", "关联推定",
                f"被《{ttl[:40]}》（{dn}）明文{word}，该文发布于 {_d(pd)}")

    # 2) 被明文修订
    rev = [k for k in killers if k[0] == "revise" and valid(k)]
    if rev:
        rev.sort(key=lambda x: -x[3])
        rt, ttl, dn, cf, pd, lv, ev = rev[0]
        return ("已修改", "关联推定",
                f"被《{ttl[:40]}》（{dn}）修订，该文发布于 {_d(pd)}")

    # 3) 文件自载执行期限已届满
    #    注意：政策到期≠文件失效。大量优惠政策到期后由后续文件延续，
    #    源站仍标"全文有效"。故此处只作「优惠期已过」提示，不判定失效。
    ed = _d(expire_date)
    if ed and re.match(r"\d{4}-\d{2}-\d{2}", ed) and ed < today:
        return ("执行期已过", "期限提示",
                f"文件载明执行至 {ed}，优惠期已过；是否有后续延续文件请核实")

    # 4) 尚未到生效日
    fd = _d(effective_date)
    if fd and re.match(r"\d{4}-\d{2}-\d{2}", fd) and fd > today:
        return ("尚未生效", "期限推定", f"文件载明自 {fd} 起施行，尚未到生效日")

    # 5) 无任何线索 —— 诚实标注「未见废止记录」而非断言有效。
    #    经回测，发布年代与是否失效无相关性（2006 年文件中有效与废止各半），
    #    据此兜底只会制造噪音，故不再按年代打「存疑」。
    return ("未见废止记录", "无证据", "源站未标注时效，且全库未检索到废止或替代记录")


def infer_status(verbose=True):
    """全库时效推断 + 与官方标注交叉验证准确率"""
    import datetime
    conn = get_conn()
    ensure_columns(conn)
    c = conn.cursor()
    today = datetime.date.today().isoformat()

    # 构建「被废止索引」：tgt_id -> [(rel_type, 废止方标题, 文号, conf, 发布日)]
    killers = defaultdict(list)
    q = """SELECT r.tgt_id, r.rel_type, r.confidence, r.evidence,
                  p.title AS s_title, p.doc_num AS s_doc, p.pub_date AS s_pub,
                  p.effect_level AS s_level
           FROM relations r JOIN policies p ON p.id = r.src_id
           WHERE r.tgt_id != '' AND r.rel_type IN ('abolish','supersede','revise')"""
    for r in c.execute(q):
        killers[r["tgt_id"]].append(
            (r["rel_type"], r["s_title"] or "", r["s_doc"] or "",
             r["confidence"] or 0, r["s_pub"] or "", r["s_level"] or "",
             r["evidence"] or ""))

    rows = c.execute("""SELECT p.id, p.title, p.pub_date, p.aging, p.abolish_date,
                               p.effect_level, a.expire_date, a.effective_date
                        FROM policies p LEFT JOIN analysis a ON a.policy_id = p.id""").fetchall()

    n_off = n_inf = 0
    tp = fp = fn = tn = 0        # 以「判定为已废止」为正类
    fp_samples = []
    dist = defaultdict(int)
    ups = []

    for r in rows:
        pid = r["id"]
        st_i, src_i, ev_i = infer_one(pid, r["pub_date"], r["expire_date"],
                                      r["effective_date"], killers.get(pid, []), today,
                                      r["effect_level"] or "")
        aging = (r["aging"] or "").strip()

        if aging and aging in OFFICIAL_MAP:
            status = OFFICIAL_MAP[aging]
            source = "官方标注"
            ev = f"源站标注：{aging}"
            if r["abolish_date"]:
                ev += f"（废止日期 {_d(r['abolish_date'])}）"
            n_off += 1
            # 回测：只看结论明确的两类
            if aging in ("全文有效", "全文废止"):
                pred_dead = (st_i == "已废止")
                real_dead = (aging == "全文废止")
                if pred_dead and real_dead:
                    tp += 1
                elif pred_dead and not real_dead:
                    fp += 1
                    if len(fp_samples) < 5:
                        fp_samples.append((r["title"][:46], ev_i[:88]))
                elif not pred_dead and real_dead:
                    fn += 1
                else:
                    tn += 1
        else:
            status, source, ev = st_i, src_i, ev_i
            n_inf += 1

        dist[status] += 1
        ups.append((status, source, ev, pid))

    c.executemany("""UPDATE analysis SET status_final=?, status_source=?, status_evidence=?
                     WHERE policy_id=?""", ups)
    conn.commit()

    if verbose:
        print(f"\n时效判定完成：官方标注 {n_off} 件 | 引擎推断 {n_inf} 件")
        print("--- 效力分布 ---")
        for k, v in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"   {k:<12} {v:>5}")
        tot = tp + fp + fn + tn
        if tot:
            prec = tp / (tp + fp) * 100 if (tp + fp) else 0
            rec = tp / (tp + fn) * 100 if (tp + fn) else 0
            print(f"\n--- 废止识别回测（官方已标注的 {tot} 件）---")
            print(f"   正确识别废止 {tp} | 误报 {fp} | 漏报 {fn} | 正确保留 {tn}")
            print(f"   准确率(判废止有多准) {prec:.1f}%   "
                  f"召回率(废止查全率) {rec:.1f}%")
            print(f"   >> 误报是最危险的（把有效文件标成废止），当前误报 {fp} 件")
            if fp_samples:
                print("   误报样本：")
                for t, e in fp_samples:
                    print(f"     · {t}\n       {e}")
    conn.close()
    return dist


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="重新分析全部（默认只分析未分析的）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--link", action="store_true", help="同时挂接官方解读")
    ap.add_argument("--status", action="store_true", help="只跑时效推断")
    a = ap.parse_args()
    if a.status:
        infer_status()
    else:
        run(only_new=not a.all, limit=a.limit)
        if a.link:
            link_interpretations()
        infer_status()

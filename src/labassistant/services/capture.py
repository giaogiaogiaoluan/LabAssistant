"""随手记（模糊记忆）：把一句话按规则识别成结构化记录。

用法：用户在一行里随手写「明天记得交实验报告 9:00-11:00」或
「示例网站 账号 user@example.com 密码 Example123」，本模块负责：

1. `classify(text)` —— 纯规则判定意图，返回 (kind, 置信度, 字段字典)，**不写库**；
2. `capture(db, text)` —— 判定后真正落到对应的业务表，并把原文留在 captures 表。

识别类别（kind）：
    credential 账号密码   → 写入 vault_items（密码 AES-256-GCM 加密）
    lab        实验室打卡 → 写入 attendance_blocks（有时间段）或 manual_hours（只有时长）
    todo       待办事项  → 写入 todos
    website    网址收藏  → 写入 websites（类别按域名猜）
    holiday    节假日    → 写入 holidays
    note       纯笔记    → 只留在 captures

判定优先级 credential > lab > holiday > website > todo > note：
越具体的规则越先判，避免「账号 xxx 密码 yyy」被当成网址。
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from labassistant.db import Database, now_text
from labassistant.services import attendance as att
from labassistant.services import holidays as hol
from labassistant.services import todos as td
from labassistant.services import websites as ws
from labassistant.services.timing import clock_to_min, min_to_clock

KIND_LABEL: dict[str, str] = {
    "credential": "账号密码",
    "lab": "实验室打卡",
    "todo": "待办事项",
    "website": "网址收藏",
    "holiday": "节假日",
    "note": "随手笔记",
}
KIND_ORDER = list(KIND_LABEL)

# ---------------------------------------------------------------- 正则库
_URL_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:[/?#][^\s，。；]*)?", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})[:：点](\d{2})?\s*(?:-|~|—|–|到|至|--)\s*(\d{1,2})[:：点](\d{2})?")
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(小时|h|H|半小时|分钟|min|m)")
_DATE_ABS_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?")
_DATE_MD_RE = re.compile(r"(?<![\d/年.-])(\d{1,2})月(\d{1,2})日?")
_DATE_SLASH_RE = re.compile(r"(?<![\d/年.:-])(\d{1,2})/(\d{1,2})(?![\d/])")
_CN_REL_RE = re.compile(r"(大前天|前天|今天|明天|后天|大后天|今晚|明早|明晚)")
_CN_WEEK_RE = re.compile(r"(下|本|上)?\s*(周|星期|礼拜)([一二三四五六日天1-7])")
_HOLIDAY_RE = re.compile(r"放假|调休|补班|休息日|节假日")
_CRED_RE = re.compile(r"密码|口令|password|passwd|pwd|账号|帐号|用户名|登录|验证码", re.I)
_SECRET_KEY_RE = re.compile(r"(?:密码|口令|password|passwd|pwd)\s*[:：=]?\s*(\S+)", re.I)
_USER_KEY_RE = re.compile(
    r"(?:账号|帐号|账户|用户名|user(?:name)?|邮箱|学号|工号|eid)\s*[:：=]?\s*(\S+)", re.I)
_TODO_RE = re.compile(
    r"待办|记得|提醒|要去|要做|要写|要交|要买|要查|要改|deadline|截止|前完成|todo|task|"
    r"^要|^记得|^别忘|^得去", re.I)
_LAB_RE = re.compile(r"打卡|实验室|在实验室|lab")
_PRI_HI_RE = re.compile(r"紧急|重要|高优先|加急|必须|务必")
_PRI_LO_RE = re.compile(r"有空|不着急|低优先|随便|以后")
_EST_RE = re.compile(r"(?:预计|大约|约|大概)?\s*(\d+(?:\.\d+)?)\s*(小时|h|分钟|min)(?![\w])")

_WEEK_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
             "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}


# ---------------------------------------------------------------- 时间抽取
def _strip_punct(s: str) -> str:
    return s.strip().strip("，,。.；;：:、-—~·")


def find_date(text: str, today: date) -> tuple[str, str]:
    """从一句话里抽日期。返回 (YYYY-MM-DD, 命中的原文片段)；没命中则 (今天, '')。"""
    m = _DATE_ABS_RE.search(text)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d.isoformat(), m.group(0)
        except ValueError:
            pass
    m = _DATE_MD_RE.search(text) or _DATE_SLASH_RE.search(text)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        try:
            d = date(today.year, mm, dd)
        except ValueError:
            return today.isoformat(), ""
        if d < today - timedelta(days=180):   # 「12月3日」在一月多半指明年
            try:
                d = date(today.year + 1, mm, dd)
            except ValueError:
                return today.isoformat(), ""
        return d.isoformat(), m.group(0)
    m = _CN_REL_RE.search(text)
    if m:
        off = {"大前天": -2, "前天": -2, "今天": 0, "明天": 1,
               "后天": 2, "大后天": 3, "今晚": 0, "明早": 1, "明晚": 1}[m.group(1)]
        return (today + timedelta(days=off)).isoformat(), m.group(1)
    m = _CN_WEEK_RE.search(text)
    if m:
        wd = _WEEK_MAP.get(m.group(3))
        if wd is not None:
            cur = today.weekday()
            delta = wd - cur
            if m.group(1) == "下":
                delta += 7
            elif m.group(1) == "上":
                delta -= 7
            elif delta < 0:                   # 「周五」已过 → 下一个周五
                delta += 7
            return (today + timedelta(days=delta)).isoformat(), m.group(0)
    return today.isoformat(), ""


def find_time_range(text: str) -> tuple[int, int, str] | None:
    m = _TIME_RANGE_RE.search(text)
    if not m:
        return None
    try:
        s = clock_to_min(f"{int(m.group(1))}:{m.group(2) or '00'}")
        e = clock_to_min(f"{int(m.group(3))}:{m.group(4) or '00'}")
    except Exception:  # noqa: BLE001 非法时间直接忽略
        return None
    if e <= s:
        e += 24 * 60                        # 跨午夜
    return s, e, m.group(0)


def find_duration(text: str) -> int | None:
    if "半小时" in text:
        base = 30
    else:
        base = 0
    m = _EST_RE.search(text)
    if not m:
        return base or None
    val = float(m.group(1))
    unit = m.group(2).lower()
    minutes = int(round(val * 60)) if unit in ("小时", "h") else int(round(val))
    return minutes or base or None


def _domain_of(url: str) -> str:
    s = re.sub(r"^https?://", "", url or "", flags=re.I)
    return s.split("/")[0].replace("www.", "")


def _guess_category(domain: str) -> str:
    d = (domain or "").lower()
    rules = [
        ("academic", r"arxiv|zotero|sci-hub|cnki|wechat|weixin|github|gitlab|acge|"
                     r"scheduler|overleaf|biorxiv|nature|elsevier|ieee|harvard|edu\.cn"),
        ("school", r"yuketang|chaoxing|icourse|mooc|\.edu\.|\.edu|jwxt|course|btbu|stu\."),
        ("ai", r"openai|chatgpt|qianwen|tongyi|deepseek|kimi|claude|gemini|huggingface|"
               r"lingxi|kuafuai|ai\.|platform\."),
        ("tools", r"convert|tool|figma|notion|obsidian|123|pan\.|drive|translate|json"),
        ("life", r"taobao|jd\.com|pinduoduo|meituan|dianping|bilibili|zhihu|weibo|"
                 r"xiaohongshu|amap|ctrip"),
    ]
    for cat, pat in rules:
        if re.search(pat, d):
            return cat
    return "other"


# ---------------------------------------------------------------- 分类
def classify(text: str, today: date | None = None) -> dict:
    """纯规则识别。返回 {kind, confidence, fields, why}，不写任何数据。"""
    today = today or date.today()
    raw = (text or "").strip()
    fields: dict = {}
    if not raw:
        return {"kind": "note", "confidence": 0.0, "fields": {}, "why": "空"}

    date_str, date_hit = find_date(raw, today)
    fields["date"] = date_str
    if date_hit:
        fields["date_token"] = date_hit
    rng = find_time_range(raw)
    dur = find_duration(raw)
    urls = [u for u in _URL_RE.findall(raw) if "." in u]
    has_cred_kw = bool(_CRED_RE.search(raw))
    has_todo_kw = bool(_TODO_RE.search(raw))
    has_lab_kw = bool(_LAB_RE.search(raw))
    has_holiday_kw = bool(_HOLIDAY_RE.search(raw))

    # 1) 账号密码：必须有“密码/口令”类关键词，且能抓到账号或密文
    if has_cred_kw and ("密码" in raw or "password" in raw.lower() or "pwd" in raw.lower()
                        or "口令" in raw):
        secret = _SECRET_KEY_RE.search(raw)
        user = _USER_KEY_RE.search(raw)
        email = _EMAIL_RE.search(raw)
        username = (user.group(1) if user else (email.group(0) if email else ""))
        # 找“站点域名”时先把邮箱整段挖掉，否则 user@example.com 会把标题带成 example.com
        no_email = _EMAIL_RE.sub(" ", raw)
        dom = None
        for u in urls:
            d = _domain_of(u)
            if "." in d:
                dom = d
                break
        if dom is None:
            m = re.search(r"([\w-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)", no_email, re.I)
            dom = m.group(1) if m else None
        fields["username"] = _strip_punct(username)
        fields["secret"] = _strip_punct(secret.group(1)) if secret else ""
        # 域名只是邮箱后缀时不再单列，免得界面出现 “a@example.com · example.com” 这种重复
        email_dom = (email.group(0).split("@")[-1] if email else "")
        fields["url"] = "" if (dom and email_dom and dom.endswith(email_dom)) else (dom or "")
        # 标题：先看“XX 的账号 / XX 登录”这类前缀（华为云、网盘…），再看域名
        title = ""
        m = re.match(r"^\s*(?:记一下|记录|记个|备忘|存一下|存个)?\s*"
                     r"([^\s，,。；;]{1,14}?)(?:的)?\s*(?:账号|帐号|账户|登录|密码)", raw)
        if m:
            title = _strip_punct(m.group(1))
        title = title or fields["url"] or ""
        if not title:
            m = re.match(r"^\s*(?:记一下|记录|记个|备忘)?\s*([：:，,\s]*[^\s，,。；;]{1,16}?)\s*"
                         r"(?:的)?(?:账号|帐号|账户|登录)", raw)
            title = _strip_punct(m.group(1)) if m else ""
        if not title:
            m = re.search(r"\b([a-zA-Z][a-zA-Z0-9_-]{2,})\b", raw)
            skip = {"user", "username", "pwd", "password", "http", "https", "com", "www",
                    "site", "account", "login", "the", "and"}
            for cand in re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}", raw):
                if cand.lower() not in skip and not cand.lower().startswith(("pass", "user")):
                    title = cand
                    break
        title = title or "未命名账号"
        fields["title"] = title or "未命名账号"
        conf = 0.9 if fields["secret"] else 0.6
        return {"kind": "credential", "confidence": conf, "fields": fields,
                "why": "含“密码/账号”关键词"}

    # 2) 实验室打卡：有打卡关键词，或“时间段 + 实验室”
    if has_lab_kw and (rng or dur):
        if rng:
            fields["start_min"], fields["end_min"], fields["time_token"] = rng
            fields["minutes"] = rng[1] - rng[0]
        if dur:
            fields["minutes"] = dur
        fields["note"] = _strip_punct(_clean_phrase(raw, date_hit, rng[2] if rng else "",
                                                    "")) or "实验室打卡"
        return {"kind": "lab", "confidence": 0.9, "fields": fields,
                "why": "打卡关键词 + 时间"}

    # 3) 节假日
    if has_holiday_kw and (date_hit or _DATE_MD_RE.search(raw) or _DATE_ABS_RE.search(raw)):
        body = raw.replace(date_hit, " ") if date_hit else raw
        m = re.search(r"([^\s，,。；;]{1,10}?)(?:放假|调休|补班|休息)", body)
        name = _strip_punct(m.group(1)) if m else ""
        name = re.sub(r"(周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天])",
                      "", name).strip()
        fields["name"] = name or _strip_punct(date_hit) or "调休"
        return {"kind": "holiday", "confidence": 0.85, "fields": fields,
                "why": "放假/调休 + 日期"}

    # 4) 网址收藏：有 URL，且没有待办/打卡意图
    if urls and not has_todo_kw and not has_lab_kw:
        u = urls[0]
        if not u.lower().startswith("http"):
            u = "https://" + u
        fields["url"] = u
        fields["domain"] = _domain_of(u)
        fields["category"] = _guess_category(fields["domain"])
        name = re.sub(r"^\s*(?:记个|收藏|存一下|网址|网站)\s*[:：]?\s*", "", raw).strip()
        name = re.sub(r"https?://", "", name, flags=re.I)
        name = _strip_punct(re.sub(re.escape(_domain_of(u)) + r"\S*", "", name)) \
            or fields["domain"]
        fields["name"] = name or fields["domain"]
        return {"kind": "website", "confidence": 0.85, "fields": fields,
                "why": "含链接"}

    # 5) 待办
    if has_todo_kw or (rng and not has_lab_kw) or (date_hit and len(raw) > 4):
        fields["priority"] = ("高" if _PRI_HI_RE.search(raw)
                              else ("低" if _PRI_LO_RE.search(raw) else "中"))
        if dur:
            fields["est_minutes"] = dur
        title = _clean_phrase(raw, date_hit, rng[2] if rng else "",
                              _first(_EST_RE, raw))
        fields["title"] = title or _strip_punct(raw) or "待办"
        if rng:
            fields["start_min"], fields["end_min"] = rng[0], rng[1]
            fields["time_token"] = rng[2]
        conf = 0.9 if has_todo_kw else 0.55
        return {"kind": "todo", "confidence": conf, "fields": fields,
                "why": "待办关键词" if has_todo_kw else "含日期/时间"}

    # 6) 兜底：纯笔记
    fields["text"] = raw
    return {"kind": "note", "confidence": 0.4, "fields": fields, "why": "无明确规则命中"}


def _first(rx, text: str) -> str:
    m = rx.search(text)
    return m.group(0) if m else ""


def _clean_phrase(raw: str, *drop: str) -> str:
    """把日期/时间/时长这些“已被结构化吸收”的片段从标题里去掉。"""
    s = raw
    for d in drop:
        if d:
            s = s.replace(d, " ")
    s = re.sub(r"(?:预计|大约|约|大概)?\s*\d+(?:\.\d+)?\s*(?:小时|分钟|h|min|半小时)", " ", s,
               flags=re.I)
    s = re.sub(r"(?:记得|提醒我|提醒|别忘|待办|todo|任务)\s*[:：,，]?", " ", s, flags=re.I)
    s = re.sub(r"(?:紧急|重要|加急|必须|务必|有空|不着急|低优先|高优先|优先级?[高中低])", " ", s)
    s = re.sub(r"[，,、;；\s]+", " ", s).strip(" ：:。-")
    return s


# ---------------------------------------------------------------- 落库
def capture(db: Database, text: str, today: date | None = None) -> dict:
    """识别 + 写入对应业务表 + 在 captures 表留原文。返回 captures 行（含 target 信息）。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("内容不能为空")
    verdict = classify(raw, today)
    kind = verdict["kind"]
    f = verdict["fields"]
    target_table, target_id = "", None

    if kind == "credential":
        from labassistant.services import vault
        target_id = vault.add_item(
            db, title=f.get("title") or "未命名账号",
            username=f.get("username", ""), secret=f.get("secret", ""),
            url=f.get("url", ""), note="来自随手记")
        target_table = "vault_items"
    elif kind == "lab":
        ds = f["date"]
        if f.get("start_min") is not None and f.get("end_min") is not None:
            target_id = att.add_block(db, ds, f["start_min"], f["end_min"],
                                      f.get("note", "实验室打卡"))
            target_table = "attendance_blocks"
        else:
            target_id = att.add_manual(db, ds, int(f.get("minutes") or 0),
                                       f.get("note", "实验室打卡"))
            target_table = "manual_hours"
    elif kind == "todo":
        target_id = td.add_todo(db, f["date"], f.get("title", "待办"),
                                est_minutes=f.get("est_minutes"),
                                priority=f.get("priority", "中"),
                                note="来自随手记")
        target_table = "todos"
    elif kind == "website":
        target_id = ws.add_website(db, f["name"], f["url"], f.get("category", "other"),
                                   note="来自随手记")
        target_table = "websites"
    elif kind == "holiday":
        hol.add_holiday(db, f["date"], f.get("name", "调休"))
        row = db.query_one("SELECT id FROM holidays WHERE date=?", (f["date"],))
        target_id = row["id"] if row else None
        target_table = "holidays"

    cid = db.execute(
        "INSERT INTO captures(raw, kind, on_date, target_table, target_id, parsed, created) "
        "VALUES(?,?,?,?,?,?,?)",
        (raw, kind, f.get("date", ""), target_table, target_id,
         json.dumps(verdict, ensure_ascii=False), now_text()))
    return get_capture(db, cid) or {"id": cid, "raw": raw, "kind": kind,
                                    "target_table": target_table, "target_id": target_id,
                                    "parsed": verdict}


def get_capture(db: Database, capture_id: int) -> dict | None:
    r = db.query_one("SELECT * FROM captures WHERE id=?", (capture_id,))
    if not r:
        return None
    d = dict(r)
    try:
        d["parsed"] = json.loads(d.get("parsed") or "{}")
    except json.JSONDecodeError:
        d["parsed"] = {}
    return d


def list_captures(db: Database, limit: int = 200) -> list[dict]:
    rows = db.query(
        "SELECT * FROM captures ORDER BY id DESC LIMIT ?", (int(limit),))
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["parsed"] = json.loads(d.get("parsed") or "{}")
        except json.JSONDecodeError:
            d["parsed"] = {}
        out.append(d)
    return out


def undo_capture(db: Database, capture_id: int) -> bool:
    """撤销一条随手记：删掉它自动生成的那条记录，并删除原文留痕。"""
    row = get_capture(db, capture_id)
    if not row:
        return False
    table, tid = row.get("target_table"), row.get("target_id")
    if tid:
        try:
            if table == "todos":
                td.delete_todo(db, tid)
            elif table == "attendance_blocks":
                att.delete_block(db, tid)
            elif table == "manual_hours":
                att.delete_manual(db, tid)
            elif table == "websites":
                ws.delete_website(db, tid)
            elif table == "holidays":
                hol.delete_holiday(db, holiday_id=tid)
            elif table == "vault_items":
                from labassistant.services import vault
                vault.delete_item(db, tid)
        except Exception:  # noqa: BLE001 目标已被手动删除时忽略
            pass
    db.execute("DELETE FROM captures WHERE id=?", (capture_id,))
    return True


def describe(verdict: dict) -> str:
    """把识别结果压成一行人类可读的说明（页面实时预览用）。"""
    kind = verdict.get("kind", "note")
    f = verdict.get("fields", {}) or {}
    label = KIND_LABEL.get(kind, kind)
    if kind == "credential":
        bits = [f.get("title", "")]
        if f.get("username"):
            bits.append(f"账号 {f['username']}")
        bits.append("密码已加密保存" if f.get("secret") else "缺密码")
        return f"{label} · " + " · ".join(b for b in bits if b)
    if kind == "lab":
        if f.get("start_min") is not None:
            return (f"{label} · {f['date']} "
                    f"{min_to_clock(f['start_min'])}–{min_to_clock(f['end_min'])}")
        return f"{label} · {f.get('date','')} {round((f.get('minutes') or 0) / 60, 1)}h"
    if kind == "todo":
        extra = f" · 预计 {round((f['est_minutes']) / 60, 1)}h" if f.get("est_minutes") else ""
        return f"{label} · {f.get('date','')} [{f.get('priority','中')}]{extra} · {f.get('title','')}"
    if kind == "website":
        return f"{label} · {f.get('name','')} → {f.get('domain','')}"
    if kind == "holiday":
        return f"{label} · {f.get('date','')} {f.get('name','')}"
    return f"{label} · 仅留原文"

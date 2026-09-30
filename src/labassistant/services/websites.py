"""网站收藏 CRUD —— 类别枚举、URL 规范化、软删除、可同步。

类别稳定枚举：
    academic 学术 / school 学校 / ai AI / tools 工具 / life 生活 / other 其他
"""

from __future__ import annotations

import re

from labassistant.db import Database, now_text
from labassistant.services import meta

CATEGORY_CN: dict[str, str] = {
    "academic": "学术",
    "school": "学校",
    "ai": "AI",
    "tools": "工具",
    "life": "生活",
    "other": "其他",
}
CATEGORIES = list(CATEGORY_CN)

_ALIVE = "deleted_at IS NULL"

_URL_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})(:\d+)?(/.*)?$")


def normalize_url(raw: str) -> str:
    """基础 URL 规范化：
    - 去掉首尾空白；
    - 无协议时自动补 https://（支持 http:// / https://）；
    - 其它明显非法输入原样返回（由调用方校验）。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        s = "https://" + s
    return s


def is_valid_url(raw: str) -> bool:
    s = (raw or "").strip()
    low = s.lower()
    if low.startswith("http://"):
        host = s[7:]
    elif low.startswith("https://"):
        host = s[8:]
    else:
        host = s
    if not host:
        return False
    return bool(_URL_RE.match(host)) and "." in host.split("/")[0]


def list_websites(db: Database, keyword: str = "", category: str = "") -> list[dict]:
    conds: list[str] = [_ALIVE]
    params: list = []
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        conds.append("(name LIKE ? OR url LIKE ? OR note LIKE ?)")
        params += [kw, kw, kw]
    if category and category != "all":
        conds.append("category=?")
        params.append(category)
    sql = ("SELECT * FROM websites WHERE " + " AND ".join(conds) +
           " ORDER BY COALESCE(deleted_at,''), id")
    return [dict(r) for r in db.query(sql, tuple(params))]


def get_website(db: Database, website_id: int) -> dict | None:
    r = db.query_one(f"SELECT * FROM websites WHERE id=? AND {_ALIVE}", (website_id,))
    return dict(r) if r else None


def add_website(db: Database, name: str, url: str, category: str = "other",
                note: str = "") -> int:
    m = meta.new_row_meta(db)
    cat = category if category in CATEGORY_CN else "other"
    return db.execute(
        "INSERT INTO websites(name, url, category, note, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,?,NULL,?,?)",
        (name.strip(), url.strip(), cat, note.strip(), now_text(),
         m["sync_uuid"], m["created_at"], m["updated_at"], m["device_id"], m["sync_dirty"]),
    )


def update_website(db: Database, website_id: int, name: str, url: str,
                   category: str = "other", note: str = "") -> None:
    t = meta.touch_meta(db)
    cat = category if category in CATEGORY_CN else "other"
    db.execute(
        "UPDATE websites SET name=?, url=?, category=?, note=?, updated_at=?, device_id=?, "
        "sync_dirty=1, deleted_at=NULL WHERE id=? AND deleted_at IS NULL",
        (name.strip(), url.strip(), cat, note.strip(), t["updated_at"], t["device_id"], website_id),
    )


def delete_website(db: Database, website_id: int) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE websites SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE id=? AND deleted_at IS NULL",
        (t["updated_at"], t["updated_at"], t["device_id"], website_id),
    )

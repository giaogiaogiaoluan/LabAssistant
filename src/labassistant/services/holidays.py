"""节假日 CRUD —— 带同步元数据与软删除。"""

from __future__ import annotations

from datetime import date

from labassistant.db import Database, now_text
from labassistant.services import meta

_ALIVE = "deleted_at IS NULL"
MAKEUP_PREFIX = "[补班] "
LEAVE_PREFIX = "[请假] "
_ORIGINAL_SEP = "\x1f"


def rule_type(name: str) -> str:
    if name.startswith(MAKEUP_PREFIX):
        return "makeup"
    if name.startswith(LEAVE_PREFIX):
        return "leave"
    return "holiday"


def rule_label(name: str) -> str:
    kind = rule_type(name)
    if kind == "holiday":
        return name
    visible = name[len(MAKEUP_PREFIX if kind == "makeup" else LEAVE_PREFIX):]
    return visible.split(_ORIGINAL_SEP, 1)[0]


def add_special_day(db: Database, ds: str, kind: str, note: str = "") -> bool:
    """用现有假期实体保存单日例外，以保持旧同步协议兼容。"""
    if kind not in ("makeup", "leave"):
        raise ValueError("特殊日期只能是补班或请假")
    prefix = MAKEUP_PREFIX if kind == "makeup" else LEAVE_PREFIX
    previous = get_holiday(db, ds)
    previous_name = (previous or {}).get("name", "")
    had_original = bool(previous)
    if rule_type(previous_name) != "holiday":
        had_original = _ORIGINAL_SEP in previous_name
        previous_name = previous_name.split(_ORIGINAL_SEP, 1)[1] if had_original else ""
    original = _ORIGINAL_SEP + previous_name if had_original else ""
    return add_holiday(db, ds, prefix + note.strip().replace(_ORIGINAL_SEP, " ") + original)


def remove_rule(db: Database, ds: str) -> None:
    """撤销特殊日期时恢复被它覆盖的原假期。"""
    row = get_holiday(db, ds)
    if not row:
        return
    name = row["name"]
    if rule_type(name) != "holiday" and _ORIGINAL_SEP in name:
        add_holiday(db, ds, name.split(_ORIGINAL_SEP, 1)[1])
    else:
        delete_holiday(db, holiday_id=row["id"])


def _dates_for_rule(db: Database, *, makeup: bool) -> set[date]:
    rows = db.query(f"SELECT date, name FROM holidays WHERE {_ALIVE}")
    out: set[date] = set()
    for r in rows:
        if (rule_type(r["name"]) == "makeup") != makeup:
            continue
        try:
            out.add(date.fromisoformat(r["date"]))
        except ValueError:
            continue
    return out


def holiday_set(db: Database) -> set[date]:
    return _dates_for_rule(db, makeup=False)


def makeup_set(db: Database) -> set[date]:
    return _dates_for_rule(db, makeup=True)


def list_holidays(db: Database) -> list[dict]:
    rows = db.query(f"SELECT * FROM holidays WHERE {_ALIVE} ORDER BY date DESC")
    return [dict(r) for r in rows]


def get_holiday(db: Database, ds: str) -> dict | None:
    r = db.query_one(f"SELECT * FROM holidays WHERE date=? AND {_ALIVE}", (ds,))
    return dict(r) if r else None


def add_holiday(db: Database, ds: str, name: str = "", is_sample: int = 0) -> bool:
    """插入单条；同一天已存在（含软删除行）则复活/更新。返回是否新增。"""
    m = meta.new_row_meta(db, sample=bool(is_sample))
    cur = db.conn.execute(
        "INSERT INTO holidays(date, name, is_sample, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,NULL,?,?) "
        "ON CONFLICT(date) DO UPDATE SET name=excluded.name, is_sample=excluded.is_sample, "
        "sync_uuid=CASE WHEN holidays.deleted_at IS NOT NULL THEN excluded.sync_uuid "
        "                 ELSE holidays.sync_uuid END, "
        "created_at=CASE WHEN holidays.deleted_at IS NOT NULL THEN excluded.created_at "
        "                 ELSE holidays.created_at END, "
        "updated_at=excluded.updated_at, deleted_at=NULL, device_id=excluded.device_id, "
        "sync_dirty=excluded.sync_dirty",
        (ds, name.strip(), int(is_sample), now_text(),
         m["sync_uuid"], m["created_at"], m["updated_at"], m["device_id"], m["sync_dirty"]),
    )
    db.commit()
    return cur.rowcount > 0


def add_holidays_batch(db: Database, items: list[tuple[str, str]]) -> int:
    """批量添加 [(date_str, name)]，返回新增数量。"""
    added = 0
    for ds, name in items:
        if add_holiday(db, ds, name):
            added += 1
    return added


def delete_holiday(db: Database, holiday_id: int | None = None,
                   date_str: str | None = None) -> None:
    """软删除（按 id 或日期）。"""
    t = meta.touch_meta(db)
    if holiday_id is not None:
        db.execute(
            "UPDATE holidays SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
            "WHERE id=? AND deleted_at IS NULL",
            (t["updated_at"], t["updated_at"], t["device_id"], holiday_id),
        )
    elif date_str is not None:
        db.execute(
            "UPDATE holidays SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
            "WHERE date=? AND deleted_at IS NULL",
            (t["updated_at"], t["updated_at"], t["device_id"], date_str),
        )

"""节假日 CRUD —— 带同步元数据与软删除。"""

from __future__ import annotations

from datetime import date

from labassistant.db import Database, now_text
from labassistant.services import meta

_ALIVE = "deleted_at IS NULL"


def holiday_set(db: Database) -> set[date]:
    rows = db.query(f"SELECT date FROM holidays WHERE {_ALIVE}")
    out: set[date] = set()
    for r in rows:
        try:
            out.add(date.fromisoformat(r["date"]))
        except ValueError:
            continue
    return out


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

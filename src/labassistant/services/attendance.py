"""实验室打卡记录（时间段 + 手动时长）CRUD —— 带同步元数据与软删除。

所有查询默认隐藏已软删除( deleted_at IS NOT NULL )的记录。
"""

from __future__ import annotations

from labassistant.db import Database, now_text
from labassistant.services import meta
from labassistant.services.timing import raw_duration

_ALIVE = "deleted_at IS NULL"


def row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def list_blocks(db: Database, date_str: str) -> list[dict]:
    rows = db.query(
        f"SELECT * FROM attendance_blocks WHERE date=? AND {_ALIVE} ORDER BY start_min, id",
        (date_str,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["duration_min"] = raw_duration(d["start_min"], d["end_min"])
        out.append(d)
    return out


def list_manual(db: Database, date_str: str) -> list[dict]:
    rows = db.query(
        f"SELECT * FROM manual_hours WHERE date=? AND {_ALIVE} ORDER BY id", (date_str,)
    )
    return [dict(r) for r in rows]


def add_block(db: Database, date_str: str, start_min: int, end_min: int,
              note: str = "", is_sample: int = 0) -> int:
    m = meta.new_row_meta(db, sample=bool(is_sample))
    return db.execute(
        "INSERT INTO attendance_blocks(date, start_min, end_min, note, is_sample, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (date_str, int(start_min), int(end_min), note, int(is_sample), now_text(),
         m["sync_uuid"], m["created_at"], m["updated_at"], None, m["device_id"], m["sync_dirty"]),
    )


def update_block(db: Database, block_id: int, start_min: int, end_min: int, note: str) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE attendance_blocks SET start_min=?, end_min=?, note=?, updated_at=?, "
        "device_id=?, sync_dirty=1, deleted_at=NULL WHERE id=? AND deleted_at IS NULL",
        (int(start_min), int(end_min), note, t["updated_at"], t["device_id"], block_id),
    )


def delete_block(db: Database, block_id: int) -> None:
    """软删除：保留记录以便把删除状态同步到其他设备。"""
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE attendance_blocks SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE id=? AND deleted_at IS NULL",
        (t["updated_at"], t["updated_at"], t["device_id"], block_id),
    )


def add_manual(db: Database, date_str: str, minutes: int, note: str = "",
               is_sample: int = 0) -> int:
    m = meta.new_row_meta(db, sample=bool(is_sample))
    return db.execute(
        "INSERT INTO manual_hours(date, minutes, note, is_sample, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (date_str, int(minutes), note, int(is_sample), now_text(),
         m["sync_uuid"], m["created_at"], m["updated_at"], None, m["device_id"], m["sync_dirty"]),
    )


def update_manual(db: Database, manual_id: int, minutes: int, note: str) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE manual_hours SET minutes=?, note=?, updated_at=?, device_id=?, "
        "sync_dirty=1, deleted_at=NULL WHERE id=? AND deleted_at IS NULL",
        (int(minutes), note, t["updated_at"], t["device_id"], manual_id),
    )


def delete_manual(db: Database, manual_id: int) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE manual_hours SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE id=? AND deleted_at IS NULL",
        (t["updated_at"], t["updated_at"], t["device_id"], manual_id),
    )

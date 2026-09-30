"""业务表写入同步元数据的小工具（不参与 UI）。"""

from __future__ import annotations

from labassistant.db import Database
from labassistant_shared import timeutil


def device_of(db: Database) -> str:
    return db.get_setting("device_id") or ""


def new_row_meta(db: Database, *, sample: bool = False) -> dict:
    """INSERT 时使用的同步元字段。示例数据不进同步队列（dirty=0）。"""
    now = timeutil.utcnow_iso()
    return {
        "sync_uuid": timeutil.new_uuid(),
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "device_id": device_of(db),
        "sync_dirty": 0 if sample else 1,
    }


def touch_meta(db: Database) -> dict:
    """UPDATE / 软删除时只更新必要字段。"""
    return {
        "updated_at": timeutil.utcnow_iso(),
        "device_id": device_of(db),
        "sync_dirty": 1,
    }

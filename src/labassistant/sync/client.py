"""客户端同步引擎（纯逻辑，无网络、无 UI，便于测试）。

设计要点（对应任务书）：
- Offline First：本地 SQLite 始终可写；改动先落本地并置 sync_dirty=1。
- push：上传本地 dirty 记录（含软删除 tombstone）。
- pull：按 last_server_revision 增量拉取服务端变更并应用。
- Last Write Wins：比较 updated_at（UTC ISO 字典序）。
- 幂等：以 sync_uuid 定位记录；重复应用同一版本是 no-op。
- 空本地库 pull 只应用服务端数据，绝不反向产生“删除”。

状态表 sync_state：last_server_revision / last_sync_at / last_push_watermark / last_error。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from labassistant.db import Database
from labassistant_shared import timeutil
from labassistant_shared.protocol import (
    ENTITIES,
    META_FIELDS,
    SYNCED_SETTINGS,
    fields_of,
    has_sample_col,
)

ACTION_UPSERT = "upsert"
ACTION_DELETE = "delete"


# ---------------------------------------------------------------------------
@dataclass
class Change:
    entity: str
    sync_uuid: str
    action: str            # upsert | delete
    updated_at: str
    deleted_at: str | None = None
    device_id: str = ""
    data: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "entity": self.entity,
            "sync_uuid": self.sync_uuid,
            "action": self.action,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
            "device_id": self.device_id,
            "data": self.data,
        }


# ---------------------------------------------------------------------------
def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def dirty_count(db: Database) -> int:
    """等待上传的记录数（含软删除 tombstone、不含示例数据）。"""
    total = 0
    conn = db.conn
    for entity, spec in ENTITIES.items():
        table = spec["table"]
        cols = _cols(conn, table)
        if "sync_dirty" not in cols:
            continue
        where = "sync_dirty=1"
        if has_sample_col(entity) and "is_sample" in cols:
            where += " AND COALESCE(is_sample,0)=0"
        if "sync_uuid" not in cols:
            continue
        r = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()
        total += r[0]
    # 业务设置
    if "sync_dirty" in _cols(conn, "settings"):
        r = conn.execute(
            "SELECT COUNT(*) FROM settings WHERE sync_dirty=1 AND key IN (%s)"
            % ",".join("?" * len(SYNCED_SETTINGS)),
            SYNCED_SETTINGS,
        ).fetchone()
        total += r[0]
    return total


def collect_dirty(db: Database) -> list[Change]:
    """收集所有本地待上传改动。"""
    out: list[Change] = []
    conn = db.conn
    for entity, spec in ENTITIES.items():
        table = spec["table"]
        cols = _cols(conn, table)
        if "sync_uuid" not in cols or "sync_dirty" not in cols:
            continue
        fnames = fields_of(entity)
        select = ", ".join(["sync_uuid", "updated_at", "deleted_at", "device_id"] + list(fnames))
        where = "sync_dirty=1"
        if has_sample_col(entity) and "is_sample" in cols:
            where += " AND COALESCE(is_sample,0)=0"
        rows = conn.execute(
            f"SELECT {select} FROM {table} WHERE {where} ORDER BY updated_at"
        ).fetchall()
        for row in rows:
            row = dict(zip(
                ["sync_uuid", "updated_at", "deleted_at", "device_id"] + list(fnames), row))
            data = {k: row[k] for k in fnames}
            action = ACTION_DELETE if row.get("deleted_at") else ACTION_UPSERT
            out.append(Change(
                entity=entity,
                sync_uuid=row["sync_uuid"],
                action=action,
                updated_at=row["updated_at"] or "",
                deleted_at=row.get("deleted_at"),
                device_id=row.get("device_id") or "",
                data=data,
            ))
    # 业务设置
    if "sync_dirty" in _cols(conn, "settings"):
        rows = conn.execute(
            "SELECT key, value, updated_at, deleted_at, device_id FROM settings "
            "WHERE sync_dirty=1 AND key IN (%s)" % ",".join("?" * len(SYNCED_SETTINGS)),
            SYNCED_SETTINGS,
        ).fetchall()
        for key, value, updated_at, deleted_at, device_id in rows:
            out.append(Change(
                entity="setting",
                sync_uuid=key,
                action=ACTION_DELETE if deleted_at else ACTION_UPSERT,
                updated_at=updated_at or "",
                deleted_at=deleted_at,
                device_id=device_id or "",
                data={"key": key, "value": value},
            ))
    return out


def clear_dirty(db: Database, changes: list[Change]) -> None:
    """服务器确认后清除本地 dirty 标记。"""
    conn = db.conn
    for ch in changes:
        if ch.entity == "setting":
            conn.execute(
                "UPDATE settings SET sync_dirty=0 WHERE key=? AND key IN (%s)"
                % ",".join("?" * len(SYNCED_SETTINGS)),
                (ch.sync_uuid, *SYNCED_SETTINGS),
            )
            continue
        spec = ENTITIES[ch.entity]
        table = spec["table"]
        conn.execute(f"UPDATE {table} SET sync_dirty=0 WHERE sync_uuid=?", (ch.sync_uuid,))
    conn.commit()


# ---------------------------------------------------------------------------
def _entity_fields(entity: str) -> tuple:
    return fields_of(entity)


def _insert_remote(db: Database, ch: Change) -> bool:
    """把服务端下发的记录插入本地（不存在时）。"""
    conn = db.conn
    spec = ENTITIES[ch.entity]
    table = spec["table"]
    data = dict(ch.data)
    # 引用映射：course_exception 的 course_uuid -> 本地 course_id
    if ch.entity == "course_exception":
        cuuid = data.get("course_uuid")
        row = conn.execute(
            "SELECT id FROM courses WHERE sync_uuid=?", (cuuid,)).fetchone() if cuuid else None
        if row is None:
            return False  # 关联课程还没同步到，稍后整批重试
        data["course_id"] = row[0]
    extra_cols = []
    extra_vals = []
    if has_sample_col(ch.entity):
        if "is_sample" in _cols(conn, table):
            extra_cols.append("is_sample")
            extra_vals.append(0)
    if "created" in _cols(conn, table):
        extra_cols.append("created")
        extra_vals.append("")
    allowed = set(fields_of(ch.entity)) | {"course_id"}
    colnames: list[str] = []
    vals: list = []
    for f in fields_of(ch.entity):
        if f in data and f in allowed:
            colnames.append(f)
            vals.append(data[f])
    if ch.entity == "course_exception" and "course_id" in data:
        colnames.append("course_id")
        vals.append(data["course_id"])
    colnames += extra_cols
    vals += extra_vals
    # 同步字段
    meta_cols = ["sync_uuid", "created_at", "updated_at", "deleted_at", "device_id", "sync_dirty"]
    colnames += meta_cols
    vals += [
        ch.sync_uuid,
        data.get("created_at") or ch.updated_at,
        ch.updated_at,
        ch.deleted_at,
        ch.device_id,
        0,
    ]
    conn.execute(f"INSERT INTO {table}({','.join(colnames)}) VALUES({','.join('?' * len(vals))})", vals)
    return True


def apply_pull(db: Database, changes: list[Change]) -> tuple[int, int]:
    """把服务端变更应用到本地（Last Write Wins）。

    返回 (applied, skipped)，且只有在整批全部应用成功时才更新 revision（由调用方处理）。
    本函数对每个变更分别提交；重复应用同一版本是幂等 no-op。
    """
    conn = db.conn
    applied = skipped = 0
    for ch in changes:
        if ch.entity == "setting":
            applied += _apply_setting(db, ch)
            skipped += 0
            continue
        spec = ENTITIES[ch.entity]
        table = spec["table"]
        row = conn.execute("SELECT id, updated_at, sync_dirty, deleted_at FROM %s "
                           "WHERE sync_uuid=?" % table, (ch.sync_uuid,)).fetchone()
        if row is None:
            if ch.action == ACTION_DELETE:
                skipped += 1  # 本地没有这条，删除无需处理
                continue
            ok = _insert_remote(db, ch)
            if not ok:
                # 依赖未就绪（课程例外引用课程未同步）：跳过本条，等整批重试
                conn.rollback()
                return applied, -1
            applied += 1
            continue
        # 已存在：LWW
        if row[1] and row[1] >= ch.updated_at:
            # 本地不旧于远端（含本地更新挂起时），保持不变；若本地是 pending 且更新，则保留等待推送
            skipped += 1
            continue
        if ch.action == ACTION_DELETE:
            conn.execute("UPDATE %s SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=0 "
                         "WHERE sync_uuid=?" % table,
                         (ch.updated_at, ch.updated_at, ch.device_id, ch.sync_uuid))
            applied += 1
            continue
        # upsert 覆盖
        data = dict(ch.data)
        if ch.entity == "course_exception":
            cuuid = data.get("course_uuid")
            cid = None
            if cuuid:
                r = conn.execute("SELECT id FROM courses WHERE sync_uuid=?", (cuuid,)).fetchone()
                if r is not None:
                    cid = r[0]
            if cid is None:
                conn.rollback()
                return applied, -1
            data["course_id"] = cid
        set_parts = []
        vals = []
        for f in _entity_fields(ch.entity):
            if f == "course_uuid":
                continue
            if f in data:
                set_parts.append(f"{f}=?")
                vals.append(data[f])
        if ch.entity == "course_exception" and "course_id" in data:
            set_parts.append("course_id=?")
            vals.append(data["course_id"])
        set_parts += ["updated_at=?", "device_id=?", "deleted_at=?", "sync_dirty=0"]
        vals += [ch.updated_at, ch.device_id, None, ch.sync_uuid]
        conn.execute(f"UPDATE {table} SET {','.join(set_parts)} WHERE sync_uuid=?", vals)
        applied += 1
    conn.commit()
    return applied, skipped


def _apply_setting(db: Database, ch: Change) -> int:
    conn = db.conn
    key = ch.data.get("key") or ch.sync_uuid
    if key not in SYNCED_SETTINGS:
        return 0
    if ch.action == ACTION_DELETE:
        conn.execute(
            "UPDATE settings SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=0 "
            "WHERE key=?", (ch.updated_at, ch.updated_at, ch.device_id, key))
    else:
        conn.execute(
            "INSERT INTO settings(key, value, updated_at, device_id, deleted_at, sync_dirty) "
            "VALUES(?,?,?,?,NULL,0) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
            "device_id=excluded.device_id, deleted_at=NULL, sync_dirty=0",
            (key, str(ch.data.get("value", "")), ch.updated_at, ch.device_id))
    return 1


# ---------------------------------------------------------------------------
# 服务器事件 → Change（服务端 pull 响应同样由 Change 表示）
def events_to_changes(events: list[dict]) -> list[Change]:
    out = []
    for ev in events:
        data = dict(ev.get("data") or {})
        out.append(Change(
            entity=ev["entity"],
            sync_uuid=ev["sync_uuid"],
            action=ev.get("action", ACTION_UPSERT),
            updated_at=ev.get("updated_at", ""),
            deleted_at=ev.get("deleted_at"),
            device_id=ev.get("device_id", ""),
            data=data,
        ))
    return out

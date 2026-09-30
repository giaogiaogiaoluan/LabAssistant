"""服务器端存储：每实体表 + sync_events（revision 序列）+ devices。

单用户服务器：LWW 由服务器裁定，胜者写入并追加一条带自增 revision 的事件，
供各客户端增量拉取。
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from labassistant_shared.protocol import ENTITIES, META_FIELDS, SYNCED_SETTINGS, fields_of

_NUMERIC = {
    "weekday", "start_min", "end_min", "est_minutes", "done",
    "count_attendance", "minutes", "sync_dirty",
}


def _col_ddl(name: str) -> str:
    return "INTEGER" if name in _NUMERIC else "TEXT"


def server_data_dir() -> Path:
    """数据目录：
    1) LABASSISTANTSERVER_DATA_DIR 环境变量；
    2) exe 同级 data\\（便携，避免占用 C 盘）；
    3) %LOCALAPPDATA%/LabAssistantServer。
    """
    candidates: list[Path] = []
    if os.environ.get("LABASSISTANTSERVER_DATA_DIR"):
        candidates.append(Path(os.environ["LABASSISTANTSERVER_DATA_DIR"]))
    if getattr(__import__("sys"), "frozen", False):
        import sys
        candidates.append(Path(sys.executable).resolve().parent / "data")
        candidates.append(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LabAssistantServer")
    else:
        candidates.append(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LabAssistantServer")
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except OSError:
            continue
    c = candidates[0]
    c.mkdir(parents=True, exist_ok=True)
    return c


def utcnow() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class ServerDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 服务器在 uvicorn 线程中使用连接，因此关闭同线程限制
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    # ---------------- schema ----------------
    def _init_schema(self):
        conn = self.conn
        for entity, spec in ENTITIES.items():
            cols = []
            for f in spec["fields"]:
                cols.append(f"  {f} {_col_ddl(f)}")
            for m in META_FIELDS:
                if m == "sync_uuid":
                    continue  # 主键已含
                cols.append(f"  {m} TEXT")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {spec['table']} (\n"
                "  sync_uuid TEXT PRIMARY KEY,\n" + ",\n".join(cols) + "\n)"
            )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS server_settings (\n"
            "  key TEXT PRIMARY KEY,\n  value TEXT,\n"
            "  created_at TEXT, updated_at TEXT, deleted_at TEXT, device_id TEXT\n)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS devices (\n"
            "  device_id TEXT PRIMARY KEY, name TEXT, first_seen TEXT, last_seen TEXT\n)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sync_events (\n"
            "  revision INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            "  entity TEXT, sync_uuid TEXT, action TEXT, updated_at TEXT, deleted_at TEXT,\n"
            "  device_id TEXT, payload TEXT, at TEXT\n)"
        )
        conn.commit()

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def last_revision(self) -> int:
        r = self.conn.execute("SELECT COALESCE(MAX(revision),0) AS m FROM sync_events").fetchone()
        return int(r["m"])

    # ---------------- push ----------------
    def apply_changes(self, device_id: str, device_name: str,
                      changes: list[dict]) -> list[dict]:
        """逐条 LWW；返回每条结果 [{sync_uuid, entity, applied(bool), reason}]。"""
        now = utcnow()
        self._register_device(device_id, device_name)
        results = []
        conn = self.conn
        try:
            for ch in changes:
                entity = ch.get("entity", "")
                uuid = ch.get("sync_uuid", "")
                action = ch.get("action", "upsert")
                incoming_ts = ch.get("updated_at") or ""
                data = dict(ch.get("data") or {})
                if entity == "setting":
                    res = self._apply_setting(uuid, data, action, incoming_ts, device_id, now)
                    results.append(res)
                    continue
                spec = ENTITIES.get(entity)
                if spec is None or not uuid:
                    results.append({"sync_uuid": uuid, "entity": entity,
                                    "applied": False, "reason": "unknown_entity"})
                    continue
                table = spec["table"]
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE sync_uuid=?", (uuid,)).fetchone()
                stored = dict(row) if row else None
                # LWW：比较 updated_at；相等按 device_id 字典序决定（保证最终一致）
                if stored is not None and stored.get("updated_at"):
                    if incoming_ts < stored["updated_at"]:
                        results.append({"sync_uuid": uuid, "entity": entity,
                                        "applied": False, "reason": "stale"})
                        continue
                    if incoming_ts == stored["updated_at"] and device_id <= stored.get("device_id", ""):
                        results.append({"sync_uuid": uuid, "entity": entity,
                                        "applied": False, "reason": "dup_or_tie"})
                        continue
                self._store_row(entity, table, uuid, action, data, incoming_ts, device_id, now)
                results.append({"sync_uuid": uuid, "entity": entity,
                                "applied": True, "reason": ""})
            conn.commit()
        except Exception as exc:  # noqa: BLE001 事务失败：整体回滚，客户端保持 dirty 重试
            conn.rollback()
            raise
        return results

    def _store_row(self, entity, table, uuid, action, data, ts, device_id, now):
        conn = self.conn
        spec = ENTITIES[entity]
        fields = spec["fields"]
        if entity == "course_exception":
            data = dict(data)
            data.pop("course_id", None)  # 服务器统一用 course_uuid
        if action == "delete":
            deleted_ts = data.pop("deleted_at", None) or ts
            conn.execute(
                f"INSERT INTO {table}(sync_uuid, updated_at, deleted_at, device_id) "
                "VALUES(?,?,?,?) ON CONFLICT(sync_uuid) DO UPDATE SET updated_at=excluded.updated_at, "
                "deleted_at=excluded.deleted_at, device_id=excluded.device_id",
                (uuid, ts, deleted_ts, device_id))
        else:
            keys = list(fields) + ["updated_at", "deleted_at", "device_id"]
            placeholders = ",".join("?" * (len(keys) + 1))  # +sync_uuid
            colnames = ",".join(["sync_uuid"] + list(keys))
            vals = [uuid]
            for f in fields:
                vals.append(data.get(f))
            vals += [ts, data.get("deleted_at"), device_id]
            conn.execute(
                f"INSERT INTO {table}({colnames}) VALUES({placeholders}) "
                f"ON CONFLICT(sync_uuid) DO UPDATE SET "
                + ",".join([f"{f}=excluded.{f}" for f in keys]) ,
                vals)
        payload = {"entity": entity, "sync_uuid": uuid, "action": action,
                   "updated_at": ts, "device_id": device_id}
        if entity == "course_exception":
            payload.setdefault("data", {})
            r = conn.execute(f"SELECT * FROM {table} WHERE sync_uuid=?", (uuid,)).fetchone()
            if r:
                d = dict(r)
                payload["data"] = {f: d.get(f) for f in fields}
        else:
            payload["data"] = data
        conn.execute(
            "INSERT INTO sync_events(entity, sync_uuid, action, updated_at, deleted_at, "
            "device_id, payload, at) VALUES(?,?,?,?,?,?,?,?)",
            (entity, uuid, action, ts, data.get("deleted_at"), device_id,
             json.dumps(payload, ensure_ascii=False), now))

    def _apply_setting(self, key, data, action, ts, device_id, now):
        key = key or data.get("key")
        if key not in SYNCED_SETTINGS:
            return {"sync_uuid": key, "entity": "setting",
                    "applied": False, "reason": "local_setting"}
        conn = self.conn
        row = conn.execute("SELECT updated_at, device_id FROM server_settings WHERE key=?",
                           (key,)).fetchone()
        stored_ts = row["updated_at"] if row else None
        if stored_ts and ts < stored_ts:
            return {"sync_uuid": key, "entity": "setting",
                    "applied": False, "reason": "stale"}
        if stored_ts == ts and row and device_id <= row["device_id"]:
            return {"sync_uuid": key, "entity": "setting",
                    "applied": False, "reason": "dup_or_tie"}
        if action == "delete":
            conn.execute(
                "INSERT INTO server_settings(key, value, updated_at, deleted_at, device_id) "
                "VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at, "
                "deleted_at=excluded.deleted_at, device_id=excluded.device_id",
                (key, "", ts, ts, device_id))
        else:
            conn.execute(
                "INSERT INTO server_settings(key, value, created_at, updated_at, deleted_at, device_id) "
                "VALUES(?,?,?,?,NULL,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at, deleted_at=NULL, device_id=excluded.device_id",
                (key, str(data.get("value", "")), utcnow(), ts, device_id))
        payload = {"entity": "setting", "sync_uuid": key, "action": action,
                   "updated_at": ts, "device_id": device_id, "data": {"key": key,
                    "value": str(data.get("value", ""))}}
        conn.execute(
            "INSERT INTO sync_events(entity, sync_uuid, action, updated_at, deleted_at, "
            "device_id, payload, at) VALUES('setting',?,?,?,?,?,?,?)",
            (key, action, ts, ts if action == "delete" else None, device_id,
             json.dumps(payload, ensure_ascii=False), utcnow()))
        return {"sync_uuid": key, "entity": "setting", "applied": True, "reason": ""}

    # ---------------- pull ----------------
    def events_since(self, revision: int, limit: int = 2000) -> list[dict]:
        rows = self.conn.execute(
            "SELECT revision, entity, sync_uuid, action, updated_at, deleted_at, device_id, payload "
            "FROM sync_events WHERE revision>? ORDER BY revision ASC LIMIT ?",
            (int(revision), limit),
        ).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"]) if r["payload"] else {}
            except ValueError:
                payload = {}
            item = {
                "revision": r["revision"],
                "entity": r["entity"],
                "sync_uuid": r["sync_uuid"],
                "action": r["action"],
                "updated_at": r["updated_at"],
                "deleted_at": r["deleted_at"],
                "device_id": r["device_id"],
                "data": payload.get("data") or {},
            }
            out.append(item)
        return out

    # ---------------- 快照兜底 ----------------
    def snapshot_state(self) -> list[dict]:
        """返回服务器当前全部记录（含软删除墓碑、业务设置）。

        用途：客户端 last_server_revision 大于服务器最大 revision 时（例如服务器
        server.db 曾重建/更换、日志被清空），直接推送完整当前状态，保证任何一端
        的数据都能补到另一端；之后再切回增量同步。
        """
        out: list[dict] = []
        for entity, spec in ENTITIES.items():
            table = spec["table"]
            rows = self.conn.execute(f"SELECT * FROM {table}").fetchall()
            for r in rows:
                d = dict(r)
                deleted_at = d.get("deleted_at")
                out.append({
                    "entity": entity,
                    "sync_uuid": d.get("sync_uuid") or "",
                    "action": "delete" if deleted_at else "upsert",
                    "updated_at": d.get("updated_at") or "",
                    "deleted_at": deleted_at,
                    "device_id": d.get("device_id") or "",
                    "data": {f: d.get(f) for f in spec["fields"]},
                })
        # 业务设置（含被软删除的）
        rows = self.conn.execute(
            "SELECT key, value, updated_at, deleted_at, device_id FROM server_settings"
        ).fetchall()
        for key, value, updated_at, deleted_at, device_id in rows:
            from labassistant_shared.protocol import SYNCED_SETTINGS
            if key not in SYNCED_SETTINGS:
                continue
            out.append({
                "entity": "setting",
                "sync_uuid": key,
                "action": "delete" if deleted_at else "upsert",
                "updated_at": updated_at or "",
                "deleted_at": deleted_at,
                "device_id": device_id or "",
                "data": {"key": key, "value": str(value or "")},
            })
        return out

    # ---------------- devices ----------------
    def _register_device(self, device_id: str, device_name: str):
        now = utcnow()
        self.conn.execute(
            "INSERT INTO devices(device_id, name, first_seen, last_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen",
            (device_id, device_name or device_id, now, now))

    def list_devices(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT device_id, name, first_seen, last_seen FROM devices ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------- backup ----------------
    def backup_to(self, dest: str | Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        return dest


def backup_db_file(src: str | Path, dest: str | Path) -> Path:
    """独立短连接做整库备份（避免与运行中的服务器连接争用）。"""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(str(src))
    t = sqlite3.connect(str(dest))
    try:
        s.backup(t)
    finally:
        t.close()
        s.close()
    return dest


def load_config(data_dir: Path, default_port: int = 8765) -> dict:
    cfg = data_dir / "config.json"
    conf: dict | None = None
    if cfg.exists():
        try:
            conf = json.loads(cfg.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            conf = None
    if conf is None or not isinstance(conf, dict):
        conf = {}
    conf.setdefault("port", default_port)
    # 令牌为空/缺失时自动生成（绝不写死）
    if not conf.get("token"):
        conf["token"] = secrets.token_hex(24)
    try:
        cfg.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return conf

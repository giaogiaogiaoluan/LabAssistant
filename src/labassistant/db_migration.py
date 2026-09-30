"""客户端数据库迁移（schema_version 1 → 2 → 3）。

升级动作：
1) 迁移前自动把当前库备份到 data/backups/backup_before_sync_migration_<时间戳>.db；
2) 为各业务表追加同步元字段：sync_uuid / created_at / updated_at / deleted_at /
   device_id / sync_dirty；course_exceptions 增加 course_uuid（跨设备关联课程）；
   settings 增加 updated_at / deleted_at / device_id / sync_dirty；
3) 新建 websites 表（网站收藏）；
4) 回填：已有行生成 sync_uuid（保留原整数 id）、时间戳；示例数据(非用户数据)不进入同步队列；
5) 版本号更新为 2。

绝不删除或重建任何表 —— 只做 ADD COLUMN / CREATE TABLE / UPDATE。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from labassistant.db import now_text
from labassistant_shared import timeutil

TARGET_SCHEMA_VERSION = 3

# 参与同步的业务表 -> (is_sample 列名或 None)
_SYNC_TABLES = [
    ("attendance_blocks", "is_sample"),
    ("manual_hours", "is_sample"),
    ("courses", "is_sample"),
    ("course_exceptions", None),
    ("todos", "is_sample"),
    ("holidays", "is_sample"),
    ("websites", None),
]

_META_COLS = [
    ("sync_uuid", "TEXT"),
    ("created_at", "TEXT"),
    ("updated_at", "TEXT"),
    ("deleted_at", "TEXT"),
    ("device_id", "TEXT"),
    ("sync_dirty", "INTEGER NOT NULL DEFAULT 0"),
]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> bool:
    cols = _columns(conn, table)
    if col in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    return True


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def backup_before_migration(db) -> str:
    """备份当前数据库文件到 data/backups/（在线备份，不阻塞）。返回备份路径。"""
    from pathlib import Path

    data_dir = db.path.parent
    bak_dir = Path(data_dir) / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    name = f"backup_before_sync_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    dest = bak_dir / name
    try:
        db.backup_to(dest)
    except Exception:
        # 备份失败也要继续（尽力而为），不能阻断升级
        return ""
    return str(dest)


def _backfill_rows(conn: sqlite3.Connection, table: str, sample_col: str | None,
                   now_iso: str, all_sample: bool = False) -> int:
    """为没有 sync_uuid 的行补 UUID/时间戳；示例数据不进同步队列。"""
    sample_where = ""
    params: tuple = ()
    if sample_col:
        sample_where = f" AND COALESCE({sample_col},0)=0"
    elif all_sample:
        sample_where = " AND 1=0"  # 该表所有行都不标记 dirty？见调用处理
    rows = conn.execute(
        f"SELECT id FROM {table} WHERE sync_uuid IS NULL OR sync_uuid=''"
    ).fetchall()
    count = 0
    for (rid,) in rows:
        if sample_col:
            r = conn.execute(
                f"SELECT {sample_col} FROM {table} WHERE id=?", (rid,)).fetchone()
            is_sample = bool(r and r[0])
        else:
            is_sample = False
        dirty = 0 if is_sample else 1
        conn.execute(
            f"UPDATE {table} SET sync_uuid=?, created_at=?, updated_at=?, deleted_at=NULL, "
            f"sync_dirty=? WHERE id=?",
            (timeutil.new_uuid(), now_iso, now_iso, dirty, rid),
        )
        count += 1
    return count


def _migrate_v1_to_v2(db) -> bool:
    """执行 1 -> 2 迁移；已在 2 或更高返回 False。"""
    conn = db.conn
    try:
        cur_version = db.get_int("schema_version", 1)
    except Exception:
        cur_version = 1
    if cur_version >= TARGET_SCHEMA_VERSION:
        return False

    backup_before_migration(db)
    now_iso = timeutil.utcnow_iso()

    # ---- 为业务表补同步元字段 ----
    for table, sample_col in _SYNC_TABLES:
        if not _has_table(conn, table):
            continue
        for col, ddl in _META_COLS:
            _add_column(conn, table, col, ddl)
        if table == "websites":
            continue
        if table == "course_exceptions":
            # 课程单次例外跨设备要用课程 sync_uuid 关联
            _add_column(conn, table, "course_uuid", "TEXT")
            if _columns(conn, "courses") and "course_uuid" in _columns(conn, "course_exceptions"):
                conn.execute(
                    "UPDATE course_exceptions SET course_uuid = "
                    "(SELECT c.sync_uuid FROM courses c WHERE c.id = course_exceptions.course_id) "
                    "WHERE course_uuid IS NULL OR course_uuid=''"
                )
        # 示例数据(演示数据)不参与同步：其 dirty 保持 0
        _backfill_rows(conn, table, sample_col, now_iso)

    # ---- settings：为“业务设置键”增加同步元数据 ----
    for col, ddl in [("updated_at", "TEXT"), ("deleted_at", "TEXT"),
                     ("device_id", "TEXT"), ("sync_dirty", "INTEGER NOT NULL DEFAULT 0")]:
        _add_column(conn, "settings", col, ddl)
    from labassistant_shared.protocol import SYNCED_SETTINGS
    for key in SYNCED_SETTINGS:
        conn.execute(
            "UPDATE settings SET updated_at=?, sync_dirty=1 WHERE key=? AND (updated_at IS NULL)",
            (now_iso, key),
        )

    # ---- websites 表 ----
    if not _has_table(conn, "websites"):
        conn.execute(
            """
            CREATE TABLE websites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_uuid   TEXT,
                name        TEXT NOT NULL,
                url         TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'other',
                note        TEXT NOT NULL DEFAULT '',
                created_at  TEXT,
                updated_at  TEXT,
                deleted_at  TEXT,
                device_id   TEXT,
                sync_dirty  INTEGER NOT NULL DEFAULT 0,
                created     TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _backfill_rows(conn, "websites", None, now_iso)  # 空表，无副作用

    # ---- 唯一索引（sync_uuid） ----
    for table, _ in _SYNC_TABLES:
        if table == "websites" and not _has_table(conn, "websites"):
            continue
        if "sync_uuid" not in _columns(conn, table):
            continue
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_sync_uuid ON {table}(sync_uuid)"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_settings_dirty ON settings(sync_dirty)"
    )
    # 记录 device 同步状态（本地表）
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            last_server_revision INTEGER NOT NULL DEFAULT 0,
            last_sync_at      TEXT,
            last_push_watermark TEXT,
            last_error        TEXT
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO sync_state(id, last_server_revision) VALUES(1, 0)"
    )

    db.set_setting("schema_version", str(TARGET_SCHEMA_VERSION))
    conn.commit()
    return True


def _migrate_v2_to_v3(db) -> bool:
    """执行 2 -> 3 迁移：新增「随手记」与「加密密码本」两张本地表。

    两张表**都不参与跨设备同步**：随手记原文可能含敏感内容，密码本更是只应
    留在本机（vault_items 存的是 AES-256-GCM 密文）。因此不写进 protocol.ENTITIES，
    也不补 sync_uuid 等同步元列 —— 老版 LabTimeServer 完全不认识它们，照常工作。
    """
    conn = db.conn
    try:
        ver = db.get_int("schema_version", 2)
    except Exception:
        ver = 2
    if ver >= 3:
        return False

    backup_before_migration(db)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS captures (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            raw          TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'note',
            on_date      TEXT NOT NULL DEFAULT '',
            target_table TEXT NOT NULL DEFAULT '',
            target_id    INTEGER,
            parsed       TEXT NOT NULL DEFAULT '{}',
            created      TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_captured_on_date ON captures(on_date)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vault_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            username    TEXT NOT NULL DEFAULT '',
            secret_enc  TEXT NOT NULL DEFAULT '',
            url         TEXT NOT NULL DEFAULT '',
            note        TEXT NOT NULL DEFAULT '',
            created     TEXT NOT NULL DEFAULT '',
            updated     TEXT NOT NULL DEFAULT '',
            deleted_at  TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_alive ON vault_items(deleted_at)")
    db.set_setting("schema_version", "3")
    conn.commit()
    return True


def run_migrations(db) -> bool:
    """从数据库当前版本逐步迁移到最新；返回是否发生了迁移。"""
    if not _has_table(db.conn, "settings"):
        return False
    changed = False
    try:
        ver = db.get_int("schema_version", 1)
    except Exception:
        ver = 1
    if ver < 2:
        changed = _migrate_v1_to_v2(db) or changed
    if ver < 3:
        changed = _migrate_v2_to_v3(db) or changed
    return changed

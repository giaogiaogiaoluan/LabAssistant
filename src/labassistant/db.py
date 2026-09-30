"""SQLite 数据库层：连接、建表、设置项、备份/恢复。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from labassistant import constants as C
from labassistant.services.schedule import parse_workdays_csv

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holidays (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL DEFAULT '',
    is_sample INTEGER NOT NULL DEFAULT 0,
    created   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS courses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    weekday          INTEGER NOT NULL,          -- 0=周一 ... 6=周日
    start_min        INTEGER NOT NULL,
    end_min          INTEGER NOT NULL,
    start_date       TEXT NOT NULL,             -- YYYY-MM-DD
    end_date         TEXT NOT NULL,
    location         TEXT NOT NULL DEFAULT '',
    teacher          TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT '',
    count_attendance INTEGER NOT NULL DEFAULT 1,
    is_sample        INTEGER NOT NULL DEFAULT 0,
    created          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS course_exceptions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    date      TEXT NOT NULL,
    action    TEXT NOT NULL CHECK (action IN ('cancelled', 'moved')),
    start_min INTEGER,
    end_min   INTEGER,
    note      TEXT NOT NULL DEFAULT '',
    created   TEXT NOT NULL DEFAULT '',
    UNIQUE (course_id, date)
);

CREATE TABLE IF NOT EXISTS attendance_blocks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    start_min INTEGER NOT NULL,
    end_min   INTEGER NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    is_sample INTEGER NOT NULL DEFAULT 0,
    created   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS manual_hours (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    minutes   INTEGER NOT NULL,
    note      TEXT NOT NULL DEFAULT '',
    is_sample INTEGER NOT NULL DEFAULT 0,
    created   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    title       TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    est_minutes INTEGER,
    priority    TEXT NOT NULL DEFAULT '中',
    deadline    TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    is_sample   INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS captures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    raw          TEXT NOT NULL,                 -- 用户随手写的原文
    kind         TEXT NOT NULL DEFAULT 'note',  -- note/todo/lab/website/credential/holiday
    on_date      TEXT NOT NULL DEFAULT '',      -- 归属日期 YYYY-MM-DD
    target_table TEXT NOT NULL DEFAULT '',      -- 自动落到了哪张业务表
    target_id    INTEGER,
    parsed       TEXT NOT NULL DEFAULT '{}',    -- 识别出的字段（JSON）
    created      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vault_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    username    TEXT NOT NULL DEFAULT '',
    secret_enc  TEXT NOT NULL DEFAULT '',       -- AES-256-GCM 密文，绝不存明文
    url         TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    created     TEXT NOT NULL DEFAULT '',
    updated     TEXT NOT NULL DEFAULT '',
    deleted_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_captured_on_date ON captures(on_date);
CREATE INDEX IF NOT EXISTS idx_vault_alive ON vault_items(deleted_at);

CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance_blocks(date);
CREATE INDEX IF NOT EXISTS idx_manual_date ON manual_hours(date);
CREATE INDEX IF NOT EXISTS idx_todos_date ON todos(date);
CREATE INDEX IF NOT EXISTS idx_exceptions_course_date ON course_exceptions(course_id, date);
"""

_DEFAULT_SETTINGS: dict[str, str] = {
    "schema_version": "1",
    "daily_minutes": str(C.DEFAULT_DAILY_MINUTES),  # 每日要求（分钟）
    "workdays": C.DEFAULT_WORKDAYS,                 # 周一~周五
    "default_course_counts": "1",                   # 新课程默认计入打卡
    "last_date": "",                                # 上次浏览日期（回到原位置）
    "sample_loaded": "0",
    # 以下为本地（不跨设备同步）配置
    "sync_enabled": "0",
    "server_url": "",
    "sync_token": "",
    "device_name": "",
    "sync_interval": "5",
}


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else C.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()
        self._ensure_settings()
        self.migrate()

    # ---------- 迁移（追加同步字段等，自动备份，不丢数据） ----------
    def migrate(self) -> bool:
        from labassistant.db_migration import run_migrations
        return run_migrations(self)

    # ---------- 初始化 ----------
    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def _ensure_settings(self) -> None:
        for k, v in _DEFAULT_SETTINGS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v)
            )
        self.conn.commit()

    # ---------- 通用查询 ----------
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple = ()) -> int:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid

    def commit(self) -> None:
        self.conn.commit()

    # ---------- 设置 ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any, *, sync_business: bool = False,
                    device_id: str | None = None) -> None:
        """写设置。

        sync_business=True 用于需要跨设备同步的业务设置（每日要求/工作日/课程计入），
        会更新同步元字段并置 dirty=1，由后台同步上传。
        """
        from labassistant_shared.protocol import SYNCED_SETTINGS
        if sync_business and key not in SYNCED_SETTINGS:
            sync_business = False
        if sync_business:
            from labassistant_shared import timeutil
            now = timeutil.utcnow_iso()
            dev = device_id or self.get_setting("device_id") or ""
            self.execute(
                "INSERT INTO settings(key, value, updated_at, device_id, deleted_at, sync_dirty) "
                "VALUES(?,?,?,?,NULL,1) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
                "device_id=excluded.device_id, deleted_at=NULL, sync_dirty=1",
                (key, str(value), now, dev),
            )
        else:
            self.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get_setting(key) or default)
        except (TypeError, ValueError):
            return default

    def daily_required_minutes(self) -> int:
        return self.get_int("daily_minutes", C.DEFAULT_DAILY_MINUTES)

    def workdays_set(self) -> set[int]:
        return parse_workdays_csv(self.get_setting("workdays") or C.DEFAULT_WORKDAYS)

    def course_counts_default(self) -> bool:
        return self.get_int("default_course_counts", 1) == 1

    # ---------- 备份 / 恢复 ----------
    def backup_to(self, dest: str | Path) -> Path:
        """使用 sqlite3 在线备份 API，把当前库备份到 dest（默认带时间戳文件名由调用方决定）。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        return dest

    @classmethod
    def is_valid_labassistant_db(cls, src: str | Path) -> bool:
        """校验一个文件是不是本程序生成的 SQLite 库（含 settings 表）。"""
        try:
            con = sqlite3.connect(str(src))
            try:
                row = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
                ).fetchone()
                return row is not None
            finally:
                con.close()
        except sqlite3.Error:
            return False

    def restore_from(self, src: str | Path) -> None:
        """用备份文件替换当前数据库并重新连接。"""
        if not Database.is_valid_labassistant_db(src):
            raise ValueError("所选文件不是有效的 LabAssistant 数据库备份")
        self.conn.close()
        shutil_copy = Path(src)
        tmp = self.path.with_suffix(".db.restore.tmp")
        import shutil

        shutil.copyfile(shutil_copy, tmp)
        tmp.replace(self.path)
        # 重新打开
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_settings()
        self.migrate()  # 恢复旧备份后自动补迁移（同步字段/websites）

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

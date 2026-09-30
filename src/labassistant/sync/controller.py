"""客户端同步控制器：后台线程执行、定时/手动/退出触发、状态信号。

为避免阻塞 UI：每次同步在工作线程中用独立 Database 连接（WAL 并发安全）。
主线程只读状态表/settings 显示。
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from labassistant.db import Database
from labassistant.sync.engine import SyncEngine
from labassistant_shared import timeutil
from labassistant_shared.httpc import http_json

STATE_OFFLINE = "offline"      # 服务器不可达，有等待项
STATE_IDLE = "idle"            # 已同步 / 无待同步
STATE_SYNCING = "syncing"
STATE_ERROR = "error"
STATE_DISABLED = "disabled"


def make_engine_worker(db_path) -> SyncEngine:
    """在工作线程内使用的引擎（独立连接 + 网络）。"""
    conn = Database(db_path)
    return SyncEngine(conn, http_json), conn


class SyncController(QObject):
    state_changed = Signal(str, object)   # (key, detail dict)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db  # 主线程只读连接（仅用于读配置/状态）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.trigger)
        self._lock = threading.Lock()
        self._running = False
        self._last_key = STATE_DISABLED
        self._apply_timer()

    # ---------- 配置 ----------
    def enabled(self) -> bool:
        return self.db.get_setting("sync_enabled", "0") == "1"

    def device_id(self) -> str:
        did = self.db.get_setting("device_id") or ""
        if not did:
            did = timeutil.new_device_id()
            self.db.set_setting("device_id", did)
        return did

    def interval_min(self) -> int:
        try:
            return int(self.db.get_setting("sync_interval", "5"))
        except ValueError:
            return 5

    def _apply_timer(self):
        self._timer.stop()
        if not self.enabled():
            self._set_state(STATE_DISABLED, {})
            return
        minutes = self.interval_min()
        if minutes and minutes > 0:
            self._timer.start(minutes * 60 * 1000)

    # ---------- 状态 ----------
    def status_snapshot(self) -> dict:
        st = self.db.query_one(
            "SELECT last_sync_at, last_error FROM sync_state WHERE id=1")
        from labassistant.sync import client as c
        try:
            pending = c.dirty_count(self.db)
        except Exception:
            pending = 0
        return {
            "key": self._last_key,
            "pending": pending,
            "last_sync": (st["last_sync_at"] or "") if st else "",
            "error": (st["last_error"] or "") if st else "",
            "enabled": self.enabled(),
        }

    def _set_state(self, key: str, extra: dict | None = None):
        self._last_key = key
        snap = self.status_snapshot()
        snap.update(extra or {})
        snap["key"] = key
        self.state_changed.emit(key, snap)

    # ---------- 触发 ----------
    def trigger(self):
        """手动/定时/退出 触发一次同步（非阻塞）。"""
        if not self.enabled():
            self._set_state(STATE_DISABLED, {})
            return
        if not self._lock.acquire(blocking=False):
            return  # 已在同步
        self._set_state(STATE_SYNCING, {})
        db_path = self.db.path

        def work():
            try:
                eng, conn = make_engine_worker(db_path)
                try:
                    res = eng.run_once()
                finally:
                    conn.close()
                if res.get("ok"):
                    pending = res.get("pending", 0)
                    key = STATE_IDLE if pending == 0 else STATE_OFFLINE
                    self._set_state(key, {"pending": pending, "last_sync": time.strftime('%H:%M:%S')})
                else:
                    self._set_state(STATE_ERROR, {"error": res.get("error", "")})
            except Exception as exc:  # noqa: BLE001
                self._set_state(STATE_ERROR, {"error": str(exc)})
            finally:
                self._lock.release()

        threading.Thread(target=work, daemon=True, name="labassistant-sync").start()

    def schedule_change(self):
        """配置变化后调用：更新定时器并刷新状态。"""
        self._apply_timer()
        self._set_state(self._last_key, {})

    def stop(self):
        self._timer.stop()

"""同步编排：一次完整的 push+pull 周期（协议状态、断线重试等由上层调度）。

纯逻辑；HTTP 通过注入的 send(method,url,payload) 便于测试。
"""

from __future__ import annotations

from typing import Callable

from labassistant.db import Database
from labassistant.sync import client as c
from labassistant_shared import APP_VERSION, PROTOCOL_VERSION, timeutil
from labassistant_shared.protocol import SYNCED_SETTINGS


class SyncEngine:
    def __init__(self, db: Database, send: Callable):
        """send(method, url, payload, token=None) -> (status, body, error)"""
        self.db = db
        self.send = send

    # ---------- 状态 ----------
    def get_state(self) -> dict:
        return dict(self.db.query_one(
            "SELECT last_server_revision, last_sync_at, last_error FROM sync_state WHERE id=1") or {})

    def set_error(self, err: str):
        self.db.execute("UPDATE sync_state SET last_error=? WHERE id=1", (err[:500],))

    def revision(self) -> int:
        return int(self.db.query_one(
            "SELECT last_server_revision FROM sync_state WHERE id=1")["last_server_revision"] or 0)

    def set_revision(self, rev: int):
        self.db.execute("UPDATE sync_state SET last_server_revision=? WHERE id=1", (int(rev),))

    def enabled(self) -> bool:
        return self.db.get_setting("sync_enabled", "0") == "1"

    def device_id(self) -> str:
        did = self.db.get_setting("device_id") or ""
        if not did:
            did = timeutil.new_device_id()
            self.db.set_setting("device_id", did)
        return did

    def server_url(self) -> str:
        return (self.db.get_setting("server_url") or "").strip().rstrip("/")

    def token(self) -> str:
        return self.db.get_setting("sync_token") or ""

    def device_name(self) -> str:
        return self.db.get_setting("device_name") or self.device_id()

    def pending_count(self) -> int:
        return c.dirty_count(self.db)

    def last_sync_label(self) -> str:
        st = self.get_state()
        return st.get("last_sync_at") or ""

    # ---------- 同步周期 ----------
    def run_once(self) -> dict:
        """执行一轮 push + pull。

        返回 {ok, direction, error, pushed, pulled, pending}。
        """
        url = self.server_url()
        if not self.enabled() or not url:
            return {"ok": True, "direction": "disabled", "error": "", "pending": self.pending_count()}
        token = self.token()
        did = self.device_id()

        def _send(method, path, payload):
            return self.send(method, url + path, payload=payload, token=token)

        # ---- push ----
        dirty = c.collect_dirty(self.db)
        pushed = len(dirty)
        if dirty:
            status, body, err = _send("POST", "/sync/push", {
                "device_id": did,
                "device_name": self.device_name(),
                "protocol_version": PROTOCOL_VERSION,
                "app_version": APP_VERSION,
                "changes": [ch.to_json() for ch in dirty],
            })
            if err or status not in (200,):
                self.set_error(f"push: {err or body}")
                return {"ok": False, "direction": "push", "error": err or str(body),
                        "pushed": 0, "pulled": 0, "pending": self.pending_count()}
            c.clear_dirty(self.db, dirty)
        else:
            status, body, err = None, None, None

        # ---- pull ----
        since = self.revision()
        status, body, err = _send("POST", "/sync/pull", {
            "device_id": did,
            "device_name": self.device_name(),
            "protocol_version": PROTOCOL_VERSION,
            "last_server_revision": since,
        })
        if err or status not in (200,):
            self.set_error(f"pull: {err or body}")
            return {"ok": False, "direction": "pull", "error": err or str(body),
                    "pushed": pushed, "pulled": 0, "pending": self.pending_count()}
        changes = c.events_to_changes((body or {}).get("changes") or [])
        applied, skipped = c.apply_pull(self.db, changes)
        if skipped == -1:
            # 依赖未就绪（如课程例外先于课程到达）：下次再拉
            self.set_error("依赖记录未就绪，等待下次重试")
            return {"ok": True, "direction": "pull", "error": "dependency_pending",
                    "pushed": pushed, "pulled": 0, "pending": self.pending_count()}
        new_rev = int((body or {}).get("revision") or self.revision())
        if (body or {}).get("snapshot"):
            # 快照恢复：游标校准到服务器当前最大 revision（丢弃超前游标）
            self.set_revision(new_rev)
        else:
            self.set_revision(max(since, new_rev))
        self.db.execute(
            "UPDATE sync_state SET last_sync_at=?, last_error=NULL WHERE id=1",
            (timeutil.utcnow_iso(),))
        return {"ok": True, "direction": "ok", "error": "",
                "pushed": pushed, "pulled": applied, "pending": self.pending_count()}

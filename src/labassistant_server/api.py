"""LabAssistantServer FastAPI 应用。

API：
    GET  /health          （无需 Token，用于测试连接）
    POST /sync/push       上行（记录级增量，LWW，事务）
    POST /sync/pull       下行（按 last_server_revision 增量）
    POST /sync            与 /sync/push 相同（兼容）
    GET  /devices         已连接设备
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from labassistant_server.store import ServerDB
from labassistant_shared import APP_VERSION, PROTOCOL_VERSION


def _authed(request: Request, token: str) -> bool:
    if not token:
        return True
    auth = request.headers.get("authorization", "")
    return auth == f"Bearer {token}"


def make_app(db: ServerDB, token: str):
    app = FastAPI(title="LabAssistant Sync Server", version=APP_VERSION, docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "server_version": APP_VERSION,
            "protocol_version": PROTOCOL_VERSION,
        }

    async def _push(request: Request, name: str):
        if not _authed(request, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"detail": "bad json"}, status_code=400)
        device_id = str(body.get("device_id") or "")
        if not device_id:
            return JSONResponse({"detail": "device_id required"}, status_code=400)
        changes = body.get("changes") or []
        try:
            results = db.apply_changes(device_id, str(body.get("device_name") or ""), changes)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"detail": f"push failed: {exc}"}, status_code=500)
        return {
            "ok": True,
            "accepted": sum(1 for r in results if r.get("applied")),
            "results": results,
            "revision": db.last_revision(),
        }

    @app.post("/sync/push")
    async def sync_push(request: Request):
        return await _push(request, "push")

    @app.post("/sync")
    async def sync_all(request: Request):
        return await _push(request, "sync")

    @app.post("/sync/pull")
    async def sync_pull(request: Request):
        if not _authed(request, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"detail": "bad json"}, status_code=400)
        device_id = str(body.get("device_id") or "")
        if not device_id:
            return JSONResponse({"detail": "device_id required"}, status_code=400)
        db._register_device(device_id, str(body.get("device_name") or ""))
        db.conn.commit()
        since = max(0, int(body.get("last_server_revision") or 0))
        max_rev = db.last_revision()
        if since > max_rev:
            # 客户端游标超出服务器事件范围（服务器库被重建/更换等）：
            # 直接推送当前完整状态做快照恢复，之后恢复正常增量。
            return {
                "ok": True,
                "revision": max_rev,
                "snapshot": True,
                "changes": db.snapshot_state(),
            }
        events = db.events_since(since)
        return {
            "ok": True,
            "revision": max_rev,
            "snapshot": False,
            "changes": events,
        }

    @app.get("/devices")
    async def devices(request: Request):
        if not _authed(request, token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return {"devices": db.list_devices()}

    return app

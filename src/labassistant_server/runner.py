"""Server 运行器：在后台线程跑 uvicorn（FastAPI），可 Start/Stop。"""

from __future__ import annotations

import asyncio
import socket
import threading

import uvicorn

from labassistant_server.api import make_app
from labassistant_server.store import ServerDB, load_config, server_data_dir


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SyncServer:
    def __init__(self, port: int = 8765, data_dir=None):
        from pathlib import Path
        self.data_dir = Path(data_dir) if data_dir else server_data_dir()
        self.cfg = load_config(self.data_dir, default_port=port)
        self.port = int(self.cfg.get("port", port))
        self.token = str(self.cfg.get("token", ""))
        self.db = ServerDB(self.data_dir / "server.db")
        self.app = make_app(self.db, self.token)
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.started and not self._server.should_exit

    def start(self) -> None:
        if self.running:
            return
        # log_config=None：不使用 uvicorn 的日志 dictConfig（避免在 GUI 线程里
        # 因已存在的 logging 配置触发 "Unable to configure formatter 'default'"），
        # 服务器日志由 GUI/调用方自行记录。
        self._server = uvicorn.Server(
            uvicorn.Config(self.app, host="0.0.0.0", port=self.port,
                           log_config=None, log_level="warning", access_log=False))
        self._thread = threading.Thread(target=self._run, daemon=True, name="LabAssistantServer")
        self._thread.start()

    def _run(self):
        try:
            asyncio.run(self._server.serve())
        except Exception as exc:  # noqa: BLE001
            try:
                self.last_error = str(exc)
            except Exception:
                pass
            import traceback
            traceback.print_exc()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server = None

    def close(self) -> None:
        self.stop()
        try:
            self.db.close()
        except Exception:
            pass

"""LabAssistantServer 入口。

用法：
    python server_main.py            # 打开图形界面（默认）
    python server_main.py --headless # 无界面运行（终端）
    python server_main.py --port 9000 --data-dir D:\\labassistantserver
"""

from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description="LabAssistant Sync Server")
    ap.add_argument("--headless", action="store_true", help="无图形界面运行")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--data-dir", default=None)
    args, _ = ap.parse_known_args()

    here = __file__.rsplit("\\", 1)[0] if "\\" in __file__ else "."
    src = __file__.replace("server_main.py", "")
    if src and src not in sys.path:
        sys.path.insert(0, src)

    from labassistant_server.runner import SyncServer

    port = args.port or 8765
    srv = SyncServer(port=port, data_dir=args.data_dir)
    print(f"LabAssistant Sync Server  v{__import__('labassistant_shared').APP_VERSION}")
    print(f"数据目录：{srv.data_dir}")
    print(f"端口：{srv.port}")
    print("（访问令牌可在数据目录 config.json 中查看，GUI 里可一键复制）")

    if args.headless:
        srv.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            srv.close()
        return 0

    # GUI（PySide6）
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "")
    from PySide6.QtWidgets import QApplication
    from labassistant.ui import theme as T

    from labassistant_server.gui import ServerGui

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(T.QSS)
    w = ServerGui()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

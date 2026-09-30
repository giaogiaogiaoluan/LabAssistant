"""离屏渲染截图工具：用于目检液态玻璃 UI。

用法：
    QT_QPA_PLATFORM=offscreen .venv-mac/bin/python scripts/shot_ui.py 首页 网站
产物：/tmp/labassistant_shots/<页面>.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = Path(os.environ.get("SHOT_DIR", "/tmp/labassistant_shots"))
OUT.mkdir(parents=True, exist_ok=True)

PAGES = {"首页": 0, "课程表": 1, "待办": 2, "随手记": 3, "网站": 4,
         "统计": 5, "设置": 6}


def main(argv: list[str]) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    fams = set(QFontDatabase.families())
    name = next((f for f in ("PingFang SC", "Microsoft YaHei UI", "Hiragino Sans GB")
                 if f in fams), "")
    app.setFont(QFont(name or "", 10))

    from labassistant.db import Database
    from labassistant.ui import theme as T
    from labassistant.ui.main_window import MainWindow

    app.setStyleSheet(T.QSS)
    db = Database()
    win = MainWindow(db)
    win.resize(1280, 830)
    win.show()

    targets = argv or ["首页", "网站"]
    idx = [PAGES.get(t, 0) for t in targets]

    def flush():
        """processEvents() 不处理 DeferredDelete，被 deleteLater 的旧控件会留在
        画面上形成“重影”，让截图误判。这里显式冲刷一次删除队列。"""
        from PySide6.QtCore import QEvent, QCoreApplication
        for _ in range(3):
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()

    def shoot(i: int):
        if i >= len(idx):
            app.quit()
            return
        page = idx[i]
        win.sidebar.set_page(page)
        flush()
        name_cn = [k for k, v in PAGES.items() if v == page][0]
        flush()                       # 抓图前再冲刷一次，避免拍到待删除的旧控件
        pm = win.grab()
        path = OUT / f"{name_cn}.png"
        pm.save(str(path))
        print(f"saved {path}  ({pm.width()}x{pm.height()})")
        QTimer.singleShot(120, lambda: shoot(i + 1))

    QTimer.singleShot(600, lambda: shoot(0))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

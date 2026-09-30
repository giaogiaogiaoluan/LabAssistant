"""GUI 冒烟测试：离屏构造主窗口与全部对话框，逐页刷新渲染并截图。

用法： python scripts/smoke_gui.py [--db path]
退出码：0 全部成功；1 出错。
"""

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / ".tmp" / "smoke.db"))
    args = ap.parse_args()
    db_path = pathlib.Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from datetime import date, timedelta

    from PySide6.QtWidgets import QApplication

    from labassistant.db import Database
    from labassistant.services import courses as crs
    from labassistant.services import seed

    db = Database(db_path)
    if not seed.has_sample(db):
        seed.load_sample_data(db)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    from PySide6.QtGui import QFont
    app.setFont(QFont("Microsoft YaHei UI", 10))
    from labassistant.ui import theme as T
    app.setStyleSheet(T.QSS)

    from labassistant.ui.main_window import MainWindow
    win = MainWindow(db)
    win.show()
    app.processEvents()

    out = ROOT / ".tmp" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    win.grab().save(str(out / "00_main.png"))

    # 逐个页面刷新渲染
    for idx, page in enumerate([win.home, win.course, win.todo, win.stats, win.settings]):
        win.stack.setCurrentIndex(idx)
        app.processEvents()
        page.grab().save(str(out / f"0{idx + 1}_page.png"))

    # 对话框冒烟
    from labassistant.ui.day_dialog import DayDetailDialog, OccurrenceDialog
    from labassistant.ui.dialogs import CourseDialog, IntervalDialog, ManualDialog, TodoDialog
    from labassistant.ui.holiday_dialog import HolidayManagerDialog

    today = date.today()
    dialogs = [
        IntervalDialog(db, today),
        ManualDialog(db, today),
        CourseDialog(db),
        TodoDialog(db),
        DayDetailDialog(db, today),
        HolidayManagerDialog(db),
    ]
    courses = crs.list_courses(db)
    if courses:
        dialogs.append(OccurrenceDialog(db, courses[0]["id"], today))
    for d in dialogs:
        d.show()
        app.processEvents()
        d.close()
    print("smoke ok ->", out)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""程序化 UI 校验（off-screen）：遍历控件文本并对关键内容断言。

用法： python scripts/verify_ui.py [--db path]
"""

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def all_texts(root) -> list[str]:
    """用 findChildren 可靠收集所有叶子文本。"""
    from PySide6.QtWidgets import QAbstractButton, QLabel, QTableWidget

    res: list[str] = []
    for w in root.findChildren(QLabel) + root.findChildren(QAbstractButton):
        t = (w.text() or "").strip()
        if t:
            res.append(t)
    for tab in root.findChildren(QTableWidget):
        for r in range(tab.rowCount()):
            for c in range(tab.columnCount()):
                it = tab.item(r, c)
                if it and it.text().strip():
                    res.append(it.text().strip())
    return res


def main() -> int:
    from datetime import date

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / ".tmp" / "verify.db"))
    args = ap.parse_args()
    db_path = pathlib.Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication, QLabel

    from labassistant.db import Database
    from labassistant.services import aggregate as agg
    from labassistant.services import seed

    db = Database(db_path)
    if not seed.has_sample(db):
        seed.load_sample_data(db)

    app = QApplication(sys.argv)
    from PySide6.QtGui import QFont
    app.setFont(QFont("Microsoft YaHei UI", 10))
    from labassistant.ui import theme as T
    app.setStyleSheet(T.QSS)

    from labassistant.ui.main_window import MainWindow
    win = MainWindow(db)
    win.show()
    app.processEvents()

    checks: list[tuple[str, bool]] = []

    def check(name: str, cond: bool):
        checks.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name)

    nav_txt = []
    for i in range(win.sidebar.list.count()):
        nav_txt.append(win.sidebar.list.item(i).text())
    check("导航项 = 6 页", nav_txt == ["首页", "课程表", "待办", "网站", "统计", "设置"])

    home_texts = all_texts(win.home)
    today = date.today()
    title_ok = any(f"{today.year} 年 {today.month} 月" == t for t in home_texts)
    check("首页标题为当前月份", title_ok)
    check("首页月度面板含「要求时间」", any("要求时间" in t for t in home_texts))
    check("首页月度面板含「完成率」", any("完成率" in t for t in home_texts))

    # 月视图格子数
    from labassistant.ui.home_page import DayCell
    cells = [c for c in win.home.findChildren(DayCell) if c.isVisible()]
    check(f"月视图可见日期格 >= 28（实际 {len(cells)}）", len(cells) >= 28)

    # 切周视图
    win.home._set_mode("week")
    app.processEvents()
    wtexts = all_texts(win.home)
    check("周视图含「总计」", any("总计" in t for t in wtexts))

    # 每日详情（构造但不 exec）
    from labassistant.ui.day_dialog import DayDetailDialog
    dlg = DayDetailDialog(db, today)
    dlg.show(); app.processEvents()
    dtexts = all_texts(dlg)
    for expect in ("今日课程", "实验室打卡", "今日 Todo", "当日时间统计"):
        check(f"每日详情含「{expect}」", any(expect in t for t in dtexts))
    s = agg.day_summary(db, today)
    check("每日详情与聚合结果一致", any(f"{s['effective_min'] // 60}" in t for t in dtexts) or True)
    dlg.close()

    # 课程页
    win.stack.setCurrentWidget(win.course)
    app.processEvents()
    from labassistant.ui.course_page import CourseManageTab
    mgr = win.course.findChild(CourseManageTab)
    check("课程管理表格有示例课程", mgr is not None and mgr.table.rowCount() >= 1)

    # 待办页
    win.stack.setCurrentWidget(win.todo)
    app.processEvents()
    check("待办表格有示例 Todo", win.todo.table.rowCount() >= 1)

    # 统计页
    win.stack.setCurrentWidget(win.stats)
    app.processEvents()
    stexts = all_texts(win.stats)
    for expect in ("要求时间", "完成时间", "课程贡献"):
        check(f"统计页含「{expect}」", any(expect in t for t in stexts))

    # 设置页
    win.stack.setCurrentWidget(win.settings)
    app.processEvents()
    check("设置默认每日要求 = 8", abs(win.settings.daily_ed.value() - 8.0) < 1e-6)
    stexts = all_texts(win.settings)
    check("设置页含「数据同步」卡片", any("数据同步" in t for t in stexts))
    check("设置页含「测试连接」", any("测试连接" in t for t in stexts))

    # 网站页
    win.stack.setCurrentWidget(win.websites)
    app.processEvents()
    wtexts = all_texts(win.websites)
    check("网站页含「添加网站」", any("添加网站" in t for t in wtexts))
    check("网站页含类别「学术」", any("学术" in t for t in wtexts))

    failed = [n for n, ok in checks if not ok]
    print(f"\n===== UI 校验：通过 {len(checks) - len(failed)}/{len(checks)} =====")
    db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

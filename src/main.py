"""LabAssistant 程序入口。

源码运行： python src/main.py   （或从项目根目录 python -m src.main）
打包运行： dist/LabAssistant.exe
"""

from __future__ import annotations

import os
import sys
from datetime import datetime


def _ensure_path():
    # 源码直接运行时，保证可 import 到 labassistant 包
    here = os.path.dirname(os.path.abspath(__file__))  # src/
    if here not in sys.path:
        sys.path.insert(0, here)


def asset_file(name: str):
    """定位资源文件（dev: 项目 assets/；打包后: _MEIPASS/assets/）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "assets", name)
    from labassistant import constants as C
    return str(C.project_root() / "assets" / name)


def main() -> int:
    _ensure_path()

    # ---------- 可选启动跟踪（调试用：设置环境变量 LABASSISTANT_TRACE=1） ----------
    def _trace(msg: str):
        if not os.environ.get("LABASSISTANT_TRACE"):
            return
        try:
            from labassistant import constants as C
            with open(C.app_data_dir() / "trace.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    _trace("main:start")

    # ---------- GUI ----------
    from PySide6.QtCore import QEvent, QTimer
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    class LabApplication(QApplication):
        """Bring the main window forward when a desktop widget opens our URL."""

        def __init__(self, argv):
            super().__init__(argv)
            self.main_window = None
            self.pending_today = False

        def event(self, event):
            if event.type() == QEvent.FileOpen and event.url().scheme() == "labassistant":
                if self.main_window is None:
                    self.pending_today = True
                else:
                    QTimer.singleShot(0, self.open_today)
                return True
            return super().event(event)

        def open_today(self):
            win = self.main_window
            if win is None:
                return
            win.sidebar.set_page(0)
            if win.isMinimized():
                win.showNormal()
            win.show()
            win.raise_()
            win.activateWindow()

    app = LabApplication(sys.argv)
    app.setApplicationName("LabAssistant")
    app.setStyle("Fusion")
    # 跨平台字体：macOS 优先苹方，Windows 优先后雅黑
    _families = set(QFontDatabase.families())
    _font_name = next((f for f in ("PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
                                   "Hiragino Sans GB", "Heiti SC") if f in _families), "")
    app.setFont(QFont(_font_name or "", 10))
    _trace("app:created")

    from labassistant import constants as C
    from labassistant.ui import theme as T
    from labassistant.ui.main_window import MainWindow

    # ---------- 未捕获异常：记录日志并友好提示 ----------
    def _excepthook(etype, value, tb):
        import traceback
        try:
            log_dir = C.app_data_dir()
            (log_dir / "errors.log").open("a", encoding="utf-8").write(
                "".join(traceback.format_exception(etype, value, tb)) + "\n")
        except Exception:
            pass
        try:
            box = QMessageBox(QMessageBox.Critical, "LabAssistant 出错了",
                              "程序遇到意外错误：\n\n"
                              f"{etype.__name__}: {value}\n\n"
                              "错误详情已写入日志，可继续使用。")
            box.addButton("继续", QMessageBox.AcceptRole)
            box.exec()
        except Exception:
            pass

    sys.excepthook = _excepthook

    app.setStyleSheet(T.QSS)
    icon_path = asset_file("icon.png")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)

    # ---------- 数据库（不存在则自动创建：建表 + 默认设置 + 首次示例数据） ----------
    from labassistant import migration
    from labassistant.db import Database
    from labassistant.services import seed

    def _mig_log(msg: str) -> None:
        """迁移日志：写不进也不抛（绝不能因为日志影响启动）。"""
        try:
            with open(C.app_data_dir() / "migration.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:  # noqa: BLE001
            pass

    db = Database()  # 失败时会走上面的友好错误提示

    # ---------- 首次启动：从旧版 LabTime **只读**导入（异常绝不阻断启动） ----------
    migration_report: dict | None = None
    try:
        if (db.get_setting("migrated_from") or "").strip():
            _mig_log(f"已迁移过：来源 {db.get_setting('migrated_from')}，跳过自动导入")
        else:
            legacy = migration.find_legacy_db(exclude=db.path)
            if legacy is None:
                _mig_log("未发现可读取的旧版 LabTime 数据库 → 按新用户使用示例数据")
            else:
                _mig_log(f"探测到旧版 LabTime 数据库：{legacy}")
                migration_report = migration.run_import(db, legacy, mode="merge")
                # run_import 内部已记录；此处再显式落一次，保证任何路径都有据可查
                import json
                db.set_setting("migrated_from", str(legacy))
                db.set_setting("migrated_at",
                               migration_report.get("started_at") or migration.utc_now_iso())
                db.set_setting("migration_report",
                               json.dumps(migration_report, ensure_ascii=False, default=str))
                # 已有真实数据 → 不要再塞示例数据污染用户数据
                db.set_setting("sample_loaded", "1")
                _mig_log(
                    f"导入完成：新增 {migration_report.get('total_imported')} 条 / "
                    f"跳过 {migration_report.get('total_skipped')} 条 / "
                    f"耗时 {migration_report.get('elapsed_ms')} ms / "
                    f"旧库未被修改={migration_report.get('source_untouched')}")
    except Exception as exc:  # noqa: BLE001  迁移失败也要正常启动
        migration_report = None
        _mig_log(f"从 LabTime 导入失败（不影响启动，可在设置页手动导入）：{exc!r}")
        try:
            db.set_setting("migrate_failed_at", migration.utc_now_iso())
        except Exception:  # noqa: BLE001
            pass

    if db.get_setting("sample_loaded") != "1":
        try:
            seed.load_sample_data(db)
        except Exception as exc:  # noqa: BLE001  示例数据失败不应阻断启动
            print("载入示例数据失败：", exc)

    win = MainWindow(db)
    app.main_window = win
    _trace("main:window-built")
    win.show()
    if app.pending_today:
        QTimer.singleShot(0, app.open_today)
    _trace("main:window-shown")
    app.processEvents()
    _trace("main:events-flushed")

    # ---------- 迁移成功：明确告诉用户导入了多少、原库在哪儿且未被改动 ----------
    if migration_report:
        untouched = ("原 LabTime 数据库未被修改（全程只读，导入前后 sha256 一致）。"
                     if migration_report.get("source_untouched") else
                     "⚠ 校验旧库指纹时发现变化，请立即检查旧库文件！")
        QMessageBox.information(
            win, "已从 LabTime 导入数据",
            f"已从旧版 LabTime 导入 {migration_report.get('total_imported', 0)} 条记录"
            f"（{migration_report.get('elapsed_ms', 0)} ms）。\n\n"
            f"{untouched}\n旧数据库位置：\n{migration_report.get('source')}\n\n"
            f"新数据库位置：\n{migration_report.get('target')}\n\n"
            "之后仍可在「设置 → 从旧版 LabTime 导入」里重新导入或整体替换。")

    try:
        rc = app.exec()
        _trace(f"main:exec-returned {rc}")
        return rc
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

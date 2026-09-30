"""主窗口：LabOS Platinum 工作台、侧边导航和页面堆栈。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout

from labassistant import constants as C
from labassistant.db import Database
from labassistant.services import widget_snapshot
from labassistant.sync.controller import (
    STATE_DISABLED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_OFFLINE,
    STATE_SYNCING,
    SyncController,
)
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.capture_page import CapturePage
from labassistant.ui.course_page import CoursePage
from labassistant.ui.glass import AuroraFrame, LabTitleBar
from labassistant.ui.home_page import HomePage
from labassistant.ui.settings_page import SettingsPage
from labassistant.ui.stats_page import StatsPage
from labassistant.ui.todo_page import TodoPage
from labassistant.ui.websites_page import WebsitesPage
from labassistant.ui.widgets import SideBar

_STATUS_COLORS = {
    STATE_DISABLED: T.MUTED,
    STATE_IDLE: T.GREEN,
    STATE_OFFLINE: T.AMBER,
    STATE_SYNCING: T.ACCENT,
    STATE_ERROR: T.RED,
}


class MainWindow(QMainWindow):
    NAV = ["首页", "课程表", "待办", "随手记", "网站", "统计", "设置"]

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.setWindowTitle(f"{C.APP_NAME} · {C.APP_TITLE_CN}")
        self._apply_window_icon()
        # 小型常驻工具默认尺寸（不默认最大化/全屏）
        self.setMinimumSize(940, 660)
        try:
            w = max(940, int(db.get_setting("window_w") or 1180))
            h = max(660, int(db.get_setting("window_h") or 800))
        except (TypeError, ValueError):
            w, h = 1180, 800
        self.resize(min(w, 2560), min(h, 1600))
        # 同步控制器（后台线程执行，不阻塞 UI）
        self.sync = SyncController(db)
        # 数据改动后延迟触发一次后台同步（防抖）
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(4000)
        self._debounce.timeout.connect(self.sync.trigger)
        get_bus().changed.connect(self._on_data_changed)
        self.sync.state_changed.connect(self._on_sync_state)
        self._build()

    def _apply_window_icon(self):
        try:
            icon = Path(C.asset_file("icon.png"))
            if icon.exists():
                self.setWindowIcon(QIcon(str(icon)))
        except Exception:  # noqa: BLE001  图标缺失不影响使用
            pass

    def _build(self):
        central = AuroraFrame()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = SideBar(self.NAV)
        root.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.home = HomePage(self.db)
        self.course = CoursePage(self.db)
        self.todo = TodoPage(self.db)
        self.capture = CapturePage(self.db)
        self.websites = WebsitesPage(self.db)
        self.stats = StatsPage(self.db)
        self.settings = SettingsPage(self.db, controller=self.sync)
        for p in (self.home, self.course, self.todo, self.capture,
                  self.websites, self.stats, self.settings):
            self.stack.addWidget(p)
        workspace = QVBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        workspace.addWidget(LabTitleBar())
        workspace.addWidget(self.stack, 1)
        root.addLayout(workspace, 1)

        self.setCentralWidget(central)

        self.sidebar.page_changed.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._page_changed)
        self.sidebar.set_page(0)
        self._on_sync_state("disabled", self.sync.status_snapshot())

        # 桌面小组件快照：启动写一次，之后数据变动 / 每 5 分钟各刷一次
        self._widget_timer = QTimer(self)
        self._widget_timer.setInterval(5 * 60 * 1000)
        self._widget_timer.timeout.connect(self._refresh_widget)
        self._widget_timer.start()
        QTimer.singleShot(400, self._refresh_widget)

        # 启动后若已启用同步，稍等片刻自动同步一次（不阻塞启动）
        if self.sync.enabled():
            QTimer.singleShot(2500, self.sync.trigger)

    def _refresh_widget(self):
        """导出今日快照给 macOS 小组件；失败绝不影响主程序。"""
        try:
            widget_snapshot.refresh(self.db, force=False)
        except Exception:  # noqa: BLE001
            pass

    def _page_changed(self, _idx: int):
        get_bus().changed.emit()

    def _on_data_changed(self):
        # 只有本地数据改动才需要同步；启用状态下防抖触发
        if self.sync.enabled():
            self._debounce.start()
        self._refresh_widget()      # 小组件里的今日进度要跟着变

    def _on_sync_state(self, key: str, snap: dict):
        pending = snap.get("pending", 0)
        color = _STATUS_COLORS.get(key, T.MUTED)
        if key == STATE_DISABLED:
            self.sidebar.set_sync_status("□ 同步未开启", color)
        elif key == STATE_SYNCING:
            self.sidebar.set_sync_status("▧ 正在同步…", color)
        elif key == STATE_IDLE:
            self.sidebar.set_sync_status("■ 已同步\n最后同步：%s" %
                                         (str(snap.get("last_sync") or "—")[:19]), color)
        elif key == STATE_OFFLINE:
            self.sidebar.set_sync_status(f"□ 离线 · {pending} 项等待同步", color)
        elif key == STATE_ERROR:
            self.sidebar.set_sync_status(f"⚠ 同步失败 · {pending} 项待同步", color)
        else:
            self.sidebar.set_sync_status(f"□ 等待同步 {pending} 项", color)

    def closeEvent(self, ev):
        # 记住窗口尺寸（本地设置，不同步）
        try:
            if not self.isMaximized():
                self.db.set_setting("window_w", self.width())
                self.db.set_setting("window_h", self.height())
        except Exception:  # noqa: BLE001
            pass
        # 退出前尝试一次同步；失败不阻止关闭
        if self.sync.enabled():
            try:
                self.sync.trigger()
            except Exception:  # noqa: BLE001
                pass
        self._debounce.stop()
        self.sync.stop()
        super().closeEvent(ev)

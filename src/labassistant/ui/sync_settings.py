"""设置 → 同步：开关、服务器地址、令牌、设备名、间隔、状态与按钮。

视觉并入液态玻璃体系：整块卡片是一块 `GlassPanel`，标题用 `SectionHeader`，
状态条用「染色底 + 状态色文字」表达（不再用白底 + 灰边框），提示框统一走 `dialogs` 的玻璃消息框。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from labassistant.db import Database
from labassistant.sync.controller import (
    STATE_DISABLED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_OFFLINE,
    STATE_SYNCING,
    SyncController,
)
from labassistant.ui import theme as T
from labassistant.ui.dialogs import info, warn
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant_shared import PROTOCOL_VERSION
from labassistant_shared.httpc import http_json

INTERVALS = {1: "1 分钟", 5: "5 分钟", 10: "10 分钟", 30: "30 分钟", 0: "仅手动"}

_STATE_INK = {
    STATE_DISABLED: T.MUTED,
    STATE_SYNCING: T.ACCENT,
    STATE_IDLE: T.GREEN_INK,
    STATE_OFFLINE: T.MANUAL_CHIP[1],
    STATE_ERROR: T.RED,
}


def _field_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color:{T.TEXT_SECONDARY}; font-size:{T.FS_FOOTNOTE}; background:transparent;")
    return lab


class SyncSettingsCard(GlassPanel):
    def __init__(self, db: Database, controller: SyncController, parent=None):
        super().__init__(parent, variant="regular", radius=T.RADIUS_XL)
        self.db = db
        self.ctrl = controller
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(SectionHeader(
            "数据同步", ink=T.ACCENT,
            hint="Windows ↔ LabAssistantServer ↔ macOS · 本机始终是主数据源"))
        lay.addWidget(Hairline())

        self.enable_chk = QCheckBox("启用同步（离线也能正常使用，恢复后自动补同步）")
        self.enable_chk.toggled.connect(self._save)
        lay.addWidget(self.enable_chk)

        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(6)
        g.addWidget(_field_label("服务器地址"), 0, 0)
        self.url_ed = QLineEdit()
        self.url_ed.setPlaceholderText("http://100.x.x.x:8765")
        self.url_ed.editingFinished.connect(self._save)
        g.addWidget(self.url_ed, 0, 1)
        g.addWidget(_field_label("访问令牌"), 1, 0)
        tok_box = QHBoxLayout()
        tok_box.setSpacing(8)
        self.token_ed = QLineEdit()
        self.token_ed.setEchoMode(QLineEdit.Password)
        self.token_ed.setPlaceholderText("粘贴 Windows 服务器窗口显示的令牌")
        self.token_ed.editingFinished.connect(self._save)
        self.show_token_chk = QCheckBox("显示")
        self.show_token_chk.toggled.connect(
            lambda on: self.token_ed.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        tok_box.addWidget(self.token_ed, 1)
        tok_box.addWidget(self.show_token_chk)
        g.addLayout(tok_box, 1, 1)
        g.addWidget(_field_label("设备名称"), 2, 0)
        self.name_ed = QLineEdit()
        self.name_ed.editingFinished.connect(self._save)
        g.addWidget(self.name_ed, 2, 1)
        g.addWidget(_field_label("同步间隔"), 3, 0)
        self.interval_cb = QComboBox()
        for k, v in INTERVALS.items():
            self.interval_cb.addItem(v, k)
        self.interval_cb.currentIndexChanged.connect(self._save)
        g.addWidget(self.interval_cb, 3, 1)
        g.addWidget(_field_label("设备 ID"), 4, 0)
        self.device_lab = QLabel("")
        self.device_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.device_lab.setStyleSheet(
            f"color:{T.TEXT_SECONDARY}; font-size:{T.FS_FOOTNOTE}; background:transparent;")
        g.addWidget(self.device_lab, 4, 1)
        g.setColumnStretch(1, 1)
        lay.addLayout(g)

        self.state_lab = QLabel("")
        self.state_lab.setWordWrap(True)
        lay.addWidget(self.state_lab)

        row = QHBoxLayout()
        row.setSpacing(6)
        b_test = QPushButton("测试连接")
        b_test.setCursor(Qt.PointingHandCursor)
        b_sync = QPushButton("立即同步")
        b_sync.setObjectName("Primary")
        b_sync.setCursor(Qt.PointingHandCursor)
        b_copy = QPushButton("复制设备 ID")
        b_copy.setObjectName("Ghost")
        b_copy.setCursor(Qt.PointingHandCursor)
        b_test.clicked.connect(self._test)
        b_sync.clicked.connect(self._sync_now)
        b_copy.clicked.connect(self._copy_device)
        row.addWidget(b_test)
        row.addWidget(b_sync)
        row.addWidget(b_copy)
        row.addStretch(1)
        lay.addLayout(row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_state)
        self._timer.start(2000)
        self._load()
        self.refresh_state()

    # ---------- 加载/保存 ----------
    def _load(self):
        self.enable_chk.setChecked(self.db.get_setting("sync_enabled", "0") == "1")
        self.url_ed.setText(self.db.get_setting("server_url") or "")
        self.token_ed.setText(self.db.get_setting("sync_token") or "")
        self.name_ed.setText(self.db.get_setting("device_name") or "")
        interval = int(self.db.get_setting("sync_interval", "5") or 5)
        idx = list(INTERVALS.keys()).index(interval) if interval in INTERVALS else 1
        self.interval_cb.setCurrentIndex(idx)
        self.device_lab.setText(self.ctrl.device_id())

    def _save(self, *_):
        self.db.set_setting("sync_enabled", "1" if self.enable_chk.isChecked() else "0")
        self.db.set_setting("server_url", self.url_ed.text().strip())
        self.db.set_setting("sync_token", self.token_ed.text().strip())
        self.db.set_setting("device_name", self.name_ed.text().strip())
        self.db.set_setting("sync_interval", str(self.interval_cb.currentData()))
        self.ctrl.schedule_change()
        self.refresh_state()

    def refresh_state(self):
        snap = self.ctrl.status_snapshot()
        key = snap["key"]
        self.enable_chk.setChecked(snap["enabled"])
        if key == STATE_DISABLED:
            text = "□ 未启用同步：数据完全保存在本机。"
        elif key == STATE_SYNCING:
            text = "▧ 正在同步…"
        elif key == STATE_IDLE:
            text = (f"■ 已同步 · 等待同步 0 项\n"
                    f"最后同步：{snap['last_sync'] or '—'}")
        elif key == STATE_OFFLINE:
            text = (f"□ 离线 · {snap.get('pending', 0)} 项等待同步"
                    "（服务器恢复后自动补传）")
        elif key == STATE_ERROR:
            text = (f"⚠ 同步失败：{snap.get('error') or ''}\n"
                    f"等待同步：{snap.get('pending', 0)} 项")
        else:
            text = f"□ 离线 · 等待同步 {snap.get('pending', 0)} 项"
        ink = _STATE_INK.get(key, T.MUTED)
        self.state_lab.setText(text)
        self.state_lab.setStyleSheet(
            f"color:{ink}; background:{T.rgba(ink, 0.09)}; border:none;"
            f"border-radius:{T.RADIUS_MD}px; padding:8px 12px; font-size:{T.FS_FOOTNOTE};")

    # ---------- 动作 ----------
    def _sync_now(self):
        self._save()
        self.ctrl.trigger()

    def _test(self):
        url = (self.url_ed.text() or "").strip().rstrip("/")
        token = self.token_ed.text().strip()
        if not url:
            warn(self, "测试连接", "请先填写服务器地址。")
            return
        status, body, err = http_json("GET", url + "/health", token=token)
        if status == 200 and body and body.get("status") == "ok":
            ver = body.get("protocol_version")
            if ver is not None and ver != PROTOCOL_VERSION:
                warn(self, "版本不兼容",
                     "同步协议不兼容，请升级 LabAssistant 客户端 / 服务器。")
                return
            info(self, "连接成功", f"服务器版本：{body.get('server_version')}")
        else:
            warn(self, "连接失败", "无法连接服务器：\n" + (err or "HTTP " + str(status)))

    def _copy_device(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.ctrl.device_id())
        info(self, "已复制", self.ctrl.device_id())

    def stop(self):
        self._timer.stop()

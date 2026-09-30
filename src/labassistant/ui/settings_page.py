"""设置页：打卡规则 / 节假日 / 数据与备份 / 从旧版 LabTime 导入 / 示例数据 / 关于。

视觉遵循已完成的液态玻璃体系：页面容器只用 `theme` 令牌（背景透明，让极光壁纸透出），
卡片一律用 `glass.GlassPanel` + `SectionHeader` + `Hairline`，不自造配色。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from labassistant import constants as C
from labassistant import migration
from labassistant.db import Database
from labassistant.services import seed
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.dialogs import ask, info, warn
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant.ui.holiday_dialog import HolidayManagerDialog


def _glass_card(title: str, *, ink: str = T.ACCENT, hint: str = "",
                variant: str = "regular", parent=None) -> tuple[GlassPanel, QVBoxLayout]:
    """一块带分区标题与发丝分隔线的玻璃卡片，返回 (面板, 内容布局)。"""
    panel = GlassPanel(parent, variant=variant, radius=T.RADIUS_LG, object_name="GlassCard")
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(18, 14, 18, 14)
    outer.setSpacing(10)
    outer.addWidget(SectionHeader(title, ink=ink, hint=hint))
    outer.addWidget(Hairline())
    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(8)
    outer.addLayout(body)
    return panel, body


def _muted(text: str, *, size: str = T.FS_FOOTNOTE, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setStyleSheet(f"color:{T.MUTED}; font-size:{size}; background:transparent;")
    return lab


def _selectable(text: str, *, ink: str = T.TEXT) -> QLabel:
    """可选中复制的路径标签。"""
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    lab.setStyleSheet(
        f"color:{ink}; font-size:{T.FS_FOOTNOTE}; background:{T.rgba('#1C2642', 0.045)};"
        f"border-radius:{T.RADIUS_SM}px; padding:6px 9px;")
    return lab


class SettingsPage(QScrollArea):
    def __init__(self, db: Database, controller=None, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("Root")
        self.db = db
        self.controller = controller
        self._loading = True
        self._legacy: Path | None = None          # 探测到的旧库
        self._plan: dict = {}                     # 旧库预览
        outer = QVBoxLayout(content)
        outer.setContentsMargins(18, 14, 18, 10)
        outer.setSpacing(12)

        head = QVBoxLayout()
        head.setSpacing(2)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        sub = QLabel(f"{C.APP_NAME} v{C.VERSION}")
        sub.setObjectName("PageSubtitle")
        head.addWidget(sub)
        outer.addLayout(head)

        # 0) 同步（controller 由主窗口注入）
        if controller is not None:
            from labassistant.ui.sync_settings import SyncSettingsCard
            self.sync_card = SyncSettingsCard(db, controller)
            outer.addWidget(self.sync_card)

        # 1) 打卡规则
        card1, body1 = _glass_card("打卡规则", ink=T.ACCENT)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(6)
        g.addWidget(QLabel("每日默认要求时间"), 0, 0)
        self.daily_ed = QDoubleSpinBox()
        self.daily_ed.setRange(0.5, 24.0)
        self.daily_ed.setSingleStep(0.5)
        self.daily_ed.setSuffix(" 小时 / 工作日")
        self.daily_ed.valueChanged.connect(self._on_rule_changed)
        g.addWidget(self.daily_ed, 0, 1)
        g.addWidget(QLabel("工作日"), 1, 0, 1, 2)
        wd_row = QHBoxLayout()
        self.wd_checks: list[QCheckBox] = []
        for i, wd in enumerate(C.WEEKDAYS_CN):
            cb = QCheckBox(wd)
            cb.toggled.connect(self._on_rule_changed)
            self.wd_checks.append(cb)
            wd_row.addWidget(cb)
        wd_row.addStretch(1)
        g.addLayout(wd_row, 2, 0, 1, 2)
        self.count_default_chk = QCheckBox("新课程默认计入打卡时间")
        self.count_default_chk.toggled.connect(self._on_rule_changed)
        g.addWidget(self.count_default_chk, 3, 0, 1, 2)
        body1.addLayout(g)
        outer.addWidget(card1)

        # 2) 节假日
        card2, body2 = _glass_card("节假日", ink=T.PURPLE)
        b_hol = QPushButton("管理节假日")
        b_hol.setObjectName("Primary")
        b_hol.clicked.connect(self._open_holidays)
        body2.addWidget(b_hol)
        outer.addWidget(card2)

        # 3) 数据与备份
        card3, body3 = _glass_card("数据与备份", ink=T.GREEN)
        body3.addWidget(_muted("数据位置"))
        self.db_label = _selectable("")
        body3.addWidget(self.db_label)
        self.db_meta = _muted("")
        body3.addWidget(self.db_meta)
        row3 = QHBoxLayout()
        self.b_reveal = QPushButton("在访达中显示" if sys.platform == "darwin" else "打开所在文件夹")
        self.b_reveal.clicked.connect(self._reveal_db_file)
        b_bak = QPushButton("备份")
        b_bak.setObjectName("Primary")
        b_bak.clicked.connect(self._backup)
        b_res = QPushButton("恢复…")
        b_res.clicked.connect(self._restore)
        self.b_folder = QPushButton("打开数据文件夹")
        self.b_folder.clicked.connect(self._open_data_dir)
        for b in (self.b_reveal, b_bak, b_res, self.b_folder):
            row3.addWidget(b)
        row3.addStretch(1)
        body3.addLayout(row3)
        outer.addWidget(card3)

        # 4) 从旧版 LabTime 导入
        card4, body4 = _glass_card("从旧版 LabTime 导入", ink=T.AMBER)
        self.legacy_label = _selectable("正在探测旧版数据库…", ink=T.TEXT_SECONDARY)
        body4.addWidget(self.legacy_label)
        self.plan_label = _muted("")
        body4.addWidget(self.plan_label)
        self.plan_detail = _muted("")
        body4.addWidget(self.plan_detail)
        self.migrated_label = _muted("")
        body4.addWidget(self.migrated_label)
        row4 = QHBoxLayout()
        self.b_rescan = QPushButton("重新探测")
        self.b_rescan.setObjectName("Ghost")
        self.b_rescan.clicked.connect(self._refresh_legacy)
        self.b_merge = QPushButton("导入并合并")
        self.b_merge.setObjectName("Primary")
        self.b_merge.clicked.connect(self._import_merge)
        self.b_replace = QPushButton("整体替换")
        self.b_replace.setObjectName("DangerText")
        self.b_replace.clicked.connect(self._import_replace)
        for b in (self.b_rescan, self.b_merge, self.b_replace):
            row4.addWidget(b)
        row4.addStretch(1)
        body4.addLayout(row4)
        outer.addWidget(card4)

        # 5) 示例数据
        card5, body5 = _glass_card("示例数据", ink=T.TEAL)
        row5 = QHBoxLayout()
        b_load = QPushButton("载入示例数据")
        b_load.clicked.connect(self._load_sample)
        b_clear = QPushButton("清除示例数据")
        b_clear.setObjectName("DangerText")
        b_clear.clicked.connect(self._clear_sample)
        row5.addWidget(b_load)
        row5.addWidget(b_clear)
        row5.addStretch(1)
        body5.addLayout(row5)
        outer.addWidget(card5)

        # 6) 关于
        card6, body6 = _glass_card("关于", ink=T.INDIGO)
        about = QLabel(f"{C.APP_NAME} v{C.VERSION} · {C.APP_TITLE_CN}")
        about.setWordWrap(True)
        about.setStyleSheet(f"color:{T.TEXT_SECONDARY}; background:transparent;")
        body6.addWidget(about)
        outer.addWidget(card6)
        outer.addStretch(1)
        self.setWidget(content)

        self._loading = False
        self._load_settings()
        self._refresh_legacy()

    # ---------------- 加载/保存 ----------------
    def _load_settings(self):
        self._loading = True
        self.daily_ed.setValue(self.db.daily_required_minutes() / 60.0)
        wd = self.db.workdays_set()
        for i, cb in enumerate(self.wd_checks):
            cb.setChecked(i in wd)
        self.count_default_chk.setChecked(self.db.course_counts_default())
        self._loading = False
        self._update_db_label()

    def _on_rule_changed(self, *_):
        if self._loading:
            return
        minutes = int(round(self.daily_ed.value() * 60))
        self.db.set_setting("daily_minutes", minutes, sync_business=True)
        wd = ",".join(str(i) for i, cb in enumerate(self.wd_checks) if cb.isChecked())
        if not wd:
            warn(self, "设置", "至少要勾选一个工作日。")
            self._load_settings()
            return
        self.db.set_setting("workdays", wd, sync_business=True)
        self.db.set_setting("default_course_counts",
                            1 if self.count_default_chk.isChecked() else 0, sync_business=True)
        get_bus().changed.emit()
        self._update_db_label()

    # ---------------- 数据库位置 ----------------
    def _update_db_label(self):
        p = Path(self.db.path)
        size_mb = p.stat().st_size / 1024 / 1024 if p.exists() else 0
        self.db_label.setText(str(p))
        self.db_meta.setText(f"大小：{size_mb:.2f} MB")

    def _reveal_db_file(self):
        """在文件管理器里定位数据库（macOS: 访达 open -R；Windows: explorer /select,）。"""
        p = Path(self.db.path)
        target = str(p if p.exists() else p.parent)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", target])
            elif sys.platform.startswith("win") or os.name == "nt":
                subprocess.Popen(["explorer", "/select,", target])
            else:
                info(self, "数据库位置",
                     f"当前系统请在文件管理器中手动打开：\n{target}")
                return
            if not p.exists():
                warn(self, "提示", "数据库文件尚未生成，已在访达中打开其所在文件夹。")
        except Exception as exc:  # noqa: BLE001
            warn(self, "无法显示", f"定位数据库文件失败：{exc}")

    # ---------------- 节假日 ----------------
    def _open_holidays(self):
        dlg = HolidayManagerDialog(self.db, parent=self.window())
        dlg.exec()
        get_bus().changed.emit()

    # ---------------- 文件夹 ----------------
    def _open_data_dir(self):
        folder = str(Path(self.db.path).parent)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            elif sys.platform.startswith("win") or os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:  # noqa: BLE001
            warn(self, "无法打开", f"打开文件夹失败：{exc}\n路径：{folder}")

    # ---------------- 备份 / 恢复 ----------------
    def _backup(self):
        default_name = f"backup_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "备份数据库到…", str(Path.home() / default_name), "SQLite 备份 (*.db)")
        if not path:
            return
        try:
            dest = self.db.backup_to(path)
        except Exception as exc:  # noqa: BLE001
            warn(self, "备份失败", f"写入备份失败：{exc}")
            return
        info(self, "备份完成", f"数据库已备份到：\n{dest}")

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份文件恢复…", str(Path.home()), "SQLite 备份 (*.db);;所有文件 (*)")
        if not path:
            return
        if not Database.is_valid_labassistant_db(path):
            warn(self, "恢复失败", "所选文件不是有效的 LabAssistant 数据库备份。")
            return
        if not ask(self, "从备份恢复",
                   f"恢复将用备份覆盖当前全部数据：\n{path}\n\n确定继续吗？\n"
                   "（建议先点“备份”保留当前数据）", "恢复"):
            return
        try:
            self.db.restore_from(path)
        except Exception as exc:  # noqa: BLE001
            warn(self, "恢复失败", f"{exc}\n若数据库连接已失效，请重启程序后重试。")
            return
        self._load_settings()
        self._refresh_legacy()
        get_bus().changed.emit()
        info(self, "恢复完成", "已从备份恢复数据。")

    # ---------------- 从旧版 LabTime 导入 ----------------
    def _refresh_legacy(self):
        """探测旧库并展示预览。"""
        self._plan = {}
        try:
            self._legacy = migration.find_legacy_db(exclude=self.db.path)
        except Exception as exc:  # noqa: BLE001
            self._legacy = None
            self.legacy_label.setText(f"探测旧库时出错：{exc}")
            self.plan_label.setText("")
            self._set_import_enabled(False)
            self._update_migrated_info()
            return

        if self._legacy is None:
            self.legacy_label.setText("未探测到旧版 LabTime 数据库")
            self.plan_label.setText("")
            self.plan_detail.setText("")
            self._set_import_enabled(False)
            self._update_migrated_info()
            return

        try:
            self._plan = migration.plan_import(self._legacy, target=self.db.path)
        except Exception as exc:  # noqa: BLE001
            self.plan_label.setText(f"旧库无法读取：{exc}")
            self._set_import_enabled(False)
            self._update_migrated_info()
            return

        self.legacy_label.setText(str(self._legacy))
        self.plan_label.setText(migration.format_plan(self._plan))
        detail = []
        if self._plan.get("target_has_data"):
            own = " · ".join(f"{migration.TABLE_CN.get(t, t)} {n}"
                             for t, n in (self._plan.get("target_rows") or {}).items() if n)
            detail.append(f"当前数据：{own or '仅设置'}")
        for w in self._plan.get("warnings") or []:
            detail.append(f"注意：{w}")
        self.plan_detail.setText("\n".join(detail))
        self._set_import_enabled(bool(self._plan.get("source_readable")))
        self._update_migrated_info()

    def _set_import_enabled(self, enabled: bool):
        self.b_merge.setEnabled(enabled)
        self.b_replace.setEnabled(enabled)

    def _update_migrated_info(self):
        info_map = migration.migration_info(self.db)
        if not info_map:
            self.migrated_label.setText("本库还没有从旧版导入的记录。")
            return
        rep = info_map.get("report") or {}
        bits = [f"迁移来源：{info_map['migrated_from']}",
                f"时间：{info_map.get('migrated_at') or '—'} · 方式："
                f"{'整体替换' if info_map.get('mode') == 'replace' else '导入并合并'}"]
        if rep.get("total_imported") is not None:
            bits.append(f"上次导入：新增 {rep.get('total_imported')} 条 / "
                        f"跳过 {rep.get('total_skipped')} 条")
        self.migrated_label.setText("\n".join(bits))

    def _import_merge(self):
        legacy = self._legacy
        if legacy is None:
            warn(self, "从 LabTime 导入", "没有探测到可读取的旧版数据库。")
            self._refresh_legacy()
            return
        text = (f"将旧版数据合并到当前数据中：\n{legacy}\n\n"
                f"{migration.format_plan(self._plan)}\n\n已有记录不会重复导入。继续吗？")
        if not ask(self, "导入并合并", text, "导入并合并"):
            return
        self._do_import(legacy, "merge")

    def _import_replace(self):
        legacy = self._legacy
        if legacy is None:
            warn(self, "从 LabTime 导入", "没有探测到可读取的旧版数据库。")
            self._refresh_legacy()
            return
        own = " · ".join(f"{migration.TABLE_CN.get(t, t)} {n}"
                         for t, n in (self._plan.get("target_rows") or {}).items() if n)
        own_text = own or "仅设置项，无业务数据"
        if not ask(self, "整体替换",
                   f"将用旧版数据覆盖当前数据：\n\n来源：{legacy}\n\n"
                   f"{migration.format_plan(self._plan)}\n\n"
                   f"新库当前数据：{own_text}\n"
                   "替换前会自动备份当前数据。继续吗？", "继续"):
            return
        if not ask(self, "再次确认：整体替换",
                   f"确定放弃新库当前的「{own_text}」，改用旧库内容吗？\n\n"
                   "可以通过备份恢复当前数据。", "仍然替换"):
            return
        self._do_import(legacy, "replace")

    def _do_import(self, legacy: Path, mode: str):
        self._set_import_enabled(False)
        try:
            report = migration.run_import(self.db, legacy, mode=mode)
        except Exception as exc:  # noqa: BLE001
            warn(self, "导入失败", str(exc))
            self._refresh_legacy()
            return
        self._load_settings()
        self._refresh_legacy()
        get_bus().changed.emit()
        info(self, "导入完成" if mode == "merge" else "替换完成",
             migration.format_report(report))

    # ---------------- 示例数据 ----------------
    def _load_sample(self):
        if seed.has_sample(self.db):
            info(self, "示例数据", "已经存在示例数据。")
            return
        seed.load_sample_data(self.db)
        get_bus().changed.emit()
        info(self, "示例数据", "已载入示例数据。")

    def _clear_sample(self):
        if not seed.has_sample(self.db):
            info(self, "示例数据", "当前没有示例数据。")
            return
        if ask(self, "清除示例数据",
               "确定删除所有示例数据吗？", "清除"):
            seed.clear_sample_data(self.db)
            get_bus().changed.emit()
            info(self, "已清除", "示例数据已清除。")

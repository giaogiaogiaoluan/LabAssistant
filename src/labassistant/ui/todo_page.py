"""Todo / 待办页面：搜索、过滤、增删改、完成切换。"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from labassistant.db import Database
from labassistant.services import schedule as sch
from labassistant.services import todos as tds
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.dialogs import TodoDialog, ask, warn
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant.util import fmt_hm, is_overdue


class TodoPage(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setObjectName("Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(12)

        # ---- 标题行：大标题 + 一行汇总（左）· 新增按钮（右）
        top = QHBoxLayout()
        top.setSpacing(12)
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel("待办")
        title.setObjectName("PageTitle")
        self.summary_lab = QLabel("")
        self.summary_lab.setObjectName("PageSubtitle")
        box.addWidget(title)
        box.addWidget(self.summary_lab)
        top.addLayout(box)
        top.addStretch(1)
        btn_add = QPushButton("＋  新增 Todo")
        btn_add.setObjectName("Primary")
        btn_add.setCursor(Qt.PointingHandCursor)
        top.addWidget(btn_add)
        outer.addLayout(top)

        # ---- 过滤条（一条薄玻璃）
        bar_panel = GlassPanel(variant="thin", radius=T.RADIUS_LG, shadow=False)
        bar = QHBoxLayout(bar_panel)
        bar.setContentsMargins(14, 9, 14, 9)
        bar.setSpacing(12)
        self.search_ed = QLineEdit()
        self.search_ed.setObjectName("Search")
        self.search_ed.setPlaceholderText("搜索事项名称 / 备注…")
        self.search_ed.setClearButtonEnabled(True)
        self.date_filter = QComboBox()
        self.date_filter.addItems(["全部日期", "今天", "明天", "本周", "本月"])
        self.only_open = QCheckBox("只看未完成")
        bar.addWidget(self.search_ed, 1)
        bar.addWidget(self.date_filter)
        bar.addWidget(self.only_open)
        outer.addWidget(bar_panel)
        self.search_ed.textChanged.connect(lambda _t: self.reload())
        self.date_filter.currentIndexChanged.connect(lambda _i: self.reload())
        self.only_open.toggled.connect(lambda _b: self.reload())
        btn_add.clicked.connect(self._add)

        # ---- 清单（一块玻璃）
        panel = GlassPanel(variant="regular", radius=T.RADIUS_XL)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(16, 12, 16, 12)
        pl.setSpacing(9)
        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        head_row.addWidget(SectionHeader("清单", ink=T.ACCENT), 1)
        self.count_lab = QLabel("")
        self.count_lab.setStyleSheet(
            f"color:{T.ACCENT}; background:{T.rgba(T.ACCENT, 0.13)}; border:none;"
            f"border-radius:{T.RADIUS_SM}px; padding:2px 10px;"
            f"font-size:{T.FS_CAPTION}; font-weight:700;")
        head_row.addWidget(self.count_lab, 0, Qt.AlignVCenter)
        pl.addLayout(head_row)
        pl.addWidget(Hairline())

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["完成", "日期", "事项", "优先级", "预计", "截止", "备注", "星期", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        for col, width in ((0, 48), (1, 174), (3, 62), (4, 74), (5, 164), (7, 56), (8, 116)):
            self.table.setColumnWidth(col, width)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._edit_selected())
        pl.addWidget(self.table, 1)

        ops = QHBoxLayout()
        ops.setSpacing(6)
        b_toggle = QPushButton("✓  切换完成状态")
        b_toggle.setCursor(Qt.PointingHandCursor)
        b_toggle.clicked.connect(self._toggle_selected)
        b_edit = QPushButton("编辑所选")
        b_edit.setCursor(Qt.PointingHandCursor)
        b_edit.clicked.connect(self._edit_selected)
        b_del = QPushButton("删除所选")
        b_del.setObjectName("DangerText")
        b_del.setCursor(Qt.PointingHandCursor)
        b_del.clicked.connect(self._delete_selected)
        ops.addWidget(b_toggle)
        ops.addWidget(b_edit)
        ops.addWidget(b_del)
        ops.addStretch(1)
        pl.addLayout(ops)
        outer.addWidget(panel, 1)

        get_bus().changed.connect(self._on_changed)
        self.reload()

    # ---------------- 过滤 ----------------
    def _date_range(self) -> tuple[date | None, date | None]:
        idx = self.date_filter.currentIndex()
        t = date.today()
        if idx == 0:
            return None, None
        if idx == 1:
            return t, t
        if idx == 2:
            return t + timedelta(days=1), t + timedelta(days=1)
        if idx == 3:
            monday = sch.week_start(t)
            return monday, monday + timedelta(days=6)
        first = date(t.year, t.month, 1)
        import calendar
        last = date(t.year, t.month, calendar.monthrange(t.year, t.month)[1])
        return first, last

    def _load_rows(self):
        d0, d1 = self._date_range()
        all_todos = tds.list_todos(self.db, keyword=self.search_ed.text().strip(),
                                   only_open=self.only_open.isChecked())
        rows = []
        for t in all_todos:
            td = date.fromisoformat(t["date"])
            if d0 is not None and (td < d0 or td > d1):
                continue
            rows.append(t)
        rows.sort(key=lambda x: (x["done"], x["date"], {"高": 0, "中": 1, "低": 2}.get(x["priority"], 1)))
        return rows

    def reload(self):
        rows = self._load_rows()
        self.table.setRowCount(len(rows))
        today = date.today()
        for r, t in enumerate(rows):
            td = date.fromisoformat(t["date"])
            # 完成复选框
            chk = QCheckBox()
            chk.setChecked(bool(t["done"]))
            chk.setToolTip("勾选 / 取消勾选 = 完成 / 未完成")
            tid = t["id"]
            chk.toggled.connect(lambda on, _id=tid: (tds.set_done(self.db, _id, on),
                                                     get_bus().changed.emit()))
            self.table.setCellWidget(r, 0, chk)
            done_flag = bool(t["done"])
            day_txt = t["date"]
            if td == today:
                day_txt += "（今天）"
            wd = sch.weekday_cn(td.weekday())
            overdue = is_overdue(t["deadline"], done_flag)
            cells = [day_txt, t["title"], t["priority"] or "中",
                     fmt_hm(t["est_minutes"]) if t.get("est_minutes") else "—",
                     t["deadline"] or "—", t["note"] or "", wd]
            for col_i, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if col_i == 1:
                    it.setData(Qt.UserRole, t["id"])
                if col_i in (0, 2, 3, 4, 6):
                    it.setData(Qt.TextAlignmentRole, int(Qt.AlignCenter))
                fg = T.qcolor(T.MUTED) if done_flag else T.qcolor(T.TEXT)
                if done_flag:
                    font = it.font(); font.setStrikeOut(True); it.setFont(font)
                it.setForeground(fg)
                if col_i == 4 and overdue:
                    it.setForeground(T.qcolor(T.RED))
                    it.setText("⚠ " + (t["deadline"] or ""))
                if col_i == 3 and txt == "高":
                    it.setForeground(T.qcolor(T.RED))
                    it.setFont(QFont(it.font().family(), -1, QFont.Bold))
                self.table.setItem(r, col_i + 1, it)
            # 操作列：每行“编辑 / 删除”（行内小动作，无框图标按钮）
            op_w = QWidget()
            op_lay = QHBoxLayout(op_w)
            op_lay.setContentsMargins(2, 0, 2, 0)
            op_lay.setSpacing(2)
            b_e = QPushButton("编辑")
            b_e.setObjectName("Icon")
            b_d = QPushButton("删除")
            b_d.setObjectName("Icon")
            b_d.setStyleSheet(f"QPushButton {{ color:{T.RED}; }}")
            for b in (b_e, b_d):
                b.setCursor(Qt.PointingHandCursor)
            b_e.clicked.connect(lambda _=False, i=tid: self._edit_by_id(i))
            b_d.clicked.connect(lambda _=False, i=tid: self._del_by_id(i))
            op_lay.addWidget(b_e)
            op_lay.addWidget(b_d)
            op_lay.addStretch(1)
            self.table.setCellWidget(r, 8, op_w)
        # 汇总
        open_n = sum(1 for t in rows if not t["done"])
        est = sum((t["est_minutes"] or 0) for t in rows if not t["done"])
        self.count_lab.setText(f"{len(rows)} 项")
        self.summary_lab.setText(
            f"共 {len(rows)} 项 · 未完成 {open_n} 项 · 未完成预计总时长 {fmt_hm(est)}")

    def _on_changed(self):
        if self.isVisible():
            self.reload()

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 2)
        return item.data(Qt.UserRole) if item else None

    def _add(self):
        dlg = TodoDialog(self.db, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.reload()

    def _edit_selected(self):
        tid = self._selected_id()
        if tid is None:
            warn(self, "提示", "请先在列表中选择一条 Todo。")
            return
        self._edit_by_id(tid)

    def _edit_by_id(self, todo_id: int):
        dlg = TodoDialog(self.db, todo_id=todo_id, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.reload()

    def _toggle_selected(self):
        tid = self._selected_id()
        if tid is None:
            warn(self, "提示", "请先在列表中选择一条 Todo。")
            return
        row = self.table.currentRow()
        chk = self.table.cellWidget(row, 0)
        if isinstance(chk, QCheckBox):
            chk.setChecked(not chk.isChecked())

    def _delete_selected(self):
        tid = self._selected_id()
        if tid is None:
            warn(self, "提示", "请先在列表中选择一条 Todo。")
            return
        self._del_by_id(tid)

    def _del_by_id(self, todo_id: int):
        """删除 Todo（软删除，会同步到其他设备，删除后不会复活）。"""
        if not ask(self, "删除 Todo",
                   "确定删除这个待办事项吗？\n\n此操作会同步到其他设备。", "删除"):
            return
        tds.delete_todo(self.db, todo_id)
        get_bus().changed.emit()
        self.reload()

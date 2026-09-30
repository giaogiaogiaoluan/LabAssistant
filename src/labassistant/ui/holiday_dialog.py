"""节假日管理对话框：时间段添加（推荐）+ 批量添加 + 列表删除（按段）。

例如：起始 2026-10-01、结束 2026-10-07、名称“国庆节”
→ 自动把 10月1日~10月7日 每一天都设为节假日（当天要求时间归 0，实际打卡仍计入）。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from labassistant.db import Database
from labassistant.services import holidays as hds
from labassistant.services import schedule as sch
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.dialogs import ask, info, warn
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader


def _segments(rows: list[dict]) -> list[list[dict]]:
    """把节假日按“日期连续 + 名称相同”分成一段段。"""
    rows = sorted(rows, key=lambda x: x["date"])
    out: list[list[dict]] = []
    cur: list[dict] = []
    for h in rows:
        if cur:
            prev = date.fromisoformat(cur[-1]["date"])
            d = date.fromisoformat(h["date"])
            if h["name"] == cur[-1]["name"] and d == prev + timedelta(days=1):
                cur.append(h)
                continue
            out.append(cur)
        cur = [h]
    if cur:
        out.append(cur)
    return out


class HolidayManagerDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("节假日管理")
        self.resize(700, 660)
        self.setStyleSheet(f"QDialog {{ background: {T.DIALOG_BG}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)

        # ---- 标题 + 说明
        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(SectionHeader("节假日管理", ink=T.PURPLE))
        lay.addLayout(head)

        # ---------------- 时间段添加（推荐） ----------------
        p1 = GlassPanel(variant="strong", radius=T.RADIUS_XL)
        p1_lay = QVBoxLayout(p1)
        p1_lay.setContentsMargins(18, 13, 18, 13)
        p1_lay.setSpacing(10)
        p1_lay.addWidget(SectionHeader("时间段添加", ink=T.PURPLE))
        p1_lay.addWidget(Hairline())

        self.start_ed = QDateEdit()
        self.start_ed.setCalendarPopup(True)
        self.start_ed.setDisplayFormat("yyyy-MM-dd")
        self.end_ed = QDateEdit()
        self.end_ed.setCalendarPopup(True)
        self.end_ed.setDisplayFormat("yyyy-MM-dd")
        t = date.today()
        self.start_ed.setDate(QDate(t.year, t.month, t.day))
        self.end_ed.setDate(QDate(t.year, t.month, t.day))
        self.name_ed = QLineEdit()
        self.name_ed.setPlaceholderText("名称，如：国庆节")
        self.name_ed.setMaximumWidth(240)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        for w in (QLabel("起始"), self.start_ed, QLabel("结束"), self.end_ed):
            add_row.addWidget(w)
        add_row.addSpacing(10)
        add_row.addWidget(self.name_ed, 1)
        b_add = QPushButton("添加该时间段")
        b_add.setObjectName("Primary")
        b_add.setCursor(Qt.PointingHandCursor)
        b_add.clicked.connect(self._add_range)
        add_row.addWidget(b_add)
        p1_lay.addLayout(add_row)
        lay.addWidget(p1)

        # ---------------- 列表（按连续段显示） ----------------
        p2 = GlassPanel(variant="regular", radius=T.RADIUS_XL)
        p2_lay = QVBoxLayout(p2)
        p2_lay.setContentsMargins(18, 13, 18, 13)
        p2_lay.setSpacing(10)
        p2_lay.addWidget(SectionHeader("已录入的节假日", ink=T.INDIGO))
        p2_lay.addWidget(Hairline())
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间段", "名称", "天数", "星期"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(2, 70)
        self.table.setColumnWidth(3, 64)
        p2_lay.addWidget(self.table, 1)
        del_row = QHBoxLayout()
        del_row.setSpacing(6)
        del_btn = QPushButton("删除所选时间段")
        del_btn.setObjectName("DangerText")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self._delete_selected)
        del_row.addWidget(del_btn)
        del_row.addStretch(1)
        p2_lay.addLayout(del_row)
        lay.addWidget(p2, 3)

        # ---------------- 批量粘贴 ----------------
        p3 = GlassPanel(variant="thin", radius=T.RADIUS_XL)
        p3_lay = QVBoxLayout(p3)
        p3_lay.setContentsMargins(18, 13, 18, 13)
        p3_lay.setSpacing(10)
        p3_lay.addWidget(SectionHeader("批量粘贴", ink=T.TEAL))
        p3_lay.addWidget(Hairline())
        self.batch_ed = QTextEdit()
        self.batch_ed.setPlaceholderText("2026-10-01 国庆节\n2026-10-02 国庆节")
        self.batch_ed.setFixedHeight(72)
        p3_lay.addWidget(self.batch_ed)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        b_batch = QPushButton("批量添加")
        b_batch.setObjectName("Primary")
        b_batch.setCursor(Qt.PointingHandCursor)
        b_batch.clicked.connect(self._add_batch)
        row2.addWidget(b_batch)
        row2.addStretch(1)
        p3_lay.addLayout(row2)
        lay.addWidget(p3)

        c_row = QHBoxLayout()
        c_row.addStretch(1)
        close_btn = QPushButton("完成")
        close_btn.setObjectName("Primary")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        c_row.addWidget(close_btn)
        lay.addLayout(c_row)

        self.reload()

    # ---------------- 数据 ----------------
    def reload(self):
        segs = _segments(hds.list_holidays(self.db))
        self.table.setRowCount(len(segs))
        for r, seg in enumerate(segs):
            first = date.fromisoformat(seg[0]["date"])
            last = date.fromisoformat(seg[-1]["date"])
            name = seg[0]["name"] or "（未命名）"
            span = first.isoformat() if first == last else f"{first.isoformat()} ~ {last.isoformat()}"
            cells = [span, name + (f"（{len(seg)} 天）" if len(seg) > 1 else ""),
                     f"{len(seg)} 天", sch.weekday_cn(first.weekday())]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c == 0:
                    it.setData(Qt.UserRole, (first.isoformat(), last.isoformat(), seg[0]["name"]))
                self.table.setItem(r, c, it)

    def _add_range(self):
        s = self._qdate(self.start_ed)
        e = self._qdate(self.end_ed)
        if e < s:
            warn(self, "无法添加", "结束日期不能早于起始日期。")
            return
        name = self.name_ed.text().strip()
        items = []
        cur = s
        while cur <= e:
            items.append((cur.isoformat(), name))
            cur += timedelta(days=1)
        n = hds.add_holidays_batch(self.db, items)
        get_bus().changed.emit()
        self.reload()
        info(self, "添加结果",
             f"已将 {s.isoformat()} ~ {e.isoformat()} 设为节假日：\n"
             f"新增 {n} 天（另有 {len(items) - n} 天原本已存在）。")

    def _add_batch(self):
        text = self.batch_ed.toPlainText().strip()
        if not text:
            return
        items = []
        bad = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            try:
                d = date.fromisoformat(parts[0].strip())
            except ValueError:
                bad.append(line)
                continue
            name = parts[1].strip() if len(parts) > 1 else ""
            items.append((d.isoformat(), name))
        n = hds.add_holidays_batch(self.db, items) if items else 0
        get_bus().changed.emit()
        self.reload()
        msg = f"成功添加 {n} 个节假日。"
        if bad:
            msg += "\n以下行无法解析：\n" + "\n".join(bad)
        info(self, "批量添加", msg)

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            warn(self, "提示", "请先选择一条记录。")
            return
        data = self.table.item(row, 0).data(Qt.UserRole)
        if not data:
            return
        first_s, last_s, name = data
        days = (date.fromisoformat(last_s) - date.fromisoformat(first_s)).days + 1
        span = first_s if first_s == last_s else f"{first_s} ~ {last_s}"
        if not ask(self, "删除节假日",
                   f"确定删除 {span}（共 {days} 天）的节假日设置吗？\n"
                   "删除后这些天恢复为普通工作/周末规则。", "删除"):
            return
        rows = hds.list_holidays(self.db)
        for h in rows:
            if first_s <= h["date"] <= last_s and (h["name"] or "") == (name or ""):
                hds.delete_holiday(self.db, holiday_id=h["id"])
        get_bus().changed.emit()
        self.reload()

    @staticmethod
    def _qdate(ed: QDateEdit) -> date:
        q = ed.date()
        return date(q.year(), q.month(), q.day())

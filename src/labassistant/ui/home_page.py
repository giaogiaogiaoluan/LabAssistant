"""首页：LabOS 月/周日历、方形状态图例与工作时间读数。"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from labassistant import util
from labassistant.constants import WEEKDAYS_CN
from labassistant.db import Database
from labassistant.services import aggregate as agg
from labassistant.services import schedule as sch
from labassistant.services import timing
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.day_dialog import DayDetailDialog
from labassistant.ui.glass import GlassPanel, Hairline, VDivider
from labassistant.ui.widgets import StatBox

WEEKDAY_HEAD = ["一", "二", "三", "四", "五", "六", "日"]


def _elide(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _short_status(key: str) -> str:
    return {
        "done": "完成", "partial": "未完成", "none": "未打卡",
        "weekend": "周末", "holiday": "节假日",
        "weekend_done": "已打卡", "holiday_done": "已打卡",
    }.get(key, "")


# ================================================================== 无框日期格
class DayCell(QFrame):
    """日历里的一天：完全无框，只画 hover 反馈与底部细进度条。"""

    clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._d: date | None = None
        self._hover = False
        self._ratio: float | None = None
        self._ink = T.MUTED
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 6)
        lay.setSpacing(2)

        self.head = QHBoxLayout()
        self.head.setSpacing(5)
        self.day_lab = QLabel("")
        self.badge = QLabel("")          # “今天”的实心圆徽标（仅今天可见）
        self.status_lab = QLabel("")
        self.head.addWidget(self.day_lab)
        self.head.addSpacing(4)
        self.head.addWidget(self.badge)
        self.head.addStretch(1)
        self.head.addWidget(self.status_lab)
        lay.addLayout(self.head)

        self.content = QVBoxLayout()
        self.content.setSpacing(1)
        lay.addLayout(self.content, 1)

        self.foot = QHBoxLayout()
        self.tot_lab = QLabel("")
        self.foot.addWidget(self.tot_lab)
        self.foot.addStretch(1)
        lay.addLayout(self.foot)
        lay.addWidget(Hairline())
        self.tooltip_text = ""

    # ---- hover 反馈：一层极淡的白光，没有边框
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(T.qcolor(T.ACCENT_LIGHT))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -2, -2), 2, 2)
        # 底部细进度条：完成度（无框表达进度的唯一方式）
        if self._ratio is not None and self._ratio > 0:
            w = self.width() - 20
            x, y = 10, self.height() - 4
            p.setPen(Qt.NoPen)
            p.setBrush(T.qcolor(T.HAIRLINE))
            p.drawRoundedRect(QRectF(x, y, w, 2.5), 1.25, 1.25)
            frac = max(0.0, min(1.0, self._ratio))
            p.setBrush(T.qcolor(self._ink))
            p.drawRoundedRect(QRectF(x, y, max(3.0, w * frac), 2.5), 1.25, 1.25)
        p.end()

    # ---- 内容
    def set_day(self, d: date, s: dict, compact: bool):
        self._d = d
        ink = T.status_ink(s["status_key"])
        self._ink = ink
        req = s.get("required_min") or 0
        eff = s.get("effective_min") or 0
        self._ratio = (eff / req) if req else (1.0 if eff else None)
        is_today = bool(s["is_today"])
        future = bool(s.get("future"))

        # 日期数字：今天=蓝底白字方形徽标；周末/节假日=染色；其余=主文本色
        self.badge.hide()
        if is_today:
            self.day_lab.setText("")
            self.badge.setText(f"{d.day}")
            self.badge.show()
            self.badge.setFixedSize(26, 26)
            self.badge.setAlignment(Qt.AlignCenter)
            self.badge.setStyleSheet(
                f"background:{T.ACCENT_SOLID}; color:{T.ON_ACCENT}; border-radius:1px;"
                "font-size:10.5pt; font-weight:700; border:none;")
        else:
            self.day_lab.setText(f"{d.day}")
            weight = "700" if s["kind"] == "workday" else "600"
            col = T.TEXT if s["kind"] == "workday" else ink
            self.day_lab.setStyleSheet(
                f"background:transparent; border:none; color:{col};"
                f"font-size:12pt; font-weight:{weight};")

        self.status_lab.setText("" if future and not s["occ"] and not eff
                                else _short_status(s["status_key"]))
        self.status_lab.setStyleSheet(
            f"background:transparent; border:none; color:{ink};"
            f"font-size:{T.FS_CAPTION}; font-weight:600;")

        self._clear_content()
        fs = T.FS_FOOTNOTE if not compact else "9pt"
        lines: list[tuple[str, str]] = []
        for occ in s["occ"]:
            if occ["state"] == "cancelled":
                continue
            lines.append(("课", f"{_elide(occ['name'], 9)} "
                               f"{timing.min_to_clock(occ['disp_start_min'])}"))
        if s["lab_min"]:
            lines.append(("验", f"实验室 {util.fmt_hours(s['lab_min'])}h"))
        if s["manual_min"]:
            lines.append(("手", f"手动 {util.fmt_hours(s['manual_min'])}h"))
        done_todos = [x for x in s["todos"] if x["done"]]
        open_todos = [x for x in s["todos"] if not x["done"]]
        for t in open_todos[:2]:
            lines.append(("todo", f"□ {_elide(t['title'], 11)}"))
        for t in done_todos[:1]:
            lines.append(("todone", f"■ {_elide(t['title'], 11)}"))
        hidden = max(0, len(open_todos) - 2)
        if hidden:
            lines.append(("todo", f"… 还有 {hidden} 项"))

        max_lines = 4 if compact else 6
        for tag, text in lines[:max_lines]:
            lab = QLabel(text)
            lab.setStyleSheet(self._line_style(tag, fs))
            self.content.addWidget(lab)
        if not lines:
            self.content.addWidget(QLabel(""))

        # 底部：当日有效时长（无框，只有一行小字）
        if eff > 0:
            self.tot_lab.setText(f"{util.fmt_hours(eff, 1)}h"
                                 + (f" / {util.fmt_hours(req, 0)}h" if req else ""))
            self.tot_lab.setStyleSheet(
                f"background:transparent; border:none; color:{ink};"
                f"font-size:{T.FS_FOOTNOTE}; font-weight:700;")
        else:
            self.tot_lab.setText("")

        self.setToolTip(self._tooltip(d, s))
        self.update()

    @staticmethod
    def _line_style(tag: str, fs: str) -> str:
        base = "background:transparent; border:none;"
        if tag == "课":
            return f"{base} color:{T.COURSE_CHIP[1]}; font-size:{fs}; font-weight:600;"
        if tag == "验":
            return f"{base} color:{T.LAB_CHIP[1]}; font-size:{fs};"
        if tag == "手":
            return f"{base} color:{T.MANUAL_CHIP[1]}; font-size:{fs};"
        if tag == "todone":
            return (f"{base} color:{T.MUTED}; font-size:{fs};"
                    "text-decoration:line-through;")
        return f"{base} color:{T.TODO_INK}; font-size:{fs};"

    def _clear_content(self):
        while self.content.count():
            it = self.content.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _tooltip(d: date, s: dict) -> str:
        tip = [f"{d.year}年{d.month}月{d.day}日 {WEEKDAYS_CN[d.weekday()]}",
               f"状态：{s['status_label']}"]
        if s["holiday_name"]:
            tip[0] += f"（{s['holiday_name']}）"
        for occ in s["occ"]:
            st = {"normal": "", "moved": "【已调】", "cancelled": "【已取消】"}[occ["state"]]
            tip.append(f"课程 {st}{occ['name']} "
                       f"{timing.min_to_clock(occ['disp_start_min'])}–"
                       f"{timing.min_to_clock(occ['disp_end_min'])}"
                       f"{'（不计入）' if not occ['count_attendance'] else ''}")
        for b in s["lab_blocks"]:
            tip.append(f"实验室 {timing.min_to_clock(b['start_min'])}–"
                       f"{timing.min_to_clock(b['end_min'])}"
                       f"{'　' + b['note'] if b['note'] else ''}")
        for m in s["manual_items"]:
            tip.append(f"手动 {util.fmt_hours(m['minutes'])}h"
                       f"{'　' + m['note'] if m['note'] else ''}")
        for t in s["todos"]:
            tip.append(f"{'■' if t['done'] else '□'} {t['title']}")
        tip.append(f"有效 {util.fmt_hm(s['effective_min'])} / "
                   f"要求 {util.fmt_hm(s['required_min'])}")
        return "\n".join(tip)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._d is not None:
            d = self._d
            self.clicked.emit(d)
            ev.accept()
            # 点击后对话框会改数据并触发整体刷新，本对象可能已被 deleteLater：
            # 不再调用 super()，避免操作已删除的 C++ 对象。
            return
        super().mouseReleaseEvent(ev)


# ================================================================== 月视图
class MonthGrid(QWidget):
    day_clicked = Signal(object)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.anchor: date = date(date.today().year, date.today().month, 1)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self.sheet = GlassPanel(variant="regular", radius=T.RADIUS_XL)
        sheet_lay = QVBoxLayout(self.sheet)
        sheet_lay.setContentsMargins(14, 10, 14, 12)
        sheet_lay.setSpacing(2)
        self.head_row = QHBoxLayout()
        self.head_row.setSpacing(6)
        sheet_lay.addLayout(self.head_row)
        sheet_lay.addWidget(Hairline())
        self.grid = QGridLayout()
        self.grid.setSpacing(2)
        sheet_lay.addLayout(self.grid)
        vbox.addWidget(self.sheet)
        self._widgets: list[tuple[date | None, QWidget]] = []

    def set_month(self, anchor: date):
        self.anchor = anchor
        self._rebuild()

    def _rebuild(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        while self.head_row.count():
            it = self.head_row.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._widgets = []
        y, m = self.anchor.year, self.anchor.month
        summary = agg.month_summary(self.db, y, m)
        by_day = {s["date"]: s for s in summary["days"]}

        for col, wd in enumerate(WEEKDAY_HEAD):
            lab = QLabel(wd)
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet(
                f"background:transparent; border:none; font-size:{T.FS_FOOTNOTE};"
                f"font-weight:700; color:{T.RED if col >= 5 else T.MUTED};")
            self.head_row.addWidget(lab, 1)

        first = date(y, m, 1)
        import calendar
        n_days = calendar.monthrange(y, m)[1]
        offset = first.weekday()          # 周一开头
        rows = (offset + n_days + 6) // 7
        row = 0
        for idx in range(rows * 7):
            day_num = idx - offset + 1
            cell_date = date(y, m, day_num) if 1 <= day_num <= n_days else None
            r, c = idx // 7, idx % 7
            if cell_date is None:
                holder = QFrame()
                holder.setMinimumHeight(100)
                holder.setStyleSheet("background:transparent; border:none;")
                self.grid.addWidget(holder, r, c)
                self._widgets.append((None, holder))
            else:
                cell = DayCell()
                cell.set_day(cell_date, by_day[cell_date], compact=True)
                cell.clicked.connect(self.day_clicked.emit)
                self.grid.addWidget(cell, r, c)
                self._widgets.append((cell_date, cell))
        for c in range(7):
            self.grid.setColumnStretch(c, 1)
        for r in range(rows):
            self.grid.setRowStretch(r, 1)


# ================================================================== 周视图
class _ClickCol(QFrame):
    """可点击的周视图日列（无框）。"""

    clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.col_date: date | None = None
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(T.qcolor(T.ACCENT_LIGHT))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -3, -3), 2, 2)
        p.end()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.col_date is not None:
            d = self.col_date
            self.clicked.emit(d)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)


class WeekGrid(QWidget):
    day_clicked = Signal(object)

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.anchor: date = sch.week_start(date.today())
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        self.sheet = GlassPanel(variant="regular", radius=T.RADIUS_XL)
        self.sheet_lay = QVBoxLayout(self.sheet)
        self.sheet_lay.setContentsMargins(14, 12, 14, 12)
        self.sheet_lay.setSpacing(0)
        vbox.addWidget(self.sheet)

    def set_week(self, anchor: date):
        self.anchor = sch.week_start(anchor)
        self._rebuild()

    def _rebuild(self):
        while self.sheet_lay.count():
            it = self.sheet_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        wk = agg.week_summary(self.db, self.anchor)
        today = date.today()
        hbox = QHBoxLayout()
        hbox.setSpacing(0)
        for i, s in enumerate(wk["days"]):
            if i:
                hbox.addWidget(VDivider(inset=12))
            hbox.addWidget(self._day_column(s, s["date"] == today), 1)
        self.sheet_lay.addLayout(hbox)

    def _day_column(self, s: dict, is_today: bool) -> QWidget:
        ink = T.status_ink(s["status_key"])
        frame = _ClickCol()
        frame.col_date = s["date"]
        frame.clicked.connect(self.day_clicked.emit)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 8, 14, 10)
        lay.setSpacing(3)

        head = QHBoxLayout()
        num = QLabel(f"{s['date'].day}")
        if is_today:
            num.setFixedSize(26, 26)
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(
                f"background:{T.ACCENT_SOLID}; color:{T.ON_ACCENT}; border-radius:1px;"
                "font-size:10.5pt; font-weight:700; border:none;")
        else:
            num.setStyleSheet(
                f"background:transparent; border:none; color:{T.TEXT if s['kind'] == 'workday' else ink};"
                f"font-size:13pt; font-weight:700;")
        wd = QLabel(WEEKDAYS_CN[s["date"].weekday()])
        wd.setStyleSheet(
            f"background:transparent; border:none; color:{T.MUTED};"
            f"font-size:{T.FS_FOOTNOTE}; font-weight:600;")
        head.addWidget(num)
        head.addSpacing(6)
        head.addWidget(wd)
        head.addStretch(1)
        lay.addLayout(head)

        stat = QLabel("未开始" if s.get("future") else _short_status(s["status_key"]))
        stat.setStyleSheet(
            f"background:transparent; border:none; color:{ink};"
            f"font-size:{T.FS_CAPTION}; font-weight:600;")
        lay.addWidget(stat)
        lay.addWidget(Hairline())

        def add_line(text, style):
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setStyleSheet(f"background:transparent; border:none; {style}")
            lay.addWidget(lab)

        for occ in s["occ"]:
            if occ["state"] == "cancelled":
                continue
            add_line(f"{occ['name']} {timing.min_to_clock(occ['disp_start_min'])}–"
                     f"{timing.min_to_clock(occ['disp_end_min'])}",
                     f"color:{T.COURSE_CHIP[1]}; font-size:9.5pt; font-weight:600;")
            if occ["location"]:
                add_line(occ["location"], f"color:{T.MUTED}; font-size:8.5pt;")
        for b in s["lab_blocks"]:
            txt = f"{timing.min_to_clock(b['start_min'])}–{timing.min_to_clock(b['end_min'])}"
            if b["note"]:
                txt += f" {b['note']}"
            add_line(txt, f"color:{T.LAB_CHIP[1]}; font-size:9.5pt;")
        for m in s["manual_items"]:
            add_line(f"手动 {util.fmt_hours(m['minutes'])}h",
                     f"color:{T.MANUAL_CHIP[1]}; font-size:9.5pt;")
        for t in s["todos"]:
            mark = "■" if t["done"] else "□"
            color = T.MUTED if t["done"] else T.TODO_INK
            deco = "text-decoration:line-through;" if t["done"] else ""
            add_line(f"{mark} {_elide(t['title'], 14)}",
                     f"color:{color}; font-size:9pt; {deco}")
        if s["overlap_min"] > 0:
            add_line(f"重叠已去重 {util.fmt_hm(s['overlap_min'])}",
                     f"color:{T.AMBER}; font-size:8.5pt;")
        lay.addStretch(1)
        if s.get("future"):
            tot, style = "未开始 · 仅安排参考", f"color:{T.MUTED}; font-size:9pt;"
        elif s["effective_min"] > 0:
            tot = (f"{util.fmt_hm(s['effective_min'])}"
                   + (f" / 目标 {util.fmt_hm(s['required_min'])}" if s["required_min"] else ""))
            style = f"color:{ink}; font-size:10.5pt; font-weight:700;"
        elif s["required_min"] > 0:
            tot, style = "无记录", f"color:{T.MUTED}; font-size:9pt;"
        else:
            tot, style = "—", f"color:{T.MUTED}; font-size:9pt;"
        lay.addWidget(Hairline())
        lab = QLabel(tot)
        lab.setStyleSheet(f"background:transparent; border:none; {style}")
        lay.addWidget(lab)

        d = s["date"]
        frame.setToolTip("\n".join(x.strip() for x in
                                   [f"{d.year}年{d.month}月{d.day}日", s["status_label"],
                                    *(f"课程：{o['name']}" for o in s["occ"])]) or str(d))
        return frame


# ================================================================== 首页
class HomePage(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.mode = "month"  # month | week
        self.anchor = date.today()
        self.setObjectName("Root")
        self._build()
        get_bus().changed.connect(self._on_changed)
        self.refresh()

    # ---------- UI ----------
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(14)

        # 标题行：大标题 + 副标题（左），分段控件（右）
        top = QHBoxLayout()
        top.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.title_lab = QLabel("")
        self.title_lab.setObjectName("PageTitle")
        self.subtitle_lab = QLabel("")
        self.subtitle_lab.setObjectName("PageSubtitle")
        title_box.addWidget(self.title_lab)
        title_box.addWidget(self.subtitle_lab)
        top.addLayout(title_box)
        top.addStretch(1)

        self.seg = self._segmented()
        top.addWidget(self.seg)
        nav_box = QHBoxLayout()
        nav_box.setSpacing(6)
        self.btn_prev = QPushButton("‹")
        self.btn_next = QPushButton("›")
        self.btn_today = QPushButton("今天")
        for b in (self.btn_prev, self.btn_next, self.btn_today):
            b.setObjectName("NavBtn")
            b.setCursor(Qt.PointingHandCursor)
        self.btn_prev.setFixedWidth(36)
        self.btn_next.setFixedWidth(36)
        nav_box.addWidget(self.btn_prev)
        nav_box.addWidget(self.btn_next)
        nav_box.addWidget(self.btn_today)
        top.addLayout(nav_box)
        outer.addLayout(top)

        # 指标带（一整块玻璃，内部发丝分隔，无小卡片）
        self.panel_host = QVBoxLayout()
        self.panel_host.setSpacing(0)
        outer.addLayout(self.panel_host)

        # 日历滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.view_host = QWidget()
        self.view_host.setStyleSheet("background:transparent;")
        self.view_host_lay = QVBoxLayout(self.view_host)
        self.view_host_lay.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.view_host)
        outer.addWidget(scroll, 1)

        # 图例：方形色块 + 文字。
        legend = QHBoxLayout()
        legend.setSpacing(16)
        for text, key in [("已完成", "done"), ("未完成", "partial"), ("未打卡", "none"),
                          ("周末", "weekend"), ("节假日", "holiday"),
                          ("周末/节假日已打卡", "weekend_done")]:
            legend.addWidget(self._legend_item(text, T.status_dot(key)))
        legend.addStretch(1)
        outer.addLayout(legend)

        self.btn_prev.clicked.connect(lambda: self._navigate(-1))
        self.btn_next.clicked.connect(lambda: self._navigate(1))
        self.btn_today.clicked.connect(self._go_today)

    @staticmethod
    def _legend_item(text: str, ink: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        d = QLabel()
        d.setFixedSize(9, 9)
        d.setStyleSheet(
            f"background:{ink}; border-radius:0px; border:1px solid {T.BORDER};"
            f"color:{ink}; font-size:1px;")
        lab = QLabel(text)
        lab.setStyleSheet(
            f"background:transparent; border:none; color:{T.TEXT_SECONDARY};"
            f"font-size:{T.FS_FOOTNOTE};")
        lay.addWidget(d)
        lay.addWidget(lab)
        return w

    def _segmented(self) -> QWidget:
        """macOS 风格分段控件：一块玻璃 + 选中项高亮。"""
        box = QFrame()
        box.setStyleSheet(
            "QFrame { background:#DEDCD2; border:1px solid #66665F; border-radius:"
            f"{T.RADIUS_MD}px; }}")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.btn_month = QPushButton("月视图")
        self.btn_week = QPushButton("周视图")
        for b in (self.btn_month, self.btn_week):
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setMinimumWidth(74)
            b.setStyleSheet(self._seg_css(False))
            b.clicked.connect(lambda _=False, m=("month" if b is self.btn_month else "week"):
                              self._set_mode(m))
        lay.addWidget(self.btn_month)
        lay.addWidget(self.btn_week)
        return box

    @staticmethod
    def _seg_css(on: bool) -> str:
        return T.segment_css(T.ACCENT, on)

    # ---------- 数据 ----------
    def _on_changed(self):
        if self.isVisible():
            self.refresh()

    def _set_mode(self, mode: str):
        self.mode = mode
        self.btn_month.setStyleSheet(self._seg_css(mode == "month"))
        self.btn_week.setStyleSheet(self._seg_css(mode == "week"))
        if mode == "week":
            self.anchor = sch.week_start(self.anchor)
        else:
            self.anchor = date(self.anchor.year, self.anchor.month, 1)
        self.refresh()

    def _navigate(self, delta: int):
        if self.mode == "month":
            y, m = self.anchor.year, self.anchor.month
            new_y, new_m = util.add_months(y, m, delta)
            self.anchor = date(new_y, new_m, 1)
        else:
            self.anchor = sch.week_start(self.anchor) + timedelta(weeks=delta)
        self.refresh()

    def _go_today(self):
        t = date.today()
        self.anchor = date(t.year, t.month, 1) if self.mode == "month" else sch.week_start(t)
        self.refresh()

    # ---------- 渲染 ----------
    def refresh(self):
        while self.view_host_lay.count():
            it = self.view_host_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        while self.panel_host.count():
            it = self.panel_host.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

        if self.mode == "month":
            m = agg.month_summary(self.db, self.anchor.year, self.anchor.month)
            self.title_lab.setText(f"{self.anchor.year} 年 {self.anchor.month} 月")
            self.subtitle_lab.setText(
                f"工作日 {m['workday_count']} 天 · "
                f"实验室 {util.fmt_hours(m['lab_min'])}h · "
                f"课程 {util.fmt_hours(m['course_min'])}h · "
                f"手动 {util.fmt_hours(m['manual_min'])}h")
            self._build_month_panel(m)
            grid = MonthGrid(self.db)
            grid.day_clicked.connect(self.open_day)
            grid.set_month(self.anchor)
            self.view_host_lay.addWidget(grid)
        else:
            wk = agg.week_summary(self.db, self.anchor)
            monday = sch.week_start(self.anchor)
            sunday = monday + timedelta(days=6)
            self.title_lab.setText(
                (f"{monday.month}月{monday.day}日 – {sunday.month}月{sunday.day}日"
                 if monday.month != sunday.month else
                 f"{monday.month}月{monday.day}日 – {sunday.day}日"))
            self.subtitle_lab.setText(
                f"本周目标 {util.fmt_hours(wk['required_total'])}h · "
                f"已完成 {util.fmt_hours(wk['effective_total'])}h")
            self._build_week_panel(wk)
            wg = WeekGrid(self.db)
            wg.set_week(self.anchor)
            self.view_host_lay.addWidget(wg)

    # ---------- 顶部指标带 ----------
    def _panel(self) -> tuple[GlassPanel, QHBoxLayout, QVBoxLayout]:
        panel = GlassPanel(variant="strong", radius=T.RADIUS_XL)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        lay.addLayout(row)
        self.panel_host.addWidget(panel)
        return panel, row, lay

    @staticmethod
    def _add_metric(row: QHBoxLayout, box: StatBox, first: bool):
        if not first:
            row.addWidget(VDivider(inset=12))
        row.addWidget(box, 1)

    def _build_month_panel(self, m: dict):
        panel, row, lay = self._panel()
        req, eff = m["required_min"], m["effective_min"]
        rate = eff / req if req else None
        boxes = [
            ("工作日", f"{m['workday_count']}", "天", T.TEXT),
            ("要求时间", util.fmt_hours(req), "小时", T.TEXT),
            ("完成时间", util.fmt_hours(eff), "小时", T.GREEN_INK),
            ("剩余 / 超额", self._diff_text(req - eff),
             "", T.RED if eff < req else T.GREEN_INK),
            ("完成率", util.fmt_percent(eff, req), "",
             T.GREEN_INK if rate is not None and rate >= 1 else T.ACCENT),
            ("平均每日",
             f"{util.fmt_hours(eff / m['workday_count'], 1) if m['workday_count'] else '0'}",
             f"每周平均 {util.fmt_hours(eff / m['weeks_in_month'], 1)}h", T.TEXT),
        ]
        for i, (title, val, sub, color) in enumerate(boxes):
            box = StatBox(title)
            box.set_value(val, color)
            box.set_sub(sub)
            self._add_metric(row, box, i == 0)
        self._add_progress(lay, rate)

    def _build_week_panel(self, wk: dict):
        panel, row, lay = self._panel()
        req, eff = wk["required_total"], wk["effective_total"]
        rate = eff / req if req else None
        boxes = [
            ("本周目标", util.fmt_hours(req), "小时", T.TEXT),
            ("本周完成", util.fmt_hours(eff), "小时", T.GREEN_INK),
            ("剩余 / 超额", self._diff_text(req - eff), "",
             T.RED if eff < req else T.GREEN_INK),
            ("完成率", util.fmt_percent(eff, req), "",
             T.GREEN_INK if rate is not None and rate >= 1 else T.ACCENT),
            ("区间", f"{wk['dates'][0].month}/{wk['dates'][0].day} – "
                    f"{wk['dates'][-1].month}/{wk['dates'][-1].day}", "周一 – 周日", T.TEXT),
        ]
        for i, (title, val, sub, color) in enumerate(boxes):
            box = StatBox(title)
            box.set_value(val, color)
            box.set_sub(sub)
            self._add_metric(row, box, i == 0)
        self._add_progress(lay, rate)

    def _add_progress(self, lay: QVBoxLayout, rate: float | None):
        from PySide6.QtWidgets import QProgressBar

        lay.addSpacing(4)
        lay.addWidget(Hairline())
        row = QHBoxLayout()
        row.setContentsMargins(16, 8, 16, 0)
        row.setSpacing(10)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(min(rate, 1.0) * 100) if rate is not None else 0)
        bar.setTextVisible(False)
        ink = T.GREEN if (rate is not None and rate >= 1) else T.ACCENT
        bar.setStyleSheet(
            f"QProgressBar {{ background:#DEDCD2; border:1px solid {T.BORDER};"
            f" border-radius:{T.RADIUS_SM}px; height:7px; }}"
            f"QProgressBar::chunk {{ border-radius:{T.RADIUS_SM}px; background:{ink}; }}")
        lab = QLabel("进度 " + (f"{rate * 100:.1f}%" if rate is not None else "—"))
        lab.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_FOOTNOTE};"
            f" font-weight:600;")
        row.addWidget(bar, 1)
        row.addWidget(lab)
        lay.addLayout(row)

    @staticmethod
    def _diff_text(diff: int) -> str:
        if diff > 0:
            return f"还差 {util.fmt_hm(diff)}"
        if diff < 0:
            return f"超额 +{util.fmt_hm(-diff)}"
        return "正好达标"

    def open_day(self, d: date):
        dlg = DayDetailDialog(self.db, d, parent=self.window())
        dlg.exec()
        get_bus().changed.emit()

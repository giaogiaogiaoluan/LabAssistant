"""课程表页：大学风格课表视图（自绘时间轴网格） + 课程管理（增删改查 / 例外记录）。"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from labassistant.constants import WEEKDAYS_CN
from labassistant.db import Database
from labassistant.services import courses as crs
from labassistant.services import schedule as sch
from labassistant.services import timing
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.day_dialog import DayDetailDialog, OccurrenceDialog
from labassistant.ui.dialogs import CourseDialog, ask, warn
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant.ui.widgets import make_chip

M = 60
HOUR_START = 7 * 60    # 07:00
HOUR_END = 22 * 60     # 22:00


def _glass_section(title: str, *, ink: str = T.ACCENT, hint: str = "",
                   parent=None) -> tuple[GlassPanel, QVBoxLayout, QLabel]:
    """一块玻璃分区：SectionHeader + 右侧动态计数徽标 + 发丝线 + 内容布局。"""
    panel = GlassPanel(parent, variant="regular", radius=T.RADIUS_XL)
    outer = QVBoxLayout(panel)
    outer.setContentsMargins(18, 14, 18, 14)
    outer.setSpacing(10)
    head_row = QHBoxLayout()
    head_row.setSpacing(10)
    head_row.addWidget(SectionHeader(title, ink=ink, hint=hint), 1)
    count = QLabel("")
    count.setStyleSheet(
        f"color:{T.readable(ink)}; background:{T.rgba(ink, 0.13)}; border:none;"
            f"border-radius:{T.RADIUS_SM}px;"
        f"padding:2px 10px; font-size:{T.FS_CAPTION}; font-weight:700;")
    head_row.addWidget(count, 0, Qt.AlignVCenter)
    outer.addLayout(head_row)
    outer.addWidget(Hairline())
    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(8)
    outer.addLayout(body)
    return panel, body, count


# ------------------------------------------------------------------ 课表视图
class TimetableView(QWidget):
    """自绘每周课表。"""

    day_activated = Signal(object)            # date
    occurrence_activated = Signal(int, object)  # course_id, date

    def __init__(self, parent=None):
        super().__init__(parent)
        self.week_ref: date = sch.week_start(date.today())
        self._rects: list[tuple] = []  # (QRect, meta)
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(420)

    def set_week(self, ref: date):
        self.week_ref = sch.week_start(ref)
        self.update()

    def _dates(self) -> list[date]:
        return sch.week_dates(self.week_ref)

    def paintEvent(self, _e):
        """透明自绘：课表坐在一张玻璃板上，层级只靠发丝线、字重与染色表达。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, top = 54, 40
        day_w = (w - left) / 7.0
        body_h = h - top - 6
        span = HOUR_END - HOUR_START
        scale = body_h / span

        def yof(m): return top + (m - HOUR_START) * scale

        def xof(col): return left + col * day_w

        # 时间轴：整点一条发丝线，半点更淡（不用边框、不用白底）
        hair = T.qcolor(T.HAIRLINE)
        faint = QColor(hair)
        faint.setAlphaF(max(0.0, hair.alphaF() * 0.5))
        axis_font = QFont(self.font())
        axis_font.setPointSizeF(7.5)
        p.setFont(axis_font)
        for m in range(HOUR_START, HOUR_END + 30, 30):
            y = int(yof(m))
            if m % 60 == 0:
                p.setPen(QPen(hair, 1))
                p.drawLine(left, y, w - 8, y)
                p.setPen(T.qcolor(T.MUTED))
                p.drawText(0, y - 7, left - 10, 14, Qt.AlignRight | Qt.AlignVCenter,
                           f"{m // 60:02d}:00")
            else:
                p.setPen(QPen(faint, 1))
                p.drawLine(left, y, w - 8, y)

        # 列分隔线 + 表头（今天用淡蓝染色胶囊标记，不描边）
        dates = self._dates()
        today = date.today()
        for col, d in enumerate(dates):
            x0 = xof(col)
            if col > 0:
                p.setPen(QPen(faint, 1))
                p.drawLine(int(x0), top, int(x0), int(h - 4))
            is_today = d == today
            weekend = d.weekday() >= 5
            if is_today:
                p.setPen(Qt.NoPen)
                p.setBrush(T.qcolor(T.ACCENT_LIGHT))
                p.drawRoundedRect(int(x0) + 2, top + 1, int(day_w) - 4, 32, 2, 2)
            txt1 = WEEKDAYS_CN[d.weekday()]
            txt2 = f"{d.month}/{d.day}"
            p.setPen(T.qcolor(T.TEXT if is_today
                              else (T.MUTED if weekend else T.TEXT_SECONDARY)))
            f1 = QFont(self.font()); f1.setBold(is_today); f1.setPointSizeF(9)
            p.setFont(f1)
            p.drawText(int(x0), top + 1, int(day_w), 18, Qt.AlignCenter, txt1)
            f2 = QFont(self.font()); f2.setPointSizeF(7.5)
            p.setFont(f2)
            p.setPen(T.qcolor(T.TEXT_SECONDARY if is_today else T.MUTED))
            p.drawText(int(x0), top + 16, int(day_w), 16, Qt.AlignCenter, txt2)

        # 课程块：类别染色洗色 + 墨色文字，无描边；hover 只是把洗色加深一点
        self._rects = []
        fonts_small = QFont(self.font()); fonts_small.setPointSizeF(8)
        sub_font = QFont(self.font()); sub_font.setPointSizeF(7)
        mark_font = QFont(self.font()); mark_font.setPointSizeF(6.5); mark_font.setBold(True)
        for col, d in enumerate(dates):
            occs = crs.occurrences_on(self.db, d)
            for occ in occs:
                if occ["state"] == "cancelled":
                    continue
                s0, e0 = occ["disp_start_min"], occ["disp_end_min"]
                if e0 <= s0:
                    e0 = s0 + 1  # 跨天课程在课表里只画首段提示
                y0, y1 = int(yof(max(s0, HOUR_START))), int(yof(min(e0, HOUR_END)))
                if y1 <= y0:
                    y1 = y0 + 14
                rect = (int(xof(col)) + 3, y0 + 1, int(day_w) - 6, max(16, y1 - y0 - 1))
                counted = occ["count_attendance"]
                ink = T.INDIGO if counted else T.MUTED
                hovered = self._hover == len(self._rects)
                fill_a = (0.19 if counted else 0.14) + (0.10 if hovered else 0.0)
                text_c = T.qcolor(T.COURSE_CHIP[1] if counted else T.TEXT_SECONDARY)
                moved = occ["state"] == "moved"
                p.setPen(QPen(T.qcolor(ink), 1))
                p.setBrush(T.qcolor(T.rgba(ink, min(0.95, fill_a))))
                p.drawRoundedRect(*rect, 2, 2)
                if moved:
                    p.setPen(Qt.NoPen)
                    p.setBrush(T.qcolor(T.AMBER))
                    p.drawRect(rect[0] + rect[2] - 9, rect[1] + 2, 7, 7)
                    p.setPen(T.qcolor(T.CARD))
                    p.setFont(mark_font)
                    p.drawText(rect[0] + rect[2] - 9, rect[1], 8, 10, Qt.AlignCenter, "调")
                p.setFont(fonts_small)
                p.save()
                from PySide6.QtCore import QRect
                p.setClipRect(QRect(*rect))
                p.setPen(text_c)
                name = occ["name"]
                while p.fontMetrics().horizontalAdvance(name) > rect[2] - 14 and len(name) > 1:
                    name = name[:-1]
                p.drawText(rect[0] + 7, rect[1] + 3, rect[2] - 14, 16,
                           Qt.AlignLeft | Qt.AlignVCenter, name + ("…" if name != occ["name"] else ""))
                tline = f"{timing.min_to_clock(occ['disp_start_min'])}–{timing.min_to_clock(occ['disp_end_min'])}"
                if occ["location"]:
                    tline += f"  {occ['location']}"
                p.setPen(T.qcolor(T.MUTED))
                p.setFont(sub_font)
                p.drawText(rect[0] + 7, rect[1] + 16, rect[2] - 14, 14,
                           Qt.AlignLeft | Qt.AlignVCenter, tline)
                p.restore()
                self._rects.append((rect, (d, occ["course_id"], occ)))

        p.end()

    # 供外部传入 db
    def bind_db(self, db: Database):
        self.db = db

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            for rect, meta in self._rects:
                rx, ry, rw, rh = rect
                if rx <= ev.position().x() <= rx + rw and ry <= ev.position().y() <= ry + rh:
                    d, course_id, _occ = meta
                    self.occurrence_activated.emit(course_id, d)
                    return
            # 空白区域 -> 按列定位到当天
            left, top = 54, 40
            day_w = (self.width() - left) / 7.0
            if ev.position().x() >= left and ev.position().y() >= top:
                col = int((ev.position().x() - left) // day_w)
                if 0 <= col < 7:
                    self.day_activated.emit(self._dates()[col])
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev):
        x, y = ev.position().x(), ev.position().y()
        idx = -1
        for i, (rect, _m) in enumerate(self._rects):
            rx, ry, rw, rh = rect
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                idx = i
                break
        if idx != self._hover:
            self._hover = idx
            self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()


class TimetableTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.ref_week: date = sch.week_start(date.today())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ---- 周切换条：标题（左）+ 玻璃分段按钮（右）
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.title_lab = QLabel("")
        self.title_lab.setObjectName("SectionTitle")
        bar.addWidget(self.title_lab)
        bar.addStretch(1)
        nav = GlassPanel(variant="thin", radius=T.RADIUS_MD, shadow=False)
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(5, 4, 5, 4)
        nav_lay.setSpacing(3)
        b_prev = QPushButton("‹"); b_next = QPushButton("›")
        b_now = QPushButton("本周")
        for b in (b_prev, b_next, b_now):
            b.setObjectName("NavBtn")
            b.setCursor(Qt.PointingHandCursor)
        b_prev.setFixedWidth(38)
        b_next.setFixedWidth(38)
        b_prev.clicked.connect(lambda: self._go(-1))
        b_next.clicked.connect(lambda: self._go(1))
        b_now.clicked.connect(lambda: (self._set_anchor(date.today())))
        nav_lay.addWidget(b_prev)
        nav_lay.addWidget(b_next)
        nav_lay.addWidget(b_now)
        bar.addWidget(nav)
        lay.addLayout(bar)

        # ---- 课表：一整块玻璃
        sheet = GlassPanel(variant="regular", radius=T.RADIUS_XL)
        sheet_lay = QVBoxLayout(sheet)
        sheet_lay.setContentsMargins(10, 8, 10, 8)
        self.view = TimetableView()
        self.view.bind_db(db)
        self.view.day_activated.connect(self._open_day)
        self.view.occurrence_activated.connect(self._open_occ)
        sheet_lay.addWidget(self.view, 1)
        lay.addWidget(sheet, 1)

        # ---- 图例（玻璃胶囊，无框）
        legend = QHBoxLayout()
        legend.setSpacing(8)
        cap = QLabel("图例")
        cap.setObjectName("Eyebrow")
        legend.addWidget(cap)
        legend.addWidget(make_chip("计入打卡", T.COURSE_CHIP[0], T.COURSE_CHIP[1], True))
        legend.addWidget(make_chip("不计入打卡", T.rgba(T.MUTED, 0.14), T.TEXT_SECONDARY))
        legend.addWidget(make_chip("本次已调整", T.rgba(T.AMBER, 0.16), T.AMBER))
        legend.addStretch(1)
        lay.addLayout(legend)
        self._render_title()

    def _set_anchor(self, ref: date):
        self.ref_week = sch.week_start(ref)
        self._render_title()
        self.view.set_week(self.ref_week)

    def _go(self, delta: int):
        self._set_anchor(self.ref_week + timedelta(weeks=delta))

    def _render_title(self):
        mon = self.ref_week
        sun = mon + timedelta(days=6)
        label = f"第 {mon.isocalendar()[1]} 周　{mon.year}年{mon.month}月{mon.day}日 – "
        if sun.month != mon.month:
            label += f"{sun.month}月{sun.day}日"
        else:
            label += f"{sun.day}日"
        self.title_lab.setText(label)

    def _open_day(self, d: date):
        dlg = DayDetailDialog(self.db, d, parent=self.window())
        dlg.exec()
        get_bus().changed.emit()

    def _open_occ(self, course_id: int, d: date):
        dlg = OccurrenceDialog(self.db, course_id, d, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.refresh_now()

    def refresh_now(self):
        self.view.update()
        self._render_title()


# ------------------------------------------------------------------ 课程管理
class CourseManageTab(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # ---- 工具条
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search_ed = QLineEdit()
        self.search_ed.setObjectName("Search")
        self.search_ed.setPlaceholderText("搜索课程名称 / 地点 / 教师 / 备注 …")
        self.search_ed.setClearButtonEnabled(True)
        self.search_ed.setFixedWidth(320)
        self.search_ed.textChanged.connect(lambda _t: self.reload())
        bar.addWidget(self.search_ed)
        bar.addStretch(1)
        self.course_count = QLabel("")
        self.course_count.setObjectName("Muted")
        bar.addWidget(self.course_count)
        btn_add = QPushButton("＋  新增课程")
        btn_add.setObjectName("Primary")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(self._add)
        bar.addWidget(btn_add)
        lay.addLayout(bar)

        # ---- 课程列表（一块玻璃）
        panel, body, self.course_head = _glass_section("课程列表", ink=T.INDIGO)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["名称", "星期", "时间", "开始日期", "结束日期", "地点", "教师", "计入打卡", "备注"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, width in ((1, 56), (2, 106), (3, 108), (4, 108),
                           (5, 118), (6, 72), (7, 72), (8, 116)):
            self.table.setColumnWidth(col, width)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._edit_selected())
        body.addWidget(self.table, 1)

        row_btn = QHBoxLayout()
        row_btn.setSpacing(6)
        b_edit = QPushButton("编辑所选")
        b_edit.setCursor(Qt.PointingHandCursor)
        b_edit.clicked.connect(self._edit_selected)
        b_del = QPushButton("删除所选")
        b_del.setObjectName("DangerText")
        b_del.setCursor(Qt.PointingHandCursor)
        b_del.clicked.connect(self._delete_selected)
        row_btn.addWidget(b_edit)
        row_btn.addWidget(b_del)
        row_btn.addStretch(1)
        body.addLayout(row_btn)
        lay.addWidget(panel, 3)

        # ---- 单次例外（另一块玻璃）
        panel2, body2, self.exc_head = _glass_section("单次例外记录", ink=T.AMBER)
        self.exc_table = QTableWidget(0, 5)
        self.exc_table.setHorizontalHeaderLabels(["日期", "课程", "类型", "调整后时间", "备注"])
        self.exc_table.verticalHeader().setVisible(False)
        self.exc_table.setAlternatingRowColors(True)
        self.exc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.exc_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.exc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.exc_table.setShowGrid(False)
        h2 = self.exc_table.horizontalHeader()
        h2.setSectionResizeMode(QHeaderView.Interactive)
        h2.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, width in ((0, 112), (2, 88), (3, 124), (4, 140)):
            self.exc_table.setColumnWidth(col, width)
        body2.addWidget(self.exc_table, 1)
        b_restore = QPushButton("恢复所选（删除例外）")
        b_restore.setObjectName("DangerText")
        b_restore.setCursor(Qt.PointingHandCursor)
        b_restore.clicked.connect(self._restore_exception)
        body2.addLayout(self._wrap_right(b_restore))
        lay.addWidget(panel2, 2)
        get_bus().changed.connect(self._on_changed)
        self.reload()

    @staticmethod
    def _wrap_right(w):
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(w)
        return h

    def _on_changed(self):
        if self.isVisible():
            self.reload()

    def _current_course_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(Qt.UserRole)

    def reload(self):
        courses = crs.list_courses(self.db, self.search_ed.text().strip())
        self.table.setRowCount(len(courses))
        for r, c in enumerate(courses):
            cells = [
                c["name"], WEEKDAYS_CN[c["weekday"]],
                f"{timing.min_to_clock(c['start_min'])}–{timing.min_to_clock(c['end_min'])}",
                c["start_date"], c["end_date"], c["location"], c["teacher"],
                "✓" if c["count_attendance"] else "—", c["note"] or "",
            ]
            for col_i, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                if col_i == 0:
                    item.setData(Qt.UserRole, c["id"])
                if col_i == 7:
                    item.setData(Qt.TextAlignmentRole, int(Qt.AlignCenter))
                self.table.setItem(r, col_i, item)
        self.course_head.setText(f"{len(courses)} 门")
        self.reload_exceptions()

    def reload_exceptions(self):
        exes = crs.list_exceptions(self.db)
        self.exc_table.setRowCount(len(exes))
        for r, e in enumerate(exes):
            typ = "已取消" if e["action"] == "cancelled" else "临时调整"
            time_txt = ""
            if e["action"] == "moved" and e["start_min"] is not None:
                time_txt = (f"{timing.min_to_clock(e['start_min'])}–"
                            f"{timing.min_to_clock(e['end_min'] or e['start_min'])}")
            values = [e["date"], e["course_name"], typ, time_txt, e["note"] or ""]
            for col_i, txt in enumerate(values):
                item = QTableWidgetItem(txt)
                if col_i == 0:
                    item.setData(Qt.UserRole, (e["course_id"], e["date"]))
                self.exc_table.setItem(r, col_i, item)
        self.exc_head.setText(f"{len(exes)} 条")

    def _add(self):
        dlg = CourseDialog(self.db, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.reload()

    def _edit_selected(self):
        cid = self._current_course_id()
        if cid is None:
            warn(self, "提示", "请先在列表中选择一门课程。")
            return
        dlg = CourseDialog(self.db, cid, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.reload()

    def _delete_selected(self):
        cid = self._current_course_id()
        if cid is None:
            warn(self, "提示", "请先在列表中选择一门课程。")
            return
        c = crs.get_course(self.db, cid)
        if not c:
            return
        if ask(self, "删除课程",
               f"确定删除课程「{c['name']}」吗？\n所有“单次取消/调整”记录会一并删除。", "删除"):
            crs.delete_course(self.db, cid)
            get_bus().changed.emit()
            self.reload()

    def _restore_exception(self):
        row = self.exc_table.currentRow()
        if row < 0:
            warn(self, "提示", "请先在例外列表中选择一条记录。")
            return
        course_id, ds = self.exc_table.item(row, 0).data(Qt.UserRole)
        crs.remove_exception(self.db, course_id, ds)
        get_bus().changed.emit()
        self.reload_exceptions()


# ------------------------------------------------------------------ 页
class CoursePage(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setObjectName("Root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(12)

        # ---- 标题行：大标题 + 副标题（左）
        top = QVBoxLayout()
        top.setSpacing(0)
        title = QLabel("课程表")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("PageSubtitle")
        top.addWidget(title)
        top.addWidget(self.subtitle)
        outer.addLayout(top)

        tabs = QTabWidget()
        self.tab_time = TimetableTab(db)
        self.tab_manage = CourseManageTab(db)
        tabs.addTab(self.tab_time, "一周课表")
        tabs.addTab(self.tab_manage, "课程管理")
        outer.addWidget(tabs, 1)
        get_bus().changed.connect(self._on_changed)
        self._refresh_subtitle()

    def _refresh_subtitle(self):
        n_course = len(crs.list_courses(self.db, ""))
        n_exc = len(crs.list_exceptions(self.db))
        self.subtitle.setText(
            f"{n_course} 门课程 · {n_exc} 条单次例外 · 课表上的每一块玻璃都可以直接点开调整")

    def _on_changed(self):
        if self.isVisible():
            self.tab_time.refresh_now()
            self.tab_manage.reload()
            self._refresh_subtitle()

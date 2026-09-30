"""每日详情对话框：课程（含单次取消/调整）、打卡、Todo、当日统计。

视觉并入液态玻璃体系：对话框是顶层窗口（没有极光壁纸），底色用 `theme.DIALOG_BG`，
每个分区坐在一块 `GlassPanel` 上，分区内行用极淡的染色与发丝线分层，不再用白卡片 + 灰边框。
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from labassistant.db import Database
from labassistant.services import aggregate as agg
from labassistant.services import attendance as att
from labassistant.services import courses as crs
from labassistant.services import schedule as sch
from labassistant.services import timing
from labassistant.services import todos as tds
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.dialogs import (
    IntervalDialog,
    ManualDialog,
    TodoDialog,
    ask,
    warn,
)
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant.util import fmt_hm, fmt_hours

M = 60


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def _dialog_bg(dlg: QDialog) -> None:
    """顶层窗口没有壁纸 → 用主题给的柔和渐变底。"""
    dlg.setStyleSheet(f"QDialog {{ background: {T.DIALOG_BG}; }}")


def _row_tint(row: QFrame, ink: str) -> QFrame:
    """分区里的一行：极淡染色 + 圆角，不用边框。

    注意：必须用 **带选择器** 的样式（`QFrame#TintRow`），否则裸 `background:` 会
    向下传染给整棵子树，把子控件（如按钮）自己的 QSS 背景全部覆盖掉。
    """
    row.setObjectName("TintRow")
    row.setStyleSheet(
        f"QFrame#TintRow {{ background:{T.rgba(ink, 0.075)}; border:none;"
        f"border-radius:{T.RADIUS_MD}px; }}")
    return row


def _label(text: str, *, ink: str = T.TEXT, size: str = T.FS_BODY,
           bold: bool = False, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setStyleSheet(
        f"color:{ink}; font-size:{size}; background:transparent; border:none;"
        + (" font-weight:600;" if bold else ""))
    return lab


def _icon_btn(text: str, slot, *, danger: bool = False) -> QPushButton:
    """行内小动作按钮（无框，hover 才有底色）。"""
    b = QPushButton(text)
    b.setObjectName("Icon")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    if danger:
        b.setStyleSheet(f"QPushButton {{ color:{T.RED}; }}")
    return b


class _Card(GlassPanel):
    """一块玻璃分区：染色标题 + 发丝线 + 内容。"""

    def __init__(self, title: str = "", *, ink: str = T.ACCENT, parent=None):
        super().__init__(parent, variant="regular", radius=T.RADIUS_XL)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 13, 18, 13)
        self.lay.setSpacing(9)
        if title:
            self.lay.addWidget(SectionHeader(title, ink=ink))
            self.lay.addWidget(Hairline())
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        self.lay.addLayout(self.body)


# ---------------------------------------------------------------- 单次调整对话框
class OccurrenceDialog(QDialog):
    """对某一天的一节课执行：恢复默认 / 取消本次 / 临时调整时间。"""

    def __init__(self, db: Database, course_id: int, d: date, parent=None):
        super().__init__(parent)
        self.db = db
        self.course_id = course_id
        self.d = d
        self.course = crs.get_course(db, course_id)
        self.exc = crs.get_exception(db, course_id, d.isoformat())
        self.setWindowTitle(f"课程单次调整 · {d.year}-{d.month:02d}-{d.day:02d}")
        _dialog_bg(self)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)

        head = QVBoxLayout()
        head.setSpacing(2)
        head.addWidget(SectionHeader("本次课程调整", ink=T.AMBER))
        if self.course:
            head.addWidget(_label(
                f"{self.course['name']}（每周{sch.weekday_cn(self.course['weekday'])} "
                f"{timing.min_to_clock(self.course['start_min'])}–"
                f"{timing.min_to_clock(self.course['end_min'])}）",
                ink=T.TEXT_SECONDARY, size=T.FS_FOOTNOTE, wrap=True))
        lay.addLayout(head)

        panel = GlassPanel(variant="strong", radius=T.RADIUS_XL)
        body = QVBoxLayout(panel)
        body.setContentsMargins(18, 14, 18, 14)
        body.setSpacing(10)
        lay.addWidget(panel)

        self.start_ed = QTimeEdit(); self.start_ed.setDisplayFormat("HH:mm")
        self.end_ed = QTimeEdit(); self.end_ed.setDisplayFormat("HH:mm")
        orig_s = self.course["start_min"] if self.course else 9 * M
        orig_e = self.course["end_min"] if self.course else 11 * M
        if self.exc and self.exc["action"] == "moved":
            orig_s = self.exc["start_min"] or orig_s
            orig_e = self.exc["end_min"] or orig_e
        self.start_ed.setTime(self.start_ed.time().fromString(timing.min_to_clock(orig_s), "HH:mm"))
        self.end_ed.setTime(self.end_ed.time().fromString(timing.min_to_clock(orig_e), "HH:mm"))

        if self.exc:
            state_lab = _label(
                "本次已取消" if self.exc["action"] == "cancelled" else "本次已临时调整",
                ink=T.RED if self.exc["action"] == "cancelled" else T.AMBER, bold=True)
            body.addWidget(state_lab)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.addRow("临时时间", self.start_ed)
        form.addRow("", self.end_ed)
        body.addLayout(form)

        row = QHBoxLayout()
        row.setSpacing(6)
        if self.exc:
            restore = QPushButton("恢复本次为默认")
            restore.setCursor(Qt.PointingHandCursor)
            restore.clicked.connect(self._restore)
            row.addWidget(restore)
        row.addStretch(1)
        apply_btn = QPushButton("应用为本次时间")
        apply_btn.setObjectName("Primary")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.clicked.connect(self._apply_moved)
        cancel_btn = QPushButton("取消本次")
        cancel_btn.setObjectName("DangerText")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._apply_cancel)
        row.addWidget(apply_btn)
        row.addWidget(cancel_btn)
        lay.addLayout(row)

    def _times(self) -> tuple[int, int]:
        s = self.start_ed.time(); e = self.end_ed.time()
        return s.hour() * 60 + s.minute(), e.hour() * 60 + e.minute()

    def _apply_moved(self):
        s, e = self._times()
        if s == e:
            warn(self, "无法保存", "开始与结束时间不能相同。")
            return
        crs.upsert_exception(self.db, self.course_id, self.d.isoformat(),
                             "moved", s, e, "临时调整")
        self.accept()

    def _apply_cancel(self):
        if ask(self, "取消本次", f"确定取消 {self.d} 这次「{self.course['name']}」吗？\n其他周仍正常上课。", "取消本次"):
            crs.upsert_exception(self.db, self.course_id, self.d.isoformat(),
                                 "cancelled", note="临时取消")
            self.accept()

    def _restore(self):
        crs.remove_exception(self.db, self.course_id, self.d.isoformat())
        self.accept()


# ---------------------------------------------------------------- 每日详情
class DayDetailDialog(QDialog):
    def __init__(self, db: Database, d: date, parent=None):
        super().__init__(parent)
        self.db = db
        self.d = d
        self.setWindowTitle("每日详情")
        self.resize(860, 680)
        self.bus = get_bus()
        self._build()
        self._reload()

    # ---------- UI 骨架 ----------
    def _build(self):
        _dialog_bg(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(10)

        head_row = QHBoxLayout()
        head_row.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.head = QLabel()
        self.head.setStyleSheet(
            f"font-size:{T.FS_TITLE1}; font-weight:700; color:{T.TEXT}; background:transparent;")
        titles.addWidget(self.head)
        self.kind_lab = QLabel()
        self.kind_lab.setContentsMargins(0, 2, 0, 0)
        titles.addWidget(self.kind_lab, 0, Qt.AlignLeft)
        head_row.addLayout(titles)
        head_row.addStretch(1)
        root.addLayout(head_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body_w = QWidget()
        self.body = QVBoxLayout(body_w)
        self.body.setContentsMargins(0, 4, 8, 0)
        self.body.setSpacing(12)
        scroll.setWidget(body_w)
        root.addWidget(scroll, 1)

        foot_row = QHBoxLayout()
        foot_row.addStretch(1)
        foot = QDialogButtonBox()
        close = foot.addButton("关闭", QDialogButtonBox.RejectRole)
        close.setObjectName("Primary")
        close.setCursor(Qt.PointingHandCursor)
        foot.rejected.connect(self.reject)
        foot_row.addWidget(foot)
        root.addLayout(foot_row)

    # ---------- 数据刷新 ----------
    def _reload(self):
        s = agg.day_summary(self.db, self.d)
        self._data = s
        d = self.d
        wd = sch.weekday_cn(d.weekday())
        self.head.setText(f"{d.year} 年 {d.month} 月 {d.day} 日 · {wd}")
        kind_txt = {
            "workday": "工作日", "weekend": "周末", "holiday": f"节假日{s['holiday_name'] and ' · ' + s['holiday_name'] or ''}"
        }[s["kind"]]
        ink = T.status_ink(s["status_key"])
        self.kind_lab.setText(f"{kind_txt} ｜ 状态：{s['status_label']}")
        self.kind_lab.setStyleSheet(
            f"background:{T.rgba(ink, 0.13)}; color:{ink}; border:none;"
            f"border-radius:{T.RADIUS_INNER}px; padding:3px 12px; font-size:{T.FS_FOOTNOTE};"
            " font-weight:600;")

        clear_layout(self.body)
        self.body.addWidget(self._build_courses(s))
        self.body.addWidget(self._build_attendance(s))
        self.body.addWidget(self._build_todos(s))
        self.body.addWidget(self._build_stats(s))

    def _refresh(self):
        self.bus.changed.emit()
        self._reload()

    # ---------- 课程区 ----------
    def _build_courses(self, s) -> _Card:
        card = _Card("今日课程", ink=T.INDIGO)
        if not s["occ"]:
            card.body.addWidget(_label("今天没有排课", ink=T.MUTED, wrap=True))
            return card
        for occ in s["occ"]:
            card.body.addWidget(self._occ_row(occ))
        return card

    def _occ_row(self, occ) -> QFrame:
        state = occ["state"]
        ink = (T.RED if state == "cancelled"
               else T.AMBER if state == "moved"
               else T.INDIGO if occ["count_attendance"] else T.MUTED)
        row = _row_tint(QFrame(), ink)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 7, 8, 7)
        lay.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(1)
        t1 = _label(f"{occ['name']}　{timing.min_to_clock(occ['disp_start_min'])}–"
                    f"{timing.min_to_clock(occ['disp_end_min'])}"
                    f"{'（不计入打卡）' if not occ['count_attendance'] else ''}",
                    ink=T.TEXT, bold=True)
        t2 = _label("  ".join(x for x in (occ["location"], occ["teacher"], occ["exc_note"]) if x),
                    size=T.FS_FOOTNOTE)
        if state == "cancelled":
            for l in (t1, t2):
                l.setStyleSheet(l.styleSheet() + f" color:{T.MUTED};")
                f = l.font(); f.setStrikeOut(True); l.setFont(f)
            t2.setText("已取消本次" + (f"（{occ['exc_note']}）" if occ["exc_note"] else ""))
        elif state == "moved":
            t2.setText("本次已临时调整" + (f"（{occ['exc_note']}）" if occ["exc_note"] else ""))
            t2.setStyleSheet(t2.styleSheet() + f" color:{T.AMBER}; font-weight:600;")
        left.addWidget(t1)
        left.addWidget(t2)
        lay.addLayout(left, 1)

        def _act(_checked=False):
            dlg = OccurrenceDialog(self.db, occ["course_id"], self.d, parent=self)
            if dlg.exec():
                self._refresh()

        btn = QPushButton(
            "恢复本次" if state in ("cancelled", "moved") else "取消/调整本次")
        btn.setCursor(Qt.PointingHandCursor)
        if state == "cancelled":
            btn.setObjectName("Primary")
        else:
            btn.setObjectName("Ghost")
        btn.clicked.connect(_act)
        lay.addWidget(btn)
        return row

    # ---------- 打卡区 ----------
    def _build_attendance(self, s) -> _Card:
        card = _Card("实验室打卡", ink=T.GREEN)
        for b in s["lab_blocks"]:
            row = _row_tint(QFrame(), T.GREEN)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 4, 6, 4)
            lay.setSpacing(4)
            text = (f"{timing.min_to_clock(b['start_min'])} – {timing.min_to_clock(b['end_min'])}"
                    f"　（{fmt_hm(b['duration_min'])}）")
            if b.get("note"):
                text += f"　{b['note']}"
            lab = _label(text, ink=T.LAB_CHIP[1], size=T.FS_FOOTNOTE)
            lay.addWidget(lab, 1)
            bid = b["id"]
            lay.addWidget(_icon_btn("编辑", lambda _=False, _id=bid: self._edit_block(_id)))
            lay.addWidget(_icon_btn("删除", lambda _=False, _id=bid: self._del_block(_id),
                                    danger=True))
            card.body.addWidget(row)

        for m in s["manual_items"]:
            row = _row_tint(QFrame(), T.AMBER)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 4, 6, 4)
            lay.setSpacing(4)
            text = f"手动 {fmt_hours(m['minutes'])}h"
            if m.get("note"):
                text += f"　{m['note']}"
            lab = _label(text, ink=T.MANUAL_CHIP[1], size=T.FS_FOOTNOTE)
            lay.addWidget(lab, 1)
            mid = m["id"]
            lay.addWidget(_icon_btn("编辑", lambda _=False, _id=mid: self._edit_manual(_id)))
            lay.addWidget(_icon_btn("删除", lambda _=False, _id=mid: self._del_manual(_id),
                                    danger=True))
            card.body.addWidget(row)

        if not s["lab_blocks"] and not s["manual_items"]:
            card.body.addWidget(_label(
                "今天还没有打卡记录，点击下方按钮添加时间段或直接填总时长。",
                ink=T.MUTED, wrap=True))

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        a1 = QPushButton("＋ 添加时间段")
        a1.setObjectName("Primary")
        a1.setCursor(Qt.PointingHandCursor)
        a2 = QPushButton("＋ 添加手动时长")
        a2.setCursor(Qt.PointingHandCursor)
        a1.clicked.connect(self._add_block)
        a2.clicked.connect(self._add_manual)
        add_row.addWidget(a1)
        add_row.addWidget(a2)
        add_row.addStretch(1)
        card.body.addLayout(add_row)
        return card

    def _add_block(self):
        dlg = IntervalDialog(self.db, self.d, parent=self)
        if dlg.exec():
            self._refresh()

    def _edit_block(self, block_id: int):
        b = next((x for x in self._data["lab_blocks"] if x["id"] == block_id), None)
        if not b:
            return
        dlg = IntervalDialog(self.db, self.d, b, parent=self)
        if dlg.exec():
            self._refresh()

    def _del_block(self, block_id: int):
        if ask(self, "删除打卡记录", "确定删除这条时间段记录吗？", "删除"):
            att.delete_block(self.db, block_id)
            self._refresh()

    def _add_manual(self):
        dlg = ManualDialog(self.db, self.d, parent=self)
        if dlg.exec():
            self._refresh()

    def _edit_manual(self, manual_id: int):
        m = next((x for x in self._data["manual_items"] if x["id"] == manual_id), None)
        if not m:
            return
        dlg = ManualDialog(self.db, self.d, m, parent=self)
        if dlg.exec():
            self._refresh()

    def _del_manual(self, manual_id: int):
        if ask(self, "删除记录", "确定删除这条手动时长记录吗？", "删除"):
            att.delete_manual(self.db, manual_id)
            self._refresh()

    # ---------- Todo 区 ----------
    def _build_todos(self, s) -> _Card:
        card = _Card("今日 Todo", ink=T.TEAL)
        for t in s["todos"]:
            row = _row_tint(QFrame(), T.TEAL if not t["done"] else T.MUTED)
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 3, 6, 3)
            lay.setSpacing(4)
            chk = QCheckBox()
            chk.setChecked(bool(t["done"]))
            chk.setToolTip("点击切换完成状态")
            tid = t["id"]
            chk.toggled.connect(lambda on, _id=tid: (tds.set_done(self.db, _id, on), self._refresh()))
            lay.addWidget(chk)

            txt = t["title"]
            if t["est_minutes"]:
                txt += f"　（预计 {fmt_hm(t['est_minutes'])}）"
            if t["deadline"]:
                txt += f"　截止 {t['deadline']}"
            prio = t.get("priority") or "中"
            lab = QLabel(txt)
            lab.setWordWrap(True)
            style = "background:transparent; border:none;"
            if t["done"]:
                style += f" color:{T.MUTED};"
                f = lab.font(); f.setStrikeOut(True); lab.setFont(f)
            else:
                style += f" color:{T.TEXT};"
            if not t["done"] and prio == "高":
                style += f" font-weight:600;"
            lab.setStyleSheet(style)
            lay.addWidget(lab, 1)

            lay.addWidget(_icon_btn("编辑", lambda _=False, _id=tid: self._edit_todo(_id)))
            lay.addWidget(_icon_btn("删除", lambda _=False, _id=tid: self._del_todo(_id),
                                    danger=True))
            card.body.addWidget(row)

        if not s["todos"]:
            card.body.addWidget(_label("今天没有 Todo。", ink=T.MUTED))
        add = QPushButton("＋ 新增 Todo")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add_todo)
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        add_row.addWidget(add)
        add_row.addStretch(1)
        card.body.addLayout(add_row)
        return card

    def _add_todo(self):
        dlg = TodoDialog(self.db, default_date=self.d, parent=self)
        if dlg.exec():
            self._refresh()

    def _edit_todo(self, todo_id: int):
        dlg = TodoDialog(self.db, todo_id=todo_id, default_date=self.d, parent=self)
        if dlg.exec():
            self._refresh()

    def _del_todo(self, todo_id: int):
        if ask(self, "删除 Todo",
               "确定删除这个待办事项吗？\n\n此操作会同步到其他设备。", "删除"):
            tds.delete_todo(self.db, todo_id)
            self._refresh()

    # ---------- 统计区 ----------
    def _build_stats(self, s) -> _Card:
        card = _Card("当日时间统计", ink=T.ACCENT)
        grid = QHBoxLayout()
        grid.setSpacing(8)

        def _box(title, value, color=T.TEXT, big=False):
            f = QFrame()
            f.setObjectName("StatChip")
            f.setStyleSheet(
                f"QFrame#StatChip {{ background:{T.rgba(color, 0.12 if big else 0.065)};"
                f"border:none; border-radius:{T.RADIUS_MD}px; }}")
            l = QVBoxLayout(f)
            l.setContentsMargins(12, 7, 12, 7)
            l.setSpacing(0)
            t = _label(title, ink=T.MUTED, size=T.FS_CAPTION)
            v = _label(value, ink=color, size=T.FS_TITLE2 if big else T.FS_HEADLINE,
                       bold=True)
            l.addWidget(t)
            l.addWidget(v)
            return f

        eff_color = (T.GREEN_INK if s["status_key"] == "done"
                     else T.MANUAL_CHIP[1]
                     if s["kind"] == "workday" and s["effective_min"] > 0 else T.TEXT)
        grid.addWidget(_box("有效总时间", fmt_hm(s["effective_min"]), eff_color, big=True))
        grid.addWidget(_box("今日目标", fmt_hm(s["required_min"]) if s["kind"] == "workday" else "0h"))
        diff = s["required_min"] - s["effective_min"]
        if s["kind"] == "workday":
            if diff > 0:
                grid.addWidget(_box("还差", fmt_hm(diff), T.RED))
            else:
                grid.addWidget(_box("超额", "+" + fmt_hm(-diff), T.GREEN_INK))
        else:
            grid.addWidget(_box("实际时间", fmt_hm(s["effective_min"]), T.GREEN_INK))
        grid.addWidget(_box("实验室(去重后)", fmt_hm(s["lab_min"])))
        grid.addWidget(_box("课程(去重后)", fmt_hm(s["course_min"])))
        if s["manual_min"]:
            grid.addWidget(_box("手动时长", fmt_hm(s["manual_min"])))
        card.body.addLayout(grid)

        return card

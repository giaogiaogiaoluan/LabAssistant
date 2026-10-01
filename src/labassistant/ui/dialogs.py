"""通用提示框 + 各实体编辑器对话框（时间区间、手动时长、课程、Todo）。

视觉并入液态玻璃体系：
- 对话框是顶层窗口（没有极光壁纸），统一用 `theme.DIALOG_BG` 作窗口底色；
- 表单内容坐在**一块玻璃板**里，标题用 `SectionHeader`，不再出现白卡片 + 灰边框；
- 主要动作 `Primary`、次要动作默认、危险动作 `DangerText`。
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from labassistant.db import Database
from labassistant.services import attendance as att
from labassistant.services import courses as crs
from labassistant.services import timing
from labassistant.services import todos as tds
from labassistant.ui import theme as T
from labassistant.ui.date_picker import configure_date_picker
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant.util import fmt_hm

M = 60


# ---------------- 通用外壳 ----------------

def _box(parent, title: str, text: str, icon, buttons: list[tuple[str, int, str]]):
    """一个套了液态玻璃底的消息框（顶层窗口没有壁纸 → 用 DIALOG_BG）。

    按钮的 objectName 必须在 `addButton` **之前**设好：QMessageBox 一被构建就会
    polish 子控件，之后再改 objectName 不会重新匹配全局 QSS。
    """
    box = QMessageBox(icon, title, text, parent=parent)
    box.setStyleSheet(
        f"QMessageBox {{ background: {T.DIALOG_BG}; }}"
        f"QLabel {{ color:{T.TEXT}; font-size:{T.FS_BODY}; }}"
        f"QPushButton {{ min-width: 74px; }}")
    created = []
    for label, role, obj in buttons:
        btn = QPushButton(label)
        if obj:
            btn.setObjectName(obj)
        btn.setCursor(Qt.PointingHandCursor)
        box.addButton(btn, role)
        created.append(btn)
    return box, created


def ask(parent: QWidget, title: str, text: str, ok_text: str = "确定") -> bool:
    box, created = _box(parent, title, text, QMessageBox.Question,
                        [(ok_text, QMessageBox.AcceptRole, "Primary"),
                         ("取消", QMessageBox.RejectRole, "")])
    box.exec()
    return box.clickedButton() is created[0]


def warn(parent: QWidget, title: str, text: str):
    box, _ = _box(parent, title, text, QMessageBox.Warning,
                  [("知道了", QMessageBox.AcceptRole, "Primary")])
    box.exec()


def info(parent: QWidget, title: str, text: str):
    box, _ = _box(parent, title, text, QMessageBox.Information,
                  [("好", QMessageBox.AcceptRole, "Primary")])
    box.exec()


def _dialog_shell(dlg: QDialog, title: str, *, ink: str = T.ACCENT,
                  subtitle: str = "", min_w: int = 420) -> tuple[QVBoxLayout, QVBoxLayout]:
    """对话框统一骨架：柔和底 + 分区标题 + 一块玻璃。返回 (外布局, 玻璃内容布局)。"""
    dlg.setStyleSheet(f"QDialog {{ background: {T.DIALOG_BG}; }}")
    dlg.setMinimumWidth(min_w)
    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(20, 16, 20, 16)
    outer.setSpacing(12)
    head = QVBoxLayout()
    head.setSpacing(2)
    head.addWidget(SectionHeader(title, ink=ink))
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("PageSubtitle")
        sub.setWordWrap(True)
        head.addWidget(sub)
    outer.addLayout(head)
    panel = GlassPanel(variant="strong", radius=T.RADIUS_XL)
    body = QVBoxLayout(panel)
    body.setContentsMargins(18, 14, 18, 14)
    body.setSpacing(10)
    outer.addWidget(panel, 1)
    return outer, body


def _form(host: QVBoxLayout, *, spacing: int = 9) -> QFormLayout:
    """统一的表单排版（标签右列对齐、字段撑满）。"""
    form = QFormLayout()
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(spacing)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    host.addLayout(form)
    return form


def _muted(text: str, *, ink: str | None = None) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(
        f"color:{ink or T.MUTED}; font-size:{T.FS_FOOTNOTE}; background:transparent;")
    return lab


def _buttons(dlg: QDialog, host: QVBoxLayout, accept_text: str, on_accept,
             extra: list[tuple[str, object, str]] | None = None) -> QDialogButtonBox:
    """右下角按钮组：附加动作（左）· 取消 · 主要动作（Primary）。"""
    row = QHBoxLayout()
    row.setSpacing(6)
    for text, slot, obj in (extra or []):
        b = QPushButton(text)
        if obj:
            b.setObjectName(obj)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(slot)
        row.addWidget(b)
    row.addStretch(1)
    btns = QDialogButtonBox()
    ok = btns.addButton(accept_text, QDialogButtonBox.AcceptRole)
    ok.setObjectName("Primary")
    ok.setCursor(Qt.PointingHandCursor)
    btns.addButton("取消", QDialogButtonBox.RejectRole)
    btns.accepted.connect(on_accept)
    btns.rejected.connect(dlg.reject)
    row.addWidget(btns)
    host.addLayout(row)
    return btns


# ---------------- 打卡：时间段 ----------------

class IntervalDialog(QDialog):
    def __init__(self, db: Database, date_obj: date, block: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.date_obj = date_obj
        self.block = block
        self.setWindowTitle("编辑打卡时间段" if block else "添加打卡时间段")
        lay, body = _dialog_shell(
            self, "实验室打卡时间段", ink=T.GREEN,
            subtitle=f"{date_obj.year} 年 {date_obj.month} 月 {date_obj.day} 日 · "
                     "填写实际在实验室的起止时间",
            min_w=400)

        form = _form(body)
        self.start_ed = QTimeEdit()
        self.start_ed.setDisplayFormat("HH:mm")
        self.end_ed = QTimeEdit()
        self.end_ed.setDisplayFormat("HH:mm")
        self.note_ed = QLineEdit()
        self.note_ed.setPlaceholderText("备注（可选），如：上午在实验室")
        form.addRow("开始时间", self.start_ed)
        form.addRow("结束时间", self.end_ed)
        form.addRow("备注", self.note_ed)

        body.addWidget(Hairline())
        self.preview = _muted("")
        body.addWidget(self.preview)

        for w in (self.start_ed, self.end_ed):
            w.timeChanged.connect(self._refresh_preview)

        _buttons(self, lay, "保存", self._on_accept)

        if block:
            self.start_ed.setTime(self.start_ed.time().fromString(
                timing.min_to_clock(block["start_min"]), "HH:mm"))
            self.end_ed.setTime(self.end_ed.time().fromString(
                timing.min_to_clock(block["end_min"]), "HH:mm"))
            self.note_ed.setText(block.get("note", ""))
        else:
            self.start_ed.setTime(self.start_ed.time().fromString("08:00", "HH:mm"))
            self.end_ed.setTime(self.end_ed.time().fromString("12:00", "HH:mm"))
        self._refresh_preview()

    def _time_min(self, ed: QTimeEdit) -> int:
        t = ed.time()
        return t.hour() * 60 + t.minute()

    def _set_preview(self, text: str, ink: str):
        self.preview.setText(text)
        self.preview.setStyleSheet(
            f"color:{ink}; font-size:{T.FS_FOOTNOTE}; background:transparent;")

    def _refresh_preview(self):
        s, e = self._time_min(self.start_ed), self._time_min(self.end_ed)
        if s == e:
            self._set_preview("开始与结束时间不能相同", T.RED)
            return
        dur = timing.raw_duration(s, e)
        tip = ""
        if e <= s:
            tip = "　（跨午夜，按开始日期计入，共 "
        self._set_preview(f"时长：{fmt_hm(dur)}" + (tip if tip else ""), T.MUTED)

    def _on_accept(self):
        s, e = self._time_min(self.start_ed), self._time_min(self.end_ed)
        if s == e:
            warn(self, "无法保存", "开始与结束时间不能相同。")
            return
        note = self.note_ed.text().strip()
        if self.block:
            att.update_block(self.db, self.block["id"], s, e, note)
        else:
            att.add_block(self.db, self.date_obj.isoformat(), s, e, note)
        self.accept()


# ---------------- 打卡：手动时长 ----------------

class ManualDialog(QDialog):
    def __init__(self, db: Database, date_obj: date, item: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.date_obj = date_obj
        self.item = item
        self.setWindowTitle("编辑手动时长" if item else "添加手动时长")
        lay, body = _dialog_shell(
            self, "手动时长", ink=T.AMBER,
            subtitle=f"{date_obj.year} 年 {date_obj.month} 月 {date_obj.day} 日 · "
                     "不计具体时段，直接计入当天",
            min_w=380)

        form = _form(body)
        self.hours_ed = QDoubleSpinBox()
        self.hours_ed.setRange(0.01, 24)
        self.hours_ed.setSingleStep(0.01)
        self.hours_ed.setDecimals(2)
        self.hours_ed.setSuffix(" 小时")
        self.note_ed = QLineEdit()
        self.note_ed.setPlaceholderText("备注（可选），如：晚上在家看文献 1.5h")
        form.addRow("时长", self.hours_ed)
        form.addRow("备注", self.note_ed)
        if item:
            self.hours_ed.setValue(item["minutes"] / 60.0)
            self.note_ed.setText(item.get("note", ""))
        else:
            self.hours_ed.setValue(1.0)

        _buttons(self, lay, "保存", self._on_accept)

    def _on_accept(self):
        minutes = round(self.hours_ed.value() * 60, 2)
        if minutes <= 0:
            warn(self, "无法保存", "时长必须大于 0。")
            return
        if self.item:
            att.update_manual(self.db, self.item["id"], minutes, self.note_ed.text().strip())
        else:
            att.add_manual(self.db, self.date_obj.isoformat(), minutes,
                           self.note_ed.text().strip())
        self.accept()


# ---------------- 课程 ----------------

class CourseDialog(QDialog):
    def __init__(self, db: Database, course_id: int | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.course = crs.get_course(db, course_id) if course_id else None
        self.setWindowTitle("编辑课程" if self.course else "新增课程")
        lay, body = _dialog_shell(
            self, "编辑课程" if self.course else "新增课程", ink=T.INDIGO,
            min_w=460)

        form = _form(body)
        self.name_ed = QLineEdit()
        self.name_ed.setPlaceholderText("如：系统数理基础")
        self.week_combo = QComboBox()
        from labassistant.constants import WEEKDAYS_CN
        for i, wd in enumerate(WEEKDAYS_CN):
            self.week_combo.addItem(wd, i)
        self.start_ed = QTimeEdit(); self.start_ed.setDisplayFormat("HH:mm")
        self.end_ed = QTimeEdit(); self.end_ed.setDisplayFormat("HH:mm")
        today = date.today()
        self.start_date_ed = QDateEdit()
        self.end_date_ed = QDateEdit()
        for de in (self.start_date_ed, self.end_date_ed):
            configure_date_picker(de)
            de.setDisplayFormat("yyyy-MM-dd")
        self.start_date_ed.setDate(QDate(today.year, today.month, today.day))
        self.end_date_ed.setDate(QDate(today.year, today.month, today.day) .addMonths(4))
        self.location_ed = QLineEdit(); self.location_ed.setPlaceholderText("地点，如：教学楼 A")
        self.teacher_ed = QLineEdit(); self.teacher_ed.setPlaceholderText("教师，如：张老师")
        self.note_ed = QLineEdit(); self.note_ed.setPlaceholderText("备注（可选）")
        self.count_chk = QCheckBox("计入打卡时间")
        self.count_chk.setChecked(db.course_counts_default())

        form.addRow("课程名称 *", self.name_ed)
        form.addRow("星期", self.week_combo)
        form.addRow("开始时间", self.start_ed)
        form.addRow("结束时间", self.end_ed)
        form.addRow("开始日期 *", self.start_date_ed)
        form.addRow("结束日期 *", self.end_date_ed)
        form.addRow("地点", self.location_ed)
        form.addRow("教师", self.teacher_ed)
        form.addRow("备注", self.note_ed)
        body.addWidget(self.count_chk)

        body.addWidget(Hairline())
        self.preview = _muted("")
        body.addWidget(self.preview)
        for ed in (self.start_ed, self.end_ed):
            ed.timeChanged.connect(self._refresh_preview)

        _buttons(self, lay, "保存", self._on_save,
                 extra=[("删除课程", self._on_delete, "DangerText")] if self.course else None)

        self._fill_from_course()
        self._refresh_preview()

    def _fill_from_course(self):
        if not self.course:
            self.start_ed.setTime(self.start_ed.time().fromString("09:50", "HH:mm"))
            self.end_ed.setTime(self.end_ed.time().fromString("11:25", "HH:mm"))
            return
        c = self.course
        self.name_ed.setText(c["name"])
        self.week_combo.setCurrentIndex(c["weekday"] % 7)
        self.start_ed.setTime(self.start_ed.time().fromString(
            timing.min_to_clock(c["start_min"]), "HH:mm"))
        self.end_ed.setTime(self.end_ed.time().fromString(
            timing.min_to_clock(c["end_min"]), "HH:mm"))
        d0 = date.fromisoformat(c["start_date"]); d1 = date.fromisoformat(c["end_date"])
        self.start_date_ed.setDate(QDate(d0.year, d0.month, d0.day))
        self.end_date_ed.setDate(QDate(d1.year, d1.month, d1.day))
        self.location_ed.setText(c["location"])
        self.teacher_ed.setText(c["teacher"])
        self.note_ed.setText(c["note"])
        self.count_chk.setChecked(bool(c["count_attendance"]))

    def _time_min(self, ed: QTimeEdit) -> int:
        t = ed.time()
        return t.hour() * 60 + t.minute()

    def _to_date(self, ed: QDateEdit) -> date:
        q = ed.date()
        return date(q.year(), q.month(), q.day())

    def _set_preview(self, text: str, ink: str):
        self.preview.setText(text)
        self.preview.setStyleSheet(
            f"color:{ink}; font-size:{T.FS_FOOTNOTE}; background:transparent;")

    def _refresh_preview(self):
        s, e = self._time_min(self.start_ed), self._time_min(self.end_ed)
        if s == e:
            self._set_preview("开始与结束时间不能相同", T.RED)
            return
        dur = timing.raw_duration(s, e)
        txt = f"每周时长：{fmt_hm(dur)}"
        if e <= s:
            txt += "（跨午夜，每周日按跨天处理）"
        self._set_preview(txt, T.MUTED)

    def _on_delete(self):
        if not ask(self, "删除课程", f"确定删除课程「{self.course['name']}」吗？\n其所有“单次取消/调整”记录也会一并删除。", "删除"):
            return
        crs.delete_course(self.db, self.course["id"])
        self.accept()
        self.deleted = True

    def _on_save(self):
        name = self.name_ed.text().strip()
        if not name:
            warn(self, "无法保存", "请填写课程名称。")
            return
        s, e = self._time_min(self.start_ed), self._time_min(self.end_ed)
        if s == e:
            warn(self, "无法保存", "开始与结束时间不能相同。")
            return
        d0, d1 = self._to_date(self.start_date_ed), self._to_date(self.end_date_ed)
        if d1 < d0:
            warn(self, "无法保存", "结束日期不能早于开始日期。")
            return
        counted = self.count_chk.isChecked()
        if self.course:
            crs.update_course(self.db, self.course["id"], name,
                              self.week_combo.currentData(), s, e, d0, d1,
                              self.location_ed.text().strip(),
                              self.teacher_ed.text().strip(),
                              self.note_ed.text().strip(), counted)
        else:
            crs.add_course(self.db, name, self.week_combo.currentData(), s, e, d0, d1,
                           self.location_ed.text().strip(),
                           self.teacher_ed.text().strip(),
                           self.note_ed.text().strip(), counted)
        self.accept()


# ---------------- Todo ----------------

class TodoDialog(QDialog):
    def __init__(self, db: Database, todo_id: int | None = None,
                 default_date: date | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.todo = tds.get_todo(db, todo_id) if todo_id else None
        self.setWindowTitle("编辑 Todo" if self.todo else "新增 Todo")
        lay, body = _dialog_shell(
            self, "编辑 Todo" if self.todo else "新增 Todo", ink=T.TEAL,
            subtitle="勾选“设置截止时间”后，逾期未完成的事项会在待办页标红。",
            min_w=460)

        form = _form(body)
        self.date_ed = QDateEdit(); configure_date_picker(self.date_ed)
        self.date_ed.setDisplayFormat("yyyy-MM-dd")
        d = date.today() if default_date is None else default_date
        self.date_ed.setDate(QDate(d.year, d.month, d.day))
        self.title_ed = QLineEdit(); self.title_ed.setPlaceholderText("事项名称，如：阅读蛋白酶切位点预测论文")
        self.prio_combo = QComboBox()
        for p in ("高", "中", "低"):
            self.prio_combo.addItem(p)
        self.est_ed = QDoubleSpinBox()
        self.est_ed.setRange(0, 24); self.est_ed.setSingleStep(0.5)
        self.est_ed.setDecimals(2); self.est_ed.setSuffix(" 小时")
        self.est_ed.setSpecialValueText("不填")
        self.est_ed.setValue(0)
        self.deadline_chk = QCheckBox("设置截止时间")
        self.deadline_ed = QDateTimeEdit()
        self.deadline_ed.setDisplayFormat("yyyy-MM-dd HH:mm")
        configure_date_picker(self.deadline_ed)
        from datetime import datetime
        now = datetime.now()
        self.deadline_ed.setDateTime(self.deadline_ed.dateTime().fromString(
            f"{now.strftime('%Y-%m-%d %H:%M')}", "yyyy-MM-dd HH:mm"))
        self.deadline_ed.setEnabled(False)
        self.deadline_chk.toggled.connect(self.deadline_ed.setEnabled)
        self.done_chk = QCheckBox("已完成")
        self.note_ed = QTextEdit(); self.note_ed.setFixedHeight(64)
        self.note_ed.setPlaceholderText("备注（可选）")

        form.addRow("日期 *", self.date_ed)
        form.addRow("事项 *", self.title_ed)
        form.addRow("优先级", self.prio_combo)
        form.addRow("预计时间", self.est_ed)
        form.addRow("截止时间", self.deadline_chk)
        form.addRow("", self.deadline_ed)
        form.addRow("状态", self.done_chk)
        form.addRow("备注", self.note_ed)

        _buttons(self, lay, "保存", self._on_save)

        if self.todo:
            t = self.todo
            q = self.date_ed.date().fromString(t["date"], "yyyy-MM-dd")
            self.date_ed.setDate(q)
            self.title_ed.setText(t["title"])
            self.prio_combo.setCurrentText(t["priority"] or "中")
            est = t["est_minutes"] or 0
            if est > 0:
                self.est_ed.setValue(est / 60.0)
            else:
                self.est_ed.setValue(0)
            if t["deadline"]:
                self.deadline_chk.setChecked(True)
                dt = self.deadline_ed.dateTime().fromString(t["deadline"], "yyyy-MM-dd HH:mm")
                if dt.isValid():
                    self.deadline_ed.setDateTime(dt)
            self.done_chk.setChecked(bool(t["done"]))
            self.note_ed.setPlainText(t["note"] or "")

    def _on_save(self):
        title = self.title_ed.text().strip()
        if not title:
            warn(self, "无法保存", "请填写事项名称。")
            return
        q = self.date_ed.date()
        ds = date(q.year(), q.month(), q.day()).isoformat()
        est = int(round(self.est_ed.value() * 60)) if self.est_ed.value() > 0 else None
        deadline = ""
        if self.deadline_chk.isChecked():
            dt = self.deadline_ed.dateTime()
            deadline = dt.toString("yyyy-MM-dd HH:mm")
        if self.todo:
            tds.update_todo(self.db, self.todo["id"], ds, title, self.done_chk.isChecked(),
                            est, self.prio_combo.currentText(), deadline,
                            self.note_ed.toPlainText().strip())
        else:
            tds.add_todo(self.db, ds, title, self.done_chk.isChecked(), est,
                         self.prio_combo.currentText(), deadline,
                         self.note_ed.toPlainText().strip())
        self.accept()

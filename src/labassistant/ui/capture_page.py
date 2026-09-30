"""随手记页面：一句话自动落到对应业务表 + 加密密码本。

两个分段：
    随手记 —— 输入框 + 实时识别预览 + 按天分组的时间线（可撤销）
    密码本 —— AES-256-GCM 加密的账号密码，默认本机密钥免打扰，可选主密码加锁
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from labassistant.db import Database
from labassistant.services import capture as cap
from labassistant.services import vault
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.dialogs import ask
from labassistant.ui.glass import GlassPanel, Hairline, VDivider

EXAMPLES = [
    "明天记得交实验报告，预计2小时",
    "9/25 14:00-17:00 实验室",
    "收藏 https://arxiv.org/list/cs.CV/recent",
    "示例网站 账号 user@example.com 密码 Example123",
]


class _InputBox(QPlainTextEdit):
    """支持 Ctrl/Cmd+Enter 直接记录。"""

    submit = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and (
                e.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            self.submit.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class CapturePage(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setObjectName("Root")
        self._tab = "capture"
        self._revealed: set[int] = set()
        self._build()
        get_bus().changed.connect(self._on_changed)
        self.refresh()

    # ---------------------------------------------------------------- 结构
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(12)
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel("随手记")
        title.setObjectName("PageTitle")
        self.page_title = title
        self.subtitle = QLabel("一句话记下，按规则自动落到待办 / 打卡 / 网址 / 密码本")
        self.subtitle.setObjectName("PageSubtitle")
        box.addWidget(title)
        box.addWidget(self.subtitle)
        top.addLayout(box)
        top.addStretch(1)
        top.addWidget(self._segmented())
        outer.addLayout(top)

        self.stack_host = QVBoxLayout()
        self.stack_host.setSpacing(12)
        outer.addLayout(self.stack_host, 1)
        self._render_tab()

    def _segmented(self) -> QWidget:
        holder = QFrame()
        holder.setStyleSheet(
            "QFrame { background:#DEDCD2; border:1px solid #66665F; border-radius:"
            f"{T.RADIUS_MD}px; }}")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.btn_capture = QPushButton("随手记")
        self.btn_vault = QPushButton("密码本")
        for b, key in ((self.btn_capture, "capture"), (self.btn_vault, "vault")):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(86)
            b.setStyleSheet(self._seg_css(key == self._tab))
            b.clicked.connect(lambda _=False, k=key: self._set_tab(k))
        lay.addWidget(self.btn_capture)
        lay.addWidget(self.btn_vault)
        return holder

    @staticmethod
    def _seg_css(on: bool) -> str:
        return T.segment_css(T.ACCENT, on)

    def _set_tab(self, key: str):
        self._tab = key
        if hasattr(self, "page_title"):
            self.page_title.setText("随手记" if key == "capture" else "密码本")
            self.subtitle.setText(
                "一句话记下，按规则自动落到待办 / 打卡 / 网址 / 密码本" if key == "capture"
                else "账号密码用 AES-256-GCM 加密保存在本机，不写入同步队列")
        self.btn_capture.setStyleSheet(self._seg_css(key == "capture"))
        self.btn_vault.setStyleSheet(self._seg_css(key == "vault"))
        self._render_tab()

    def _clear_host(self):
        while self.stack_host.count():
            it = self.stack_host.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.hide()
                w.deleteLater()

    def _render_tab(self):
        self._clear_host()
        if self._tab == "capture":
            self._render_capture()
        else:
            self._render_vault()

    # ---------------------------------------------------------------- 随手记
    def _render_capture(self):
        sheet = GlassPanel(variant="regular", radius=T.RADIUS_XL)
        lay = QVBoxLayout(sheet)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        self.input = _InputBox()
        self.input.setObjectName("CaptureInput")
        self.input.setPlaceholderText(
            "想到什么就写什么，例如：「明天记得交实验报告，预计2小时」\n"
            "「9/25 14:00-17:00 实验室」「华为云 账号 xx@yy.com 密码 Abc@1234」")
        self.input.setFixedHeight(92)
        self.input.textChanged.connect(self._preview)
        self.input.submit.connect(self._submit)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.kind_badge = QLabel("")
        self.preview_lab = QLabel("输入内容后，这里会实时显示识别结果")
        self.preview_lab.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_FOOTNOTE};")
        row.addWidget(self.kind_badge)
        row.addWidget(self.preview_lab, 1)
        b_rules = QPushButton("识别规则")
        b_rules.setObjectName("Ghost")
        b_rules.setCursor(Qt.PointingHandCursor)
        b_rules.clicked.connect(self._show_rules)
        b_add = QPushButton("记录  ⌘")
        b_add.setObjectName("Primary")
        b_add.setCursor(Qt.PointingHandCursor)
        b_add.clicked.connect(self._submit)
        row.addWidget(b_rules)
        row.addWidget(b_add)
        lay.addLayout(row)

        hint = QHBoxLayout()
        hint.setSpacing(8)
        lab = QLabel("试试：")
        lab.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_CAPTION};")
        hint.addWidget(lab)
        for ex in EXAMPLES:
            b = QPushButton(ex)
            b.setObjectName("Icon")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ background:{T.CARD}; border:1px solid {T.HAIRLINE};"
                f"border-radius:{T.RADIUS_XS}px; padding:3px 10px; color:{T.TEXT_SECONDARY};"
                f"font-size:{T.FS_CAPTION}; }}"
                f"QPushButton:hover {{ background:#FFFFFF; color:{T.TEXT}; }}")
            b.clicked.connect(lambda _=False, t=ex: self._fill(t))
            hint.addWidget(b)
        hint.addStretch(1)
        lay.addLayout(hint)
        self.stack_host.addWidget(sheet)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.timeline_host = QWidget()
        self.timeline_host.setStyleSheet("background:transparent;")
        self.timeline_lay = QVBoxLayout(self.timeline_host)
        self.timeline_lay.setContentsMargins(0, 0, 8, 0)
        self.timeline_lay.setSpacing(10)
        scroll.setWidget(self.timeline_host)
        self.stack_host.addWidget(scroll, 1)
        self._render_timeline()

    def _fill(self, text: str):
        self.input.setPlainText(text)
        self.input.setFocus()

    def _preview(self):
        text = self.input.toPlainText().strip()
        if not text:
            self.kind_badge.setText("")
            self.kind_badge.setStyleSheet("background:transparent; border:none;")
            self.preview_lab.setText("输入内容后，这里会实时显示识别结果")
            return
        v = cap.classify(text, date.today())
        ink = T.kind_color(v["kind"])
        self.kind_badge.setText(cap.KIND_LABEL.get(v["kind"], v["kind"]))
        self.kind_badge.setStyleSheet(
            f"background:{T.rgba(ink, 0.15)}; color:{T.ink(ink)}; border:none;"
            f"border-radius:{T.RADIUS_SM}px; padding:2px 10px; font-size:{T.FS_CAPTION};"
            f"font-weight:700;")
        note = cap.describe(v)
        if v["confidence"] < 0.6:
            note += " · 置信度低，落库后仍可手动改"
        self.preview_lab.setText(note)

    def _submit(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        try:
            row = cap.capture(self.db, text, date.today())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "没能记下来", str(exc))
            return
        self.input.clear()
        get_bus().changed.emit()
        self._preview()
        self._render_timeline()
        kind = row.get("kind", "note")
        extra = ""
        if kind == "credential":
            extra = "\n\n密码已用 AES-256-GCM 加密保存，可在「密码本」里查看。"
        elif row.get("target_table"):
            extra = f"\n已写入：{row['target_table']}#{row['target_id']}"
        QMessageBox.information(self, "已记录",
                                f"识别为「{cap.KIND_LABEL.get(kind, kind)}」{extra}",
                                QMessageBox.Ok)

    def _show_rules(self):
        lines = [
            f"<b>账号密码</b>：含「密码/口令/password」→ 存入密码本（AES-256-GCM 加密，不跨设备同步）",
            f"<b>实验室打卡</b>：含「打卡/实验室」且带时间段或时长 → 写入打卡或手动时长",
            f"<b>节假日</b>：含「放假/调休/补班」且带日期 → 写入节假日",
            f"<b>网址收藏</b>：含链接且无待办意图 → 写入网站（类别按域名猜）",
            f"<b>待办事项</b>：含「记得/提醒/要…/截止」或带日期 → 写入待办（自动抽日期/优先级/预计时长）",
            f"<b>随手笔记</b>：都不匹配 → 只保留原文",
            "",
            "日期写法：今天/明天/后天/周五/下周三/9-25/10月3日/2026-10-03",
            "时长写法：3小时 / 90分钟 / 半小时 / 9:00-11:30",
            "优先级：紧急·重要→高，有空·不着急→低",
        ]
        box = QMessageBox(self.window())
        box.setIcon(QMessageBox.NoIcon)
        box.setWindowTitle("识别规则")
        box.setTextFormat(Qt.RichText)
        box.setText("<div style='font-size:10.5pt; line-height:150%'>"
                    + "<br>".join(lines) + "</div>")
        box.addButton("知道了", QMessageBox.AcceptRole)
        box.exec()

    def _render_timeline(self):
        while self.timeline_lay.count():
            it = self.timeline_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.hide()
                w.deleteLater()
        rows = cap.list_captures(self.db)
        if not rows:
            empty = QLabel("还没有随手记。上面那句话怎么顺手就怎么写。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"background:transparent; color:{T.MUTED}; padding:40px;")
            self.timeline_lay.addWidget(empty)
            return
        cur_day = None
        panel = None
        inner = None
        for r in rows:
            day = (r.get("created") or "")[:10]
            if day != cur_day:
                cur_day = day
                panel = GlassPanel(variant="regular", radius=T.RADIUS_XL)
                pl = QVBoxLayout(panel)
                pl.setContentsMargins(16, 12, 16, 8)
                pl.setSpacing(2)
                pl.addWidget(self._day_head(day))
                inner = QVBoxLayout()
                inner.setContentsMargins(0, 4, 0, 0)
                inner.setSpacing(0)
                pl.addLayout(inner)
                self.timeline_lay.addWidget(panel)
            inner.addWidget(self._capture_row(r))
        self.timeline_lay.addStretch(1)

    @staticmethod
    def _day_head(day: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 4)
        lay.setSpacing(9)
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
            wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()]
            label = f"{d.month} 月 {d.day} 日 {wd}"
            if d == date.today():
                label += " · 今天"
        except ValueError:
            label = day
        lab = QLabel(label)
        lab.setStyleSheet(
            f"background:transparent; color:{T.TEXT};"
            f"font-size:{T.FS_HEADLINE}; font-weight:700;")
        lay.addWidget(lab)
        lay.addStretch(1)
        return w

    def _capture_row(self, r: dict) -> QWidget:
        kind = r.get("kind", "note")
        ink = T.kind_color(kind)
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 6, 2, 6)
        lay.setSpacing(10)

        t = QLabel((r.get("created") or "")[11:16])
        t.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_FOOTNOTE};"
            f"font-weight:600;")
        t.setFixedWidth(38)
        lay.addWidget(t)

        badge = QLabel(cap.KIND_LABEL.get(kind, kind))
        badge.setStyleSheet(
            f"background:{T.rgba(ink, 0.14)}; color:{T.ink(ink)}; border:none;"
            f"border-radius:{T.RADIUS_SM}px; padding:1px 8px; font-size:{T.FS_CAPTION};"
            f"font-weight:700;")
        badge.setFixedWidth(66)
        badge.setAlignment(Qt.AlignCenter)
        lay.addWidget(badge)

        raw = QLabel(r.get("raw", ""))
        raw.setStyleSheet(f"background:transparent; color:{T.TEXT}; font-size:{T.FS_BODY};")
        raw.setToolTip(r.get("raw", ""))
        raw.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(raw, 1)

        target = r.get("target_table") or ""
        note = QLabel({"todos": "已建待办", "attendance_blocks": "已记打卡",
                       "manual_hours": "已记手动时长", "websites": "已存网址",
                       "holidays": "已加节假日", "vault_items": "已存密码本",
                       "": "仅留原文"}.get(target, target))
        note.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_CAPTION};")
        lay.addWidget(note)

        b = QPushButton("撤销")
        b.setObjectName("Icon")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, cid=r["id"], txt=r.get("raw", ""):
                          self._undo(cid, txt))
        lay.addWidget(b)
        return w

    def _undo(self, cid: int, raw: str):
        if not ask(self, "撤销随手记",
                   f"撤销「{raw[:28]}」？\n它自动生成的那条记录也会一起删除。", "撤销"):
            return
        cap.undo_capture(self.db, cid)
        get_bus().changed.emit()
        self._render_timeline()

    # ---------------------------------------------------------------- 密码本
    def _render_vault(self):
        mode = vault.mode(self.db)
        unlocked = vault.is_unlocked() or mode in ("none", "keyfile")
        if mode == "none":
            vault.setup(self.db, None)
            mode = "keyfile"

        head = GlassPanel(variant="strong", radius=T.RADIUS_XL)
        hl = QVBoxLayout(head)
        hl.setContentsMargins(18, 14, 18, 12)
        hl.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(10)
        state_ink = T.GREEN if unlocked else T.AMBER
        state = QLabel("■ 已解锁 · 本机密钥保护" if mode == "keyfile"
                       else ("■ 已解锁 · 主密码保护" if unlocked
                             else "□ 已锁定 · 需要主密码"))
        state.setStyleSheet(
            f"background:transparent; color:{T.ink(state_ink)};"
            f"font-size:{T.FS_FOOTNOTE}; font-weight:700;")
        row.addWidget(state)
        row.addStretch(1)
        if mode == "passphrase" and not unlocked:
            self.unlock_ed = QLineEdit()
            self.unlock_ed.setEchoMode(QLineEdit.Password)
            self.unlock_ed.setPlaceholderText("输入主密码解锁")
            self.unlock_ed.setFixedWidth(200)
            self.unlock_ed.returnPressed.connect(self._unlock)
            row.addWidget(self.unlock_ed)
            b = QPushButton("解锁")
            b.setObjectName("Primary")
            b.clicked.connect(self._unlock)
            row.addWidget(b)
        else:
            b_lock = QPushButton("锁定")
            b_lock.setObjectName("Icon")
            b_lock.setEnabled(mode == "passphrase")
            b_lock.clicked.connect(self._lock)
            b_pass = QPushButton("改用主密码" if mode == "keyfile" else "更换主密码")
            b_pass.setObjectName("Ghost")
            b_pass.clicked.connect(self._set_passphrase)
            row.addWidget(b_lock)
            row.addWidget(b_pass)
        hl.addLayout(row)

        tip = QLabel("密码用 AES-256-GCM 加密后入库，数据库文件里找不到明文；"
                     "密码本不参与跨设备同步。")
        tip.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_CAPTION};")
        tip.setWordWrap(True)
        hl.addWidget(tip)

        if unlocked:
            form = QHBoxLayout()
            form.setSpacing(8)
            self.v_title = QLineEdit()
            self.v_title.setPlaceholderText("站点 / 应用名")
            self.v_user = QLineEdit()
            self.v_user.setPlaceholderText("账号 / 邮箱")
            self.v_secret = QLineEdit()
            self.v_secret.setPlaceholderText("密码")
            self.v_secret.setEchoMode(QLineEdit.Password)
            for ed, w in ((self.v_title, 200), (self.v_user, 220), (self.v_secret, 220)):
                ed.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                form.addWidget(ed)
            b_add = QPushButton("＋ 添加")
            b_add.setObjectName("Primary")
            b_add.clicked.connect(self._vault_add)
            form.addWidget(b_add)
            form.addStretch(1)
            hl.addLayout(form)
        else:
            self.unlock_ed = None

        self.stack_host.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(8)
        scroll.setWidget(host)
        self.stack_host.addWidget(scroll, 1)

        if not unlocked:
            empty = QLabel("密码本已锁定。输入主密码解锁后才能查看。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"background:transparent; color:{T.MUTED}; padding:40px;")
            lay.addWidget(empty)
            return

        items = vault.list_items(self.db)
        if not items:
            empty = QLabel("密码本还是空的。在上方填一行，或直接在「随手记」里写\n"
                           "「华为云 账号 xx@yy.com 密码 Abc@1234」就会自动存进来。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"background:transparent; color:{T.MUTED}; padding:36px;")
            lay.addWidget(empty)
            return
        for it in items:
            lay.addWidget(self._vault_row(it))
        lay.addStretch(1)

    def _vault_row(self, it: dict) -> QWidget:
        panel = GlassPanel(variant="thin", radius=T.RADIUS_LG)
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(12)

        tile = QLabel((it["title"] or "?")[:1].upper())
        tile.setFixedSize(34, 34)
        tile.setAlignment(Qt.AlignCenter)
        ink = T.kind_color("credential")
        tile.setStyleSheet(
            f"background:{T.rgba(ink, 0.14)}; color:{T.ink(ink)}; border-radius:{T.tile_radius(34)}px;"
            f"font-size:12pt; font-weight:800; border:none;")
        lay.addWidget(tile)

        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(it["title"])
        name.setStyleSheet(
            f"background:transparent; color:{T.TEXT}; font-size:{T.FS_HEADLINE};"
            f"font-weight:700;")
        sub = " · ".join(x for x in (it.get("username") or "", it.get("url") or "") if x)
        sublab = QLabel(sub or "—")
        sublab.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_FOOTNOTE};")
        col.addWidget(name)
        col.addWidget(sublab)
        lay.addLayout(col, 1)

        shown = it["id"] in self._revealed
        secret = vault.get_secret(self.db, it["id"]) if shown else ""
        val = QLabel(secret if (shown and secret) else ("•" * 8 if it.get("has_secret") else "无密码"))
        val.setStyleSheet(
            f"background:transparent; color:{T.TEXT_SECONDARY};"
            f"font-family:'SF Mono', Menlo, monospace; font-size:{T.FS_FOOTNOTE};")
        lay.addWidget(val)

        b_show = QPushButton("隐藏" if shown else "显示")
        b_show.setObjectName("Icon")
        b_show.clicked.connect(lambda _=False, i=it["id"]: self._toggle_reveal(i))
        b_copy = QPushButton("复制")
        b_copy.setObjectName("Icon")
        b_copy.clicked.connect(lambda _=False, i=it["id"]: self._copy(i))
        b_del = QPushButton("删除")
        b_del.setObjectName("Icon")
        b_del.setStyleSheet(
            f"QPushButton {{ background:transparent; border:none; color:{T.MUTED};"
            f"padding:3px 7px; border-radius:{T.RADIUS_XS}px; font-size:{T.FS_FOOTNOTE}; }}"
            f"QPushButton:hover {{ background:{T.rgba(T.RED, 0.12)}; color:{T.RED}; }}")
        b_del.clicked.connect(lambda _=False, i=it["id"], t=it["title"]: self._vault_del(i, t))
        for b in (b_show, b_copy, b_del):
            b.setCursor(Qt.PointingHandCursor)
            lay.addWidget(b)
        return panel

    def _toggle_reveal(self, item_id: int):
        if item_id in self._revealed:
            self._revealed.discard(item_id)
        else:
            self._revealed.add(item_id)
        self._render_vault()

    def _copy(self, item_id: int):
        from PySide6.QtWidgets import QApplication
        text = vault.get_secret(self.db, item_id)
        if not text:
            QMessageBox.information(self, "复制", "这条没有密码，或密码本尚未解锁。")
            return
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "已复制", "密码已复制到剪贴板。", QMessageBox.Ok)

    def _vault_add(self):
        title = self.v_title.text().strip()
        if not title:
            QMessageBox.warning(self, "缺少名称", "至少填一个站点 / 应用名。")
            return
        vault.add_item(self.db, title=title, username=self.v_user.text(),
                       secret=self.v_secret.text())
        self.v_title.clear(); self.v_user.clear(); self.v_secret.clear()
        self._render_vault()

    def _vault_del(self, item_id: int, title: str):
        if ask(self, "删除密码条目",
               f"删除「{title}」？密码条目是物理删除，不进回收站。", "删除"):
            vault.delete_item(self.db, item_id)
            self._render_vault()

    def _lock(self):
        vault.lock()
        self._revealed.clear()
        self._render_vault()

    def _unlock(self):
        text = self.unlock_ed.text() if self.unlock_ed else ""
        if vault.unlock(self.db, text):
            self._render_vault()
        else:
            QMessageBox.warning(self, "解锁失败", "主密码不对。")

    def _set_passphrase(self):
        from PySide6.QtWidgets import QInputDialog
        p1, ok = QInputDialog.getText(self, "设置主密码", "新主密码（至少 8 位）：",
                                      QLineEdit.Password)
        if not ok:
            return
        if len(p1) < 8:
            QMessageBox.warning(self, "太短", "主密码至少 8 位。")
            return
        p2, ok2 = QInputDialog.getText(self, "再确认一次", "重复新主密码：",
                                       QLineEdit.Password)
        if not ok2 or p1 != p2:
            QMessageBox.warning(self, "不一致", "两次输入不一样，没有修改。")
            return
        vault.setup(self.db, p1)
        QMessageBox.information(
            self, "已启用主密码",
            "从现在起，每次打开 LabAssistant 都要先解锁密码本。\n"
            "主密码没有找回机制——忘了就等于那些密码找不回来了，请自己留好备份。")
        self._render_vault()

    # ---------------------------------------------------------------- 刷新
    def _on_changed(self):
        if self.isVisible():
            self.refresh()

    def refresh(self):
        self._render_tab()

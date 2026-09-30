"""网站收藏：对话框（新增/编辑/打开）。

视觉并入液态玻璃体系：`theme.DIALOG_BG` 作窗口底，表单坐在一块玻璃板上，
标题用 `SectionHeader`（颜色跟随网站类别），校验提示用 `theme.RED` 令牌。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLineEdit,
    QTextEdit,
)

from labassistant.db import Database
from labassistant.services.websites import (
    CATEGORIES,
    CATEGORY_CN,
    add_website,
    update_website,
)
from labassistant.services import websites as ws
from labassistant.ui import theme as T
from labassistant.ui.dialogs import _buttons, _dialog_shell, _form, _muted
from labassistant.ui.glass import Hairline


class WebsiteDialog(QDialog):
    def __init__(self, db: Database, website: dict | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.website = website
        cs = T.category_style((website or {}).get("category", ""))
        lay, body = _dialog_shell(
            self, "编辑网站" if website else "添加网站", ink=cs.ink,
            subtitle="网址可以省略 http/https，保存时会自动补全；收藏会随同步跨设备可用。",
            min_w=460)

        form = _form(body)
        self.name_ed = QLineEdit()
        self.name_ed.setPlaceholderText("如：Google Scholar")
        self.url_ed = QLineEdit()
        self.url_ed.setPlaceholderText("如：https://scholar.google.com")
        self.cat_combo = QComboBox()
        for cat in CATEGORIES:
            self.cat_combo.addItem(CATEGORY_CN[cat], cat)
        # 类别选择框保持短小，不随表单无限拉伸（宽度仅容纳“其他”等选项即可）
        self.cat_combo.setMinimumWidth(110)
        self.cat_combo.setMaximumWidth(160)
        self.cat_combo.setSizePolicy(
            self.cat_combo.sizePolicy().horizontalPolicy(), self.cat_combo.sizePolicy().verticalPolicy())
        self.cat_combo.setStyleSheet("QComboBox { min-width: 110px; max-width: 160px; }")
        self.note_ed = QTextEdit()
        self.note_ed.setFixedHeight(60)
        self.note_ed.setPlaceholderText("备注（可选）")
        form.addRow("网站名称 *", self.name_ed)
        form.addRow("网址 *", self.url_ed)
        form.addRow("类别", self.cat_combo)
        form.addRow("备注", self.note_ed)

        if website:
            self.name_ed.setText(website["name"])
            self.url_ed.setText(website["url"])
            idx = self.cat_combo.findData(website["category"])
            self.cat_combo.setCurrentIndex(max(0, idx))
            self.note_ed.setPlainText(website.get("note") or "")

        body.addWidget(Hairline())
        self.hint = _muted("", ink=T.RED)
        body.addWidget(self.hint)

        _buttons(self, lay, "保存", self._save)

    def _save(self):
        name = self.name_ed.text().strip()
        raw = self.url_ed.text().strip()
        if not name:
            self.hint.setText("请填写网站名称。")
            return
        if not raw:
            self.hint.setText("请填写网址。")
            return
        url = ws.normalize_url(raw)
        if not ws.is_valid_url(url):
            self.hint.setText("请输入有效的网址（可省略 http/https，会自动补全）。")
            return
        note = self.note_ed.toPlainText().strip()
        category = self.cat_combo.currentData()
        if self.website:
            update_website(self.db, self.website["id"], name, url, category, note)
        else:
            add_website(self.db, name, url, category, note)
        self.accept()

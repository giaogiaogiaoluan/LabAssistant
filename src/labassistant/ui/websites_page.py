"""网站页：按类别**分区**展示，每个类别一套配色。

结构（不再把所有网站堆成一列）：
    标题 + 搜索 + 添加
    分类筛选（玻璃胶囊，带类别色点）
    └─ 每个类别一个区块：SectionHeader + 自适应网格的玻璃卡片
"""

from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from labassistant.db import Database
from labassistant.services import websites as ws
from labassistant.services.websites import CATEGORIES, CATEGORY_CN
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.dialogs import ask, warn
from labassistant.ui.glass import GlassPanel, Hairline
from labassistant.ui.widgets import nav_pixmap
from labassistant.ui.website_dialog import WebsiteDialog

FILTERS = [("all", "全部")] + [(c, CATEGORY_CN[c]) for c in CATEGORIES]
CARD_MIN_W = 336


def open_in_browser(url: str) -> bool:
    """用系统默认浏览器打开（跨平台）。"""
    try:
        webbrowser.open(url or "")
        return True
    except Exception:  # noqa: BLE001
        return False


def _host_of(url: str) -> str:
    s = (url or "").strip()
    for prefix in ("https://", "http://", "www."):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
    return s.split("/")[0]


# ================================================================== 卡片
class WebsiteCard(GlassPanel):
    """单个网站：染色玻璃瓦片 + 名称 + 域名 + 操作。"""

    def __init__(self, site: dict, page: "WebsitesPage", parent=None):
        cs = T.category_style(site.get("category", "other"))
        super().__init__(parent, variant="thin", ink=cs.ink, radius=T.RADIUS_LG,
                         hover_lift=True)
        self.site = site
        self.page = page
        self.setMinimumHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 12, 12)
        lay.setSpacing(12)

        # 类别色玻璃瓦片（取名称首字）
        tile = QLabel((site.get("name") or "?").strip()[:1].upper())
        tile.setFixedSize(42, 42)
        tile.setAlignment(Qt.AlignCenter)
        tile.setStyleSheet(
            f"color:{cs.ink_text}; font-size:15pt; font-weight:800;"
            f"background: qlineargradient(x1:0, y1:0, x2:0.6, y2:1,"
            f" stop:0 {cs.tile_top}, stop:1 {cs.tile_bottom});"
            f"border: 1px solid {cs.ink}; border-radius:{T.tile_radius(42)}px;")
        lay.addWidget(tile)

        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(site["name"])
        name.setStyleSheet(
            f"background:transparent; border:none; color:{T.TEXT};"
            f"font-size:{T.FS_HEADLINE}; font-weight:700;")
        name.setTextInteractionFlags(Qt.NoTextInteraction)
        host = QLabel(_host_of(site.get("url", "")))
        host.setStyleSheet(
            f"background:transparent; border:none; color:{cs.ink_text};"
            f"font-size:{T.FS_FOOTNOTE};")
        note = (site.get("note") or "").strip()
        sub_lines = [host]
        if note:
            n = QLabel(_elide(note, 34))
            n.setStyleSheet(
                f"background:transparent; border:none; color:{T.MUTED};"
                f"font-size:{T.FS_CAPTION};")
            sub_lines.append(n)
        text.addWidget(name)
        for lab in sub_lines:
            text.addWidget(lab)
        lay.addLayout(text, 1)

        acts = QVBoxLayout()
        acts.setSpacing(4)
        b_open = QPushButton("打开")
        b_open.setObjectName("Primary")
        b_open.setStyleSheet(
            f"QPushButton {{ background:{T.ACCENT_SOLID}; color:{T.ON_ACCENT};"
            f"border:1px solid {T.ACCENT_DARK}; border-radius:{T.RADIUS_MD}px; font-weight:700; }}"
            f"QPushButton:hover {{ background:{T.ACCENT_DARK}; }}")
        b_open.setFixedHeight(30)
        b_open.setCursor(Qt.PointingHandCursor)
        b_open.clicked.connect(lambda _=False, u=site["url"]: self.page.open_url(u))
        row2 = QHBoxLayout()
        row2.setSpacing(2)
        b_edit = QPushButton("编辑")
        b_edit.setObjectName("Icon")
        b_edit.setCursor(Qt.PointingHandCursor)
        b_edit.clicked.connect(lambda _=False, i=site["id"]: self.page.edit_site(i))
        b_del = QPushButton("删除")
        b_del.setObjectName("Icon")
        b_del.setCursor(Qt.PointingHandCursor)
        b_del.setStyleSheet(
            f"QPushButton {{ background:transparent; border:none; color:{T.MUTED};"
            f"padding:3px 7px; border-radius:{T.RADIUS_XS}px; font-size:{T.FS_FOOTNOTE}; }}"
            f"QPushButton:hover {{ background:{T.rgba(T.RED, 0.12)}; color:{T.RED}; }}")
        b_del.clicked.connect(lambda _=False, i=site["id"]: self.page.delete_site(i))
        row2.addWidget(b_edit)
        row2.addWidget(b_del)
        acts.addWidget(b_open)
        acts.addLayout(row2)
        lay.addLayout(acts)

        self.mouseDoubleClickEvent = lambda _e, u=site["url"]: self.page.open_url(u)


def _elide(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ================================================================== 分类区块
class CategorySection(QWidget):
    """一个类别：标题行 + 自适应网格。"""

    def __init__(self, cat_key: str, sites: list[dict], page: "WebsitesPage", parent=None):
        super().__init__(parent)
        self.cat_key = cat_key
        self.sites = sites
        self.page = page
        self.cards: list[WebsiteCard] = []
        self.cols = 0
        cs = T.category_style(cat_key)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(9)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            f"background:{cs.ink}; border-radius:{T.RADIUS_SM // 2}px; border:none;"
            f"color:{cs.ink}; font-size:1px;")
        title = QLabel(cs.label)
        title.setStyleSheet(
            f"background:transparent; color:{T.TEXT};"
            f"font-size:{T.FS_TITLE2}; font-weight:700;")
        count = QLabel(str(len(sites)))
        count.setStyleSheet(
            f"background:{cs.tint}; color:{cs.ink_text}; border-radius:{T.RADIUS_SM}px;"
            f"padding:2px 9px; font-size:{T.FS_CAPTION}; font-weight:800;")
        hint = QLabel(cs.hint)
        hint.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_CAPTION};")
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {cs.line}, stop:1 rgba(255,255,255,0)); border:none;")
        head.addWidget(dot)
        head.addWidget(title)
        head.addWidget(count)
        head.addSpacing(4)
        head.addWidget(hint)
        head.addSpacing(10)
        head.addWidget(line, 1)
        lay.addLayout(head)

        # 网格直接挂在区块布局上：不引入中间透明容器，避免换列时旧像素残留成“重影”
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        lay.addLayout(self.grid)
        self.reflow(1)

    def reflow(self, cols: int):
        """按新的列数重建卡片（列数变化很少发生，重建比搬移更可靠）。"""
        cols = max(1, min(6, cols))
        if cols == self.cols:
            return
        self.cols = cols
        for card in self.cards:
            self.grid.removeWidget(card)
            card.setParent(None)     # 立刻离屏：只 deleteLater 会留下旧像素重影
            card.deleteLater()
        self.cards = []
        while self.grid.count():
            self.grid.takeAt(0)
        for i, site in enumerate(self.sites):
            card = WebsiteCard(site, self.page)
            self.cards.append(card)
            self.grid.addWidget(card, i // cols, i % cols)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        for c in range(cols, 7):
            self.grid.setColumnStretch(c, 0)
        self.updateGeometry()


# ================================================================== 页面
class WebsitesPage(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setObjectName("Root")
        self._filter = "all"
        self._sections: list[CategorySection] = []
        self._cols = 0
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(90)
        self._reflow_timer.timeout.connect(self._relayout)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(14)

        # ---- 标题行
        top = QHBoxLayout()
        top.setSpacing(12)
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel("网站")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("PageSubtitle")
        box.addWidget(title)
        box.addWidget(self.subtitle)
        top.addLayout(box)
        top.addStretch(1)
        self.search_ed = QLineEdit()
        self.search_ed.setObjectName("Search")
        self.search_ed.setPlaceholderText("搜索名称 / 网址 / 备注")
        self.search_ed.setFixedWidth(260)
        self.search_ed.setClearButtonEnabled(True)
        self.search_ed.textChanged.connect(lambda _t: self.reload())
        self.search_ed.addAction(
            QIcon(nav_pixmap("search", 15, T.MUTED)), QLineEdit.LeadingPosition)
        top.addWidget(self.search_ed)
        btn_add = QPushButton("＋  添加网站")
        btn_add.setObjectName("Primary")
        btn_add.setCursor(Qt.PointingHandCursor)
        btn_add.clicked.connect(self.add_site)
        top.addWidget(btn_add)
        outer.addLayout(top)

        # ---- 分类筛选（玻璃分段胶囊）
        bar = QFrame()
        bar.setStyleSheet(
            "QFrame { background:#DEDCD2; border:1px solid #66665F; border-radius:"
            f"{T.RADIUS_MD}px; }}")
        chips = QHBoxLayout(bar)
        chips.setContentsMargins(5, 5, 5, 5)
        chips.setSpacing(3)
        self._chip_btns: dict[str, QPushButton] = {}
        for key, label in FILTERS:
            ink = T.ACCENT if key == "all" else T.category_style(key).ink
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(30)
            b.setMinimumWidth(64)
            b.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Fixed)
            b.setStyleSheet(self._chip_css(ink, False))
            b.toggled.connect(lambda on, k=key, bb=b, i=ink:
                              bb.setStyleSheet(self._chip_css(i, on)))
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            chips.addWidget(b)
            self._chip_btns[key] = b
        chips.addStretch(1)
        outer.addWidget(bar)

        # ---- 内容滚动区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.list_host = QWidget()
        self.list_host.setStyleSheet("background:transparent;")
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 2, 8, 0)
        self.list_lay.setSpacing(6)
        scroll.setWidget(self.list_host)
        outer.addWidget(scroll, 1)

        tip = QLabel("双击卡片或点“打开”会用系统默认浏览器访问；网站数据随同步体系跨设备同步。")
        tip.setStyleSheet(
            f"background:transparent; color:{T.MUTED}; font-size:{T.FS_CAPTION};")
        outer.addWidget(tip)

        self._chip_btns["all"].setChecked(True)
        get_bus().changed.connect(self._on_changed)
        self.reload()

    # ---- 外观
    @staticmethod
    def _chip_css(ink: str, on: bool) -> str:
        return T.segment_css(ink, on, padding="0 14px")

    def _clear(self):
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._sections = []

    def _set_filter(self, key: str):
        self._filter = key
        for k, b in self._chip_btns.items():
            b.blockSignals(True)
            b.setChecked(k == key)
            b.blockSignals(False)
            ink = T.ACCENT if k == "all" else T.category_style(k).ink
            b.setStyleSheet(self._chip_css(ink, k == key))
        self.reload()

    def _on_changed(self):
        if self.isVisible():
            self.reload()

    # ---- 数据
    def reload(self):
        keyword = self.search_ed.text().strip() if hasattr(self, "search_ed") else ""
        rows = ws.list_websites(self.db, keyword=keyword, category="")
        alive = len(rows)
        self._clear()

        if self._filter == "all":
            groups = [(c, [r for r in rows if r.get("category", "other") == c])
                      for c in T.CATEGORY_ORDER]
            groups = [(c, g) for c, g in groups if g]
            # 未知类别兜底归入 other
            known = set(T.CATEGORY_ORDER)
            extra = [r for r in rows if r.get("category", "other") not in known]
            if extra:
                groups.append(("other", [r for r in rows
                                         if r.get("category", "other") not in known]))
        else:
            groups = [(self._filter, [r for r in rows
                                      if r.get("category", "other") == self._filter])]

        shown = sum(len(g) for _, g in groups)
        cats = len(groups)
        self.subtitle.setText(
            f"共 {alive} 个收藏 · 当前显示 {shown} 个 · 分 {cats} 类"
            if keyword else f"共 {alive} 个收藏 · {cats} 个类别有内容")

        if not shown:
            empty = QLabel("这个分类下还没有网站。点右上角“＋ 添加网站”开始收藏。"
                           if rows else "还没有任何收藏。点右上角“＋ 添加网站”开始。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"background:transparent; color:{T.MUTED};"
                f"font-size:{T.FS_BODY}; padding:46px;")
            self.list_lay.addWidget(empty)
            return

        for cat_key, items in groups:
            sec = CategorySection(cat_key, items, self)
            self._sections.append(sec)
            self.list_lay.addWidget(sec)
        self.list_lay.addStretch(1)
        self._cols = 0
        self._relayout()

    # ---- 自适应网格
    def _cols_for(self, width: int) -> int:
        return max(1, min(4, (max(width, 1) - 12) // CARD_MIN_W))

    def _relayout(self):
        cols = self._cols_for(self.list_host.width() or self.width())
        if cols == self._cols:
            return
        self._cols = cols
        for sec in self._sections:
            sec.reflow(cols)

    def resizeEvent(self, e):
        self._reflow_timer.start()
        super().resizeEvent(e)

    # ---- 动作
    def open_url(self, url: str):
        if not open_in_browser(url):
            warn(self, "打开失败", "无法打开浏览器，请检查系统默认浏览器设置。")

    def add_site(self):
        dlg = WebsiteDialog(self.db, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.reload()

    def edit_site(self, website_id: int):
        w = ws.get_website(self.db, website_id)
        if not w:
            return
        dlg = WebsiteDialog(self.db, w, parent=self.window())
        if dlg.exec():
            get_bus().changed.emit()
            self.reload()

    def delete_site(self, website_id: int):
        if ask(self, "删除网站", "确定删除该网站吗？（会同步删除到其他设备）", "删除"):
            ws.delete_website(self.db, website_id)
            get_bus().changed.emit()
            self.reload()

"""LabOS 通用组件：导航、指标、自绘图表与状态标记。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from labassistant import constants as C
from labassistant.ui import theme as T
from labassistant.ui.glass import GlassPanel


# ---------------------------------------------------------------- 矢量图标
def nav_pixmap(kind: str, size: int = 20, ink: str = T.TEXT_SECONDARY) -> QPixmap:
    """手绘导航图标：不依赖任何图标字体，缩放到 16~24px 都清晰。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = T.qcolor(ink)
    pen = QPen(c, max(1.4, size * 0.085))
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = size * 0.16                    # margin
    box = QRectF(m, m, size - 2 * m, size - 2 * m)

    if kind == "home":
        path = QPainterPath()
        path.moveTo(box.left(), box.top() + box.height() * 0.42)
        path.lineTo(box.center().x(), box.top())
        path.lineTo(box.right(), box.top() + box.height() * 0.42)
        p.drawPath(path)
        p.drawRoundedRect(QRectF(box.left() + size * 0.06, box.top() + box.height() * 0.40,
                                 box.width() - size * 0.12, box.height() * 0.60),
                          size * 0.09, size * 0.09)
    elif kind == "calendar":
        p.drawRoundedRect(box, size * 0.10, size * 0.10)
        p.drawLine(QPointF(box.left(), box.top() + box.height() * 0.30),
                   QPointF(box.right(), box.top() + box.height() * 0.30))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        for i in range(2):
            for j in range(2):
                x = box.left() + box.width() * (0.28 + i * 0.36)
                y = box.top() + box.height() * (0.48 + j * 0.26)
                p.drawEllipse(QPointF(x, y), size * 0.055, size * 0.055)
    elif kind == "check":
        p.drawEllipse(box)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
        tick = QPainterPath()
        tick.moveTo(box.left() + box.width() * 0.26, box.top() + box.height() * 0.53)
        tick.lineTo(box.left() + box.width() * 0.44, box.top() + box.height() * 0.70)
        tick.lineTo(box.left() + box.width() * 0.76, box.top() + box.height() * 0.34)
        p.drawPath(tick)
    elif kind == "globe":
        p.drawEllipse(box)
        p.drawEllipse(QRectF(box.center().x() - box.width() * 0.20, box.top(),
                             box.width() * 0.40, box.height()))
        p.drawLine(QPointF(box.left(), box.center().y()),
                   QPointF(box.right(), box.center().y()))
    elif kind == "chart":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        w = box.width() / 3.0
        for i, h in enumerate((0.45, 0.80, 0.62)):
            bar = QRectF(box.left() + i * w + w * 0.14,
                         box.bottom() - box.height() * h, w * 0.72, box.height() * h)
            p.drawRoundedRect(bar, size * 0.05, size * 0.05)
    elif kind == "search":
        glass = QRectF(box.left(), box.top(), box.width() * 0.62, box.height() * 0.62)
        p.drawEllipse(glass)
        p.drawLine(QPointF(glass.right() - glass.width() * 0.06,
                           glass.bottom() - glass.height() * 0.06),
                   QPointF(box.right(), box.bottom()))
    elif kind == "sparkle":
        def star(cx, cy, rad):
            pts = QPainterPath()
            import math as _m
            for i in range(8):
                a = _m.pi / 4 * i - _m.pi / 2
                rr = rad if i % 2 == 0 else rad * 0.38
                x, y = cx + rr * _m.cos(a), cy + rr * _m.sin(a)
                pts.lineTo(x, y) if i else pts.moveTo(x, y)
            pts.closeSubpath()
            return pts
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(star(box.center().x(), box.center().y(), box.width() * 0.50))
        p.setBrush(T.qcolor(T.rgba(ink, 0.55)))
        p.drawPath(star(box.left() + box.width() * 0.16, box.bottom() - box.height() * 0.12,
                        box.width() * 0.20))
    elif kind == "gear":
        p.drawEllipse(QRectF(box.center().x() - box.width() * 0.22,
                             box.center().y() - box.width() * 0.22,
                             box.width() * 0.44, box.width() * 0.44))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        import math
        for i in range(8):
            a = math.tau * i / 8
            r = box.width() * 0.46
            x = box.center().x() + r * math.cos(a)
            y = box.center().y() + r * math.sin(a)
            p.drawEllipse(QPointF(x, y), size * 0.055, size * 0.055)
    p.end()
    return pm


def nav_icon(kind: str, ink: str = T.TEXT_SECONDARY) -> QIcon:
    return QIcon(nav_pixmap(kind, 20, ink))


NAV_ICONS = {"首页": "home", "课程表": "calendar", "待办": "check", "随手记": "sparkle",
             "网站": "globe", "统计": "chart", "设置": "gear"}


# ---------------------------------------------------------------- 徽标
def make_chip(text: str, bg: str, fg: str, bold: bool = False) -> QLabel:
    """玻璃胶囊徽标（图例、状态标记）。"""
    lab = QLabel(text)
    weight = " font-weight:600;" if bold else ""
    lab.setStyleSheet(
        f"background:{bg}; color:{T.ink(fg)}; border:none;"
        f"border-radius:{T.RADIUS_SM}px; padding:2px 10px;"
        f" font-size:{T.FS_FOOTNOTE};{weight}")
    lab.setAlignment(Qt.AlignCenter)
    return lab


# ---------------------------------------------------------------- 指标列（无框）
class StatBox(QFrame):
    """指标列：小标题 + 大数字 + 可选副标题。**无卡片边框**，靠发丝线与相邻列分隔。"""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("StatBox")
        self.setStyleSheet("QFrame#StatBox { background: transparent; border: none; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(1)
        self.title_lab = QLabel(title)
        self.title_lab.setObjectName("MetricTitle")
        self.value_lab = QLabel("—")
        self.value_lab.setObjectName("MetricValue")
        self.sub_lab = QLabel("")
        self.sub_lab.setObjectName("MetricSub")
        self.sub_lab.setWordWrap(True)
        lay.addWidget(self.title_lab)
        lay.addWidget(self.value_lab)
        lay.addWidget(self.sub_lab)

    def set_value(self, value: str, color: str | None = None):
        # 大号数字也要过 WCAG：鲜色（橙/红/紫）直接当文字用会糊成一片
        ink = T.ink_big(color) if color else T.TEXT
        self.value_lab.setStyleSheet(
            f"background:transparent; border:none; color:{ink};"
            f"font-size:19pt; font-weight:700;")
        self.value_lab.setText(value)

    def set_sub(self, text: str):
        self.sub_lab.setText(text)


class MetricStrip(GlassPanel):
    """一整条玻璃面板 + 内部发丝分隔的指标带（替代一排小卡片）。"""

    def __init__(self, parent=None):
        super().__init__(parent, variant="regular", radius=T.RADIUS_XL)
        self._row = QHBoxLayout()
        self._row.setContentsMargins(6, 10, 6, 10)
        self._row.setSpacing(0)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._row)
        self._count = 0

    def add_metric(self, box: StatBox):
        if self._count:
            from labassistant.ui.glass import Hairline
            self._row.addWidget(VDivider(inset=12))
        self._row.addWidget(box, 1)
        self._count += 1

    def clear(self):
        while self._row.count():
            it = self._row.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self._count = 0


# ---------------------------------------------------------------- 图表
def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    exp = 10 ** (len(str(int(value))) - 1)
    m = value / exp
    for k in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m <= k:
            return k * exp
    return 10 * exp


def _fmt_h(v: float) -> str:
    if abs(v - round(v)) < 1e-6:
        return f"{round(v):g}"
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _axis_font(widget: QWidget, size: int = 7) -> QFont:
    f = QFont(widget.font())
    f.setPointSizeF(size)
    return f


class RingWidget(QWidget):
    """完成率方格仪表，保留 set_ratio 接口供统计页使用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ratio: float | None = None
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_ratio(self, ratio: float | None):
        self._ratio = ratio
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        ratio = self._ratio
        ink = T.GREEN if ratio is not None and ratio >= 1 else T.ACCENT
        cx = self.width() // 2
        top = max(16, (self.height() - 138) // 2)
        f = QFont(self.font())
        f.setFamily("Menlo")
        f.setPointSizeF(22)
        f.setBold(True)
        p.setFont(f)
        p.setPen(T.qcolor(ink))
        txt = "—" if ratio is None else f"{ratio * 100:.1f}%"
        p.drawText(0, top, self.width(), 42, Qt.AlignCenter, txt)
        cols, rows, gap = 10, 4, 3
        cell_w = max(7, min(15, (self.width() - 34 - gap * (cols - 1)) // cols))
        cell_h = 11
        grid_w = cols * cell_w + (cols - 1) * gap
        x0 = cx - grid_w // 2
        y0 = top + 52
        filled = 0 if ratio is None else max(0, min(cols * rows, round(ratio * cols * rows)))
        for index in range(cols * rows):
            col, row = index % cols, index // cols
            x = x0 + col * (cell_w + gap)
            y = y0 + row * (cell_h + gap)
            p.setPen(QPen(T.qcolor(T.BORDER), 1))
            p.setBrush(T.qcolor(ink if index < filled else "#E4E1D7"))
            p.drawRect(x, y, cell_w, cell_h)
        f2 = QFont(self.font())
        f2.setPointSizeF(8)
        p.setFont(f2)
        p.setPen(T.qcolor(T.MUTED))
        p.drawText(0, y0 + rows * (cell_h + gap) + 5, self.width(), 20,
                   Qt.AlignHCenter | Qt.AlignTop, "每格 2.5%  ·  目标 100%")
        p.end()


class LineChart(QWidget):
    """每日有效时间直线图，使用方形节点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[int] = []
        self._ref: int | None = None
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_series(self, labels: list[str], values: list[int], reference: int | None = None):
        self._labels = list(labels)
        self._values = list(values)
        self._ref = reference
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        left, right, top, bottom = 46, 12, 14, 26
        pw, ph = w - left - right, h - top - bottom
        n = len(self._values)
        if n == 0 or pw <= 6 or ph <= 6:
            p.end()
            return
        all_vals = list(self._values) + ([self._ref] if self._ref else [])
        ymax = _nice_max(max(all_vals) if all_vals else 1)
        step = ymax / 4.0

        def ypos(v): return top + ph - ph * v / ymax
        def xpos(i): return left + pw * i / max(1, n - 1)

        p.setFont(_axis_font(self))
        for i in range(5):
            yy = int(ypos(i * step))
            p.setPen(QPen(T.qcolor(T.HAIRLINE), 1))
            p.drawLine(left, yy, w - right, yy)
            p.setPen(T.qcolor(T.MUTED))
            p.drawText(0, yy - 7, left - 6, 14, Qt.AlignRight | Qt.AlignVCenter,
                       f"{_fmt_h(i * step / 60)}h")
        if self._ref and self._ref > 0:
            p.setPen(QPen(T.qcolor(T.AMBER), 1, Qt.DashLine))
            yy = int(ypos(self._ref))
            p.drawLine(left, yy, w - right, yy)
        pts = [(round(xpos(i)), round(ypos(v))) for i, v in enumerate(self._values)]
        pen = QPen(T.qcolor(T.ACCENT_DARK), 2)
        pen.setJoinStyle(Qt.MiterJoin)
        pen.setCapStyle(Qt.SquareCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            p.drawLine(x1, y1, x2, y2)
        label_step = max(1, -(-n // 9))
        for i, (x, y) in enumerate(pts):
            p.setPen(QPen(T.qcolor(T.ACCENT_DARK), 1))
            p.setBrush(T.qcolor(T.CARD))
            p.drawRect(x - 3, y - 3, 6, 6)
            if i % label_step == 0 or i == n - 1:
                p.setPen(T.qcolor(T.MUTED))
                p.drawText(int(x - 24), top + ph + 5, 48, 16, Qt.AlignHCenter,
                           self._labels[i])
        p.end()


class BarChart(QWidget):
    """每周累计柱状图（可叠加每周目标虚线）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[int] = []
        self._refs: list[int] = []
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_series(self, labels: list[str], values: list[int], refs: list[int] | None = None):
        self._labels = list(labels)
        self._values = list(values)
        self._refs = list(refs or [])
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        left, right, top, bottom = 46, 12, 14, 28
        pw, ph = w - left - right, h - top - bottom
        n = len(self._values)
        if n == 0 or pw <= 6 or ph <= 6:
            p.end()
            return
        ymax = _nice_max(max(max(self._values + self._refs, default=0), 1))
        step = ymax / 4.0

        def ypos(v): return top + ph - ph * v / ymax

        p.setFont(_axis_font(self))
        for i in range(5):
            yy = int(ypos(i * step))
            p.setPen(QPen(T.qcolor(T.HAIRLINE), 1))
            p.drawLine(left, yy, w - right, yy)
            p.setPen(T.qcolor(T.MUTED))
            p.drawText(0, yy - 7, left - 6, 14, Qt.AlignRight | Qt.AlignVCenter,
                       f"{_fmt_h(i * step / 60)}h")
        slot = pw / n
        bar_w = max(10, slot * 0.52)
        for i, v in enumerate(self._values):
            cx = left + slot * i + slot / 2
            bh = ph * v / ymax
            x0 = cx - bar_w / 2
            target = self._refs[i] if self._refs and i < len(self._refs) else 0
            ink = T.GREEN if (target > 0 and v >= target) else T.ACCENT
            if v > 0:
                p.setPen(QPen(T.qcolor(T.BORDER), 1))
                p.setBrush(T.qcolor(ink))
                p.drawRect(int(x0), int(ypos(v)), int(bar_w),
                           max(3, int(ph - ypos(v)) + 1))
                if bh > 8:
                    p.setPen(QPen(T.qcolor(T.rgba("#FFFFFF", 0.46)), 1))
                    p.drawLine(int(x0) + 2, int(ypos(v)) + 2,
                               int(x0 + bar_w) - 2, int(ypos(v)) + 2)
            if v > 0 and bh > 14:
                label = f"{_fmt_h(v / 60)}"
                p.setFont(QFont(self.font().family(), 7.5, QFont.Bold))
                bar_top = int(ypos(v))
                if bar_top - 16 >= top:
                    # 数字画在柱子上方 → 落在玻璃底上，必须用压深的墨色（白色在这里等于看不见）
                    p.setPen(T.qcolor(T.ink(ink)))
                    p.drawText(int(x0), bar_top - 16, int(bar_w), 14,
                               Qt.AlignHCenter, label)
                elif bh > 26:
                    # 柱子顶到画布上沿时才把数字塞进柱子里，这时才是白字
                    p.setPen(T.qcolor(T.ON_ACCENT))
                    p.drawText(int(x0), bar_top + 4, int(bar_w), 14,
                               Qt.AlignHCenter | Qt.AlignTop, label)
            p.setFont(_axis_font(self))
            p.setPen(T.qcolor(T.MUTED))
            p.drawText(int(cx - slot / 2), top + ph + 6, int(slot), 16, Qt.AlignHCenter,
                       self._labels[i])
            if target > 0:
                ry = int(ypos(target))
                p.setPen(QPen(T.qcolor(T.AMBER), 1.5, Qt.DashLine))
                p.drawLine(int(x0), ry, int(x0 + bar_w), ry)
        p.end()


# ---------------------------------------------------------------- 侧边导航
class SideBar(QFrame):
    """左侧导航：品牌标（新 logo）+ 矢量图标导航 + 底部状态。"""

    page_changed = Signal(int)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("SideBar")
        self.setFixedWidth(208)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(8)

        # ---- 品牌区
        head = QHBoxLayout()
        head.setContentsMargins(16, 16, 10, 4)
        head.setSpacing(10)
        logo = QLabel()
        logo.setFixedSize(38, 38)
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(brand_pixmap(76).scaled(
            38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        name_box = QVBoxLayout()
        name_box.setSpacing(0)
        name = QLabel("LabAssistant")
        name.setObjectName("BrandName")
        sub = QLabel("实验室时间与日程助手")
        sub.setObjectName("BrandSub")
        name_box.addWidget(name)
        name_box.addWidget(sub)
        head.addWidget(logo)
        head.addSpacing(2)
        head.addLayout(name_box)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addSpacing(4)

        self.list = QListWidget()
        self.list.setObjectName("Nav")
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setIconSize(QSize(18, 18))
        self.list.viewport().setAutoFillBackground(False)
        for it in items:
            item = QListWidgetItem(it)
            item.setData(Qt.DecorationRole, nav_icon(NAV_ICONS.get(it, "gear")))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self.page_changed)
        lay.addWidget(self.list, 1)

        self.foot = QLabel("数据保存在本机\n「设置」页可备份 / 恢复")
        self.foot.setObjectName("BrandSub")
        self.foot.setContentsMargins(18, 0, 12, 0)
        self.foot.setWordWrap(True)
        lay.addWidget(self.foot)

    def set_sync_status(self, text: str, color: str | None = None):
        self.foot.setText(text)
        self.foot.setStyleSheet(
            f"color:{color or T.MUTED}; font-size:{T.FS_CAPTION}; padding:0; border:none;")

    def set_page(self, idx: int):
        self.list.setCurrentRow(idx)


def brand_pixmap(size: int = 64) -> QPixmap:
    """应用内品牌标：优先用 assets/brand_mark.png（新 logo），缺失时回退到自绘玻璃标。"""
    from pathlib import Path
    path = Path(C.asset_file("brand_mark.png"))
    if path.exists():
        pm = QPixmap(str(path))
        if not pm.isNull():
            return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(1, 1, size - 2, size - 2)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0.0, T.qcolor(T.TEAL))
    grad.setColorAt(0.5, T.qcolor(T.ACCENT))
    grad.setColorAt(1.0, T.qcolor(T.PURPLE))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(r, size * 0.26, size * 0.26)
    p.setPen(QPen(T.qcolor("rgba(255,255,255,235)"), max(1.5, size * 0.045),
                  Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    box = r.adjusted(size * 0.24, size * 0.24, -size * 0.24, -size * 0.24)
    p.drawEllipse(box)
    p.drawLine(QPointF(box.center().x(), box.center().y()),
               QPointF(box.center().x(), box.top() + box.height() * 0.22))
    p.drawLine(QPointF(box.center().x(), box.center().y()),
               QPointF(box.right() - box.width() * 0.10, box.center().y()))
    p.end()
    return pm

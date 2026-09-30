"""LabOS shared controls: crisp Platinum panels and restrained pixel accents."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from labassistant.ui import theme as T


class AuroraFrame(QFrame):
    """Compatibility name for the main Platinum desktop surface."""

    def __init__(self, parent=None, *, object_name: str = "Root"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), T.qcolor(T.BG))
        # Quiet 8px pin-dot texture recalls the classic desktop without competing with data.
        p.setPen(T.qcolor("#CAC8BE"))
        for y in range(5, self.height(), 8):
            for x in range(5, self.width(), 8):
                p.drawPoint(x, y)
        p.end()


class LabTitleBar(QFrame):
    """A small striped Mac window header above the working area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), T.qcolor("#E8E6DE"))
        p.setPen(T.qcolor("#B4B1A7"))
        for y in range(7, 26, 3):
            p.drawLine(16, y, max(16, self.width() - 16), y)
        title = "LABOS  •  LABASSISTANT"
        width = p.fontMetrics().horizontalAdvance(title) + 28
        box = QRectF((self.width() - width) / 2, 3, width, 27)
        p.fillRect(box, T.qcolor("#E8E6DE"))
        p.setPen(T.qcolor(T.TEXT))
        p.drawText(box, Qt.AlignCenter, title)
        p.setPen(T.qcolor(T.BORDER))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


def aurora_pixmaps(w: int, h: int) -> tuple[QPixmap, QPixmap]:
    """Compatibility helper; both surfaces are the solid LabOS desktop."""
    pm = QPixmap(max(1, w), max(1, h))
    pm.fill(T.qcolor(T.BG))
    return pm, pm


class GlassPanel(QFrame):
    """Compatibility name for a raised Macintosh style panel."""

    def __init__(self, parent=None, *, variant: str = "regular",
                 ink: str | None = None, radius: int | None = None,
                 shadow: bool = True, hover_lift: bool = False,
                 pad: int | None = None, object_name: str = ""):
        super().__init__(parent)
        self.variant = variant
        self.ink = ink
        self.radius = radius if radius is not None else T.RADIUS_LG
        self.shadow = shadow
        self.pad = pad if pad is not None else (4 if shadow else 1)
        self.hover_lift = hover_lift
        self._hover = False
        if object_name:
            self.setObjectName(object_name)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setMouseTracking(hover_lift)

    def enterEvent(self, e):
        if self.hover_lift:
            self._hover = True
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._hover:
            self._hover = False
            self.update()
        super().leaveEvent(e)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        pad = float(self.pad)
        body = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        if body.width() <= 1 or body.height() <= 1:
            p.end()
            return
        r = min(float(self.radius), 4.0, body.width() / 2, body.height() / 2)
        path = QPainterPath()
        path.addRoundedRect(body, r, r)
        if self.shadow and pad >= 3:
            shadow = QPainterPath()
            shadow.addRoundedRect(body.translated(2, 2), r, r)
            p.fillPath(shadow, T.qcolor("#AAA79D"))
        base = (T.GLASS_STRONG_TOP if self.variant == "strong" else
                T.GLASS_THIN_TOP if self.variant == "thin" else T.CARD)
        p.fillPath(path, T.qcolor("#FFFEFA" if self._hover else base))
        if self.ink:
            p.fillPath(path, T.qcolor(T.rgba(self.ink, 0.075)))
        p.setPen(QPen(T.qcolor(T.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        # Two straight highlight lines give the panel a raised Platinum edge.
        p.setPen(QPen(T.qcolor("#FFFFFF"), 1))
        p.drawLine(QPointF(body.left() + 2, body.top() + 2),
                   QPointF(body.right() - 2, body.top() + 2))
        p.drawLine(QPointF(body.left() + 2, body.top() + 2),
                   QPointF(body.left() + 2, body.bottom() - 2))
        p.end()


def hairline(parent_layout, *, vertical: bool = False, color: str | None = None,
             thickness: int = 1) -> QFrame:
    line = Hairline(vertical=vertical, color=color, thickness=thickness)
    parent_layout.addWidget(line)
    return line


class Hairline(QFrame):
    def __init__(self, *, vertical: bool = False, color: str | None = None,
                 thickness: int = 1, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{color or T.HAIRLINE}; border:none;")
        if vertical:
            self.setFixedWidth(thickness)
        else:
            self.setFixedHeight(thickness)


class VDivider(QFrame):
    def __init__(self, *, inset: int = 14, color: str | None = None,
                 thickness: int = 1, parent=None):
        super().__init__(parent)
        self._inset = inset
        self._color = color or T.HAIRLINE
        self.setFixedWidth(max(1, thickness))
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(T.qcolor(self._color))
        x = self.width() // 2
        p.drawLine(x, self._inset, x, max(self._inset, self.height() - self._inset))
        p.end()


class HDivider(QFrame):
    def __init__(self, *, inset: int = 16, color: str | None = None,
                 thickness: int = 1, parent=None):
        super().__init__(parent)
        self._inset = inset
        self._color = color or T.HAIRLINE
        self.setFixedHeight(max(1, thickness))
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(T.qcolor(self._color))
        y = self.height() // 2
        p.drawLine(self._inset, y, max(self._inset, self.width() - self._inset), y)
        p.end()


class GlassPill(QLabel):
    def __init__(self, text: str = "", *, ink: str = T.ACCENT, filled: bool = False,
                 parent=None):
        super().__init__(text, parent)
        self.ink = ink
        self.filled = filled
        self.setAlignment(Qt.AlignCenter)
        self._apply()

    def set_text(self, text: str):
        self.setText(text)
        self._apply()

    def _apply(self):
        bg = T.rgba(self.ink, 0.16) if not self.filled else self.ink
        fg = T.readable(self.ink) if not self.filled else T.ON_ACCENT
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {self.ink};"
            f"border-radius:{T.RADIUS_SM}px; padding:1px 9px; font-size:{T.FS_CAPTION}; font-weight:700;")


def dot(color: str, size: int = 7) -> QLabel:
    lab = QLabel()
    lab.setFixedSize(size, size)
    lab.setStyleSheet(f"background:{color}; border:1px solid {T.BORDER}; border-radius:2px;")
    return lab


class SectionHeader(QWidget):
    def __init__(self, title: str, *, ink: str = T.ACCENT, count: int | None = None,
                 hint: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(11)
        lay.addWidget(dot(ink, 10))
        lab = QLabel(title)
        lab.setStyleSheet(f"color:{T.TEXT}; font-size:{T.FS_TITLE2}; font-weight:700;")
        lay.addWidget(lab)
        if count is not None:
            c = QLabel(str(count))
            c.setStyleSheet(f"color:{T.readable(ink)}; background:{T.rgba(ink, 0.13)};"
                            f"border:1px solid {ink}; border-radius:{T.RADIUS_SM}px;"
                            f"padding:1px 8px; font-size:{T.FS_CAPTION}; font-weight:700;")
            lay.addWidget(c)
        if hint:
            h = QLabel(hint)
            h.setStyleSheet(f"color:{T.MUTED}; font-size:{T.FS_CAPTION};")
            lay.addWidget(h)
        lay.addStretch(1)


__all__ = ["AuroraFrame", "LabTitleBar", "GlassPanel", "GlassPill", "Hairline", "HDivider",
           "SectionHeader", "VDivider", "aurora_pixmaps", "dot", "hairline"]

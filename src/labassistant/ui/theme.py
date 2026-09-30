"""LabOS 主题：Platinum 风格的实验室工作站。"""

from __future__ import annotations

from PySide6.QtGui import QColor

from labassistant.constants import (
    STATE_DONE,
    STATE_HOLIDAY,
    STATE_HOLIDAY_DONE,
    STATE_NONE,
    STATE_PARTIAL,
    STATE_WEEKEND,
    STATE_WEEKEND_DONE,
)


def rgba(hex_color: str, alpha: float) -> str:
    """#RRGGBB + 0..1 → CSS rgba() 字符串（供 QSS 使用）。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {round(alpha, 3)})"

# ---------------------------------------------------------------- 可读性（WCAG）
def _rel_lum(css: str) -> float:
    c = qcolor(css)
    if not c.isValid():
        return 1.0
    def ch(v: int) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(fg: str, bg: str) -> float:
    """WCAG 2.1 相对亮度比。"""
    a, b = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# 玻璃面板上的典型底色（极光中段 + 半透明白），比纯白更保守
PANEL_BG = "#F7F5EE"


def readable(css: str, bg: str = PANEL_BG, ratio: float = 4.5) -> str:
    """沿 HSL 明度往下压，直到在 bg 上达到 ratio:1；尽量保住色相与饱和度。

    设计原则：**鲜色只用于装饰**（方块、瓦片、进度条、徽标底色），
    凡是承担信息的文字一律走这个函数，避免“好看但看不清”。
    """
    c = qcolor(css)
    if not c.isValid() or contrast(css, bg) >= ratio:
        return css
    h, sat, l, a = c.getHsl()
    best = css
    for _ in range(64):
        l = max(0, l - 22)                 # HSL 明度 0..255
        t = QColor.fromHsl(h, sat, l, a if a > 0 else 255)
        best = t.name()
        if contrast(best, bg) >= ratio:
            return best
        if l <= 0:
            break
    return QColor(23, 30, 43).name()        # 兜底：正文色


def ink(css: str) -> str:
    """小字/正文用墨色（≥4.5:1）。"""
    return readable(css, PANEL_BG, 4.5)


def ink_big(css: str) -> str:
    """大号数字用墨色（≥3.2:1，比 AA 大字号的 3.0 留一点余量）。"""
    return readable(css, PANEL_BG, 3.2)



def qcolor(css: str) -> QColor:
    """把 CSS 颜色字符串转成 QColor。

    Qt 的 `QColor("rgba(255,255,255,0.74)")` **不解析**（会得到 invalid 颜色，
    画出来是黑色），所以自绘代码一律走这个函数。
    支持：`#RGB` / `#RRGGBB` / `#RRGGBBAA` / `rgb(r,g,b)` / `rgba(r,g,b,a)` / 颜色名。
    """
    s = (css or "").strip()
    low = s.lower()
    if low.startswith("rgba(") or low.startswith("rgb("):
        try:
            inner = s[s.index("(") + 1:s.rindex(")")]
            parts = [p.strip() for p in inner.split(",")]
            r, g, b = (int(round(float(p))) for p in parts[:3])
            a = float(parts[3]) if len(parts) > 3 else 1.0
            if a <= 1.0:                      # CSS 里 alpha 是 0..1
                a = a * 255.0
            c = QColor(max(0, min(255, r)), max(0, min(255, g)),
                       max(0, min(255, b)), max(0, min(255, int(round(a)))))
            if c.isValid():
                return c
        except (ValueError, IndexError):
            pass
    c = QColor(s)
    return c if c.isValid() else QColor(0, 0, 0, 0)


# ---------------------------------------------------------------- 极光壁纸
AURORA_BASE = ("#D8D5CA", "#D8D5CA", "#D8D5CA")
AURORA_BLOBS: list[tuple[float, float, float, str, float]] = []

# ---------------------------------------------------------------- 玻璃令牌
# 不透明度刻意压低：玻璃要真的让背后的极光透出来，否则只是一张白卡片。
GLASS_FILL_TOP = "#F7F5EE"
GLASS_FILL_BOTTOM = "#F7F5EE"
GLASS_STRONG_TOP = "#FFFEF8"
GLASS_STRONG_BOTTOM = "#FFFEF8"
GLASS_THIN_TOP = "#EEECE3"
GLASS_THIN_BOTTOM = "#EEECE3"
GLASS_SPECULAR = "#FFFFFF"
GLASS_HAIRLINE = "#B5B2A9"
GLASS_EDGE_DARK = "#66665F"
GLASS_SHADOW = "#98968E"
GLASS_INNER_TOP = "#FFFFFF"

# 一套刻度，别再出现 8/9/10/11/13 混用：
#   大玻璃板 → 卡片 → 按钮/输入 → 徽标 → 图标按钮/菜单项
RADIUS_XL = 4
RADIUS_LG = 3
RADIUS_MD = 3
RADIUS_SM = 2
RADIUS_XS = 2
RADIUS_INNER = 2
RADIUS_PILL = 3


def tile_radius(px: int) -> int:
    """方形瓦片的圆角：约 30%，和 macOS 图标的观感一致。"""
    return max(2, round(px * 0.08))

# ---------------------------------------------------------------- 基础色板
BG = "#D8D5CA"
CARD = "#F7F5EE"
BORDER = "#66665F"
HAIRLINE = "#A9A79D"
TEXT = "#23292B"
TEXT_SECONDARY = "#3F4B50"
MUTED = "#586168"
ACCENT = "#236F87"
ACCENT_DARK = "#145267"
ACCENT_SOLID = "#145267"
ON_ACCENT = "#FFFFFF"
ACCENT_LIGHT = "#D9EBE9"
GREEN = "#20795F"
GREEN_INK = "#18624E"
RED = "#B84F44"
AMBER = "#875716"
PURPLE = "#735A9E"
PINK = "#9E4961"
TEAL = "#206F75"
INDIGO = "#4E639C"

# ---------------------------------------------------------------- 状态语义
# 无框设计：状态只用「文字色 + 小圆点 + 细进度条」表达，不再整格填色。
STATE_INK: dict[str, str] = {
    STATE_DONE: GREEN_INK,
    STATE_PARTIAL: AMBER,
    STATE_NONE: MUTED,
    STATE_WEEKEND: MUTED,
    STATE_HOLIDAY: PURPLE,
    STATE_WEEKEND_DONE: INDIGO,
    STATE_HOLIDAY_DONE: PURPLE,
}

# 兼容旧调用（返回 (背景, 前景, 边框)）：背景改为极淡洗色，边框几乎不可见
def _mk_state_styles(src: dict[str, str]) -> dict[str, tuple[str, str, str]]:
    return {k: (rgba(v, 0.07), readable(v, PANEL_BG, 4.5), rgba(v, 0.14))
            for k, v in src.items()}


STATE_STYLES: dict[str, tuple[str, str, str]] = _mk_state_styles(STATE_INK)

COURSE_CHIP = ("#E3E5F1", INDIGO)
LAB_CHIP = ("#DCECE3", GREEN_INK)
MANUAL_CHIP = ("#F5E8D3", "#875716")
CANCEL_CHIP = ("#F4DEDC", "#9A3F37")
TODO_INK = MUTED


def status_colors(state_key: str) -> tuple[str, str, str]:
    return STATE_STYLES.get(state_key, STATE_STYLES[STATE_NONE])


def status_ink(state_key: str) -> str:
    """日历里的日期数字 / 状态小字 / 进度条颜色：一律压到可读。"""
    return readable(STATE_INK.get(state_key, STATE_INK[STATE_NONE]), PANEL_BG, 4.5)


def status_dot(state_key: str) -> str:
    """纯装饰（圆点、色块）可以保留鲜色。"""
    return STATE_INK.get(state_key, STATE_INK[STATE_NONE])


# ---------------------------------------------------------------- 网站分类配色
class CategoryStyle:
    """一个网站类别的完整配色（前景墨色 / 玻璃洗色 / 图标瓦片渐变）。"""

    def __init__(self, key: str, label: str, ink: str, hint: str):
        self.key = key
        self.label = label
        self.ink = ink                              # 装饰：圆点 / 瓦片 / 进度
        self.ink_text = readable(ink, PANEL_BG, 4.5)  # 文字：域名 / 计数 / 首字母
        self.hint = hint
        self.tint = rgba(ink, 0.13)
        self.soft = rgba(ink, 0.06)
        self.line = rgba(ink, 0.34)
        self.tile_top = rgba(ink, 0.34)
        self.tile_bottom = rgba(ink, 0.14)
        self.glass_top = rgba(ink, 0.16)
        self.glass_bottom = rgba(ink, 0.05)


CATEGORY_STYLES: dict[str, CategoryStyle] = {
    "academic": CategoryStyle("academic", "学术", INDIGO, "论文 · 文献 · 竞赛"),
    "school":   CategoryStyle("school", "学校", TEAL, "教务 · 网课 · 校内系统"),
    "ai":       CategoryStyle("ai", "AI", PURPLE, "模型 · 平台 · 助手"),
    "tools":    CategoryStyle("tools", "工具", AMBER, "效率 · 转换 · 开发"),
    "life":     CategoryStyle("life", "生活", PINK, "购物 · 出行 · 日常"),
    "other":    CategoryStyle("other", "其他", MUTED, "未归类收藏"),
}
CATEGORY_ORDER = list(CATEGORY_STYLES)


def category_style(key: str) -> CategoryStyle:
    return CATEGORY_STYLES.get(key, CATEGORY_STYLES["other"])



# ---------------------------------------------------------------- 随手记类别配色
KIND_COLORS: dict[str, str] = {
    "credential": PINK,
    "lab": GREEN,
    "todo": ACCENT,
    "website": INDIGO,
    "holiday": PURPLE,
    "note": MUTED,
}


def kind_color(kind: str) -> str:
    return KIND_COLORS.get(kind, KIND_COLORS["note"])


# ---------------------------------------------------------------- 字号阶梯
FS_CAPTION = "8.5pt"
FS_FOOTNOTE = "9.5pt"
FS_BODY = "10.5pt"
FS_HEADLINE = "11.5pt"
FS_TITLE2 = "15pt"
FS_TITLE1 = "20pt"


def segment_css(ink_color: str, on: bool, *, padding: str = "5px 14px") -> str:
    """Shared square segmented control for month/week, capture and website filters."""
    if on:
        return (f"QPushButton {{ background:{CARD}; border:1px solid {ink_color};"
                f" border-radius:{RADIUS_INNER}px; padding:{padding};"
                f" color:{readable(ink_color)}; font-weight:700; font-size:{FS_BODY}; }}")
    return (f"QPushButton {{ background:transparent; border:1px solid transparent;"
            f" border-radius:{RADIUS_INNER}px; padding:{padding};"
            f" color:{TEXT_SECONDARY}; font-size:{FS_BODY}; }}"
            f"QPushButton:hover {{ background:#F7F5EE; border-color:{HAIRLINE}; }}")

# ---------------------------------------------------------------- LabOS QSS
QSS = f"""
* {{ font-family: "PingFang SC", "Microsoft YaHei UI", "Hiragino Sans GB", sans-serif; font-size: {FS_BODY}; }}
QWidget {{ color: {TEXT}; background: transparent; }}
QMainWindow, QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
#Root {{ background: {BG}; }}
QLabel {{ background: transparent; color: {TEXT}; }}
QLabel#PageTitle {{ font-size: {FS_TITLE1}; font-weight: 700; color: {TEXT}; }}
QLabel#PageSubtitle {{ font-size: {FS_FOOTNOTE}; color: {TEXT_SECONDARY}; }}
QLabel#SectionTitle {{ font-size: {FS_HEADLINE}; font-weight: 700; color: {TEXT}; }}
QLabel#Eyebrow {{ font-size: {FS_CAPTION}; font-weight: 700; color: {MUTED}; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Accent {{ color: {ink(ACCENT)}; }}
QLabel#Danger {{ color: {ink(RED)}; }}
QLabel#MetricValue {{ font-size: 19pt; font-weight: 700; color: {TEXT}; }}
QLabel#MetricTitle {{ font-size: {FS_CAPTION}; font-weight: 700; color: {MUTED}; }}
QLabel#MetricSub {{ font-size: {FS_CAPTION}; color: {MUTED}; }}
QFrame#Card, QFrame#CardFlat {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}
QFrame#Hairline {{ background: {HAIRLINE}; border: none; }}

/* Macintosh Platinum sidebar and selection */
QFrame#SideBar {{ background: #E8E6DE; border: none; border-right: 2px solid {BORDER}; }}
QLabel#BrandName {{ font-size: 13pt; font-weight: 700; color: {TEXT}; }}
QLabel#BrandSub {{ font-size: {FS_CAPTION}; color: {MUTED}; }}
QListWidget#Nav {{ background: transparent; border: none; outline: none; }}
QListWidget#Nav::item {{ height: 38px; border: 1px solid transparent; border-radius: {RADIUS_MD}px; margin: 2px 10px; padding-left: 12px; color: {TEXT_SECONDARY}; }}
QListWidget#Nav::item:hover {{ background: #F6F3E9; border: 1px solid {HAIRLINE}; color: {TEXT}; }}
QListWidget#Nav::item:selected {{ background: #C9E2DE; border: 1px solid {ACCENT_DARK}; color: {TEXT}; font-weight: 700; }}

/* Raised controls, inset inputs, and clear focus */
QPushButton {{ background: #EEECE4; border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; padding: 6px 16px; color: {TEXT}; min-height: 18px; }}
QPushButton:hover {{ background: #FFFEF8; border-color: {ACCENT_DARK}; }}
QPushButton:pressed {{ background: #D5D2C8; padding-top: 7px; padding-bottom: 5px; }}
QPushButton:disabled {{ color: {MUTED}; background: #E2DFD6; border-color: {HAIRLINE}; }}
QPushButton#Primary {{ background: {ACCENT_SOLID}; border: 1px solid #103F51; color: {ON_ACCENT}; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; border-color: #103F51; }}
QPushButton#Primary:pressed {{ background: #103F51; }}
QPushButton#DangerText {{ color: {ink(RED)}; }}
QPushButton#DangerText:hover {{ background: #F4DEDC; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {ink(ACCENT)}; padding: 4px 10px; }}
QPushButton#Ghost:hover {{ background: #E4EFEC; border-color: {ACCENT}; }}
QPushButton#NavBtn {{ padding: 5px 12px; }}
QPushButton#Icon {{ background: transparent; border: 1px solid transparent; padding: 3px 7px; border-radius: {RADIUS_XS}px; color: {TEXT_SECONDARY}; }}
QPushButton#Icon:hover {{ background: #EEECE4; border-color: {HAIRLINE}; color: {TEXT}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit, QTextEdit, QPlainTextEdit {{ background: #FFFEFA; border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; padding: 5px 10px; color: {TEXT}; selection-background-color: #BCDCD7; selection-color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QDateEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 2px solid {ACCENT_DARK}; background: #FFFFFF; }}
QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button, QDateEdit::up-button, QDateEdit::down-button {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: #FFFEFA; border: 1px solid {BORDER}; padding: 4px; outline: none; }}
QComboBox QAbstractItemView::item {{ padding: 5px 10px; }}
QComboBox QAbstractItemView::item:selected {{ background: #C9E2DE; color: {TEXT}; }}
QLineEdit#Search {{ padding-left: 30px; }}
QSpinBox#Inline, QComboBox#Inline, QDateEdit#Inline, QTimeEdit#Inline {{ background: transparent; border: none; padding: 0; color: {TEXT}; font-size: 11.5pt; font-weight: 700; }}
QSpinBox#Inline:focus, QComboBox#Inline:focus {{ border: none; background: transparent; }}
QComboBox#Inline::drop-down {{ border: none; width: 14px; }}
QComboBox#Inline::down-arrow {{ image: none; border-left: 3px solid transparent; border-right: 3px solid transparent; border-top: 4px solid {MUTED}; width: 0; height: 0; margin-right: 2px; }}
QPlainTextEdit#CaptureInput {{ background: #FFFEFA; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; padding: 12px 14px; font-size: 11.5pt; }}
QPlainTextEdit#CaptureInput:focus {{ border: 2px solid {ACCENT_DARK}; background: #FFFFFF; }}
QLabel#InlineUnit {{ color: {MUTED}; font-size: {FS_FOOTNOTE}; }}
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}

QTableWidget, QTableView {{ background: #FFFEFA; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; gridline-color: #D4D1C7; alternate-background-color: #F1EFE7; selection-background-color: #C9E2DE; selection-color: {TEXT}; }}
QHeaderView {{ background: #E7E4DA; }}
QHeaderView::section {{ background: #E7E4DA; color: {TEXT_SECONDARY}; border: none; border-right: 1px solid {HAIRLINE}; border-bottom: 1px solid {BORDER}; padding: 7px 10px; font-weight: 700; font-size: {FS_FOOTNOTE}; }}
QTableWidget::item {{ padding: 5px 9px; }}
QTableWidget::item:selected {{ background: #C9E2DE; color: {TEXT}; }}
QTableCornerButton::section {{ background: #E7E4DA; border: none; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {CARD}; top: -1px; }}
QTabBar::tab {{ background: #E5E2D8; color: {TEXT_SECONDARY}; padding: 8px 18px; margin-right: 2px; border: 1px solid {HAIRLINE}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {CARD}; color: {TEXT}; border-color: {BORDER}; font-weight: 700; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QScrollBar:vertical {{ background: #E4E1D8; width: 12px; margin: 1px; border: 1px solid {HAIRLINE}; }}
QScrollBar::handle:vertical {{ background: #AAA89F; border: 1px solid {BORDER}; border-radius: {RADIUS_XS}px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #929188; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: #E4E1D8; height: 12px; margin: 1px; border: 1px solid {HAIRLINE}; }}
QScrollBar::handle:horizontal {{ background: #AAA89F; border: 1px solid {BORDER}; border-radius: {RADIUS_XS}px; min-width: 28px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
QToolTip {{ background: #252C31; color: #FFFFFF; border: 1px solid #101619; padding: 5px 8px; }}
QProgressBar {{ background: #DEDCD2; border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px; min-height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: {RADIUS_SM}px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; margin-top: 12px; padding: 10px; background: {CARD}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {TEXT}; font-weight: 700; }}
QMenu {{ background: #FFFEFA; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background: #C9E2DE; color: {TEXT}; }}
"""

DIALOG_BG = CARD

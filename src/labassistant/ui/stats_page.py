"""统计页：Platinum 方框指标、完成率方格仪表与直角图表。"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QListView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from labassistant import util
from labassistant.db import Database
from labassistant.services import aggregate as agg
from labassistant.ui import theme as T
from labassistant.ui.bus import get_bus
from labassistant.ui.glass import GlassPanel, Hairline, SectionHeader
from labassistant.ui.widgets import BarChart, LineChart, RingWidget, StatBox


def _clear_layout(lay) -> None:
    """清空布局。

    注意：只调用 deleteLater() 是不够的——Qt 的事件循环**不会**在 processEvents()
    里处理 DeferredDelete，所以连续两次 refresh 之间旧控件会留在原地继续被绘制，
    表现为文字“重影”。这里先 setParent(None) 让它立刻离屏，再交给 deleteLater 回收。
    """
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        sub = it.layout()
        if w is not None:
            w.setParent(None)
            w.hide()
            w.deleteLater()
        elif sub is not None:
            _clear_layout(sub)
            sub.deleteLater()
        else:
            del it


def _chart_panel(title: str, *, ink: str = T.ACCENT, hint: str = "",
                 stretch: int = 0) -> tuple[GlassPanel, QVBoxLayout]:
    """一块带标题的工作站图表面板，返回 (面板, 内容布局)。"""
    panel = GlassPanel(variant="regular", radius=T.RADIUS_XL)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(18, 14, 18, 12)
    lay.setSpacing(8)
    lay.addWidget(SectionHeader(title, ink=ink, hint=hint))
    lay.addWidget(Hairline())
    return panel, lay


class StatsPage(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setObjectName("Root")
        self._build()
        t = date.today()
        self.year_ed.setValue(t.year)
        self.month_combo.setCurrentIndex(t.month - 1)
        get_bus().changed.connect(self._on_changed)
        self.refresh()

    def _build(self):
        """页面可滚动，小窗口下图表仍保持可读高度。"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("Root")
        scroll.setWidget(content)
        outer.addWidget(scroll)

        head_row = QHBoxLayout()
        head_row.setSpacing(12)
        box = QVBoxLayout()
        box.setSpacing(0)
        title = QLabel("统计")
        title.setObjectName("PageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("PageSubtitle")
        box.addWidget(title)
        box.addWidget(self.subtitle)
        head_row.addLayout(box)
        head_row.addStretch(1)

        # 年月选择器采用 Platinum 工具栏的紧凑外框。
        picker = GlassPanel(variant="thin", radius=T.RADIUS_PILL, shadow=False)
        pk = QHBoxLayout(picker)
        pk.setContentsMargins(16, 7, 8, 7)
        pk.setSpacing(0)
        self.year_ed = QSpinBox()
        self.year_ed.setRange(2000, 2100)
        self.year_ed.setFixedWidth(62)
        self.year_ed.setButtonSymbols(QSpinBox.NoButtons)
        self.year_ed.setSuffix("")
        self.year_ed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.year_ed.setObjectName("Inline")
        pk.addWidget(self.year_ed)
        y_lab = QLabel(" 年")
        y_lab.setObjectName("InlineUnit")
        pk.addWidget(y_lab)
        pk.addSpacing(12)
        self.month_combo = QComboBox()
        self.month_combo.setObjectName("Inline")
        for i in range(1, 13):
            self.month_combo.addItem(f"{i}", i)
        self.month_combo.setView(QListView())
        self.month_combo.view().setObjectName("GlassPopup")
        self.month_combo.setFixedWidth(40)
        pk.addWidget(self.month_combo)
        m_lab = QLabel(" 月")
        m_lab.setObjectName("InlineUnit")
        pk.addWidget(m_lab)
        pk.addSpacing(10)
        b_now = QPushButton("本月")
        b_now.setObjectName("Ghost")
        b_now.setCursor(Qt.PointingHandCursor)
        pk.addWidget(b_now)
        head_row.addWidget(picker)

        outer_lay = QVBoxLayout(content)
        outer_lay.setContentsMargins(22, 18, 22, 14)
        outer_lay.setSpacing(12)
        outer_lay.addLayout(head_row)

        self.year_ed.valueChanged.connect(lambda _v: self.refresh())
        self.month_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        b_now.clicked.connect(self._go_current)

        # ---- 月度指标：独立的方框读数，方便快速扫描。
        self.cards_panel = GlassPanel(variant="strong", radius=T.RADIUS_XL)
        cards_wrap = QVBoxLayout(self.cards_panel)
        cards_wrap.setContentsMargins(14, 12, 14, 14)
        cards_wrap.setSpacing(10)
        cards_wrap.addWidget(SectionHeader("月度指标", ink=T.ACCENT))
        cards_wrap.addWidget(Hairline())
        self.cards = QVBoxLayout()
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(7)
        cards_wrap.addLayout(self.cards)
        outer_lay.addWidget(self.cards_panel)

        # ---- 图表区
        charts = QHBoxLayout()
        charts.setSpacing(14)

        line_card, lc_lay = _chart_panel("每日有效时间", ink=T.ACCENT)
        self.line_chart = LineChart()
        lc_lay.addWidget(self.line_chart, 1)
        charts.addWidget(line_card, 5)

        ring_card, rc_lay = _chart_panel("完成率方格", ink=T.GREEN)
        self.ring = RingWidget()
        rc_lay.addWidget(self.ring, 1)
        self.ring_note = QLabel("")
        self.ring_note.setAlignment(Qt.AlignCenter)
        self.ring_note.setWordWrap(True)
        self.ring_note.setStyleSheet(
            f"color:{T.TEXT_SECONDARY}; font-size:{T.FS_FOOTNOTE}; background:transparent;")
        rc_lay.addWidget(self.ring_note)
        charts.addWidget(ring_card, 2)
        outer_lay.addLayout(charts, 4)

        bar_card, bc_lay = _chart_panel("每周累计", ink=T.GREEN)
        self.bar_chart = BarChart()
        bc_lay.addWidget(self.bar_chart, 1)
        outer_lay.addWidget(bar_card, 4)


    def _go_current(self):
        t = date.today()
        self.year_ed.setValue(t.year)
        self.month_combo.setCurrentIndex(t.month - 1)

    def _clear_cards(self):
        _clear_layout(self.cards)

    def _on_changed(self):
        if self.isVisible():
            self.refresh()

    def refresh(self):
        y = self.year_ed.value()
        m = self.month_combo.currentIndex() + 1
        m_sum = agg.month_summary(self.db, y, m)
        req, eff = m_sum["required_min"], m_sum["effective_min"]
        rate = eff / req if req else None
        diff = req - eff

        self._clear_cards()
        cards = [
            ("要求时间", util.fmt_hours(req) + "h", "", T.TEXT),
            ("完成时间", util.fmt_hours(eff) + "h", "",
             T.GREEN_INK),
            ("剩余 / 超额",
             (f"还差 {util.fmt_hm(diff)}" if diff > 0
              else (f"超额 +{util.fmt_hm(-diff)}" if diff < 0 else "正好达标")),
             "",
             T.RED if diff > 0 else (T.GREEN_INK if diff < 0 else T.TEXT)),
            ("完成率", util.fmt_percent(eff, req), "达标线 100%",
             T.GREEN_INK if rate is not None and rate >= 1 else T.ACCENT),
            ("平均每日", (f"{util.fmt_hours(eff / m_sum['workday_count'], 2)}h"
                          if m_sum["workday_count"] else "0h"),
             "完成 / 工作日数", T.TEXT),
            ("平均每周", f"{util.fmt_hours(eff / m_sum['weeks_in_month'], 1)}h",
             "完成 / 周数", T.TEXT),
            ("课程贡献", util.fmt_hours(m_sum["course_min"]) + "h",
             "计入打卡的课程（已按天去重）", T.INDIGO),
            ("实验室实际", util.fmt_hours(m_sum["lab_min"]) + "h",
             "实验时间段（已按天去重）", T.GREEN_INK),
            ("手动时长", util.fmt_hours(m_sum["manual_min"]) + "h",
             "直接填写总时长", T.AMBER),
            ("重叠去重", util.fmt_hours(m_sum["overlap_min"]) + "h",
             "课程与实验室重叠部分", T.PURPLE),
        ]
        per_row = 5
        rows = [cards[i:i + per_row] for i in range(0, len(cards), per_row)]
        for row in rows:
            hbox = QHBoxLayout()
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(7)
            for tt, val, sub, color in row:
                box = StatBox(tt)
                box.setObjectName("StatsMetric")
                box.setStyleSheet(
                    f"QFrame#StatsMetric {{ background:{T.CARD}; border:1px solid {T.BORDER};"
                    "border-radius:1px; }")
                box.set_value(val, color)
                box.set_sub(sub)
                hbox.addWidget(box, 1)
            # 列数不足时补齐，保证每列等宽
            for _ in range(len(row), per_row):
                hbox.addStretch(1)
            self.cards.addLayout(hbox)

        self.subtitle.setText(
            f"{y} 年 {m} 月 · 工作日 {m_sum['workday_count']} 天 · "
            f"要求 {util.fmt_hours(req)}h / 完成 {util.fmt_hours(eff)}h")

        # 方格仪表
        self.ring.set_ratio(rate)
        if rate is None:
            self.ring_note.setText("本月无工作日要求（或目标为 0）")
        elif rate >= 1:
            self.ring_note.setText(f"已完成目标，超额 {util.fmt_hm(-diff)}")
        else:
            self.ring_note.setText(f"还差 {util.fmt_hm(diff)}")

        # 折线：所选月每天
        labels = [str(s["date"].day) for s in m_sum["days"]]
        values = [s["effective_min"] for s in m_sum["days"]]
        self.line_chart.set_series(labels, values)

        # 柱状：最近 8 周（只关心实时进度，故相对今天）
        wks = agg.weekly_totals(self.db, weeks_before=8)
        wl = [f"{w['monday'].month}/{w['monday'].day}" for w in wks]
        wv = [w["effective_min"] for w in wks]
        wr = [w["required_min"] for w in wks]
        self.bar_chart.set_series(wl, wv, wr)

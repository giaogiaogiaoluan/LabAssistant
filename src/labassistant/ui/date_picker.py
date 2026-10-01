"""统一配置所有表单日期弹窗。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDateTimeEdit

from labassistant.ui import theme as T


def configure_date_picker(editor: QDateTimeEdit) -> None:
    """修正 Qt 默认日历过小、导航栏文字难辨且与首页周起点不一致的问题。"""
    editor.setCalendarPopup(True)
    calendar = editor.calendarWidget()
    calendar.setFirstDayOfWeek(Qt.Monday)
    calendar.setMinimumSize(320, 280)
    calendar.setStyleSheet(f"""
        QCalendarWidget {{ background:#FFFEFA; color:{T.TEXT}; border:1px solid {T.BORDER}; }}
        QWidget#qt_calendar_navigationbar {{
            background:#E7E4DA; min-height:36px; border-bottom:1px solid {T.BORDER};
        }}
        QCalendarWidget QToolButton {{
            color:{T.TEXT}; background:transparent; border:0;
            padding:4px 6px; font-weight:700; min-height:24px;
        }}
        QCalendarWidget QToolButton:hover {{ background:#D5E8E4; }}
        QCalendarWidget QAbstractItemView {{
            background:#FFFEFA; color:{T.TEXT};
            selection-background-color:#BCDCD7; selection-color:{T.TEXT}; outline:none;
        }}
        QCalendarWidget QHeaderView::section {{
            background:#E7E4DA; color:{T.TEXT}; border:0; padding:4px 2px;
        }}
    """)

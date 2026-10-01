"""展示格式化与通用小工具（纯函数，不依赖 Qt）。"""

from __future__ import annotations

from datetime import date, datetime, time


def fmt_hm(total_min: int) -> str:
    """1h35min / 8h / 45min / 0min 风格（带符号支持负数）。"""
    if abs(float(total_min) - round(float(total_min))) > 1e-7:
        return f"{fmt_hours(total_min, 2)}h"
    sign = "-" if total_min < 0 else ""
    t = abs(int(total_min))
    h, m = divmod(t, 60)
    if h and m:
        return f"{sign}{h}h {m}min"
    if h:
        return f"{sign}{h}h"
    return f"{sign}{m}min"


def fmt_hours(mins: int | float, decimals: int = 2) -> str:
    """分钟 -> 小时文本：480 -> '8'，450 -> '7.5'，7.5h -> '7.5'。"""
    v = float(mins) / 60.0
    s = f"{v:.{decimals}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def fmt_hm_hours(mins: int, decimals: int = 1) -> str:
    """给小时数补 'h' 后缀的便捷方法。"""
    return f"{fmt_hours(mins, decimals)}h"


def fmt_percent(part: int | float, whole: int | float, decimals: int = 1) -> str:
    if whole is None or float(whole) == 0:
        return "-"
    return f"{float(part) / float(whole) * 100:.{decimals}f}%"


def parse_deadline(s: str) -> datetime | None:
    """解析 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'，失败返回 None。"""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def is_overdue(deadline: str, done: bool) -> bool:
    if done or not deadline:
        return False
    dt = parse_deadline(deadline)
    return dt is not None and dt < datetime.now()


def clock_text(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    m = month - 1 + delta
    return year + m // 12, m % 12 + 1

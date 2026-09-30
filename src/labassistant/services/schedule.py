"""工作日 / 周末 / 节假日 与月度目标计算（纯函数）。"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from labassistant.constants import WEEKDAYS_CN


def parse_date(s: str) -> date:
    return date.fromisoformat(str(s))


def parse_workdays_csv(csv_str: str) -> set[int]:
    """'0,1,2,3,4' -> {0,1,2,3,4}（0=周一）。"""
    out: set[int] = set()
    for part in str(csv_str).split(","):
        part = part.strip()
        if part:
            out.add(int(part))
    return out


def day_kind(d: date, workdays: set[int], holidays: set[date]) -> str:
    """返回 'holiday' | 'weekend' | 'workday'（节假日优先级最高）。"""
    if d in holidays:
        return "holiday"
    if d.weekday() not in workdays:
        return "weekend"
    return "workday"


def is_workday(d: date, workdays: set[int], holidays: set[date]) -> bool:
    return day_kind(d, workdays, holidays) == "workday"


def month_range(year: int, month: int) -> tuple[int, int]:
    """返回 (当月天数, 当月1号的星期)。"""
    first_wd, nd = calendar.monthrange(year, month)
    return nd, first_wd


def iter_month_dates(year: int, month: int):
    nd, _ = month_range(year, month)
    for day in range(1, nd + 1):
        yield date(year, month, day)


def month_workday_count(year: int, month: int, workdays: set[int], holidays: set[date]) -> int:
    return sum(1 for d in iter_month_dates(year, month) if is_workday(d, workdays, holidays))


def required_for_day(
    d: date, workdays: set[int], holidays: set[date], daily_required_min: int
) -> int:
    return daily_required_min if is_workday(d, workdays, holidays) else 0


def month_required_minutes(
    year: int, month: int, workdays: set[int], holidays: set[date], daily_required_min: int
) -> int:
    return month_workday_count(year, month, workdays, holidays) * daily_required_min


def week_start(d: date) -> date:
    """所在周的周一。"""
    return d - timedelta(days=d.weekday())


def week_dates(ref: date) -> list[date]:
    """ref 所在周（周一~周日）7 天。"""
    start = week_start(ref)
    return [start + timedelta(days=i) for i in range(7)]


def weekday_cn(index: int) -> str:
    return WEEKDAYS_CN[index % 7]

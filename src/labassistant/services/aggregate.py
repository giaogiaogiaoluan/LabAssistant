"""按“天 / 周 / 月”聚合所有来源，输出 UI 与统计所需的统一摘要。

所有时间内部以“分钟”整数计算，只在展示层转成 小时/分钟 文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from labassistant import util
from labassistant.db import Database
from labassistant.services import attendance as att
from labassistant.services import courses as crs
from labassistant.services import holidays as hds
from labassistant.services import schedule as sch
from labassistant.services import timing
from labassistant.services import todos as tds


@dataclass
class Cfg:
    daily_min: int = 480
    workdays: set[int] = field(default_factory=lambda: {0, 1, 2, 3, 4})


# 允许测试注入“今天”（不改动全局时钟）
_TODAY = date.today


def _today() -> date:
    return _TODAY()


def override_today_for_tests(d: date | None = None):
    """仅供测试使用：把“今天”固定到 d，用于构造确定的过去/未来。"""
    global _TODAY
    if d is None:
        _TODAY = date.today
    else:
        _TODAY = lambda: d


def load_cfg(db: Database) -> Cfg:
    return Cfg(daily_min=db.daily_required_minutes(), workdays=db.workdays_set())


# ---------------- 状态语义（键 + 文案） ----------------

def status_of(kind: str, effective_min: int, required_min: int) -> tuple[str, str]:
    """返回 (状态键, 短标签)。按键在 UI 主题里映射颜色。"""
    from labassistant.constants import (
        STATE_DONE, STATE_HOLIDAY, STATE_HOLIDAY_DONE, STATE_NONE,
        STATE_PARTIAL, STATE_WEEKEND, STATE_WEEKEND_DONE,
    )

    if kind == "holiday":
        if effective_min > 0:
            return STATE_HOLIDAY_DONE, f"节假日 · 已打卡 {util.fmt_hours(effective_min)}h"
        return STATE_HOLIDAY, "节假日"
    if kind == "weekend":
        if effective_min > 0:
            return STATE_WEEKEND_DONE, f"周末 · 已打卡 {util.fmt_hours(effective_min)}h"
        return STATE_WEEKEND, "周末"
    # workday
    if effective_min >= required_min:
        return STATE_DONE, "已完成"
    if effective_min > 0:
        return STATE_PARTIAL, "未完成"
    return STATE_NONE, "未打卡"


# ---------------- 单日摘要 ----------------

def day_summary(
    db: Database,
    d: date,
    cfg: Cfg | None = None,
    holidays: set[date] | None = None,
    makeups: set[date] | None = None,
) -> dict:
    cfg = cfg or load_cfg(db)
    holidays = holidays if holidays is not None else hds.holiday_set(db)
    makeups = makeups if makeups is not None else hds.makeup_set(db)
    ds = d.isoformat()

    blocks = att.list_blocks(db, ds)
    manual = att.list_manual(db, ds)
    occ = crs.occurrences_on(db, d)

    kind = sch.day_kind(d, cfg.workdays, holidays, makeups)
    holiday_name = ""
    special_type = ""
    if kind == "holiday" or d in makeups:
        hrow = hds.get_holiday(db, ds)
        name = (hrow or {}).get("name", "")
        holiday_name = hds.rule_label(name)
        special_type = hds.rule_type(name) if name else ""

    required_min = sch.required_for_day(d, cfg.workdays, holidays, cfg.daily_min, makeups)

    lab_pairs = [(b["start_min"], b["end_min"]) for b in blocks]
    course_pairs = [
        (o["disp_start_min"], o["disp_end_min"])
        for o in occ
        if kind == "workday" and o["state"] != "cancelled" and o["count_attendance"]
    ]
    lab_min = timing.merged_duration(lab_pairs)
    course_min = timing.merged_duration(course_pairs)
    union_min = timing.merged_duration(lab_pairs + course_pairs)
    overlap_min = max(0, lab_min + course_min - union_min)
    manual_min = sum(m["minutes"] for m in manual)
    effective_full = union_min + manual_min

    # 未来日期：仅作“安排参考”，时间不计入“已完成”
    future = d > _today()
    effective_min = 0 if future else effective_full
    status_key, status_label = status_of(kind, effective_min, required_min)
    if future:
        status_label = "未开始"

    return {
        "date": d,
        "iso": ds,
        "kind": kind,
        "holiday_name": holiday_name,
        "special_type": special_type,
        "future": future,
        "required_min": required_min,
        "lab_blocks": blocks,
        "manual_items": manual,
        "occ": occ,
        "todos": tds.list_todos(db, ds),
        "lab_min": lab_min,
        "course_min": course_min,
        "manual_min": manual_min,
        "overlap_min": overlap_min,
        "effective_min": effective_min,
        "status_key": status_key,
        "status_label": status_label,
        "is_today": d == _today(),
    }


# ---------------- 周 ----------------

def week_summary(db: Database, ref: date) -> dict:
    dates = sch.week_dates(ref)
    days = [day_summary(db, d) for d in dates]
    return {
        "ref": ref,
        "dates": dates,
        "days": days,
        "required_total": sum(x["required_min"] for x in days),
        "effective_total": sum(x["effective_min"] for x in days),
        "lab_total": sum(x["lab_min"] for x in days),
        "course_total": sum(x["course_min"] for x in days),
        "manual_total": sum(x["manual_min"] for x in days),
    }


def days_in_range(db: Database, start: date, end: date) -> list[dict]:
    out = []
    cur = start
    while cur <= end:
        out.append(day_summary(db, cur))
        cur += timedelta(days=1)
    return out


def weekly_totals(db: Database, weeks_before: int = 9, ref: date | None = None) -> list[dict]:
    """返回最近 n 个完整周的汇总（以周一为一周起点，含本周）。"""
    ref = ref or date.today()
    this_monday = sch.week_start(ref)
    out = []
    for i in range(weeks_before - 1, -1, -1):
        monday = this_monday - timedelta(weeks=i)
        wk = week_summary(db, monday)
        out.append(
            {
                "monday": monday,
                "sunday": monday + timedelta(days=6),
                "effective_min": wk["effective_total"],
                "required_min": wk["required_total"],
            }
        )
    return out


# ---------------- 月 ----------------

def month_summary(db: Database, year: int, month: int) -> dict:
    holidays = hds.holiday_set(db)
    makeups = hds.makeup_set(db)
    cfg = load_cfg(db)
    workdays = cfg.workdays
    workday_count = sch.month_workday_count(year, month, workdays, holidays, makeups)
    required_total = workday_count * cfg.daily_min

    days: list[dict] = []
    lab_total = course_total = manual_total = overlap_total = effective_total = 0
    scheduled_course_total = 0
    for d in sch.iter_month_dates(year, month):
        s = day_summary(db, d, cfg, holidays, makeups)
        days.append(s)
        scheduled_course_total += s["course_min"]
        if s["future"]:
            continue  # 未来日期只做安排展示，不提前计入“已完成”
        lab_total += s["lab_min"]
        course_total += s["course_min"]
        manual_total += s["manual_min"]
        overlap_total += s["overlap_min"]
        effective_total += s["effective_min"]

    # 月份内跨过的周数（用于“每周平均”）
    week_ids = {d.isocalendar()[1] for d in sch.iter_month_dates(year, month)}
    weeks_in_month = max(1, len(week_ids))

    return {
        "year": year,
        "month": month,
        "workday_count": workday_count,
        "required_min": required_total,
        "effective_min": effective_total,
        "lab_min": lab_total,
        "course_min": course_total,
        "scheduled_course_min": scheduled_course_total,
        "remaining_after_courses_min": max(0, required_total - scheduled_course_total),
        "manual_min": manual_total,
        "overlap_min": overlap_total,
        "days": days,
        "weeks_in_month": weeks_in_month,
    }

"""示例数据（首次启动自动载入一次，可随时在“设置-清除示例数据”删除）。"""

from __future__ import annotations

from datetime import date, timedelta

from labassistant.db import Database, now_text
from labassistant.services import attendance as att
from labassistant.services import courses as crs
from labassistant.services import holidays as hds
from labassistant.services import schedule as sch
from labassistant.services import todos as tds


def has_sample(db: Database) -> bool:
    for table in ("courses", "attendance_blocks", "manual_hours", "todos", "holidays"):
        r = db.query_one(f"SELECT COUNT(*) AS c FROM {table} WHERE is_sample=1")
        if r and r["c"]:
            return True
    return False


def load_sample_data(db: Database) -> int:
    """载入示例数据（幂等：已有示例则不重复）。返回新增行数估计。"""
    if has_sample(db):
        return 0
    added = 0
    today = date.today()
    monday = sch.week_start(today)
    end = monday + timedelta(weeks=17, days=6)

    # 两门每周重复课程（默认计入打卡）
    added += crs.add_course(
        db, "示例课程 A", 0, 9 * 60 + 50, 11 * 60 + 25, monday, end,
        location="教学楼 A", teacher="教师 A",
        note="示例课程", count_attendance=True, is_sample=1,
    )
    added += crs.add_course(
        db, "示例课程 B", 2, 14 * 60, 15 * 60 + 35, monday, end,
        location="教学楼 B", teacher="教师 B",
        note="示例课程", count_attendance=True, is_sample=1,
    )

    # 往前找 4 个“工作日”作为示例打卡日
    workdays: list[date] = []
    cur = today - timedelta(days=1)
    while len(workdays) < 4 and cur >= today - timedelta(days=30):
        if cur.weekday() < 5:
            workdays.append(cur)
        cur -= timedelta(days=1)
    workdays.reverse()
    for i, d in enumerate(workdays):
        # 前两三天给足 8h，最后一天给半天，模拟不同状态
        if d == workdays[-1]:
            att.add_block(db, d.isoformat(), 8 * 60 + 30, 12 * 60, "上午（示例）", is_sample=1)
        else:
            att.add_block(db, d.isoformat(), 8 * 60 + 20, 12 * 60, "上午（示例）", is_sample=1)
            att.add_block(db, d.isoformat(), 13 * 60 + 30, 17 * 60 + 30, "下午（示例）", is_sample=1)
        added += 1

    # 找一个本月内的周六，模拟“周末主动去实验室打卡 3h”
    sat = today - timedelta(days=today.weekday() + 2)  # 上周六
    if sat.day >= 1 and sat < today:
        att.add_block(db, sat.isoformat(), 9 * 60, 12 * 60, "周末加班（示例）", is_sample=1)
        added += 1

    # 示例 Todo
    t_today = today.isoformat()
    t_tomorrow = (today + timedelta(days=1)).isoformat()
    added += tds.add_todo(
        db, t_today, "阅读一篇论文", done=False, est_minutes=120,
        priority="高", deadline=f"{t_today} 22:00", note="整理研究问题和方法", is_sample=1,
    )
    added += tds.add_todo(
        db, t_today, "整理实验数据", done=False, est_minutes=60, priority="中",
        deadline=f"{t_today} 18:00", note="", is_sample=1,
    )
    added += tds.add_todo(
        db, t_tomorrow, "写周报", done=False, est_minutes=90, priority="中",
        deadline=f"{t_tomorrow} 20:00", note="", is_sample=1,
    )

    # 示例节假日（近半年内找国庆/元旦这类周末之外日期意义不大，直接给固定示例并标记 sample）
    fixed = []
    for y, m, day, nm in ((2026, 10, 1, "国庆节(示例)"), (2026, 10, 2, "国庆节(示例)")):
        try:
            d = date(y, m, day)
        except ValueError:
            continue
        if abs((d - today).days) <= 180 or d.year == today.year:
            fixed.append((d.isoformat(), nm))
    hds.add_holidays_batch(db, fixed)

    db.set_setting("sample_loaded", "1")
    db.commit()
    return added


def clear_sample_data(db: Database) -> None:
    # 注：course_exceptions 无 is_sample 列，随课程级联删除即可
    for table in ("courses", "attendance_blocks", "manual_hours", "todos", "holidays"):
        db.conn.execute(f"DELETE FROM {table} WHERE is_sample=1")
    db.commit()

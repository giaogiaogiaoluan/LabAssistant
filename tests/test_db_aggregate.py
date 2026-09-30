"""数据库 + 聚合：验收场景级测试（打卡/课程/节假日/统计）。"""

import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from labassistant.db import Database
from labassistant.services import aggregate as agg
from labassistant.services import attendance as att
from labassistant.services import courses as crs
from labassistant.services import holidays as hds
from labassistant.services import schedule as sch
from labassistant.services import seed
from labassistant.services import todos as tds

M = 60

# 固定“今天”为 2026-12-31，使下面固定的 2026-09 日期全部视为“过去/今天”
agg.override_today_for_tests(date(2026, 12, 31))

_TMP = Path(__file__).resolve().parent / ".tmpdb"
_TMP.mkdir(exist_ok=True)


def make_db() -> Database:
    import uuid
    return Database(_TMP / f"t_{uuid.uuid4().hex[:10]}.db")


def add_course(db, name, wd, s, e, d0, d1, counted=True):
    return crs.add_course(db, name, wd, s, e, d0, d1,
                          location="X", teacher="T", count_attendance=counted)


def test_验收_周一完成8h():
    db = make_db()
    monday = date(2026, 9, 7)
    assert monday.weekday() == 0
    add_course(db, "课程", 0, 10 * M, 12 * M, monday, monday)
    att.add_block(db, monday.isoformat(), 8 * M, 11 * M)
    att.add_block(db, monday.isoformat(), 13 * M, 17 * M)
    s = agg.day_summary(db, monday)
    assert s["effective_min"] == 8 * M
    assert s["required_min"] == 8 * M
    assert s["status_key"] == "done"
    db.close()


def test_周末无要求但计入累计():
    db = make_db()
    sat = date(2026, 9, 12)
    assert sat.weekday() == 5
    att.add_block(db, sat.isoformat(), 9 * M, 12 * M)
    s = agg.day_summary(db, sat)
    assert s["required_min"] == 0
    assert s["effective_min"] == 3 * M
    assert s["kind"] == "weekend"
    assert s["status_key"] == "weekend_done"
    # 月累计包含周末的 3h
    m = agg.month_summary(db, 2026, 9)
    assert m["effective_min"] == 3 * M
    db.close()


def test_节假日使要求归零但时间仍计入():
    db = make_db()
    mon = date(2026, 9, 7)
    hds.add_holiday(db, mon.isoformat(), "测试节假日")
    att.add_block(db, mon.isoformat(), 8 * M, 13 * M)  # 5h
    s = agg.day_summary(db, mon)
    assert s["kind"] == "holiday"
    assert s["required_min"] == 0
    assert s["effective_min"] == 5 * M
    assert s["status_key"] == "holiday_done"
    m = agg.month_summary(db, 2026, 9)
    assert m["effective_min"] >= 5 * M
    assert m["required_min"] < 22 * 8 * M  # 减少了一个工作日
    db.close()


def test_重叠去重_课程与实验室():
    db = make_db()
    d = date(2026, 9, 7)
    add_course(db, "重叠课", 0, 10 * M, 12 * M, d, d)
    att.add_block(db, d.isoformat(), 8 * M, 11 * M)
    s = agg.day_summary(db, d)
    # 实验室3h + 课程2h，重叠1h -> 有效4h
    assert s["lab_min"] == 3 * M
    assert s["course_min"] == 2 * M
    assert s["overlap_min"] == 1 * M
    assert s["effective_min"] == 4 * M
    db.close()


def test_手动时长单独计入():
    db = make_db()
    d = date(2026, 9, 8)
    att.add_block(db, d.isoformat(), 8 * M, 10 * M)
    att.add_manual(db, d.isoformat(), 90, "晚上在家看文献")
    s = agg.day_summary(db, d)
    assert s["manual_min"] == 90
    assert s["effective_min"] == 2 * M + 90
    assert len(s["manual_items"]) == 1
    db.close()


def test_不计入打卡的课程():
    db = make_db()
    d = date(2026, 9, 7)
    add_course(db, "讲座不计时", 0, 10 * M, 12 * M, d, d, counted=False)
    s = agg.day_summary(db, d)
    assert s["course_min"] == 0
    assert s["effective_min"] == 0
    assert len(s["occ"]) == 1
    db.close()


def test_课程单次取消与调整():
    db = make_db()
    d = date(2026, 9, 7)
    cid = add_course(db, "课A", 0, 10 * M, 12 * M, d, d)
    # 取消本次
    crs.upsert_exception(db, cid, d.isoformat(), "cancelled", note="临时取消")
    s = agg.day_summary(db, d)
    assert s["effective_min"] == 0
    assert s["occ"][0]["state"] == "cancelled"
    # 恢复为临时调整时间（同一天其他时间段仍在）
    crs.upsert_exception(db, cid, d.isoformat(), "moved", 14 * M, 16 * M, note="调到下午")
    s = agg.day_summary(db, d)
    assert s["effective_min"] == 2 * M
    assert s["occ"][0]["state"] == "moved"
    assert s["occ"][0]["disp_start_min"] == 14 * M
    # 删除例外 -> 恢复正常
    crs.remove_exception(db, cid, d.isoformat())
    s = agg.day_summary(db, d)
    assert s["effective_min"] == 2 * M
    assert s["occ"][0]["state"] == "normal"
    db.close()


def test_周重复课程只在范围内出现():
    db = make_db()
    d0 = date(2026, 9, 7)   # 周一
    d1 = date(2026, 10, 5)  # 周一
    add_course(db, "每周课", 0, 9 * M + 50, 11 * M + 25, d0, d1)
    assert len(crs.occurrences_on(db, d0)) == 1
    assert len(crs.occurrences_on(db, d0 + timedelta(weeks=4))) == 1  # 10月5日
    # 范围外的周一没有
    assert crs.occurrences_on(db, d1 + timedelta(weeks=1)) == []
    # 非周一的同范围日期没有
    assert crs.occurrences_on(db, date(2026, 9, 8)) == []
    db.close()


def test_月度统计_超额():
    db = make_db()
    # 用一个固定 9 月（无节假日，22 个工作日 -> 176h 要求）
    dates = [d for d in sch.iter_month_dates(2026, 9) if d.weekday() < 5]
    for d in dates:
        att.add_block(db, d.isoformat(), 8 * M, 17 * M)  # 9h/天
    m = agg.month_summary(db, 2026, 9)
    assert m["required_min"] == 22 * 8 * M
    assert m["effective_min"] == 22 * 9 * M
    assert m["effective_min"] > m["required_min"]  # 超额不被限制在 100%
    db.close()


def test_todo_crud():
    db = make_db()
    d = date(2026, 9, 8)
    tid = tds.add_todo(db, d.isoformat(), "阅读论文", est_minutes=120,
                       priority="高", deadline="2026-09-10 22:00", note="n")
    row = tds.get_todo(db, tid)
    assert row["title"] == "阅读论文"
    assert row["priority"] == "高"
    tds.set_done(db, tid, True)
    assert tds.get_todo(db, tid)["done"] == 1
    tds.update_todo(db, tid, d.isoformat(), "改名", True, 60, "低", "", "新备注")
    row = tds.get_todo(db, tid)
    assert row["title"] == "改名" and row["priority"] == "低"
    tds.delete_todo(db, tid)
    assert tds.get_todo(db, tid) is None
    db.close()


def test_示例数据_载入清除幂等():
    db = make_db()
    n1 = seed.load_sample_data(db)
    assert n1 > 0
    n2 = seed.load_sample_data(db)
    assert n2 == 0  # 幂等
    assert seed.has_sample(db)
    seed.clear_sample_data(db)
    assert not seed.has_sample(db)
    # 全库示例清理后业务表里不应残留 is_sample 行
    for table in ("courses", "attendance_blocks", "manual_hours", "todos", "holidays"):
        r = db.query_one(f"SELECT COUNT(*) AS c FROM {table} WHERE is_sample=1")
        assert r["c"] == 0
    db.close()


def test_备份与恢复():
    db = make_db()
    d = date(2026, 9, 8)
    att.add_block(db, d.isoformat(), 8 * M, 10 * M)
    tmp = _TMP / "backup.db"
    db.backup_to(tmp)
    assert Database.is_valid_labassistant_db(tmp)
    # 改坏再恢复
    db.execute("DELETE FROM attendance_blocks")
    assert len(att.list_blocks(db, d.isoformat())) == 0
    db.restore_from(tmp)
    assert len(att.list_blocks(db, d.isoformat())) == 1
    db.close()


def test_非法文件无法恢复():
    db = make_db()
    tmp = _TMP / "bad.db"
    tmp.write_text("not a database")
    try:
        db.restore_from(tmp)
        assert False, "应当抛出错误"
    except (sqlite3.Error, ValueError):
        pass
    db.close()


def test_跨午夜区间聚合():
    db = make_db()
    d = date(2026, 9, 11)
    att.add_block(db, d.isoformat(), 22 * M, 1 * M)  # 22:00 - 次日01:00
    s = agg.day_summary(db, d)
    assert s["effective_min"] == 3 * M
    db.close()


def test_未来日期不计入已完成():
    agg.override_today_for_tests(date(2026, 9, 7))  # “今天”= 9/7
    try:
        db = make_db()
        wed = date(2026, 9, 9)  # 未来（周三）
        add_course(db, "周三课", 2, 10 * M, 12 * M, wed, wed)
        s = agg.day_summary(db, wed)
        assert s["future"] is True
        assert s["effective_min"] == 0          # 未来课程不提前计入
        assert s["course_min"] == 2 * M         # 但课程安排信息保留
        m = agg.month_summary(db, 2026, 9)
        assert m["course_min"] == 0             # 月度课程贡献不包含未来
        # 切回未来覆盖避免影响其它用例
        db.close()
    finally:
        agg.override_today_for_tests(date(2026, 12, 31))

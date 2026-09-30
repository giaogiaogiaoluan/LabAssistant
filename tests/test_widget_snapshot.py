"""小组件快照：口径与软件内一致 + 原子落盘。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from labassistant.db import Database
from labassistant.services import widget_snapshot as w
from labassistant.services import aggregate as agg

DAY = date(2026, 9, 22)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 Widget 容器与主应用数据目录都指到临时目录，绝不碰真实文件。"""
    monkeypatch.setattr(w, "widget_container_dir", lambda: tmp_path / "widget")
    monkeypatch.setattr(w.C, "app_data_dir", lambda: tmp_path / "data")
    db = Database(tmp_path / "t.db")
    return db, tmp_path


def test_快照字段与软件内日汇总一致(env):
    db, _ = env
    db.conn.execute("INSERT INTO attendance_blocks(date,start_min,end_min,note,is_sample,created)"
                    " VALUES('2026-09-22',540,660,'',0,'')")
    db.conn.execute("INSERT INTO manual_hours(date,minutes,note,is_sample,created)"
                    " VALUES('2026-09-22',60,'',0,'')")
    db.conn.commit()
    snap = w.build_snapshot(db, DAY)
    day = agg.day_summary(db, DAY)
    assert snap["effective_min"] == day["effective_min"] == 180
    assert snap["required_min"] == day["required_min"]
    assert snap["ratio"] == pytest.approx(180 / 480, abs=1e-3)
    assert snap["lab_min"] == 120 and snap["manual_min"] == 60
    assert snap["date"] == "2026-09-22"
    assert snap["weekday_cn"] == "周二"


def test_课程按时段排序且带位置(env):
    db, _ = env
    db.conn.execute(
        "INSERT INTO courses(name,weekday,start_min,end_min,start_date,end_date,"
        "location,teacher,note,count_attendance,is_sample,created) "
        "VALUES('晚课',1,1020,1140,'2026-09-01','2026-12-31','主楼301','T','',1,0,'')")
    db.conn.execute(
        "INSERT INTO courses(name,weekday,start_min,end_min,start_date,end_date,"
        "location,teacher,note,count_attendance,is_sample,created) "
        "VALUES('早课',1,480,600,'2026-09-01','2026-12-31','外楼','T','',1,0,'')")
    db.conn.commit()
    snap = w.build_snapshot(db, DAY)
    assert [c["name"] for c in snap["courses"]] == ["早课", "晚课"]
    assert snap["courses"][0]["start"] == "08:00"
    assert snap["courses"][1]["location"] == "主楼301"
    assert snap["courses"][0]["counts"] is True


def test_写入两处且是合法json(env):
    db, tmp = env
    res = w.write_snapshot(db)
    assert not res["errors"], res["errors"]
    assert len(res["paths"]) == 2
    for path in res["paths"]:
        data = json.loads(open(path, encoding="utf-8").read())
        assert data["schema"] == w.SCHEMA
        assert data["date"] == DAY.isoformat() or "date" in data
    assert (tmp / "widget" / w.SNAPSHOT_NAME).exists()
    assert not (tmp / "group").exists()
    assert (tmp / "data" / w.SNAPSHOT_NAME).exists()


def test_原子写不留临时文件(env):
    db, tmp = env
    w.write_snapshot(db)
    leftovers = list(tmp.rglob("*.tmp"))
    assert leftovers == [], f"残留临时文件：{leftovers}"


def test_read_snapshot_能读回(env):
    db, _ = env
    w.write_snapshot(db)
    back = w.read_snapshot()
    assert back and "generated_at" in back and "ratio" in back


def test_刷新有节流但可强制(env, monkeypatch):
    db, tmp = env
    monkeypatch.setattr(w, "_last_write_ts", 0.0)
    assert w.refresh(db, force=True) is not None
    assert w.refresh(db) is None              # 20 秒内第二次被节流掉
    assert w.refresh(db, force=True) is not None


def test_next_refresh_落在未来且不超过5分钟(env):
    """小组件靠这个时间决定下一次什么时候值得刷新。"""
    from datetime import datetime, timedelta
    db, _ = env
    snap = w.build_snapshot(db, DAY)
    assert "next_refresh" not in snap          # build 是纯函数，刷新时间由写盘补上
    w.write_snapshot(db)
    nxt = datetime.fromisoformat(w.read_snapshot()["next_refresh"])
    now = datetime.now().astimezone()
    assert nxt.tzinfo is not None, "next_refresh 必须带时区，否则 Swift 侧解析不了"
    assert now - timedelta(seconds=5) < nxt <= now + timedelta(minutes=5)


def test_高优先级待办成为重要事件(env):
    db, _ = env
    db.conn.execute(
        "INSERT INTO todos(date,title,done,est_minutes,priority,deadline,note,is_sample,created) "
        "VALUES('2026-09-22','提交重要报告',0,60,'高','','',0,'')")
    db.conn.commit()
    snap = w.build_snapshot(db, DAY)
    assert snap["important_event"] == "提交重要报告"


def test_周末不产生要求(env):
    db, _ = env
    sat = date(2026, 9, 26)
    snap = w.build_snapshot(db, sat)
    assert snap["kind"] == "weekend"
    assert snap["required_min"] == 0
    assert snap["ratio"] == 0.0

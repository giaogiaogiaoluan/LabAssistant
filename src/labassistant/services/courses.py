"""课程及课程例外（单次取消 / 单次调整）CRUD 与发生日计算 —— 同步元数据 + 软删除。

课程例外跨设备使用 course_uuid 关联课程（而非本机整数 course_id）。
"""

from __future__ import annotations

from datetime import date

from labassistant.db import Database, now_text
from labassistant.services import meta

_ALIVE = "deleted_at IS NULL"


def _course_row(r) -> dict:
    return dict(r)


def list_courses(db: Database, keyword: str = "") -> list[dict]:
    sql = f"SELECT * FROM courses WHERE {_ALIVE}"
    params: tuple = ()
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        sql += " AND (name LIKE ? OR location LIKE ? OR teacher LIKE ? OR note LIKE ?)"
        params = (kw, kw, kw, kw)
    sql += " ORDER BY weekday, start_min, id"
    return [_course_row(r) for r in db.query(sql, params)]


def get_course(db: Database, course_id: int) -> dict | None:
    r = db.query_one(f"SELECT * FROM courses WHERE id=? AND {_ALIVE}", (course_id,))
    return dict(r) if r else None


def add_course(
    db: Database,
    name: str,
    weekday: int,
    start_min: int,
    end_min: int,
    start_date: date,
    end_date: date,
    location: str = "",
    teacher: str = "",
    note: str = "",
    count_attendance: bool = True,
    is_sample: int = 0,
) -> int:
    m = meta.new_row_meta(db, sample=bool(is_sample))
    return db.execute(
        "INSERT INTO courses(name, weekday, start_min, end_min, start_date, end_date, "
        "location, teacher, note, count_attendance, is_sample, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
        (
            name.strip(), int(weekday), int(start_min), int(end_min),
            start_date.isoformat(), end_date.isoformat(), location.strip(), teacher.strip(),
            note.strip(), 1 if count_attendance else 0, is_sample, now_text(),
            m["sync_uuid"], m["created_at"], m["updated_at"], m["device_id"], m["sync_dirty"],
        ),
    )


def update_course(
    db: Database,
    course_id: int,
    name: str,
    weekday: int,
    start_min: int,
    end_min: int,
    start_date: date,
    end_date: date,
    location: str = "",
    teacher: str = "",
    note: str = "",
    count_attendance: bool = True,
) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE courses SET name=?, weekday=?, start_min=?, end_min=?, start_date=?, "
        "end_date=?, location=?, teacher=?, note=?, count_attendance=?, "
        "updated_at=?, device_id=?, sync_dirty=1, deleted_at=NULL WHERE id=? AND deleted_at IS NULL",
        (
            name.strip(), int(weekday), int(start_min), int(end_min),
            start_date.isoformat(), end_date.isoformat(), location.strip(), teacher.strip(),
            note.strip(), 1 if count_attendance else 0,
            t["updated_at"], t["device_id"], course_id,
        ),
    )


def delete_course(db: Database, course_id: int) -> None:
    """软删除课程（其它设备同样会收到删除状态）。"""
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE courses SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE id=? AND deleted_at IS NULL",
        (t["updated_at"], t["updated_at"], t["device_id"], course_id),
    )


def course_sync_uuid(db: Database, course_id: int) -> str:
    r = db.query_one("SELECT sync_uuid FROM courses WHERE id=?", (course_id,))
    return (r["sync_uuid"] if r and r["sync_uuid"] else "")


# ---------------- 课程例外（单次取消 / 单次调整） ----------------

def get_exception(db: Database, course_id: int, date_str: str) -> dict | None:
    r = db.query_one(
        f"SELECT * FROM course_exceptions WHERE course_id=? AND date=? AND {_ALIVE}",
        (course_id, date_str),
    )
    return dict(r) if r else None


def list_exceptions(db: Database, course_id: int | None = None) -> list[dict]:
    join = ("JOIN courses c ON c.id = e.course_id AND c.deleted_at IS NULL "
            "WHERE e.deleted_at IS NULL")
    if course_id is None:
        rows = db.query(
            "SELECT e.*, c.name AS course_name FROM course_exceptions e "
            + join + " ORDER BY e.date DESC, e.id DESC"
        )
    else:
        rows = db.query(
            "SELECT e.*, c.name AS course_name FROM course_exceptions e "
            + join + " AND e.course_id=? ORDER BY e.date DESC",
            (course_id,),
        )
    return [dict(r) for r in rows]


def upsert_exception(
    db: Database,
    course_id: int,
    date_str: str,
    action: str,  # 'cancelled' | 'moved'
    start_min: int | None = None,
    end_min: int | None = None,
    note: str = "",
) -> None:
    t = meta.touch_meta(db)
    cuuid = course_sync_uuid(db, course_id)
    from labassistant_shared import timeutil
    new_uuid = timeutil.new_uuid()
    db.execute(
        "INSERT INTO course_exceptions(course_id, date, action, start_min, end_min, note, "
        "course_uuid, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?) "
        "ON CONFLICT(course_id, date) DO UPDATE SET action=excluded.action, "
        "start_min=excluded.start_min, end_min=excluded.end_min, note=excluded.note, "
        "course_uuid=excluded.course_uuid, "
        "updated_at=excluded.updated_at, device_id=excluded.device_id, "
        "sync_dirty=1, deleted_at=NULL",
        (course_id, date_str, action, start_min, end_min, note,
         cuuid, now_text(),
         new_uuid, t["updated_at"], t["updated_at"], t["device_id"], 1),
    )


def remove_exception(db: Database, course_id: int, date_str: str) -> None:
    """恢复本次 = 软删除该例外。"""
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE course_exceptions SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE course_id=? AND date=? AND deleted_at IS NULL",
        (t["updated_at"], t["updated_at"], t["device_id"], course_id, date_str),
    )


# ---------------- 发生日 ----------------

def occurrences_on(db: Database, d: date) -> list[dict]:
    """d 这天按周重复规则应发生的课程（已解析例外状态）。"""
    ds = d.isoformat()
    rows = db.query(
        f"SELECT * FROM courses WHERE weekday=? AND start_date<=? AND end_date>=? "
        f"AND {_ALIVE} ORDER BY start_min, id",
        (d.weekday(), ds, ds),
    )
    out: list[dict] = []
    for r in rows:
        c = dict(r)
        exc = get_exception(db, c["id"], ds)
        item = {
            "course_id": c["id"],
            "course_uuid": c.get("sync_uuid") or "",
            "name": c["name"],
            "weekday": c["weekday"],
            "location": c["location"],
            "teacher": c["teacher"],
            "note": c["note"],
            "count_attendance": bool(c["count_attendance"]),
            "state": "normal",
            "disp_start_min": c["start_min"],
            "disp_end_min": c["end_min"],
            "exc_note": "",
        }
        if exc:
            item["exc_note"] = exc.get("note") or ""
            if exc["action"] == "cancelled":
                item["state"] = "cancelled"
            else:  # moved
                item["state"] = "moved"
                if exc.get("start_min") is not None:
                    item["disp_start_min"] = exc["start_min"]
                if exc.get("end_min") is not None:
                    item["disp_end_min"] = exc["end_min"]
        out.append(item)
    return out


def list_exceptions_full(db: Database) -> list[dict]:
    return list_exceptions(db)

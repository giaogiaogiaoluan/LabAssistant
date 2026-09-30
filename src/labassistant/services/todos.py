"""Todo / 日程 CRUD 与查询 —— 带同步元数据与软删除。"""

from __future__ import annotations

from labassistant.db import Database, now_text
from labassistant.services import meta

PRIORITIES = ["高", "中", "低"]

_ALIVE = "deleted_at IS NULL"


def _dict(r) -> dict:
    return dict(r)


def list_todos(
    db: Database,
    date_str: str | None = None,
    keyword: str = "",
    only_open: bool = False,
) -> list[dict]:
    sql = "SELECT * FROM todos"
    conds: list[str] = [_ALIVE]
    params: list = []
    if date_str:
        conds.append("date=?")
        params.append(date_str)
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        conds.append("(title LIKE ? OR note LIKE ?)")
        params += [kw, kw]
    if only_open:
        conds.append("done=0")
    sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY done ASC, date ASC, "
    sql += "CASE priority WHEN '高' THEN 0 WHEN '中' THEN 1 ELSE 2 END, id DESC"
    return [_dict(r) for r in db.query(sql, tuple(params))]


def get_todo(db: Database, todo_id: int) -> dict | None:
    r = db.query_one(
        f"SELECT * FROM todos WHERE id=? AND {_ALIVE}", (todo_id,)
    )
    return dict(r) if r else None


def add_todo(
    db: Database,
    date_str: str,
    title: str,
    done: bool = False,
    est_minutes: int | None = None,
    priority: str = "中",
    deadline: str = "",
    note: str = "",
    is_sample: int = 0,
) -> int:
    m = meta.new_row_meta(db, sample=bool(is_sample))
    return db.execute(
        "INSERT INTO todos(date, title, done, est_minutes, priority, deadline, note, is_sample, created, "
        "sync_uuid, created_at, updated_at, deleted_at, device_id, sync_dirty) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            date_str, title.strip(), 1 if done else 0, est_minutes,
            priority if priority in PRIORITIES else "中", deadline.strip(), note.strip(),
            int(is_sample), now_text(),
            m["sync_uuid"], m["created_at"], m["updated_at"], None, m["device_id"], m["sync_dirty"],
        ),
    )


def update_todo(
    db: Database,
    todo_id: int,
    date_str: str,
    title: str,
    done: bool,
    est_minutes: int | None,
    priority: str,
    deadline: str,
    note: str,
) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE todos SET date=?, title=?, done=?, est_minutes=?, priority=?, deadline=?, note=?, "
        "updated_at=?, device_id=?, sync_dirty=1, deleted_at=NULL WHERE id=? AND deleted_at IS NULL",
        (
            date_str, title.strip(), 1 if done else 0, est_minutes,
            priority if priority in PRIORITIES else "中", deadline.strip(), note.strip(),
            t["updated_at"], t["device_id"], todo_id,
        ),
    )


def set_done(db: Database, todo_id: int, done: bool) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE todos SET done=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE id=? AND deleted_at IS NULL",
        (1 if done else 0, t["updated_at"], t["device_id"], todo_id),
    )


def delete_todo(db: Database, todo_id: int) -> None:
    t = meta.touch_meta(db)
    db.execute(
        "UPDATE todos SET deleted_at=?, updated_at=?, device_id=?, sync_dirty=1 "
        "WHERE id=? AND deleted_at IS NULL",
        (t["updated_at"], t["updated_at"], t["device_id"], todo_id),
    )

"""数据安全：v1 -> v2 迁移不丢数据；网站模块 CRUD/URL；软删除后可重建。"""

import os
import sqlite3
import sys
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT / ".pylibs"):
    if p not in sys.path:
        sys.path.insert(0, str(p))

from labassistant.db import Database
from labassistant.db_migration import TARGET_SCHEMA_VERSION
from labassistant.services import holidays as hds
from labassistant.services import todos as tds
from labassistant.services import websites as ws

TMP = ROOT / "tests" / ".tmpdb"


def _mk() -> Path:
    TMP.mkdir(exist_ok=True)
    return TMP / f"m_{uuid.uuid4().hex[:8]}.db"


def test_迁移不丢数据():
    from labassistant.db import _SCHEMA  # noqa: F401  v1 建表语句
    import labassistant.db as _dbmod

    p = _mk()
    # 手工构造一个 v1 老库并写入真实数据
    con = sqlite3.connect(str(p))
    con.executescript(_dbmod._SCHEMA)
    con.execute("INSERT INTO settings(key,value) VALUES('schema_version','1')")
    con.execute(
        "INSERT INTO courses(name,weekday,start_min,end_min,start_date,end_date,is_sample) "
        "VALUES('老课程',0,600,720,'2026-09-01','2026-12-31',0)")
    con.execute("INSERT INTO attendance_blocks(date,start_min,end_min,is_sample) VALUES('2026-09-07',480,720,0)")
    con.execute("INSERT INTO todos(date,title,done,is_sample) VALUES('2026-09-08','老Todo',0,0)")
    con.execute("INSERT INTO holidays(date,name,is_sample) VALUES('2026-10-01','国庆',1)")
    con.commit()
    con.close()

    db = Database(p)  # 触发迁移
    ver = db.get_int("schema_version")
    assert ver == TARGET_SCHEMA_VERSION   # 不再写死版本号，加迁移也不用改测试
    # 数据仍在
    assert len(db.query("SELECT * FROM courses")) == 1
    assert len(db.query("SELECT * FROM attendance_blocks")) == 1
    assert len(db.query("SELECT * FROM todos")) == 1
    # 同步字段已加
    cols = {r["name"] for r in db.query("PRAGMA table_info(courses)")}
    for need in ("sync_uuid", "updated_at", "deleted_at", "device_id", "sync_dirty"):
        assert need in cols, need
    # 老数据已获得 uuid，且非示例数据进入待同步队列（dirty=1）
    row = db.query_one("SELECT sync_uuid, sync_dirty FROM courses WHERE name='老课程'")
    assert row["sync_uuid"] and row["sync_dirty"] == 1
    # 示例数据（is_sample=1）不进同步队列
    h = db.query_one("SELECT sync_dirty FROM holidays WHERE name='国庆'")
    assert h["sync_dirty"] == 0
    # 自动备份存在
    baks = list((p.parent / "backups").glob("backup_before_sync_migration_*.db"))
    assert baks, "迁移前应自动备份"
    # 服务层仍能正常读到老数据
    assert len(tds.list_todos(db)) == 1
    assert "2026-10-01" in {d.isoformat() for d in hds.holiday_set(db)}
    db.close()


def test_网站_CRUD_软删_重建():
    db = Database(_mk())
    wid = ws.add_website(db, "Google Scholar", "https://scholar.google.com", "academic", "检索")
    rows = ws.list_websites(db)
    assert len(rows) == 1 and rows[0]["category"] == "academic"
    # 编辑
    ws.update_website(db, wid, "GS", "https://scholar.google.com/", "tools", "x")
    assert ws.list_websites(db)[0]["name"] == "GS"
    # 软删除
    ws.delete_website(db, wid)
    assert ws.list_websites(db) == []
    # 再新增同名（新 uuid），属于不同记录——不复活旧 tombstone
    wid2 = ws.add_website(db, "GS", "https://scholar.google.com/", "tools", "")
    assert len(ws.list_websites(db)) == 1 and wid2 != wid
    db.close()


def test_网址规范化与校验():
    assert ws.normalize_url("scholar.google.com") == "https://scholar.google.com"
    assert ws.normalize_url("http://a.cn/x") == "http://a.cn/x"
    assert ws.is_valid_url("https://scholar.google.com")
    assert ws.is_valid_url("scholar.google.com/x") is True   # 缺协议会自动补全为 https
    assert ws.is_valid_url("not a url") is False
    assert ws.is_valid_url("https://www.baidu.com/query?a=1") is True
    assert ws.normalize_url("") == ""


def test_节假日软删后可重加():
    db = Database(_mk())
    ds = "2026-10-08"
    assert hds.add_holiday(db, ds, "测试") is True
    hds.delete_holiday(db, date_str=ds)
    assert hds.holiday_set(db) == set()
    assert hds.add_holiday(db, ds, "测试2") is True
    assert date.fromisoformat(ds) in hds.holiday_set(db)
    db.close()

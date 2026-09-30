"""LabTime → LabAssistant 数据迁移：merge 幂等 / replace 先备份 / 旧库全程只读未被改动。"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labassistant import constants as C          # noqa: E402
from labassistant import migration as mig        # noqa: E402
from labassistant.db import Database, _SCHEMA                  # noqa: E402
from labassistant.db_migration import TARGET_SCHEMA_VERSION    # noqa: E402
from labassistant.services import attendance as att   # noqa: E402
from labassistant.services import courses as crs      # noqa: E402
from labassistant.services import holidays as hds     # noqa: E402
from labassistant.services import todos as tds        # noqa: E402
from labassistant.services import websites as ws      # noqa: E402

BUSINESS_TABLES = ("holidays", "courses", "course_exceptions", "attendance_blocks",
                   "manual_hours", "todos", "websites")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """把 app_data_dir() 指到 tmp，避免测试写进项目 data/。"""
    d = tmp_path / "appdata"
    d.mkdir(parents=True)
    monkeypatch.setattr(C, "_data_dir_cache", d)
    return d


def _legacy_path(tmp_path: Path, name: str = "labtime.db") -> Path:
    p = tmp_path / "legacy"
    p.mkdir(exist_ok=True)
    return p / name


def _fill_legacy(db: Database) -> None:
    """往「旧库」里塞真实数据（含 settings / websites / todos / holidays / 课程例外）。"""
    db.set_setting("device_id", "legacy-device-0001")
    db.set_setting("daily_minutes", 300, sync_business=True)      # 用户改过的每日要求
    db.set_setting("workdays", "1,2,3,4,5", sync_business=True)
    db.set_setting("sync_enabled", "1")
    db.set_setting("custom_theme_accent", "violet")               # 程序未知的自定义键
    today = date.today()
    hds.add_holidays_batch(db, [(today.isoformat(), "国庆"),
                                ((today + timedelta(days=1)).isoformat(), "中秋"),
                                ((today + timedelta(days=2)).isoformat(), "元旦")])
    c1 = crs.add_course(db, "高等数学", 0, 600, 690, today, today + timedelta(days=100),
                        "主楼 201", "王老师", "", True, 0)
    crs.add_course(db, "实验物理", 2, 800, 920, today, today + timedelta(days=100),
                   "物楼 305", "李老师", "带报告", True, 0)
    crs.upsert_exception(db, c1, (today + timedelta(days=7)).isoformat(), "cancelled")
    att.add_block(db, today.isoformat(), 540, 660, "上午")
    att.add_block(db, (today + timedelta(days=1)).isoformat(), 780, 1020, "下午")
    att.add_manual(db, (today + timedelta(days=2)).isoformat(), 90, "看文献")
    tds.add_todo(db, today.isoformat(), "写周报", False, 60, "高", "", "")
    tds.add_todo(db, today.isoformat(), "交报销单", True, None, "低", "", "")
    # 源库里真正重复的两行：必须一起导过来，不能被幂等规则吃掉
    tds.add_todo(db, today.isoformat(), "重复事项", False, 30, "中", "", "dup")
    tds.add_todo(db, today.isoformat(), "重复事项", False, 30, "中", "", "dup")
    ws.add_website(db, "arXiv", "https://arxiv.org", "academic", "预印本")
    ws.add_website(db, "知网", "https://cnki.net", "academic", "")
    ws.add_website(db, "DeepL", "https://www.deepl.com/translator", "tools", "翻译")


def _counts(db: Database, tables=BUSINESS_TABLES) -> dict[str, int]:
    return {t: int(db.query_one(f"SELECT COUNT(*) AS c FROM {t}")["c"]) for t in tables}


def _counts_of_file(path: Path, tables=BUSINESS_TABLES) -> dict[str, int]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        out = {}
        for t in tables:
            has = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                              (t,)).fetchone()
            out[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) if has else 0
        return out
    finally:
        con.close()


def test_临时旧库可被建出来(tmp_path):
    p = _legacy_path(tmp_path)
    db = Database(p)
    _fill_legacy(db)
    assert sum(_counts(db).values()) > 10
    db.close()


# ----------------------------------------------------------------- merge
def test_merge_行数与来源一致且旧库未被改动(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    expected = _counts_of_file(legacy)
    fp_before = mig.file_fingerprint(legacy)
    wal = Path(str(legacy) + "-wal")
    wal_before = mig.file_fingerprint(wal) if wal.exists() else None

    target = Database(tmp_path / "new" / "labassistant.db")
    report = mig.run_import(target, legacy, mode="merge")

    assert report["mode"] == "merge"
    assert report["integrity_check"] == "ok"
    for table, n in expected.items():
        assert _counts(target, (table,))[table] == n, table
    # 源库里重复的两行都进来了（没被业务键吃掉）
    assert _counts(target, ("todos",))["todos"] == 4
    # 幂等标记：来源/时间/报告都写进了设置
    assert target.get_setting("migrated_from") == str(legacy)
    assert target.get_setting("migrated_at")
    assert json.loads(target.get_setting("migration_report"))["mode"] == "merge"
    assert target.get_setting("sample_loaded") == "1"
    # 旧库文件全程只读：mtime + size + sha256 完全一致
    assert report["source_untouched"] is True
    assert mig.file_fingerprint(legacy) == fp_before
    if wal_before is not None:
        assert mig.file_fingerprint(wal) == wal_before
    target.close()


def test_merge_导入两次不产生重复(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    target = Database(tmp_path / "new" / "labassistant.db")
    first = mig.run_import(target, legacy, mode="merge")
    after_first = _counts(target)
    second = mig.run_import(target, legacy, mode="merge")

    assert first["total_imported"] > 10
    assert second["total_imported"] == 0
    assert _counts(target) == after_first
    # sync_uuid 原样保留 → 第二次必然命中 uuid 判定
    assert second["tables"]["todos"]["skipped"] == after_first["todos"]
    assert mig.file_fingerprint(legacy) == first["source_fingerprint_before"]
    assert second["source_untouched"] is True
    target.close()


def test_merge_不覆盖用户已改的值但补齐未知设置键(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    target = Database(tmp_path / "new" / "labassistant.db")
    target.set_setting("daily_minutes", 600, sync_business=True)   # 新版里自己改过
    assert target.get_setting("custom_theme_accent") is None
    mig.run_import(target, legacy, mode="merge")

    assert target.get_setting("daily_minutes") == "600"            # 不被旧库覆盖
    assert target.get_setting("workdays") == "1,2,3,4,5"           # 仍是默认值 → 采纳旧库
    assert target.get_setting("custom_theme_accent") == "violet"   # 新键带过来
    assert target.get_setting("device_id") != "legacy-device-0001"  # 机器身份不迁移
    assert target.get_setting("sync_enabled") == "0"               # 同步开关不迁移
    target.close()


def test_merge_课程例外重映射到目标库(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    target = Database(tmp_path / "new" / "labassistant.db")
    mig.run_import(target, legacy, mode="merge")
    exc = target.query_one("SELECT * FROM course_exceptions")
    course = target.query_one("SELECT * FROM courses WHERE name='高等数学'")
    assert exc["course_id"] == course["id"]
    assert exc["course_uuid"] == course["sync_uuid"]
    assert len(crs.list_courses(target)) == 2
    assert len(crs.list_exceptions(target)) == 1
    target.close()


def test_merge_v1老库按业务键去重(tmp_path):
    """v1 旧库（无 sync_uuid / 无 websites 表）：按业务键幂等，并自动补 uuid。"""
    legacy = _legacy_path(tmp_path, "labtime_v1.db")
    legacy.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(legacy))
    con.executescript(_SCHEMA)
    con.execute("INSERT INTO settings(key,value) VALUES('schema_version','1')")
    con.execute("INSERT INTO settings(key,value) VALUES('daily_minutes','420')")
    con.execute("INSERT INTO courses(name,weekday,start_min,end_min,start_date,end_date,"
                "location,is_sample) VALUES('v1课程',1,600,690,'2026-09-01','2026-12-31','',0)")
    cid = con.execute("SELECT id FROM courses WHERE name='v1课程'").fetchone()[0]
    con.execute("INSERT INTO course_exceptions(course_id,date,action) VALUES(?,?, 'cancelled')",
                (cid, "2026-10-01"))
    con.execute("INSERT INTO holidays(date,name,is_sample) VALUES('2026-10-01','国庆',0)")
    con.execute("INSERT INTO attendance_blocks(date,start_min,end_min) VALUES('2026-09-07',480,720)")
    con.execute("INSERT INTO todos(date,title) VALUES('2026-09-07','v1待办')")
    con.commit()
    con.close()

    target = Database(tmp_path / "new" / "labassistant.db")
    first = mig.run_import(target, legacy, mode="merge")
    counts1 = _counts(target)
    assert counts1["courses"] == 1 and counts1["holidays"] == 1
    assert counts1["attendance_blocks"] == 1 and counts1["todos"] == 1
    assert counts1["websites"] == 0                    # 旧库没这张表，跳过而不是报错
    assert any("websites" in n or "网站" in n for n in first["notes"])
    # v1 行拿到了新的同步身份，并进入待上传队列
    row = target.query_one("SELECT sync_uuid, sync_dirty FROM todos WHERE title='v1待办'")
    assert row["sync_uuid"] and row["sync_dirty"] == 1
    exc = target.query_one("SELECT course_id FROM course_exceptions")
    assert exc["course_id"] == target.query_one("SELECT id FROM courses")["id"]
    assert target.get_setting("daily_minutes") == "420"

    second = mig.run_import(target, legacy, mode="merge")
    assert second["total_imported"] == 0               # 业务键命中 → 一行都不再新增
    assert _counts(target) == counts1
    assert mig.file_fingerprint(legacy) == second["source_fingerprint_after"]
    target.close()


def test_merge_能读到旧库WAL里尚未落盘的数据(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    # 不关闭：最新一页数据仍在 -wal 里，只读连接必须也看得见
    att.add_block(src, date.today().isoformat(), 1200, 1260, "WAL中的新打卡")
    fp_before = mig.file_fingerprint(legacy)

    target = Database(tmp_path / "new" / "labassistant.db")
    report = mig.run_import(target, legacy, mode="merge")
    assert target.query_one("SELECT COUNT(*) AS c FROM attendance_blocks "
                            "WHERE note='WAL中的新打卡'")["c"] == 1
    assert report["source_untouched"] is True
    assert mig.file_fingerprint(legacy) == fp_before
    target.close()
    src.close()


# ----------------------------------------------------------------- replace
def test_replace_会先自动备份再整体替换(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()
    expected = _counts_of_file(legacy)
    fp_before = mig.file_fingerprint(legacy)

    target = Database(tmp_path / "new" / "labassistant.db")
    own = crs.add_course(target, "新版独有课程", 3, 600, 660, date.today(),
                         date.today() + timedelta(days=30), "", "", "", True, 0)
    target.set_setting("daily_minutes", 600, sync_business=True)
    report = mig.run_import(target, legacy, mode="replace")

    # 1) 替换前先把当前目标库在线备份到 import_backups/
    backups = sorted((C.app_data_dir() / "import_backups").glob(
        "labassistant_before_replace_*.db"))
    assert backups, "replace 必须先备份目标库"
    assert Path(report["backup"]).name == backups[-1].name
    assert _counts_of_file(backups[-1], ("courses",))["courses"] == 1
    assert Database.is_valid_labassistant_db(backups[-1])
    # 备份里确实是自己原来的数据（可回滚）
    con = sqlite3.connect(f"file:{backups[-1]}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        assert con.execute("SELECT name FROM courses WHERE id=?", (own,)).fetchone()["name"] \
            == "新版独有课程"
    finally:
        con.close()

    # 2) 目标库变成旧库内容，且 Database 已重连可继续用
    assert _counts(target) == expected
    assert len(crs.list_courses(target)) == 2
    assert target.get_setting("daily_minutes") == "300"
    assert target.get_setting("migrated_from") == str(legacy)
    assert target.get_setting("migration_mode") == "replace"
    assert report["target_integrity_check"] == "ok"
    # 3) 旧库依旧一个字节都没变
    assert report["source_untouched"] is True
    assert mig.file_fingerprint(legacy) == fp_before
    target.close()


def test_replace之后merge不会重复插数据(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()
    expected = _counts_of_file(legacy)

    target = Database(tmp_path / "new" / "labassistant.db")
    mig.run_import(target, legacy, mode="replace")
    report = mig.run_import(target, legacy, mode="merge")
    assert report["total_imported"] == 0
    assert _counts(target) == expected
    target.close()


# ----------------------------------------------------------------- 探测 / 预览 / 参数校验
def test_find_legacy_db按环境变量探测并校验只读可开(tmp_path, monkeypatch):
    legacy = _legacy_path(tmp_path, "labtime.db")
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    monkeypatch.setenv("LABTIME_DATA_DIR", str(legacy.parent))
    assert mig.find_legacy_db() == legacy.resolve()
    # 排除自身 → 不会再命中这个候选（本机可能还有别的老库，所以不断言 None）
    got = mig.find_legacy_db(exclude=legacy)
    assert got is None or got != legacy.resolve()
    # 非数据库文件不会被误认成旧库
    junk = tmp_path / "labtime.db"
    junk.write_text("not a database")
    monkeypatch.setenv("LABTIME_DATA_DIR", str(tmp_path))
    assert mig.find_legacy_db() != junk.resolve()


def test_plan_import给出行数预览与目标占用判断(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    target = Database(tmp_path / "new" / "labassistant.db")
    empty = mig.plan_import(legacy, target=target.path)
    assert empty["integrity_check"] == "ok"
    assert empty["source_readable"] is True
    assert empty["legacy_schema_version"] == str(TARGET_SCHEMA_VERSION)
    assert empty["tables"]["holidays"]["rows"] == 3
    src_rows = _counts_of_file(legacy, BUSINESS_TABLES + ("settings",))
    assert empty["total_source_rows"] == sum(src_rows.values())
    assert empty["fingerprint"]["sha256"] == mig.file_fingerprint(legacy)["sha256"]
    assert empty["target_has_data"] is False

    crs.add_course(target, "新库已有课程", 1, 600, 660, date.today(),
                   date.today() + timedelta(days=10), "", "", "", True, 0)
    plan = mig.plan_import(legacy, target=target.path)
    assert plan["target_has_data"] is True
    assert plan["target_rows"]["courses"] == 1
    assert "旧数据库" not in mig.format_plan(plan)
    assert "节假日 3" in mig.format_plan(plan)
    target.close()


def test_run_import拒绝异常输入(tmp_path):
    legacy = _legacy_path(tmp_path)
    src = Database(legacy)
    _fill_legacy(src)
    src.close()

    target = Database(tmp_path / "new" / "labassistant.db")
    with pytest.raises(ValueError):
        mig.run_import(target, legacy, mode="overwrite")     # type: ignore[arg-type]
    with pytest.raises(mig.MigrationError):
        mig.run_import(target, target.path, mode="merge")    # 自己导自己
    with pytest.raises(mig.MigrationError, match="不存在"):
        mig.run_import(target, tmp_path / "没有这个文件.db", mode="merge")
    junk = tmp_path / "junk.db"
    junk.write_text("hello")
    with pytest.raises(mig.MigrationError):
        mig.run_import(target, junk, mode="merge")
    # 失败过程没有动旧库
    assert mig.file_fingerprint(legacy)["exists"] is True
    target.close()

"""从旧版 LabTime **只读**导入数据 → LabAssistant 独立数据库。

设计要点
--------
1. **旧库永远只读**：所有对旧库的连接都通过只读 URI
   ``sqlite3.connect("file:" + path + "?mode=ro", uri=True)`` 建立，并额外执行
   ``PRAGMA query_only = ON`` 兜底。本模块不对旧库做任何写操作、不 checkpoint /
   truncate 它的 WAL、不改它的权限与时间戳。``file_fingerprint()`` 用于前后校验
   （mtime_ns + size + sha256），``run_import`` 把前后指纹写进迁移报告。

2. **表结构与 LabTime 一致**（除 websites / 同步元列），所以迁移本质是
   「把旧库文件安全地变成新库文件」。两种模式：

   * ``mode="merge"``：目标库保持自己的 schema，逐表幂等导入旧库数据。
     幂等判定（同一行视为已存在 → 跳过）：

     - ``sync_uuid`` 相同；**或**
     - 业务键完全相同（见 ``_IDENTITY``，与 ``protocol.ENTITIES`` 的可同步字段一致；
       holidays 用 ``date``（表上有 UNIQUE），course_exceptions 用解析后的
       ``(course_id, date)``，settings 用 ``key``）。

     业务键只与「本次导入前目标库已有的行」比较，因此源库里真正重复的多行不会被
     合并掉；而用户已经用上新版后再点「重新导入」也不会产生重复记录。
     settings 额外策略：机器/本地/状态类键（``_LOCAL_SKIP_SETTINGS``、``sync_*``）
     一律不导入；已存在的键一律不覆盖用户；只有 *目标库仍是程序默认值* 的业务设置
     （daily_minutes / workdays / default_course_counts）才采纳旧库值。

   * ``mode="replace"``：先用 sqlite3 在线备份 API 把当前目标库备份到
     ``app_data_dir()/import_backups/labassistant_before_replace_<时间戳>.db``，
     再对旧库做一次一致性快照（同样用在线备份 API 从只读连接读出，能包含 WAL 中
     尚未落盘的页），用快照整体替换目标库文件并让 ``Database`` 重连。

3. 迁移成功/失败都不应影响启动：调用方 ``try/except``；本模块只抛
   ``MigrationError`` / ``ValueError``，且不留下半成品状态（merge 出错回滚事务）。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from labassistant import constants as C
from labassistant_shared import timeutil
from labassistant_shared.protocol import SYNCED_SETTINGS

if TYPE_CHECKING:  # pragma: no cover
    from labassistant.db import Database

# 导入顺序：courses 必须先于 course_exceptions（要解析 course_id）
MERGE_ORDER: tuple[str, ...] = (
    "settings", "holidays", "courses", "course_exceptions",
    "attendance_blocks", "manual_hours", "todos", "websites",
)

TABLE_CN: dict[str, str] = {
    "settings": "设置",
    "holidays": "节假日",
    "courses": "课程",
    "course_exceptions": "课程单次调整",
    "attendance_blocks": "打卡时段",
    "manual_hours": "手动时长",
    "todos": "待办事项",
    "websites": "网站收藏",
}

# merge 幂等「业务键」（sync_uuid 之外的兜底判定）
_IDENTITY: dict[str, tuple[str, ...]] = {
    "settings": ("key",),
    "holidays": ("date",),
    "courses": ("name", "weekday", "start_min", "end_min", "start_date", "end_date",
                "location", "teacher", "note", "count_attendance"),
    "course_exceptions": ("course_id", "date"),
    "attendance_blocks": ("date", "start_min", "end_min", "note"),
    "manual_hours": ("date", "minutes", "note"),
    "todos": ("date", "title", "done", "est_minutes", "priority", "deadline", "note"),
    "websites": ("name", "url"),
}

_META_COLS = ("sync_uuid", "created_at", "updated_at", "deleted_at", "device_id", "sync_dirty")

# 这些设置键是机器/本地/状态类，迁移时**不**从旧库带过来（避免两版互相顶掉设备身份、
# 同步配置、示例数据标记、schema 版本等）
_LOCAL_SKIP_SETTINGS = frozenset({
    "schema_version", "sample_loaded", "device_id", "device_name", "device_kind",
    "sync_enabled", "sync_token", "sync_interval", "server_url",
    "window_w", "window_h", "last_date",
    "migrated_from", "migrated_at", "migration_report", "migration_mode",
})

# 只有这些业务设置允许「目标仍是默认值」时被旧库值采纳
_ADOPTABLE_SETTINGS = tuple(SYNCED_SETTINGS)


class MigrationError(RuntimeError):
    """迁移失败（旧库不可读、自检不通过、目标与来源相同等）。"""


# --------------------------------------------------------------- 只读连接 / 指纹
def _ro_uri(path: str | Path) -> str:
    """构造只读 URI（对 URI 保留字符做百分号转义）。"""
    s = str(Path(path))
    for ch, enc in (("%", "%25"), ("?", "%3F"), ("#", "%23")):
        s = s.replace(ch, enc)
    return f"file:{s}?mode=ro"


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """以**只读**方式打开数据库；失败抛异常，绝不创建或写入文件。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"旧数据库不存在：{p}")
    con = sqlite3.connect(_ro_uri(p), uri=True)
    con.row_factory = sqlite3.Row
    try:
        # 兜底：这个连接上任何写操作都会直接报错（包括误执行 DDL）
        con.execute("PRAGMA query_only = ON")
        con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.Error:
        con.close()
        raise
    return con


def file_fingerprint(path: str | Path) -> dict[str, Any]:
    """文件的「未被改动」指纹：mtime_ns + size + sha256。"""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "exists": False}
    st = p.stat()
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": str(p), "exists": True, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "mode": oct(st.st_mode), "sha256": h.hexdigest()}


# --------------------------------------------------------------- 小工具
def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def _count(con: sqlite3.Connection, table: str) -> int:
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def _integrity(con: sqlite3.Connection) -> str:
    try:
        r = con.execute("PRAGMA integrity_check").fetchone()
        return str(r[0]) if r else "no-result"
    except sqlite3.Error as exc:
        return f"error: {exc}"


def _norm(v: Any) -> str:
    """业务键归一化：区分 None / int / str，避免 1 与 '1' 混淆。"""
    if v is None:
        return "\x00null"
    if isinstance(v, bool):
        return f"b:{int(v)}"
    if isinstance(v, int):
        return f"i:{v}"
    if isinstance(v, float):
        return f"f:{v!r}"
    if isinstance(v, (bytes, bytearray)):
        return f"y:{bytes(v).hex()}"
    return f"s:{v}"


def _identity(table: str, values: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_norm(values.get(c)) for c in _IDENTITY[table])


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------- 探测 / 预览
def find_legacy_db(exclude: str | Path | None = None) -> Path | None:
    """按 ``constants.legacy_db_candidates()`` 的顺序探测可用的旧库。

    要求：文件存在、能以**只读**方式打开、且含可读的 ``settings`` 表。
    （完整性检查留给 ``plan_import`` / ``run_import``，坏库也要能被看见并给出说明。）
    ``exclude`` 用于排除目标库自身（防止自己导自己）。
    """
    skip: set[Path] = set()
    if exclude is not None:
        try:
            skip.add(Path(exclude).expanduser().resolve())
        except OSError:
            skip.add(Path(exclude))
    for cand in C.legacy_db_candidates():
        try:
            if cand in skip or not cand.is_file():
                continue
            con = open_readonly(cand)
            try:
                if not _has_table(con, "settings"):
                    continue
                con.execute("SELECT COUNT(*) FROM settings").fetchone()   # 真的读得动
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            continue
        return cand
    return None


def plan_import(legacy: str | Path, target: str | Path | None = None) -> dict[str, Any]:
    """迁移预览：来源、各表行数、integrity_check、目标路径、目标是否已有数据。"""
    src = Path(legacy).expanduser()
    tgt = Path(target) if target is not None else C.db_path()
    plan: dict[str, Any] = {
        "source": str(src),
        "target": str(tgt),
        "source_exists": src.is_file(),
        "source_readable": False,
        "integrity_check": "",
        "legacy_schema_version": None,
        "tables": {},
        "total_source_rows": 0,
        "target_exists": tgt.is_file(),
        "target_has_data": False,
        "target_rows": {},
        "fingerprint": None,
        "warnings": [],
    }
    if not plan["source_exists"]:
        plan["warnings"].append("旧库文件不存在，可能已被移动或卸载。")
        return plan

    con = open_readonly(src)          # 只读；不可读会抛 sqlite3.Error
    try:
        plan["source_readable"] = True
        plan["integrity_check"] = _integrity(con)
        if plan["integrity_check"] != "ok":
            plan["warnings"].append(f"旧库完整性检查未通过：{plan['integrity_check']}")
        row = con.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        plan["legacy_schema_version"] = row[0] if row else None
        total = 0
        for table in MERGE_ORDER:
            if not _has_table(con, table):
                continue
            n = _count(con, table)
            if table == "settings":
                n_business = n  # 展示用；实际导入按幂等规则逐键判定
                plan["tables"][table] = {"label": TABLE_CN[table], "rows": n_business,
                                         "columns": _columns(con, table)}
            else:
                plan["tables"][table] = {"label": TABLE_CN[table], "rows": n,
                                         "columns": _columns(con, table)}
            total += n
        plan["total_source_rows"] = total
        plan["fingerprint"] = file_fingerprint(src)
    finally:
        con.close()

    if plan["target_exists"]:
        try:
            tcon = sqlite3.connect(_ro_uri(tgt), uri=True)
            tcon.row_factory = sqlite3.Row
        except sqlite3.Error:
            tcon = None
        if tcon is not None:
            try:
                for table in MERGE_ORDER:
                    if table == "settings" or not _has_table(tcon, table):
                        continue
                    n = _count(tcon, table)
                    plan["target_rows"][table] = n
                    if n:
                        plan["target_has_data"] = True
            finally:
                tcon.close()
        else:
            plan["warnings"].append("目标库正被其它进程以不可读方式占用，无法预览现状。")
    return plan


# --------------------------------------------------------------- merge
def _merge_settings(sconn: sqlite3.Connection, tconn: sqlite3.Connection) -> dict[str, Any]:
    """幂等导入 settings（永不覆盖用户已改的值、不导入机器/本地键）。"""
    from labassistant.db import _DEFAULT_SETTINGS as defaults

    t_cols = set(_columns(tconn, "settings"))
    s_cols = set(_columns(sconn, "settings"))
    existing = {r["key"]: r for r in _rows(tconn, "SELECT * FROM settings")}
    t_has_meta = {"sync_dirty", "updated_at", "device_id"} <= t_cols
    before = _count(tconn, "settings")
    imported = skipped = 0
    adopted: list[str] = []

    def _insert(key: str, value: Any, meta: dict[str, Any]) -> None:
        cols = ["key", "value", *meta.keys()]
        vals = [key, value, *meta.values()]
        tconn.execute(
            f"INSERT INTO settings ({', '.join(cols)}) VALUES "
            f"({', '.join('?' * len(cols))})", tuple(vals))

    for row in _rows(sconn, "SELECT * FROM settings ORDER BY key"):
        key = row.get("key")
        if key is None:
            continue
        if key in _LOCAL_SKIP_SETTINGS or str(key).startswith("sync_"):
            skipped += 1
            continue
        value = row.get("value")
        meta: dict[str, Any] = {}
        if t_has_meta:
            src_dirty = row.get("sync_dirty") if "sync_dirty" in s_cols else None
            # 旧库明确标记为「已同步」→ 保持 0；否则（v1 老库/待上传）标记 1 交给服务端
            meta["sync_dirty"] = int(src_dirty or 0) if src_dirty is not None else 1
            meta["updated_at"] = row.get("updated_at")
            meta["device_id"] = row.get("device_id")
            meta["deleted_at"] = None
        meta = {k: v for k, v in meta.items() if k in t_cols}

        cur = existing.get(key)
        if key in _ADOPTABLE_SETTINGS:
            if cur is None:
                _insert(key, value, meta)
                existing[key] = {"key": key, "value": value}
                imported += 1
                adopted.append(key)
                continue
            same_as_source = str(cur["value"]) == str(value)
            untouched_by_user = str(cur["value"]) == str(defaults.get(key))
            if same_as_source or not untouched_by_user:
                skipped += 1     # 值本来就一样，或用户在新版自己改过 → 不覆盖
                continue
            tconn.execute(
                f"UPDATE settings SET value=?"
                + "".join(f", {k}=?" for k in meta) + " WHERE key=?",
                (value, *meta.values(), key))
            imported += 1
            adopted.append(key)
            continue

        if cur is not None:
            skipped += 1         # 业务键（key）已存在 → 不重复插入、不覆盖
            continue
        _insert(key, value, meta)
        existing[key] = {"key": key, "value": value}
        imported += 1

    return {"imported": imported, "skipped": skipped, "adopted_keys": adopted,
            "orphans": 0, "uuid_generated": 0, "target_rows_before": before,
            "target_rows_after": _count(tconn, "settings")}


def _merge_table(sconn: sqlite3.Connection, tconn: sqlite3.Connection, table: str,
                 course_map: dict[int, int], notes: list[str]) -> dict[str, Any]:
    s_cols = _columns(sconn, table)
    s_set = set(s_cols)
    t_cols = _columns(tconn, table)
    t_set = set(t_cols)
    # 以**目标 schema** 为准：源库（可能是 v1 老库）没有的同步元列也补齐写入，
    # 这样导入进来的每一行都有 sync_uuid，可正常参与同步。
    cols = [c for c in s_cols if c in t_set and c != "id"]
    for c in _META_COLS:
        if c in t_set and c not in s_set:
            cols.append(c)
    if table == "course_exceptions" and "course_uuid" in t_set and "course_uuid" not in s_set:
        cols.append("course_uuid")
    # 业务键列若源库没有则不参与判定（退化为只按 sync_uuid 去重）
    id_cols = [c for c in _IDENTITY[table] if c in cols]
    if tuple(id_cols) != _IDENTITY[table]:
        notes.append(f"{TABLE_CN[table]}：旧库缺少业务键列 {id_cols}，仅按 sync_uuid 去重")

    uuid_index: dict[str, int] = {}
    key_index: dict[tuple[str, ...], int] = {}
    sel_cols = [c for c in (*id_cols, "sync_uuid") if c in t_set]
    before = _count(tconn, table)
    for r in _rows(tconn, f"SELECT id, {', '.join(sel_cols)} FROM {table}"):
        u = r.get("sync_uuid")
        if u:
            uuid_index[str(u)] = r["id"]
        key_index[_identity(table, r)] = r["id"]

    insert_sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                  f"({', '.join('?' * len(cols))})")
    imported = skipped = orphans = uuid_generated = 0
    now_iso = timeutil.utcnow_iso()

    for row in _rows(sconn, f"SELECT * FROM {table} ORDER BY id"):
        values = {c: (row.get(c) if c in s_set else None) for c in cols}
        if table == "course_exceptions":
            resolved = _resolve_course_id(tconn, row, course_map)
            if resolved is None:
                skipped += 1
                orphans += 1
                continue
            values["course_id"] = resolved
            if "course_uuid" in cols and not values.get("course_uuid"):
                values["course_uuid"] = _course_uuid(tconn, resolved)

        uuid = values.get("sync_uuid")
        uuid = str(uuid) if uuid else ""
        key = _identity(table, values)
        if uuid and uuid in uuid_index:
            skipped += 1
            matched_id: int | None = uuid_index[uuid]
        elif key in key_index:
            skipped += 1
            matched_id = key_index[key]
        else:
            if "sync_uuid" in cols and not uuid:
                uuid = timeutil.new_uuid()
                values["sync_uuid"] = uuid
                uuid_generated += 1
            if "created_at" in cols and not values.get("created_at"):
                values["created_at"] = now_iso
            if "updated_at" in cols and not values.get("updated_at"):
                values["updated_at"] = now_iso
            if "sync_dirty" in cols and not row.get("sync_uuid"):
                # 旧库没有同步身份 → 新记录要上传给服务端（示例数据除外）
                values["sync_dirty"] = 0 if row.get("is_sample") else 1
            cur = tconn.execute(insert_sql, tuple(values[c] for c in cols))
            imported += 1
            matched_id = cur.lastrowid
            if uuid:
                uuid_index[uuid] = matched_id
            # 注意：不写入 key_index —— 业务键只与「导入前目标已有的行」比较，
            # 这样源库里真正重复的多行不会被误合并成一条。

        if table == "courses" and matched_id:
            course_map[int(row["id"])] = int(matched_id)

    return {"imported": imported, "skipped": skipped, "orphans": orphans,
            "uuid_generated": uuid_generated, "target_rows_before": before,
            "target_rows_after": _count(tconn, table), "source_rows": _count(sconn, table)}


def _course_uuid(tconn: sqlite3.Connection, course_id: int) -> str | None:
    r = tconn.execute("SELECT sync_uuid FROM courses WHERE id=?", (int(course_id),)).fetchone()
    return r[0] if r and r[0] else None


def _resolve_course_id(tconn: sqlite3.Connection, row: dict[str, Any],
                       course_map: dict[int, int]) -> int | None:
    """课程例外的 course_id 是「旧库坐标系」的整数 id，必须重映射到目标库。"""
    src_id = row.get("course_id")
    if src_id is not None and int(src_id) in course_map:
        return course_map[int(src_id)]
    cuid = row.get("course_uuid")
    if cuid:
        r = tconn.execute("SELECT id FROM courses WHERE sync_uuid=?", (str(cuid),)).fetchone()
        if r is not None:
            return int(r[0])
    return None


def _run_merge(db: "Database", sconn: sqlite3.Connection) -> dict[str, Any]:
    tconn = db.conn
    tables_report: dict[str, Any] = {}
    notes: list[str] = []
    course_map: dict[int, int] = {}
    total_imported = total_skipped = 0
    try:
        for table in MERGE_ORDER:
            if not _has_table(sconn, table):
                notes.append(f"旧库没有 {TABLE_CN.get(table, table)} 表，已跳过")
                continue
            if not _has_table(tconn, table):
                notes.append(f"新库没有 {TABLE_CN.get(table, table)} 表，已跳过")
                continue
            if table == "settings":
                r = _merge_settings(sconn, tconn)
                r["source_rows"] = _count(sconn, "settings")
            else:
                r = _merge_table(sconn, tconn, table, course_map, notes)
            tables_report[table] = r
            total_imported += r["imported"]
            total_skipped += r["skipped"]
        tconn.commit()
    except Exception:
        tconn.rollback()
        raise
    return {
        "mode": "merge",
        "tables": tables_report,
        "total_imported": total_imported,
        "total_skipped": total_skipped,
        "notes": notes,
        "backup": None,
        "target_integrity_check": _integrity(tconn),
    }


# --------------------------------------------------------------- replace
def _run_replace(db: "Database", sconn: sqlite3.Connection) -> dict[str, Any]:
    """整体替换：先备份当前目标库 → 用旧库快照替换文件 → 重连。"""
    data_dir = C.app_data_dir()
    bak_dir = Path(data_dir) / "import_backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = bak_dir / f"labassistant_before_replace_{stamp}.db"
    db.backup_to(backup_path)                      # 在线备份 API，不阻塞

    tmp_dir = Path(tempfile.mkdtemp(prefix="labassistant_import_"))
    snap = tmp_dir / "labassistant_from_labtime.db"
    try:
        # 从只读连接做一致性快照（能带上旧库 WAL 中尚未落盘的页），不触碰旧库文件
        dest = sqlite3.connect(str(snap))
        try:
            sconn.backup(dest)
        finally:
            dest.close()
        if _integrity_via_path(snap) != "ok":
            raise MigrationError("旧库快照完整性检查未通过，已放弃替换")
        db.restore_from(snap)                      # 关闭→复制→重连→补迁移
        tconn = db.conn
        tables_report: dict[str, Any] = {}
        for table in MERGE_ORDER:
            if not _has_table(tconn, table):
                continue
            src_rows = _count(sconn, table) if _has_table(sconn, table) else 0
            now = _count(tconn, table)
            tables_report[table] = {
                "imported": now, "skipped": 0, "orphans": 0, "uuid_generated": 0,
                "target_rows_before": 0, "target_rows_after": now, "source_rows": src_rows,
            }
        return {
            "mode": "replace",
            "tables": tables_report,
            "total_imported": sum(v["imported"] for v in tables_report.values()),
            "total_skipped": 0,
            "notes": [f"已整体替换目标库，替换前的库备份到：{backup_path}"],
            "backup": str(backup_path),
            "target_integrity_check": _integrity(tconn),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _integrity_via_path(path: Path) -> str:
    con = sqlite3.connect(str(path))
    try:
        return _integrity(con)
    finally:
        con.close()


# --------------------------------------------------------------- 入口
def run_import(db: "Database", legacy: str | Path, *, mode: str = "merge") -> dict[str, Any]:
    """执行迁移，返回迁移报告 dict。

    参数
    ----
    db     : 已建好（并完成 schema 迁移）的目标 ``Database``。
    legacy : 旧版 LabTime 数据库路径；**全程只读**。
    mode   : "merge"（默认，幂等导入）或 "replace"（先自动备份再整体替换）。
    """
    if mode not in ("merge", "replace"):
        raise ValueError(f"未知迁移模式：{mode!r}（只支持 merge / replace）")
    src = Path(legacy).expanduser()
    tgt = Path(db.path)
    try:
        if src.resolve() == tgt.resolve():
            raise MigrationError("旧库与目标库是同一个文件，已终止导入")
    except OSError:
        pass

    started = time.perf_counter()
    before = file_fingerprint(src)
    sconn: sqlite3.Connection | None = None
    try:
        sconn = open_readonly(src)                 # 只读 URI，绝不写旧库
        integrity = _integrity(sconn)
        if integrity != "ok":
            raise MigrationError(f"旧库完整性检查未通过（{integrity}），已终止导入")
        src_tables = {t: _count(sconn, t) for t in MERGE_ORDER if _has_table(sconn, t)}
        body = (_run_replace(db, sconn) if mode == "replace" else _run_merge(db, sconn))
    except MigrationError:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise MigrationError(f"导入失败（旧库只读访问或新库写入异常）：{exc}") from exc
    finally:
        if sconn is not None:
            try:
                sconn.close()
            except sqlite3.Error:
                pass

    after = file_fingerprint(src)
    report: dict[str, Any] = {
        "mode": mode,
        "source": str(src),
        "target": str(tgt),
        "legacy_app": C.LEGACY_APP_NAME,
        "integrity_check": integrity,
        "source_tables": src_tables,
        "source_total_rows": sum(src_tables.values()),
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "source_fingerprint_before": before,
        "source_fingerprint_after": after,
        "source_untouched": (before.get("sha256") == after.get("sha256")
                             and before.get("size") == after.get("size")
                             and before.get("mtime_ns") == after.get("mtime_ns")),
        **body,
    }
    record_migration(db, report)
    return report


def record_migration(db: "Database", report: dict[str, Any]) -> None:
    """把迁移来源/时间/报告写进新库设置（供设置页展示与幂等判断）。"""
    import json

    try:
        db.set_setting("migrated_from", str(report.get("source") or ""))
        db.set_setting("migrated_at", str(report.get("started_at") or utc_now_iso()))
        db.set_setting("migration_mode", str(report.get("mode") or "merge"))
        db.set_setting("migration_report", json.dumps(report, ensure_ascii=False, default=str))
        # 已导入真实数据 → 不再自动塞示例数据
        db.set_setting("sample_loaded", "1")
    except sqlite3.Error:
        pass    # 记录失败不影响已完成的导入


def migration_info(db: "Database") -> dict[str, Any]:
    """读取已记录的迁移信息（无迁移记录返回 {}）。"""
    import json

    src = (db.get_setting("migrated_from") or "").strip()
    if not src:
        return {}
    report: Any = None
    raw = db.get_setting("migration_report")
    if raw:
        try:
            report = json.loads(raw)
        except (ValueError, TypeError):
            report = None
    return {
        "migrated_from": src,
        "migrated_at": db.get_setting("migrated_at") or "",
        "mode": db.get_setting("migration_mode") or "",
        "report": report,
    }


def format_report(report: dict[str, Any]) -> str:
    """把迁移报告渲染成中文摘要（用于弹窗）。"""
    lines: list[str] = [
        f"方式：{'导入并合并' if report.get('mode') == 'merge' else '整体替换（已自动备份）'}",
        f"来源：{report.get('source')}",
        f"目标：{report.get('target')}",
        f"耗时：{report.get('elapsed_ms', 0)} ms · 完整性检查：{report.get('integrity_check')}",
        "",
    ]
    tables = report.get("tables") or {}
    for table, r in tables.items():
        if report.get("mode") == "replace":
            lines.append(f"· {TABLE_CN.get(table, table)}：{r.get('target_rows_after', 0)} 条")
        else:
            lines.append(
                f"· {TABLE_CN.get(table, table)}：新增 {r.get('imported', 0)} · "
                f"已存在跳过 {r.get('skipped', 0)}（旧库共 {r.get('source_rows', 0)}）")
    lines += ["",
              f"合计新增 {report.get('total_imported', 0)} 条，跳过 {report.get('total_skipped', 0)} 条。",
              "旧数据库未被修改"
              f"（前后 sha256 {'一致' if report.get('source_untouched') else '不一致！请检查'}）。"]
    if report.get("backup"):
        lines.append(f"替换前的备份：{report['backup']}")
    for note in report.get("notes") or []:
        lines.append(f"说明：{note}")
    return "\n".join(lines)


def format_plan(plan: dict[str, Any]) -> str:
    """把预览计划渲染成一行行的中文文本。"""
    if not plan.get("source_readable"):
        return "未能读取旧数据库（可能已被移动、卸载或权限不足）。"
    parts = [f"{TABLE_CN.get(t, t)} {r['rows']}" for t, r in (plan.get("tables") or {}).items()]
    text = " · ".join(parts) or "（没有可导入的表）"
    return f"共 {plan.get('total_source_rows', 0)} 行：{text}"

"""给 macOS 桌面小组件导出今日快照。

为什么由 App 端算好再导出，而不是让小组件直接读 SQLite：
**统计算法只保留一份实现**。小组件如果自己再实现一遍“实验室时段 ∪ 计入打卡的课程时段
（同日合并去重）+ 手动时长”，两边口径迟早会漂移，桌面方格进度就会和软件里的数字对不上。

写入两处：
1. Widget 自身沙盒容器 `~/Library/Containers/com.labassistant.desktop.host.widget/Data/…`
   —— 系统扩展实际读取的位置；主应用与扩展须由同一开发者 Team 签名。
2. 主应用数据目录 `~/Library/Application Support/LabAssistant/`
   —— 自检兜底，也方便直接查看内容。

Personal Team 的 profile 未授权 App Groups，不应尝试写入其受保护容器。

写文件一律「临时文件 + os.replace」原子替换，小组件不会读到半截 JSON。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from labassistant import constants as C
from labassistant.db import Database
from labassistant.services import aggregate as agg

WIDGET_BUNDLE_ID = "com.labassistant.desktop.host.widget"
SNAPSHOT_NAME = "widget_snapshot.json"
SCHEMA = 1

_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def widget_container_dir() -> Path:
    """Widget 沙盒内 `homeDirectoryForCurrentUser/Library/Application Support/LabAssistant` 的宿主侧路径。"""
    return (Path.home() / "Library" / "Containers" / WIDGET_BUNDLE_ID / "Data"
            / "Library" / "Application Support" / "LabAssistant")


def snapshot_paths() -> list[Path]:
    """按小组件侧的读取优先级返回落盘位置。"""
    return [
        widget_container_dir() / SNAPSHOT_NAME,
        C.app_data_dir() / SNAPSHOT_NAME,
    ]


def _hhmm(minute_of_day: int) -> str:
    m = max(0, int(minute_of_day)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _hours(minutes: int) -> str:
    """和软件里一致的时长文案：整数就不带小数。"""
    h = (float(minutes) or 0) / 60.0
    return f"{h:g}" if abs(h - round(h)) < 1e-6 else f"{h:.2f}"


def build_snapshot(db: Database, today: date | None = None) -> dict[str, Any]:
    """组装今日快照（纯读，不写库）。"""
    today = today or date.today()
    day = agg.day_summary(db, today)
    occ = [o for o in day["occ"] if o["state"] != "cancelled"]
    todos = day["todos"] or []
    open_todos = [t for t in todos if not t["done"]]
    done_todos = [t for t in todos if t["done"]]
    req = int(day["required_min"] or 0)
    eff = round(day["effective_min"] or 0)

    # 下一条要上的课（按开始时间排序，取还没结束的）
    now_min = datetime.now().hour * 60 + datetime.now().minute
    upcoming = sorted((o for o in occ if day["kind"] == "workday" and o["disp_end_min"] > now_min),
                      key=lambda o: o["disp_start_min"])
    nxt = upcoming[0] if upcoming else None
    # 最近一条未完成待办（按 id 即录入顺序）
    nxt_todo = open_todos[0]["title"] if open_todos else ""
    important = next((t["title"] for t in open_todos if (t.get("priority") or "") == "高"), "")

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": C.VERSION,
        "date": day["iso"],
        "day_num": today.day,
        "month_cn": f"{today.month}月",
        "weekday_cn": _WEEKDAY_CN[today.weekday()],
        "kind": day["kind"],
        "holiday_name": day["holiday_name"] or "",
        "status_key": day["status_key"],
        "status_label": day["status_label"],
        "is_today": bool(day["is_today"]),
        "required_min": req,
        "effective_min": eff,
        "ratio": round(eff / req, 4) if req else (1.0 if eff else 0.0),
        "lab_min": int(day["lab_min"] or 0),
        "course_min": int(day["course_min"] or 0),
        "manual_min": round(day["manual_min"] or 0),
        "overlap_min": int(day["overlap_min"] or 0),
        "courses": [
            {
                "name": o["name"],
                "start": _hhmm(o["disp_start_min"]),
                "end": _hhmm(o["disp_end_min"]),
                "location": o.get("location") or "",
                "state": o["state"],
                "counts": day["kind"] == "workday" and bool(o.get("count_attendance", 1)),
            }
            for o in sorted(occ, key=lambda x: x["disp_start_min"])
        ],
        "lab_blocks": [
            {"start": _hhmm(b["start_min"]), "end": _hhmm(b["end_min"])}
            for b in day["lab_blocks"] or []
        ],
        "next_course": (
            {"name": nxt["name"], "start": _hhmm(nxt["disp_start_min"]),
             "location": nxt.get("location") or ""} if nxt else None),
        "todo_open": len(open_todos),
        "todo_done": len(done_todos),
        "todo_titles": [t["title"] for t in open_todos[:4]],
        "next_todo": nxt_todo,
        "important_event": important,
    }


def _next_boundary(now: datetime | None = None) -> str:
    """每 5 分钟检查最新快照；跨日时优先在 00:00 切换。"""
    now = now or datetime.now()
    next_five = (now + timedelta(minutes=5)).replace(second=0, microsecond=0)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return min(next_five, midnight).astimezone().isoformat(timespec="seconds")


def write_snapshot(db: Database, today: date | None = None) -> dict[str, Any]:
    """生成并原子写入快照；返回 {paths, bytes, error}。失败不抛异常，交由调用方记日志。"""
    data = build_snapshot(db, today)
    data["next_refresh"] = _next_boundary()
    payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    out: dict[str, Any] = {"paths": [], "errors": []}
    for path in snapshot_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_bytes(payload)
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)      # 今日课表与待办也算隐私内容
            except OSError:
                pass
            out["paths"].append(str(path))
        except OSError as exc:
            out["errors"].append(f"{path}: {exc}")
    out["bytes"] = len(payload)
    out["date"] = data["date"]
    return out


_last_write_ts = 0.0


def refresh(db: Database, *, force: bool = False, min_interval: float = 20.0) -> dict | None:
    """带节流的刷新：数据频繁变动时最多每 min_interval 秒落一次盘。"""
    global _last_write_ts
    now = time.monotonic()
    if not force and now - _last_write_ts < min_interval:
        return None
    result = write_snapshot(db)
    _last_write_ts = now
    return result


def read_snapshot() -> dict[str, Any] | None:
    """读回当前快照（自检 / 命令行查看用）。"""
    for path in snapshot_paths():
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None

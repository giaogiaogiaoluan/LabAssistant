#!/usr/bin/env python3
"""命令行刷新 macOS 桌面小组件的今日快照。

平时不需要手动跑：LabAssistant 启动、数据变动、以及每 5 分钟都会自动写。
这个入口是给「不想开界面也要刷新」或配 cron 的场景用的。

    .venv-mac/bin/python scripts/refresh_widget.py          # 重写并打印摘要
    .venv-mac/bin/python scripts/refresh_widget.py --show   # 只打印当前快照内容
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from labassistant.db import Database          # noqa: E402
from labassistant.services import widget_snapshot as w   # noqa: E402


def main(argv: list[str]) -> int:
    if "--show" in argv:
        snap = w.read_snapshot()
        if not snap:
            print("还没有快照，先运行一次：python scripts/refresh_widget.py")
            return 1
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0

    db = Database()
    try:
        res = w.write_snapshot(db)
    finally:
        db.close()
    print(f"今日 {res['date']} · 写入 {res['bytes']} 字节")
    for path in res["paths"]:
        print(f"  ✅ {path}")
    for err in res["errors"]:
        print(f"  ⚠️ {err}")
    snap = w.read_snapshot() or {}
    print(f"  有效 {snap.get('effective_min', 0)} 分 / 目标 {snap.get('required_min', 0)} 分"
          f" · {snap.get('status_label', '')} · 待办 {snap.get('todo_open', 0)} 项")
    return 0 if res["paths"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

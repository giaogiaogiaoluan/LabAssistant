"""全局常量与路径定位。

LabAssistant = LabTime 的液态玻璃重设计版本。
数据策略：**独立数据库**（首次启动从旧版只读复制迁移），旧版 LabTime 的库永不被本程序写入。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "LabAssistant"
APP_TITLE_CN = "实验室时间与日程助手"
VERSION = "2.0.1"

# 旧版 LabTime 的数据位置（只读探测 → 迁移来源，按优先级排列）
LEGACY_APP_NAME = "LabTime"
LEGACY_DB_NAME = "labtime.db"

MIN_PER_DAY = 24 * 60          # 一天 1440 分钟
DEFAULT_DAILY_MINUTES = 8 * 60  # 默认每日要求 8 小时
DEFAULT_WORKDAYS = "0,1,2,3,4"  # 周一~周五（Python weekday: 周一=0）
WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PRIORITIES = ["高", "中", "低"]

# 状态/颜色语义键（供 UI 主题映射）
STATE_DONE = "done"
STATE_PARTIAL = "partial"
STATE_NONE = "none"
STATE_WEEKEND = "weekend"
STATE_HOLIDAY = "holiday"
STATE_WEEKEND_DONE = "weekend_done"
STATE_HOLIDAY_DONE = "holiday_done"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """src/labassistant/constants.py -> parents[2] = 项目根目录。"""
    return Path(__file__).resolve().parents[2]


def mac_app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support"


_data_dir_cache: Path | None = None


def app_data_dir() -> Path:
    """数据目录（自动选择第一个可用的位置，带缓存）。

    macOS 打包版把**标准位置优先**放在 `~/Library/Application Support/LabAssistant`，
    避免像旧版那样把数据写进 `.app` 包内（重装 / 更新即可能丢失）。

    1) 环境变量 LABASSISTANT_DATA_DIR 强制指定；
    2) macOS 打包版：~/Library/Application Support/LabAssistant（推荐）；
    3) 其它打包版：exe 同级 data\\（便携模式，数据跟随程序，可整个文件夹拷走）；
    4) 打包版上述不可写时回退 %APPDATA%/LabAssistant；
    5) 源码运行：项目根目录 data/。
    """
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache
    candidates: list[Path] = []
    override = os.environ.get("LABASSISTANT_DATA_DIR")
    if override:
        candidates.append(Path(override))
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        if sys.platform == "darwin":
            candidates.append(mac_app_support_dir() / APP_NAME)
            candidates.append(exe_dir / "data")
        else:
            candidates.append(exe_dir / "data")
        base = Path(os.environ.get("APPDATA") or Path.home())
        candidates.append(base / APP_NAME)
    else:
        candidates.append(project_root() / "data")
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            _data_dir_cache = c
            return c
        except OSError:
            continue
    # 全部失败：抛出第一个候选的错误，交由上层友好提示
    c = candidates[0]
    c.mkdir(parents=True, exist_ok=True)
    _data_dir_cache = c
    return c


def db_path() -> Path:
    return app_data_dir() / "labassistant.db"


def asset_file(name: str) -> str:
    """定位资源文件（源码运行：项目 assets/；打包后：_MEIPASS/assets/）。"""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", str(Path(sys.executable).resolve().parent))
        return str(Path(base) / "assets" / name)
    return str(project_root() / "assets" / name)


def legacy_db_candidates() -> list[Path]:
    """本机可能存在的旧版 LabTime 数据库位置（只读探测用，按可信度排序）。"""
    found: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        try:
            rp = p.expanduser().resolve()
        except OSError:
            return
        if rp not in seen:
            seen.add(rp)
            found.append(rp)

    env = os.environ.get("LABTIME_DATA_DIR")
    if env:
        add(Path(env) / LEGACY_DB_NAME)
    if sys.platform == "darwin":
        add(mac_app_support_dir() / LEGACY_APP_NAME / LEGACY_DB_NAME)
        add(Path("/Applications") / f"{LEGACY_APP_NAME}.app" / "Contents" / "MacOS"
            / "data" / LEGACY_DB_NAME)
    add(Path.home() / "Applications" / f"{LEGACY_APP_NAME}.app" / "Contents" / "MacOS"
        / "data" / LEGACY_DB_NAME)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
        add(base / LEGACY_APP_NAME / LEGACY_DB_NAME)
    # 旧版源码工程里的 data/
    root = project_root()
    for sibling in ("LabTime-macOS", "LabTime"):
        add(root.parent / sibling / "data" / LEGACY_DB_NAME)
    return found

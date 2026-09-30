"""时间与唯一标识工具：同步字段统一使用 UTC ISO8601（如 2026-09-08T08:30:00Z）。

客户端展示时才转本地时区。时间比较统一用 UTC 字符串的词典序（ISO 格式保证一致）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow_iso() -> str:
    """当前 UTC 时间，ISO8601（含微秒，避免同秒操作被误判为重复）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_device_id() -> str:
    return str(uuid.uuid4())


def iso_after(a: str | None, b: str | None) -> bool:
    """a > b（按 UTC ISO 比较）。None 视为最小值。"""
    return (a or "") > (b or "")


def iso_max(a: str | None, b: str | None) -> str | None:
    return a if (a or "") >= (b or "") else b

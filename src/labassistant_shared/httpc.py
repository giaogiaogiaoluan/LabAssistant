"""极简 JSON HTTP 客户端（urllib，无第三方依赖）。超时短，失败不抛异常而是返回错误串。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def http_json(
    method: str,
    url: str,
    token: str | None = None,
    payload: Any = None,
    timeout: float = 4.0,
) -> tuple[int | None, Any, str]:
    """发送请求，返回 (status, json_or_None, error)。网络错误返回 (None, None, err)。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            body: Any = None
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = raw.decode("utf-8", "replace")[:2000]
            return resp.status, body, ""
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = e.read().decode("utf-8", "replace")[:1500]
        except Exception:
            msg = ""
        return e.code, (json.loads(msg) if msg.startswith("{") else None), msg
    except Exception as exc:  # noqa: BLE001 网络错误（超时/拒绝/解析）
        return None, None, str(exc)

"""共享工具：时间、JSON、ID 规范化。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional


def now() -> str:
    """RFC3339 毫秒级 UTC 字符串，排序安全。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def unix_of(ts: Optional[str]) -> Optional[int]:
    """RFC3339 -> unix 秒；无效返回 None。"""
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def jloads(s: Optional[str], default: Any = None) -> Any:
    """JSON 字符串 -> 对象；空/无效返回 default。"""
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def jdumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def sid(v: Any) -> str:
    """数据库整数 ID -> API 字符串 ID。"""
    return str(v) if v is not None else None


def norm_key(name: str) -> str:
    """nkey：小写 + 压缩空白。"""
    return re.sub(r"\s+", " ", name.strip().lower())

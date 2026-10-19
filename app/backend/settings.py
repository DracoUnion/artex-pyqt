"""系统设置服务：settings 键值读写 + 全局默认配置。"""
from __future__ import annotations

import json
from typing import Any

from .db import DB
from .util import now


class SettingsService:
    DEFAULTS = {
        "traffic_enabled": "true",
        "traffic_proxy": ":8788",
        "llm_record_enabled": "true",
        "web_search_enabled": "false",
        "web_search_backend": "",
        "web_search_api_key": "",
        "global_proxy": "",
        "python_interpreter": "",
        "worker_concurrency": "3",
        "constraint_inject_planner": "true",
        "constraint_inject_worker": "true",
        "experimental_noa_compress": "false",
        "chat_send_key": "Enter",
        "update_github_token": "",
    }

    def __init__(self, db: DB):
        self.db = db

    def all(self) -> dict[str, str]:
        out = dict(self.DEFAULTS)
        rows = self.db.query("SELECT key, value FROM settings")
        for r in rows:
            out[r["key"]] = r["value"]
        return out

    def get(self, key: str, default: Any = None) -> Any:
        v = self.all().get(key, default)
        return v

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.all().get(key)
        if v is None:
            return default
        return v.lower() in ("true", "1", "yes", "on")

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.all().get(key, default))
        except Exception:
            return default

    def set(self, key: str, value: str) -> None:
        ts = now()
        self.db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, ts),
        )

    def update(self, data: dict) -> None:
        for k, v in data.items():
            self.set(k, str(v))
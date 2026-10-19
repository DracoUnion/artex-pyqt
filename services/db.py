"""SQLite 存储层：连接管理 + schema 初始化 + 行/JSON 辅助。

单进程内多线程使用：WAL 模式 + 全局写锁。桌面单用户场景并发低，
一把 RLock 串行化写即可满足引擎 worker / SSE / API 的并发访问。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Optional

from .util import now as _now

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


class DB:
    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "artex.db")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.init_schema()

    def init_schema(self) -> None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            sql = f.read()
        with self._lock:
            self.conn.executescript(sql)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """旧库补列（SQLite 无 ADD COLUMN IF NOT EXISTS）。"""
        cols = {
            "conversation_activities": [("metadata", "TEXT NOT NULL DEFAULT '{}'")],
            "activity": [("metadata", "TEXT NOT NULL DEFAULT '{}'")],
            "exploration_nodes": [("delete_reason", "TEXT"), ("blocked_reason", "TEXT")],
        }
        for table, adds in cols.items():
            exist = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, ddl in adds:
                if name not in exist:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    # ---- 基础查询 ------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: list) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self.conn.execute(sql, params)
            return cur.fetchone()

    def scalar(self, sql: str, params: tuple = ()) -> Any:
        with self._lock:
            cur = self.conn.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

    def last_id(self) -> int:
        return self.scalar("SELECT last_insert_rowid()")

    def in_transaction(self) -> None:
        """供事务性逻辑：外部手动 begin/commit。"""
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# ---- 行访问辅助 ---------------------------------------------------------
def get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        v = row[key]
        return default if v is None else v
    except Exception:
        return default


def jfield(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    """读取行内的 JSON 文本列。"""
    return json.loads(get(row, key, None) or "null") if get(row, key) else default

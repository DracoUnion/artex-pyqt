"""后端日志：环形缓冲 + DB 持久化 + SSE 订阅。

log_sink 是对接 Go logSink 的实现：slog 风格 logger 写入内存环形缓冲
(cap 3000) + server_logs 表 + 推送订阅者。
"""
from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from typing import Optional

from .util import now


class LogSink:
    def __init__(self, db=None, cap: int = 3000):
        self._cap = cap
        self._buf: deque = deque(maxlen=cap)
        self._lock = threading.Lock()
        self._seq = 0
        self._subs: list = []
        self.db = db
        self._logger = logging.getLogger("artex")

    def log(self, level: str, text: str, tag: str = "") -> None:
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq, "ts": now(), "level": level, "tag": tag, "text": text}
            self._buf.append(entry)
            subs = list(self._subs)
            db = self.db
        if db is not None:
            try:
                db.execute(
                    "INSERT INTO server_logs(created_at, level, tag, text) VALUES(?,?,?,?)",
                    (entry["ts"], level, tag, text),
                )
            except Exception:
                pass
        for sub in subs:
            try:
                sub.put_nowait(entry)
            except Exception:
                pass
        if level in ("error", "critical"):
            self._logger.error(f"[{tag}] {text}")
        elif level == "warning":
            self._logger.warning(f"[{tag}] {text}")
        else:
            self._logger.info(f"[{tag}] {text}")

    def info(self, text: str, tag: str = ""):
        self.log("info", text, tag)

    def warn(self, text: str, tag: str = ""):
        self.log("warning", text, tag)

    def error(self, text: str, tag: str = ""):
        self.log("error", text, tag)

    def subscribe(self, q) -> None:
        with self._lock:
            self._subs.append(q)

    def unsubscribe(self, q) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def history(self, since: int = 0, limit: int = 200) -> list:
        with self._lock:
            rows = [e for e in self._buf if e["seq"] > since]
        return rows[-limit:]

    def before(self, before_seq: int, limit: int = 200) -> list:
        with self._lock:
            rows = [e for e in self._buf if e["seq"] < before_seq]
        return rows[-limit:]

    def last_seq(self) -> int:
        with self._lock:
            return self._seq

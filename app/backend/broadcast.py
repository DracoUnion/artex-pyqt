"""进程内广播：Broadcaster（任务 activity pub/sub）+ 通用 SSE 辅助。

订阅端跑在 uvicorn 事件循环线程；发布端来自引擎 worker 线程。
用标准库 queue.Queue（线程安全）+ asyncio 侧 to_thread 轮询。
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections import defaultdict
from typing import Any, Optional


class Broadcaster:
    """每任务 pub/sub。Publish 非阻塞扇出；客户端用 seq 游标回源 DB 补漏。"""

    def __init__(self, buffer_size: int = 256):
        self._lock = threading.Lock()
        self._subs: dict[Any, list["_Sub"]] = defaultdict(list)
        self._buffer_size = buffer_size
        self._seq = 0

    def subscribe(self, task_key: Any) -> "_Sub":
        with self._lock:
            self._seq += 1
            sub = _Sub(f"sub-{self._seq}", queue.Queue(maxsize=self._buffer_size))
            self._subs[task_key].append(sub)
            return sub

    def unsubscribe(self, task_key: Any, sub: "_Sub") -> None:
        with self._lock:
            try:
                self._subs[task_key].remove(sub)
                if not self._subs[task_key]:
                    del self._subs[task_key]
            except ValueError:
                pass

    def publish(self, task_key: Any, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(task_key, []))
        for sub in subs:
            try:
                sub.queue.put_nowait(event)
            except queue.Full:
                pass  # 丢弃：客户端用 seq 回源

    def close(self, task_key: Any) -> None:
        with self._lock:
            self._subs.pop(task_key, None)


class _Sub:
    def __init__(self, name: str, q: queue.Queue):
        self.name = name
        self.queue = q


async def next_event(sub, timeout: float = 15.0):
    """异步等待下一条事件；sub 可为 _Sub 或裸 queue.Queue；超时返回 None。"""
    q = sub.queue if isinstance(sub, _Sub) else sub
    try:
        return await asyncio.to_thread(q.get, True, timeout)
    except queue.Empty:
        return None


def sse_event(data: Any, event: Optional[str] = None, eid: Optional[str] = None) -> str:
    """拼 SSE 帧。data 为对象时 JSON 序列化。"""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)
    parts = []
    if eid is not None:
        parts.append(f"id: {eid}")
    if event:
        parts.append(f"event: {event}")
    for line in payload.splitlines():
        parts.append(f"data: {line}")
    return "\n".join(parts) + "\n\n"

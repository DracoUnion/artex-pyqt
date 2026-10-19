"""PySide6 API 客户端：进程内直接调用后端 services（无 HTTP/SSE）。

替代原先的 HTTP + SSE 传输：ApiClient 直接走 gateway.dispatch，SSE 由
进程内 queue（Broadcaster / LogSink）订阅驱动，经 QThread 桥接到 Qt 信号。
对外保持原有接口面（get/post/put/patch/delete/upload + SseWorker/SseManager），
页面无需改动。
"""
from __future__ import annotations

import os
import queue
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from services.gateway import Gateway, GatewayError


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


class ApiClient:
    """进程内客户端，直接分发到后端 services。"""

    def __init__(self, gateway: Gateway):
        self.gateway = gateway

    # ---- 请求 ------------------------------------------------------
    def request(self, method: str, path: str, **kw) -> Any:
        params = dict(kw.pop("params", None) or {})
        json_body = kw.pop("json", None)
        files = kw.pop("files", {})
        # 兼容形如 "/api/report?task=1" 的 path 内嵌 query
        if "?" in path:
            base, _, qs = path.partition("?")
            for kv in qs.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params.setdefault(k, v)
            path = base
        try:
            return self.gateway.dispatch(method, path, params=params, body=json_body, files=files)
        except GatewayError as e:
            raise ApiError(e.message, e.status)
        except ValueError as e:
            raise ApiError(str(e), 400)
        except Exception as e:
            self.gateway.logsink.error(f"API 异常 {method} {path}: {e}", tag="api")
            raise ApiError(f"服务器内部错误: {e}", 500)

    def get(self, path: str, params: Optional[dict] = None, **kw) -> Any:
        return self.request("GET", path, params=params, **kw)

    def post(self, path: str, json: Any = None, **kw) -> Any:
        return self.request("POST", path, json=json, **kw)

    def put(self, path: str, json: Any = None, **kw) -> Any:
        return self.request("PUT", path, json=json, **kw)

    def patch(self, path: str, json: Any = None, **kw) -> Any:
        return self.request("PATCH", path, json=json, **kw)

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)

    def upload(self, path: str, file_path: str, field: str = "file", extra: Optional[dict] = None) -> Any:
        with open(file_path, "rb") as f:
            content = f.read()
        return self.request("POST", path, params=extra or {},
                            files={"file": content, "filename": os.path.basename(file_path)})


class SseWorker(QThread):
    """进程内 SSE 替代：订阅 Broadcaster / LogSink 队列并桥接为信号。

    emit: event(dict) 事件负载；status(str) connected / error。
    停止通过 _running 标志 + 哨兵唤醒，避免线程永不退出。
    """

    event = Signal(dict)
    status = Signal(str)
    _SENTINEL = object()

    def __init__(self, gateway: Gateway, path: str, params: Optional[dict] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.gateway = gateway
        # 拆分 path 内嵌 query（如 "/api/.../stream?task=X"）
        base_path, _, qs = (path or "").partition("?")
        self.path = base_path
        self.params = dict(params or {})
        if qs:
            for kv in qs.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    self.params.setdefault(k, v)
        self._running = True
        self._q: Optional[queue.Queue] = None
        self._close = None

    def stop(self) -> None:
        self._running = False
        if self._q is not None:
            try:
                self._q.put_nowait(self._SENTINEL)
            except Exception:
                pass
        self.wait(3000)

    # 兼容旧接口：run 内首次连接后即置 connected
    def run(self) -> None:
        try:
            snapshot = self.gateway.stream_snapshot(self.path, self.params)
            if self.path == "/api/exploration/activity/stream":
                self.event.emit({"type": "snapshot", "items": snapshot.get("items", []),
                                 "cursor": snapshot.get("cursor", 0)})
            else:
                for e in snapshot.get("items", []):
                    self.event.emit(e)
            # 订阅
            try:
                self._q, self._close = self.gateway.stream_subscribe(self.path, self.params)
            except GatewayError:
                self.status.emit("error")
                return
            self.status.emit("connected")
            self._read_loop()
        except Exception as e:
            if self._running:
                self.gateway.logsink.error(f"SSE {self.path}: {e}", tag="sse")
                self.status.emit("error")

    def _read_loop(self) -> None:
        while self._running:
            try:
                ev = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if ev is self._SENTINEL or not self._running:
                break
            # activity 事件透传；页面按 seq 去重；logs 直接透传
            self.event.emit(ev)
        self._cleanup()

    def _cleanup(self) -> None:
        if self._close is not None:
            try:
                self._close()
            except Exception:
                pass
            self._close = None


class SseManager(QObject):
    """管理多个 SseWorker，暴露统一事件流接口（页面沿用原用法）。"""

    def __init__(self, client: ApiClient, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.client = client
        self._workers: dict[str, SseWorker] = {}

    def subscribe(self, key: str, path: str, params: Optional[dict] = None) -> SseWorker:
        worker = SseWorker(self.client.gateway, path, params, self)
        self._workers[key] = worker
        worker.start()
        return worker

    def unsubscribe(self, key: str) -> None:
        worker = self._workers.pop(key, None)
        if worker:
            worker.stop()

    def shutdown(self) -> None:
        for w in list(self._workers.values()):
            w.stop()
        self._workers.clear()
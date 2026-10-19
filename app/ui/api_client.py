"""PySide6 API 客户端：HTTP + SSE 订阅。

对齐 web/lib/api.ts 的约定：Bearer token、401 全局登出、{error} 解析。
SSE 用 QThread 读流，按事件发信号。
"""
from __future__ import annotations

import json
import queue
from typing import Any, Optional

import requests
from PySide6.QtCore import QObject, QThread, Signal


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = None
        self.session = requests.Session()

    def set_token(self, token: Optional[str]) -> None:
        self._token = token

    @property
    def token(self) -> Optional[str]:
        return self._token

    def request(self, method: str, path: str, **kw) -> Any:
        url = f"{self.base_url}{path}"
        headers = dict(kw.pop("headers", {}) or {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = self.session.request(method, url, headers=headers, timeout=kw.pop("timeout", 60), **kw)
        except requests.RequestException as e:
            raise ApiError(f"网络错误: {e}")
        if resp.status_code == 401:
            raise ApiError("未授权或登录已过期", 401)
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            data = resp.json()
        else:
            return resp.text
        if resp.status_code >= 400:
            if isinstance(data, dict) and "error" in data:
                raise ApiError(data["error"], resp.status_code)
            raise ApiError(f"请求失败 ({resp.status_code})", resp.status_code)
        return data

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
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        with open(file_path, "rb") as f:
            resp = self.session.post(url, headers=headers, files={field: f}, data=extra or {},
                                     timeout=300)
        if resp.status_code >= 400:
            try:
                d = resp.json()
                raise ApiError(d.get("error", f"上传失败 ({resp.status_code})"), resp.status_code)
            except ValueError:
                raise ApiError(f"上传失败 ({resp.status_code})", resp.status_code)
        try:
            return resp.json()
        except ValueError:
            return {"ok": True}


class SseWorker(QThread):
    """SSE 订阅线程：连接、解析 data 帧、发信号、断线自动重连。"""

    event = Signal(dict)
    status = Signal(str)  # connected / disconnected / error

    def __init__(self, client: ApiClient, path: str, params: Optional[dict] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self.client = client
        self.path = path
        self.params = params or {}
        self._running = True
        self._last_event_id: Optional[str] = None

    def stop(self) -> None:
        self._running = False
        self.wait(3000)

    def run(self) -> None:
        while self._running:
            try:
                self._connect_once()
            except Exception as e:
                if self._running:
                    self.status.emit("error")
            if self._running:
                import time
                time.sleep(1.5)  # 重连间隔

    def _connect_once(self) -> None:
        url = f"{self.client.base_url}{self.path}"
        headers = {}
        params = dict(self.params)
        if self.client.token:
            params["token"] = self.client.token
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id
        with requests.get(url, headers=headers, params=params, stream=True, timeout=(5, 3600)) as resp:
            if resp.status_code == 401:
                self.status.emit("error")
                return
            self.status.emit("connected")
            buf = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if not self._running:
                    break
                if raw is None:
                    continue
                if raw.startswith(":"):
                    continue  # 心跳注释
                if raw.startswith("id:"):
                    self._last_event_id = raw[3:].strip()
                    continue
                if raw.startswith("data:"):
                    buf += raw[5:].strip() + "\n"
                    continue
                if raw == "" and buf:
                    try:
                        payload = json.loads(buf)
                    except Exception:
                        payload = {"raw": buf}
                    self.event.emit(payload)
                    buf = ""


class SseManager(QObject):
    """管理多个 SseWorker，暴露统一事件流。"""

    def __init__(self, client: ApiClient, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.client = client
        self._workers: dict[str, SseWorker] = {}

    def subscribe(self, key: str, path: str, params: Optional[dict] = None) -> SseWorker:
        worker = SseWorker(self.client, path, params, self)
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

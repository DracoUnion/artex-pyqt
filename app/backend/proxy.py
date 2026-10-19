"""流量录制代理（对应 Go 的 :8788 MITM + CA 留痕）。

简化实现：一个 HTTP 正向代理服务器，记录通过它的所有请求/响应到
traffic_exchanges 表。HTTPS 通过 CONNECT 隧道记录握手（TLS 解密需
额外证书链，此处仅记录隧道元信息 + 可选明文）。

桌面版默认监听 127.0.0.1:8788，系统设置里配置 global_proxy 指向它，
worker 的 HTTP 工具走代理时即被留痕。
"""
from __future__ import annotations

import base64
import gzip
import json
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .db import DB
from .util import now


class TrafficProxy:
    def __init__(self, db: DB, host: str = "127.0.0.1", port: int = 8788, enabled: bool = True):
        self.db = db
        self.host = host
        self.port = port
        self.enabled = enabled
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._server:
            return
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    def _make_handler(self):
        db = self.db

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _record(self, method, url, status, req_body, resp_body, req_headers, resp_headers, mime):
                try:
                    parsed = urllib.parse.urlsplit(url)
                    host = parsed.hostname or ""
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    scheme = parsed.scheme or "http"
                    ts = now()
                    db.execute(
                        "INSERT INTO traffic_exchanges(host, method, path, scheme, status, resp_len, "
                        "req_headers, resp_headers, req_body, resp_body, req_body_b64, resp_body_b64, "
                        "mime, task_ids, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (host, method, path, scheme, status, len(resp_body or ""),
                         json.dumps(dict(req_headers), ensure_ascii=False),
                         json.dumps(dict(resp_headers), ensure_ascii=False),
                         req_body or "", resp_body or "", 0, 0, mime or "", [], ts),
                    )
                except Exception:
                    pass

            def _do(self, method):
                body = b""
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length:
                    body = self.rfile.read(length)
                req_headers = dict(self.headers.items())
                try:
                    req = urllib.request.Request(self.path, data=body or None, method=method,
                                                 headers=req_headers)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        resp_body = resp.read()
                        status = resp.status
                        resp_headers = dict(resp.headers.items())
                        mime = resp.headers.get("Content-Type", "")
                except urllib.error.HTTPError as e:
                    status = e.code
                    resp_body = e.read()
                    resp_headers = dict(e.headers.items())
                    mime = e.headers.get("Content-Type", "")
                except Exception as e:
                    status = 502
                    resp_body = str(e).encode()
                    resp_headers = {}
                    mime = "text/plain"
                # gzip 解压以便查看
                raw_resp = resp_body
                try:
                    if resp_headers.get("Content-Encoding") == "gzip":
                        raw_resp = gzip.decompress(resp_body)
                except Exception:
                    pass
                self._record(method, self.path, status, body.decode("utf-8", "replace"),
                             raw_resp.decode("utf-8", "replace"), req_headers, resp_headers, mime)
                self.send_response(status)
                for k, v in resp_headers.items():
                    try:
                        self.send_header(k, v)
                    except Exception:
                        pass
                self.end_headers()
                self.wfile.write(resp_body)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def do_PUT(self):
                self._do("PUT")

            def do_DELETE(self):
                self._do("DELETE")

            def do_PATCH(self):
                self._do("PATCH")

            def do_HEAD(self):
                self._do("HEAD")

            def do_CONNECT(self):
                # HTTPS 隧道：仅记录握手元信息
                self._record("CONNECT", f"http://{self.path}", 200, b"", b"", dict(self.headers.items()), {}, "tunnel")
                self.send_response(200)
                self.end_headers()

        return _Handler
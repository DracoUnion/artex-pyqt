"""流量录制服务：交换记录浏览 / 过滤 / 删除 / 主机列表。"""
from __future__ import annotations

from typing import Any, Optional

from .db import DB
from .util import jloads, now, sid, unix_of


class TrafficService:
    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink

    def list(self, host: Optional[str] = None, method: Optional[str] = None,
             q: Optional[str] = None, body: Optional[str] = None, path: Optional[str] = None,
             status: Optional[int] = None, resp_min: Optional[int] = None,
             resp_max: Optional[int] = None, sort: str = "ts", order: str = "desc",
             page: int = 0, size: int = 20) -> dict:
        where, params = [], []
        if host:
            where.append("host LIKE ?")
            params.append(f"%{host}%")
        if method:
            where.append("method=?")
            params.append(method.upper())
        if q:
            where.append("(url LIKE ? OR path LIKE ?)")
            params.extend([f"%{q}%"] * 2)
        if body:
            where.append("(req_body LIKE ? OR resp_body LIKE ?)")
            params.extend([f"%{body}%"] * 2)
        if path:
            where.append("path LIKE ?")
            params.append(f"%{path}%")
        if status:
            where.append("status=?")
            params.append(int(status))
        if resp_min is not None:
            where.append("resp_len>=?")
            params.append(int(resp_min))
        if resp_max is not None:
            where.append("resp_len<=?")
            params.append(int(resp_max))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.scalar(f"SELECT COUNT(*) FROM traffic_exchanges{clause}", tuple(params))
        sort_col = {"ts": "created_at", "status": "status", "resp_len": "resp_len"}.get(sort, "created_at")
        od = "ASC" if order == "asc" else "DESC"
        rows = self.db.query(
            f"SELECT * FROM traffic_exchanges{clause} ORDER BY {sort_col} {od}, id {od} LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        exchanges = [self._exchange_dto(r) for r in rows]
        return {
            "exchanges": exchanges, "total": total, "page": page, "size": size,
            "enabled": True, "proxy": "127.0.0.1:8788", "count": total,
        }

    def _exchange_dto(self, r) -> dict:
        return {
            "id": sid(r["id"]), "ts": r["created_at"], "host": r["host"],
            "method": r["method"], "url": r["scheme"] + "://" + r["host"] + r["path"] if r["scheme"] else r["host"] + r["path"],
            "status": r["status"], "content_type": r["mime"], "resp_len": r["resp_len"],
            "task_ids": jloads(r["task_ids"], []),
        }

    def get_exchange(self, eid: Any) -> dict:
        r = self.db.query_one("SELECT * FROM traffic_exchanges WHERE id=?", (int(eid),))
        if not r:
            raise ValueError("流量记录不存在")
        d = dict(r)
        d["id"] = sid(d["id"])
        d["req_headers"] = jloads(d["req_headers"], {})
        d["resp_headers"] = jloads(d["resp_headers"], {})
        d["task_ids"] = jloads(d["task_ids"], [])
        return d

    def hosts(self) -> list[dict]:
        rows = self.db.query(
            "SELECT host, COUNT(*) AS count, MAX(created_at) AS last FROM traffic_exchanges GROUP BY host ORDER BY count DESC"
        )
        return [{"host": r["host"], "count": r["count"], "last": r["last"]} for r in rows]

    def delete_by_host(self, host: str) -> int:
        cur = self.db.execute("DELETE FROM traffic_exchanges WHERE host LIKE ?", (f"%{host}%",))
        return cur.rowcount

    def delete_host(self, host: str) -> int:
        cur = self.db.execute("DELETE FROM traffic_exchanges WHERE host=?", (host,))
        return cur.rowcount

    def clear_all(self) -> int:
        cur = self.db.execute("DELETE FROM traffic_exchanges")
        return cur.rowcount

    def delete_ids(self, ids: list) -> int:
        cnt = 0
        for i in ids:
            cur = self.db.execute("DELETE FROM traffic_exchanges WHERE id=?", (int(i),))
            cnt += cur.rowcount
        return cnt

    def record(self, host: str, method: str, path: str, scheme: str, status: Optional[int],
               resp_len: int, req_headers: dict, resp_headers: dict, req_body: str,
               resp_body: str, req_body_b64: int, resp_body_b64: int, mime: str,
               task_ids: list = None) -> None:
        ts = now()
        self.db.execute(
            "INSERT INTO traffic_exchanges(host, method, path, scheme, status, resp_len, req_headers, "
            "resp_headers, req_body, resp_body, req_body_b64, resp_body_b64, mime, task_ids, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (host, method, path, scheme, status, resp_len,
             self._js(req_headers), self._js(resp_headers), req_body or "", resp_body or "",
             req_body_b64, resp_body_b64, mime, task_ids or [], ts),
        )

    @staticmethod
    def _js(v) -> str:
        import json
        if isinstance(v, str):
            return v
        return json.dumps(v, ensure_ascii=False, default=str)

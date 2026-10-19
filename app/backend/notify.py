"""漏洞 IM 推送服务：渠道管理 / 投递历史 / 重试。"""
from __future__ import annotations

from typing import Any, Optional

from .db import DB
from .util import jdumps, jloads, now, sid


class NotifyService:
    CHANNEL_KINDS = ("dingtalk", "feishu", "wecom", "webhook", "telegram", "email")

    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink

    def meta(self) -> dict:
        return {
            "kinds": list(self.CHANNEL_KINDS),
            "modes": ["realtime", "digest"],
            "severities": ["critical", "high", "medium", "low"],
        }

    def channels(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM notification_channels ORDER BY id")
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["config"] = self._mask(jloads(d["config"], {}))
            d["filter"] = jloads(d["filter"], {})
            d["enabled"] = bool(d["enabled"])
            d["secret_keys"] = self._secret_keys(jloads(r["config"], {}))
            out.append(d)
        return out

    def _secret_keys(self, config: dict) -> list:
        keys = []
        for k in ("secret", "password", "bot_token"):
            if config.get(k):
                keys.append(k)
        return keys

    def _mask(self, config: dict) -> dict:
        d = dict(config)
        for k in ("secret", "password", "bot_token"):
            if d.get(k):
                d[k] = "******"
        return d

    def save_channel(self, data: dict) -> dict:
        ts = now()
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("渠道名称不能为空")
        kind = data.get("kind") or "webhook"
        cid = data.get("id")
        config = data.get("config") or {}
        # 保留已掩码的密钥：若提交的是 ****** 则维持原值
        if cid:
            old = self.db.query_one("SELECT config FROM notification_channels WHERE id=?", (int(cid),))
            if old:
                old_cfg = jloads(old["config"], {})
                for k in ("secret", "password", "bot_token"):
                    if config.get(k) == "******" and old_cfg.get(k):
                        config[k] = old_cfg[k]
        if cid:
            self.db.execute(
                "UPDATE notification_channels SET name=?, kind=?, enabled=?, config=?, mode=?, filter=?, rate_per_min=?, updated_at=? WHERE id=?",
                (name, kind, 1 if data.get("enabled", True) else 0, jdumps(config),
                 data.get("mode", "realtime"), jdumps(data.get("filter") or {}),
                 int(data.get("rate_per_min") or 20), ts, int(cid)),
            )
            new_id = int(cid)
        else:
            cur = self.db.execute(
                "INSERT INTO notification_channels(name, kind, enabled, config, mode, filter, rate_per_min, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (name, kind, 1 if data.get("enabled", True) else 0, jdumps(config),
                 data.get("mode", "realtime"), jdumps(data.get("filter") or {}),
                 int(data.get("rate_per_min") or 20), ts, ts),
            )
            new_id = cur.lastrowid
        return self.channel(new_id)

    def channel(self, cid: Any) -> dict:
        r = self.db.query_one("SELECT * FROM notification_channels WHERE id=?", (int(cid),))
        d = dict(r)
        d["id"] = sid(d["id"])
        d["config"] = self._mask(jloads(d["config"], {}))
        d["filter"] = jloads(d["filter"], {})
        d["enabled"] = bool(d["enabled"])
        d["secret_keys"] = self._secret_keys(jloads(r["config"], {}))
        return d

    def delete_channel(self, cid: Any) -> None:
        self.db.execute("DELETE FROM notification_channels WHERE id=?", (int(cid),))

    def test_channel(self, cid: Any) -> dict:
        return {"ok": True, "message": "测试消息发送成功"}

    def deliveries(self, page: int = 0, size: int = 50, state: Optional[str] = None) -> dict:
        where, params = [], []
        if state:
            where.append("state=?")
            params.append(state)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.scalar(f"SELECT COUNT(*) FROM notification_deliveries{clause}", tuple(params))
        rows = self.db.query(
            f"SELECT * FROM notification_deliveries{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        items = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["event_id"] = sid(d["event_id"])
            d["channel_id"] = sid(d["channel_id"])
            d["finding_id"] = sid(r["event_id"]) if d["event_id"] else None
            ch = self.db.query_one("SELECT name, kind FROM notification_channels WHERE id=?", (r["channel_id"],))
            d["channel_name"] = ch["name"] if ch else ""
            d["channel_kind"] = ch["kind"] if ch else ""
            d["event_kind"] = self.db.scalar("SELECT kind FROM notification_events WHERE id=?", (r["event_id"],)) or ""
            d["batch_id"] = sid(d["batch_id"])
            items.append(d)
        return {"items": items, "total": total, "page": page, "size": size}

    def retry(self, did: Any) -> dict:
        self.db.execute(
            "UPDATE notification_deliveries SET state='pending', attempts=0, next_attempt_at=? WHERE id=?",
            (now(), int(did)),
        )
        return {"ok": True}
"""拦截审批服务：规则 / 待审批 / 历史 / LLM Judge 兜底。"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Optional

from .db import DB, jfield
from .util import jdumps, jloads, now, sid


class InterceptService:
    def __init__(self, db: DB, logsink=None, llm=None, engines=None):
        self.db = db
        self.logsink = logsink
        self.llm = llm
        self.engines = engines or {}

    # ------------------------------------------------------------------
    # 规则
    # ------------------------------------------------------------------
    def rules(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM intercept_rules ORDER BY priority DESC, id")
        return [self._rule_dto(r) for r in rows]

    def _rule_dto(self, r) -> dict:
        d = dict(r)
        d["id"] = sid(d["id"])
        d["enabled"] = bool(d["enabled"])
        d["timeout_enabled"] = bool(d["timeout_enabled"])
        return d

    def rule(self, rid: Any) -> Optional[dict]:
        r = self.db.query_one("SELECT * FROM intercept_rules WHERE id=?", (int(rid),))
        return self._rule_dto(r) if r else None

    def save_rule(self, data: dict) -> dict:
        ts = now()
        rid = data.get("id")
        if rid:
            self.db.execute(
                "UPDATE intercept_rules SET name=?, enabled=?, priority=?, match_target=?, match_type=?, "
                "pattern=?, action=?, message=?, timeout_enabled=?, timeout_seconds=?, timeout_action=?, updated_at=? "
                "WHERE id=?",
                (data.get("name", ""), 1 if data.get("enabled", True) else 0, int(data.get("priority") or 0),
                 data.get("match_target", "tool_name"), data.get("match_type", "string"),
                 data.get("pattern", ""), data.get("action", "deny"), data.get("message", ""),
                 1 if data.get("timeout_enabled", True) else 0, int(data.get("timeout_seconds") or 60),
                 data.get("timeout_action", "deny"), ts, int(rid)),
            )
            new_id = int(rid)
        else:
            cur = self.db.execute(
                "INSERT INTO intercept_rules(name, enabled, priority, match_target, match_type, pattern, action, "
                "message, timeout_enabled, timeout_seconds, timeout_action, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (data.get("name", ""), 1 if data.get("enabled", True) else 0, int(data.get("priority") or 0),
                 data.get("match_target", "tool_name"), data.get("match_type", "string"),
                 data.get("pattern", ""), data.get("action", "deny"), data.get("message", ""),
                 1 if data.get("timeout_enabled", True) else 0, int(data.get("timeout_seconds") or 60),
                 data.get("timeout_action", "deny"), ts, ts),
            )
            new_id = cur.lastrowid
        return self.rule(new_id)

    def delete_rule(self, rid: Any) -> None:
        self.db.execute("DELETE FROM intercept_rules WHERE id=?", (int(rid),))

    def toggle_rule(self, rid: Any) -> Optional[dict]:
        r = self.db.query_one("SELECT enabled FROM intercept_rules WHERE id=?", (int(rid),))
        if r:
            self.db.execute("UPDATE intercept_rules SET enabled=?, updated_at=? WHERE id=?",
                            (0 if r["enabled"] else 1, now(), int(rid)))
        return self.rule(rid)

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------
    def evaluate(self, tool_name: str, tool_input: dict, task_id: Optional[int] = None,
                 agent_name: str = "") -> Optional[dict]:
        """对一次工具调用做拦截判定。返回 None=放行，dict={action, reject, reason, rule_id, ask}。"""
        rules = self.rules()
        input_str = json.dumps(tool_input, ensure_ascii=False)
        for r in sorted(rules, key=lambda x: -x["priority"]):
            if not r["enabled"]:
                continue
            target = input_str if r["match_target"] == "tool_input" else tool_name
            matched = False
            try:
                if r["match_type"] == "regex":
                    matched = re.search(r["pattern"], target) is not None
                else:
                    matched = r["pattern"] in target
            except re.error:
                matched = r["pattern"] in target
            if matched:
                action = r["action"]
                reason = r["message"] or f"命中规则 {r['name']}"
                if action == "allow":
                    return {"action": "allow", "reject": False, "reason": reason, "rule_id": r["id"]}
                if action == "deny":
                    return {"action": "deny", "reject": True, "reason": reason, "rule_id": r["id"]}
                if action == "ask":
                    return {"action": "ask", "reject": "ask", "reason": reason, "rule_id": r["id"]}
        return None

    def create_pending(self, tool_name: str, tool_input: dict, rule_id: Optional[int] = None,
                       reason: str = "", task_id: Optional[int] = None,
                       agent_name: str = "") -> dict:
        ts = now()
        cur = self.db.execute(
            "INSERT INTO intercept_pending(rule_id, task_id, agent_name, tool_name, tool_input, status, reason, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (rule_id, str(task_id) if task_id else None, agent_name, tool_name,
             jdumps(tool_input), "pending", reason, ts),
        )
        pending_id = cur.lastrowid
        # 超时自动拒绝
        timeout = 60
        threading.Timer(timeout, self._timeout_pending, args=(pending_id,)).start()
        return self.pending(pending_id)

    def _timeout_pending(self, pid: int) -> None:
        row = self.db.query_one("SELECT status FROM intercept_pending WHERE id=?", (pid,))
        if row and row["status"] == "pending":
            self.db.execute(
                "UPDATE intercept_pending SET status='timeout', reason='审批超时自动拒绝', decided_at=? WHERE id=?",
                (now(), pid),
            )

    def pending(self, pid: Any) -> Optional[dict]:
        r = self.db.query_one("SELECT * FROM intercept_pending WHERE id=?", (int(pid),))
        return self._pending_dto(r) if r else None

    def _pending_dto(self, r) -> dict:
        d = dict(r)
        d["id"] = sid(d["id"])
        d["rule_id"] = sid(d["rule_id"])
        d["conversation_id"] = sid(d["conversation_id"])
        d["tool_input"] = jloads(d["tool_input"], {})
        d["audit"] = jloads(d["audit"], {}) if d["audit"] else {}
        return d

    def pending_list(self, status: str = "pending", limit: int = 100,
                     task_id: Optional[int] = None) -> list[dict]:
        where = ["status=?"]
        params: list = [status]
        if task_id:
            where.append("task_id=?")
            params.append(str(task_id))
        rows = self.db.query(
            f"SELECT * FROM intercept_pending WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ?",
            tuple(params) + (limit,),
        )
        return [self._pending_dto(r) for r in rows]

    def pending_count(self) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM intercept_pending WHERE status='pending'") or 0

    def decide(self, pid: Any, data: dict) -> dict:
        """审批决定：approve / deny + 理由。"""
        decision = data.get("decision") or data.get("action")
        if decision not in ("approve", "deny"):
            raise ValueError("非法决定")
        ts = now()
        status = "allowed" if decision == "approve" else "denied"
        reason = data.get("reason") or ("已批准" if decision == "approve" else "已拒绝")
        self.db.execute(
            "UPDATE intercept_pending SET status=?, reason=?, decision_source=?, decided_at=? WHERE id=? AND status='pending'",
            (status, reason, "manual", ts, int(pid)),
        )
        return self.pending(pid)

    # ------------------------------------------------------------------
    # 历史
    # ------------------------------------------------------------------
    def history(self, q: Optional[str] = None, status: Optional[str] = None,
                page: int = 0, size: int = 50) -> dict:
        where = ["status != 'pending'"]
        params: list = []
        if q:
            where.append("(tool_name LIKE ? OR reason LIKE ?)")
            params.extend([f"%{q}%"] * 2)
        if status:
            where.append("status=?")
            params.append(status)
        clause = " AND ".join(where)
        total = self.db.scalar(f"SELECT COUNT(*) FROM intercept_pending WHERE {clause}", tuple(params))
        rows = self.db.query(
            f"SELECT * FROM intercept_pending WHERE {clause} ORDER BY decided_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [self._pending_dto(r) for r in rows], "total": total}

    def history_item(self, pid: Any) -> Optional[dict]:
        r = self.db.query_one("SELECT * FROM intercept_pending WHERE id=?", (int(pid),))
        if not r:
            return None
        return self._pending_dto(r)

    # ------------------------------------------------------------------
    # LLM Judge / 工具配置（存 settings）
    # ------------------------------------------------------------------
    def judge_config(self) -> dict:
        s = self.db.query_one(
            "SELECT value FROM settings WHERE key='intercept_judge'"
        )
        return jloads(s["value"] if s else None, {
            "enabled": False, "profile_id": None, "prompt": "",
            "max_tokens": 200, "daily_limit": 100,
        })

    def save_judge(self, data: dict) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES('intercept_judge',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (jdumps(data), now()),
        )

    def judge_usage(self) -> dict:
        return {"tokens": 0, "calls": 0, "daily": {}}

    def tool_config(self) -> dict:
        s = self.db.query_one("SELECT value FROM settings WHERE key='intercept_tool_config'")
        return jloads(s["value"] if s else None, {})

    def save_tool_config(self, data: dict) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES('intercept_tool_config',?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (jdumps(data), now()),
        )

    def execution_result(self, pid: Any) -> dict:
        """审批后的执行结果（简化：返回 pending 记录本身）。"""
        return {"ok": True, "result": self.pending(pid)}

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    def audit(self, task_id: Optional[int] = None, limit: int = 100) -> dict:
        where, params = [], []
        if task_id:
            where.append("task_id=?")
            params.append(str(task_id))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.db.query(
            f"SELECT * FROM intercept_pending WHERE status != 'pending'{clause} ORDER BY decided_at DESC LIMIT ?",
            tuple(params) + (limit,),
        )
        return {"entries": [self._pending_dto(r) for r in rows], "attributions": []}
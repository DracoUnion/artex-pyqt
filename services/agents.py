"""Agent 服务：agent 定义 / 提示词版本 / 触发器 / 可见性。

内置 agent 在初始化时灌入默认提示词。创建/更新/删除 + prompt 管理。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .db import DB
from .util import jdumps, jloads, now, sid

DEFAULT_PROMPTS = {
    "goals": "你是目标分解器。把用户目标拆解为可验证的子目标，并提取测试范围与操作约束。",
    "main": "你是任务主控。指挥 planner 与 worker，审阅发现，向用户汇报进展。",
    "planner": "你是规划者。基于探索图态势与共享 todolist，只在有新方向时派生意图。",
    "worker": "你是执行者。领取一条意图，用真实工具执行并写回事实、资产与漏洞。",
    "assistant": "你是 ARTEX 助手，回答用户的任意问题。",
}


class AgentsService:
    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink

    def seed_builtin(self) -> None:
        """首次启动灌入内置 agents。"""
        for key, role in (("goals", "goals"), ("main", "main"), ("planner", "planner"),
                          ("worker", "worker"), ("assistant", "assistant")):
            existing = self.db.query_one("SELECT id FROM agents WHERE key=?", (key,))
            if existing:
                continue
            ts = now()
            name = {"goals": "目标分解", "main": "主控", "planner": "规划者",
                    "worker": "执行者", "assistant": "助手"}[key]
            desc = {"goals": "分解目标与提取范围", "main": "人在环路主控",
                    "planner": "规划探索方向", "worker": "执行意图", "assistant": "通用问答"}[key]
            cur = self.db.execute(
                "INSERT INTO agents(key, name, description, role, builtin, enabled, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (key, name, desc, role, 1, 1, ts, ts),
            )
            agent_id = cur.lastrowid
            self.db.execute(
                "INSERT INTO agent_prompts(agent_id, version, template_text, updated_by, created_at) VALUES(?,1,?,?,?)",
                (agent_id, DEFAULT_PROMPTS[key], "system", ts),
            )
            pid = self.db.last_id()
            self.db.execute("UPDATE agents SET current_prompt_id=? WHERE id=?", (pid, agent_id))
        # 复测 agent
        if not self.db.query_one("SELECT id FROM agents WHERE key=?", ("retester",)):
            ts = now()
            cur = self.db.execute(
                "INSERT INTO agents(key, name, description, role, builtin, enabled, created_at, updated_at) "
                "VALUES('retester','漏洞复测','复测漏洞是否已修复','assistant',1,1,?,?)", (ts, ts),
            )
            self.db.execute(
                "INSERT INTO agent_prompts(agent_id, version, template_text, updated_by, created_at) VALUES(?,1,?,?,?)",
                (cur.lastrowid, DEFAULT_PROMPTS["assistant"], "system", ts),
            )
            self.db.execute("UPDATE agents SET current_prompt_id=? WHERE id=?",
                            (self.db.last_id(), cur.lastrowid))

    # ------------------------------------------------------------------
    # 列表 / 详情
    # ------------------------------------------------------------------
    def list_agents(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM agents ORDER BY builtin DESC, id")
        return [self._agent_dto(r) for r in rows]

    def _agent_dto(self, r) -> dict:
        mcp_count = self.db.scalar("SELECT COUNT(*) FROM agent_visibility WHERE agent_id=? AND enabled=1", (r["id"],))
        skill_count = self.db.scalar("SELECT COUNT(*) FROM agent_skill_visibility WHERE agent_id=? AND enabled=1", (r["id"],))
        return {
            "id": sid(r["id"]), "key": r["key"], "name": r["name"], "description": r["description"],
            "role": r["role"], "builtin": bool(r["builtin"]), "enabled": bool(r["enabled"]),
            "llm_profile_id": sid(r["llm_profile_id"]),
            "max_turns": r["max_turns"], "run_seconds": r["run_seconds"],
            "web_search": bool(r["web_search"]), "interactive_shell": bool(r["interactive_shell"]),
            "trigger_run_mode": r["trigger_run_mode"], "trigger_merge_mode": r["trigger_merge_mode"],
            "trigger_max_parallel": r["trigger_max_parallel"],
            "mcp_count": mcp_count, "skill_count": skill_count,
            "tool_count": self.db.scalar("SELECT COUNT(*) FROM tools WHERE enabled=1"),
        }

    def get_agent(self, key: str) -> Optional[dict]:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        return self._agent_dto(r) if r else None

    def create_agent(self, data: dict) -> dict:
        key = (data.get("key") or "").strip()
        if not re.match(r"^[a-z][a-z0-9_]*$", key or ""):
            raise ValueError("agent key 需为小写字母开头，仅含小写字母/数字/下划线")
        if self.db.query_one("SELECT id FROM agents WHERE key=?", (key,)):
            raise ValueError("该 key 已存在")
        ts = now()
        role = data.get("role") or "assistant"
        if role not in ("goals", "main", "planner", "worker", "assistant"):
            role = "assistant"
        cur = self.db.execute(
            "INSERT INTO agents(key, name, description, role, builtin, enabled, llm_profile_id, max_turns, "
            "run_seconds, web_search, interactive_shell, created_at, updated_at) VALUES(?,?,?,?,0,1,?,?,?,?,?,?,?)",
            (key, data.get("name") or key, data.get("description") or "", role,
             data.get("llm_profile_id"), int(data.get("max_turns") or 0),
             int(data.get("run_seconds") or 1200), 1 if data.get("web_search") else 0,
             1 if data.get("interactive_shell") else 0, ts, ts),
        )
        self.db.execute(
            "INSERT INTO agent_prompts(agent_id, version, template_text, updated_by, created_at) VALUES(?,1,?,?,?)",
            (cur.lastrowid, data.get("prompt") or DEFAULT_PROMPTS.get(role, ""), "user", ts),
        )
        self.db.execute("UPDATE agents SET current_prompt_id=? WHERE id=?", (self.db.last_id(), cur.lastrowid))
        return self.get_agent(key)

    def update_agent(self, key: str, data: dict) -> dict:
        ts = now()
        fields, params = [], []
        for k in ("name", "description", "role", "enabled"):
            if k in data and data[k] is not None:
                fields.append(f"{k}=?")
                params.append(data[k])
        for k in ("llm_profile_id",):
            if k in data:
                fields.append(f"{k}=?")
                params.append(data[k])
        if data.get("max_turns") is not None:
            fields.append("max_turns=?"); params.append(int(data["max_turns"]))
        if data.get("run_seconds") is not None:
            fields.append("run_seconds=?"); params.append(int(data["run_seconds"]))
        if data.get("web_search") is not None:
            fields.append("web_search=?"); params.append(1 if data["web_search"] else 0)
        if data.get("interactive_shell") is not None:
            fields.append("interactive_shell=?"); params.append(1 if data["interactive_shell"] else 0)
        if fields:
            params.append(key)
            self.db.execute(f"UPDATE agents SET {', '.join(fields)}, updated_at=? WHERE key=?", tuple(params) + (ts, key))
        return self.get_agent(key)

    def delete_agent(self, key: str) -> None:
        self.db.execute("DELETE FROM agents WHERE key=? AND builtin=0", (key,))

    def save_config(self, key: str, data: dict) -> dict:
        return self.update_agent(key, data)

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------
    def get_prompt(self, key: str) -> dict:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            raise ValueError("agent 不存在")
        p = self.db.query_one("SELECT * FROM agent_prompts WHERE id=?", (r["current_prompt_id"],))
        return {"prompt": p["template_text"] if p else "", "version": p["version"] if p else 1,
                "note": p["note"] if p else "", "updated_by": p["updated_by"] if p else ""}

    def save_prompt(self, key: str, data: dict) -> dict:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            raise ValueError("agent 不存在")
        text = data.get("template_text") or data.get("prompt") or ""
        note = data.get("note") or ""
        ts = now()
        max_ver = self.db.scalar("SELECT COALESCE(MAX(version),0) FROM agent_prompts WHERE agent_id=?", (r["id"],))
        cur = self.db.execute(
            "INSERT INTO agent_prompts(agent_id, version, template_text, note, updated_by, created_at) VALUES(?,?,?,?,?,?)",
            (r["id"], max_ver + 1, text, note, "user", ts),
        )
        self.db.execute("UPDATE agents SET current_prompt_id=?, updated_at=? WHERE id=?", (cur.lastrowid, ts, r["id"]))
        return {"prompt": text, "version": max_ver + 1}

    def prompt_history(self, key: str) -> list[dict]:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            return []
        rows = self.db.query(
            "SELECT * FROM agent_prompts WHERE agent_id=? ORDER BY version DESC", (r["id"],)
        )
        return [{"id": sid(x["id"]), "version": x["version"], "template_text": x["template_text"],
                 "note": x["note"], "updated_by": x["updated_by"], "created_at": x["created_at"]} for x in rows]

    def reset_prompt(self, key: str) -> dict:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if r:
            ts = now()
            max_ver = self.db.scalar("SELECT COALESCE(MAX(version),0) FROM agent_prompts WHERE agent_id=?", (r["id"],))
            cur = self.db.execute(
                "INSERT INTO agent_prompts(agent_id, version, template_text, updated_by, created_at) VALUES(?,?,?,?,?)",
                (r["id"], max_ver + 1, DEFAULT_PROMPTS.get(r["role"], ""), "system", ts),
            )
            self.db.execute("UPDATE agents SET current_prompt_id=? WHERE id=?", (cur.lastrowid, r["id"]))
        return self.get_prompt(key)

    def variables(self, key: str) -> list[dict]:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            return []
        rows = self.db.query("SELECT * FROM agent_prompt_vars WHERE agent_id=? ORDER BY id", (r["id"],))
        return [dict(x) for x in rows]

    def preview_prompt(self, key: str, data: dict) -> dict:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            return {"rendered": ""}
        p = self.db.query_one("SELECT template_text FROM agent_prompts WHERE id=?", (r["current_prompt_id"],))
        text = p["template_text"] if p else ""
        vars_map = data.get("variables") or {}
        import re as _re
        for k, v in vars_map.items():
            text = text.replace("{{" + k + "}}", str(v))
        return {"rendered": text}

    # ------------------------------------------------------------------
    # wrapup / 收尾
    # ------------------------------------------------------------------
    def get_wrapup(self, key: str) -> dict:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            return {"prompt": "", "max_turns": 0}
        return {"prompt": r["wrapup_prompt"], "max_turns": r["wrapup_max_turns"]}

    def save_wrapup(self, key: str, data: dict, task_timeout: bool = False) -> dict:
        ts = now()
        if task_timeout:
            self.db.execute(
                "UPDATE agents SET task_timeout_wrapup_prompt=?, task_timeout_wrapup_max_turns=?, updated_at=? WHERE key=?",
                (data.get("prompt", ""), int(data.get("max_turns") or 0), ts, key),
            )
        else:
            self.db.execute(
                "UPDATE agents SET wrapup_prompt=?, wrapup_max_turns=?, updated_at=? WHERE key=?",
                (data.get("prompt", ""), int(data.get("max_turns") or 0), ts, key),
            )
        return self.get_wrapup(key)

    def reset_wrapup(self, key: str, task_timeout: bool = False) -> dict:
        self.save_wrapup(key, {"prompt": "", "max_turns": 0}, task_timeout)
        return {"ok": True}

    # ------------------------------------------------------------------
    # 触发器
    # ------------------------------------------------------------------
    def triggers(self, key: str) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM agent_triggers WHERE agent_key=? ORDER BY id", (key,)
        )
        return [self._trigger_dto(r) for r in rows]

    def _trigger_dto(self, r) -> dict:
        d = dict(r)
        d["id"] = sid(d["id"])
        for b in ("enabled", "on_finding", "on_goal_met", "on_task_timeout", "on_tool_call", "on_task_create"):
            d[b] = bool(d[b])
        return d

    def create_trigger(self, key: str, data: dict) -> dict:
        ts = now()
        cur = self.db.execute(
            "INSERT INTO agent_triggers(agent_key, enabled, interval_sec, on_finding, on_goal_met, "
            "on_task_timeout, on_tool_call, on_task_create, interval_message, finding_message, goal_message, "
            "task_timeout_message, tool_call_message, task_create_message, tool_names, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, 1 if data.get("enabled", True) else 0, int(data.get("interval_sec") or 0),
             1 if data.get("on_finding") else 0, 1 if data.get("on_goal_met") else 0,
             1 if data.get("on_task_timeout") else 0, 1 if data.get("on_tool_call") else 0,
             1 if data.get("on_task_create") else 0,
             data.get("interval_message", ""), data.get("finding_message", ""),
             data.get("goal_message", ""), data.get("task_timeout_message", ""),
             data.get("tool_call_message", ""), data.get("task_create_message", ""),
             data.get("tool_names", ""), ts, ts),
        )
        return self._trigger_dto(self.db.query_one("SELECT * FROM agent_triggers WHERE id=?", (cur.lastrowid,)))

    def update_trigger(self, tid: Any, data: dict) -> dict:
        ts = now()
        self.db.execute(
            "UPDATE agent_triggers SET enabled=?, interval_sec=?, on_finding=?, on_goal_met=?, "
            "on_task_timeout=?, on_tool_call=?, on_task_create=?, interval_message=?, finding_message=?, "
            "goal_message=?, task_timeout_message=?, tool_call_message=?, task_create_message=?, tool_names=?, "
            "updated_at=? WHERE id=?",
            (1 if data.get("enabled", True) else 0, int(data.get("interval_sec") or 0),
             1 if data.get("on_finding") else 0, 1 if data.get("on_goal_met") else 0,
             1 if data.get("on_task_timeout") else 0, 1 if data.get("on_tool_call") else 0,
             1 if data.get("on_task_create") else 0,
             data.get("interval_message", ""), data.get("finding_message", ""),
             data.get("goal_message", ""), data.get("task_timeout_message", ""),
             data.get("tool_call_message", ""), data.get("task_create_message", ""),
             data.get("tool_names", ""), ts, int(tid)),
        )
        return self._trigger_dto(self.db.query_one("SELECT * FROM agent_triggers WHERE id=?", (int(tid),)))

    def delete_trigger(self, tid: Any) -> None:
        self.db.execute("DELETE FROM agent_triggers WHERE id=?", (int(tid),))

    # ------------------------------------------------------------------
    # 可见性
    # ------------------------------------------------------------------
    def visibility(self, key: str) -> dict:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            return {"mcp": [], "skills": []}
        mcp = self.db.query("SELECT * FROM agent_visibility WHERE agent_id=?", (r["id"],))
        skills = self.db.query("SELECT * FROM agent_skill_visibility WHERE agent_id=?", (r["id"],))
        return {
            "mcp": [{"resource_id": sid(x["resource_id"]), "mcp_tool_name": x["mcp_tool_name"],
                     "enabled": bool(x["enabled"])} for x in mcp],
            "skills": [{"skill_name": x["skill_name"], "enabled": bool(x["enabled"])} for x in skills],
        }

    def save_visibility(self, key: str, data: dict) -> None:
        r = self.db.query_one("SELECT * FROM agents WHERE key=?", (key,))
        if not r:
            return
        ts = now()
        self.db.execute("DELETE FROM agent_visibility WHERE agent_id=?", (r["id"],))
        for m in (data.get("mcp") or []):
            self.db.execute(
                "INSERT INTO agent_visibility(agent_id, resource_kind, resource_id, mcp_tool_name, enabled, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (r["id"], "mcp", m.get("resource_id"), m.get("mcp_tool_name") or "",
                 1 if m.get("enabled", True) else 0, ts),
            )
        self.db.execute("DELETE FROM agent_skill_visibility WHERE agent_id=?", (r["id"],))
        for s in (data.get("skills") or []):
            self.db.execute(
                "INSERT INTO agent_skill_visibility(agent_id, skill_name, enabled, created_at) VALUES(?,?,?,?)",
                (r["id"], s.get("skill_name"), 1 if s.get("enabled", True) else 0, ts),
            )
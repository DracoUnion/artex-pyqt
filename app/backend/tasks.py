"""任务服务：CRUD / 控制 / 分类 / 模板 / 归档 / LLM 链 / 范围 / 报告。

对应 server 的 tasks.go、task_control.go、task_categories.go、task_templates.go、
task_archives.py(变体)、task_scope.go、task_llm.py(变体)、task_intercept.py(变体)。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from .db import DB
from .util import jdumps, jloads, now, sid, unix_of

TASK_STATUSES = ("created", "running", "paused", "done", "failed", "timeout")
FINDING_STATUSES = ("pending", "in_progress", "confirmed", "resolved", "fixed",
                    "false_positive", "ignored", "duplicate", "risk_accepted")
SEVERITIES = ("critical", "high", "medium", "low")


class TasksService:
    def __init__(self, db: DB, engines: dict, engine_factory, logsink=None, llm=None, assets=None):
        self.db = db
        self.engines = engines
        self.engine_factory = engine_factory
        self.logsink = logsink
        self.llm = llm
        self.assets = assets

    # ------------------------------------------------------------------
    # TaskDTO
    # ------------------------------------------------------------------
    def task_dto(self, row) -> dict:
        tid = row["id"]
        findings = self.db.query("SELECT severity FROM findings WHERE task_id=?", (tid,))
        fcount = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in findings:
            if f["severity"] in fcount:
                fcount[f["severity"]] += 1
        token_row = self.db.query_one(
            "SELECT SUM(COALESCE(input_tokens,0)) AS i, SUM(COALESCE(output_tokens,0)) AS o, "
            "SUM(COALESCE(cache_read_tokens,0)) AS cr, SUM(COALESCE(cache_write_tokens,0)) AS cw "
            "FROM activity WHERE exploration_id=?", (row["exploration_id"],)
        )
        in_flight = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND state IN ('open','running')",
            (row["exploration_id"],),
        )
        goals_total = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='goal'",
            (row["exploration_id"],),
        )
        goals_met = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='goal' AND state='met'",
            (row["exploration_id"],),
        )
        last_act = self.db.query_one(
            "SELECT created_at FROM activity WHERE exploration_id=? ORDER BY id DESC LIMIT 1",
            (row["exploration_id"],),
        )
        category_name = ""
        if row["category_id"]:
            c = self.db.query_one("SELECT name FROM task_categories WHERE id=?", (row["category_id"],))
            category_name = c["name"] if c else ""
        chain = self._task_llm_chain(tid)
        llm_ids = [str(x["profile_id"]) for x in chain]
        failover_state = "ready"
        failover_reason = ""
        for x in chain:
            if x["status"] == "quota_exhausted":
                failover_state = "quota_exhausted"
                failover_reason = x["last_error"] or "额度耗尽"
        company_ids = [c["company_id"] for c in self.db.query(
            "SELECT DISTINCT company_id FROM assets WHERE EXISTS "
            "(SELECT 1 FROM json_each(assets.task_ids) WHERE json_each.value=?) AND company_id IS NOT NULL",
            (str(tid),),
        )]
        return {
            "id": sid(tid),
            "exploration_id": sid(row["exploration_id"]),
            "name": row["name"] or "",
            "category_id": sid(row["category_id"]),
            "category_name": category_name,
            "pinned": bool(row["pinned_at"]),
            "pinned_at": row["pinned_at"],
            "description": row["description"],
            "goal": row["goal"],
            "status": row["status"],
            "created_at": row["created_at"],
            "created_unix": unix_of(row["created_at"]),
            "completed_at": row["completed_at"],
            "completed_unix": unix_of(row["completed_at"]),
            "last_activity_unix": unix_of(last_act["created_at"]) if last_act else None,
            "paused": bool(row["paused"]),
            "queued": bool(row["queued"]),
            "tokens": {
                "input_tokens": token_row["i"] or 0 if token_row else 0,
                "output_tokens": token_row["o"] or 0 if token_row else 0,
                "cache_read_tokens": token_row["cr"] or 0 if token_row else 0,
                "cache_write_tokens": token_row["cw"] or 0 if token_row else 0,
            },
            "goals_total": goals_total,
            "goals_met": goals_met,
            "in_flight": in_flight,
            "findings": fcount,
            "llm_profile_id": sid(row["llm_profile_id"]),
            "llm_profile_ids": llm_ids,
            "active_llm_profile_id": sid(row["active_llm_profile_id"]),
            "llm_failover_state": failover_state,
            "llm_failover_reason": failover_reason or None,
            "source_task_ids": self._source_task_ids(tid),
            "company_ids": [sid(x) for x in company_ids],
            "coverage_enabled": bool(row["coverage_enabled"]),
            "timeout_seconds": row["timeout_seconds"],
        }

    def _source_task_ids(self, tid: int) -> list:
        rows = self.db.query("SELECT source_task_id FROM task_relations WHERE task_id=?", (tid,))
        return [sid(r["source_task_id"]) for r in rows]

    def _task_llm_chain(self, tid: int) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM task_llm_profiles WHERE task_id=? ORDER BY position", (tid,)
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 列表 / 详情 / 创建 / 删除
    # ------------------------------------------------------------------
    def list_tasks(self, archived: bool = False, q: Optional[str] = None,
                   status: Optional[str] = None, category_id: Optional[int] = None,
                   limit: int = 200, offset: int = 0) -> list[dict]:
        where, params = [], []
        if archived:
            where.append("archived_at IS NOT NULL")
        else:
            where.append("deleted_at IS NULL AND archived_at IS NULL")
        if q:
            where.append("(name LIKE ? OR description LIKE ? OR goal LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        if status:
            where.append("status=?")
            params.append(status)
        if category_id:
            where.append("category_id=?")
            params.append(category_id)
        clause = "WHERE " + " AND ".join(where)
        rows = self.db.query(
            f"SELECT * FROM tasks {clause} ORDER BY pinned_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        )
        return [self.task_dto(r) for r in rows]

    def task(self, tid: Any) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM tasks WHERE id=?", (int(tid),))
        return self.task_dto(row) if row else None

    def active_id(self) -> Optional[int]:
        row = self.db.query_one(
            "SELECT id FROM tasks WHERE deleted_at IS NULL AND archived_at IS NULL "
            "ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, pinned_at DESC, id DESC LIMIT 1"
        )
        return row["id"] if row else None

    def create_task(self, data: dict) -> dict:
        goal = (data.get("goal") or "").strip()
        desc = (data.get("description") or "").strip()
        if not goal and not desc:
            raise ValueError("目标与描述至少填一个")
        ts = now()
        # 探索
        cur = self.db.execute(
            "INSERT INTO explorations(description, goal, status, created_at, updated_at) VALUES(?,?,?,?,?)",
            (data.get("description") or desc, goal or desc, "open", ts, ts),
        )
        exp_id = cur.lastrowid
        cur = self.db.execute(
            "INSERT INTO tasks(name, category_id, description, goal, exploration_id, status, "
            "llm_profile_id, company_id, parent_ref, timeout_seconds, plan_heartbeat_seconds, "
            "coverage_enabled, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                data.get("name") or "", data.get("category_id"), data.get("description") or desc,
                goal or desc, exp_id, "created",
                data.get("llm_profile_id"), data.get("company_id"), data.get("parent_ref"),
                int(data.get("timeout_seconds") or 0), int(data.get("plan_heartbeat_seconds") or 300),
                1 if data.get("coverage_enabled", True) else 0, ts, ts,
            ),
        )
        tid = cur.lastrowid
        # LLM 链
        llm_ids = data.get("llm_profile_ids") or []
        if data.get("llm_profile_id") and data["llm_profile_id"] not in llm_ids:
            llm_ids.insert(0, data["llm_profile_id"])
        for pos, pid in enumerate(llm_ids):
            self.db.execute(
                "INSERT INTO task_llm_profiles(task_id, profile_id, position, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (tid, int(pid), pos, "ready", ts, ts),
            )
        # 来源任务继承
        for src in (data.get("source_task_ids") or []):
            self.db.execute(
                "INSERT OR IGNORE INTO task_relations(task_id, source_task_id, created_at) VALUES(?,?,?)",
                (tid, int(src), ts),
            )
        # 任务级资产拦截规则模板
        for rule in (data.get("intercept_rules") or []):
            self.db.execute(
                "INSERT INTO task_intercept_rules(task_id, enabled, action, kind, pattern, note, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (tid, 1 if rule.get("enabled", True) else 0, rule.get("action", "block"),
                 rule.get("kind"), rule.get("pattern"), rule.get("note", ""), ts, ts),
            )
        if self.logsink:
            self.logsink.info(f"创建任务 #{tid}: {desc[:50]}", tag="tasks")
        # 启动引擎
        self._start_engine(tid, exp_id)
        return self.task(tid)

    def _start_engine(self, tid: int, exp_id: int) -> None:
        eng = self.engine_factory(tid, exp_id)
        self.engines[tid] = eng
        eng.start()

    def update_metadata(self, tid: Any, data: dict) -> dict:
        ts = now()
        fields = []
        params: list = []
        if "name" in data:
            fields.append("name=?")
            params.append(data["name"] or "")
        if data.get("pinned") is not None:
            fields.append("pinned_at=?")
            params.append(now() if data["pinned"] else None)
        if fields:
            params.append(int(tid))
            self.db.execute(f"UPDATE tasks SET {', '.join(fields)}, updated_at=? WHERE id=?", tuple(params) + (ts, int(tid)))
        return self.task(tid)

    def delete_task(self, tid: Any, options: Optional[dict] = None) -> dict:
        """删除任务。options.cleanup 控制是否级联清除资产/流量等。"""
        tid = int(tid)
        eng = self.engines.pop(tid, None)
        if eng:
            eng.stop()
        row = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (tid,))
        if not row:
            raise ValueError("任务不存在")
        cleanup = options or {}
        if cleanup.get("assets"):
            # 摘除任务关联资产：重建 task_ids（json_each 无 pos 列，用 json_group_array 过滤）
            self.db.execute(
                "UPDATE assets SET task_ids=(SELECT json_group_array(value) FROM json_each(assets.task_ids) WHERE value<>?) "
                "WHERE EXISTS (SELECT 1 FROM json_each(assets.task_ids) WHERE json_each.value=?)",
                (str(tid), str(tid)),
            )
        if cleanup.get("traffic"):
            self.db.execute("DELETE FROM traffic_exchanges WHERE task_ids LIKE ?", (f"%{tid}%",))
        self.db.execute("DELETE FROM findings WHERE task_id=?", (tid,))
        self.db.execute("DELETE FROM task_scope WHERE task_id=?", (tid,))
        self.db.execute("DELETE FROM tasks WHERE id=?", (tid,))
        if row["exploration_id"]:
            self.db.execute("DELETE FROM explorations WHERE id=?", (row["exploration_id"],))
        return {"ok": True}

    def set_active(self, tid: Any) -> dict:
        return self.task(tid)

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def control(self, tid: Any, action: str) -> dict:
        tid = int(tid)
        ts = now()
        row = self.db.query_one("SELECT status, paused FROM tasks WHERE id=?", (tid,))
        if not row:
            raise ValueError("任务不存在")
        if action == "pause":
            self.db.execute("UPDATE tasks SET paused=1, status='paused', updated_at=? WHERE id=?", (ts, tid))
            eng = self.engines.get(tid)
            if eng:
                eng.stop()
        elif action == "resume":
            self.db.execute("UPDATE tasks SET paused=0, status='running', updated_at=? WHERE id=?", (ts, tid))
            if tid not in self.engines:
                row2 = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (tid,))
                self._start_engine(tid, row2["exploration_id"])
            else:
                eng = self.engines.get(tid)
                eng.start()
        else:
            raise ValueError(f"未知动作: {action}")
        dto = self.task(tid)
        return {"id": dto["id"], "paused": dto["paused"], "queued": dto["queued"], "status": dto["status"]}

    def control_batch(self, tasks: list, action: str) -> list[dict]:
        out = []
        for t in tasks[:100]:
            try:
                r = self.control(t, action)
                out.append({"id": r["id"], "ok": True, "status": r["status"]})
            except Exception as e:
                out.append({"id": str(t), "ok": False, "error": str(e)})
        return out

    def intent_control(self, tid: Any, iid: Any, action: str) -> dict:
        ts = now()
        states = {
            "pause": "paused",
            "resume": "open",
            "cancel": "stopped",
        }
        if action not in states:
            raise ValueError(f"未知意图动作: {action}")
        self.db.execute(
            "UPDATE exploration_nodes SET state=?, updated_at=?, completed_at=? WHERE id=? AND kind='intent'",
            (states[action], ts, now() if action == "cancel" else None, int(iid)),
        )
        return {"id": sid(iid), "state": states[action]}

    def rerun_intent(self, tid: Any, iid: Any) -> dict:
        ts = now()
        self.db.execute(
            "UPDATE exploration_nodes SET state='open', blocked_reason=NULL, updated_at=? WHERE id=? AND kind='intent'",
            (ts, int(iid)),
        )
        eng = self.engines.get(int(tid))
        if eng:
            eng.notify_graph_changed()
        return {"ok": True}

    def rerun_blocked(self, tid: Any) -> dict:
        row = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (int(tid),))
        if not row:
            raise ValueError("任务不存在")
        cur = self.db.execute(
            "UPDATE exploration_nodes SET state='open', blocked_reason=NULL, updated_at=? "
            "WHERE exploration_id=? AND kind='intent' AND state IN ('blocked','exhausted','stopped')",
            (now(), row["exploration_id"]),
        )
        eng = self.engines.get(int(tid))
        if eng:
            eng.notify_graph_changed()
        return {"affected": cur.rowcount}

    # ------------------------------------------------------------------
    # 分类
    # ------------------------------------------------------------------
    def categories(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM task_categories ORDER BY id")
        return [{"id": sid(r["id"]), "name": r["name"]} for r in rows]

    def create_category(self, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("分类名不能为空")
        ts = now()
        nkey = name.lower()
        cur = self.db.execute(
            "INSERT INTO task_categories(name, nkey, created_at, updated_at) VALUES(?,?,?,?)",
            (name, nkey, ts, ts),
        )
        return {"id": sid(cur.lastrowid), "name": name}

    def rename_category(self, cid: Any, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("分类名不能为空")
        self.db.execute("UPDATE task_categories SET name=?, nkey=?, updated_at=? WHERE id=?",
                        (name, name.lower(), now(), int(cid)))
        return {"id": sid(cid), "name": name}

    def delete_category(self, cid: Any) -> None:
        self.db.execute("DELETE FROM task_categories WHERE id=?", (int(cid),))

    def set_task_category(self, tid: Any, cid: Optional[int]) -> dict:
        self.db.execute("UPDATE tasks SET category_id=? WHERE id=?", (cid, int(tid)))
        return self.task(tid)

    def batch_set_category(self, task_ids: list, cid: Optional[int]) -> dict:
        for t in task_ids:
            self.db.execute("UPDATE tasks SET category_id=? WHERE id=?", (cid, int(t)))
        return {"updated": len(task_ids)}

    # ------------------------------------------------------------------
    # 模板
    # ------------------------------------------------------------------
    def templates(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM task_templates ORDER BY updated_at DESC, id DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["category_id"] = sid(d["category_id"])
            d["intercept_rules"] = jloads(d["intercept_rules"], [])
            out.append(d)
        return out

    def create_template(self, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("模板名不能为空")
        ts = now()
        cur = self.db.execute(
            "INSERT INTO task_templates(name, nkey, description, goal, category_id, intercept_rules, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (name, name.lower(), data.get("description", ""), data.get("goal", ""),
             data.get("category_id"), jdumps(data.get("intercept_rules") or []), ts, ts),
        )
        return {"id": sid(cur.lastrowid), "name": name}

    def update_template(self, tid: Any, data: dict) -> dict:
        self.db.execute(
            "UPDATE task_templates SET name=?, description=?, goal=?, category_id=?, intercept_rules=?, updated_at=? "
            "WHERE id=?",
            (data.get("name", ""), data.get("description", ""), data.get("goal", ""),
             data.get("category_id"), jdumps(data.get("intercept_rules") or []), now(), int(tid)),
        )
        return {"ok": True}

    def delete_template(self, tid: Any) -> None:
        self.db.execute("DELETE FROM task_templates WHERE id=?", (int(tid),))

    # ------------------------------------------------------------------
    # 任务级 LLM 链
    # ------------------------------------------------------------------
    def set_llm_chain(self, tid: Any, profile_ids: list) -> dict:
        tid = int(tid)
        self.db.execute("DELETE FROM task_llm_profiles WHERE task_id=?", (tid,))
        ts = now()
        for pos, pid in enumerate(profile_ids):
            self.db.execute(
                "INSERT INTO task_llm_profiles(task_id, profile_id, position, status, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (tid, int(pid), pos, "ready", ts, ts),
            )
        first = profile_ids[0] if profile_ids else None
        self.db.execute(
            "UPDATE tasks SET llm_profile_id=?, llm_chain_revision=llm_chain_revision+1, updated_at=? WHERE id=?",
            (first, ts, tid),
        )
        return self.task(tid)

    def llm_resolution(self, tid: Any) -> list[dict]:
        """各角色实际解析到的 LLM 配置。"""
        role_keys = {"main": ["mainagent", "main"], "planner": ["planner"], "worker": ["worker"]}
        chain = self._task_llm_chain(tid)
        out = []
        for role, keys in role_keys.items():
            pid = None
            agent = self.db.query_one("SELECT llm_profile_id FROM agents WHERE key=?", (keys[0],))
            if agent and agent["llm_profile_id"]:
                pid = agent["llm_profile_id"]
            elif chain:
                pid = chain[0]["profile_id"]
            elif self.llm:
                act = self.llm.active_profile()
                pid = act["id"] if act else None
            prof = self.llm.get_profile(pid) if (self.llm and pid) else None
            out.append({
                "role": role, "profile_id": sid(pid),
                "name": prof["name"] if prof else "",
                "format": prof["format"] if prof else "",
                "model": prof["model"] if prof else "",
                "source": "task_chain",
                "available": bool(prof),
            })
        return out

    # ------------------------------------------------------------------
    # 任务级拦截规则
    # ------------------------------------------------------------------
    def intercept_rules(self, tid: Any) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM task_intercept_rules WHERE task_id=? ORDER BY id", (int(tid),)
        )
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["enabled"] = bool(d["enabled"])
            out.append(d)
        return out

    def add_intercept_rule(self, tid: Any, data: dict) -> dict:
        ts = now()
        cur = self.db.execute(
            "INSERT INTO task_intercept_rules(task_id, enabled, action, kind, pattern, note, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (int(tid), 1 if data.get("enabled", True) else 0, data.get("action", "block"),
             data.get("kind"), data.get("pattern"), data.get("note", ""), ts, ts),
        )
        return self._intercept_rule(cur.lastrowid)

    def update_intercept_rule(self, tid: Any, rid: Any, data: dict) -> dict:
        self.db.execute(
            "UPDATE task_intercept_rules SET enabled=?, action=?, kind=?, pattern=?, note=?, updated_at=? WHERE id=? AND task_id=?",
            (1 if data.get("enabled", True) else 0, data.get("action", "block"),
             data.get("kind"), data.get("pattern"), data.get("note", ""), now(), int(rid), int(tid)),
        )
        return self._intercept_rule(int(rid))

    def toggle_intercept_rule(self, tid: Any, rid: Any) -> dict:
        r = self.db.query_one("SELECT enabled FROM task_intercept_rules WHERE id=? AND task_id=?", (int(rid), int(tid)))
        if r:
            self.db.execute("UPDATE task_intercept_rules SET enabled=?, updated_at=? WHERE id=?",
                            (0 if r["enabled"] else 1, now(), int(rid)))
        return self._intercept_rule(int(rid))

    def delete_intercept_rule(self, tid: Any, rid: Any) -> None:
        self.db.execute("DELETE FROM task_intercept_rules WHERE id=? AND task_id=?", (int(rid), int(tid)))

    def _intercept_rule(self, rid: int) -> dict:
        r = self.db.query_one("SELECT * FROM task_intercept_rules WHERE id=?", (rid,))
        d = dict(r)
        d["id"] = sid(d["id"])
        d["enabled"] = bool(d["enabled"])
        return d

    # ------------------------------------------------------------------
    # 任务范围
    # ------------------------------------------------------------------
    def task_scope(self, tid: Any) -> list[dict]:
        rows = self.db.query("SELECT * FROM task_scope WHERE task_id=? ORDER BY id", (int(tid),))
        return [self._scope_dto(r) for r in rows]

    def _scope_dto(self, r) -> dict:
        d = dict(r)
        d["id"] = sid(d["id"])
        d["company_id"] = sid(d["company_id"])
        return d

    def add_scope(self, tid: Any, data: dict) -> dict:
        ts = now()
        cur = self.db.execute(
            "INSERT INTO task_scope(task_id, kind, company_id, domain, net, value, source, reason, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (int(tid), data.get("kind"), data.get("company_id"), data.get("domain"),
             data.get("net"), data.get("value"), data.get("source") or "manual", data.get("reason"), ts),
        )
        return self._scope_dto(self.db.query_one("SELECT * FROM task_scope WHERE id=?", (cur.lastrowid,)))

    def delete_scope(self, tid: Any, sid_val: Any) -> None:
        self.db.execute("DELETE FROM task_scope WHERE id=? AND task_id=?", (int(sid_val), int(tid)))

    # ------------------------------------------------------------------
    # 归档
    # ------------------------------------------------------------------
    def archives(self, q: Optional[str] = None, state: Optional[str] = None,
                 page: int = 0, size: int = 20) -> dict:
        where, params = [], []
        if q:
            where.append("(task_name LIKE ? OR task_description LIKE ? OR task_goal LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        if state:
            where.append("state=?")
            params.append(state)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.scalar(f"SELECT COUNT(*) FROM task_archives{clause}", tuple(params))
        rows = self.db.query(
            f"SELECT * FROM task_archives{clause} ORDER BY requested_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [self._archive_dto(r) for r in rows], "total": total}

    def _archive_dto(self, r) -> dict:
        d = dict(r)
        d["id"] = sid(d["id"])
        d["task_id"] = sid(d["task_id"])
        d["warnings"] = jloads(d["warnings"], [])
        d["data_counts"] = jloads(d["data_counts"], {})
        d["aggregate_stats"] = jloads(d["aggregate_stats"], {})
        return d

    def archive(self, tid: Any) -> dict:
        """归档单任务（简化：立即可用）。"""
        row = self.db.query_one("SELECT * FROM tasks WHERE id=?", (int(tid),))
        if not row:
            raise ValueError("任务不存在")
        ts = now()
        self.db.execute(
            "INSERT OR REPLACE INTO task_archives(task_id, state, phase, progress, task_name, task_description, "
            "task_goal, original_status, requested_at, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], "ready", "done", 100, row["name"], row["description"], row["goal"],
             row["status"], ts, ts, ts),
        )
        self.db.execute(
            "UPDATE tasks SET archived_at=?, status='done' WHERE id=?",
            (ts, row["id"]),
        )
        return {"ok": True}

    def archive_batch(self, task_ids: list) -> dict:
        for t in task_ids:
            self.archive(t)
        return {"archived": len(task_ids)}

    def restore(self, aid: Any) -> dict:
        ar = self.db.query_one("SELECT * FROM task_archives WHERE id=?", (int(aid),))
        if not ar:
            raise ValueError("归档不存在")
        self.db.execute(
            "UPDATE tasks SET archived_at=NULL WHERE id=?", (ar["task_id"],)
        )
        return {"ok": True}

    def restore_batch(self, archive_ids: list) -> dict:
        for a in archive_ids:
            self.restore(a)
        return {"restored": len(archive_ids)}

    def delete_archive(self, aid: Any) -> dict:
        self.db.execute("DELETE FROM task_archives WHERE id=?", (int(aid),))
        return {"ok": True}

    def delete_archive_batch(self, archive_ids: list) -> dict:
        for a in archive_ids:
            self.delete_archive(a)
        return {"deleted": len(archive_ids)}

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------
    def report(self, tid: Any) -> str:
        dto = self.task(tid)
        if not dto:
            raise ValueError("任务不存在")
        findings = self.db.query(
            "SELECT * FROM findings WHERE task_id=? ORDER BY created_at", (int(tid),)
        )
        lines = [
            f"# 渗透测试报告 — {dto['name'] or dto['description']}",
            "",
            f"**任务 ID**: {dto['id']}",
            f"**目标**: {dto['goal']}",
            f"**状态**: {dto['status']}",
            f"**创建时间**: {dto['created_at']}",
            "",
            "## 统计",
            "",
            f"- 目标数量: {dto['goals_total']} (已完成 {dto['goals_met']})",
            f"- 发现漏洞: {sum(dto['findings'].values())} 个",
            f"- Token 用量: {dto['tokens']['input_tokens'] + dto['tokens']['output_tokens']}",
            "",
            "## 漏洞明细",
            "",
        ]
        if findings:
            for f in findings:
                lines.append(
                    f"### {f['name'] or f['vulnclass']}（{f['severity']} / {f['status']}）\n\n"
                    f"{f['summary']}\n\n**证据**:\n\n```\n{f['evidence']}\n```\n\n"
                    f"**复现报告**:\n\n{f['report']}\n\n---\n"
                )
        else:
            lines.append("未发现漏洞。")
        return "\n".join(lines)
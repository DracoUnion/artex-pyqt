"""探索图服务：节点 / 意图 / 事实 / 漏洞 / 活动流 / 目标 / 约束 / 覆盖度。

对应 server 的 exploration.go、findings*.go、goals_api.go、constraints_api.go、
finding_traffic.go 的 API 行为。payload 在 DTO 层是 JSON 字符串。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .db import DB, get, jfield
from .util import jdumps, jloads, now, sid, unix_of


def node_dto(row) -> dict:
    payload = row["payload"]
    return {
        "id": sid(row["id"]),
        "type": row["kind"],
        "payload": payload,
        "priority": row["priority"],
        "state": row["state"],
        "origin": row["origin"] or "",
        "ts": row["created_at"],
        "delete_reason": row["delete_reason"] if "delete_reason" in row.keys() else None,
    }


def payload_text(payload: str) -> str:
    try:
        d = json.loads(payload or "{}")
        return d.get("text", "") or ""
    except Exception:
        return payload or ""


def intent_payload(row) -> dict:
    return jloads(row["payload"], {})


class ExplorationService:
    def __init__(self, db: DB, broadcaster=None, engine_registry=None, logsink=None):
        self.db = db
        self.broadcaster = broadcaster
        self.engines = engine_registry or {}
        self.logsink = logsink

    # ------------------------------------------------------------------
    # 图
    # ------------------------------------------------------------------
    def graph(self, task_id: int) -> dict:
        exploration_id = self._exploration_id(task_id)
        nodes = self.db.query(
            "SELECT * FROM exploration_nodes WHERE exploration_id=? ORDER BY id", (exploration_id,)
        )
        edges = self.db.query(
            "SELECT src_id AS src, dst_id AS dst, rel FROM exploration_edges "
            "WHERE exploration_id=?", (exploration_id,)
        )
        return {
            "nodes": [node_dto(r) for r in nodes],
            "edges": [{"src": sid(e["src"]), "dst": sid(e["dst"]), "rel": e["rel"]} for e in edges],
        }

    def _exploration_id(self, task_id: int) -> int:
        row = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise ValueError("任务不存在")
        return row["exploration_id"]

    def _node_assets(self, node_ids: list) -> dict:
        if not node_ids:
            return {}
        marks = ",".join("?" * len(node_ids))
        rows = self.db.query(
            f"SELECT a.node_id, a.asset_id, at2.type, "
            f"COALESCE(at2.domain, at2.ip, at2.url, at2.app_name, at2.service_name) AS label "
            f"FROM exploration_anchors a JOIN assets at2 ON at2.id=a.asset_id WHERE a.node_id IN ({marks})",
            tuple(node_ids),
        )
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(str(r["node_id"]), []).append({
                "id": sid(r["asset_id"]), "type": r["type"], "label": r["label"],
            })
        return out

    # ------------------------------------------------------------------
    # 意图 / 前沿 / 播报板
    # ------------------------------------------------------------------
    def intents(self, task_id: int, before: Optional[int] = None, page: int = 0,
                size: int = 100) -> Any:
        exploration_id = self._exploration_id(task_id)
        if before is not None:
            rows = self.db.query(
                "SELECT * FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND id<? "
                "ORDER BY id DESC LIMIT ?",
                (exploration_id, before, size),
            )
            return {"items": [node_dto(r) for r in rows], "has_more": len(rows) >= size}
        rows = self.db.query(
            "SELECT * FROM exploration_nodes WHERE exploration_id=? AND kind='intent' ORDER BY id",
            (exploration_id,),
        )
        return [node_dto(r) for r in rows]

    def frontier(self, task_id: int, limit: int = 20) -> list[dict]:
        exploration_id = self._exploration_id(task_id)
        rows = self.db.query(
            "SELECT * FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND state='open' "
            "ORDER BY priority DESC, id LIMIT ?",
            (exploration_id, limit),
        )
        return [node_dto(r) for r in rows]

    def nodes_page(self, task_id: int, page: int = 0, size: int = 30, kinds: Optional[list] = None,
                   states: Optional[list] = None, q: Optional[str] = None,
                   order: str = "desc") -> dict:
        exploration_id = self._exploration_id(task_id)
        where = ["exploration_id=?"]
        params: list = [exploration_id]
        if kinds:
            marks = ",".join("?" * len(kinds))
            where.append(f"kind IN ({marks})")
            params.extend(kinds)
        if states:
            marks = ",".join("?" * len(states))
            where.append(f"state IN ({marks})")
            params.extend(states)
        if q:
            where.append("(payload LIKE ? OR origin LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        clause = " AND ".join(where)
        total = self.db.scalar(f"SELECT COUNT(*) FROM exploration_nodes WHERE {clause}", tuple(params))
        od = "ASC" if order == "asc" else "DESC"
        rows = self.db.query(
            f"SELECT * FROM exploration_nodes WHERE {clause} ORDER BY id {od} LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        node_rows = [node_dto(r) for r in rows]
        ids = [int(r["id"]) for r in node_rows]
        # 涉及边 + 邻居 refs
        edges = []
        refs: dict[str, dict] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            edge_rows = self.db.query(
                f"SELECT * FROM exploration_edges WHERE exploration_id=? AND (src_id IN ({marks}) OR dst_id IN ({marks}))",
                tuple([exploration_id] + ids + ids),
            )
            neighbor_ids = set(ids)
            for e in edge_rows:
                edges.append({"src": sid(e["src_id"]), "dst": sid(e["dst_id"]), "rel": e["rel"]})
                neighbor_ids.add(e["src_id"])
                neighbor_ids.add(e["dst_id"])
            marks2 = ",".join("?" * len(neighbor_ids))
            ref_rows = self.db.query(
                f"SELECT * FROM exploration_nodes WHERE exploration_id=? AND id IN ({marks2})",
                tuple([exploration_id] + list(neighbor_ids)),
            )
            refs = {sid(r["id"]): node_dto(r) for r in ref_rows}
        assets = self._node_assets(ids)
        return {
            "items": node_rows, "total": total, "page": page, "size": size,
            "edges": edges, "refs": refs, "assets": assets,
        }

    # ------------------------------------------------------------------
    # 活动流
    # ------------------------------------------------------------------
    def activity(self, task_id: int, since: Optional[int] = None,
                 intent_id: Optional[int] = None, limit: int = 200) -> dict:
        exploration_id = self._exploration_id(task_id)
        where = ["exploration_id=?"]
        params: list = [exploration_id]
        if since:
            where.append("id>?")
            params.append(since)
        if intent_id:
            where.append("node_id=?")
            params.append(intent_id)
        clause = " AND ".join(where)
        rows = self.db.query(
            f"SELECT * FROM activity WHERE {clause} ORDER BY id LIMIT ?", tuple(params) + (limit,)
        )
        items = [self._activity_dto(r, include_detail=False) for r in rows]
        cursor = items[-1]["seq"] if items else (since or 0)
        return {"items": items, "cursor": cursor}

    def activity_history(self, task_id: int, session: Optional[str] = None,
                         before: Optional[int] = None, limit: int = 50) -> dict:
        """某会话的反向分页历史。session=main / plan / intent:<id>。"""
        exploration_id = self._exploration_id(task_id)
        where = ["exploration_id=?"]
        params: list = [exploration_id]
        if session == "main":
            where.append("worker='mainagent'")
        elif session == "plan":
            where.append("worker='planner'")
        elif session and session.startswith("intent:"):
            iid = session.split(":", 1)[1]
            if iid.isdigit():
                where.append("node_id=?")
                params.append(int(iid))
        if before:
            where.append("id<?")
            params.append(before)
        clause = " AND ".join(where)
        rows = self.db.query(
            f"SELECT * FROM activity WHERE {clause} ORDER BY id DESC LIMIT ?", tuple(params) + (limit,)
        )
        items = [self._activity_dto(r, include_detail=False) for r in reversed(rows)]
        return {"items": items, "has_more": len(rows) >= limit, "cursor": before or 0}

    def activity_detail(self, task_id: int, seq: int) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM activity WHERE id=? AND exploration_id=?", (seq, self._exploration_id(task_id)))
        if not row:
            return None
        return self._activity_dto(row, include_detail=True)

    @staticmethod
    def _activity_dto(row, include_detail: bool = True) -> dict:
        d = {
            "seq": row["id"],
            "intent_id": sid(row["node_id"]),
            "worker": row["worker"] or "",
            "ts": row["created_at"],
            "kind": row["kind"] or "",
            "tool": row["tool"] or None,
            "tool_use_id": row["tool_use_id"] or None,
            "is_error": bool(row["is_error"]),
            "summary": row["summary"] or "",
            "metadata": jloads(row["metadata"], {}),
            "main_seg": row["main_seg"] if "main_seg" in row.keys() else None,
            "input_tokens": row["input_tokens"] if "input_tokens" in row.keys() else None,
            "output_tokens": row["output_tokens"] if "output_tokens" in row.keys() else None,
            "cache_read_tokens": row["cache_read_tokens"] if "cache_read_tokens" in row.keys() else None,
            "cache_write_tokens": row["cache_write_tokens"] if "cache_write_tokens" in row.keys() else None,
        }
        if include_detail:
            d["detail"] = row["detail"] or None
        else:
            d["detail"] = None
        return d

    # ------------------------------------------------------------------
    # 目标 / 约束
    # ------------------------------------------------------------------
    def goals(self, task_id: int) -> list[dict]:
        exploration_id = self._exploration_id(task_id)
        rows = self.db.query(
            "SELECT * FROM exploration_nodes WHERE exploration_id=? AND kind='goal' ORDER BY id",
            (exploration_id,),
        )
        out = []
        for r in rows:
            p = jloads(r["payload"], {})
            out.append({
                "id": sid(r["id"]), "text": p.get("text", ""), "vulnclass": p.get("vulnclass") or "",
                "state": r["state"], "origin": r["origin"] or "", "ts": r["created_at"],
            })
        return out

    def add_goal(self, task_id: int, data: dict) -> dict:
        exploration_id = self._exploration_id(task_id)
        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError("目标内容不能为空")
        ts = now()
        cur = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, state, origin, created_at, updated_at) "
            "VALUES(?,?,?,'open',?,?,?)",
            (exploration_id, "goal", jdumps({"text": text, "vulnclass": data.get("vulnclass") or ""}),
             "user", ts, ts),
        )
        goal_id = cur.lastrowid
        # 复活任务
        self.db.execute(
            "UPDATE tasks SET status='running', paused=0 WHERE id=? AND deleted_at IS NULL",
            (task_id,),
        )
        self._wake_engine(task_id)
        return self._goal_dto(goal_id)

    def _goal_dto(self, goal_id: int) -> dict:
        r = self.db.query_one("SELECT * FROM exploration_nodes WHERE id=?", (goal_id,))
        p = jloads(r["payload"], {})
        return {"id": sid(r["id"]), "text": p.get("text", ""), "vulnclass": p.get("vulnclass") or "",
                "state": r["state"], "origin": r["origin"] or "", "ts": r["created_at"]}

    def update_goal(self, task_id: int, goal_id: int, data: dict) -> dict:
        r = self.db.query_one("SELECT * FROM exploration_nodes WHERE id=?", (goal_id,))
        if not r or r["kind"] != "goal":
            raise ValueError("目标不存在")
        p = jloads(r["payload"], {})
        if data.get("text") is not None:
            p["text"] = data["text"]
        if data.get("vulnclass") is not None:
            p["vulnclass"] = data["vulnclass"]
        ts = now()
        self.db.execute("UPDATE exploration_nodes SET payload=?, updated_at=? WHERE id=?", (jdumps(p), ts, goal_id))
        self.db.execute("UPDATE tasks SET status='running', paused=0 WHERE id=?", (task_id,))
        self._wake_engine(task_id)
        return self._goal_dto(goal_id)

    def delete_goal(self, task_id: int, goal_id: int) -> None:
        self.db.execute("DELETE FROM exploration_nodes WHERE id=? AND kind='goal'", (goal_id,))
        self.db.execute("DELETE FROM exploration_edges WHERE src_id=? OR dst_id=?", (goal_id, goal_id))

    def constraints(self, task_id: int) -> list[dict]:
        exploration_id = self._exploration_id(task_id)
        rows = self.db.query(
            "SELECT * FROM task_constraints WHERE exploration_id=? ORDER BY id", (exploration_id,)
        )
        return [{"id": sid(r["id"]), "kind": r["kind"], "text": r["text"],
                 "origin": r["origin"] or "", "ts": r["created_at"]} for r in rows]

    def add_constraint(self, task_id: int, data: dict) -> dict:
        exploration_id = self._exploration_id(task_id)
        ts = now()
        cur = self.db.execute(
            "INSERT INTO task_constraints(exploration_id, kind, text, origin, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (exploration_id, data.get("kind") or "deny", data.get("text") or "", data.get("origin") or "user", ts, ts),
        )
        return {"id": sid(cur.lastrowid), "kind": data.get("kind") or "deny",
                "text": data.get("text") or "", "origin": "user", "ts": ts}

    def update_constraint(self, task_id: int, cid: int, data: dict) -> dict:
        ts = now()
        self.db.execute(
            "UPDATE task_constraints SET kind=?, text=?, updated_at=? WHERE id=?",
            (data.get("kind") or "deny", data.get("text") or "", ts, cid),
        )
        r = self.db.query_one("SELECT * FROM task_constraints WHERE id=?", (cid,))
        return {"id": sid(r["id"]), "kind": r["kind"], "text": r["text"], "origin": r["origin"] or "", "ts": r["created_at"]}

    def delete_constraint(self, task_id: int, cid: int) -> None:
        self.db.execute("DELETE FROM task_constraints WHERE id=?", (cid,))

    def _wake_engine(self, task_id: int) -> None:
        eng = self.engines.get(int(task_id))
        if eng:
            eng.notify_graph_changed()

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------
    def findings(self, task_id: Optional[int] = None, page: int = 0, size: int = 50,
                 q: Optional[str] = None, severity: Optional[str] = None,
                 status: Optional[str] = None, task_filter: Optional[str] = None,
                 order: str = "desc") -> Any:
        where, params = [], []
        if task_id:
            where.append("task_id=?")
            params.append(task_id)
        elif task_filter and task_filter != "all":
            where.append("task_id=?")
            params.append(task_filter)
        if q:
            where.append("(vulnclass LIKE ? OR name LIKE ? OR summary LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        if severity:
            where.append("severity=?")
            params.append(severity)
        if status and status != "all":
            where.append("status=?")
            params.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.scalar(f"SELECT COUNT(*) FROM findings{clause}", tuple(params))
        od = "ASC" if order == "asc" else "DESC"
        rows = self.db.query(
            f"SELECT * FROM findings{clause} ORDER BY created_at {od}, id {od} LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        items = [self._finding_dto(r, include_report=False) for r in rows]
        return {"items": items, "total": total, "page": page, "size": size,
                "page_count": (total + size - 1) // size if size else 0}

    def _finding_dto(self, row, include_report: bool = True) -> dict:
        assets = []
        try:
            asset_ids = json.loads(row["asset_ids"] or "[]")
        except Exception:
            asset_ids = []
        if asset_ids:
            for aid in asset_ids[:20]:
                a = self.db.query_one(
                    "SELECT type, COALESCE(domain, ip, url, app_name, service_name) AS label "
                    "FROM assets WHERE id=?", (int(aid),)
                )
                if a:
                    assets.append({"id": str(aid), "type": a["type"], "label": a["label"]})
        task_name = ""
        if row["task_id"]:
            t = self.db.query_one("SELECT name, description FROM tasks WHERE id=?", (row["task_id"],))
            if t:
                task_name = t["name"] or t["description"] or ""
        d = {
            "id": sid(row["id"]),
            "vulnclass": row["vulnclass"],
            "name": row["name"] or "",
            "severity": row["severity"] or "",
            "status": row["status"] or "pending",
            "summary": row["summary"] or "",
            "evidence": row["evidence"] or "",
            "intent_id": sid(row["node_id"]),
            "task_id": sid(row["task_id"]),
            "task_description": task_name,
            "assets": assets,
            "ts": row["created_at"],
            "traffic_count": self.db.scalar("SELECT COUNT(*) FROM finding_traffic_bindings WHERE finding_id=?", (row["id"],)),
            "evidence_version": row["evidence_version"],
            "report_evidence_version": row["report_evidence_version"],
        }
        if include_report:
            d["report"] = row["report"] or ""
        return d

    def finding(self, fid: Any) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM findings WHERE id=?", (int(fid),))
        return self._finding_dto(row, include_report=True) if row else None

    def update_finding(self, fid: Any, data: dict) -> dict:
        fields = []
        params: list = []
        for k in ("vulnclass", "name", "severity", "summary", "evidence", "status", "report"):
            if k in data and data[k] is not None:
                fields.append(f"{k}=?")
                params.append(data[k])
        if not fields:
            raise ValueError("没有可更新字段")
        if data.get("report") is not None:
            fields.append("report_evidence_version=report_evidence_version+1")
        params.append(int(fid))
        self.db.execute(f"UPDATE findings SET {', '.join(fields)} WHERE id=?", tuple(params))
        return self.finding(fid)

    def delete_finding(self, fid: Any) -> None:
        self.db.execute("DELETE FROM findings WHERE id=?", (int(fid),))

    def findings_groups(self, page: int = 0, size: int = 50, q: Optional[str] = None) -> list[dict]:
        """按任务分组。"""
        where, params = [], []
        if q:
            where.append("(vulnclass LIKE ? OR name LIKE ? OR summary LIKE ?)")
            params.extend([f"%{q}%"] * 3)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.db.query(
            f"SELECT task_id, COUNT(*) AS count FROM findings{clause} GROUP BY task_id "
            f"ORDER BY MAX(id) DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        groups = []
        for r in rows:
            task = self.db.query_one("SELECT name, description, goal FROM tasks WHERE id=?", (r["task_id"],))
            groups.append({
                "task_id": sid(r["task_id"]),
                "count": r["count"],
                "task_name": (task["name"] or task["description"] or "任务") if task else "任务",
                "task_goal": task["goal"] if task else "",
            })
        return groups

    def findings_stats(self) -> dict:
        total = self.db.scalar("SELECT COUNT(*) FROM findings")
        sev = {}
        for s in ("critical", "high", "medium", "low"):
            sev[s] = self.db.scalar("SELECT COUNT(*) FROM findings WHERE severity=?", (s,))
        status = {}
        for s in ("pending", "in_progress", "confirmed", "resolved", "fixed",
                  "false_positive", "ignored", "duplicate", "risk_accepted"):
            status[s] = self.db.scalar("SELECT COUNT(*) FROM findings WHERE status=?", (s,))
        return {
            "total": total,
            "by_severity": sev,
            "by_status": status,
            "critical": sev.get("critical", 0),
            "high": sev.get("high", 0),
            "medium": sev.get("medium", 0),
            "low": sev.get("low", 0),
            "open": sum(sev.get(s, 0) for s in ("critical", "high", "medium", "low")),
        }

    def findings_asset_tree(self) -> list[dict]:
        """按资产视图左侧树：company -> assets -> findings。"""
        rows = self.db.query("SELECT * FROM findings ORDER BY id DESC")
        tree: dict[str, dict] = {}
        for frow in rows:
            try:
                asset_ids = json.loads(frow["asset_ids"] or "[]")
            except Exception:
                asset_ids = []
            for aid in asset_ids:
                a = self.db.query_one(
                    "SELECT type, COALESCE(domain, ip, url, app_name, service_name) AS label, company_id "
                    "FROM assets WHERE id=?", (int(aid),)
                )
                if not a:
                    continue
                comp_key = "未归属"
                comp_name = "未归属"
                if a["company_id"]:
                    c = self.db.query_one("SELECT name FROM companies WHERE id=?", (a["company_id"],))
                    if c:
                        comp_key = str(a["company_id"])
                        comp_name = c["name"]
                node = tree.setdefault(comp_key, {"name": comp_name, "assets": {}})
                asset_key = str(aid)
                an = node["assets"].setdefault(asset_key, {"id": asset_key, "type": a["type"],
                                                           "label": a["label"] or a["type"], "findings": []})
                an["findings"].append({
                    "id": sid(frow["id"]), "vulnclass": frow["vulnclass"], "name": frow["name"],
                    "severity": frow["severity"], "status": frow["status"],
                })
        return [{"id": k, "name": v["name"], "assets": list(v["assets"].values())}
                for k, v in tree.items()]

    def findings_lineage(self, fid: Any) -> dict:
        row = self.db.query_one("SELECT * FROM findings WHERE id=?", (int(fid),))
        if not row:
            return {"intents": [], "facts": []}
        node_id = row["node_id"]
        intents = []
        facts = []
        if node_id:
            # 向上找 spawns 链 + yields 到 finding 的意图/事实
            edges = self.db.query(
                "SELECT * FROM exploration_edges WHERE dst_id=?", (node_id,)
            )
            for e in edges:
                n = self.db.query_one("SELECT * FROM exploration_nodes WHERE id=?", (e["src_id"],))
                if not n:
                    continue
                p = jloads(n["payload"], {})
                item = {"id": sid(n["id"]), "type": n["kind"], "state": n["state"],
                        "text": p.get("text", ""), "ts": n["created_at"], "rel": e["rel"]}
                if n["kind"] == "intent":
                    intents.append(item)
                elif n["kind"] == "fact":
                    facts.append(item)
        return {"intents": intents, "facts": facts}

    # ------------------------------------------------------------------
    # 复测
    # ------------------------------------------------------------------
    def finding_retests(self, fid: Any) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM finding_retests WHERE finding_id=? ORDER BY id DESC", (int(fid),)
        )
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["finding_id"] = sid(d["finding_id"])
            d["conversation_id"] = sid(d["conversation_id"])
            d["snapshot"] = jloads(d["snapshot"], {})
            out.append(d)
        return out

    def active_retests(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM finding_retests WHERE status IN ('pending','running') ORDER BY id")
        return [{"id": sid(r["id"]), "finding_id": sid(r["finding_id"]), "status": r["status"]} for r in rows]

    def start_retest(self, fid: Any, data: dict) -> dict:
        finding = self.finding(fid)
        if not finding:
            raise ValueError("漏洞不存在")
        active = self.db.query_one(
            "SELECT id FROM finding_retests WHERE finding_id=? AND status IN ('pending','running')", (int(fid),)
        )
        if active:
            raise ValueError("该漏洞正在复测中")
        ts = now()
        # 创建一个复测会话
        cur = self.db.execute(
            "INSERT INTO conversations(agent_key, title, llm_profile_id, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            ("retester", f"复测: {finding['name'] or finding['vulnclass']}", None, ts, ts),
        )
        conv_id = cur.lastrowid
        snapshot = {
            "vulnclass": finding["vulnclass"], "name": finding["name"],
            "severity": finding["severity"], "summary": finding["summary"],
            "evidence": finding["evidence"], "task_id": finding["task_id"],
            "assets": finding["assets"],
        }
        self.db.execute(
            "INSERT INTO finding_retests(finding_id, conversation_id, status, snapshot, notes, summary, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (int(fid), conv_id, "running", jdumps(snapshot), data.get("notes") or "",
             "复测已发起", ts),
        )
        # 模拟复测异步完成
        import threading
        threading.Timer(8.0, self._complete_retest, args=(int(fid),)).start()
        return {"ok": True}

    def _complete_retest(self, fid: int) -> None:
        row = self.db.query_one("SELECT * FROM finding_retests WHERE finding_id=? AND status='running'", (fid,))
        if not row:
            return
        ts = now()
        self.db.execute(
            "UPDATE finding_retests SET status='completed', verdict='fixed', summary='复测完成：未复现，判定已修复', "
            "evidence='复测 agent 对目标执行了验证请求，未观察到原漏洞特征。', finished_at=? WHERE id=?",
            (ts, row["id"]),
        )
        self.db.execute("UPDATE findings SET status='fixed' WHERE id=?", (fid,))

    def deepen_finding(self, fid: Any, data: dict) -> dict:
        """深挖 single finding（简化：触发一次复测）。"""
        return self.start_retest(fid, data)

    # ------------------------------------------------------------------
    # token / 覆盖度
    # ------------------------------------------------------------------
    def tokens_by_worker(self, task_id: int) -> list[dict]:
        exploration_id = self._exploration_id(task_id)
        rows = self.db.query(
            "SELECT worker, SUM(COALESCE(input_tokens,0)) AS input_tokens, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens, "
            "SUM(COALESCE(cache_read_tokens,0)) AS cache_read_tokens, "
            "SUM(COALESCE(cache_write_tokens,0)) AS cache_write_tokens, COUNT(*) AS calls "
            "FROM activity WHERE exploration_id=? GROUP BY worker", (exploration_id,)
        )
        return [dict(r) for r in rows]

    def coverage(self, task_id: int) -> dict:
        """测试覆盖度 = 范围内资产中被 fact 锚定过的占比。"""
        # 分母：task_scope 匹配的资产
        rows = self.db.query(
            "SELECT * FROM task_scope WHERE task_id=?", (task_id,)
        )
        scope_ids = []
        for s in rows:
            if s["kind"] in ("root_domain", "subdomain") and s["domain"]:
                for r in self.db.query(
                    "SELECT id FROM assets WHERE domain=? OR root_domain=?", (s["domain"], s["domain"])
                ):
                    scope_ids.append(r["id"])
            elif s["kind"] == "ip" and s["net"]:
                for r in self.db.query("SELECT id FROM assets WHERE ip=?", (s["net"],)):
                    scope_ids.append(r["id"])
            elif s["kind"] == "company" and s["company_id"]:
                for r in self.db.query("SELECT id FROM assets WHERE company_id=?", (s["company_id"],)):
                    scope_ids.append(r["id"])
        scope_ids = list(dict.fromkeys(scope_ids))
        # 分子：fact 锚定过的
        tested = set()
        for aid in scope_ids:
            n = self.db.scalar(
                "SELECT COUNT(*) FROM exploration_anchors a JOIN exploration_nodes n ON n.id=a.node_id "
                "WHERE a.asset_id=? AND n.kind='fact' AND n.exploration_id=?",
                (aid, self._exploration_id(task_id)),
            )
            if n:
                tested.add(aid)
        return {
            "denominator": len(scope_ids), "tested": len(tested),
            "untested": len(scope_ids) - len(tested),
            "percent": round(100.0 * len(tested) / len(scope_ids), 1) if scope_ids else 0,
            "scope": [dict(s) for s in rows],
        }

    def coverage_graph(self, task_id: int) -> dict:
        """覆盖度力导向图：范围内资产节点 + 锚定边。"""
        cov = self.coverage(task_id)
        # cov["tested"] 是分子数量；单独算 tested 的 asset_id 集合
        exp_id = self._exploration_id(task_id)
        tested_rows = self.db.query(
            "SELECT DISTINCT a.asset_id FROM exploration_anchors a "
            "JOIN exploration_nodes n ON n.id=a.node_id "
            "WHERE n.kind='fact' AND n.exploration_id=?", (exp_id,)
        )
        tested_ids = {r["asset_id"] for r in tested_rows}
        rows = self.db.query("SELECT * FROM task_scope WHERE task_id=?", (task_id,))
        nodes = []
        edges = []
        seen = set()
        for s in rows:
            if s["kind"] in ("root_domain", "subdomain") and s["domain"]:
                for r in self.db.query(
                    "SELECT * FROM assets WHERE domain=? OR root_domain=?", (s["domain"], s["domain"])
                ):
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    nodes.append(self._coverage_node(r, tested_ids))
        if not nodes:
            # 用任务关联资产兜底
            for r in self.db.query(
                "SELECT a.* FROM assets a WHERE EXISTS (SELECT 1 FROM json_each(a.task_ids) WHERE json_each.value=?)",
                (str(task_id),),
            ):
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                nodes.append(self._coverage_node(r, tested_ids))
        return {"nodes": nodes, "edges": edges}

    def _coverage_node(self, row, tested_ids) -> dict:
        label = row["domain"] or row["ip"] or row["url"] or row["app_name"] or row["service_name"] or f"资产#{row['id']}"
        return {
            "id": sid(row["id"]), "type": row["type"], "label": label,
            "tested": row["id"] in tested_ids, "ts": row["last_seen"],
        }

    def asset_refs(self, task_id: int, asset_id: int) -> dict:
        """某资产在本任务关联的 intents/facts/findings。"""
        rows = self.db.query(
            "SELECT a.node_id FROM exploration_anchors a "
            "JOIN exploration_nodes n ON n.id=a.node_id WHERE a.asset_id=? AND n.exploration_id=?",
            (asset_id, self._exploration_id(task_id)),
        )
        intents, facts, findings = [], [], []
        for r in rows:
            n = self.db.query_one("SELECT * FROM exploration_nodes WHERE id=?", (r["node_id"],))
            if not n:
                continue
            p = jloads(n["payload"], {})
            item = {"id": sid(n["id"]), "text": p.get("text", ""), "state": n["state"], "ts": n["created_at"]}
            if n["kind"] == "intent":
                intents.append(item)
            elif n["kind"] == "fact":
                facts.append(item)
            elif n["kind"] == "finding":
                findings.append(item)
        return {"intents": intents, "facts": facts, "findings": findings}

    def intent_assets(self, task_id: int) -> list[dict]:
        exploration_id = self._exploration_id(task_id)
        rows = self.db.query(
            "SELECT a.node_id, a.asset_id, at2.type, "
            "COALESCE(at2.domain, at2.ip, at2.url, at2.app_name, at2.service_name) AS label "
            "FROM exploration_anchors a JOIN assets at2 ON at2.id=a.asset_id "
            "JOIN exploration_nodes n ON n.id=a.node_id WHERE n.exploration_id=? AND n.kind='intent'",
            (exploration_id,),
        )
        return [{"node_id": sid(r["node_id"]), "asset_id": sid(r["asset_id"]),
                 "type": r["type"], "label": r["label"]} for r in rows]

    # ------------------------------------------------------------------
    # 主会话
    # ------------------------------------------------------------------
    def main_sessions(self, task_id: int) -> list[dict]:
        exploration_id = self._exploration_id(task_id)
        rows = self.db.query(
            "SELECT * FROM main_sessions WHERE exploration_id=? ORDER BY seq", (exploration_id,)
        )
        return [{"seq": r["seq"], "created_at": r["created_at"]} for r in rows]

    def new_main_session(self, task_id: int) -> dict:
        exploration_id = self._exploration_id(task_id)
        max_seq = self.db.scalar(
            "SELECT COALESCE(MAX(seq),0) FROM main_sessions WHERE exploration_id=?", (exploration_id,)
        )
        seq = max_seq + 1
        ts = now()
        self.db.execute(
            "INSERT INTO main_sessions(exploration_id, seq, created_at) VALUES(?,?,?)",
            (exploration_id, seq, ts),
        )
        return {"seq": seq, "created_at": ts}

    # ------------------------------------------------------------------
    # finding 流量绑定
    # ------------------------------------------------------------------
    def finding_traffic(self, fid: Any) -> list[dict]:
        rows = self.db.query(
            "SELECT b.*, s.url, s.method, s.status, s.content_type, s.captured_at "
            "FROM finding_traffic_bindings b JOIN traffic_evidence_snapshots s ON s.id=b.snapshot_id "
            "WHERE b.finding_id=? ORDER BY b.position, b.id", (int(fid),)
        )
        return [dict(r) for r in rows]

"""会话/对话服务：独立会话(chat 页) + 任务内主 Agent 对话。

会话消息以 conversation_activities 存储（ActivityDTO 形状），演示模式
下由模拟 agent 回复 + 可选 LLM 推理。任务内 chat 复用 activity 表。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from .db import DB
from .util import jdumps, jloads, now, sid, unix_of


class ConversationService:
    def __init__(self, db: DB, logsink=None, llm=None, broadcaster=None, engines=None):
        self.db = db
        self.logsink = logsink
        self.llm = llm
        self.broadcaster = broadcaster
        self.engines = engines or {}

    # ------------------------------------------------------------------
    # 会话 CRUD
    # ------------------------------------------------------------------
    def list_conversations(self, agent_filter: Optional[str] = None) -> list[dict]:
        where, params = [], []
        if agent_filter:
            where.append("agent_key=?")
            params.append(agent_filter)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self.db.query(
            f"SELECT * FROM conversations{clause} ORDER BY pinned_at DESC, updated_at DESC, id DESC",
            tuple(params),
        )
        out = []
        for r in rows:
            running = self._conv_running(r["id"])
            msg_count = self.db.scalar(
                "SELECT COUNT(*) FROM conversation_activities WHERE conversation_id=?", (r["id"],)
            )
            d = dict(r)
            d["id"] = sid(d["id"])
            d["llm_profile_id"] = sid(d["llm_profile_id"])
            d["pinned"] = bool(d["pinned_at"])
            d["running"] = running
            d["message_count"] = msg_count
            out.append(d)
        return out

    def _conv_running(self, cid: int) -> bool:
        # 最近一条 user 消息之后是否有 agent 回复在途（简化：看状态）
        return False

    def get_conversation(self, cid: Any) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM conversations WHERE id=?", (int(cid),))
        if not row:
            return None
        d = dict(row)
        d["id"] = sid(d["id"])
        d["llm_profile_id"] = sid(d["llm_profile_id"])
        d["pinned"] = bool(d["pinned_at"])
        d["running"] = self._conv_running(row["id"])
        return d

    def create_conversation(self, data: dict) -> dict:
        ts = now()
        agent_key = data.get("agent_key") or "assistant"
        title = data.get("title") or ""
        cur = self.db.execute(
            "INSERT INTO conversations(agent_key, title, llm_profile_id, created_at, updated_at) VALUES(?,?,?,?,?)",
            (agent_key, title, data.get("llm_profile_id"), ts, ts),
        )
        cid = cur.lastrowid
        return self.get_conversation(cid)

    def rename_conversation(self, cid: Any, data: dict) -> dict:
        self.db.execute("UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                        (data.get("title") or "", now(), int(cid)))
        return self.get_conversation(cid)

    def set_profile(self, cid: Any, data: dict) -> dict:
        self.db.execute("UPDATE conversations SET llm_profile_id=?, updated_at=? WHERE id=?",
                        (data.get("llm_profile_id"), now(), int(cid)))
        return self.get_conversation(cid)

    def delete_conversation(self, cid: Any) -> None:
        self.db.execute("DELETE FROM conversations WHERE id=?", (int(cid),))

    def delete_batch(self, ids: list) -> dict:
        for i in ids:
            self.delete_conversation(i)
        return {"deleted": len(ids)}

    def pin(self, cid: Any, pinned: bool) -> dict:
        self.db.execute("UPDATE conversations SET pinned_at=?, updated_at=? WHERE id=?",
                        (now() if pinned else None, now(), int(cid)))
        return self.get_conversation(cid)

    # ------------------------------------------------------------------
    # 消息
    # ------------------------------------------------------------------
    def messages(self, cid: Any, since: Optional[int] = None,
                 before: Optional[int] = None, limit: int = 50) -> dict:
        where = ["conversation_id=?"]
        params: list = [int(cid)]
        if since:
            where.append("id>?")
            params.append(since)
        if before:
            where.append("id<?")
            params.append(before)
        clause = " AND ".join(where)
        if before:
            rows = self.db.query(
                f"SELECT * FROM conversation_activities WHERE {clause} ORDER BY id DESC LIMIT ?",
                tuple(params) + (limit,),
            )
            rows = list(reversed(rows))
        else:
            rows = self.db.query(
                f"SELECT * FROM conversation_activities WHERE {clause} ORDER BY id LIMIT ?",
                tuple(params) + (limit,),
            )
        items = [self._msg_dto(r) for r in rows]
        cursor = items[-1]["seq"] if items else (since or 0)
        running = self._conv_running(int(cid))
        return {"items": items, "cursor": cursor, "running": running,
                "has_more": len(rows) >= limit}

    def _msg_dto(self, r) -> dict:
        return {
            "seq": r["id"], "worker": r["worker"] or "", "kind": r["kind"] or "",
            "tool": r["tool"], "tool_use_id": r["tool_use_id"],
            "is_error": bool(r["is_error"]), "summary": r["summary"] or "",
            "detail": r["detail"], "metadata": jloads(r["metadata"], {}),
            "ts": r["created_at"], "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
        }

    def send_message(self, cid: Any, data: dict) -> dict:
        """发送用户消息并异步触发 agent 回复。"""
        cid = int(cid)
        msg = (data.get("message") or "").strip()
        if not msg:
            raise ValueError("消息不能为空")
        ts = now()
        attachments = data.get("attachments") or []
        metadata = {}
        if attachments:
            metadata["attachments"] = attachments
        self.db.execute(
            "INSERT INTO conversation_activities(conversation_id, worker, kind, summary, detail, metadata, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (cid, "user", "user", msg, msg, jdumps(metadata), ts),
        )
        self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (ts, cid))
        threading.Thread(target=self._agent_reply, args=(cid, msg), daemon=True).start()
        return {"ok": True}

    def _agent_reply(self, cid: int, user_msg: str) -> None:
        time.sleep(0.8)
        conv = self.get_conversation(cid)
        agent_key = conv["agent_key"] if conv else "assistant"
        ts = now()
        # 尝试 LLM
        content = None
        if self.llm and self.llm.available():
            try:
                content = self.llm.complete(
                    f"你是 ARTEX 会话助手（agent={agent_key}）。用户说：{user_msg}\n请给出有帮助的回应。",
                    session_id=f"conv-{cid}",
                )
            except Exception:
                content = None
        if not content:
            content = self._demo_reply(agent_key, user_msg)
        self.db.execute(
            "INSERT INTO conversation_activities(conversation_id, worker, kind, summary, detail, input_tokens, output_tokens, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (cid, agent_key, "answer", content, content,
             0 if not self.llm else 0, 0 if not self.llm else 0, now()),
        )
        self.db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), cid))
        if self.broadcaster:
            self.broadcaster.publish(f"conv-{cid}", {"type": "message", "seq": self.db.last_id()})

    def _demo_reply(self, agent_key: str, user_msg: str) -> str:
        """演示回复：识别用户意图给出结构化回答。"""
        lowered = user_msg.lower()
        if any(k in lowered for k in ("你好", "hi", "hello", "在吗")):
            return "你好！我是 ARTEX 的自主渗透助手。我可以协助分析目标、规划攻击链路、解释漏洞细节。\n\n请告诉我你的目标或问题。"
        if any(k in lowered for k in ("任务", "task", "扫描")):
            return "关于任务：你可以在任务列表创建新任务，指定目标和约束后我会自主执行侦察、扫描与漏洞确认，并生成渗透报告。"
        if any(k in lowered for k in ("漏洞", "sql", "xss", "注入")):
            return "关于漏洞：发现的高危漏洞会进入「发现」列表。你可以发起复测验证修复情况，我会生成证据与复现步骤。"
        if "?" in user_msg or "？" in user_msg:
            return "这是一个很好的问题。作为演示环境，当前没有接入 LLM 配置。请在「系统配置 → LLM 配置」中添加 API Key，我就能提供真正的智能回答。"
        return "收到。你可以在 LLM 配置中添加可用的模型后，与我进行真实的自主对话。当前为离线演示模式。"

    # ------------------------------------------------------------------
    # 任务内主 Agent 对话
    # ------------------------------------------------------------------
    def task_chat_status(self, tid: Any) -> dict:
        row = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (int(tid),))
        running = False
        if row:
            # 最近有 user 消息且其后无 answer -> 在跑
            last = self.db.query_one(
                "SELECT kind FROM activity WHERE exploration_id=? ORDER BY id DESC LIMIT 1",
                (row["exploration_id"],),
            )
            running = bool(last and last["kind"] == "user")
        return {"running": running}

    def task_chat(self, tid: Any, data: dict) -> dict:
        """向任务主 Agent 发消息（202 后台执行）。"""
        tid = int(tid)
        row = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (tid,))
        if not row:
            raise ValueError("任务不存在")
        exp_id = row["exploration_id"]
        msg = (data.get("message") or "").strip()
        if not msg:
            raise ValueError("消息不能为空")
        ts = now()
        self.db.execute(
            "INSERT INTO activity(exploration_id, node_id, worker, kind, summary, detail, metadata, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (exp_id, None, "mainagent", "user", msg, msg, jdumps({}), ts),
        )
        eng = self.engines.get(tid)
        if eng:
            eng.broadcaster.publish(tid, {"seq": self.db.last_id(), "worker": "mainagent",
                                          "kind": "user", "summary": msg, "ts": ts})
        threading.Thread(target=self._task_chat_reply, args=(tid, exp_id, msg), daemon=True).start()
        return {"ok": True, "accepted": True}

    def _task_chat_reply(self, tid: int, exp_id: int, msg: str) -> None:
        time.sleep(1.0)
        content = None
        if self.llm and self.llm.available():
            try:
                content = self.llm.complete(
                    f"你是 ARTEX 任务主控 Agent。任务 #{tid}。用户消息：{msg}\n"
                    f"请给出指挥性回复（可引用探索进展、决策下一步）。",
                    session_id=f"task-{tid}-main",
                )
            except Exception:
                content = None
        if not content:
            content = (
                f"收到。任务 #{tid} 正在自主推进中。\n\n"
                f"你的消息：{msg}\n\n"
                f"当前为离线演示模式。配置 LLM 后可获得真正的自主指挥能力。"
            )
        self.db.execute(
            "INSERT INTO activity(exploration_id, node_id, worker, kind, summary, detail, metadata, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (exp_id, None, "mainagent", "answer", content, content, jdumps({}), now()),
        )
        act_id = self.db.last_id()
        eng = self.engines.get(tid)
        if eng:
            eng.broadcaster.publish(tid, {"seq": act_id, "worker": "mainagent",
                                          "kind": "answer", "summary": content, "ts": now()})

    def task_chat_stop(self, tid: Any) -> dict:
        return {"ok": True}

    # ------------------------------------------------------------------
    # @提及 搜索
    # ------------------------------------------------------------------
    def mentions(self, q: str, limit: int = 20) -> list[dict]:
        q = (q or "").lower()
        out = []
        for row in self.db.query("SELECT * FROM conversations ORDER BY updated_at DESC"):
            title = row["title"] or ""
            if not q or q in title.lower() or q in row["agent_key"]:
                out.append({"type": "conversation", "id": str(row["id"]),
                            "title": title, "agent_key": row["agent_key"]})
                if len(out) >= limit:
                    break
        # 资产提及
        for row in self.db.query("SELECT id, type, COALESCE(domain, ip, url, app_name) AS label FROM assets WHERE domain IS NOT NULL OR ip IS NOT NULL OR url IS NOT NULL ORDER BY id DESC LIMIT 20"):
            label = row["label"] or ""
            if not q or q in label.lower():
                out.append({"type": "asset", "id": str(row["id"]), "title": label, "asset_type": row["type"]})
                if len(out) >= limit:
                    break
        return out[:limit]

    # ------------------------------------------------------------------
    # 旁路提问 /btw
    # ------------------------------------------------------------------
    def side_question_sessions(self, scope: str, sid_val: Any) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM side_question_sessions WHERE "
            + (("conversation_id=?" if scope == "conversation" else
                "task_id=?" if scope == "task" else "intent_id=?"),)
            , (int(sid_val),),
        )
        return [dict(r) for r in rows]

    def create_side_question(self, scope: str, sid_val: Any, data: dict) -> dict:
        import uuid
        ts = now()
        session_key = f"sq-{uuid.uuid4().hex[:12]}"
        if scope == "conversation":
            conv = self.get_conversation(sid_val)
            self.db.execute(
                "INSERT INTO side_question_sessions(session_key, conversation_id, run_id, version, snapshot, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (session_key, int(sid_val), int(time.time()), 1, jdumps({}), ts),
            )
        else:
            self.db.execute(
                "INSERT INTO side_question_sessions(session_key, task_id, exploration_id, run_id, version, snapshot, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (session_key, int(sid_val), self._exp_of_task(sid_val), int(time.time()), 1, jdumps({}), ts),
            )
        # 创建请求
        req_id = f"sqr-{uuid.uuid4().hex[:12]}"
        client_id = data.get("client_id") or "client-1"
        model = data.get("model") or {}
        self.db.execute(
            "INSERT INTO side_question_requests(id, session_key, generation, client_id, question, status, model, snapshot_at, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (req_id, session_key, 0, client_id, data.get("question") or "", "running",
             jdumps(model), ts, ts),
        )
        threading.Thread(target=self._answer_side_question, args=(req_id, session_key, data.get("question") or ""), daemon=True).start()
        return {"request_id": req_id, "session_key": session_key}

    def _exp_of_task(self, tid: Any) -> int:
        r = self.db.query_one("SELECT exploration_id FROM tasks WHERE id=?", (int(tid),))
        return r["exploration_id"] if r else 0

    def _answer_side_question(self, req_id: str, session_key: str, question: str) -> None:
        time.sleep(1.5)
        answer = ("旁路分析：当前演示环境下，建议检查目标的认证逻辑、参数校验与业务边界。"
                  "配置 LLM 后我可以用真实推理辅助分析。")
        self.db.execute(
            "UPDATE side_question_requests SET answer=?, status='completed' WHERE id=?",
            (answer, req_id),
        )

    def side_question_requests(self, session_key: str) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM side_question_requests WHERE session_key=? ORDER BY ordinal DESC", (session_key,)
        )
        return [dict(r) for r in rows]

    def cancel_side_question(self, req_id: str) -> dict:
        self.db.execute("UPDATE side_question_requests SET status='cancelled' WHERE id=?", (req_id,))
        return {"ok": True}

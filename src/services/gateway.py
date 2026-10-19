"""进程内网关：PyQt 直接调用 services 的分发层（替代 FastAPI HTTP 服务）。

对齐 server.py 的响应面：方法 + 路径 -> service 调用，参数来自 params/body，
返回结构与原 HTTP JSON 一致。SSE 改为进程内队列订阅（Broadcaster / LogSink）。
不再启动 HTTP 服务，无 uvicorn / fastapi 依赖。
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

from .agents import AgentsService
from .assets import AssetsService
from .broadcast import Broadcaster
from .catalog import SkillsService, MCPService, ToolsService
from .conversations import ConversationService
from .db import DB
from .engine import Engine
from .exploration import ExplorationService
from .intercept import InterceptService
from .llm import LLMService
from .logsink import LogSink
from .misc import (
    CommandsService, LLMRecordService, StatsService, SyncService, UpdateService,
    WorkspaceService,
)
from .notify import NotifyService
from .settings import SettingsService
from .tasks import TasksService
from .traffic import TrafficService
from .util import jdumps, jloads, now, sid


class GatewayError(Exception):
    """携带 HTTP 语义的网关错误：message + status。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def settings_traffic_enabled(db) -> bool:
    row = db.query_one("SELECT value FROM settings WHERE key='traffic_enabled'")
    return not row or row["value"].lower() in ("true", "1")


def build_services(db: DB, logsink: LogSink, data_dir: str, skills_dir: str, broadcast: Broadcaster):
    """构造服务容器（对齐 server.py build_services）。"""
    settings = SettingsService(db)
    llm = LLMService(db, logsink)
    agents = AgentsService(db, logsink)
    agents.seed_builtin()
    tools = ToolsService(db, logsink)
    tools.seed_builtin()
    assets = AssetsService(db, logsink)
    engines: dict[int, Engine] = {}

    def engine_factory(task_id: int, exploration_id: int) -> Engine:
        return Engine(
            db, logsink, broadcast, task_id, exploration_id, llm,
            settings.all(), settings.get_int("worker_concurrency", 3), log=logsink,
        )

    tasks_svc = TasksService(db, engines, engine_factory, logsink, llm, assets)
    exploration = ExplorationService(db, broadcast, engines, logsink)
    conversations = ConversationService(db, logsink, llm, broadcast, engines)
    intercept = InterceptService(db, logsink, llm, engines)
    traffic = TrafficService(db, logsink)
    workspace = WorkspaceService(db, os.path.join(data_dir, "workspace"),
                                 os.path.join(data_dir, "uploads"))
    mcp = MCPService(db, logsink)
    skills = SkillsService(db, skills_dir, logsink)
    commands = CommandsService(db)
    llmrec = LLMRecordService(db)
    stats = StatsService(db)
    update = UpdateService(logsink)
    notify = NotifyService(db, logsink)
    sync = SyncService(db, mcp, assets, logsink)
    return {
        "settings": settings, "llm": llm, "agents": agents,
        "tools": tools, "assets": assets, "tasks": tasks_svc, "exploration": exploration,
        "conversations": conversations, "intercept": intercept, "traffic": traffic,
        "workspace": workspace, "mcp": mcp, "skills": skills, "commands": commands,
        "llmrec": llmrec, "stats": stats, "update": update, "notify": notify,
        "sync": sync, "db": db, "logsink": logsink, "broadcast": broadcast, "engines": engines,
    }


def rebuild_running_engines(svcs: dict) -> None:
    """启动时恢复运行中的任务引擎。"""
    rows = svcs["db"].query(
        "SELECT id, exploration_id FROM tasks WHERE deleted_at IS NULL AND archived_at IS NULL "
        "AND status='running'"
    )
    for r in rows:
        eng = svcs["tasks"]._start_engine(r["id"], r["exploration_id"])
        svcs["engines"][r["id"]] = eng


class Gateway:
    """进程内后端句柄：持有服务容器，提供 dispatch + stream。"""

    def __init__(self, data_dir: str, skills_dir: str):
        self.data_dir = data_dir
        self.skills_dir = skills_dir
        self.db = DB(data_dir)
        self.logsink = LogSink(self.db)
        self.broadcast = Broadcaster()
        self.svcs = build_services(self.db, self.logsink, data_dir, skills_dir, self.broadcast)
        rebuild_running_engines(self.svcs)
        # 启动流量录制代理（独立监听 127.0.0.1:8788，供 worker HTTP 工具留痕）
        from .proxy import TrafficProxy
        self.traffic_proxy = TrafficProxy(self.db, enabled=settings_traffic_enabled(self.db))
        self.traffic_proxy.start()

    def close(self) -> None:
        try:
            self.traffic_proxy.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _svcs(self):
        return self.svcs

    def _not_found(self, message: str) -> GatewayError:
        return GatewayError(message, 404)

    @staticmethod
    def _pi(params: dict, key: str, default: Any = None) -> Any:
        v = params.get(key, default)
        try:
            return None if v in (None, "") else int(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _pb(params: dict, key: str, default: bool = False) -> bool:
        v = params.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _split_csv(v: Any) -> Optional[list]:
        if not v:
            return None
        return [x for x in str(v).split(",") if x]

    # ------------------------------------------------------------------
    # 路由入口
    # ------------------------------------------------------------------
    def dispatch(self, method: str, path: str, params: Optional[dict] = None,
                 body: Optional[dict] = None, files: Optional[dict] = None) -> Any:
        try:
            return self._do_dispatch(method, path, params or {}, body or {}, files or {})
        except GatewayError:
            raise
        except ValueError as e:
            # services 的 ValueError 语义等价原 FastAPI exception_handler(ValueError) -> 400
            raise GatewayError(str(e), 400) from e

    def _do_dispatch(self, method: str, path: str, params: dict,
                     body: dict, files: dict) -> Any:
        svcs = self._svcs()

        # 统一挂件：无路径参数时匹配静态路径
        m = method.upper()

        if m == "GET" and path == "/api/health":
            return {"status": "ok", "version": "standalone"}
        if m == "GET" and path == "/api/stats":
            return {
                "active_tasks": svcs["tasks"].active_id() is not None,
                "engines": len(svcs["engines"]),
                "pending_intercepts": svcs["intercept"].pending_count(),
                "uptime": int(time.time()),
            }

        # ---- 任务 ----------------------------------------------------
        if m == "GET" and path == "/api/tasks":
            return {
                "items": svcs["tasks"].list_tasks(
                    self._pb(params, "archived"), params.get("q"),
                    params.get("status"), self._pi(params, "category_id")),
                "active_id": svcs["tasks"].active_id(),
            }
        if m == "POST" and path == "/api/tasks":
            return svcs["tasks"].create_task(body)
        if m == "PATCH" and path == "/api/tasks":
            return svcs["tasks"].update_metadata(body.get("id"), body)
        if m == "DELETE" and path == "/api/tasks":
            if body.get("ids"):
                return {"deleted": [svcs["tasks"].delete_task(i, body.get("options") or {}) for i in body["ids"]]}
            raise GatewayError("缺少 ids")

        # 统一 /api/tasks/{tid} 及其子路径
        task_id = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)$")
        if task_id is not None:
            if m == "GET":
                dto = svcs["tasks"].task(task_id)
                if not dto:
                    raise self._not_found("任务不存在")
                return dto
            if m == "PATCH":
                return svcs["tasks"].update_metadata(task_id, body)
            if m == "DELETE":
                return svcs["tasks"].delete_task(task_id, body or {})

        if path == "/api/tasks/control/batch" and m == "POST":
            return svcs["tasks"].control_batch(body.get("tasks", []), body.get("action", ""))
        if path == "/api/tasks/archive/batch" and m == "POST":
            return svcs["tasks"].archive_batch(body.get("task_ids", []))

        # 待办：占位，防止未命中时静默
        # 任务级子路径
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/control$")
        if tid is not None and m == "POST":
            return svcs["tasks"].control(tid, body.get("action", ""))
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/archive$")
        if tid is not None and m == "POST":
            return svcs["tasks"].archive(tid)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/coverage$")
        if tid is not None and m == "GET":
            return svcs["exploration"].coverage(tid)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/coverage-graph$")
        if tid is not None and m == "GET":
            return svcs["exploration"].coverage_graph(tid)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/scope$")
        if tid is not None and m == "GET":
            return svcs["tasks"].task_scope(tid)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/goals$")
        if tid is not None:
            if m == "GET":
                return svcs["exploration"].goals(tid)
            if m == "POST":
                return svcs["exploration"].add_goal(tid, body)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/constraints$")
        if tid is not None:
            if m == "GET":
                return svcs["exploration"].constraints(tid)
            if m == "POST":
                return svcs["exploration"].add_constraint(tid, body)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/intent-assets$")
        if tid is not None and m == "GET":
            return svcs["exploration"].intent_assets(tid)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/assets$")
        if tid is not None and m == "POST":
            n = svcs["assets"].attach_to_task(tid, body.get("asset_ids"),
                                              body.get("scope"), body.get("company_ids"))
            return {"attached": n}
        match = self._match_route(path, "^/api/tasks/(?P<tid>[^/]+)/assets/(?P<aid>[^/]+)$")
        if match and m == "DELETE":
            svcs["assets"].detach_from_task(int(match["tid"]), int(match["aid"]))
            return {"ok": True}
        match = self._match_route(path, "^/api/tasks/(?P<tid>[^/]+)/intents/(?P<iid>[^/]+)/messages$")
        if match and m == "POST":
            return self._send_worker_message(svcs, int(match["tid"]), int(match["iid"]), body)
        tid = self._match_int(path, "^/api/tasks/(?P<id>[^/]+)/asset-refs$")
        if tid is not None and m == "GET":
            return svcs["exploration"].asset_refs(tid, int(params.get("asset_id") or 0))

        # ---- 任务分类 / 归档 / LLM 链（少量用到的面） --------------
        if m == "GET" and path == "/api/task-categories":
            return svcs["tasks"].categories()
        if m == "POST" and path == "/api/task-categories":
            return svcs["tasks"].create_category(body)
        cid = self._match_int(path, "^/api/task-categories/(?P<id>[^/]+)$")
        if cid is not None and m == "DELETE":
            svcs["tasks"].delete_category(cid)
            return {"ok": True}

        if m == "GET" and path == "/api/task-archives":
            return svcs["tasks"].archives(params.get("q"), params.get("state"),
                                          self._pi(params, "page", 0), self._pi(params, "size", 20))
        aid = self._match_int(path, "^/api/task-archives/(?P<id>[^/]+)/restore$")
        if aid is not None and m == "POST":
            return svcs["tasks"].restore(aid)
        if path == "/api/task-archives/restore/batch" and m == "POST":
            return svcs["tasks"].restore_batch(body.get("archive_ids", []))
        if path == "/api/task-archives/delete/batch" and m == "POST":
            return svcs["tasks"].delete_archive_batch(body.get("archive_ids", []))

        # 报告（返回 markdown 文本）
        if m == "GET" and path == "/api/report" and params.get("task"):
            return svcs["tasks"].report(int(params["task"]))

        # ---- 探索 ------------------------------------------------
        if m == "GET" and path == "/api/exploration/intents":
            return svcs["exploration"].intents(
                int(params.get("task") or 0), self._pi(params, "before"),
                self._pi(params, "page", 0), self._pi(params, "size", 100))
        if m == "GET" and path == "/api/exploration/graph":
            return svcs["exploration"].graph(int(params.get("task") or 0))
        if m == "GET" and path == "/api/exploration/nodes":
            return svcs["exploration"].nodes_page(
                int(params.get("task") or 0), self._pi(params, "page", 0),
                self._pi(params, "size", 30), self._split_csv(params.get("kinds")),
                self._split_csv(params.get("states")), params.get("q"),
                params.get("order") or "desc")
        if m == "GET" and path == "/api/exploration/activity":
            return svcs["exploration"].activity(
                int(params.get("task") or 0), self._pi(params, "since"),
                self._pi(params, "intent"), self._pi(params, "limit", 200))
        if m == "GET" and path == "/api/exploration/activity/history":
            return svcs["exploration"].activity_history(
                int(params.get("task") or 0), params.get("session"),
                self._pi(params, "before"), self._pi(params, "limit", 50))
        if m == "GET" and path == "/api/exploration/tokens":
            return svcs["exploration"].tokens_by_worker(int(params.get("task") or 0))

        # findings
        if m == "GET" and path == "/api/exploration/findings":
            if params.get("task"):
                data = svcs["exploration"].findings(task_id=int(params["task"]),
                                                     page=self._pi(params, "page", 0),
                                                     size=self._pi(params, "size", 50),
                                                     q=params.get("q"),
                                                     severity=params.get("severity"),
                                                     status=params.get("status"))
                return data["items"]
            return svcs["exploration"].findings(page=self._pi(params, "page", 0),
                                                size=self._pi(params, "size", 50),
                                                q=params.get("q"),
                                                severity=params.get("severity"),
                                                status=params.get("status"))
        if m == "GET" and path == "/api/exploration/findings/groups":
            return svcs["exploration"].findings_groups(self._pi(params, "page", 0),
                                                       self._pi(params, "size", 50), params.get("q"))
        if m == "GET" and path == "/api/exploration/findings/asset-tree":
            return svcs["exploration"].findings_asset_tree()
        if m == "GET" and path == "/api/exploration/findings/stats":
            return svcs["exploration"].findings_stats()
        if m == "GET" and path == "/api/exploration/findings/retests/active":
            return svcs["exploration"].active_retests()
        match = self._match_route(path, "^/api/exploration/findings/(?P<fid>[^/]+)$")
        if match:
            if m == "GET":
                dto = svcs["exploration"].finding(match["fid"])
                if not dto:
                    raise self._not_found("漏洞不存在")
                return dto
            if m == "PATCH":
                return svcs["exploration"].update_finding(match["fid"], body)
            if m == "DELETE":
                svcs["exploration"].delete_finding(match["fid"])
                return {"ok": True}
        match = self._match_route(path, "^/api/exploration/findings/(?P<fid>[^/]+)/retests$")
        if match and m == "POST":
            return svcs["exploration"].start_retest(match["fid"], body or {})

        # ---- 资产 / 企业 ------------------------------------------
        if m == "GET" and path == "/api/assets":
            return svcs["assets"].list_assets(
                params.get("type"), self._pi(params, "limit", 100), self._pi(params, "offset", 0),
                params.get("dsl"), self._pi(params, "task_id"), self._pi(params, "company_id"),
                None, params.get("q"))
        if m == "GET" and path == "/api/assets/counts":
            return svcs["assets"].asset_counts(self._pi(params, "task_id"))
        if m == "POST" and path == "/api/assets":
            return svcs["assets"].register_asset(body, self._pi(params, "task_id"))
        if m == "DELETE" and path == "/api/assets":
            ids = body.get("ids", [])
            if isinstance(body.get("ids"), list) and body["ids"] and isinstance(body["ids"][0], dict):
                ids = [i["id"] for i in body["ids"]]
            return {"deleted": svcs["assets"].delete_assets(ids)}

        if m == "GET" and path == "/api/companies":
            return svcs["assets"].companies()
        if m == "POST" and path == "/api/companies":
            return svcs["assets"].create_company(body)
        cid = self._match_int(path, "^/api/companies/(?P<id>[^/]+)$")
        if cid is not None:
            if m == "DELETE":
                svcs["assets"].delete_company(cid, self._pb(params, "delete_assets"))
                return {"ok": True}
            if m == "GET":
                dto = svcs["assets"].company(cid)
                if not dto:
                    raise self._not_found("企业不存在")
                return dto

        # ---- 流量 ------------------------------------------------
        if m == "GET" and path == "/api/traffic":
            return svcs["traffic"].list(
                params.get("host"), params.get("method"), params.get("q"),
                params.get("body"), params.get("path"), self._pi(params, "status"),
                self._pi(params, "resp_min"), self._pi(params, "resp_max"),
                params.get("sort") or "ts", params.get("order") or "desc",
                self._pi(params, "page", 0), self._pi(params, "size", 20))
        if m == "GET" and path == "/api/traffic/hosts":
            return svcs["traffic"].hosts()
        if m == "GET" and path == "/api/traffic/exchange":
            return svcs["traffic"].get_exchange(int(params.get("id") or 0))
        if m == "DELETE" and path == "/api/traffic/all":
            return {"deleted": svcs["traffic"].clear_all()}

        # ---- 对话 ------------------------------------------------
        if m == "GET" and path == "/api/chat/mentions":
            return svcs["conversations"].mentions(params.get("q") or "", self._pi(params, "limit", 20))
        if m == "POST" and path == "/api/chat" and params.get("task"):
            return svcs["conversations"].task_chat(int(params["task"]), body)
        if m == "GET" and path == "/api/conversations":
            return svcs["conversations"].list_conversations(params.get("agent"))
        if m == "POST" and path == "/api/conversations":
            return svcs["conversations"].create_conversation(body)
        cid = self._match_int(path, "^/api/conversations/(?P<id>[^/]+)$")
        if cid is not None:
            if m == "GET":
                dto = svcs["conversations"].get_conversation(cid)
                if not dto:
                    raise self._not_found("会话不存在")
                return dto
            if m == "PATCH":
                return svcs["conversations"].rename_conversation(cid, body)
            if m == "DELETE":
                svcs["conversations"].delete_conversation(cid)
                return {"ok": True}
        cid = self._match_int(path, "^/api/conversations/(?P<id>[^/]+)/messages$")
        if cid is not None:
            if m == "GET":
                return svcs["conversations"].messages(cid, self._pi(params, "since"),
                                                      self._pi(params, "before"),
                                                      self._pi(params, "limit", 50))
            if m == "POST":
                return svcs["conversations"].send_message(cid, body)
        cid = self._match_int(path, "^/api/conversations/(?P<id>[^/]+)/side-questions$")
        if cid is not None and m == "POST":
            return svcs["conversations"].create_side_question("conversation", cid, body)
        # 旁路提问查询
        if path.startswith("/api/side-questions/") and m == "GET":
            req_id = path[len("/api/side-questions/"):]
            return self._side_question_get(svcs, req_id)

        # ---- 拦截审批 --------------------------------------------
        if m == "GET" and path == "/api/intercept/rules":
            return svcs["intercept"].rules()
        if m == "GET" and path == "/api/intercept/pending":
            return svcs["intercept"].pending_list(params.get("status") or "pending",
                                                  self._pi(params, "limit", 100))
        if m == "GET" and path == "/api/intercept/history":
            return svcs["intercept"].history(params.get("q"), params.get("status"),
                                             self._pi(params, "page", 0), self._pi(params, "size", 50))
        # 拦截-任务
        tid = self._match_int(path, "^/api/intercept/task/(?P<id>[^/]+)$")
        if tid is not None and m == "GET":
            return svcs["intercept"].pending_list(params.get("status") or "pending",
                                                  self._pi(params, "limit", 100), tid)
        match = self._match_route(path, "^/api/intercept/pending/(?P<pid>[^/]+)/decide$")
        if match and m == "POST":
            return svcs["intercept"].decide(match["pid"], body)

        # ---- LLM ------------------------------------------------
        if m == "GET" and path == "/api/llm/profiles":
            return svcs["llm"].profiles()
        if m == "POST" and path == "/api/llm/profiles":
            return svcs["llm"].save_profile(body)
        if m == "POST" and path == "/api/llm/profiles/active":
            svcs["llm"].set_active(body.get("id"))
            return {"ok": True}
        pid = self._match_int(path, "^/api/llm/profiles/(?P<id>[^/]+)$")
        if pid is not None and m == "DELETE":
            svcs["llm"].delete_profile(pid)
            return {"ok": True}
        if m == "POST" and path == "/api/llm/test":
            return svcs["llm"].test(body)
        if m == "GET" and path == "/api/llm":
            p = svcs["llm"].active_profile()
            if not p:
                return {}
            return {"api_key": p.get("api_key") or "", "api_key_hint": p.get("api_key_hint") or "",
                    "base_url": p.get("base_url") or "", "model": p.get("model") or "",
                    "proxy": p.get("proxy") or "", "name": p.get("name") or ""}
        if m == "GET" and path == "/api/llm/records":
            return svcs["llmrec"].list(self._pi(params, "page", 0), self._pi(params, "size", 20),
                                       params.get("model"), params.get("session"), self._pi(params, "task"))
        rid_match = self._match_route(path, "^/api/llm/records/(?P<rid>[^/]+)$")
        if rid_match and m == "GET":
            return svcs["llmrec"].get(rid_match["rid"])
        if m == "GET" and path == "/api/tokens/usage":
            return svcs["stats"].token_usage(self._pi(params, "days", 7))
        if m == "GET" and path == "/api/tokens/daily":
            return svcs["stats"].token_daily()
        if m == "GET" and path == "/api/tokens/conversations":
            return svcs["stats"].token_conversations()

        # ---- 设置 / Agent / 工具 / MCP / Skills ----------------
        if m == "GET" and path == "/api/settings":
            return svcs["settings"].all()
        if m == "PUT" and path == "/api/settings":
            svcs["settings"].update(body)
            return {"ok": True}
        if m == "POST" and path == "/api/settings/python/detect":
            import sys
            svcs["settings"].set("python_interpreter", sys.executable)
            return {"path": sys.executable}

        if m == "GET" and path == "/api/agents":
            return svcs["agents"].list_agents()
        if m == "POST" and path == "/api/agents":
            return svcs["agents"].create_agent(body)
        key_match = self._match_route(path, "^/api/agents/(?P<key>[^/]+)$")
        if key_match and m == "GET":
            dto = svcs["agents"].get_agent(key_match["key"])
            if not dto:
                raise self._not_found("agent 不存在")
            return dto
        key_match = self._match_route(path, "^/api/agents/(?P<key>[^/]+)/(?:config|prompt|wrapup)$")
        if key_match and m in ("PUT", "POST"):
            seg = path.rsplit("/", 1)[-1]
            if seg == "config":
                return svcs["agents"].save_config(key_match["key"], body)
            if seg == "prompt":
                return svcs["agents"].save_prompt(key_match["key"], body)
            return svcs["agents"].save_wrapup(key_match["key"], body)

        if m == "GET" and path == "/api/tools":
            return svcs["tools"].list_tools()

        if m == "GET" and path == "/api/mcp":
            return svcs["mcp"].list()
        if m == "POST" and path == "/api/mcp":
            return svcs["mcp"].save(body)
        mid = self._match_int(path, "^/api/mcp/(?P<id>[^/]+)$")
        if mid is not None and m == "DELETE":
            svcs["mcp"].delete(mid)
            return {"ok": True}
        mid = self._match_int(path, "^/api/mcp/(?P<id>[^/]+)/refresh$")
        if mid is not None and m == "POST":
            return svcs["mcp"].refresh(mid)

        if m == "GET" and path == "/api/skills":
            return svcs["skills"].list()
        if m == "POST" and path == "/api/skills":
            return svcs["skills"].create(body)
        if m == "POST" and path == "/api/skills/upload":
            content = files.get("file", b"")
            filename = files.get("filename") or "skill.zip"
            return svcs["skills"].upload(content, filename)
        if m == "GET" and path == "/api/skills/missing":
            return svcs["skills"].missing()
        sm = self._match_route(path, "^/api/skills/(?P<name>[^/]+)/files/(?P<rel>.+)$")
        if sm:
            if m == "GET":
                return svcs["skills"].read_file(sm["name"], sm["rel"])
            if m == "PUT":
                return svcs["skills"].write_file(sm["name"], sm["rel"], body.get("content", ""))
            if m == "DELETE":
                svcs["skills"].delete_file(sm["name"], sm["rel"])
                return {"ok": True}

        # ---- 日志 / 命令 / 工作区 / 同步 / 通知 ----------------
        if m == "GET" and path == "/api/logs/history":
            return {"items": svcs["logsink"].before(self._pi(params, "before") or 2 ** 62,
                                                    self._pi(params, "limit", 200))}
        if m == "GET" and path == "/api/commands/stats":
            return svcs["commands"].stats()
        if m == "GET" and path == "/api/commands":
            return svcs["commands"].list(self._pi(params, "page", 0), self._pi(params, "size", 50),
                                         params.get("tool"), params.get("q"), self._pi(params, "task"))
        if m == "GET" and path == "/api/workspace/list":
            return svcs["workspace"].list(params.get("path") or "")
        if m == "GET" and path == "/api/workspace/read":
            return svcs["workspace"].read(params.get("path") or "")
        if m == "POST" and path == "/api/workspace/write":
            return svcs["workspace"].write(body.get("path", ""), body.get("content", ""))
        if m == "POST" and path == "/api/workspace/mkdir":
            return svcs["workspace"].mkdir(body.get("path", ""))
        if m == "DELETE" and path == "/api/workspace/delete":
            return svcs["workspace"].delete(params.get("path") or "")

        if m == "GET" and path == "/api/sync/scopesentry/status":
            return svcs["sync"].status()
        if m == "POST" and path == "/api/sync/scopesentry/datasource":
            return svcs["sync"].save_datasource(body)
        if m == "POST" and path == "/api/sync/scopesentry/sync":
            return svcs["sync"].sync(body)

        if m == "GET" and path == "/api/notify/meta":
            return svcs["notify"].meta()
        if m == "GET" and path == "/api/notify/channels":
            return svcs["notify"].channels()
        if m == "POST" and path == "/api/notify/channels":
            return svcs["notify"].save_channel(body)
        nid = self._match_int(path, "^/api/notify/channels/(?P<id>[^/]+)$")
        if nid is not None:
            if m == "DELETE":
                svcs["notify"].delete_channel(nid)
                return {"ok": True}
            if m == "PATCH":
                body["id"] = nid
                return svcs["notify"].save_channel(body)
        nid = self._match_int(path, "^/api/notify/channels/(?P<id>[^/]+)/test$")
        if nid is not None and m == "POST":
            return svcs["notify"].test_channel(nid)
        if m == "GET" and path == "/api/notify/deliveries":
            return svcs["notify"].deliveries(self._pi(params, "page", 0),
                                             self._pi(params, "size", 50), params.get("state"))

        # 未知路由
        raise GatewayError(f"未知接口: {method} {path}", 404)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    @staticmethod
    def _match_int(path: str, pattern: str) -> Optional[int]:
        import re
        m = re.match(pattern, path)
        if not m:
            return None
        try:
            return int(m.group("id"))
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _match_route(path: str, pattern: str) -> Optional[dict]:
        import re
        m = re.match(pattern, path)
        return m.groupdict() if m else None

    def _send_worker_message(self, svcs: dict, tid: int, iid: int, body: dict) -> dict:
        exp = svcs["db"].query_one("SELECT exploration_id FROM tasks WHERE id=?", (tid,))
        if not exp:
            raise self._not_found("任务不存在")
        msg = (body.get("message") or "").strip()
        ts = now()
        svcs["db"].execute(
            "INSERT INTO activity(exploration_id, node_id, worker, kind, summary, detail, metadata, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (exp["exploration_id"], iid, "worker", "user", msg, msg, jdumps({}), ts),
        )
        eng = svcs["engines"].get(tid)
        if eng:
            eng.broadcaster.publish(tid, {"seq": svcs["db"].last_id(), "intent_id": iid,
                                          "worker": "worker", "kind": "user", "summary": msg, "ts": ts})
        return {"ok": True}

    @staticmethod
    def _side_question_get(svcs: dict, req_id: str) -> dict:
        r = svcs["db"].query_one("SELECT * FROM side_question_requests WHERE id=?", (req_id,))
        if not r:
            raise GatewayError("请求不存在", 404)
        d = dict(r)
        d["model"] = jloads(d["model"], {}) if isinstance(d.get("model"), str) else d.get("model")
        return d

    # ------------------------------------------------------------------
    # 流式订阅（替代 SSE）：返回进程内订阅应答
    # ------------------------------------------------------------------
    def stream_snapshot(self, path: str, params: Optional[dict] = None) -> Any:
        """activity / logs 流的初始回放快照。"""
        params = params or {}
        if path == "/api/exploration/activity/stream":
            svc = self.svcs["exploration"]
            return svc.activity(int(params.get("task") or 0), self._pi(params, "since"),
                                self._pi(params, "intent"), 500)
        if path == "/api/logs/stream":
            return {"items": self.svcs["logsink"].history(self._pi(params, "since", 0) or 0)}
        return {"items": []}

    def stream_subscribe(self, path: str, params: Optional[dict] = None):
        """返回 (queue, close) 供 UI 线程轮询；close() 负责退订。"""
        params = params or {}
        if path == "/api/exploration/activity/stream":
            task_id = int(params.get("task") or 0)
            sub = self.svcs["broadcast"].subscribe(task_id)

            def _close():
                self.svcs["broadcast"].unsubscribe(task_id, sub)

            return sub.queue, _close
        if path == "/api/logs/stream":
            import queue as _q
            q = _q.Queue()
            self.svcs["logsink"].subscribe(q)

            def _close():
                self.svcs["logsink"].unsubscribe(q)

            return q, _close
        raise GatewayError(f"未知流: {path}", 404)
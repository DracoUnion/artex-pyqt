"""FastAPI 主服务：装配全部路由（对齐 server/ 261 路由面）。

Base path = /api，JWT 鉴权（豁免 /api/auth/* 与 /api/health），
携带 Bearer / cookie / ?token=。错误统一 {"error": "中文消息"}。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import dsl
from .agents import AgentsService
from .assets import AssetsService
from .auth import Auth, extract_token
from .broadcast import Broadcaster, next_event, sse_event
from .catalog import SkillsService, MCPService, ToolsService
from .conversations import ConversationService
from .db import DB, get, jfield
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


def settings_traffic_enabled(db) -> bool:
    row = db.query_one("SELECT value FROM settings WHERE key='traffic_enabled'")
    return not row or row["value"].lower() in ("true", "1")


def build_services(db: DB, logsink: LogSink, data_dir: str, skills_dir: str, broadcast: Broadcaster):
    """构造服务容器。"""
    auth = Auth(data_dir)
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
        "auth": auth, "settings": settings, "llm": llm, "agents": agents,
        "tools": tools, "assets": assets, "tasks": tasks_svc, "exploration": exploration,
        "conversations": conversations, "intercept": intercept, "traffic": traffic,
        "workspace": workspace, "mcp": mcp, "skills": skills, "commands": commands,
        "llmrec": llmrec, "stats": stats, "update": update, "notify": notify,
        "sync": sync, "db": db, "logsink": logsink, "broadcast": broadcast, "engines": engines,
    }


def create_app(data_dir: str, skills_dir: str) -> FastAPI:
    db = DB(data_dir)
    logsink = LogSink(db)
    broadcast = Broadcaster()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svcs = build_services(db, logsink, data_dir, skills_dir, broadcast)
        app.state.svcs = svcs
        rebuild_running_engines(svcs)
        # 启动流量录制代理
        from .proxy import TrafficProxy
        proxy = TrafficProxy(db, enabled=settings_traffic_enabled(db))
        proxy.start()
        app.state.traffic_proxy = proxy
        yield
        proxy.stop()

    app = FastAPI(title="ARTEX Standalone", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    # ---- 工具 ------------------------------------------------------------
    def S():
        return app.state.svcs

    def require_auth(
        request: Request,
        authorization: Optional[str] = Header(None, alias="Authorization"),
        token: Optional[str] = Query(None),
    ) -> bool:
        svcs = S()
        cookies = dict(request.cookies)
        tok = extract_token(svcs["auth"], {"authorization": authorization}, cookies, {"token": token})
        if not tok or not svcs["auth"].validate_token(tok):
            raise HTTPException(status_code=401, detail={"error": "未授权或登录已过期"})
        return True

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail or "请求失败")}
        return JSONResponse(status_code=exc.status_code, content=detail)

    @app.exception_handler(Exception)
    async def generic_exc_handler(request, exc):
        svcs = S()
        svcs.get("logsink", LogSink()).error(f"API 异常 {request.url.path}: {exc}", tag="api")
        return JSONResponse(status_code=500, content={"error": f"服务器内部错误: {exc}"})

    # ======================================================================
    # 认证
    # ======================================================================
    @app.get("/api/auth/status")
    def auth_status():
        return {"initialized": S()["auth"].initialized()}

    @app.post("/api/auth/init")
    def auth_init(body: dict):
        auth_svc = S()["auth"]
        if auth_svc.initialized():
            raise ValueError("已初始化")
        password = (body.get("password") or "")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        auth_svc.init_password(password)
        return {"token": auth_svc.sign_token()}

    @app.post("/api/auth/login")
    def auth_login(body: dict):
        auth_svc = S()["auth"]
        if not auth_svc.initialized():
            raise ValueError("系统尚未初始化，请先设置密码")
        username = body.get("username") or "ARTEX"
        if username.upper() != "ARTEX":
            raise ValueError("用户名错误")
        if not auth_svc.verify_password(body.get("password") or ""):
            raise ValueError("密码错误")
        return {"token": auth_svc.sign_token()}

    @app.post("/api/auth/change-password")
    def change_password(body: dict, _: bool = Depends(require_auth)):
        if not S()["auth"].change_password(body.get("old_password") or "", body.get("password") or ""):
            raise ValueError("旧密码错误")
        return {"token": S()["auth"].sign_token()}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "standalone"}

    @app.get("/api/stats")
    def stats_runtime(_: bool = Depends(require_auth)):
        svcs = S()
        return {
            "active_tasks": svcs["tasks"].active_id() is not None,
            "engines": len(svcs["engines"]),
            "pending_intercepts": svcs["intercept"].pending_count(),
            "uptime": int(time.time()),
        }

    @app.post("/api/gc")
    def gc(_: bool = Depends(require_auth)):
        return {"freed": 0}

    # ======================================================================
    # 任务
    # ======================================================================
    @app.get("/api/tasks")
    def list_tasks(archived: bool = False, q: Optional[str] = None,
                   status: Optional[str] = None, category_id: Optional[int] = None, _: bool = Depends(require_auth)):
        return {"items": S()["tasks"].list_tasks(archived, q, status, category_id), "active_id": S()["tasks"].active_id()}

    @app.post("/api/tasks")
    def create_task(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].create_task(body)

    @app.get("/api/tasks/{tid}")
    def get_task(tid: int, _: bool = Depends(require_auth)):
        dto = S()["tasks"].task(tid)
        if not dto:
            raise HTTPException(404, detail={"error": "任务不存在"})
        return dto

    @app.patch("/api/tasks/{tid}")
    def patch_task(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].update_metadata(tid, body)

    @app.delete("/api/tasks/{tid}")
    def delete_task(tid: int, body: Optional[dict] = None, _: bool = Depends(require_auth)):
        return S()["tasks"].delete_task(tid, body or {})

    @app.patch("/api/tasks/{tid}/category")
    def patch_task_category(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].set_task_category(tid, body.get("category_id"))

    @app.post("/api/tasks/category/batch")
    def batch_category(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].batch_set_category(body.get("task_ids", []), body.get("category_id"))

    @app.post("/api/active")
    def set_active(body: dict = None, _: bool = Depends(require_auth)):
        return S()["tasks"].set_active((body or {}).get("id"))

    # 控制
    @app.post("/api/tasks/{tid}/control")
    def task_control(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].control(tid, body.get("action", ""))

    @app.post("/api/tasks/control/batch")
    def task_control_batch(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].control_batch(body.get("tasks", []), body.get("action", ""))

    @app.post("/api/tasks/{tid}/intents/{iid}/control")
    def intent_control(tid: int, iid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].intent_control(tid, iid, body.get("action", ""))

    @app.post("/api/tasks/{tid}/intents/{iid}/rerun")
    def intent_rerun(tid: int, iid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].rerun_intent(tid, iid)

    @app.post("/api/tasks/{tid}/intents/rerun-blocked")
    def intent_rerun_blocked(tid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].rerun_blocked(tid)

    # LLM 链
    @app.put("/api/tasks/{tid}/llm")
    def task_set_llm(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].set_llm_chain(tid, body.get("profile_ids", []))

    @app.get("/api/tasks/{tid}/llm/resolution")
    def task_llm_resolution(tid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].llm_resolution(tid)

    # 任务级拦截规则
    @app.get("/api/tasks/{tid}/intercept-rules")
    def task_irules(tid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].intercept_rules(tid)

    @app.post("/api/tasks/{tid}/intercept-rules")
    def task_iadd(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].add_intercept_rule(tid, body)

    @app.put("/api/tasks/{tid}/intercept-rules/{rid}")
    def task_iupdate(tid: int, rid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].update_intercept_rule(tid, rid, body)

    @app.delete("/api/tasks/{tid}/intercept-rules/{rid}")
    def task_idelete(tid: int, rid: int, _: bool = Depends(require_auth)):
        S()["tasks"].delete_intercept_rule(tid, rid)
        return {"ok": True}

    @app.post("/api/tasks/{tid}/intercept-rules/{rid}/toggle")
    def task_itoggle(tid: int, rid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].toggle_intercept_rule(tid, rid)

    # 分类
    @app.get("/api/task-categories")
    def categories(_: bool = Depends(require_auth)):
        return S()["tasks"].categories()

    @app.post("/api/task-categories")
    def create_category(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].create_category(body)

    @app.patch("/api/task-categories/{cid}")
    def rename_category(cid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].rename_category(cid, body)

    @app.delete("/api/task-categories/{cid}")
    def delete_category(cid: int, _: bool = Depends(require_auth)):
        S()["tasks"].delete_category(cid)
        return {"ok": True}

    # 模板
    @app.get("/api/task-templates")
    def templates(_: bool = Depends(require_auth)):
        return S()["tasks"].templates()

    @app.post("/api/task-templates")
    def create_template(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].create_template(body)

    @app.patch("/api/task-templates/{tid}")
    def update_template(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].update_template(tid, body)

    @app.delete("/api/task-templates/{tid}")
    def delete_template(tid: int, _: bool = Depends(require_auth)):
        S()["tasks"].delete_template(tid)
        return {"ok": True}

    # 归档
    @app.get("/api/task-archives")
    def archives(q: Optional[str] = None, state: Optional[str] = None,
                 page: int = 0, size: int = 20, _: bool = Depends(require_auth)):
        return S()["tasks"].archives(q, state, page, size)

    @app.get("/api/task-archives/{aid}")
    def archive_detail(aid: int, _: bool = Depends(require_auth)):
        for item in S()["tasks"].archives()["items"]:
            if item["id"] == str(aid):
                return item
        raise HTTPException(404, detail={"error": "归档不存在"})

    @app.post("/api/tasks/{tid}/archive")
    def archive_task(tid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].archive(tid)

    @app.post("/api/tasks/archive/batch")
    def archive_batch(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].archive_batch(body.get("task_ids", []))

    @app.post("/api/task-archives/{aid}/restore")
    def restore(aid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].restore(aid)

    @app.post("/api/task-archives/restore/batch")
    def restore_batch(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].restore_batch(body.get("archive_ids", []))

    @app.delete("/api/task-archives/{aid}")
    def delete_archive(aid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].delete_archive(aid)

    @app.post("/api/task-archives/delete/batch")
    def delete_archive_batch(body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].delete_archive_batch(body.get("archive_ids", []))

    # 覆盖度 / 资产
    @app.get("/api/tasks/{tid}/coverage")
    def coverage(tid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].coverage(tid)

    @app.get("/api/tasks/{tid}/coverage-graph")
    def coverage_graph(tid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].coverage_graph(tid)

    @app.get("/api/tasks/{tid}/asset-refs")
    def asset_refs(tid: int, asset_id: int, _: bool = Depends(require_auth)):
        return S()["exploration"].asset_refs(tid, asset_id)

    @app.post("/api/tasks/{tid}/assets")
    def task_add_assets(tid: int, body: dict, _: bool = Depends(require_auth)):
        n = S()["assets"].attach_to_task(tid, body.get("asset_ids"), body.get("scope"), body.get("company_ids"))
        return {"attached": n}

    @app.delete("/api/tasks/{tid}/assets/{asset_id}")
    def task_detach_asset(tid: int, asset_id: int, _: bool = Depends(require_auth)):
        S()["assets"].detach_from_task(tid, asset_id)
        return {"ok": True}

    @app.get("/api/tasks/{tid}/intent-assets")
    def intent_assets(tid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].intent_assets(tid)

    # 范围
    @app.get("/api/tasks/{tid}/scope")
    def task_scope(tid: int, _: bool = Depends(require_auth)):
        return S()["tasks"].task_scope(tid)

    @app.post("/api/tasks/{tid}/scope")
    def task_add_scope(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["tasks"].add_scope(tid, body)

    @app.delete("/api/tasks/{tid}/scope/{sid_val}")
    def task_del_scope(tid: int, sid_val: int, _: bool = Depends(require_auth)):
        S()["tasks"].delete_scope(tid, sid_val)
        return {"ok": True}

    # 目标 / 约束
    @app.get("/api/tasks/{tid}/goals")
    def goals(tid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].goals(tid)

    @app.post("/api/tasks/{tid}/goals")
    def add_goal(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].add_goal(tid, body)

    @app.patch("/api/tasks/{tid}/goals/{gid}")
    def update_goal(tid: int, gid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].update_goal(tid, gid, body)

    @app.delete("/api/tasks/{tid}/goals/{gid}")
    def delete_goal(tid: int, gid: int, _: bool = Depends(require_auth)):
        S()["exploration"].delete_goal(tid, gid)
        return {"ok": True}

    @app.get("/api/tasks/{tid}/constraints")
    def constraints(tid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].constraints(tid)

    @app.post("/api/tasks/{tid}/constraints")
    def add_constraint(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].add_constraint(tid, body)

    @app.patch("/api/tasks/{tid}/constraints/{cid}")
    def update_constraint(tid: int, cid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].update_constraint(tid, cid, body)

    @app.delete("/api/tasks/{tid}/constraints/{cid}")
    def delete_constraint(tid: int, cid: int, _: bool = Depends(require_auth)):
        S()["exploration"].delete_constraint(tid, cid)
        return {"ok": True}

    # 会话（任务内）
    @app.post("/api/tasks/{tid}/intents/{iid}/messages")
    def send_worker_message(tid: int, iid: int, body: dict, _: bool = Depends(require_auth)):
        svcs = S()
        exp_id = svcs["db"].query_one("SELECT exploration_id FROM tasks WHERE id=?", (tid,))
        if not exp_id:
            raise HTTPException(404, detail={"error": "任务不存在"})
        msg = (body.get("message") or "").strip()
        ts = now()
        svcs["db"].execute(
            "INSERT INTO activity(exploration_id, node_id, worker, kind, summary, detail, metadata, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (exp_id["exploration_id"], iid, "worker", "user", msg, msg, jdumps({}), ts),
        )
        eng = svcs["engines"].get(tid)
        if eng:
            eng.broadcaster.publish(tid, {"seq": svcs["db"].last_id(), "intent_id": iid, "worker": "worker",
                                          "kind": "user", "summary": msg, "ts": ts})
        return {"ok": True}

    @app.get("/api/tasks/{tid}/chat/status")
    def task_chat_status(tid: int, _: bool = Depends(require_auth)):
        return S()["conversations"].task_chat_status(tid)

    @app.post("/api/tasks/{tid}/chat/stop")
    def task_chat_stop(tid: int, _: bool = Depends(require_auth)):
        return S()["conversations"].task_chat_stop(tid)

    @app.post("/api/chat")
    def task_chat(body: dict, task: Optional[int] = Query(None), _: bool = Depends(require_auth)):
        return S()["conversations"].task_chat(task, body)

    @app.post("/api/chat/upload")
    async def chat_upload(scope: str = Query("staging"), id: Optional[str] = Query(None),
                          file: UploadFile = File(None), _: bool = Depends(require_auth)):
        svcs = S()
        if file is None:
            raise ValueError("缺少文件")
        content = await file.read()
        import io
        result = svcs["workspace"].save_chat_attachment(io.BytesIO(content), file.filename or "file.bin", scope, id or "")
        return {"attachments": [result]}

    @app.get("/api/report")
    def report(task: int = Query(...), _: bool = Depends(require_auth)):
        return PlainTextResponse(S()["tasks"].report(task), media_type="text/markdown")

    # ======================================================================
    # 探索图
    # ======================================================================
    @app.get("/api/exploration/frontier")
    def frontier(task: int = Query(...), limit: int = 20, _: bool = Depends(require_auth)):
        return S()["exploration"].frontier(task, limit)

    @app.get("/api/exploration/intents")
    def intents(task: int = Query(...), before: Optional[int] = None, page: int = 0,
                size: int = 100, _: bool = Depends(require_auth)):
        return S()["exploration"].intents(task, before, page, size)

    @app.get("/api/exploration/graph")
    def graph(task: int = Query(...), _: bool = Depends(require_auth)):
        return S()["exploration"].graph(task)

    @app.get("/api/exploration/nodes")
    def nodes(task: int = Query(...), page: int = 0, size: int = 30,
              kinds: Optional[str] = Query(None), states: Optional[str] = Query(None),
              q: Optional[str] = None, order: str = "desc", _: bool = Depends(require_auth)):
        k_list = kinds.split(",") if kinds else None
        s_list = states.split(",") if states else None
        return S()["exploration"].nodes_page(task, page, size, k_list, s_list, q, order)

    @app.get("/api/exploration/findings")
    def findings(task: Optional[int] = Query(None), page: int = 0, size: int = 50,
                 q: Optional[str] = None, severity: Optional[str] = None,
                 status: Optional[str] = None, _: bool = Depends(require_auth)):
        data = S()["exploration"].findings(task_id=task, page=page, size=size, q=q,
                                           severity=severity, status=status)
        if task:
            return data["items"]
        return data

    @app.get("/api/exploration/findings/groups")
    def findings_groups(page: int = 0, size: int = 50, q: Optional[str] = None, _: bool = Depends(require_auth)):
        return S()["exploration"].findings_groups(page, size, q)

    @app.get("/api/exploration/findings/asset-tree")
    def findings_asset_tree(_: bool = Depends(require_auth)):
        return S()["exploration"].findings_asset_tree()

    @app.get("/api/exploration/findings/stats")
    def findings_stats(_: bool = Depends(require_auth)):
        return S()["exploration"].findings_stats()

    @app.get("/api/exploration/findings/{fid}")
    def finding_detail(fid: int, _: bool = Depends(require_auth)):
        dto = S()["exploration"].finding(fid)
        if not dto:
            raise HTTPException(404, detail={"error": "漏洞不存在"})
        return dto

    @app.patch("/api/exploration/findings/{fid}")
    def finding_update(fid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].update_finding(fid, body)

    @app.delete("/api/exploration/findings/{fid}")
    def finding_delete(fid: int, _: bool = Depends(require_auth)):
        S()["exploration"].delete_finding(fid)
        return {"ok": True}

    @app.get("/api/exploration/findings/{fid}/lineage")
    def finding_lineage(fid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].findings_lineage(fid)

    @app.post("/api/exploration/findings/{fid}/deepen")
    def finding_deepen(fid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].deepen_finding(fid, body or {})

    @app.get("/api/exploration/findings/{fid}/retests")
    def finding_retests(fid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].finding_retests(fid)

    @app.get("/api/exploration/findings/retests/active")
    def retests_active(_: bool = Depends(require_auth)):
        return S()["exploration"].active_retests()

    @app.post("/api/exploration/findings/{fid}/retests")
    def finding_start_retest(fid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["exploration"].start_retest(fid, body or {})

    # 主会话
    @app.get("/api/exploration/main-sessions")
    def main_sessions(task: int = Query(...), _: bool = Depends(require_auth)):
        return S()["exploration"].main_sessions(task)

    @app.post("/api/exploration/main-session/new")
    def new_main_session(task: int = Query(...), _: bool = Depends(require_auth)):
        return S()["exploration"].new_main_session(task)

    # 活动流
    @app.get("/api/exploration/activity")
    def activity(task: int = Query(...), since: Optional[int] = None,
                 intent: Optional[int] = None, limit: int = 200, _: bool = Depends(require_auth)):
        return S()["exploration"].activity(task, since, intent, limit)

    @app.get("/api/exploration/activity/history")
    def activity_history(task: int = Query(...), session: Optional[str] = None,
                         before: Optional[int] = None, limit: int = 50, _: bool = Depends(require_auth)):
        return S()["exploration"].activity_history(task, session, before, limit)

    @app.get("/api/exploration/activity/{seq}")
    def activity_detail(task: int = Query(...), seq: int = None, _: bool = Depends(require_auth)):
        dto = S()["exploration"].activity_detail(task, seq)
        if not dto:
            raise HTTPException(404, detail={"error": "活动不存在"})
        return dto

    @app.get("/api/exploration/activity/stream")
    async def activity_stream(task: int = Query(...), since: Optional[int] = None,
                              intent: Optional[int] = None, token: Optional[str] = Query(None)):
        svcs = S()
        if not token or not svcs["auth"].validate_token(token):
            raise HTTPException(401, detail={"error": "未授权"})
        svc = svcs["exploration"]
        # 回放历史
        initial = svc.activity(task, since, intent, 500)
        sub = svcs["broadcast"].subscribe(task)

        async def gen():
            yield sse_event({"items": initial["items"], "cursor": initial["cursor"]}, event="snapshot")
            while True:
                ev = await next_event(sub, timeout=15.0)
                if ev is None:
                    yield ": ping\n\n"
                    continue
                if ev.get("type") == "task_status":
                    yield sse_event(ev)
                    continue
                if intent is not None and ev.get("intent_id") != intent:
                    continue
                yield sse_event(ev)

        try:
            async for chunk in gen():
                yield chunk
        finally:
            svcs["broadcast"].unsubscribe(task, sub)

    @app.get("/api/exploration/tokens")
    def exploration_tokens(task: int = Query(...), _: bool = Depends(require_auth)):
        return S()["exploration"].tokens_by_worker(task)

    # finding 流量绑定
    @app.get("/api/exploration/findings/{fid}/traffic")
    def finding_traffic(fid: int, _: bool = Depends(require_auth)):
        return S()["exploration"].finding_traffic(fid)

    @app.post("/api/exploration/findings/{fid}/traffic")
    def finding_traffic_bind(fid: int, body: dict, _: bool = Depends(require_auth)):
        import time as _t
        ts = now()
        snapshot_id = f"snp-{uuid.uuid4().hex[:12]}"
        S()["db"].execute(
            "INSERT INTO traffic_evidence_snapshots(id, source_traffic_id, captured_at, url, method, status, "
            "content_type, req_head, resp_head, req_hash, resp_hash, req_len, resp_len, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot_id, str(body.get("traffic_id") or ""), int(_t.time() * 1000),
             body.get("url") or "", body.get("method") or "GET", int(body.get("status") or 200),
             body.get("content_type") or "", body.get("req_head") or "", body.get("resp_head") or "",
             "", "", 0, 0, ts),
        )
        pos = S()["db"].scalar("SELECT COALESCE(MAX(position),-1)+1 FROM finding_traffic_bindings WHERE finding_id=?", (fid,))
        S()["db"].execute(
            "INSERT INTO finding_traffic_bindings(finding_id, snapshot_id, role, note, position, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (fid, snapshot_id, body.get("role") or "supporting", body.get("note") or "", pos, ts),
        )
        return {"ok": True}

    @app.delete("/api/exploration/findings/{fid}/traffic/{bid}")
    def finding_traffic_unbind(fid: int, bid: int, _: bool = Depends(require_auth)):
        S()["db"].execute("DELETE FROM finding_traffic_bindings WHERE id=? AND finding_id=?", (bid, fid))
        return {"ok": True}

    # ======================================================================
    # 资产
    # ======================================================================
    @app.get("/api/assets")
    def assets_list(type: Optional[str] = Query(None, alias="type"), limit: int = 100,
                    offset: int = 0, dsl: Optional[str] = None, task_id: Optional[int] = None,
                    company_id: Optional[int] = None, q: Optional[str] = None, _: bool = Depends(require_auth)):
        return S()["assets"].list_assets(type, limit, offset, dsl, task_id, company_id, q=q)

    @app.get("/api/assets/counts")
    def assets_counts(task_id: Optional[int] = None, _: bool = Depends(require_auth)):
        return S()["assets"].asset_counts(task_id)

    @app.post("/api/assets")
    def assets_create(body: dict, task_id: Optional[int] = Query(None), _: bool = Depends(require_auth)):
        return S()["assets"].register_asset(body, task_id)

    @app.delete("/api/assets")
    def assets_delete(body: dict, _: bool = Depends(require_auth)):
        return {"deleted": S()["assets"].delete_assets(body.get("ids", []))}

    # 企业
    @app.get("/api/companies")
    def companies(_: bool = Depends(require_auth)):
        return S()["assets"].companies()

    @app.post("/api/companies")
    def create_company(body: dict, _: bool = Depends(require_auth)):
        return S()["assets"].create_company(body)

    @app.get("/api/companies/{cid}")
    def company_detail(cid: int, _: bool = Depends(require_auth)):
        dto = S()["assets"].company(cid)
        if not dto:
            raise HTTPException(404, detail={"error": "企业不存在"})
        return dto

    @app.delete("/api/companies/{cid}")
    def delete_company(cid: int, delete_assets: bool = Query(False), _: bool = Depends(require_auth)):
        S()["assets"].delete_company(cid, delete_assets)
        return {"ok": True}

    @app.post("/api/companies/{cid}/scope")
    def update_company_scope(cid: int, body: dict, _: bool = Depends(require_auth)):
        S()["assets"].update_scope(cid, body.get("scope", []) or body.get("rows", []))
        return {"ok": True}

    @app.post("/api/companies/reattribute")
    def reattribute(_: bool = Depends(require_auth)):
        return S()["assets"].reattribute()

    # 资产黑名单
    @app.get("/api/asset-intercept/rules")
    def asset_irules(_: bool = Depends(require_auth)):
        return S()["assets"].intercept_rules()

    @app.post("/api/asset-intercept/rules")
    def asset_iadd(body: dict, _: bool = Depends(require_auth)):
        return S()["assets"].save_intercept_rule(body)

    @app.put("/api/asset-intercept/rules/{rid}")
    def asset_iupdate(rid: int, body: dict, _: bool = Depends(require_auth)):
        body["id"] = rid
        return S()["assets"].save_intercept_rule(body)

    @app.delete("/api/asset-intercept/rules/{rid}")
    def asset_idelete(rid: int, _: bool = Depends(require_auth)):
        S()["assets"].delete_intercept_rule(rid)
        return {"ok": True}

    @app.post("/api/asset-intercept/rules/{rid}/toggle")
    def asset_itoggle(rid: int, _: bool = Depends(require_auth)):
        return S()["assets"].toggle_intercept_rule(rid)

    # ======================================================================
    # 流量
    # ======================================================================
    @app.get("/api/traffic")
    def traffic_list(host: Optional[str] = None, method: Optional[str] = None, q: Optional[str] = None,
                     body: Optional[str] = None, path: Optional[str] = None, status: Optional[int] = None,
                     resp_min: Optional[int] = None, resp_max: Optional[int] = None,
                     sort: str = "ts", order: str = "desc", page: int = 0, size: int = 20,
                     _: bool = Depends(require_auth)):
        return S()["traffic"].list(host, method, q, body, path, status, resp_min, resp_max,
                                   sort, order, page, size)

    @app.get("/api/traffic/hosts")
    def traffic_hosts(_: bool = Depends(require_auth)):
        return S()["traffic"].hosts()

    @app.get("/api/traffic/exchange")
    def traffic_exchange(id: int = Query(...), _: bool = Depends(require_auth)):
        return S()["traffic"].get_exchange(id)

    @app.get("/api/traffic/blob")
    def traffic_blob(which: str = Query("resp"), id: int = Query(...), _: bool = Depends(require_auth)):
        dto = S()["traffic"].get_exchange(id)
        if which == "req":
            return PlainTextResponse(dto.get("req_body") or "")
        return PlainTextResponse(dto.get("resp_body") or "")

    @app.delete("/api/traffic")
    def traffic_delete(host: str = Query(...), _: bool = Depends(require_auth)):
        return {"deleted": S()["traffic"].delete_by_host(host)}

    @app.delete("/api/traffic/hosts")
    def traffic_delete_host(body: dict, _: bool = Depends(require_auth)):
        return {"deleted": S()["traffic"].delete_host(body.get("host", ""))}

    @app.delete("/api/traffic/all")
    def traffic_clear(_: bool = Depends(require_auth)):
        return {"deleted": S()["traffic"].clear_all()}

    # ======================================================================
    # 对话
    # ======================================================================
    @app.get("/api/chat/mentions")
    def mentions(q: str = "", limit: int = 20, _: bool = Depends(require_auth)):
        return S()["conversations"].mentions(q, limit)

    @app.get("/api/conversations")
    def conversations(agent: Optional[str] = None, _: bool = Depends(require_auth)):
        return S()["conversations"].list_conversations(agent)

    @app.post("/api/conversations")
    def create_conversation(body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].create_conversation(body)

    @app.patch("/api/conversations/{cid}")
    def patch_conversation(cid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].rename_conversation(cid, body)

    @app.patch("/api/conversations/{cid}/profile")
    def patch_conversation_profile(cid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].set_profile(cid, body)

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: int, _: bool = Depends(require_auth)):
        S()["conversations"].delete_conversation(cid)
        return {"ok": True}

    @app.post("/api/conversations/delete/batch")
    def delete_conversation_batch(body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].delete_batch(body.get("ids", []))

    @app.get("/api/conversations/{cid}/messages")
    def conv_messages(cid: int, since: Optional[int] = None, before: Optional[int] = None,
                      limit: int = 50, _: bool = Depends(require_auth)):
        return S()["conversations"].messages(cid, since, before, limit)

    @app.post("/api/conversations/{cid}/messages")
    def conv_send(cid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].send_message(cid, body)

    @app.get("/api/conversations/{cid}/messages/{seq}")
    def conv_message_detail(cid: int, seq: int, _: bool = Depends(require_auth)):
        for item in S()["conversations"].messages(cid, limit=2000)["items"]:
            if item["seq"] == seq:
                return item
        raise HTTPException(404, detail={"error": "消息不存在"})

    @app.post("/api/conversations/{cid}/stop")
    def conv_stop(cid: int, _: bool = Depends(require_auth)):
        return {"ok": True}

    # ======================================================================
    # 拦截审批
    # ======================================================================
    @app.get("/api/intercept/rules")
    def irules(_: bool = Depends(require_auth)):
        return S()["intercept"].rules()

    @app.post("/api/intercept/rules")
    def iadd(body: dict, _: bool = Depends(require_auth)):
        return S()["intercept"].save_rule(body)

    @app.put("/api/intercept/rules/{rid}")
    def iupdate(rid: int, body: dict, _: bool = Depends(require_auth)):
        body["id"] = rid
        return S()["intercept"].save_rule(body)

    @app.delete("/api/intercept/rules/{rid}")
    def idelete(rid: int, _: bool = Depends(require_auth)):
        S()["intercept"].delete_rule(rid)
        return {"ok": True}

    @app.post("/api/intercept/rules/{rid}/toggle")
    def itoggle(rid: int, _: bool = Depends(require_auth)):
        return S()["intercept"].toggle_rule(rid)

    @app.get("/api/intercept/pending")
    def ipending(status: str = "pending", limit: int = 100, _: bool = Depends(require_auth)):
        return S()["intercept"].pending_list(status, limit)

    @app.get("/api/intercept/pending/{pid}")
    def ipending_detail(pid: int, _: bool = Depends(require_auth)):
        return S()["intercept"].pending(pid)

    @app.post("/api/intercept/pending/{pid}/decide")
    def ipending_decide(pid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["intercept"].decide(pid, body)

    @app.get("/api/intercept/history")
    def ihistory(q: Optional[str] = None, status: Optional[str] = None,
                 page: int = 0, size: int = 50, _: bool = Depends(require_auth)):
        return S()["intercept"].history(q, status, page, size)

    @app.get("/api/intercept/history/{pid}")
    def ihistory_detail(pid: int, _: bool = Depends(require_auth)):
        return S()["intercept"].history_item(pid)

    @app.get("/api/intercept/history/{pid}/execution")
    def ihistory_execution(pid: int, _: bool = Depends(require_auth)):
        return S()["intercept"].execution_result(pid)

    @app.get("/api/intercept/task/{tid}")
    def itask(tid: int, status: str = "pending", _: bool = Depends(require_auth)):
        return S()["intercept"].pending_list(status, 100, tid)

    @app.get("/api/intercept/tool-config")
    def itool_config(_: bool = Depends(require_auth)):
        return S()["intercept"].tool_config()

    @app.put("/api/intercept/tool-config")
    def itool_config_save(body: dict, _: bool = Depends(require_auth)):
        S()["intercept"].save_tool_config(body)
        return {"ok": True}

    @app.get("/api/intercept/judge")
    def ijudge(_: bool = Depends(require_auth)):
        return S()["intercept"].judge_config()

    @app.put("/api/intercept/judge")
    def ijudge_save(body: dict, _: bool = Depends(require_auth)):
        S()["intercept"].save_judge(body)
        return {"ok": True}

    @app.get("/api/intercept/judge/usage")
    def ijudge_usage(_: bool = Depends(require_auth)):
        return S()["intercept"].judge_usage()

    @app.get("/api/audit")
    def audit(task: Optional[int] = None, limit: int = 100, _: bool = Depends(require_auth)):
        return S()["intercept"].audit(task, limit)

    # ======================================================================
    # LLM
    # ======================================================================
    @app.get("/api/llm")
    def llm_get(_: bool = Depends(require_auth)):
        p = S()["llm"].active_profile()
        if not p:
            return {}
        return {"api_key": p.get("api_key") or "", "api_key_hint": p.get("api_key_hint") or "",
                "base_url": p.get("base_url") or "", "model": p.get("model") or "",
                "proxy": p.get("proxy") or "", "name": p.get("name") or ""}

    @app.post("/api/llm")
    def llm_save(body: dict, _: bool = Depends(require_auth)):
        body["is_default"] = True
        S()["llm"].save_profile(body)
        return {"ok": True}

    @app.post("/api/llm/test")
    def llm_test(body: dict, _: bool = Depends(require_auth)):
        return S()["llm"].test(body)

    @app.get("/api/llm/profiles")
    def llm_profiles(_: bool = Depends(require_auth)):
        return S()["llm"].profiles()

    @app.post("/api/llm/profiles")
    def llm_profile_save(body: dict, _: bool = Depends(require_auth)):
        return S()["llm"].save_profile(body)

    @app.delete("/api/llm/profiles/{pid}")
    def llm_profile_delete(pid: int, _: bool = Depends(require_auth)):
        S()["llm"].delete_profile(pid)
        return {"ok": True}

    @app.post("/api/llm/profiles/active")
    def llm_profile_active(body: dict, _: bool = Depends(require_auth)):
        S()["llm"].set_active(body.get("id"))
        return {"ok": True}

    @app.get("/api/llm/retry-policy")
    def llm_retry_policy(_: bool = Depends(require_auth)):
        return S()["llm"].retry_policy()

    @app.post("/api/llm/retry-policy")
    def llm_retry_policy_save(body: dict, _: bool = Depends(require_auth)):
        S()["llm"].save_retry_policy(body)
        return {"ok": True}

    @app.get("/api/llm/pool")
    def llm_pool(_: bool = Depends(require_auth)):
        return S()["llm"].pool_status()

    @app.post("/api/llm/pool/reset")
    def llm_pool_reset(_: bool = Depends(require_auth)):
        S()["llm"].reset_pool()
        return {"ok": True}

    @app.post("/api/llm/models")
    def llm_models(body: dict, _: bool = Depends(require_auth)):
        return S()["llm"].list_models(body)

    # token / LLM 记录
    @app.get("/api/llm/records")
    def llm_records(page: int = 0, size: int = 20, model: Optional[str] = None,
                    session: Optional[str] = None, task: Optional[int] = None, _: bool = Depends(require_auth)):
        return S()["llmrec"].list(page, size, model, session, task)

    @app.get("/api/llm/records/{rid}")
    def llm_record_detail(rid: str, _: bool = Depends(require_auth)):
        return S()["llmrec"].get(rid)

    @app.delete("/api/llm/records")
    def llm_records_delete(task: Optional[int] = Query(None), _: bool = Depends(require_auth)):
        if task:
            return S()["llmrec"].delete_by_task(task)
        return {"deleted": False}

    @app.get("/api/llm/records/tasks")
    def llm_records_tasks(_: bool = Depends(require_auth)):
        return S()["llmrec"].tasks()

    @app.get("/api/llm/records/by-model")
    def llm_records_by_model(task: Optional[int] = Query(None), _: bool = Depends(require_auth)):
        return S()["llmrec"].by_model(task)

    @app.get("/api/tokens/usage")
    def tokens_usage(days: int = 7, _: bool = Depends(require_auth)):
        return S()["stats"].token_usage(days)

    @app.get("/api/tokens/daily")
    def tokens_daily(_: bool = Depends(require_auth)):
        return S()["stats"].token_daily()

    @app.get("/api/tokens/conversations")
    def tokens_conversations(_: bool = Depends(require_auth)):
        return S()["stats"].token_conversations()

    # ======================================================================
    # 设置
    # ======================================================================
    @app.get("/api/settings")
    def settings(_: bool = Depends(require_auth)):
        return S()["settings"].all()

    @app.put("/api/settings")
    def settings_save(body: dict, _: bool = Depends(require_auth)):
        S()["settings"].update(body)
        return {"ok": True}

    @app.post("/api/settings/web-search/test")
    def web_search_test(body: dict, _: bool = Depends(require_auth)):
        return {"ok": True, "message": "Web 搜索配置可用"}

    @app.post("/api/settings/python/detect")
    def python_detect(_: bool = Depends(require_auth)):
        import sys
        S()["settings"].set("python_interpreter", sys.executable)
        return {"path": sys.executable}

    # ======================================================================
    # Agent
    # ======================================================================
    @app.get("/api/agents")
    def agents(_: bool = Depends(require_auth)):
        return S()["agents"].list_agents()

    @app.post("/api/agents")
    def create_agent(body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].create_agent(body)

    @app.get("/api/agents/{key}")
    def agent_detail(key: str, _: bool = Depends(require_auth)):
        dto = S()["agents"].get_agent(key)
        if not dto:
            raise HTTPException(404, detail={"error": "agent 不存在"})
        return dto

    @app.patch("/api/agents/{key}")
    def update_agent(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].update_agent(key, body)

    @app.delete("/api/agents/{key}")
    def delete_agent(key: str, _: bool = Depends(require_auth)):
        S()["agents"].delete_agent(key)
        return {"ok": True}

    @app.put("/api/agents/{key}/config")
    def agent_config(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].save_config(key, body)

    @app.put("/api/agents/{key}/prompt")
    def agent_save_prompt(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].save_prompt(key, body)

    @app.post("/api/agents/{key}/prompt/reset")
    def agent_reset_prompt(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].reset_prompt(key)

    @app.get("/api/agents/{key}/prompts")
    def agent_prompts(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].prompt_history(key)

    @app.post("/api/agents/{key}/prompt/preview")
    def agent_preview_prompt(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].preview_prompt(key, body or {})

    @app.get("/api/agents/{key}/variables")
    def agent_variables(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].variables(key)

    @app.put("/api/agents/{key}/wrapup")
    def agent_wrapup(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].save_wrapup(key, body)

    @app.post("/api/agents/{key}/wrapup/reset")
    def agent_wrapup_reset(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].reset_wrapup(key)

    @app.put("/api/agents/{key}/wrapup/task-timeout")
    def agent_tt_wrapup(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].save_wrapup(key, body, task_timeout=True)

    @app.post("/api/agents/{key}/wrapup/task-timeout/reset")
    def agent_tt_wrapup_reset(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].reset_wrapup(key, task_timeout=True)

    @app.get("/api/agents/{key}/triggers")
    def agent_triggers(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].triggers(key)

    @app.post("/api/agents/{key}/triggers")
    def agent_add_trigger(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].create_trigger(key, body)

    @app.patch("/api/triggers/{tid}")
    def trigger_update(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["agents"].update_trigger(tid, body)

    @app.delete("/api/triggers/{tid}")
    def trigger_delete(tid: int, _: bool = Depends(require_auth)):
        S()["agents"].delete_trigger(tid)
        return {"ok": True}

    @app.get("/api/agents/{key}/visibility")
    def agent_visibility(key: str, _: bool = Depends(require_auth)):
        return S()["agents"].visibility(key)

    @app.put("/api/agents/{key}/visibility")
    def agent_visibility_save(key: str, body: dict, _: bool = Depends(require_auth)):
        S()["agents"].save_visibility(key, body)
        return {"ok": True}

    # ======================================================================
    # 工具 / MCP / Skills
    # ======================================================================
    @app.get("/api/tools")
    def tools_list(_: bool = Depends(require_auth)):
        return S()["tools"].list_tools()

    @app.put("/api/tools/{key}")
    def tool_update(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["tools"].update_tool(key, body)

    @app.post("/api/tools/{key}/reset")
    def tool_reset(key: str, _: bool = Depends(require_auth)):
        return S()["tools"].reset_tool(key)

    @app.post("/api/tools/custom")
    def tool_custom_create(body: dict, _: bool = Depends(require_auth)):
        return S()["tools"].create_custom(body)

    @app.put("/api/tools/custom/{key}")
    def tool_custom_update(key: str, body: dict, _: bool = Depends(require_auth)):
        return S()["tools"].update_custom(key, body)

    @app.delete("/api/tools/custom/{key}")
    def tool_custom_delete(key: str, _: bool = Depends(require_auth)):
        S()["tools"].delete_custom(key)
        return {"ok": True}

    @app.post("/api/tools/custom/test")
    def tool_custom_test(body: dict, _: bool = Depends(require_auth)):
        return S()["tools"].test_custom(body)

    @app.get("/api/mcp")
    def mcp_list(_: bool = Depends(require_auth)):
        return S()["mcp"].list()

    @app.post("/api/mcp")
    def mcp_save(body: dict, _: bool = Depends(require_auth)):
        return S()["mcp"].save(body)

    @app.delete("/api/mcp/{mid}")
    def mcp_delete(mid: int, _: bool = Depends(require_auth)):
        S()["mcp"].delete(mid)
        return {"ok": True}

    @app.get("/api/mcp/{mid}/tools")
    def mcp_tools(mid: int, _: bool = Depends(require_auth)):
        return S()["mcp"].tools(mid)

    @app.post("/api/mcp/{mid}/refresh")
    def mcp_refresh(mid: int, _: bool = Depends(require_auth)):
        return S()["mcp"].refresh(mid)

    @app.get("/api/skills")
    def skills_list(_: bool = Depends(require_auth)):
        return S()["skills"].list()

    @app.post("/api/skills")
    def skills_create(body: dict, _: bool = Depends(require_auth)):
        return S()["skills"].create(body)

    @app.post("/api/skills/upload")
    async def skills_upload(file: UploadFile = File(...), _: bool = Depends(require_auth)):
        content = await file.read()
        return S()["skills"].upload(content, file.filename or "skill.zip")

    @app.delete("/api/skills/{name}")
    def skills_delete(name: str, _: bool = Depends(require_auth)):
        S()["skills"].delete(name)
        return {"ok": True}

    @app.get("/api/skills/missing")
    def skills_missing(_: bool = Depends(require_auth)):
        return S()["skills"].missing()

    @app.get("/api/skills/{name}/usage")
    def skills_usage(name: str, _: bool = Depends(require_auth)):
        return S()["skills"].usage(name)

    @app.put("/api/skills/{name}/meta")
    def skills_meta(name: str, body: dict, _: bool = Depends(require_auth)):
        return S()["skills"].update_meta(name, body)

    @app.post("/api/skills/{name}/dirs")
    def skills_mkdir(name: str, body: dict, _: bool = Depends(require_auth)):
        return S()["skills"].create_dir(name, body)

    @app.get("/api/skills/{name}/files")
    def skills_files(name: str, _: bool = Depends(require_auth)):
        return S()["skills"].files(name)

    @app.get("/api/skills/{name}/files/{file_path:path}")
    def skills_read(name: str, file_path: str, _: bool = Depends(require_auth)):
        return S()["skills"].read_file(name, file_path)

    @app.put("/api/skills/{name}/files/{file_path:path}")
    def skills_write(name: str, file_path: str, body: dict, _: bool = Depends(require_auth)):
        return S()["skills"].write_file(name, file_path, body.get("content", ""))

    @app.delete("/api/skills/{name}/files/{file_path:path}")
    def skills_delete_file(name: str, file_path: str, _: bool = Depends(require_auth)):
        S()["skills"].delete_file(name, file_path)
        return {"ok": True}

    # 可见性
    @app.get("/api/visibility/{kind}/{rid}")
    def visibility_get(kind: str, rid: int, _: bool = Depends(require_auth)):
        return {"resource_kind": kind, "resource_id": rid, "agents": []}

    @app.post("/api/visibility/toggle")
    def visibility_toggle(body: dict, _: bool = Depends(require_auth)):
        return {"ok": True}

    @app.get("/api/visibility/skill/{name}")
    def visibility_skill(name: str, _: bool = Depends(require_auth)):
        return {"skill_name": name, "agents": []}

    @app.post("/api/visibility/skill/toggle")
    def visibility_skill_toggle(body: dict, _: bool = Depends(require_auth)):
        return {"ok": True}

    # ======================================================================
    # 日志 / 命令 / 工作区 / 更新 / 同步 / 通知
    # ======================================================================
    @app.get("/api/logs")
    def logs(since: Optional[int] = None, _: bool = Depends(require_auth)):
        return {"items": S()["logsink"].history(since or 0)}

    @app.get("/api/logs/history")
    def logs_history(before: Optional[int] = None, limit: int = 200, _: bool = Depends(require_auth)):
        return {"items": S()["logsink"].before(before or 2 ** 62, limit)}

    @app.get("/api/logs/stream")
    async def logs_stream(token: Optional[str] = Query(None), since: int = 0):
        svcs = S()
        if not token or not svcs["auth"].validate_token(token):
            raise HTTPException(401, detail={"error": "未授权"})
        sub_q = __import__("queue").Queue()
        svcs["logsink"].subscribe(sub_q)
        initial = svcs["logsink"].history(since)

        async def gen():
            for e in initial:
                yield sse_event(e)
            while True:
                ev = await next_event(sub_q)
                if ev is None:
                    yield ": ping\n\n"
                    continue
                yield sse_event(ev)

        try:
            async for chunk in gen():
                yield chunk
        finally:
            svcs["logsink"].unsubscribe(sub_q)

    @app.get("/api/commands")
    def commands(page: int = 0, size: int = 50, tool: Optional[str] = None,
                 q: Optional[str] = None, task: Optional[int] = None, _: bool = Depends(require_auth)):
        return S()["commands"].list(page, size, tool, q, task)

    @app.get("/api/commands/stats")
    def commands_stats(_: bool = Depends(require_auth)):
        return S()["commands"].stats()

    @app.get("/api/workspace/list")
    def ws_list(path: str = "", _: bool = Depends(require_auth)):
        return S()["workspace"].list(path)

    @app.get("/api/workspace/read")
    def ws_read(path: str = "", _: bool = Depends(require_auth)):
        return S()["workspace"].read(path)

    @app.post("/api/workspace/write")
    def ws_write(body: dict, _: bool = Depends(require_auth)):
        return S()["workspace"].write(body.get("path", ""), body.get("content", ""))

    @app.post("/api/workspace/mkdir")
    def ws_mkdir(body: dict, _: bool = Depends(require_auth)):
        return S()["workspace"].mkdir(body.get("path", ""))

    @app.delete("/api/workspace/delete")
    def ws_delete(path: str = Query(...), _: bool = Depends(require_auth)):
        return S()["workspace"].delete(path)

    @app.get("/api/workspace/download")
    def ws_download(path: str = Query(...), _: bool = Depends(require_auth)):
        p = S()["workspace"].download(path)
        if not p:
            raise HTTPException(404, detail={"error": "文件不存在"})
        return streaming_file(p)

    @app.post("/api/workspace/upload")
    def ws_upload(upload_path: str = Form(""), file: UploadFile = File(...), _: bool = Depends(require_auth)):
        import io
        return S()["workspace"].upload(upload_path, file.file, file.filename or "file.bin")

    @app.get("/api/update/check")
    def update_check(force: bool = False, _: bool = Depends(require_auth)):
        return S()["update"].check(force)

    @app.post("/api/update/apply")
    def update_apply(_: bool = Depends(require_auth)):
        return S()["update"].apply()

    @app.post("/api/update/rollback")
    def update_rollback(_: bool = Depends(require_auth)):
        return S()["update"].rollback()

    @app.get("/api/update/stream")
    async def update_stream(token: Optional[str] = Query(None)):
        svcs = S()
        if not token or not svcs["auth"].validate_token(token):
            raise HTTPException(401, detail={"error": "未授权"})

        async def gen():
            yield sse_event({"phase": "idle", "percent": 100, "message": "standalone 桌面版已是最新"}, event="progress")

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/sync/scopesentry/status")
    def sync_status(_: bool = Depends(require_auth)):
        return S()["sync"].status()

    @app.post("/api/sync/scopesentry/datasource")
    def sync_datasource(body: dict, _: bool = Depends(require_auth)):
        return S()["sync"].save_datasource(body)

    @app.get("/api/sync/scopesentry/projects")
    def sync_projects(_: bool = Depends(require_auth)):
        return S()["sync"].projects()

    @app.get("/api/sync/scopesentry/tasks")
    def sync_tasks(project: str = Query(...), _: bool = Depends(require_auth)):
        return S()["sync"].tasks(project)

    @app.post("/api/sync/scopesentry/sync")
    def sync_run(body: dict, _: bool = Depends(require_auth)):
        return S()["sync"].sync(body)

    @app.get("/api/notify/meta")
    def notify_meta(_: bool = Depends(require_auth)):
        return S()["notify"].meta()

    @app.get("/api/notify/channels")
    def notify_channels(_: bool = Depends(require_auth)):
        return S()["notify"].channels()

    @app.post("/api/notify/channels")
    def notify_channel_create(body: dict, _: bool = Depends(require_auth)):
        return S()["notify"].save_channel(body)

    @app.patch("/api/notify/channels/{cid}")
    def notify_channel_update(cid: int, body: dict, _: bool = Depends(require_auth)):
        body["id"] = cid
        return S()["notify"].save_channel(body)

    @app.delete("/api/notify/channels/{cid}")
    def notify_channel_delete(cid: int, _: bool = Depends(require_auth)):
        S()["notify"].delete_channel(cid)
        return {"ok": True}

    @app.post("/api/notify/channels/{cid}/test")
    def notify_channel_test(cid: int, _: bool = Depends(require_auth)):
        return S()["notify"].test_channel(cid)

    @app.get("/api/notify/deliveries")
    def notify_deliveries(page: int = 0, size: int = 50, state: Optional[str] = None, _: bool = Depends(require_auth)):
        return S()["notify"].deliveries(page, size, state)

    @app.post("/api/notify/deliveries/{did}/retry")
    def notify_retry(did: int, _: bool = Depends(require_auth)):
        return S()["notify"].retry(did)

    # ======================================================================
    # 旁路提问
    # ======================================================================
    @app.get("/api/conversations/{cid}/side-questions")
    def sq_conv_get(cid: int, _: bool = Depends(require_auth)):
        return {"sessions": []}

    @app.post("/api/conversations/{cid}/side-questions")
    def sq_conv_create(cid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].create_side_question("conversation", cid, body)

    @app.delete("/api/conversations/{cid}/side-questions")
    def sq_conv_clear(cid: int, _: bool = Depends(require_auth)):
        return {"ok": True}

    @app.post("/api/tasks/{tid}/chat/side-questions")
    def sq_task_create(tid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].create_side_question("task", tid, body)

    @app.get("/api/tasks/{tid}/chat/side-questions")
    def sq_task_get(tid: int, _: bool = Depends(require_auth)):
        return {"sessions": []}

    @app.post("/api/tasks/{tid}/intents/{iid}/side-questions")
    def sq_intent_create(tid: int, iid: int, body: dict, _: bool = Depends(require_auth)):
        return S()["conversations"].create_side_question("intent", iid, body)

    @app.get("/api/side-questions/{req_id}/events")
    async def sq_events(req_id: str, token: Optional[str] = Query(None)):
        svcs = S()
        if not token or not svcs["auth"].validate_token(token):
            raise HTTPException(401, detail={"error": "未授权"})

        async def gen():
            for _ in range(30):
                r = svcs["db"].query_one("SELECT * FROM side_question_requests WHERE id=?", (req_id,))
                if r and r["status"] in ("completed", "failed", "cancelled"):
                    yield sse_event({"status": r["status"], "answer": r["answer"], "error": r["error"]}, event="done")
                    break
                yield ": ping\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/side-questions/{req_id}")
    def sq_get(req_id: str, _: bool = Depends(require_auth)):
        r = S()["db"].query_one("SELECT * FROM side_question_requests WHERE id=?", (req_id,))
        if not r:
            raise HTTPException(404, detail={"error": "请求不存在"})
        d = dict(r)
        d["model"] = jloads(d["model"], {}) if isinstance(d.get("model"), str) else d.get("model")
        return d

    @app.post("/api/side-questions/{req_id}/cancel")
    def sq_cancel(req_id: str, _: bool = Depends(require_auth)):
        return S()["conversations"].cancel_side_question(req_id)

    # ======================================================================
    # 探索 findings export (ZIP) —— 简化占用
    # ======================================================================
    @app.get("/api/exploration/findings/export")
    def findings_export(task: Optional[int] = Query(None), _: bool = Depends(require_auth)):
        import io
        import zipfile
        buffer = io.BytesIO()
        svcs = S()
        data = svcs["exploration"].findings(task_id=task, size=1000)
        items = data if isinstance(data, list) else data.get("items", [])
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("findings.json", json.dumps(items, ensure_ascii=False, default=str))
        buffer.seek(0)
        return StreamingResponse(
            buffer, media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=findings-export.zip"},
        )

    return app


def streaming_file(path: str):
    """FileResponse 惰性读取。"""
    from fastapi.responses import FileResponse
    return FileResponse(path)


def rebuild_running_engines(svcs: dict) -> None:
    """启动时恢复运行中的任务引擎。"""
    rows = svcs["db"].query(
        "SELECT id, exploration_id FROM tasks WHERE deleted_at IS NULL AND archived_at IS NULL "
        "AND status='running'"
    )
    for r in rows:
        eng = svcs["tasks"]._start_engine(r["id"], r["exploration_id"])
        svcs["engines"][r["id"]] = eng
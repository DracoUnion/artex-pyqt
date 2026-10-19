"""杂项服务：工作区 / 命令记录 / LLM 记录 / 统计 / 更新 / 同步 / 上传。

workspace 对应 server/workspace.go；commands 对应 commands.go(工具执行记录)；
llm_records 对应 llmrec 相关；stats 供仪表盘聚合。
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .db import DB, get
from .util import jdumps, jloads, now, sid


class WorkspaceService:
    def __init__(self, db: DB, root_dir: str, upload_dir: str):
        self.db = db
        self.root_dir = os.path.abspath(root_dir or ".")
        os.makedirs(self.root_dir, exist_ok=True)
        os.makedirs(upload_dir, exist_ok=True)
        self.upload_dir = upload_dir

    def _resolve(self, path: str) -> str:
        base = self.root_dir
        p = os.path.abspath(os.path.join(base, path or ""))
        if not (p == base or p.startswith(base + os.sep)):
            raise ValueError("路径越界")
        return p

    def list(self, path: str = "") -> dict:
        p = self._resolve(path)
        if not os.path.isdir(p):
            raise ValueError("目录不存在")
        entries = []
        for name in sorted(os.listdir(p)):
            fp = os.path.join(p, name)
            st = os.stat(fp)
            rel = os.path.relpath(fp, self.root_dir).replace("\\", "/")
            entries.append({
                "name": name, "path": rel, "dir": os.path.isdir(fp),
                "size": 0 if os.path.isdir(fp) else st.st_size,
                "mtime": now(),
            })
        return {"path": rel_txt(path), "entries": entries}

    def read(self, path: str) -> dict:
        p = self._resolve(path)
        if not os.path.isfile(p):
            raise ValueError("文件不存在")
        size = os.path.getsize(p)
        binary = False
        content = ""
        with open(p, "rb") as f:
            head = f.read(2048)
        try:
            content = head.decode("utf-8")
            if "\x00" in content:
                binary = True
        except UnicodeDecodeError:
            binary = True
        if binary:
            return {"path": path, "size": size, "binary": True}
        with open(p, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": path, "size": size, "binary": False, "content": content,
                "too_large": size > 1024 * 1024}

    def write(self, path: str, content: str) -> dict:
        p = self._resolve(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True}

    def mkdir(self, path: str) -> dict:
        p = self._resolve(path)
        os.makedirs(p, exist_ok=True)
        return {"ok": True}

    def delete(self, path: str) -> dict:
        import shutil
        p = self._resolve(path)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
        return {"ok": True}

    def download(self, path: str) -> Optional[str]:
        p = self._resolve(path)
        return p if os.path.exists(p) else None

    def upload(self, path: str, file_obj, filename: str) -> dict:
        p = self._resolve(os.path.join(path or "", filename))
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "wb") as f:
            while True:
                chunk = file_obj.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
        return {"path": os.path.relpath(p, self.root_dir).replace("\\", "/"), "size": os.path.getsize(p)}

    def save_chat_attachment(self, file_obj, filename: str, scope: str = "staging", sid_val: str = "") -> dict:
        import uuid
        base = os.path.join(self.root_dir, "attachments")
        os.makedirs(base, exist_ok=True)
        if scope == "staging":
            rel = os.path.join("attachments", filename)
        elif scope == "task":
            rel = os.path.join("tasks", str(sid_val), "attachments", filename)
            os.makedirs(os.path.join(self.root_dir, "tasks", str(sid_val), "attachments"), exist_ok=True)
        elif scope == "session":
            rel = os.path.join("conversations", str(sid_val), "attachments", filename)
            os.makedirs(os.path.join(self.root_dir, "conversations", str(sid_val), "attachments"), exist_ok=True)
        else:
            rel = os.path.join("attachments", filename)
        p = os.path.join(self.root_dir, rel)
        with open(p, "wb") as f:
            while True:
                chunk = file_obj.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
        return {"name": filename, "path": rel.replace("\\", "/"), "size": os.path.getsize(p)}


def rel_txt(path: str) -> str:
    return path or "/"


class CommandsService:
    def __init__(self, db: DB):
        self.db = db

    def list(self, page: int = 0, size: int = 50, tool: Optional[str] = None,
             q: Optional[str] = None, task_id: Optional[int] = None) -> dict:
        where, params = [], []
        if tool:
            where.append("tool=?"); params.append(tool)
        if q:
            where.append("(summary LIKE ? OR detail LIKE ?)")
            params.extend([f"%{q}%"] * 2)
        if task_id:
            where.append("task_id=?"); params.append(task_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.scalar(f"SELECT COUNT(*) FROM activity WHERE kind IN ('tool_call','tool_result') AND tool IS NOT NULL{clause}", tuple(params))
        rows = self.db.query(
            f"SELECT * FROM activity WHERE kind IN ('tool_call','tool_result') AND tool IS NOT NULL{clause} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [dict(r) for r in rows], "total": total}

    def stats(self) -> list[dict]:
        rows = self.db.query(
            "SELECT tool, COUNT(*) AS count FROM activity WHERE tool IS NOT NULL GROUP BY tool ORDER BY count DESC LIMIT 50"
        )
        return [{"tool": r["tool"], "count": r["count"]} for r in rows]


class LLMRecordService:
    def __init__(self, db: DB):
        self.db = db

    def list(self, page: int = 0, size: int = 20, model: Optional[str] = None,
             session_id: Optional[str] = None, task_id: Optional[int] = None) -> dict:
        where, params = [], []
        if model:
            where.append("model=?"); params.append(model)
        if session_id:
            where.append("session_id LIKE ?"); params.append(f"%{session_id}%")
        if task_id:
            where.append("task_id=?"); params.append(task_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.db.scalar(f"SELECT COUNT(*) FROM llm_usage{clause}", tuple(params))
        rows = self.db.query(
            f"SELECT * FROM llm_usage{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [dict(r) for r in rows], "total": total}

    def get(self, rid: str) -> dict:
        r = self.db.query_one("SELECT * FROM llm_usage WHERE id=?", (rid,))
        return dict(r) if r else None

    def delete_by_task(self, task_id: int) -> dict:
        self.db.execute("DELETE FROM llm_usage WHERE task_id=?", (task_id,))
        return {"deleted": True}

    def tasks(self) -> list[dict]:
        rows = self.db.query(
            "SELECT DISTINCT task_id FROM llm_usage WHERE task_id IS NOT NULL ORDER BY task_id DESC"
        )
        return [{"task_id": sid(r["task_id"])} for r in rows]

    def by_model(self, task_id: Optional[int] = None) -> list[dict]:
        where, params = 'WHERE 1=1', []
        if task_id:
            where, params = "WHERE task_id=?", [task_id]
        rows = self.db.query(
            f"SELECT model, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, COUNT(*) AS calls "
            f"FROM llm_usage {where} GROUP BY model", tuple(params),
        )
        return [dict(r) for r in rows]


class StatsService:
    def __init__(self, db: DB):
        self.db = db

    def token_usage(self, days: int = 7) -> dict:
        rows = self.db.query(
            "SELECT model, SUM(COALESCE(input_tokens,0)) AS input_tokens, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens, "
            "SUM(COALESCE(cache_read_tokens,0)) AS cache_read_tokens, COUNT(*) AS calls "
            "FROM llm_usage GROUP BY model"
        )
        by_model = [dict(r) for r in rows]
        # 每日聚合
        daily_rows = self.db.query(
            "SELECT substr(created_at,1,10) AS day, SUM(COALESCE(input_tokens,0)) AS input_tokens, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens FROM llm_usage GROUP BY day ORDER BY day"
        )
        daily = [dict(r) for r in daily_rows]
        return {"total_input": sum(r["input_tokens"] or 0 for r in by_model),
                "total_output": sum(r["output_tokens"] or 0 for r in by_model),
                "total_cache_read": sum(r["cache_read_tokens"] or 0 for r in by_model),
                "by_model": by_model, "daily": daily}

    def token_daily(self) -> list[dict]:
        rows = self.db.query(
            "SELECT substr(created_at,1,10) AS day, SUM(COALESCE(input_tokens,0)) AS input_tokens, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens FROM llm_usage GROUP BY day ORDER BY day"
        )
        return [dict(r) for r in rows]

    def token_conversations(self) -> list[dict]:
        rows = self.db.query(
            "SELECT session_id, SUM(COALESCE(input_tokens,0)) AS input_tokens, "
            "SUM(COALESCE(output_tokens,0)) AS output_tokens FROM llm_usage "
            "WHERE session_id != '' GROUP BY session_id ORDER BY id DESC LIMIT 50"
        )
        return [dict(r) for r in rows]

    def dashboard(self) -> dict:
        from .engine import K_INTENT
        active_tasks = self.db.scalar("SELECT COUNT(*) FROM tasks WHERE deleted_at IS NULL AND archived_at IS NULL AND status IN ('running','created','paused')")
        findings = self.db.scalar("SELECT COUNT(*) FROM findings")
        assets = self.db.scalar("SELECT COUNT(*) FROM assets")
        traffic = self.db.scalar("SELECT COUNT(*) FROM traffic_exchanges")
        token = self.token_usage(7)
        return {
            "active_tasks": active_tasks or 0,
            "findings": findings or 0,
            "assets": assets or 0,
            "traffic": traffic or 0,
            "token_input": token["total_input"],
            "token_output": token["total_output"],
            "token_total": token["total_input"] + token["total_output"],
        }


class UpdateService:
    """一键更新（演示：返回已是最新；flow 结构对齐 /api/update/*）。"""

    def __init__(self, logsink=None):
        self.logsink = logsink

    def check(self, force: bool = False) -> dict:
        return {
            "has_update": False, "current_version": "standalone",
            "latest_version": "standalone", "release_url": "",
            "checksum": "", "message": "当前为 standalone 桌面版，已是最新",
        }

    def apply(self) -> dict:
        return {"ok": False, "error": "standalone 桌面版不支持在线更新"}

    def rollback(self) -> dict:
        return {"ok": False, "error": "无上一版本可回滚"}


class SyncService:
    def __init__(self, db: DB, mcp: "MCPService", assets, logsink=None):
        self.db = db
        self.mcp = mcp
        self.assets = assets
        self.logsink = logsink

    def status(self) -> dict:
        server = self.db.query_one("SELECT * FROM mcp_servers WHERE name='ScopeSentry'")
        if not server:
            return {"configured": False}
        env = jloads(server["env"], {})
        return {
            "configured": bool(server["enabled"] and server["url"]),
            "server": {"name": server["name"], "url": server["url"], "enabled": bool(server["enabled"])},
            "api_key_configured": bool(env.get("X-API-Key")),
        }

    def save_datasource(self, data: dict) -> dict:
        url = (data.get("url") or "").strip()
        key = (data.get("api_key") or "").strip()
        ts = now()
        self.db.execute(
            "UPDATE mcp_servers SET url=?, env=?, enabled=?, updated_at=? WHERE name='ScopeSentry'",
            (url, jdumps({"X-API-Key": key}), 1 if url and key else 0, ts),
        )
        return self.status()

    def projects(self) -> list[dict]:
        return [{"id": "demo-project-1", "name": "演示项目", "asset_types": ["domain", "subdomain", "ip", "port"]}]

    def tasks(self, project_id: str) -> list[dict]:
        return [{"id": "demo-task-1", "name": "演示任务", "project_id": project_id}]

    def sync(self, data: dict) -> dict:
        company_name = data.get("company_name") or "同步企业"
        # 建企业 + 灌演示资产
        nkey = company_name.strip().lower()
        existing = self.db.query_one("SELECT id FROM companies WHERE nkey=?", (nkey,))
        if existing:
            cid = existing["id"]
        else:
            ts = now()
            cur = self.db.execute(
                "INSERT INTO companies(name, nkey, created_at, updated_at) VALUES(?,?,?,?)",
                (company_name, nkey, ts, ts),
            )
            cid = cur.lastrowid
        demo_domains = ["api.demo-sync.example", "app.demo-sync.example"]
        imported = 0
        for d in demo_domains:
            self.assets._upsert_one({"type": "root_domain", "domain": d, "company_id": cid}, None)
            imported += 1
        if self.logsink:
            self.logsink.info(f"同步 {demo_domains} 资产入库", tag="sync")
        return {"ok": True, "imported": imported, "company_id": cid}
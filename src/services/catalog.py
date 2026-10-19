"""工具目录 / MCP / Skills 服务。"""
from __future__ import annotations

import json
import os
import zipfile
from typing import Any, Optional

from .db import DB
from .util import jdumps, jloads, now, sid


class ToolsService:
    BUILTIN_TOOLS = [
        ("bash", "在目标环境执行 shell 命令", {"command": {"type": "string", "description": "要执行的命令"}}),
        ("http_request", "发起 HTTP 请求", {"method": {"type": "string"}, "url": {"type": "string"}, "body": {"type": "string"}}),
        ("network_scan", "网络/端口扫描", {"target": {"type": "string"}, "ports": {"type": "string"}}),
        ("dns_resolve", "域名解析", {"domain": {"type": "string"}}),
        ("search_all_worker_traces", "检索其他 worker 执行过程", {"q": {"type": "string"}}),
        ("list_worker_traces", "列出本任务 worker 执行记录", {}),
        ("get_worker_trace", "获取某 work 指定步骤", {"intent_id": {"type": "string"}, "step_ids": {"type": "array"}}),
        ("submit_fact", "提交事实到探索图", {"text": {"type": "string"}}),
        ("submit_finding", "提交漏洞到探索图", {"vulnclass": {"type": "string"}, "severity": {"type": "string"}, "summary": {"type": "string"}}),
        ("add_task_scope", "扩大测试范围", {"kind": {"type": "string"}, "value": {"type": "string"}}),
        ("list_untested_assets", "列出未测试资产", {}),
        ("prove_goal", "声明目标已达成", {"goal_id": {"type": "string"}}),
    ]

    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink

    def seed_builtin(self) -> None:
        ts = now()
        for key, desc, schema in self.BUILTIN_TOOLS:
            if not self.db.query_one("SELECT key FROM tools WHERE key=?", (key,)):
                self.db.execute(
                    "INSERT INTO tools(key, system, description, schema, agents, enabled, kind, exec, deferred, updated_at) "
                    "VALUES(?,1,?,?,'[]',1,'builtin','{}',0,?)",
                    (key, desc, jdumps(schema), ts),
                )

    def list_tools(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM tools ORDER BY key")
        out = []
        for r in rows:
            d = dict(r)
            d["schema"] = jloads(d["schema"], {})
            d["agents"] = jloads(d["agents"], [])
            d["exec"] = jloads(d["exec"], {})
            d["system"] = bool(d["system"])
            d["enabled"] = bool(d["enabled"])
            d["deferred"] = bool(d["deferred"])
            out.append(d)
        return out

    def get_tool(self, key: str) -> Optional[dict]:
        r = self.db.query_one("SELECT * FROM tools WHERE key=?", (key,))
        if not r:
            return None
        d = dict(r)
        d["schema"] = jloads(d["schema"], {})
        d["agents"] = jloads(d["agents"], [])
        d["exec"] = jloads(d["exec"], {})
        return d

    def update_tool(self, key: str, data: dict) -> dict:
        ts = now()
        desc = data.get("description")
        schema = data.get("schema")
        agents = data.get("agents")
        if desc is not None:
            self.db.execute("UPDATE tools SET description=?, updated_at=? WHERE key=?", (desc, ts, key))
        if schema is not None:
            self.db.execute("UPDATE tools SET schema=?, updated_at=? WHERE key=?", (jdumps(schema), ts, key))
        if agents is not None:
            self.db.execute("UPDATE tools SET agents=?, updated_at=? WHERE key=?", (jdumps(agents), ts, key))
        return self.get_tool(key)

    def reset_tool(self, key: str) -> dict:
        for k, desc, schema in self.BUILTIN_TOOLS:
            if k == key:
                ts = now()
                self.db.execute("UPDATE tools SET description=?, schema=?, updated_at=? WHERE key=?",
                                (desc, jdumps(schema), ts, key))
        return self.get_tool(key)

    def create_custom(self, data: dict) -> dict:
        key = (data.get("key") or "").strip()
        if not key:
            raise ValueError("工具 key 不能为空")
        if self.db.query_one("SELECT key FROM tools WHERE key=?", (key,)):
            raise ValueError("工具已存在")
        ts = now()
        self.db.execute(
            "INSERT INTO tools(key, system, description, schema, agents, enabled, kind, exec, deferred, updated_at) "
            "VALUES(?,0,?,?,?,'[]',1,?,?,0,?)",
            (key, data.get("description", ""), jdumps(data.get("schema") or {}),
             jdumps(data.get("agents") or []), data.get("kind") or "exec",
             jdumps(data.get("exec") or {}), ts),
        )
        return self.get_tool(key)

    def update_custom(self, key: str, data: dict) -> dict:
        ts = now()
        self.db.execute(
            "UPDATE tools SET description=?, schema=?, agents=?, exec=?, enabled=?, updated_at=? WHERE key=? AND kind='custom'",
            (data.get("description", ""), jdumps(data.get("schema") or {}), jdumps(data.get("agents") or []),
             jdumps(data.get("exec") or {}), 1 if data.get("enabled", True) else 0, ts, key),
        )
        return self.get_tool(key)

    def delete_custom(self, key: str) -> None:
        self.db.execute("DELETE FROM tools WHERE key=? AND kind='custom'", (key,))

    def test_custom(self, data: dict) -> dict:
        """自定义工具测试（演示：直接返回配置）。"""
        return {"ok": True, "output": "自定义工具测试通过", "error": ""}


class MCPService:
    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink

    def list(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM mcp_servers ORDER BY id")
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["args"] = jloads(d["args"], [])
            d["env"] = jloads(d["env"], {})
            d["enabled"] = bool(d["enabled"])
            d["insecure"] = bool(d["insecure"])
            d["tool_count"] = self.db.scalar("SELECT COUNT(*) FROM mcp_tools_cache WHERE server_id=?", (r["id"],))
            out.append(d)
        return out

    def save(self, data: dict) -> dict:
        ts = now()
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("服务器名称不能为空")
        mid = data.get("id")
        if mid:
            self.db.execute(
                "UPDATE mcp_servers SET name=?, transport=?, command=?, args=?, env=?, url=?, enabled=?, insecure=?, updated_at=? "
                "WHERE id=?",
                (name, data.get("transport", "http"), data.get("command"), jdumps(data.get("args") or []),
                 jdumps(data.get("env") or {}), data.get("url"), 1 if data.get("enabled", True) else 0,
                 1 if data.get("insecure") else 0, ts, int(mid)),
            )
            new_id = int(mid)
        else:
            cur = self.db.execute(
                "INSERT INTO mcp_servers(name, transport, command, args, env, url, enabled, insecure, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (name, data.get("transport", "http"), data.get("command"), jdumps(data.get("args") or []),
                 jdumps(data.get("env") or {}), data.get("url"), 1 if data.get("enabled", True) else 0,
                 1 if data.get("insecure") else 0, ts, ts),
            )
            new_id = cur.lastrowid
        return self._server(new_id)

    def _server(self, mid: int) -> dict:
        r = self.db.query_one("SELECT * FROM mcp_servers WHERE id=?", (mid,))
        d = dict(r)
        d["id"] = sid(d["id"])
        d["args"] = jloads(d["args"], [])
        d["env"] = jloads(d["env"], {})
        d["enabled"] = bool(d["enabled"])
        d["insecure"] = bool(d["insecure"])
        return d

    def delete(self, mid: Any) -> None:
        self.db.execute("DELETE FROM mcp_servers WHERE id=?", (int(mid),))

    def tools(self, mid: Any) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM mcp_tools_cache WHERE server_id=? ORDER BY tool_name", (int(mid),)
        )
        return [dict(r) for r in rows]

    def refresh(self, mid: Any) -> dict:
        """刷新 MCP 工具发现（演示：注入占位工具）。"""
        server = self._server(int(mid))
        ts = now()
        self.db.execute("DELETE FROM mcp_tools_cache WHERE server_id=?", (int(mid),))
        demo_tools = ["query_asset", "fetch_url", "run_search"]
        for t in demo_tools:
            self.db.execute(
                "INSERT INTO mcp_tools_cache(server_id, tool_name, description, schema, discovered_at) VALUES(?,?,?,?,?)",
                (int(mid), t, f"{server['name']} 的 {t} 工具", jdumps({}), ts),
            )
        return {"refreshed": len(demo_tools)}


class SkillsService:
    def __init__(self, db: DB, skills_dir: str, logsink=None):
        self.db = db
        self.skills_dir = skills_dir
        self.logsink = logsink
        os.makedirs(skills_dir, exist_ok=True)

    def _skill_path(self, name: str) -> str:
        return os.path.join(self.skills_dir, name)

    def list(self) -> list[dict]:
        out = []
        if not os.path.isdir(self.skills_dir):
            return out
        for name in sorted(os.listdir(self.skills_dir)):
            path = os.path.join(self.skills_dir, name)
            if not os.path.isdir(path):
                continue
            meta = {}
            skmd = os.path.join(path, "SKILL.md")
            if os.path.exists(skmd):
                with open(skmd, encoding="utf-8") as f:
                    meta = self._parse_meta(f.read())
            usage = self.db.query_one("SELECT COUNT(*) AS c FROM skill_usage WHERE skill=?", (name,))
            out.append({
                "name": name, "description": meta.get("description", ""),
                "path": path, "usage_count": usage["c"] if usage else 0,
                "files": self._file_tree(name),
            })
        return out

    def _parse_meta(self, content: str) -> dict:
        meta = {"description": ""}
        for line in content.splitlines():
            if line.startswith("description:"):
                meta["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("---"):
                break
        return meta

    def _file_tree(self, name: str) -> list[dict]:
        root = self._skill_path(name)
        out = []
        if not os.path.isdir(root):
            return out
        for fn in sorted(os.listdir(root)):
            fp = os.path.join(root, fn)
            out.append({"name": fn, "path": os.path.relpath(fp, root).replace("\\", "/"),
                        "dir": os.path.isdir(fp)})
        return out

    def create(self, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("skill 名称不能为空")
        path = self._skill_path(name)
        os.makedirs(path, exist_ok=True)
        skmd = os.path.join(path, "SKILL.md")
        if not os.path.exists(skmd):
            with open(skmd, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n\ndescription: {data.get('description', '')}\n")
        return self.list()

    def delete(self, name: str) -> None:
        import shutil
        shutil.rmtree(self._skill_path(name), ignore_errors=True)

    def upload(self, file_bytes: bytes, filename: str) -> dict:
        import io
        import shutil
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                for zi in z.infolist():
                    if zi.is_dir():
                        continue
                    rel = zi.filename.replace("\\", "/")
                    parts = rel.split("/")
                    if not parts or not parts[0]:
                        continue
                    skill_name = parts[0]
                    dest = os.path.join(self.skills_dir, skill_name)
                    os.makedirs(os.path.dirname(os.path.join(dest, *parts[1:])), exist_ok=True)
                    with z.open(zi) as src, open(os.path.join(dest, *parts[1:]), "wb") as dst:
                        dst.write(src.read())
            return {"uploaded": filename}
        except zipfile.BadZipFile:
            raise ValueError("无效的 zip 文件")

    def missing(self) -> list[str]:
        rows = self.db.query(
            "SELECT DISTINCT skill FROM skill_usage WHERE found=0 ORDER BY skill"
        )
        existing = {s["name"] for s in self.list()}
        return [r["skill"] for r in rows if r["skill"] not in existing]

    def usage(self, name: str) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM skill_usage WHERE skill=? ORDER BY id DESC LIMIT 50", (name,)
        )
        return [dict(r) for r in rows]

    def update_meta(self, name: str, data: dict) -> dict:
        skmd = os.path.join(self._skill_path(name), "SKILL.md")
        if os.path.exists(skmd):
            with open(skmd, encoding="utf-8") as f:
                content = f.read()
            with open(skmd, "w", encoding="utf-8") as f:
                if "description:" in content:
                    import re as _re
                    content = _re.sub(r"(?m)^description:.*$", f"description: {data.get('description', '')}", content)
                else:
                    content += f"\ndescription: {data.get('description', '')}\n"
                f.write(content)
        return {"ok": True}

    def create_dir(self, name: str, data: dict) -> dict:
        path = os.path.join(self._skill_path(name), data.get("path", ""))
        os.makedirs(path, exist_ok=True)
        return {"ok": True}

    def files(self, name: str) -> list[dict]:
        return self._file_tree(name)

    def read_file(self, name: str, rel: str) -> dict:
        path = os.path.join(self._skill_path(name), rel)
        if not os.path.exists(path) or os.path.isdir(path):
            raise ValueError("文件不存在")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return {"path": rel, "content": content, "size": len(content)}

    def write_file(self, name: str, rel: str, content: str) -> dict:
        path = os.path.join(self._skill_path(name), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True}

    def delete_file(self, name: str, rel: str) -> None:
        import shutil
        path = os.path.join(self._skill_path(name), rel)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
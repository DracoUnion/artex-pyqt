"""双图引擎：planner/worker 事件驱动闭环（移植自 Go 引擎设计）。

核心循环：
  图一变 -> debounce 唤醒 planner -> planner 读态势、更新 todolist、
  派 0..N 条 intent 进 frontier -> worker 领 intent、执行真实工具、
  写回 fact/asset/finding + 每步 activity -> 图又变 -> 再次唤醒。

LLM 可用时走真实推理；无 LLM 时进入演示模式(自演推进一次任务闭环)。
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from typing import Any, Optional

from .db import DB, get, jfield
from .logsink import LogSink
from .util import now, unix_of

# 节点类型
K_BEGIN, K_GOAL, K_INTENT, K_FACT, K_FINDING, K_HINT, K_DIGEST = (
    "begin", "goal", "intent", "fact", "finding", "hint", "digest",
)
EDGE_RELS = ("spawns", "derived_from", "yields", "proves", "covers")

INTENT_STATES = ("open", "running", "paused", "done", "blocked", "exhausted", "stopped", "deleted")
FINDING_SEVERITIES = ("critical", "high", "medium", "low")


class Engine:
    """单任务引擎。每个任务一个实例，运行 plannerLoop + worker 池。"""

    def __init__(
        self,
        db: DB,
        logsink: LogSink,
        broadcaster,
        task_id: int,
        exploration_id: int,
        llm_service,
        settings: dict,
        concurrency: int = 3,
        log: Optional[LogSink] = None,
    ):
        self.db = db
        self.logsink = logsink or log
        self.broadcaster = broadcaster
        self.task_id = task_id
        self.exploration_id = exploration_id
        self.llm = llm_service
        self.settings = settings
        self.concurrency = max(1, concurrency)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._planner_round = 0
        self._todolist: list[dict] = []  # planner 多轮共享规划
        self._running_workers = 0
        self._wake_debounce: Optional[threading.Timer] = None
        self._threads: list[threading.Thread] = []
        self._todolist_item_steps: dict[int, dict] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        t = threading.Thread(target=self._loop, name=f"planner-{self.task_id}", daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        if self._wake_debounce:
            self._wake_debounce.cancel()

    def paused(self) -> bool:
        row = self.db.query_one("SELECT paused FROM tasks WHERE id=?", (self.task_id,))
        return bool(row and row["paused"])

    # ------------------------------------------------------------------
    # 图变更通知（由 activity 写入方调用）
    # ------------------------------------------------------------------
    def notify_graph_changed(self) -> None:
        if self._stop.is_set() or self.paused():
            return
        with self._lock:
            if self._wake_debounce:
                self._wake_debounce.cancel()
            self._wake_debounce = threading.Timer(1.0, self._wake_planner)
            self._wake_debounce.daemon = True
            self._wake_debounce.start()

    def _wake_planner(self) -> None:
        if self._stop.is_set() or self.paused():
            return
        threading.Thread(target=self._planner_round_once, name=f"plan-{self.task_id}", daemon=True).start()

    # ------------------------------------------------------------------
    # Planner 主循环
    # ------------------------------------------------------------------
    def _loop(self) -> None:
        self.db.execute(
            "UPDATE tasks SET status='running', first_run_at=COALESCE(first_run_at, ?) WHERE id=?",
            (now(), self.task_id),
        )
        # 首轮：解析目标 -> 创建 begin + goal 节点 + 初始 todolist
        self._planner_round_once()
        while not self._stop.is_set():
            if self.paused():
                time.sleep(1.0)
                continue
            time.sleep(2.0)

    def _planner_round_once(self) -> None:
        with self._lock:
            self._planner_round += 1
            round_no = self._planner_round
        self.db.execute(
            "UPDATE explorations SET round_no=? WHERE id=?", (round_no, self.exploration_id)
        )
        try:
            self._ensure_seed()
            self._plan_intents(round_no)
            self._dispatch_workers()
        except Exception:
            self.logsink.error(
                f"planner 轮次异常: {traceback.format_exc()}", tag=f"engine:{self.task_id}"
            )

    def _ensure_seed(self) -> None:
        """任务首次运行时创建 begin 节点 + goal 节点 + 首条 intent。"""
        goal_text = self._task_goal()
        if not goal_text:
            return
        cnt = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind IN ('begin','goal')",
            (self.exploration_id,),
        )
        if cnt > 0:
            return
        ts = now()
        cur = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, state, created_at, updated_at) "
            "VALUES(?,?,'{}','open',?,?)",
            (self.exploration_id, K_BEGIN, ts, ts),
        )
        begin_id = cur.lastrowid
        cur = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, state, created_at, updated_at) "
            "VALUES(?,?,?,'open',?,?)",
            (
                self.exploration_id,
                K_GOAL,
                json.dumps({"text": goal_text, "vulnclass": ""}, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        goal_id = cur.lastrowid
        if begin_id and goal_id:
            self.db.execute(
                "INSERT OR IGNORE INTO exploration_edges(exploration_id, src_id, dst_id, rel, created_at) "
                "VALUES(?,?,?,?,?)",
                (self.exploration_id, begin_id, goal_id, "spawns", ts),
            )
            self._seed_initial_scope()
            # 首条 intent
            self._create_intent(goal_id, self._first_intent_text(), origin="seed", prio=10)

    def _task_goal(self) -> str:
        row = self.db.query_one("SELECT goal FROM tasks WHERE id=?", (self.task_id,))
        return (row["goal"] if row else "") or ""

    def _seed_initial_scope(self) -> None:
        """把任务描述里的域名/IP 灌入 task_scope，作为覆盖度分母。"""
        desc = self.db.query_one(
            "SELECT description FROM tasks WHERE id=?", (self.task_id,)
        )
        text = (desc["description"] if desc else "") + " " + self._task_goal()
        for m in re.finditer(r"([a-z0-9][a-z0-9-]*\.)+[a-z0-9][a-z0-9-]*(\.[a-z]{2,})?", text, re.I):
            dom = m.group(0).lower()
            if dom in ("artex", "localhost", "example.com"):
                continue
            self.db.execute(
                "INSERT OR IGNORE INTO task_scope(task_id, kind, domain, source, created_at) "
                "VALUES(?,?,?,?,?)",
                (self.task_id, "root_domain", dom, "auto", now()),
            )

    def _first_intent_text(self) -> str:
        return f"针对任务目标开展初步侦察：识别可达资产、开放服务与潜在漏洞点。"

    # ------------------------------------------------------------------
    # 意图创建
    # ------------------------------------------------------------------
    def _create_intent(self, parent_id: Optional[int], text: str, origin: str = "planner", prio: int = 50) -> Optional[int]:
        ts = now()
        payload = json.dumps({"text": text, "asset_ids": []}, ensure_ascii=False)
        cur = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, priority, state, origin, created_at, updated_at) "
            "VALUES(?,?,?,?, 'open', ?,?,?)",
            (self.exploration_id, K_INTENT, payload, prio, origin, ts, ts),
        )
        intent_id = cur.lastrowid
        if parent_id:
            self.db.execute(
                "INSERT OR IGNORE INTO exploration_edges(exploration_id, src_id, dst_id, rel, created_at) "
                "VALUES(?,?,?,?,?)",
                (self.exploration_id, parent_id, intent_id, "spawns", ts),
            )
        self.logsink.info(f"planner 派发意图 #{intent_id}: {text[:60]}", tag=f"engine:{self.task_id}")
        return intent_id

    # ------------------------------------------------------------------
    # Planner 规划：更新 todolist + 派意图
    # ------------------------------------------------------------------
    def _plan_intents(self, round_no: int) -> None:
        """每次唤醒评估开放目标；为每个未满足 goal 派生下一步意图。

        演示模式(无 LLM)下用预设策略：侦察 -> 探测 -> 扫描 -> 漏洞确认，逐步推进。
        """
        goal_row = self.db.query_one(
            "SELECT id, payload, state FROM exploration_nodes "
            "WHERE exploration_id=? AND kind='goal' AND state='open' ORDER BY id LIMIT 1",
            (self.exploration_id,),
        )
        if not goal_row:
            return
        goal_id, goal_state = goal_row["id"], goal_row["state"]
        if goal_state != "open":
            return

        # 已有的 open intent 还很多就不再派生
        open_intents = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND state='open'",
            (self.exploration_id,),
        )
        if open_intents and open_intents >= self.concurrency * 2:
            return

        # 已完成的 intent 数 -> 决定下一步
        done_intents = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND state IN ('done','exhausted')",
            (self.exploration_id,),
        )
        facts = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='fact' AND state='confirmed'",
            (self.exploration_id,),
        )
        findings = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='finding' AND state='confirmed'",
            (self.exploration_id,),
        )

        self._todolist = self._build_todolist(done_intents, facts, findings)

        next_step = None
        for step in self._todolist:
            if not step.get("done"):
                next_step = step
                break
        if next_step is None:
            # 全部完成 -> 目标达成
            self._mark_goal_met(goal_id)
            return

        # 是否已经派了对应意图？
        already = self.db.scalar(
            "SELECT COUNT(*) FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND state='open' AND payload LIKE ?",
            (self.exploration_id, f"%{next_step['title'][:24]}%"),
        )
        if already:
            return

        intent_id = self._create_intent(goal_id, next_step["prompt"], origin="planner", prio=next_step["prio"])
        self._todolist_item_for_intent(next_step, intent_id)

    def _build_todolist(self, done: int, facts: int, findings: int) -> list[dict]:
        """演示模式 todolist：侦察->端口探测->漏洞扫描->确认。"""
        steps = [
            {
                "title": "资产侦察与存活确认",
                "prompt": "对目标资产做基础侦察：解析域名、确认可达性、识别子域名和暴露面。",
                "prio": 9,
                "done": done >= 1,
            },
            {
                "title": "端口与服务识别",
                "prompt": "探测目标开放端口与运行服务，识别常见 Web 服务指纹。",
                "prio": 7,
                "done": done >= 2,
            },
            {
                "title": "Web 漏洞扫描",
                "prompt": "对识别出的 Web 服务执行漏洞扫描：SQL 注入、XSS、SSRF、目录穿越等。",
                "prio": 5,
                "done": done >= 3 or findings >= 1,
            },
            {
                "title": "漏洞确认与证据收集",
                "prompt": "验证已发现漏洞，收集复现证据、评估危害等级并输出结论。",
                "prio": 3,
                "done": done >= 4 or findings >= 2,
            },
        ]
        return steps

    def _todolist_item_for_intent(self, step: dict, intent_id: int) -> None:
        self._todolist_item_steps[intent_id] = step

    def _mark_goal_met(self, goal_id: int) -> None:
        ts = now()
        self.db.execute(
            "UPDATE exploration_nodes SET state='met', completed_at=? WHERE id=?",
            (ts, goal_id),
        )
        self.db.execute("UPDATE explorations SET status='achieved', updated_at=? WHERE id=?", (ts, self.exploration_id))
        self.db.execute(
            "UPDATE tasks SET status='done', completed_at=? WHERE id=? AND status NOT IN ('done','failed')",
            (ts, self.task_id),
        )
        self.logsink.info(f"任务 {self.task_id} 目标达成，标记完成", tag=f"engine:{self.task_id}")
        self.broadcaster.publish(self.task_id, {"type": "task_status", "status": "done"})

    # ------------------------------------------------------------------
    # Worker 分派与执行
    # ------------------------------------------------------------------
    def _dispatch_workers(self) -> None:
        """领取 open intent 并启动 worker 线程。"""
        if self._stop.is_set() or self.paused():
            return
        with self._lock:
            if self._running_workers >= self.concurrency:
                return
        rows = self.db.query(
            "SELECT id, payload FROM exploration_nodes WHERE exploration_id=? AND kind='intent' AND state='open' "
            "ORDER BY priority DESC, id LIMIT ?",
            (self.exploration_id, self.concurrency - self._running_workers),
        )
        for row in rows:
            with self._lock:
                if self._running_workers >= self.concurrency:
                    break
                self._running_workers += 1
            t = threading.Thread(
                target=self._worker_run, args=(row["id"],), name=f"worker-{row['id']}", daemon=True
            )
            t.start()

    def _worker_run(self, intent_id: int) -> None:
        try:
            self._claim_intent(intent_id)
            if self.llm and self.llm.available():
                self._worker_llm(intent_id)
            else:
                self._worker_demo(intent_id)
        except Exception:
            self.logsink.error(f"worker {intent_id} 异常: {traceback.format_exc()}", tag=f"engine:{self.task_id}")
        finally:
            with self._lock:
                self._running_workers -= 1
            self.notify_graph_changed()

    def _claim_intent(self, intent_id: int) -> None:
        ts = now()
        self.db.execute(
            "UPDATE exploration_nodes SET state='running', owner='worker', completed_at=NULL, updated_at=? WHERE id=? AND state='open'",
            (ts, intent_id),
        )
        self._write_activity(None, intent_id, "worker", "session_start", summary="意图开始执行")

    # ------------------------------------------------------------------
    # Worker 演示执行：无 LLM 时的自演闭环
    # ------------------------------------------------------------------
    def _worker_demo(self, intent_id: int) -> None:
        step = getattr(self, "_todolist_item_steps", {}).get(intent_id, {})
        title = step.get("title", "通用侦察")
        self.logsink.info(f"worker #{intent_id} 执行: {title}", tag=f"engine:{self.task_id}")

        time.sleep(1.2)
        self._write_activity(None, intent_id, "worker", "tool_call", tool="network_scan", summary="开始网络侦察")
        time.sleep(1.0)
        # 演示发现资产
        assets = self._demo_discover_assets(intent_id)
        self._record_demo_traffic(intent_id, title)
        self._write_activity(
            None, intent_id, "worker", "tool_result", tool="network_scan",
            summary=f"发现 {len(assets)} 个资产端点",
            detail=json.dumps({"assets": assets}, ensure_ascii=False),
            metadata={"kind": "tool_result"},
        )
        # 演示发现漏洞
        finding_rows = self._demo_find_vulns(intent_id, step)
        for frow in finding_rows:
            self._write_activity(
                None, intent_id, "worker", "finding", summary=f"确认漏洞: {frow['name']}",
                detail=json.dumps({"severity": frow["severity"], "summary": frow["summary"]}, ensure_ascii=False),
            )
        time.sleep(0.8)
        self._write_activity(None, intent_id, "worker", "session_end", summary="意图执行完成")
        ts = now()
        self.db.execute(
            "UPDATE exploration_nodes SET state='done', completed_at=? WHERE id=?",
            (ts, intent_id),
        )
        self._write_fact_from_demo(intent_id, title, finding_rows)

    def _demo_discover_assets(self, intent_id: int) -> list[dict]:
        """按任务 scope 生成演示资产并入库(若尚未存在)。"""
        scopes = self.db.query(
            "SELECT kind, domain, value FROM task_scope WHERE task_id=?", (self.task_id,)
        )
        out = []
        for s in scopes:
            if s["kind"] == "root_domain" and s["domain"]:
                dom = s["domain"]
                # 根域
                self._upsert_asset({"type": "root_domain", "domain": dom})
                out.append({"type": "root_domain", "domain": dom})
                # 子域
                sub = f"www.{dom}"
                self._upsert_asset({"type": "subdomain", "domain": sub, "root_domain": dom})
                out.append({"type": "subdomain", "domain": sub})
                # 服务
                self._upsert_asset({"type": "service", "url": f"http://{sub}", "service_type": "http", "status_code": 200})
                out.append({"type": "service", "url": f"http://{sub}", "status_code": 200})
                # 端点
                self._upsert_asset({"type": "endpoint", "url": f"http://{sub}/api/status", "method": "GET"})
                out.append({"type": "endpoint", "url": f"http://{sub}/api/status", "method": "GET"})
        if not out:
            # 无 scope 时演示生成
            demo = "demo.artex.local"
            self._upsert_asset({"type": "root_domain", "domain": demo})
            self._upsert_asset({"type": "subdomain", "domain": f"www.{demo}", "root_domain": demo})
            out = [{"type": "root_domain", "domain": demo}]
        return out

    def _upsert_asset(self, fields: dict) -> Optional[int]:
        """按类型唯一约束 upsert 资产；返回 asset_id。"""
        typ = fields.get("type")
        ts = now()
        # 查找已有
        if typ == "root_domain":
            row = self.db.query_one("SELECT id FROM assets WHERE type='root_domain' AND domain=?", (fields.get("domain"),))
        elif typ == "subdomain":
            row = self.db.query_one("SELECT id FROM assets WHERE type='subdomain' AND domain=? AND IFNULL(record_type,'')=''", (fields.get("domain"),))
        elif typ == "service" and fields.get("service_type") == "http":
            row = self.db.query_one("SELECT id FROM assets WHERE type='service' AND url=?", (fields.get("url"),))
        elif typ == "endpoint":
            row = self.db.query_one("SELECT id FROM assets WHERE type='endpoint' AND url=? AND IFNULL(method,'')=?", (fields.get("url"), fields.get("method", "")))
        elif typ == "ip":
            row = self.db.query_one("SELECT id FROM assets WHERE type='ip' AND ip=?", (fields.get("ip"),))
        else:
            row = None
        if row:
            return row["id"]
        cols = ["type", "company_id", "domain", "root_domain", "ip", "url", "service_type",
                "method", "status_code", "task_ids", "created_at", "last_seen", "updated_at"]
        vals = [fields.get("type"), fields.get("company_id"), fields.get("domain"),
                fields.get("root_domain"), fields.get("ip"), fields.get("url"),
                fields.get("service_type"), fields.get("method"), fields.get("status_code"),
                json.dumps([self.task_id]), ts, ts, ts]
        try:
            cur = self.db.execute(
                f"INSERT INTO assets({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                tuple(vals),
            )
            asset_id = cur.lastrowid
        except Exception:
            # 并发下唯一约束竞态：回退到查找
            row = self._find_existing_asset(fields)
            if not row:
                raise
            asset_id = row["id"]
            self._append_task_id(asset_id)
        # 关联任务
        self.db.execute(
            "INSERT OR IGNORE INTO task_asset_links(task_id, asset_id, source, source_summary, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (self.task_id, asset_id, "system", "任务执行期间自动关联", ts, ts),
        )
        return asset_id

    def _find_existing_asset(self, fields: dict):
        typ = fields.get("type")
        if typ == "root_domain":
            return self.db.query_one("SELECT id FROM assets WHERE type='root_domain' AND domain=?", (fields.get("domain"),))
        if typ == "subdomain":
            return self.db.query_one("SELECT id FROM assets WHERE type='subdomain' AND domain=? AND IFNULL(record_type,'')=''", (fields.get("domain"),))
        if typ == "service" and fields.get("service_type") == "http":
            return self.db.query_one("SELECT id FROM assets WHERE type='service' AND url=?", (fields.get("url"),))
        if typ == "endpoint":
            return self.db.query_one("SELECT id FROM assets WHERE type='endpoint' AND url=? AND IFNULL(method,'')=?", (fields.get("url"), fields.get("method", "")))
        if typ == "ip":
            return self.db.query_one("SELECT id FROM assets WHERE type='ip' AND ip=?", (fields.get("ip"),))
        return None

    def _append_task_id(self, asset_id: int) -> None:
        row = self.db.query_one("SELECT task_ids FROM assets WHERE id=?", (asset_id,))
        if not row:
            return
        try:
            task_ids = json.loads(row["task_ids"] or "[]")
        except Exception:
            task_ids = []
        if self.task_id not in task_ids:
            task_ids.append(self.task_id)
            self.db.execute("UPDATE assets SET task_ids=?, last_seen=?, updated_at=? WHERE id=?",
                            (json.dumps(task_ids), now(), now(), asset_id))

    def _record_demo_traffic(self, intent_id: int, title: str) -> None:
        """演示流量记录：为任务范围里的服务生成 HTTP 交换。"""
        try:
            scopes = self.db.query("SELECT domain FROM task_scope WHERE task_id=? AND kind='root_domain'", (self.task_id,))
            hosts = [s["domain"] for s in scopes][:3] or ["demo.artex.local"]
            ts = now()
            for h in hosts:
                host = f"www.{h}"
                for method, path, status, body in [
                    ("GET", "/", 200, "<html><body>ARTEX Demo</body></html>"),
                    ("GET", "/api/status", 200, '{"status":"ok","service":"demo-api"}'),
                    ("POST", "/api/login", 302, ""),
                    ("GET", "/api/users?id=1", 500, '{"error":"internal"}'),
                ]:
                    self.db.execute(
                        "INSERT INTO traffic_exchanges(host, method, path, scheme, status, resp_len, "
                        "req_headers, resp_headers, req_body, resp_body, req_body_b64, resp_body_b64, "
                        "mime, task_ids, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (host, method, path, "http", status, len(body),
                         json.dumps({"Host": host, "User-Agent": "artex-agent"}), "{}",
                         json.dumps({}) if method != "GET" else "",
                         body, 0, 0, "text/html" if "html" in body else "application/json",
                         [self.task_id], ts),
                    )
        except Exception:
            pass

    def _demo_find_vulns(self, intent_id: int, step: dict) -> list[dict]:
        """根据规划步骤演示产出漏洞。"""
        results = []
        title = step.get("title", "")
        if "端口" in title or "服务" in title:
            results.append({
                "vulnclass": "CWE-200", "name": "敏感信息泄露",
                "severity": "medium",
                "summary": "目标 HTTP 响应头暴露了服务器版本与技术栈信息，可能泄露内部架构。",
            })
        if "扫描" in title or "漏洞" in title:
            results.append({
                "vulnclass": "CWE-89", "name": "SQL 注入",
                "severity": "high",
                "summary": "参数 id 未经过滤直接拼入 SQL 查询，可被注入导致数据泄露。",
            })
        if "确认" in title or "证据" in title:
            results.append({
                "vulnclass": "CWE-79", "name": "存储型 XSS",
                "severity": "medium",
                "summary": "评论接口未对用户输入做输出编码，存在存储型跨站脚本。",
            })
        for r in results:
            self._create_finding(intent_id, r)
        return results

    def _create_finding(self, intent_id: int, fields: dict) -> Optional[int]:
        ts = now()
        cur = self.db.execute(
            "INSERT INTO findings(task_id, node_id, vulnclass, name, severity, summary, evidence, worker, status, asset_ids, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (self.task_id, intent_id, fields.get("vulnclass", ""), fields.get("name", ""),
             fields.get("severity", ""), fields.get("summary", ""), fields.get("evidence", ""),
             f"worker#{intent_id}", "pending", "[]", ts),
        )
        finding_id = cur.lastrowid
        # 探索图节点
        cur2 = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, state, origin, created_at, updated_at, completed_at) "
            "VALUES(?,?,'{}','confirmed','worker',?,?,?)",
            (self.exploration_id, K_FINDING, ts, ts, ts),
        )
        node_id = cur2.lastrowid
        self.db.execute(
            "INSERT OR IGNORE INTO exploration_edges(exploration_id, src_id, dst_id, rel, created_at) VALUES(?,?,?,?,?)",
            (self.exploration_id, intent_id, node_id, "yields", ts),
        )
        self.db.execute("UPDATE findings SET node_id=? WHERE id=?", (node_id, finding_id))
        self.db.execute(
            "UPDATE exploration_nodes SET payload=? WHERE id=?",
            (json.dumps({"text": fields.get("name"), "vulnclass": fields.get("vulnclass"),
                         "severity": fields.get("severity"), "finding_id": finding_id}, ensure_ascii=False), node_id),
        )
        return finding_id

    def _write_fact_from_demo(self, intent_id: int, title: str, finding_rows: list) -> None:
        ts = now()
        summary = f"确认目标 {title} 面：端口/服务状态与可用漏洞集"
        cur = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, state, origin, created_at, updated_at, completed_at) "
            "VALUES(?,?,'{}','confirmed','worker',?,?,?)",
            (self.exploration_id, K_FACT, ts, ts, ts),
        )
        fact_id = cur.lastrowid
        self.db.execute(
            "INSERT OR IGNORE INTO exploration_edges(exploration_id, src_id, dst_id, rel, created_at) VALUES(?,?,?,?,?)",
            (self.exploration_id, intent_id, fact_id, "yields", ts),
        )
        payload = {"text": summary, "confidence": "high", "detail": {"findings": len(finding_rows)}}
        self.db.execute("UPDATE exploration_nodes SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), fact_id))

    # ------------------------------------------------------------------
    # Worker LLM 执行（有 LLM 时）
    # ------------------------------------------------------------------
    def _worker_llm(self, intent_id: int) -> None:
        payload = self.db.query_one("SELECT payload FROM exploration_nodes WHERE id=?", (intent_id,))
        text = jfield(payload, "text", "执行当前意图")
        self._write_activity(None, intent_id, "worker", "reasoning", summary="LLM 推理执行意图")
        try:
            result = self.llm.complete(
                prompt=(
                    "你是 ARTEX 自主渗透测试 worker。当前意图: {text}\n"
                    "请：1) 推理目标信息收集步骤；2) 选择一个可用工具执行；3) 汇报观察结果。"
                    "若确认真实漏洞则给出 vulnclass / severity / summary。以 JSON 输出。"
                ).format(text=text)
            )
            self._write_activity(None, intent_id, "worker", "tool_call", tool="llm_reasoner", summary="工具执行")
            self._write_fact_from_llm(intent_id, result)
        except Exception as e:
            self._write_activity(None, intent_id, "worker", "error", is_error=1, summary=f"LLM 调用失败: {e}")
        ts = now()
        self.db.execute("UPDATE exploration_nodes SET state='done', completed_at=? WHERE id=?", (ts, intent_id))

    def _write_fact_from_llm(self, intent_id: int, result: str) -> None:
        ts = now()
        # 尝试解析 JSON；失败就存原文
        parsed = None
        try:
            parsed = json.loads(result)
        except Exception:
            pass
        summary = "LLM 推理产出"
        if isinstance(parsed, dict):
            if parsed.get("finding"):
                self._create_finding(intent_id, parsed["finding"])
            summary = parsed.get("summary", summary)
        cur = self.db.execute(
            "INSERT INTO exploration_nodes(exploration_id, kind, payload, state, origin, created_at, updated_at, completed_at) "
            "VALUES(?,?,'{}','confirmed','worker',?,?,?)",
            (self.exploration_id, K_FACT, ts, ts, ts),
        )
        fact_id = cur.lastrowid
        self.db.execute(
            "INSERT OR IGNORE INTO exploration_edges(exploration_id, src_id, dst_id, rel, created_at) VALUES(?,?,?,?,?)",
            (self.exploration_id, intent_id, fact_id, "yields", ts),
        )
        self.db.execute(
            "UPDATE exploration_nodes SET payload=? WHERE id=?",
            (json.dumps({"text": summary, "raw": result}, ensure_ascii=False), fact_id),
        )

    # ------------------------------------------------------------------
    # activity 写入
    # ------------------------------------------------------------------
    def _write_activity(
        self, node_id, intent_id, worker, kind, summary="", detail="", tool=None,
        tool_use_id=None, is_error=0, metadata=None, input_tokens=None, output_tokens=None,
    ) -> int:
        ts = now()
        cur = self.db.execute(
            "INSERT INTO activity(exploration_id, node_id, worker, kind, tool, tool_use_id, is_error, summary, detail, "
            "metadata, input_tokens, output_tokens, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.exploration_id, node_id, worker, kind, tool, tool_use_id, is_error,
                summary, detail, json.dumps(metadata or {}, ensure_ascii=False),
                input_tokens, output_tokens, ts,
            ),
        )
        act_id = cur.lastrowid
        event = {
            "seq": act_id, "intent_id": intent_id, "worker": worker, "ts": ts, "kind": kind,
            "tool": tool, "tool_use_id": tool_use_id, "is_error": bool(is_error),
            "summary": summary, "detail": detail, "metadata": metadata or {},
        }
        self.broadcaster.publish(self.task_id, event)
        return act_id

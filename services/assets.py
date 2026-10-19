"""资产服务：企业 / 资产 / 企业范围 / 任务范围 / 资产拦截规则。

对应 server 的 companies.go、assets.go、company_scope.go、asset_dsl.go、
asset_intercept.go 的 API 行为。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import dsl
from .db import DB, get
from .util import jdumps, jloads, norm_key, now, sid


def asset_dto(row) -> dict:
    d = dict(row)
    d["id"] = sid(d["id"])
    d["company_id"] = sid(d.get("company_id"))
    for k in ("task_ids", "bound_domains", "open_ports", "record_value", "technologies",
              "auth", "params"):
        if k in d and isinstance(d[k], str):
            d[k] = jloads(d[k], [])
    d["extra"] = jloads(d.get("extra"), {})
    return d


def company_dto(row, scope: Optional[list] = None, asset_count: int = 0) -> dict:
    d = dict(row)
    d["id"] = sid(d["id"])
    d["scope"] = scope or []
    d["asset_count"] = asset_count
    return d


ASSET_TYPES = ("root_domain", "ip", "subdomain", "app", "service", "endpoint")


class AssetsService:
    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink

    # ------------------------------------------------------------------
    # 企业
    # ------------------------------------------------------------------
    def companies(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM companies ORDER BY id")
        out = []
        for r in rows:
            cnt = self.db.scalar("SELECT COUNT(*) FROM assets WHERE company_id=?", (r["id"],))
            scope = self._company_scope(r["id"])
            out.append(company_dto(r, scope, cnt))
        return out

    def company(self, cid: Any) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM companies WHERE id=?", (int(cid),))
        if not row:
            return None
        cnt = self.db.scalar("SELECT COUNT(*) FROM assets WHERE company_id=?", (row["id"],))
        return company_dto(row, self._company_scope(row["id"]), cnt)

    def _company_scope(self, cid: int) -> list[dict]:
        rows = self.db.query("SELECT * FROM company_scope WHERE company_id=? ORDER BY id", (cid,))
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["company_id"] = sid(d["company_id"])
            out.append(d)
        return out

    def create_company(self, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("企业名称不能为空")
        ts = now()
        nkey = norm_key(name)
        existing = self.db.query_one("SELECT id FROM companies WHERE nkey=?", (nkey,))
        if existing:
            raise ValueError("同名企业已存在")
        cur = self.db.execute(
            "INSERT INTO companies(name, nkey, logo, created_at, updated_at) VALUES(?,?,?,?,?)",
            (name, nkey, data.get("logo") or None, ts, ts),
        )
        cid = cur.lastrowid
        self._replace_scope(cid, data.get("scope") or [])
        if self.logsink:
            self.logsink.info(f"创建企业 {name}", tag="assets")
        return self.company(cid)

    def delete_company(self, cid: Any, delete_assets: bool = False) -> None:
        if delete_assets:
            self.db.execute("DELETE FROM assets WHERE company_id=?", (int(cid),))
        self.db.execute("DELETE FROM companies WHERE id=?", (int(cid),))

    def update_scope(self, cid: Any, scope: list) -> None:
        self._replace_scope(int(cid), scope)

    def _replace_scope(self, cid: int, scope: list) -> None:
        self.db.execute("DELETE FROM company_scope WHERE company_id=?", (cid,))
        ts = now()
        for item in scope:
            kind = item.get("kind") or self._infer_kind(item.get("raw", ""))
            raw = item.get("raw") or ""
            domain = item.get("domain")
            net = item.get("net")
            value = item.get("value")
            if kind == "domain" and not domain:
                domain = raw.strip().lower()
            if kind in ("ip", "cidr") and not net:
                net = raw.strip()
            if kind in ("icp", "keyword") and not value:
                value = raw.strip()
            if not domain and not net and not value:
                continue
            self.db.execute(
                "INSERT INTO company_scope(company_id, kind, domain, net, value, raw, reason, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (cid, kind, domain, net, value, raw, item.get("reason"), ts),
            )

    @staticmethod
    def _infer_kind(raw: str) -> str:
        raw = raw.strip()
        if re.match(r"^[0-9.]+/[0-9]+$", raw) or re.match(r"^[0-9.]+$", raw):
            return "cidr" if "/" in raw else "ip"
        if re.match(r"^([a-z0-9][a-z0-9-]*\.)+[a-z]{2,}$", raw, re.I):
            return "domain"
        return "keyword"

    def reattribute(self) -> dict:
        """重算全量资产归属企业（简化版：按 scope 域名/网段匹配）。"""
        companies = self.db.query("SELECT * FROM companies")
        matched = 0
        for comp in companies:
            scope = self._company_scope(comp["id"])
            for item in scope:
                if item["kind"] == "domain" and item.get("domain"):
                    dom = item["domain"]
                    self.db.execute(
                        "UPDATE assets SET company_id=?, company_source='scope' WHERE "
                        "(domain=? OR root_domain=? OR domain LIKE ? OR root_domain LIKE ?) AND (company_id IS NULL OR company_id=?)",
                        (comp["id"], dom, dom, f"%.{dom}", f"%.{dom}", comp["id"]),
                    )
                    matched += 1
        return {"matched": matched, "total": len(companies)}

    # ------------------------------------------------------------------
    # 资产列表 / 查询
    # ------------------------------------------------------------------
    def list_assets(
        self,
        type_: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        dsl_query: Optional[str] = None,
        task_id: Optional[int] = None,
        company_id: Optional[int] = None,
        scope: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[dict]:
        where = []
        params: list = []
        if type_ and type_ in ASSET_TYPES:
            where.append("type=?")
            params.append(type_)
        if task_id:
            where.append("EXISTS (SELECT 1 FROM json_each(assets.task_ids) WHERE json_each.value=?)")
            params.append(str(task_id))
        if company_id:
            where.append("company_id=?")
            params.append(company_id)
        if q:
            where.append("(domain LIKE ? OR ip LIKE ? OR url LIKE ? OR app_name LIKE ? OR service_name LIKE ?)")
            like = f"%{q}%"
            params.extend([like] * 5)
        sql = "SELECT * FROM assets"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_seen DESC, id DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
        rows = self.db.query(sql, tuple(params))
        items = [asset_dto(r) for r in rows]
        # DSL 后过滤（DSL 复杂语法在 SQL 层难以等价翻译）
        if dsl_query and dsl_query.strip():
            try:
                ast = dsl.parse_dsl(dsl_query)
                items = [a for a in items if dsl.evaluate(ast, a)]
            except dsl.DSLSyntaxError:
                pass
        return items

    def asset_counts(self, task_id: Optional[int] = None) -> list[dict]:
        where, params = "", []
        if task_id:
            where = " WHERE EXISTS (SELECT 1 FROM json_each(assets.task_ids) WHERE json_each.value=?)"
            params = [str(task_id)]
        rows = self.db.query(
            f"SELECT type AS t, COUNT(*) AS c FROM assets{where} GROUP BY type", tuple(params)
        )
        return [{"type": r["t"], "count": r["c"]} for r in rows]

    def get_asset(self, aid: Any) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM assets WHERE id=?", (int(aid),))
        return asset_dto(row) if row else None

    # ------------------------------------------------------------------
    # 登记 / 删除
    # ------------------------------------------------------------------
    def register_asset(self, data: dict, task_id: Optional[int] = None) -> dict:
        """登记资产。data 可为单对象或数组。"""
        items = data if isinstance(data, list) else [data]
        out = []
        for it in items:
            a = self._upsert_one(it, task_id)
            if a:
                out.append(a)
        return out if isinstance(data, list) else (out[0] if out else {})

    def _upsert_one(self, fields: dict, task_id: Optional[int]) -> Optional[dict]:
        typ = fields.get("type")
        if typ not in ASSET_TYPES:
            raise ValueError(f"非法资产类型: {typ}")
        ts = now()
        existing = self._find_by_unique(fields)
        task_ids = jloads(fields.get("task_ids"), [])
        if task_id and task_id not in task_ids:
            task_ids.append(task_id)
        if existing:
            # 更新最后看到时间 + 追加 task
            cur = self.db.execute(
                "UPDATE assets SET last_seen=?, updated_at=?, task_ids=?, company_id=COALESCE(company_id,?), "
                "domain=COALESCE(?,domain), root_domain=COALESCE(?,root_domain), ip=COALESCE(?,ip), "
                "url=COALESCE(?,url), app_name=COALESCE(?,app_name), service_name=COALESCE(?,service_name), "
                "port=COALESCE(?,port), status_code=COALESCE(?,status_code), method=COALESCE(?,method), "
                "technologies=CASE WHEN ? IS NOT NULL THEN ? ELSE technologies END "
                "WHERE id=?",
                (
                    ts, ts, jdumps(task_ids), fields.get("company_id"),
                    fields.get("domain"), fields.get("root_domain"), fields.get("ip"),
                    fields.get("url"), fields.get("app_name"), fields.get("service_name"),
                    fields.get("port"), fields.get("status_code"), fields.get("method"),
                    fields.get("technologies") and jdumps(fields["technologies"]),
                    fields.get("technologies") and jdumps(fields["technologies"]),
                    existing["id"],
                ),
            )
            aid = existing["id"]
        else:
            cols = ["type", "company_id", "company_source", "task_ids", "domain", "root_domain", "ip",
                    "port", "url", "app_name", "category", "service_type", "service_name",
                    "status_code", "content_length", "method", "technologies", "extra",
                    "created_at", "last_seen", "updated_at"]
            vals = [
                typ, fields.get("company_id"), fields.get("company_source") or "explicit",
                jdumps(task_ids), fields.get("domain"), fields.get("root_domain"), fields.get("ip"),
                fields.get("port"), fields.get("url"), fields.get("app_name"), fields.get("category"),
                fields.get("service_type"), fields.get("service_name"),
                fields.get("status_code"), fields.get("content_length"), fields.get("method"),
                jdumps(fields.get("technologies") or []), jdumps(fields.get("extra") or {}),
                ts, ts, ts,
            ]
            cur = self.db.execute(
                f"INSERT INTO assets({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(vals)
            )
            aid = cur.lastrowid
        if task_id:
            self.db.execute(
                "INSERT OR IGNORE INTO task_asset_links(task_id, asset_id, source, source_summary, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, aid, "system", "资产登记", ts, ts),
            )
        return self.get_asset(aid)

    def _find_by_unique(self, fields: dict) -> Optional[Any]:
        typ = fields.get("type")
        if typ == "root_domain":
            return self.db.query_one("SELECT id FROM assets WHERE type='root_domain' AND domain=?", (fields.get("domain"),))
        if typ == "ip":
            return self.db.query_one("SELECT id FROM assets WHERE type='ip' AND ip=?", (fields.get("ip"),))
        if typ == "subdomain":
            return self.db.query_one(
                "SELECT id FROM assets WHERE type='subdomain' AND domain=? AND IFNULL(record_type,'')=?",
                (fields.get("domain"), fields.get("record_type") or ""),
            )
        if typ == "app":
            if fields.get("bundle_id"):
                return self.db.query_one("SELECT id FROM assets WHERE type='app' AND bundle_id=?", (fields.get("bundle_id"),))
            return self.db.query_one("SELECT id FROM assets WHERE type='app' AND app_name=? AND bundle_id IS NULL", (fields.get("app_name"),))
        if typ == "service":
            if fields.get("service_type") == "http":
                return self.db.query_one("SELECT id FROM assets WHERE type='service' AND url=?", (fields.get("url"),))
            return self.db.query_one(
                "SELECT id FROM assets WHERE type='service' AND service_type='other' AND IFNULL(domain,'')=? AND IFNULL(ip,'')=? AND port=? AND service_name=?",
                (fields.get("domain") or "", fields.get("ip") or "", fields.get("port"), fields.get("service_name")),
            )
        if typ == "endpoint":
            return self.db.query_one(
                "SELECT id FROM assets WHERE type='endpoint' AND url=? AND IFNULL(method,'')=?",
                (fields.get("url"), fields.get("method") or ""),
            )
        return None

    def delete_assets(self, ids: list) -> int:
        ids = [int(i) for i in ids]
        cnt = 0
        for i in ids:
            cur = self.db.execute("DELETE FROM assets WHERE id=?", (i,))
            cnt += cur.rowcount
        return cnt

    # ------------------------------------------------------------------
    # 任务资产关联
    # ------------------------------------------------------------------
    def task_assets(self, task_id: int) -> list[dict]:
        rows = self.db.query(
            "SELECT a.* FROM assets a "
            "WHERE EXISTS (SELECT 1 FROM json_each(a.task_ids) WHERE json_each.value=?) "
            "ORDER BY a.last_seen DESC, a.id DESC",
            (str(task_id),),
        )
        return [asset_dto(r) for r in rows]

    def attach_to_task(self, task_id: int, asset_ids: Optional[list] = None,
                       scopes: Optional[list] = None, company_ids: Optional[list] = None) -> int:
        ts = now()
        n = 0
        for aid in (asset_ids or []):
            self.db.execute(
                "UPDATE assets SET task_ids=json_insert(task_ids, '$[' || json_array_length(task_ids) || ']', ?), "
                "last_seen=?, updated_at=? WHERE id=?",
                (str(task_id), ts, ts, int(aid)),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO task_asset_links(task_id, asset_id, source, source_summary, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, int(aid), "manual", "人工挂载", ts, ts),
            )
            n += 1
        # scope/company 关联
        for cid in (company_ids or []):
            rows = self.db.query("SELECT id FROM assets WHERE company_id=?", (int(cid),))
            for r in rows:
                self._append_task_to_asset(task_id, r["id"], ts)
                n += 1
        for s in (scopes or []):
            kind = s.get("kind")
            val = s.get("value") or s.get("domain") or s.get("net")
            if not val:
                continue
            self.db.execute(
                "INSERT OR IGNORE INTO task_scope(task_id, kind, domain, net, value, source, reason, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_id, kind, s.get("domain"), s.get("net"), s.get("value"), "manual", s.get("reason"), ts),
            )
            if kind == "root_domain":
                rows = self.db.query("SELECT id FROM assets WHERE root_domain=? OR domain=?", (val, val))
            elif kind == "company":
                rows = self.db.query("SELECT id FROM assets WHERE company_id=?", (int(s.get("company_id") or 0),))
            else:
                rows = []
            for r in rows:
                self._append_task_to_asset(task_id, r["id"], ts)
                n += 1
        return n

    def _append_task_to_asset(self, task_id: int, asset_id: int, ts: str) -> None:
        cur = self.db.execute(
            "SELECT task_ids FROM assets WHERE id=?", (asset_id,)
        )
        row = cur.fetchone()
        task_ids = jloads(row["task_ids"], []) if row else []
        if str(task_id) not in task_ids:
            task_ids.append(task_id)
            self.db.execute(
                "UPDATE assets SET task_ids=?, last_seen=?, updated_at=? WHERE id=?",
                (jdumps(task_ids), ts, ts, asset_id),
            )
        self.db.execute(
            "INSERT OR IGNORE INTO task_asset_links(task_id, asset_id, source, source_summary, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (task_id, asset_id, "system", "范围关联", ts, ts),
        )

    def detach_from_task(self, task_id: int, asset_id: int) -> None:
        row = self.db.query_one("SELECT task_ids FROM assets WHERE id=?", (asset_id,))
        if not row:
            return
        task_ids = [t for t in jloads(row["task_ids"], []) if str(t) != str(task_id)]
        self.db.execute(
            "UPDATE assets SET task_ids=?, updated_at=? WHERE id=?",
            (jdumps(task_ids), now(), asset_id),
        )
        self.db.execute("DELETE FROM task_asset_links WHERE task_id=? AND asset_id=?", (task_id, asset_id))

    # ------------------------------------------------------------------
    # 资产拦截规则（全局黑名单）
    # ------------------------------------------------------------------
    def intercept_rules(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM asset_intercept_rules ORDER BY id")
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["enabled"] = bool(d["enabled"])
            d["builtin"] = bool(d["builtin"])
            out.append(d)
        return out

    def save_intercept_rule(self, data: dict) -> dict:
        ts = now()
        rid = data.get("id")
        if rid:
            self.db.execute(
                "UPDATE asset_intercept_rules SET enabled=?, kind=?, pattern=?, note=?, updated_at=? WHERE id=?",
                (1 if data.get("enabled", True) else 0, data.get("kind"), data.get("pattern"),
                 data.get("note", ""), ts, int(rid)),
            )
            new_id = int(rid)
        else:
            cur = self.db.execute(
                "INSERT INTO asset_intercept_rules(enabled, kind, pattern, note, builtin, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (1 if data.get("enabled", True) else 0, data.get("kind"), data.get("pattern"),
                 data.get("note", ""), 1 if data.get("builtin") else 0, ts, ts),
            )
            new_id = cur.lastrowid
        return self._intercept_rule(new_id)

    def _intercept_rule(self, rid: int) -> dict:
        row = self.db.query_one("SELECT * FROM asset_intercept_rules WHERE id=?", (rid,))
        d = dict(row)
        d["id"] = sid(d["id"])
        d["enabled"] = bool(d["enabled"])
        d["builtin"] = bool(d["builtin"])
        return d

    def toggle_intercept_rule(self, rid: int) -> dict:
        row = self.db.query_one("SELECT enabled FROM asset_intercept_rules WHERE id=?", (rid,))
        if row:
            self.db.execute(
                "UPDATE asset_intercept_rules SET enabled=?, updated_at=? WHERE id=?",
                (0 if row["enabled"] else 1, now(), rid),
            )
        return self._intercept_rule(rid)

    def delete_intercept_rule(self, rid: int) -> None:
        self.db.execute("DELETE FROM asset_intercept_rules WHERE id=?", (rid,))

    # ------------------------------------------------------------------
    # 资产匹配（任务级拦截 / 覆盖度等共用）
    # ------------------------------------------------------------------
    def match_blocked(self, url: str, task_id: Optional[int] = None) -> Optional[dict]:
        """按全局 + 任务级规则判断 url 是否被拦截。返回 {blocked, reason}。"""
        host = self._url_host(url)
        rules = self.intercept_rules()
        for r in rules:
            if not r["enabled"]:
                continue
            if self._rule_matches(r, url, host):
                return {"blocked": True, "reason": f"命中资产拦截规则 #{r['id']}", "rule": r}
        if task_id:
            rows = self.db.query(
                "SELECT * FROM task_intercept_rules WHERE task_id=? AND enabled=1", (task_id,)
            )
            for r in rows:
                d = dict(r)
                d["id"] = sid(d["id"])
                if self._rule_matches(d, url, host) and d.get("action") == "block":
                    return {"blocked": True, "reason": f"命中任务级拦截规则 #{r['id']}", "rule": d}
            # allow 白名单模式：存在启用 allow 规则时必须命中才放行
            allows = self.db.query(
                "SELECT * FROM task_intercept_rules WHERE task_id=? AND enabled=1 AND action='allow'", (task_id,)
            )
            if allows:
                for r in allows:
                    d = dict(r)
                    d["id"] = sid(d["id"])
                    if self._rule_matches(d, url, host):
                        return None
                return {"blocked": True, "reason": "未命中任务 allow 白名单", "rule": None}
        return None

    @staticmethod
    def _url_host(url: str) -> str:
        m = re.match(r"^[a-z]+://([^/:?#]+)", url or "", re.I)
        return (m.group(1).lower() if m else (url or "").split("/")[0].lower())

    @staticmethod
    def _rule_matches(rule: dict, url: str, host: str) -> bool:
        kind = rule.get("kind")
        pattern = (rule.get("pattern") or "").strip().lower()
        if not pattern:
            return False
        if kind == "exact_domain":
            return host == pattern
        if kind == "fuzzy_domain":
            return pattern in host
        if kind == "exact_ip":
            return host == pattern
        if kind == "fuzzy_ip":
            return pattern in host
        if kind == "exact_url":
            return (url or "").lower() == pattern
        if kind == "fuzzy_url":
            return pattern in (url or "").lower()
        if kind == "cidr":
            return AssetsService._cidr_contains(pattern, host)
        return False

    @staticmethod
    def _cidr_contains(cidr: str, host: str) -> bool:
        import ipaddress
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            ip = ipaddress.ip_address(host.split("%")[0])
            return ip in net
        except Exception:
            return False

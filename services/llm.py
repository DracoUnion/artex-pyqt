"""LLM 服务：profile 管理 / 池化轮询 / 调用 / token 记账。

对接 OpenAI 兼容 / Anthropic 两种格式。本实现聚焦 API 面所需的
管理能力 + 引擎调用的 complete()，并记录 token 用量(llm_usage 等价物)。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

import requests

from .db import DB, get
from .util import now, sid


class LLMService:
    def __init__(self, db: DB, logsink=None):
        self.db = db
        self.logsink = logsink
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    def profiles(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM llm_profiles ORDER BY is_default DESC, priority DESC, id")
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = sid(d["id"])
            d["is_default"] = bool(d["is_default"])
            d["pool_exclude"] = bool(d["pool_exclude"])
            d["streaming"] = bool(d["streaming"])
            out.append(d)
        return out

    def active_profile(self) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM llm_profiles WHERE is_default=1 LIMIT 1")
        return dict(row) if row else None

    def available(self) -> bool:
        p = self.active_profile()
        return bool(p and p.get("api_key"))

    def get_profile(self, pid: Any) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM llm_profiles WHERE id=?", (int(pid),))
        return dict(row) if row else None

    def profile_by_agent_binding(self, profile_id: Any) -> Optional[dict]:
        if profile_id:
            return self.get_profile(profile_id)
        return self.active_profile()

    # ------------------------------------------------------------------
    # 配置管理
    # ------------------------------------------------------------------
    def save_profile(self, data: dict) -> dict:
        """新建或更新 profile。data 含 id 则更新。"""
        api_key = data.get("api_key", "")
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("配置名称不能为空")
        ts = now()
        existing = None
        pid = data.get("id")
        if pid:
            existing = self.get_profile(pid)
            if not existing:
                raise ValueError("配置不存在")
        # 默认值
        model = data.get("model") or "gpt-4o-mini"
        base_url = data.get("base_url") or ""
        api_key_hint = data.get("api_key_hint") or ""
        if api_key and (not existing or existing.get("api_key") != api_key):
            api_key_hint = api_key[:8] + "..."
        if not api_key and existing:
            api_key = existing.get("api_key") or ""
        is_default = 1 if data.get("is_default") else 0
        if is_default:
            self.db.execute("UPDATE llm_profiles SET is_default=0 WHERE is_default=1")

        fields = dict(
            name=name, format=data.get("format") or "openai", base_url=base_url or None,
            proxy=data.get("proxy") or None, model=model, api_key=api_key or None,
            api_key_hint=api_key_hint, rate_per_second=float(data.get("rate_per_second") or 0),
            rate_per_minute=float(data.get("rate_per_minute") or 0),
            context_window_k=int(data.get("context_window_k") or 0),
            reasoning_effort=data.get("reasoning_effort") or "",
            thinking_type=data.get("thinking_type") or "",
            is_default=is_default, priority=int(data.get("priority") or 0),
            pool_exclude=1 if data.get("pool_exclude") else 0,
            streaming=1 if data.get("streaming", True) else 0,
            max_tokens=int(data.get("max_tokens") or 0),
            max_tokens_field=data.get("max_tokens_field") or "",
            session_header_key=data.get("session_header_key") or "",
            retry_connect_attempts=int(data.get("retry_connect_attempts") or 0),
            retry_connect_interval_ms=int(data.get("retry_connect_interval_ms") or 0),
            retry_empty_attempts=int(data.get("retry_empty_attempts") or 0),
            retry_empty_interval_ms=int(data.get("retry_empty_interval_ms") or 0),
            retry_stream_attempts=int(data.get("retry_stream_attempts") or 0),
            retry_stream_interval_ms=int(data.get("retry_stream_interval_ms") or 0),
        )
        if existing:
            cols = ", ".join(f"{k}=?" for k in fields)
            self.db.execute(
                f"UPDATE llm_profiles SET {cols}, updated_at=? WHERE id=?",
                tuple(fields.values()) + (ts, int(pid)),
            )
            new_id = int(pid)
        else:
            cols = ", ".join(fields.keys())
            ph = ", ".join("?" * len(fields))
            cur = self.db.execute(
                f"INSERT INTO llm_profiles({cols}, created_at, updated_at) VALUES({ph},?,?)",
                tuple(fields.values()) + (ts, ts),
            )
            new_id = cur.lastrowid
        if self.logsink:
            self.logsink.info(f"LLM 配置 {name} 已保存", tag="llm")
        return self.get_profile(new_id)

    def delete_profile(self, pid: Any) -> None:
        self.db.execute("DELETE FROM llm_profile_health WHERE profile_id=?", (int(pid),))
        self.db.execute("DELETE FROM llm_profiles WHERE id=?", (int(pid),))

    def set_active(self, pid: Any) -> None:
        self.db.execute("UPDATE llm_profiles SET is_default=0 WHERE is_default=1")
        self.db.execute("UPDATE llm_profiles SET is_default=1 WHERE id=?", (int(pid),))

    # ------------------------------------------------------------------
    # 重试策略 / 池状态
    # ------------------------------------------------------------------
    def retry_policy(self) -> dict:
        rows = self.db.query(
            "SELECT key, value FROM settings WHERE key LIKE 'retry_%'"
        )
        return {r["key"]: r["value"] for r in rows}

    def save_retry_policy(self, data: dict) -> None:
        ts = now()
        for k in ("connect", "empty", "stream"):
            attempts = data.get(f"{k}_attempts")
            interval = data.get(f"{k}_interval_ms")
            if attempts is not None:
                self.db.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (f"retry_{k}_attempts", str(attempts), ts),
                )
            if interval is not None:
                self.db.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (f"retry_{k}_interval_ms", str(interval), ts),
                )

    def pool_status(self) -> dict:
        profiles = self.profiles()
        by_id = {p["id"]: p for p in profiles}
        health = self.db.query("SELECT * FROM llm_profile_health")
        now_ts = time.time()
        items = []
        for h in health:
            p = by_id.get(str(h["profile_id"]))
            open_until = h["open_until"]
            tripped = False
            if open_until:
                try:
                    tripped = time.mktime(time.strptime(open_until, "%Y-%m-%dT%H:%M:%S")) > now_ts
                except Exception:
                    pass
            items.append({
                "profile_id": str(h["profile_id"]), "name": p["name"] if p else "",
                "fails": h["fails"], "trips": h["trips"], "open_until": h["open_until"],
                "last_error": h["last_error"], "tripped": tripped,
            })
        return {"profiles": [p["id"] for p in profiles], "health": items}

    def reset_pool(self) -> None:
        self.db.execute("DELETE FROM llm_profile_health")

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------
    def test(self, data: dict) -> dict:
        profile_id = data.get("profile_id")
        cfg = None
        if profile_id:
            cfg = self.get_profile(profile_id)
        if not cfg:
            cfg = dict(
                name=data.get("name", "test"), format=data.get("format", "openai"),
                base_url=data.get("base_url") or "", model=data.get("model", "gpt-4o-mini"),
                api_key=data.get("api_key") or "", proxy=data.get("proxy") or "",
                max_tokens=int(data.get("max_tokens") or 16),
            )
        try:
            resp = self._chat_completion(cfg, "ping", max_tokens=8, timeout=30)
            return {"ok": True, "message": "连接成功", "sample": resp.get("content", "")[:120]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_models(self, data: dict) -> dict:
        """拉取 OpenAI 兼容 /models 列表。"""
        base_url = (data.get("base_url") or "").rstrip("/")
        api_key = data.get("api_key") or ""
        proxy = data.get("proxy") or None
        if not base_url or not api_key:
            return {"models": []}
        url = f"{base_url}/models"
        try:
            resp = requests.get(
                url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30,
                proxies={"http": proxy, "https": proxy} if proxy else None,
            )
            if resp.status_code == 200:
                data0 = resp.json()
                ids = [m.get("id") for m in data0.get("data", []) if m.get("id")]
                return {"models": ids}
            return {"models": [], "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"models": [], "error": str(e)}

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    def complete(self, prompt: str, profile: Optional[dict] = None, max_tokens: int = 0,
                 session_id: str = "", model: Optional[str] = None, timeout: int = 120) -> str:
        """引擎 worker 调用：返回文本。"""
        cfg = profile or self.active_profile()
        if not cfg or not cfg.get("api_key"):
            raise RuntimeError("未配置可用 LLM")
        try:
            resp = self._chat_completion(cfg, prompt, max_tokens=max_tokens, timeout=timeout,
                                         session_id=session_id, model=model)
        except Exception as e:
            self._note_failure(cfg, str(e))
            raise
        self._note_success(cfg)
        return resp.get("content", "")

    def _chat_completion(self, cfg: dict, prompt: str, max_tokens: int = 0,
                         timeout: int = 120, session_id: str = "", model: Optional[str] = None) -> dict:
        """同步非流式调用。返回 {content, input_tokens, output_tokens}。"""
        fmt = cfg.get("format") or "openai"
        api_key = cfg.get("api_key") or ""
        base_url = (cfg.get("base_url") or "").rstrip("/")
        proxy = cfg.get("proxy") or None
        mdl = model or cfg.get("model") or "gpt-4o-mini"
        mt = max_tokens or int(cfg.get("max_tokens") or 0)
        headers = {"Authorization": f"Bearer {api_key}"}
        if session_id and cfg.get("session_header_key"):
            headers[cfg["session_header_key"]] = session_id
        proxies = {"http": proxy, "https": proxy} if proxy else None
        request_id = f"{int(time.time()*1000)}-{threading.get_ident()}"

        if fmt == "anthropic":
            url = f"{base_url or 'https://api.anthropic.com'}/v1/messages"
            body: dict[str, Any] = {
                "model": mdl,
                "max_tokens": mt or 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
            if cfg.get("thinking_type") == "enabled":
                body["thinking"] = {"type": "enabled", "budget_tokens": 4096}
            resp = requests.post(url, json=body, headers=headers, timeout=timeout, proxies=proxies)
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic {resp.status_code}: {resp.text[:200]}")
            data0 = resp.json()
            content = "".join(b.get("text", "") for b in data0.get("content", []) if b.get("type") == "text")
            usage = data0.get("usage", {})
            in_t = usage.get("input_tokens", 0); out_t = usage.get("output_tokens", 0)
        else:  # openai / openai-responses
            if fmt == "openai-responses":
                url = f"{base_url or 'https://api.openai.com/v1'}/responses"
                body = {
                    "model": mdl,
                    "input": prompt,
                    "max_output_tokens": mt or 1024,
                }
                resp = requests.post(url, json=body, headers=headers, timeout=timeout, proxies=proxies)
                if resp.status_code != 200:
                    raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:200]}")
                data0 = resp.json()
                content = "".join(
                    item.get("text", "")
                    for item in data0.get("output", [])
                    if item.get("type") == "message"
                )
                usage = data0.get("usage", {})
                in_t = usage.get("input_tokens", 0); out_t = usage.get("output_tokens", 0)
            else:
                url = f"{base_url or 'https://api.openai.com/v1'}/chat/completions"
                body = {
                    "model": mdl,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if mt:
                    field = "max_completion_tokens" if cfg.get("max_tokens_field") == "max_completion_tokens" else "max_tokens"
                    body[field] = mt
                if cfg.get("thinking_type") == "enabled":
                    body["reasoning_effort"] = cfg.get("reasoning_effort") or "medium"
                resp = requests.post(url, json=body, headers=headers, timeout=timeout, proxies=proxies)
                if resp.status_code != 200:
                    raise RuntimeError(f"OpenAI {resp.status_code}: {resp.text[:200]}")
                data0 = resp.json()
                content = data0.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                usage = data0.get("usage", {})
                in_t = usage.get("prompt_tokens", 0); out_t = usage.get("completion_tokens", 0)
        self._record_usage(request_id, mdl, in_t, out_t, session_id)
        return {"content": content, "input_tokens": in_t, "output_tokens": out_t}

    # ------------------------------------------------------------------
    # token 记账（llm_usage 等价物）
    # ------------------------------------------------------------------
    def _record_usage(self, request_id: str, model: str, in_t: int, out_t: int, session_id: str) -> None:
        try:
            self.db.execute(
                "INSERT INTO llm_usage(id, model, session_id, input_tokens, output_tokens, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (request_id, model, session_id or "", int(in_t or 0), int(out_t or 0), now()),
            )
        except Exception:
            pass

    def _note_success(self, cfg: dict) -> None:
        pid = cfg.get("id")
        if pid:
            self.db.execute(
                "DELETE FROM llm_profile_health WHERE profile_id=?", (int(pid),)
            )

    def _note_failure(self, cfg: dict, err: str) -> None:
        pid = cfg.get("id")
        if not pid:
            return
        ts = now()
        row = self.db.query_one("SELECT * FROM llm_profile_health WHERE profile_id=?", (int(pid),))
        fails = 1
        trips = 0
        open_until = None
        if row:
            fails = row["fails"] + 1
            trips = row["trips"]
            if fails >= 3:
                trips += 1
                open_until = time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.localtime(time.time() + min(3600, 60 * 2 ** trips)),
                )
                fails = 0
        self.db.execute(
            "INSERT INTO llm_profile_health(profile_id, fails, trips, open_until, last_error, last_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(profile_id) DO UPDATE SET fails=excluded.fails, trips=excluded.trips, "
            "open_until=excluded.open_until, last_error=excluded.last_error, last_at=excluded.last_at",
            (int(pid), fails, trips, open_until, err[:300], ts),
        )

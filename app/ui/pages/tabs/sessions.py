"""会话 Tab：任务控制台（会话树 + Transcript + 实时流 + 发送）。"""
from __future__ import annotations

import html
import json
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from ...api_client import ApiError, SseManager
from ...theme import COL
from ...widgets import fmt_time
from ..base import BasePage

KIND_STYLES = {
    "user": ("#e2e8f0", ""),
    "answer": ("#34d399", ""),
    "reasoning": ("#8ea0b0", "font-style: italic;"),
    "tool_call": ("#22c3ee", ""),
    "tool_result": ("#60a5fa", ""),
    "finding": ("#f87171", ""),
    "session_start": ("#8ea0b0", ""),
    "session_end": ("#8ea0b0", ""),
    "error": ("#f87171", "font-weight: 600;"),
}


def render_activity_html(act: dict) -> str:
    kind = act.get("kind", "")
    color, extra = KIND_STYLES.get(kind, ("#e2e8f0", ""))
    worker = act.get("worker") or ""
    summary = act.get("summary") or ""
    ts = fmt_time(act.get("ts"))
    tool = act.get("tool") or ""
    parts = [f"<span style='color:#5b6b7a'>{html.escape(ts)}</span>"]
    if worker:
        parts.append(f"<b style='color:{color}'>{html.escape(worker)}</b>")
    if tool:
        parts.append(f"<span style='color:{COL['accent']}'>[{html.escape(tool)}]</span>")
    body = html.escape(summary)
    detail = act.get("detail") or ""
    if detail:
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, dict):
                body += " " + html.escape(json.dumps(parsed, ensure_ascii=False)[:400])
            else:
                body += " " + html.escape(str(parsed)[:400])
        except Exception:
            pass
    return f"<div style='margin:6px 0'>{' '.join(parts)}<div style='color:{color};{extra}'>{body}</div></div>"


class SessionsTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._selected: Optional[str] = None  # 'main' | 'plan' | 'intent:<id>'
        self._cursor = 0
        self._sse: Optional[SseManager] = None
        self._build()

        self._list_timer = QTimer(self)
        self._list_timer.timeout.connect(self.refresh_sessions)
        self._list_timer.start(5000)

    def _build(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        # 左：会话树
        self.session_list = QListWidget()
        self.session_list.setFixedWidth(230)
        self.session_list.itemClicked.connect(self._select_session)
        splitter.addWidget(self.session_list)

        # 右：Transcript
        right = QWidget()
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        f = QFont("Consolas, Microsoft YaHei")
        f.setPointSize(9)
        self.transcript.setFont(f)
        lay.addWidget(self.transcript, 1)

        input_row = QHBoxLayout()
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("向该会话发送消息…")
        self.msg_input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self._send)
        input_row.addWidget(self.msg_input)
        input_row.addWidget(self.send_btn)
        lay.addLayout(input_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        lay2 = QVBoxLayout(self)
        lay2.setContentsMargins(0, 0, 0, 0)
        lay2.addWidget(splitter)

    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        self.refresh_sessions()
        self._connect_sse()

    def _connect_sse(self) -> None:
        if self._sse:
            self._sse.shutdown()
        self._sse = SseManager(self.client, self)
        worker = self._sse.subscribe(
            f"task-{self.tid}", f"/api/exploration/activity/stream?task={self.tid}",
        )
        worker.event.connect(self._on_sse)
        worker.status.connect(lambda s: None)

    def _on_sse(self, ev: dict) -> None:
        if ev.get("type") == "snapshot":
            items = ev.get("items") or []
            if items:
                self._append_items(items)
                self._cursor = max(self._cursor, items[-1]["seq"])
            return
        if ev.get("type") == "task_status":
            self.dlg._refresh_header()
            return
        self._append_items([ev])
        if ev.get("seq"):
            self._cursor = max(self._cursor, ev["seq"])

    def _append_items(self, items: list) -> None:
        if not self._selected:
            return
        sel = self._selected
        for act in items:
            intent = act.get("intent_id")
            if sel == "main" and act.get("worker") != "mainagent":
                continue
            if sel == "plan" and act.get("worker") not in ("planner",):
                continue
            if sel.startswith("intent:") and str(intent) != sel.split(":", 1)[1]:
                continue
            self.transcript.append(render_activity_html(act))
            self.transcript.moveCursor(QTextCursor.End)

    # ------------------------------------------------------------------
    def refresh_sessions(self) -> None:
        try:
            intents = self.client.get("/api/exploration/intents", params={"task": self.tid})
        except ApiError:
            return
        if isinstance(intents, dict):
            intents = intents.get("items", [])
        self.session_list.blockSignals(True)
        self.session_list.clear()
        self._session_items = {}
        self._add_session_item("主Agent会话", "main")
        self._add_session_item("规划者", "plan")
        for it in intents:
            state = it.get("state", "")
            marker = {"running": "🟢", "paused": "🟡", "done": "✅", "open": "⚪",
                      "blocked": "🔴", "stopped": "⏹", "exhausted": "⛔"}.get(state, "•")
            label = f"{marker} 意图 #{it['id']}"
            self._add_session_item(label, f"intent:{it['id']}")
        self.session_list.blockSignals(False)
        # 保持选中
        if self._selected:
            for i in range(self.session_list.count()):
                if self.session_list.item(i).data(Qt.UserRole) == self._selected:
                    self.session_list.setCurrentRow(i)

    def _add_session_item(self, text: str, key: str) -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, key)
        self.session_list.addItem(item)

    def _select_session(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        if key == self._selected:
            return
        self._selected = key
        self.transcript.clear()
        self._cursor = 0
        self._load_history(key)

    def _load_history(self, session: str) -> None:
        try:
            r = self.client.get("/api/exploration/activity/history",
                                params={"task": self.tid, "session": session, "limit": 100})
        except ApiError:
            return
        items = r.get("items", [])
        # 正向顺序渲染
        for act in sorted(items, key=lambda x: x["seq"]):
            self.transcript.append(render_activity_html(act))
        self.transcript.moveCursor(QTextCursor.End)
        # 再拉增量到最新
        try:
            r2 = self.client.get("/api/exploration/activity", params={"task": self.tid})
            all_items = r2.get("items", [])
        except ApiError:
            all_items = []
        tail = []
        for act in all_items:
            if act["seq"] > (max((x["seq"] for x in items), default=0)):
                tail.append(act)
        self._append_items(tail)
        for act in all_items:
            self._cursor = max(self._cursor, act["seq"])

    # ------------------------------------------------------------------
    def _send(self) -> None:
        text = self.msg_input.text().strip()
        if not text or not self._selected:
            return
        try:
            if self._selected.startswith("intent:"):
                iid = self._selected.split(":", 1)[1]
                self.client.post(f"/api/tasks/{self.tid}/intents/{iid}/messages",
                                 json={"message": text})
            else:
                self.client.post(f"/api/chat?task={self.tid}", json={"message": text})
        except ApiError as e:
            self.dlg.toast(e.message, COL["danger"])
            return
        self.msg_input.clear()
        self._append_items([{"seq": 0, "intent_id": None, "worker": "user", "ts": "",
                             "kind": "user", "summary": text}])

    def stop(self) -> None:
        if self._sse:
            self._sse.shutdown()
        self._list_timer.stop()

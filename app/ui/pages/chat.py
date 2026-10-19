"""对话页：会话列表 + 消息视图（@提及 / /btw 旁路）。"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSplitter, QTextBrowser, QVBoxLayout, QWidget, QDialog, QComboBox, QFormLayout,
    QMessageBox,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, fmt_time, msg_label
from .base import BasePage


class ChatPage(BasePage):
    def title_text(self) -> str:
        return "对话"

    def _build(self) -> None:
        super()._build()
        self.toolbar_w = None
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        # 左：会话列表
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        self.agent_filter = QComboBox()
        self.agent_filter.addItem("全部 Agent", None)
        try:
            for a in self.client.get("/api/agents"):
                self.agent_filter.addItem(a["name"], a["key"])
        except ApiError:
            pass
        self.agent_filter.currentIndexChanged.connect(self.refresh_conversations)
        self.new_btn = button("＋ 新对话", primary=True)
        self.new_btn.clicked.connect(self._new_conversation)
        top = QHBoxLayout()
        top.addWidget(self.agent_filter)
        top.addWidget(self.new_btn)
        lay.addLayout(top)
        self.conv_list = QListWidget()
        self.conv_list.setMinimumWidth(230)
        self.conv_list.itemClicked.connect(self._select_conv)
        lay.addWidget(self.conv_list)
        splitter.addWidget(left)

        # 右：消息视图
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        self.header_lbl = QLabel("选择一个会话开始对话")
        self.header_lbl.setStyleSheet("font-size: 15px; font-weight: 600;")
        rlay.addWidget(self.header_lbl)
        self.messages = QTextBrowser()
        self.messages.setOpenExternalLinks(False)
        f = QFont("Microsoft YaHei")
        f.setPointSize(10)
        self.messages.setFont(f)
        rlay.addWidget(self.messages, 1)
        input_row = QHBoxLayout()
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("输入消息…（@ 提及 / /btw 旁路提问）")
        self.msg_input.returnPressed.connect(self._send)
        self.send_btn = button("发送", primary=True)
        self.send_btn.clicked.connect(self._send)
        self.side_btn = button("/btw")
        self.side_btn.clicked.connect(self._side_question)
        input_row.addWidget(self.msg_input)
        input_row.addWidget(self.side_btn)
        input_row.addWidget(self.send_btn)
        rlay.addLayout(input_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        self.add_to_content(splitter)

        self._current_conv: Optional[dict] = None
        self._cursor = 0
        self._list_timer = QTimer(self)
        self._list_timer.timeout.connect(self.refresh_conversations)
        self._list_timer.start(4000)
        self._msg_timer = QTimer(self)
        self._msg_timer.timeout.connect(self._poll_messages)
        self._msg_timer.start(1200)
        self.refresh_conversations()

    def on_shown(self) -> None:
        self.refresh_conversations()

    # ---- 会话列表 -----------------------------------------------------
    def refresh_conversations(self) -> None:
        agent = self.agent_filter.currentData()
        params = {"agent": agent} if agent else None
        try:
            convs = self.client.get("/api/conversations", params=params)
        except ApiError:
            return
        self.conv_list.blockSignals(True)
        self.conv_list.clear()
        for c in convs:
            title = c.get("title") or c.get("agent_key") or f"会话 #{c['id']}"
            running = " 🟢" if c.get("running") else ""
            item = QListWidgetItem(f"{title}{running}")
            item.setData(Qt.UserRole, c["id"])
            self.conv_list.addItem(item)
        self.conv_list.blockSignals(False)
        if self._current_conv:
            for i in range(self.conv_list.count()):
                if self.conv_list.item(i).data(Qt.UserRole) == self._current_conv["id"]:
                    self.conv_list.setCurrentRow(i)

    def _select_conv(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.UserRole)
        try:
            conv = self.client.get(f"/api/conversations/{cid}")
        except ApiError:
            return
        self._current_conv = conv
        self.header_lbl.setText(conv.get("title") or f"会话 #{cid}")
        self.messages.clear()
        self._cursor = 0
        self._poll_messages()

    def _poll_messages(self) -> None:
        if not self._current_conv:
            return
        try:
            r = self.client.get(f"/api/conversations/{self._current_conv['id']}/messages",
                                params={"since": self._cursor})
        except ApiError:
            return
        items = r.get("items", [])
        if items:
            for m in items:
                self.messages.append(self._msg_html(m))
            self._cursor = max(self._cursor, items[-1]["seq"])
            self.messages.moveCursor(QTextCursor.End)

    def _msg_html(self, m: dict) -> str:
        kind = m.get("kind", "")
        worker = m.get("worker") or ""
        summary = html.escape(m.get("summary") or "")
        ts = fmt_time(m.get("ts"))
        if kind == "user":
            return (f"<div style='margin:10px 0; text-align:right'>"
                    f"<span style='background:{COL['accent_dim']}44; padding:6px 12px; border-radius:12px'>{summary}</span>"
                    f"<div style='color:#5b6b7a; font-size:11px; margin-top:2px'>{ts} 我</div></div>")
        color = COL["ok"] if kind in ("answer", "result") else COL["text"]
        return (f"<div style='margin:10px 0'>"
                f"<b style='color:{COL['accent']}'>{html.escape(worker)}</b> "
                f"<span style='color:#5b6b7a; font-size:11px'>{ts}</span>"
                f"<div style='color:{color}'>{summary}</div></div>")

    # ---- 发送 ---------------------------------------------------------
    def _send(self) -> None:
        if not self._current_conv:
            return
        text = self.msg_input.text().strip()
        if not text:
            return
        if text.startswith("/btw"):
            self._side_question(text)
            return
        try:
            self.client.post(f"/api/conversations/{self._current_conv['id']}/messages",
                             json={"message": text})
        except ApiError as e:
            QMessageBox.warning(self, "发送失败", e.message)
            return
        self.msg_input.clear()

    def _new_conversation(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("新建对话")
        dlg.setFixedWidth(420)
        lay = QFormLayout(dlg)
        self._agent_pick = QComboBox()
        self._llm_pick = QComboBox()
        try:
            for a in self.client.get("/api/agents"):
                self._agent_pick.addItem(a["name"], a["key"])
        except ApiError:
            pass
        self._llm_pick.addItem("默认配置", None)
        try:
            for p in self.client.get("/api/llm/profiles"):
                self._llm_pick.addItem(f"{p['name']} ({p['model']})", p["id"])
        except ApiError:
            pass
        lay.addRow("Agent", self._agent_pick)
        lay.addRow("LLM 配置", self._llm_pick)
        btns = QHBoxLayout()
        ok = button("创建", primary=True)
        ok.clicked.connect(dlg.accept)
        cancel = button("取消")
        cancel.clicked.connect(dlg.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addRow(btns)
        if dlg.exec() != QDialog.Accepted:
            return
        conv = self.client.post("/api/conversations", json={
            "agent_key": self._agent_pick.currentData() or "assistant",
            "llm_profile_id": self._llm_pick.currentData(),
        })
        self.refresh_conversations()
        self._select_conv_item_by_id(conv["id"])

    def _select_conv_item_by_id(self, cid: str) -> None:
        for i in range(self.conv_list.count()):
            if self.conv_list.item(i).data(Qt.UserRole) == cid:
                self.conv_list.setCurrentRow(i)
                self._select_conv(self.conv_list.item(i))

    def _side_question(self, text: str = "") -> None:
        if not self._current_conv:
            return
        question = text[5:].strip() if text.startswith("/btw") else ""
        dlg = QDialog(self)
        dlg.setWindowTitle("旁路提问 /btw")
        dlg.setFixedWidth(460)
        lay = QVBoxLayout(dlg)
        inp = QLineEdit(question)
        inp.setPlaceholderText("输入旁路问题…")
        lay.addWidget(inp)
        result = QTextBrowser()
        lay.addWidget(result)
        btns = QHBoxLayout()
        ok = button("提问", primary=True)
        cancel = button("关闭")
        cancel.clicked.connect(dlg.accept)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        def ask():
            q = inp.text().strip()
            if not q:
                return
            try:
                r = self.client.post(
                    f"/api/conversations/{self._current_conv['id']}/side-questions",
                    json={"question": q})
                req_id = r.get("request_id", "")
                result.append("旁路提问已提交，请稍候…")
                self._poll_side(req_id, result, dlg)
            except ApiError as e:
                result.append(f"提交失败: {e.message}")
        ok.clicked.connect(ask)
        dlg.exec()

    def _poll_side(self, req_id: str, result: QTextBrowser, dlg: QDialog) -> None:
        from PySide6.QtCore import QTimer as _T
        timer = _T(dlg)
        timer.setInterval(1500)
        timer.timeout.connect(lambda: self._side_poll_once(req_id, result, timer))
        timer.start()

    def _side_poll_once(self, req_id: str, result: QTextBrowser, timer) -> None:
        try:
            r = self.client.get(f"/api/side-questions/{req_id}")
        except ApiError:
            return
        status = r.get("status")
        if status in ("completed", "failed", "cancelled"):
            timer.stop()
            if status == "completed":
                result.append(f"<div style='color:{COL['ok']}'>{html.escape(r.get('answer', ''))}</div>")
            else:
                result.append(f"<div style='color:{COL['danger']}'>{(r.get('error') or status)}</div>")

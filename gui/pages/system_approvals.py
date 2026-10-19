"""审批记录页：待审批队列 + 历史记录 + 决策操作。"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, fmt_time, table_item
from .base import BasePage


class SystemApprovalsPage(BasePage):
    def title_text(self) -> str:
        return "审批记录"

    def _build(self) -> None:
        super()._build()
        self.tabs = QTabWidget()
        self.add_to_content(self.tabs)

        self.pending_tab = QWidget()
        self.history_tab = QWidget()
        self.tabs.addTab(self.pending_tab, "待审批")
        self.tabs.addTab(self.history_tab, "历史")

        # 待审批
        lay = QVBoxLayout(self.pending_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        self.pending_table = QTableWidget(0, 6)
        self.pending_table.setHorizontalHeaderLabels(["ID", "Agent", "工具", "入参", "理由", "操作"])
        self.pending_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pending_table.verticalHeader().setVisible(False)
        lay.addWidget(self.pending_table)

        # 历史
        lay2 = QVBoxLayout(self.history_tab)
        lay2.setContentsMargins(0, 8, 0, 0)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["ID", "Agent", "工具", "状态", "理由", "决定时间"])
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.verticalHeader().setVisible(False)
        lay2.addWidget(self.history_table)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(6000)
        self.refresh()

    def refresh(self) -> None:
        try:
            pending = self.client.get("/api/intercept/pending")
            self._fill_pending(pending)
            history = self.client.get("/api/intercept/history", params={"size": 100})
            self._fill_history(history.get("items", []))
        except ApiError:
            pass

    def _fill_pending(self, items) -> None:
        t = self.pending_table
        t.setRowCount(0)
        for p in items:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, table_item(p["id"]))
            t.setItem(r, 1, table_item(p.get("agent_name", "")))
            t.setItem(r, 2, table_item(p.get("tool_name", ""), COL["accent"]))
            t.setItem(r, 3, table_item(json.dumps(p.get("tool_input", {}), ensure_ascii=False)[:100]))
            t.setItem(r, 4, table_item(p.get("reason", "")))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            ap = button("批准", primary=True)
            ap.clicked.connect(lambda _, pid=p["id"]: self._decide(pid, "approve"))
            dn = button("拒绝", danger=True)
            dn.clicked.connect(lambda _, pid=p["id"]: self._decide(pid, "deny"))
            h.addWidget(ap)
            h.addWidget(dn)
            t.setCellWidget(r, 5, cell)
        t.resizeRowsToContents()
        if not items:
            t.setRowCount(1)
            t.setItem(0, 0, table_item("暂无待审批项", COL["text_mute"]))

    def _fill_history(self, items) -> None:
        t = self.history_table
        t.setRowCount(0)
        for p in items:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, table_item(p["id"]))
            t.setItem(r, 1, table_item(p.get("agent_name", "")))
            t.setItem(r, 2, table_item(p.get("tool_name", "")))
            st = p.get("status", "")
            color = COL["ok"] if st == "allowed" else COL["danger"] if st == "denied" else COL["warn"]
            t.setItem(r, 3, table_item(st, color))
            t.setItem(r, 4, table_item(p.get("reason", "")))
            t.setItem(r, 5, table_item(fmt_time(p.get("decided_at"))))
        t.resizeRowsToContents()

    def _decide(self, pid: str, decision: str) -> None:
        try:
            self.client.post(f"/api/intercept/pending/{pid}/decide",
                             json={"decision": decision, "reason": "已批准" if decision == "approve" else "已拒绝"})
        except ApiError:
            pass
        self.refresh()
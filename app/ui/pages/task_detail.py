"""任务详情：10 个 Tab 的弹窗视图（对应 web 的 /function/tasks/detail）。

Tab：会话 | 总览 | 探索链路 | 播报板 | 发现 | 复测 | 测试资产 | 资产覆盖图 | 拦截审批 | 报告
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
    QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import (
    Card, button, fmt_status, fmt_time, make_badge, msg_label, status_color, table_item,
)
from .tabs import (
    SessionsTab, OverviewTab, GraphTab, BroadcastTab, FindingsTab,
    RetestsTab, AssetsTab, CoverageGraphTab, InterceptTab, ReportTab,
)


class TaskDetailDialog(QDialog):
    """任务详情弹窗。tid 为字符串任务 ID。"""

    def __init__(self, app, tid: str, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.tid = tid
        self.setWindowTitle(f"任务 #{tid}")
        self.resize(1280, 820)
        self.setAttribute(Qt.WA_DeleteOnClose)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)

        # 头部
        head = QHBoxLayout()
        self.name_lbl = QLabel("")
        self.name_lbl.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.status_badge = make_badge("", COL["text_dim"])
        head.addWidget(self.name_lbl)
        head.addWidget(self.status_badge)
        head.addStretch()
        self.ctrl_btn = button("")
        self.ctrl_btn.clicked.connect(self._toggle_control)
        head.addWidget(self.ctrl_btn)
        close = button("关闭")
        close.clicked.connect(self.close)
        head.addWidget(close)
        lay.addLayout(head)

        # 加载任务信息
        self.task: dict = {}
        try:
            self.task = self.client.get(f"/api/tasks/{tid}")
        except ApiError:
            pass
        self._update_header()

        # Tabs
        self.tabs = QTabWidget()
        self.session_tab = SessionsTab(self)
        self.tabs.addTab(self.session_tab, "会话")
        self.overview_tab = OverviewTab(self)
        self.tabs.addTab(self.overview_tab, "总览")
        self.graph_tab = GraphTab(self)
        self.tabs.addTab(self.graph_tab, "探索链路")
        self.broadcast_tab = BroadcastTab(self)
        self.tabs.addTab(self.broadcast_tab, "播报板")
        self.findings_tab = FindingsTab(self)
        self.tabs.addTab(self.findings_tab, "发现")
        self.retest_tab = RetestsTab(self)
        self.tabs.addTab(self.retest_tab, "复测")
        self.assets_tab = AssetsTab(self)
        self.tabs.addTab(self.assets_tab, "测试资产")
        self.coverage_tab = CoverageGraphTab(self)
        self.tabs.addTab(self.coverage_tab, "资产覆盖图")
        self.intercept_tab = InterceptTab(self)
        self.tabs.addTab(self.intercept_tab, "拦截审批")
        self.report_tab = ReportTab(self)
        self.tabs.addTab(self.report_tab, "报告")
        lay.addWidget(self.tabs, 1)

        self.tabs.currentChanged.connect(self._on_tab)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_header)
        self._timer.start(5000)

    def _update_header(self) -> None:
        self.name_lbl.setText(self.task.get("name") or self.task.get("description") or f"任务 #{self.tid}")
        st = self.task.get("status", "")
        self.status_badge.setStyleSheet(
            f"background: {status_color(st)}22; color: {status_color(st)}; border: 1px solid {status_color(st)}55;")
        self.status_badge.setText(fmt_status(st))
        self.ctrl_btn.setText("暂停" if self.task.get("paused") else "暂停" if st == "running" else "继续")
        self.ctrl_btn.setText("继续" if st in ("paused",) else "暂停")

    def _refresh_header(self) -> None:
        try:
            self.task = self.client.get(f"/api/tasks/{self.tid}")
            self._update_header()
        except ApiError:
            pass

    def _toggle_control(self) -> None:
        action = "resume" if self.task.get("status") == "paused" else "pause"
        self.client.post(f"/api/tasks/{self.tid}/control", json={"action": action})
        self._refresh_header()

    def _on_tab(self, idx: int) -> None:
        tab = self.tabs.widget(idx)
        if hasattr(tab, "on_shown"):
            tab.on_shown()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        for t in getattr(self, "_tab_timers", []):
            t.stop()
        super().closeEvent(event)
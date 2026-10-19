"""侧边导航：功能 / 系统 两组 + 底部用户。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from .api_client import ApiError

NAV_GROUPS = [
    ("功能", [
        ("dashboard", "总览", "📊"),
        ("tasks", "任务", "🗂"),
        ("chat", "对话", "💬"),
        ("findings", "发现", "🐞"),
        ("traffic", "流量", "🌐"),
        ("assets", "资产", "📡"),
        ("commands", "命令记录", "💻"),
        ("llm-records", "LLM 记录", "📋"),
        ("workspace", "工作空间", "📁"),
        ("sync", "资产同步", "🔄"),
    ]),
    ("系统", [
        ("system/llm", "LLM 配置", "🧠"),
        ("system/agents", "Agent 管理", "🤖"),
        ("system/tools", "工具目录", "🧰"),
        ("system/mcp", "MCP 服务器", "🔌"),
        ("system/skills", "技能", "📚"),
        ("system/intercept", "拦截审批", "🛡"),
        ("system/approvals", "审批记录", "📑"),
        ("system/notify", "漏洞推送", "🔔"),
        ("system/logs", "后端日志", "📜"),
        ("system/settings", "系统配置", "⚙️"),
    ]),
]


class Sidebar(QFrame):
    def __init__(self, app: "ArtexApplication"):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        self.app = app
        self._buttons: dict[str, QPushButton] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(2)

        title = QLabel("ARTEX")
        title.setObjectName("SidebarTitle")
        lay.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("SidebarScroll")
        self._nav_lay = QVBoxLayout(inner)
        self._nav_lay.setContentsMargins(0, 0, 0, 0)
        self._nav_lay.setSpacing(2)
        self._nav_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        self._build_nav()
        self._build_user()

    def _build_nav(self) -> None:
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for group_name, items in NAV_GROUPS:
            lbl = QLabel(group_name)
            lbl.setObjectName("SidebarGroup")
            self._nav_lay.insertWidget(self._nav_lay.count() - 1, lbl)
            for route, label, icon in items:
                btn = QPushButton(f"{icon}  {label}")
                btn.setObjectName("SidebarItem")
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _, r=route: self.app.navigate(r))
                self._group.addButton(btn)
                self._buttons[route] = btn
                self._nav_lay.insertWidget(self._nav_lay.count() - 1, btn)
        # 待审批计数徽章（每 8s 轮询）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_pending)
        self._timer.start(8000)
        self._refresh_pending()

    def _refresh_pending(self) -> None:
        try:
            r = self.app.client.get("/api/intercept/pending")
            n = len(r) if isinstance(r, list) else 0
            btn = self._buttons.get("system/approvals")
            if btn and n:
                btn.setText(f"📑  审批记录  [{n}]")
            elif btn:
                btn.setText("📑  审批记录")
        except ApiError:
            pass

    def _build_user(self) -> None:
        bar = QFrame()
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        u = QLabel("👤  ARTEX")
        u.setStyleSheet("color: #8ea0b0; font-size: 12px;")
        logout = QPushButton("退出")
        logout.setObjectName("Ghost")
        logout.setFixedWidth(40)
        logout.clicked.connect(self.app.logout)
        h.addWidget(u)
        h.addStretch()
        h.addWidget(logout)
        self._nav_lay.addWidget(bar)

    def set_current(self, route: str) -> None:
        for r, btn in self._buttons.items():
            if r == route:
                btn.setChecked(True)
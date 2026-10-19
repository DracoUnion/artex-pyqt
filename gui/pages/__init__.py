"""页面注册表：route -> Page 类。"""
from __future__ import annotations

from typing import Optional

from .base import BasePage
from .dashboard import DashboardPage
from .tasks import TasksPage
from .chat import ChatPage
from .findings import FindingsPage
from .traffic import TrafficPage
from .assets import AssetsPage
from .commands import CommandsPage
from .llm_records import LLMRecordsPage
from .workspace import WorkspacePage
from .sync import SyncPage
from .system_llm import SystemLLMPage
from .system_agents import SystemAgentsPage
from .system_agent_detail import SystemAgentDetailPage
from .system_tools import SystemToolsPage
from .system_mcp import SystemMcpPage
from .system_skills import SystemSkillsPage
from .system_intercept import SystemInterceptPage
from .system_approvals import SystemApprovalsPage
from .system_notify import SystemNotifyPage
from .system_logs import SystemLogsPage
from .system_settings import SystemSettingsPage

ROUTES = {
    "dashboard": DashboardPage,
    "tasks": TasksPage,
    "chat": ChatPage,
    "findings": FindingsPage,
    "traffic": TrafficPage,
    "assets": AssetsPage,
    "commands": CommandsPage,
    "llm-records": LLMRecordsPage,
    "workspace": WorkspacePage,
    "sync": SyncPage,
    "system/llm": SystemLLMPage,
    "system/agents": SystemAgentsPage,
    "system/tools": SystemToolsPage,
    "system/mcp": SystemMcpPage,
    "system/skills": SystemSkillsPage,
    "system/intercept": SystemInterceptPage,
    "system/approvals": SystemApprovalsPage,
    "system/notify": SystemNotifyPage,
    "system/logs": SystemLogsPage,
    "system/settings": SystemSettingsPage,
}


def get_page(app, route: str) -> "BasePage":
    cls = ROUTES.get(route)
    if not cls:
        if route.startswith("system/agents/detail"):
            cls = SystemAgentDetailPage
        else:
            from .base import NotFoundPage
            return NotFoundPage(app, route)
    return cls(app)


def open_route(app, route: str) -> None:
    app.navigate(route)

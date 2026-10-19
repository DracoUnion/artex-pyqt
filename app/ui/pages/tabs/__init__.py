"""任务详情 Tab 包。"""
from .sessions import SessionsTab
from .overview import OverviewTab
from .graph import GraphTab, CoverageGraphTab
from .broadcast import BroadcastTab
from .findings import FindingsTab, RetestsTab
from .a2tab import AssetsTab, InterceptTab, ReportTab

__all__ = [
    "SessionsTab", "OverviewTab", "GraphTab", "CoverageGraphTab",
    "BroadcastTab", "FindingsTab", "RetestsTab", "AssetsTab",
    "InterceptTab", "ReportTab",
]
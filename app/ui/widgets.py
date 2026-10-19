"""通用 UI 组件：状态徽章、卡片、工具函数、表格助手。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .theme import COL

STATUS_LABELS = {
    "running": "运行中", "created": "已创建", "paused": "已暂停", "done": "已完成",
    "failed": "失败", "timeout": "超时", "pending": "待处理", "open": "待处理",
    "confirmed": "已确认", "resolved": "已处理", "fixed": "已修复",
    "false_positive": "误报", "ignored": "忽略", "duplicate": "重复",
    "risk_accepted": "风险接受", "in_progress": "处理中", "active": "进行中",
    "met": "已完成", "achieved": "已完成", "queued": "排队中",
}

SEVERITY_LABELS = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}

KIND_LABELS = {
    "begin": "开始", "goal": "目标", "intent": "意图", "fact": "事实",
    "finding": "漏洞", "hint": "提示", "digest": "摘要",
}

ASSET_TYPE_LABELS = {
    "root_domain": "根域名", "subdomain": "子域名", "ip": "IP",
    "app": "应用", "service": "服务", "endpoint": "端点",
}


def status_color(state: str) -> str:
    return COL["status"].get(state, COL["text_dim"])


def severity_color(sev: str) -> str:
    return COL["severity"].get(sev, COL["text_dim"])


def fmt_status(state: str) -> str:
    return STATUS_LABELS.get(state, state)


def fmt_severity(sev: str) -> str:
    return SEVERITY_LABELS.get(sev, sev)


def fmt_time(ts: Optional[str]) -> str:
    """RFC3339 -> 可读时间。"""
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return ts
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def rel_time(ts: Optional[str]) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return ts
    diff = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.now() - dt
    secs = int(diff.total_seconds())
    if secs < 0:
        return "刚刚"
    if secs < 60:
        return f"{secs}s前"
    if secs < 3600:
        return f"{secs // 60}m前"
    if secs < 86400:
        return f"{secs // 3600}h前"
    return f"{secs // 86400}d前"


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


def make_badge(text: str, color: str, tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Badge")
    lbl.setStyleSheet(f"background: {color}22; color: {color}; border: 1px solid {color}55;")
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


def table_item(text: Any, color: Optional[str] = None, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "")
    item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    item.setTextAlignment(align)
    if color:
        item.setForeground(QColor(color))
    return item


class Card(QFrame):
    """卡片容器：标题 + 内容区。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        if title:
            title_lbl = QLabel(title)
            title_lbl.setObjectName("CardTitle")
            lay.addWidget(title_lbl)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        lay.addLayout(self.body)

    def add(self, w: QWidget):
        self.body.addWidget(w)

    def add_row(self, left: QWidget, right: Optional[QWidget] = None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(left)
        if right:
            row.addWidget(right)
        self.body.addLayout(row)
        return row


class StatCard(QFrame):
    """仪表盘统计卡：数值 + 标签 + 图标色。"""

    def __init__(self, label: str, color: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        self.value_lbl = QLabel("0")
        self.value_lbl.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {color};")
        self.label_lbl = QLabel(label)
        self.label_lbl.setStyleSheet("color: #8ea0b0; font-size: 12px;")
        lay.addWidget(self.value_lbl)
        lay.addWidget(self.label_lbl)

    def set_value(self, v: Any) -> None:
        self.value_lbl.setText(str(v))


def msg_label(text: str, color: str = COL["text_dim"]) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {color};")
    return lbl


def button(text: str, obj: str = "", primary: bool = False, danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    elif danger:
        b.setObjectName("Danger")
    elif obj:
        b.setObjectName(obj)
    return b

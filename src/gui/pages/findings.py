"""发现页：漏洞列表（平铺/分组/资产树）+ 详情 + 复测。"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import (
    SEVERITY_LABELS, fmt_time, make_badge, msg_label, severity_color, status_color,
    table_item, button, ASSET_TYPE_LABELS,
)
from .base import BasePage

SEVERS = [("all", "全部严重度"), ("critical", "严重"), ("high", "高"), ("medium", "中"), ("low", "低")]
STATUSES = [("all", "全部状态"), ("pending", "待处理"), ("in_progress", "处理中"),
            ("confirmed", "已确认"), ("resolved", "已处理"), ("fixed", "已修复"),
            ("false_positive", "误报"), ("ignored", "忽略"), ("duplicate", "重复")]


class FindingsPage(BasePage):
    def title_text(self) -> str:
        return "发现"

    def _build(self) -> None:
        super()._build()
        # 工具栏
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索漏洞…")
        self.search.setMaximumWidth(220)
        self.search.returnPressed.connect(self.refresh_list)
        self.sev_filter = QComboBox()
        for v, t in SEVERS:
            self.sev_filter.addItem(t, v)
        self.status_filter = QComboBox()
        for v, t in STATUSES:
            self.status_filter.addItem(t, v)
        self.sev_filter.currentIndexChanged.connect(self.refresh_list)
        self.status_filter.currentIndexChanged.connect(self.refresh_list)
        self.add_toolbar(self.search)
        self.add_toolbar(self.sev_filter)
        self.add_toolbar(self.status_filter)

        self.tabs = QTabWidget()
        self.add_to_content(self.tabs)

        self.list_tab = QWidget()
        self.group_tab = QWidget()
        self.tree_tab = QWidget()
        self.tabs.addTab(self.list_tab, "漏洞列表")
        self.tabs.addTab(self.group_tab, "按任务分组")
        self.tabs.addTab(self.tree_tab, "按资产")

        self._build_list()
        self._build_group()
        self._build_tree()

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh_list)
        self._poll.start(12000)
        self.refresh_list()

    # ---- 平铺列表 -----------------------------------------------------
    def _build_list(self) -> None:
        lay = QVBoxLayout(self.list_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "漏洞", "严重度", "状态", "任务", "摘要", "创建时间", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(lambda r, c: self._open_detail(
            self.table.item(r, 0).text()))
        lay.addWidget(self.table)

    def refresh_list(self) -> None:
        params = {"size": 100}
        if self.search.text().strip():
            params["q"] = self.search.text().strip()
        if self.sev_filter.currentData() != "all":
            params["severity"] = self.sev_filter.currentData()
        if self.status_filter.currentData() != "all":
            params["status"] = self.status_filter.currentData()
        try:
            data = self.client.get("/api/exploration/findings", params=params)
        except ApiError:
            return
        items = data.get("items", []) if isinstance(data, dict) else data
        table = self.table
        table.setRowCount(0)
        for f in items:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(f["id"]))
            table.setItem(r, 1, table_item(f.get("name") or f.get("vulnclass") or ""))
            table.setItem(r, 2, table_item(SEVERITY_LABELS.get(f.get("severity", ""), "-"),
                                           severity_color(f.get("severity", ""))))
            table.setItem(r, 3, table_item(f.get("status", ""), status_color(f.get("status", ""))))
            table.setItem(r, 4, table_item((f.get("task_description") or "")[:40]))
            table.setItem(r, 5, table_item((f.get("summary") or "")[:100]))
            table.setItem(r, 6, table_item(fmt_time(f.get("ts"))))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            b = button("详情", obj="Link")
            b.clicked.connect(lambda _, fid=f["id"]: self._open_detail(fid))
            h.addWidget(b)
            h.addStretch()
            table.setCellWidget(r, 7, cell)
        table.resizeRowsToContents()
        table.setColumnWidth(2, 70)
        table.setColumnWidth(3, 80)
        table.setColumnWidth(5, 320)

    def _open_detail(self, fid: str) -> None:
        dlg = FindingDetailDialog(self.app, fid, self)
        dlg.exec()
        self.refresh_list()

    # ---- 分组 ---------------------------------------------------------
    def _build_group(self) -> None:
        lay = QVBoxLayout(self.group_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        self.group_table = QTableWidget(0, 4)
        self.group_table.setHorizontalHeaderLabels(["任务 ID", "任务", "目标", "漏洞数"])
        self.group_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.group_table.verticalHeader().setVisible(False)
        lay.addWidget(self.group_table)

    def _build_tree(self) -> None:
        lay = QVBoxLayout(self.tree_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        self.tree_text = QTextEdit()
        self.tree_text.setReadOnly(True)
        lay.addWidget(self.tree_text)

    def on_shown(self) -> None:
        self.refresh_list()
        self._refresh_group()
        self._refresh_tree()

    def _refresh_group(self) -> None:
        try:
            groups = self.client.get("/api/exploration/findings/groups", params={"size": 100})
        except ApiError:
            return
        self.group_table.setRowCount(0)
        for g in groups:
            r = self.group_table.rowCount()
            self.group_table.insertRow(r)
            self.group_table.setItem(r, 0, table_item(g["task_id"]))
            self.group_table.setItem(r, 1, table_item(g["task_name"]))
            self.group_table.setItem(r, 2, table_item((g.get("task_goal") or "")[:80]))
            self.group_table.setItem(r, 3, table_item(g["count"], COL["severity"]["high"]))
        self.group_table.resizeRowsToContents()

    def _refresh_tree(self) -> None:
        try:
            tree = self.client.get("/api/exploration/findings/asset-tree")
        except ApiError:
            return
        lines = []
        for comp in tree:
            lines.append(f"🏢 {comp['name']}")
            for a in comp.get("assets", []):
                lines.append(f"  📌 {ASSET_TYPE_LABELS.get(a.get('type', ''), a.get('type', ''))} {a.get('label', '')}")
                for f in a.get("findings", []):
                    lines.append(f"    🐞 {f.get('name') or f.get('vulnclass')} [{f.get('severity')}] {f.get('status')}")
        self.tree_text.setPlainText("\n".join(lines))


class FindingDetailDialog(QDialog):
    def __init__(self, app, fid: str, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.fid = fid
        self.setWindowTitle(f"漏洞 #{fid}")
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        self.body = QTextEdit()
        self.body.setReadOnly(True)
        lay.addWidget(self.body, 1)

        row = QHBoxLayout()
        self.status_pick = QComboBox()
        for v, t in STATUSES[1:]:
            self.status_pick.addItem(t, v)
        apply = button("更新状态", primary=True)
        apply.clicked.connect(self._apply_status)
        retest = button("发起复测")
        retest.clicked.connect(self._retest)
        close = button("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(QLabel("处置状态:"))
        row.addWidget(self.status_pick)
        row.addWidget(apply)
        row.addWidget(retest)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)
        self._load()

    def _load(self) -> None:
        try:
            f = self.client.get(f"/api/exploration/findings/{self.fid}")
        except ApiError as e:
            self.body.setPlainText(f"加载失败: {e.message}")
            return
        idx = STATUSES.index(f.get("status", "pending")) - 1
        if idx >= 0:
            self.status_pick.setCurrentIndex(min(idx, self.status_pick.count() - 1))
        parts = [
            f"# {f.get('name') or f.get('vulnclass')}",
            "",
            f"- **严重度**: {f.get('severity', '-')}",
            f"- **状态**: {f.get('status', '-')}",
            f"- **任务**: {f.get('task_description', '-')}",
            f"- **创建**: {fmt_time(f.get('ts'))}",
            "",
            "## 摘要",
            "",
            f.get("summary", ""),
            "",
            "## 证据",
            "",
            f"```\n{f.get('evidence', '')}\n```",
            "",
            "## 复现报告",
            "",
            f.get("report", "（无）"),
            "",
            "## 关联资产",
            "",
        ]
        for a in f.get("assets", []):
            parts.append(f"- {a.get('type')} {a.get('label', '')}")
        self.body.setMarkdown("\n".join(parts))

    def _apply_status(self) -> None:
        status = self.status_pick.currentData()
        try:
            self.client.patch(f"/api/exploration/findings/{self.fid}", json={"status": status})
        except ApiError as e:
            QMessageBox.warning(self, "更新失败", e.message)
        self._load()

    def _retest(self) -> None:
        try:
            self.client.post(f"/api/exploration/findings/{self.fid}/retests", json={})
            QMessageBox.information(self, "复测", "复测已发起。")
        except ApiError as e:
            QMessageBox.warning(self, "复测失败", e.message)

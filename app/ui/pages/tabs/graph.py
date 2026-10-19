"""探索链路图 / 资产覆盖图 Tab。"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from ...api_client import ApiError
from ...graph_widget import GraphWidget
from ...theme import COL
from ...widgets import button, msg_label


class GraphTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(15000)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.graph = GraphWidget(detail_cb=self._show_detail)
        lay.addWidget(self.graph)
        legend = QLabel(
            "🟣目标 🩵意图 💚事实 🔴漏洞 ⚪开始/提示   ·   点击节点查看详情 · 滚轮缩放 · 拖拽平移")
        legend.setStyleSheet(f"color: {COL['text_mute']}; padding: 4px 8px;")
        lay.addWidget(legend)

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            data = self.client.get(f"/api/exploration/graph?task={self.tid}")
        except ApiError:
            return
        nodes = []
        for n in data.get("nodes", []):
            payload = n.get("payload") or "{}"
            try:
                p = json.loads(payload) if isinstance(payload, str) else payload or {}
            except Exception:
                p = {}
            label = p.get("text") or n.get("id")
            if n.get("type") == "finding":
                label = p.get("name") or label
            nodes.append({"id": n["id"], "label": label[:32], "type": n["type"], "payload": p})
        edges = data.get("edges", [])
        self.graph.set_graph(nodes, edges)

    def _show_detail(self, node: dict) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"节点 #{node['id']} · {node.get('type', '')}")
        dlg.resize(520, 460)
        lay = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(node.get("payload") or {}, ensure_ascii=False, indent=2))
        lay.addWidget(text)
        btn = button("关闭")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn, 0, Qt.AlignRight)
        dlg.exec()


class CoverageGraphTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(20000)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.graph = GraphWidget(detail_cb=self._show_detail)
        lay.addWidget(self.graph)
        legend = QLabel("💚 已测试高亮 · 点击节点查看关联 · 滚轮缩放")
        legend.setStyleSheet(f"color: {COL['text_mute']}; padding: 4px 8px;")
        lay.addWidget(legend)

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            data = self.client.get(f"/api/tasks/{self.tid}/coverage-graph")
        except ApiError:
            return
        nodes = [{"id": n["id"], "label": n.get("label", ""), "type": n.get("type"),
                  "tested": n.get("tested", False)} for n in data.get("nodes", [])]
        self.graph.set_graph(nodes, data.get("edges", []))

    def _show_detail(self, node: dict) -> None:
        try:
            refs = self.client.get(f"/api/tasks/{self.tid}/asset-refs",
                                   params={"asset_id": node["id"]})
        except ApiError:
            refs = {}
        dlg = QDialog(self)
        dlg.setWindowTitle(f"资产 #{node['id']} · {node.get('label', '')}")
        dlg.resize(520, 460)
        lay = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(refs, ensure_ascii=False, indent=2, default=str))
        lay.addWidget(text)
        btn = button("关闭")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn, 0, Qt.AlignRight)
        dlg.exec()

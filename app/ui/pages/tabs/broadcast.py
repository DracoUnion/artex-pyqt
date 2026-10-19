"""播报板 Tab：探索节点分页时间序列。"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...api_client import ApiError
from ...widgets import (
    KIND_LABELS, fmt_time, table_item, status_color, button,
)


class BroadcastTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._page = 0
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(15000)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("全部类型", None)
        for k in ("begin", "goal", "intent", "fact", "finding", "hint", "digest"):
            self.kind_filter.addItem(KIND_LABELS.get(k, k), k)
        self.kind_filter.currentIndexChanged.connect(lambda: self._reset())
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索…")
        self.search.setMaximumWidth(200)
        self.search.returnPressed.connect(self._reset)
        bar.addWidget(self.kind_filter)
        bar.addWidget(self.search)
        bar.addStretch()
        self.prev_btn = button("上一页")
        self.prev_btn.clicked.connect(lambda: self._go(-1))
        self.next_btn = button("下一页")
        self.next_btn.clicked.connect(lambda: self._go(1))
        self.page_lbl = QLabel("")
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.page_lbl)
        bar.addWidget(self.next_btn)
        lay.addLayout(bar)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "类型", "内容", "状态", "来源", "创建时间", "关联资产"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table)

    def _reset(self) -> None:
        self._page = 0
        self.refresh()

    def _go(self, delta: int) -> None:
        self._page = max(0, self._page + delta)
        self.refresh()

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        params = {"task": self.tid, "page": self._page, "size": 30}
        if self.kind_filter.currentData():
            params["kinds"] = self.kind_filter.currentData()
        if self.search.text().strip():
            params["q"] = self.search.text().strip()
        try:
            data = self.client.get("/api/exploration/nodes", params=params)
        except ApiError:
            return
        items = data.get("items", [])
        total = data.get("total", 0)
        self.page_lbl.setText(f"{self._page + 1} / {max(1, (total + 29) // 30)}")
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled((self._page + 1) * 30 < total)
        table = self.table
        table.setRowCount(0)
        assets = data.get("assets", {})
        for n in items:
            r = table.rowCount()
            table.insertRow(r)
            payload = n.get("payload") or "{}"
            try:
                p = json.loads(payload) if isinstance(payload, str) else payload or {}
            except Exception:
                p = {}
            text = p.get("text") or p.get("name") or ""
            table.setItem(r, 0, table_item(n["id"]))
            table.setItem(r, 1, table_item(KIND_LABELS.get(n["type"], n["type"])))
            table.setItem(r, 2, table_item(text[:120]))
            table.setItem(r, 3, table_item(n["state"], status_color(n["state"])))
            table.setItem(r, 4, table_item(n.get("origin", "")))
            table.setItem(r, 5, table_item(fmt_time(n.get("ts"))))
            a_labels = ", ".join(a.get("label") or "" for a in assets.get(n["id"], [])[:3])
            table.setItem(r, 6, table_item(a_labels))
        table.resizeRowsToContents()
        table.setColumnWidth(2, 360)

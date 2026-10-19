"""流量录制页：分页浏览 / 原文查看 / 删除。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, fmt_time, msg_label, table_item
from .base import BasePage


class TrafficPage(BasePage):
    def title_text(self) -> str:
        return "流量"

    def _build(self) -> None:
        super()._build()
        # 工具栏
        self.search = QLineEdit()
        self.search.setPlaceholderText("关键词搜索…")
        self.search.setMaximumWidth(180)
        self.search.returnPressed.connect(self._reset)
        self.host = QLineEdit()
        self.host.setPlaceholderText("主机过滤")
        self.host.setMaximumWidth(150)
        self.host.returnPressed.connect(self._reset)
        self.method = QComboBox()
        self.method.addItem("全部方法", None)
        for m in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            self.method.addItem(m, m)
        self.status = QLineEdit()
        self.status.setPlaceholderText("状态码")
        self.status.setMaximumWidth(80)
        self.status.returnPressed.connect(self._reset)
        self.add_toolbar(self.host)
        self.add_toolbar(self.method)
        self.add_toolbar(self.status)
        self.add_toolbar(self.search)

        clear_all = button("清空全部", danger=True)
        clear_all.clicked.connect(self._clear_all)
        self.toolbar.addWidget(clear_all)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        # 表格
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "时间", "主机", "方法", "URL", "状态", "大小"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._show_detail)
        lay.addWidget(self.table)
        page_row = QHBoxLayout()
        self.prev = button("上一页")
        self.prev.clicked.connect(lambda: self._page(-1))
        self.next = button("下一页")
        self.next.clicked.connect(lambda: self._page(+1))
        self.page_lbl = QLabel("")
        page_row.addWidget(self.prev)
        page_row.addWidget(self.page_lbl)
        page_row.addWidget(self.next)
        page_row.addStretch()
        lay.addLayout(page_row)
        splitter.addWidget(left)

        # 原文
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(420)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(1, 1)
        self.add_to_content(splitter)

        self._page_no = 0
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(8000)
        self.refresh()

    def _reset(self) -> None:
        self._page_no = 0
        self.refresh()

    def _page(self, d: int) -> None:
        self._page_no = max(0, self._page_no + d)
        self.refresh()

    def refresh(self) -> None:
        params = {"page": self._page_no, "size": 30}
        if self.host.text().strip():
            params["host"] = self.host.text().strip()
        if self.method.currentData():
            params["method"] = self.method.currentData()
        if self.status.text().strip().isdigit():
            params["status"] = int(self.status.text())
        if self.search.text().strip():
            params["q"] = self.search.text().strip()
        try:
            data = self.client.get("/api/traffic", params=params)
        except ApiError:
            return
        total = data.get("total", 0)
        self.page_lbl.setText(f"{self._page_no + 1} / {max(1, (total + 29) // 30)} · 共 {total} 条")
        self.prev.setEnabled(self._page_no > 0)
        self.next.setEnabled((self._page_no + 1) * 30 < total)
        table = self.table
        table.setRowCount(0)
        for e in data.get("exchanges", []):
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(e["id"]))
            table.setItem(r, 1, table_item(fmt_time(e.get("ts"))))
            table.setItem(r, 2, table_item(e.get("host", "")))
            st = e.get("method", "")
            color = COL["ok"] if st == "GET" else COL["purple"] if st == "POST" else COL["text"]
            table.setItem(r, 3, table_item(st, color))
            table.setItem(r, 4, table_item(e.get("url", "")))
            table.setItem(r, 5, table_item(e.get("status", ""),
                                           COL["danger"] if e.get("status", 0) >= 500 else COL["ok"]))
            table.setItem(r, 6, table_item(e.get("resp_len", 0)))
        table.resizeRowsToContents()
        table.setColumnWidth(4, 380)

    def _show_detail(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        eid = self.table.item(row, 0).text()
        try:
            e = self.client.get("/api/traffic/exchange", params={"id": eid})
        except ApiError:
            return
        import json
        req_head = e.get("req_headers") or {}
        resp_head = e.get("resp_headers") or {}
        lines = [
            f"===== 请求 =====\n{e.get('method')} {e.get('path')} HTTP/1.1",
            *[f"{k}: {v}" for k, v in (req_head if isinstance(req_head, dict) else {}).items()],
            "",
            (e.get("req_body") or ""),
            "",
            f"===== 响应 ({e.get('status')}) =====",
            *[f"{k}: {v}" for k, v in (resp_head if isinstance(resp_head, dict) else {}).items()],
            "",
            (e.get("resp_body") or ""),
        ]
        self.detail.setPlainText("\n".join(str(x) for x in lines))

    def _clear_all(self) -> None:
        if QMessageBox.question(self, "清空流量", "确认清空全部流量记录？") != QMessageBox.Yes:
            return
        try:
            self.client.delete("/api/traffic/all")
        except ApiError:
            pass
        self.refresh()
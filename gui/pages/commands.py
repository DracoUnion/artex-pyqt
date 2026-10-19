"""命令记录页：分页浏览工具执行记录。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QTableWidget, QWidget,
)

from ..api_client import ApiError
from ..widgets import button, fmt_time, table_item
from .base import BasePage

PAGE_SIZE = 30


class CommandsPage(BasePage):
    poll_intervals = {"refresh": 10000}

    def title_text(self) -> str:
        return "命令记录"

    def _build(self) -> None:
        super()._build()
        # 工具栏：搜索 + 工具筛选
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索命令…")
        self.search.setMaximumWidth(220)
        self.search.returnPressed.connect(self._reset)
        self.tool_filter = QComboBox()
        self.tool_filter.addItem("全部工具", None)
        self.tool_filter.currentIndexChanged.connect(self._reset)
        self.add_toolbar(self.search)
        self.add_toolbar(self.tool_filter)

        # 表格
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["工具", "Worker", "摘要", "详情", "时间"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.add_to_content(self.table)

        # 分页
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
        self.add_to_content(_wrap(page_row))

        self._page_no = 0
        self._load_tools()
        self.refresh()

    # ---- 分页 ---------------------------------------------------------
    def _reset(self) -> None:
        self._page_no = 0
        self.refresh()

    def _page(self, d: int) -> None:
        self._page_no = max(0, self._page_no + d)
        self.refresh()

    # ---- 工具筛选 -----------------------------------------------------
    def _load_tools(self) -> None:
        try:
            stats = self.client.get("/api/commands/stats")
        except ApiError:
            return
        cur = self.tool_filter.currentData()
        self.tool_filter.blockSignals(True)
        self.tool_filter.clear()
        self.tool_filter.addItem("全部工具", None)
        for s in stats:
            self.tool_filter.addItem(f"{s.get('tool', '')} ({s.get('count', 0)})", s.get("tool"))
        idx = self.tool_filter.findData(cur)
        self.tool_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.tool_filter.blockSignals(False)

    # ---- 刷新 ---------------------------------------------------------
    def refresh(self) -> None:
        params = {"page": self._page_no, "size": PAGE_SIZE}
        if self.search.text().strip():
            params["q"] = self.search.text().strip()
        if self.tool_filter.currentData():
            params["tool"] = self.tool_filter.currentData()
        try:
            data = self.client.get("/api/commands", params=params)
        except ApiError:
            return
        total = data.get("total", 0)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_lbl.setText(f"{self._page_no + 1} / {pages} · 共 {total} 条")
        self.prev.setEnabled(self._page_no > 0)
        self.next.setEnabled((self._page_no + 1) * PAGE_SIZE < total)
        table = self.table
        table.setRowCount(0)
        for c in data.get("items", []):
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(c.get("tool", "")))
            table.setItem(r, 1, table_item(c.get("worker", "")))
            table.setItem(r, 2, table_item((c.get("summary") or "")[:120]))
            table.setItem(r, 3, table_item((c.get("detail") or "")[:200]))
            table.setItem(r, 4, table_item(fmt_time(c.get("created_at"))))
        table.resizeRowsToContents()
        table.setColumnWidth(3, 320)


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w

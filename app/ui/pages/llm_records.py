"""LLM 请求记录页：分页浏览 + 双击查看 Prompt / Response。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QTableWidget, QTextEdit, QVBoxLayout,
    QWidget,
)

from ..api_client import ApiError
from ..widgets import button, fmt_time, table_item
from .base import BasePage

PAGE_SIZE = 30


class LLMRecordsPage(BasePage):
    poll_intervals = {"refresh": 10000}

    def title_text(self) -> str:
        return "LLM 记录"

    def _build(self) -> None:
        super()._build()
        # 工具栏：按 model 搜索
        self.search = QLineEdit()
        self.search.setPlaceholderText("按模型搜索…")
        self.search.setMaximumWidth(220)
        self.search.returnPressed.connect(self._reset)
        self.add_toolbar(self.search)

        # 表格
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "模型", "会话", "输入 Token", "输出 Token", "时间"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(
            lambda r, c: self._open_detail(self.table.item(r, 0).text()))
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
        self.refresh()

    # ---- 分页 ---------------------------------------------------------
    def _reset(self) -> None:
        self._page_no = 0
        self.refresh()

    def _page(self, d: int) -> None:
        self._page_no = max(0, self._page_no + d)
        self.refresh()

    # ---- 刷新 ---------------------------------------------------------
    def refresh(self) -> None:
        params = {"page": self._page_no, "size": PAGE_SIZE}
        if self.search.text().strip():
            params["model"] = self.search.text().strip()
        try:
            data = self.client.get("/api/llm/records", params=params)
        except ApiError:
            return
        total = data.get("total", 0)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_lbl.setText(f"{self._page_no + 1} / {pages} · 共 {total} 条")
        self.prev.setEnabled(self._page_no > 0)
        self.next.setEnabled((self._page_no + 1) * PAGE_SIZE < total)
        table = self.table
        table.setRowCount(0)
        for rec in data.get("items", []):
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(rec.get("id", "")))
            table.setItem(r, 1, table_item(rec.get("model", "")))
            table.setItem(r, 2, table_item(rec.get("session_id", "")))
            table.setItem(r, 3, table_item(rec.get("input_tokens", 0)))
            table.setItem(r, 4, table_item(rec.get("output_tokens", 0)))
            table.setItem(r, 5, table_item(fmt_time(rec.get("created_at"))))
        table.resizeRowsToContents()
        table.setColumnWidth(2, 220)

    def _open_detail(self, rid: str) -> None:
        dlg = LlmRecordDialog(self.app, rid, self)
        dlg.exec()


class LlmRecordDialog(QDialog):
    """双击行弹出：显示该条记录的 prompt 与 response。"""

    def __init__(self, app, rid: str, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.rid = rid
        self.setWindowTitle(f"LLM 记录 #{rid}")
        self.resize(760, 620)
        lay = QVBoxLayout(self)
        self.body = QTextEdit()
        self.body.setReadOnly(True)
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        close = button("关闭")
        close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)
        self._load()

    def _load(self) -> None:
        try:
            d = self.client.get(f"/api/llm/records/{self.rid}")
        except ApiError as e:
            self.body.setPlainText(f"加载失败: {e.message}")
            return
        lines = [
            f"# 记录 #{self.rid}",
            "",
            f"- **模型**: {d.get('model', '-')}",
            f"- **会话**: {d.get('session_id', '-')}",
            f"- **输入 Token**: {d.get('input_tokens', '-')}",
            f"- **输出 Token**: {d.get('output_tokens', '-')}",
            f"- **时间**: {fmt_time(d.get('created_at'))}",
            "",
            "## Prompt",
            "",
            d.get("prompt") or "-",
            "",
            "## Response",
            "",
            d.get("response") or "-",
        ]
        self.body.setMarkdown("\n".join(str(x) for x in lines))


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w
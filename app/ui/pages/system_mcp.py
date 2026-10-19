"""MCP 服务器管理页。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, table_item, fmt_time
from .base import BasePage


class SystemMcpPage(BasePage):
    def title_text(self) -> str:
        return "MCP 服务器"

    def _build(self) -> None:
        super()._build()
        new_btn = button("＋ 新建服务器", primary=True)
        new_btn.clicked.connect(self._edit)
        self.add_toolbar(new_btn)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "名称", "传输", "地址/命令", "启用", "工具数", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.add_to_content(self.table)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(8000)
        self.refresh()

    def refresh(self) -> None:
        try:
            servers = self.client.get("/api/mcp")
        except ApiError:
            return
        table = self.table
        table.setRowCount(0)
        for s in servers:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(s["id"]))
            table.setItem(r, 1, table_item(s["name"]))
            table.setItem(r, 2, table_item(s["transport"], COL["accent"]))
            addr = s.get("url") or s.get("command") or ""
            table.setItem(r, 3, table_item(addr))
            table.setItem(r, 4, table_item("启用" if s.get("enabled") else "停用",
                                           COL["ok"] if s.get("enabled") else COL["text_mute"]))
            table.setItem(r, 5, table_item(s.get("tool_count", 0)))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            for text, cb in [("编辑", lambda _, sid=s["id"]: self._edit(sid)),
                             ("刷新工具", lambda _, sid=s["id"]: self._refresh_tools(sid)),
                             ("删除", lambda _, sid=s["id"]: self._delete(sid))]:
                b = button(text, obj="Link", danger=(text == "删除"))
                b.clicked.connect(cb)
                h.addWidget(b)
            h.addStretch()
            table.setCellWidget(r, 6, cell)
        table.resizeRowsToContents()

    def _edit(self, sid=None) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑 MCP 服务器" if sid else "新建 MCP 服务器")
        dlg.setFixedWidth(460)
        lay = QFormLayout(dlg)
        server = None
        if sid:
            for s in self.client.get("/api/mcp"):
                if s["id"] == str(sid):
                    server = s
        name = QLineEdit(server["name"] if server else "")
        transport = QComboBox()
        for t in ("stdio", "http", "sse"):
            transport.addItem(t, t)
        if server:
            transport.setCurrentIndex(max(0, [transport.itemData(i) for i in range(transport.count())].index(server.get("transport"))))
        command = QLineEdit(server.get("command") or "" if server else "")
        args = QLineEdit((server.get("args") and ",".join(server["args"])) or "" if server else "")
        url = QLineEdit(server.get("url") or "" if server else "")
        enabled = QCheckBox("启用")
        enabled.setChecked(server.get("enabled", True) if server else True)
        lay.addRow("名称", name)
        lay.addRow("传输", transport)
        lay.addRow("命令", command)
        lay.addRow("参数(逗号分隔)", args)
        lay.addRow("URL", url)
        lay.addRow("", enabled)
        btns = QHBoxLayout()
        ok = button("保存", primary=True)
        ok.clicked.connect(dlg.accept)
        cancel = button("取消")
        cancel.clicked.connect(dlg.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addRow(btns)
        if dlg.exec() != QDialog.Accepted:
            return
        body = {
            "name": name.text().strip(),
            "transport": transport.currentData(),
            "command": command.text().strip() or None,
            "args": [a.strip() for a in args.text().split(",") if a.strip()],
            "url": url.text().strip() or None,
            "enabled": enabled.isChecked(),
        }
        if server:
            body["id"] = server["id"]
        try:
            self.client.post("/api/mcp", json=body)
        except ApiError as e:
            QMessageBox.warning(self, "保存失败", e.message)
        self.refresh()

    def _refresh_tools(self, sid: str) -> None:
        try:
            r = self.client.post(f"/api/mcp/{sid}/refresh")
            self.toast(f"刷新 {r.get('refreshed', 0)} 个工具")
        except ApiError as e:
            QMessageBox.warning(self, "刷新失败", e.message)

    def _delete(self, sid: str) -> None:
        if QMessageBox.question(self, "删除服务器", "确认删除该 MCP 服务器？") == QMessageBox.Yes:
            try:
                self.client.delete(f"/api/mcp/{sid}")
            except ApiError:
                pass
            self.refresh()
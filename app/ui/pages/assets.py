"""资产页：7 类型 Tab + DSL 搜索 + 批量删除 + 企业管理。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import (
    ASSET_TYPE_LABELS, fmt_time, table_item, button,
)
from .base import BasePage

TYPES = [("root_domain", "根域名"), ("subdomain", "子域名"), ("ip", "IP"),
         ("app", "应用"), ("service", "服务"), ("endpoint", "端点")]


class AssetsPage(BasePage):
    def title_text(self) -> str:
        return "资产"

    def _build(self) -> None:
        super()._build()
        # 工具栏
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 / DSL 语法，如 type:root_domain and domain:xxx.com")
        self.search.setMinimumWidth(360)
        self.search.returnPressed.connect(self.refresh)
        self.add_toolbar(self.search)
        self.company_btn = button("企业管理")
        self.company_btn.clicked.connect(self._companies)
        self.add_toolbar(self.company_btn)

        self.tabs = QTabWidget()
        self.add_to_content(self.tabs)
        self._type_tabs = {}
        for typ, label in TYPES:
            tab = QWidget()
            lay = QVBoxLayout(tab)
            lay.setContentsMargins(0, 8, 0, 0)
            table = QTableWidget(0, 6)
            table.setHorizontalHeaderLabels(["ID", "标识", "关联任务", "技术栈", "最后出现", "操作"])
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            lay.addWidget(table)
            self.tabs.addTab(tab, label)
            self._type_tabs[typ] = table

        # 批量删除
        ops = QHBoxLayout()
        del_btn = button("批量删除选中", danger=True)
        del_btn.clicked.connect(self._batch_delete)
        ops.addWidget(del_btn)
        ops.addStretch()
        self.add_to_content(_wrap(ops))

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(12000)
        self.refresh()

    def refresh(self) -> None:
        try:
            items = self.client.get("/api/assets", params={
                "limit": 500,
                "dsl": self.search.text().strip() or None,
            })
        except ApiError:
            return
        for typ, table in self._type_tabs.items():
            typed = [a for a in items if a["type"] == typ]
            table.setRowCount(0)
            for a in typed:
                r = table.rowCount()
                table.insertRow(r)
                table.setItem(r, 0, table_item(a["id"]))
                label = a.get("domain") or a.get("ip") or a.get("url") or a.get("app_name") or a.get("service_name") or ""
                table.setItem(r, 1, table_item(label))
                table.setItem(r, 2, table_item(", ".join(str(x) for x in (a.get("task_ids") or [])[:3])))
                techs = ", ".join((a.get("technologies") or [])[:3])
                table.setItem(r, 3, table_item(techs))
                table.setItem(r, 4, table_item(fmt_time(a.get("last_seen"))))
                cell = QWidget()
                h = QHBoxLayout(cell)
                h.setContentsMargins(2, 2, 2, 2)
                b = button("删除", danger=True)
                b.clicked.connect(lambda _, aid=a["id"]: self._delete_one(aid))
                h.addWidget(b)
                table.setCellWidget(r, 5, cell)
            table.resizeRowsToContents()

    def _delete_one(self, aid: str) -> None:
        if QMessageBox.question(self, "删除资产", f"确认删除资产 #{aid}？") == QMessageBox.Yes:
            try:
                self.client.delete("/api/assets", json={"ids": [int(aid)]})
            except ApiError:
                pass
            self.refresh()

    def _batch_delete(self) -> None:
        ids = []
        for typ, table in self._type_tabs.items():
            for r in range(table.rowCount()):
                it = table.item(r, 0)
                if it:
                    ids.append(int(it.text()))
        if not ids:
            return
        if QMessageBox.question(self, "批量删除", f"确认删除 {len(ids)} 个资产？") == QMessageBox.Yes:
            try:
                self.client.delete("/api/assets", json={"ids": ids})
            except ApiError:
                pass
            self.refresh()

    def _companies(self) -> None:
        dlg = CompanyDialog(self.app, self)
        dlg.exec()
        self.refresh()


class CompanyDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.setWindowTitle("企业管理")
        self.resize(620, 480)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ID", "名称", "范围数", "资产数", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("企业名称")
        add = button("新建企业", primary=True)
        add.clicked.connect(self._add)
        row.addWidget(self.input)
        row.addWidget(add)
        lay.addLayout(row)
        self._load()

    def _load(self) -> None:
        comps = self.client.get("/api/companies")
        self.table.setRowCount(0)
        for c in comps:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, table_item(c["id"]))
            self.table.setItem(r, 1, table_item(c["name"]))
            self.table.setItem(r, 2, table_item(len(c.get("scope") or [])))
            self.table.setItem(r, 3, table_item(c.get("asset_count", 0)))
            b = button("删除", danger=True)
            b.clicked.connect(lambda _, cid=c["id"]: self._delete(cid))
            self.table.setCellWidget(r, 4, b)
        self.table.resizeRowsToContents()

    def _add(self) -> None:
        name = self.input.text().strip()
        if name:
            try:
                self.client.post("/api/companies", json={"name": name, "scope": []})
            except ApiError:
                pass
            self.input.clear()
            self._load()

    def _delete(self, cid: str) -> None:
        if QMessageBox.question(self, "删除企业", "确认删除该企业及其资产？") == QMessageBox.Yes:
            try:
                self.client.delete(f"/api/companies/{cid}", params={"delete_assets": True})
            except ApiError:
                pass
            self._load()


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w
"""测试资产 / 拦截审批 / 报告 Tab。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QTextEdit,
)

from ...api_client import ApiError
from ...theme import COL
from ...widgets import ASSET_TYPE_LABELS, fmt_time, table_item, button, msg_label


class AssetsTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(10000)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "类型", "标识", "关联意图", "最后出现", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table)
        row = QHBoxLayout()
        attach = button("挂载资产")
        attach.clicked.connect(self._attach)
        row.addStretch()
        row.addWidget(attach)
        lay.addLayout(row)

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            items = self.client.get("/api/assets", params={"task_id": self.tid, "limit": 300})
        except ApiError:
            return
        table = self.table
        table.setRowCount(0)
        refs = self._intent_assets()
        for a in items:
            r = table.rowCount()
            table.insertRow(r)
            label = a.get("domain") or a.get("ip") or a.get("url") or a.get("app_name") or a.get("service_name") or ""
            table.setItem(r, 0, table_item(a["id"]))
            table.setItem(r, 1, table_item(ASSET_TYPE_LABELS.get(a["type"], a["type"]), COL["accent"]))
            table.setItem(r, 2, table_item(label))
            table.setItem(r, 3, table_item(str(refs.get(str(a["id"]), ""))))
            table.setItem(r, 4, table_item(fmt_time(a.get("last_seen"))))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            rm = button("解除")
            rm.clicked.connect(lambda _, aid=a["id"]: self._detach(aid))
            h.addWidget(rm)
            table.setCellWidget(r, 5, cell)
        table.resizeRowsToContents()

    def _intent_assets(self) -> dict:
        try:
            items = self.client.get(f"/api/tasks/{self.tid}/intent-assets")
            out = {}
            for i in items:
                out.setdefault(str(i["asset_id"]), []).append(i["node_id"])
            return {k: ", ".join(v[:3]) for k, v in out.items()}
        except ApiError:
            return {}

    def _detach(self, aid: str) -> None:
        self.client.delete(f"/api/tasks/{self.tid}/assets/{aid}")
        self.refresh()

    def _attach(self) -> None:
        from ...widgets import Card
        from PySide6.QtWidgets import QDialog, QListWidget, QListWidgetItem
        dlg = QDialog(self)
        dlg.setWindowTitle("挂载资产")
        dlg.resize(480, 520)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        items = self.client.get("/api/assets", params={"limit": 500})
        for a in items:
            label = a.get("domain") or a.get("ip") or a.get("url") or a.get("app_name") or a.get("service_name") or ""
            item = QListWidgetItem(f"[{a['type']}] {label}  #{a['id']}")
            item.setData(Qt.UserRole, a["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            lst.addItem(item)
        lay.addWidget(lst)
        row = QHBoxLayout()
        ok = button("挂载", primary=True)
        ok.clicked.connect(dlg.accept)
        cancel = button("取消")
        cancel.clicked.connect(dlg.reject)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        if dlg.exec() != QDialog.Accepted:
            return
        ids = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
               if lst.item(i).checkState() == Qt.CheckState.Checked]
        if ids:
            self.client.post(f"/api/tasks/{self.tid}/assets", json={"asset_ids": ids})
            self.refresh()


class InterceptTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(8000)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["ID", "工具", "入参", "状态", "理由", "创建时间"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table)

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            items = self.client.get(f"/api/intercept/task/{self.tid}", params={"status": "pending"})
        except ApiError:
            return
        table = self.table
        table.setRowCount(0)
        for p in items:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(p["id"]))
            table.setItem(r, 1, table_item(p.get("tool_name", ""), COL["accent"]))
            import json
            table.setItem(r, 2, table_item(json.dumps(p.get("tool_input", {}), ensure_ascii=False)[:100]))
            table.setItem(r, 3, table_item(p.get("status", ""), COL["warn"]))
            table.setItem(r, 4, table_item(p.get("reason", "")))
            table.setItem(r, 5, table_item(fmt_time(p.get("created_at"))))
        table.resizeRowsToContents()
        if not items:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, table_item("该任务暂无待审批拦截", COL["text_mute"]))


class ReportTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        refresh = button("加载报告")
        refresh.clicked.connect(self._load)
        copy = button("复制")
        copy.clicked.connect(self._copy)
        row.addStretch()
        row.addWidget(copy)
        row.addWidget(refresh)
        lay.addLayout(row)
        self._load()

    def on_shown(self) -> None:
        self._load()

    def _load(self) -> None:
        try:
            r = self.client.get(f"/api/report?task={self.tid}")
            self.text.setPlainText(r)
        except ApiError as e:
            self.text.setPlainText(f"加载失败: {e.message}")

    def _copy(self) -> None:
        self.text.selectAll()
        self.text.copy()
        self.text.moveCursor(self.text.textCursor().Start)
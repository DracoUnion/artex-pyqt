"""发现 / 复测 Tab。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...api_client import ApiError
from ...theme import COL
from ...widgets import (
    SEVERITY_LABELS, fmt_time, make_badge, severity_color, status_color, table_item,
)

SEVERITY_ORDER = ["critical", "high", "medium", "low", ""]
STATUS_ORDER = ["pending", "in_progress", "confirmed", "resolved", "fixed",
                "false_positive", "ignored", "duplicate", "risk_accepted"]


class FindingsTab(QWidget):
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
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "漏洞", "严重度", "状态", "证据", "关联资产", "创建时间", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table)

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            data = self.client.get(f"/api/exploration/findings?task={self.tid}&size=200")
        except ApiError:
            return
        items = data if isinstance(data, list) else data.get("items", [])
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
            table.setItem(r, 4, table_item((f.get("evidence") or "")[:120]))
            labels = ", ".join(a.get("label") or "" for a in f.get("assets", [])[:2])
            table.setItem(r, 5, table_item(labels))
            table.setItem(r, 6, table_item(fmt_time(f.get("ts"))))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            sev = QComboBox()
            for s in SEVERITY_ORDER:
                sev.addItem(SEVERITY_LABELS.get(s, "未分级"), s)
            sev.setCurrentIndex(SEVERITY_ORDER.index(f.get("severity", "")) if f.get("severity") in SEVERITY_ORDER else SEVERITY_ORDER.index(""))
            sev.currentIndexChanged.connect(lambda idx, fid=f["id"], c=sev: self._set_sev(fid, c.itemData(idx)))
            h.addWidget(sev)
            table.setCellWidget(r, 7, cell)
        table.resizeRowsToContents()
        table.setColumnWidth(4, 240)

    def _set_sev(self, fid: str, sev: str) -> None:
        try:
            self.client.patch(f"/api/exploration/findings/{fid}", json={"severity": sev or None})
        except ApiError:
            pass

    def _set_status(self, fid: str, status: str) -> None:
        try:
            self.client.patch(f"/api/exploration/findings/{fid}", json={"status": status or None})
        except ApiError:
            pass


class RetestsTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "漏洞", "严重度", "最近复测状态", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table)
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {COL['text_dim']};")
        lay.addWidget(self.info)
        self.refresh()

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            data = self.client.get(f"/api/exploration/findings?task={self.tid}&size=200")
        except ApiError:
            return
        items = data if isinstance(data, list) else data.get("items", [])
        table = self.table
        table.setRowCount(0)
        active_ids = {str(r["finding_id"]) for r in self._active_retests()}
        for f in items:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(f["id"]))
            table.setItem(r, 1, table_item(f.get("name") or f.get("vulnclass") or ""))
            table.setItem(r, 2, table_item(SEVERITY_LABELS.get(f.get("severity", ""), "-"),
                                           severity_color(f.get("severity", ""))))
            running = f["id"] in active_ids
            table.setItem(r, 3, table_item("复测中…" if running else "可复测",
                                           COL["warn"] if running else COL["text_dim"]))
            btn = QPushButton("发起复测")
            if running:
                btn.setEnabled(False)
            btn.clicked.connect(lambda _, fid=f["id"]: self._retest(fid))
            table.setCellWidget(r, 4, btn)
        table.resizeRowsToContents()

    def _active_retests(self) -> list:
        try:
            return self.client.get("/api/exploration/findings/retests/active")
        except ApiError:
            return []

    def _retest(self, fid: str) -> None:
        try:
            self.client.post(f"/api/exploration/findings/{fid}/retests", json={})
            QMessageBox.information(self, "复测", "复测已发起，完成后将自动更新漏洞状态。")
        except ApiError as e:
            QMessageBox.warning(self, "复测失败", e.message)
        self.refresh()

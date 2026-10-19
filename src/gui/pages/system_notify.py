"""漏洞 IM 推送页：渠道管理 + 投递历史。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, table_item, fmt_time
from .base import BasePage


class SystemNotifyPage(BasePage):
    def title_text(self) -> str:
        return "漏洞推送"

    def _build(self) -> None:
        super()._build()
        new_btn = button("＋ 新建渠道", primary=True)
        new_btn.clicked.connect(self._new)
        self.add_toolbar(new_btn)

        self.tabs = QTabWidget()
        self.add_to_content(self.tabs)

        self.channels_tab = QWidget()
        self.deliveries_tab = QWidget()
        self.tabs.addTab(self.channels_tab, "推送渠道")
        self.tabs.addTab(self.deliveries_tab, "投递记录")

        # 渠道表
        lay = QVBoxLayout(self.channels_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        self.ch_table = QTableWidget(0, 7)
        self.ch_table.setHorizontalHeaderLabels(["ID", "名称", "类型", "模式", "启用", "限流", "操作"])
        self.ch_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ch_table.verticalHeader().setVisible(False)
        lay.addWidget(self.ch_table)

        # 投递记录
        lay2 = QVBoxLayout(self.deliveries_tab)
        lay2.setContentsMargins(0, 8, 0, 0)
        self.del_table = QTableWidget(0, 7)
        self.del_table.setHorizontalHeaderLabels(["ID", "事件", "渠道", "状态", "尝试次数", "错误", "时间"])
        self.del_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.del_table.verticalHeader().setVisible(False)
        lay2.addWidget(self.del_table)

        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(8000)
        self.refresh()

    def refresh(self) -> None:
        try:
            channels = self.client.get("/api/notify/channels")
            self._fill_channels(channels)
            deliveries = self.client.get("/api/notify/deliveries", params={"size": 100})
            self._fill_deliveries(deliveries.get("items", []))
        except ApiError:
            pass

    def _fill_channels(self, channels) -> None:
        t = self.ch_table
        t.setRowCount(0)
        for c in channels:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, table_item(c["id"]))
            t.setItem(r, 1, table_item(c["name"]))
            t.setItem(r, 2, table_item(c.get("kind", ""), COL["accent"]))
            t.setItem(r, 3, table_item(c.get("mode", "")))
            t.setItem(r, 4, table_item("启用" if c.get("enabled") else "停用",
                                       COL["ok"] if c.get("enabled") else COL["text_mute"]))
            t.setItem(r, 5, table_item(c.get("rate_per_min", 0)))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            for text, cb in [("编辑", lambda _, cid=c["id"]: self._edit(cid)),
                             ("测试", lambda _, cid=c["id"]: self._test(cid)),
                             ("删除", lambda _, cid=c["id"]: self._delete(cid))]:
                b = button(text, obj="Link", danger=(text == "删除"))
                b.clicked.connect(cb)
                h.addWidget(b)
            h.addStretch()
            t.setCellWidget(r, 6, cell)
        t.resizeRowsToContents()

    def _fill_deliveries(self, items) -> None:
        t = self.del_table
        t.setRowCount(0)
        for d in items:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, table_item(d["id"]))
            t.setItem(r, 1, table_item(d.get("event_kind") or d.get("finding_id") or ""))
            t.setItem(r, 2, table_item(d.get("channel_name") or ""))
            state = d.get("state", "")
            color = COL["ok"] if state == "sent" else COL["warn"] if state == "pending" else COL["danger"]
            t.setItem(r, 3, table_item(state, color))
            t.setItem(r, 4, table_item(d.get("attempts", 0)))
            t.setItem(r, 5, table_item((d.get("last_error") or "")[:40]))
            t.setItem(r, 6, table_item(fmt_time(d.get("created_at"))))
        t.resizeRowsToContents()

    def _new(self) -> None:
        self._edit()

    def _edit(self, cid=None) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑渠道" if cid else "新建渠道")
        dlg.setFixedWidth(480)
        lay = QFormLayout(dlg)
        channel = None
        if cid:
            for c in self.client.get("/api/notify/channels"):
                if c["id"] == str(cid):
                    channel = c
        name = QLineEdit(channel["name"] if channel else "")
        kind = QComboBox()
        kinds = self.client.get("/api/notify/meta").get("kinds", [])
        for k in kinds:
            kind.addItem(k, k)
        if channel:
            idx = kind.findData(channel.get("kind"))
            if idx >= 0:
                kind.setCurrentIndex(idx)
        webhook = QLineEdit(channel and channel["config"].get("webhook") or "" if channel else "")
        secret = QLineEdit(channel and channel["config"].get("secret") or "" if channel else "")
        secret.setEchoMode(QLineEdit.Password)
        enabled = QCheckBox("启用渠道")
        enabled.setChecked((channel or {}).get("enabled", True))
        rate = QSpinBox()
        rate.setRange(0, 1000)
        rate.setValue((channel or {}).get("rate_per_min", 20))
        lay.addRow("名称", name)
        lay.addRow("类型", kind)
        lay.addRow("Webhook URL", webhook)
        lay.addRow("密钥(secret)", secret)
        lay.addRow("", enabled)
        lay.addRow("限流/分", rate)
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
        config = {}
        if webhook.text().strip():
            config["webhook"] = webhook.text().strip()
        if secret.text().strip() and secret.text() != "******":
            config["secret"] = secret.text().strip()
        body = {"name": name.text().strip(), "kind": kind.currentData(),
                "config": config, "enabled": enabled.isChecked(),
                "rate_per_min": rate.value()}
        if channel:
            body["id"] = channel["id"]
        try:
            self.client.post("/api/notify/channels", json=body)
        except ApiError as e:
            QMessageBox.warning(self, "保存失败", e.message)
        self.refresh()

    def _test(self, cid: str) -> None:
        try:
            r = self.client.post(f"/api/notify/channels/{cid}/test")
            QMessageBox.information(self, "测试", r.get("message", "测试消息已发送"))
        except ApiError as e:
            QMessageBox.warning(self, "测试失败", e.message)

    def _delete(self, cid: str) -> None:
        if QMessageBox.question(self, "删除渠道", "确认删除该渠道？") == QMessageBox.Yes:
            try:
                self.client.delete(f"/api/notify/channels/{cid}")
            except ApiError:
                pass
            self.refresh()
"""资产同步页：数据源配置 + 同步状态 + 一键同步演示数据。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import Card, button, make_badge, msg_label
from .base import BasePage


class SyncPage(BasePage):
    poll_intervals = {"refresh": 8000}

    def title_text(self) -> str:
        return "资产同步"

    def _build(self) -> None:
        super()._build()
        # ---- 数据源配置表单 ----
        self.config_card = Card("数据源配置")
        form = QFormLayout()
        form.setSpacing(8)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://…")
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("API Key")
        save_btn = button("保存配置", primary=True)
        save_btn.clicked.connect(self._save_config)
        form.addRow("URL", self.url_input)
        form.addRow("API Key", self.api_key_input)
        form.addRow("", save_btn)
        self.config_card.body.addLayout(form)
        self.add_to_content(self.config_card)

        # ---- 状态卡 ----
        self.status_card = Card("同步状态")
        self._status_row = QVBoxLayout()
        self.status_card.body.addLayout(self._status_row)
        self.add_to_content(self.status_card)

        # ---- 一键同步 ----
        sync_btn = button("一键同步演示数据")
        sync_btn.clicked.connect(self._sync_demo)
        self.add_to_content(_wrap_layout(sync_btn))

        self.refresh()

    # ---- 状态 ---------------------------------------------------------
    def refresh(self) -> None:
        try:
            data = self.client.get("/api/sync/scopesentry/status")
        except ApiError:
            return
        configured = bool(data.get("configured"))
        server = data.get("server") or {}
        api_ok = bool(data.get("api_key_configured"))

        while self._status_row.count():
            it = self._status_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        color = COL["ok"] if configured else COL["warn"]
        badge = make_badge("已配置" if configured else "未配置", color)
        self._status_row.addWidget(badge)
        self._status_row.addWidget(msg_label(
            f"服务器: {server.get('url', '-')} · 启用: {'是' if server.get('enabled') else '否'}"))
        self._status_row.addWidget(msg_label(
            f"API Key 已配置: {'是' if api_ok else '否'}",
            COL["ok"] if api_ok else COL["warn"]))

    # ---- 操作 ---------------------------------------------------------
    def _save_config(self) -> None:
        body = {
            "url": self.url_input.text().strip(),
            "api_key": self.api_key_input.text().strip(),
        }
        try:
            self.client.post("/api/sync/scopesentry/datasource", json=body)
        except ApiError as e:
            self.toast(f"保存失败: {e.message}", COL["danger"])
            return
        self.api_key_input.clear()
        self.toast("配置已保存")
        self.refresh()

    def _sync_demo(self) -> None:
        try:
            self.client.post("/api/sync/scopesentry/sync", json={"company_name": "演示企业"})
        except ApiError as e:
            self.toast(f"同步失败: {e.message}", COL["danger"])
            return
        self.toast("演示数据同步完成")
        self.refresh()


def _wrap_layout(w: QWidget) -> QWidget:
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(w)
    lay.addStretch()
    return holder
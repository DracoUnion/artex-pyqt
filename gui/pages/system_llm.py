"""LLM 配置页：profile 列表 / 新建 / 编辑 / 设为激活 / 删除 / 测试。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import (
    Card, button, msg_label, table_item,
)
from .base import BasePage

FORMATS = ["openai", "anthropic", "openai-responses"]


class SystemLLMPage(BasePage):
    poll_intervals = {"refresh": 8000}
    refresh_on_show = ["refresh"]

    def title_text(self) -> str:
        return "LLM 配置"

    def _build(self) -> None:
        super()._build()
        new_btn = button("＋ 新建配置", primary=True)
        new_btn.clicked.connect(lambda: self._open_form())
        self.add_toolbar(new_btn)

        # 顶部激活配置状态卡
        self.active_card = Card("激活配置")
        self.active_lbl = msg_label("加载中…")
        self.active_lbl.setWordWrap(True)
        self.active_card.body.addWidget(self.active_lbl)
        self.add_to_content(self.active_card)

        # 表格
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["名称", "模型", "格式", "激活", "优先", "流式", "操作"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.add_to_content(self.table)

        self._profiles: list = []
        self.refresh()

    # ---- 刷新 ---------------------------------------------------------
    def refresh(self) -> None:
        try:
            data = self.client.get("/api/llm/profiles")
        except ApiError:
            return
        profiles = data.get("profiles", []) if isinstance(data, dict) else []
        self._profiles = profiles

        # 激活配置：后端以 is_default 标记当前激活
        act = next((p for p in profiles if p.get("is_default")), None)
        if act:
            self.active_lbl.setText(
                f"当前激活：{act.get('name', '')} · {act.get('model', '')} · {act.get('format', '')}"
            )
            self.active_lbl.setStyleSheet(f"color: {COL['ok']};")
        else:
            self.active_lbl.setText("未设置激活配置")
            self.active_lbl.setStyleSheet(f"color: {COL['text_mute']};")

        # 表格
        table = self.table
        table.setRowCount(0)
        for p in profiles:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(p.get("name", "")))
            table.setItem(r, 1, table_item(p.get("model", "")))
            table.setItem(r, 2, table_item(p.get("format", ""), COL["accent"]))
            is_act = bool(p.get("is_default"))
            table.setItem(r, 3, table_item("激活" if is_act else "-",
                                           COL["ok"] if is_act else COL["text_mute"]))
            table.setItem(r, 4, table_item(p.get("priority", 0)))
            table.setItem(r, 5, table_item("是" if p.get("streaming", True) else "否"))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            pid = p.get("id")
            for text, cb in [
                ("编辑", lambda _, _pid=pid: self._open_form(_pid)),
                ("测试", lambda _, _pid=pid: self._test(_pid)),
            ]:
                b = button(text, obj="Link")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(cb)
                h.addWidget(b)
            if not is_act:
                d = button("删除", obj="Link", danger=True)
                d.clicked.connect(lambda _, _pid=pid: self._delete(_pid))
                h.addWidget(d)
            if not is_act:
                a = button("设为激活", obj="Link")
                a.setCursor(Qt.PointingHandCursor)
                a.clicked.connect(lambda _, _pid=pid: self._set_active(_pid))
                h.addWidget(a)
            h.addStretch()
            table.setCellWidget(r, 6, cell)
        table.resizeRowsToContents()
        table.setColumnWidth(1, 220)

    # ---- 操作 ---------------------------------------------------------
    def _open_form(self, profile=None) -> None:
        if profile is not None and not isinstance(profile, dict):
            profile = next((p for p in self._profiles if p.get("id") == profile), None)
            if profile is None:
                try:
                    data = self.client.get("/api/llm/profiles")
                    profiles = data.get("profiles", []) if isinstance(data, dict) else []
                    profile = next((p for p in profiles if p.get("id") == profile), None)
                except ApiError:
                    profile = None
        dlg = LLMProfileDialog(self.app, profile, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def _set_active(self, pid) -> None:
        try:
            self.client.post("/api/llm/profiles/active", json={"id": int(pid)})
            self.toast("已切换激活配置")
        except ApiError as e:
            QMessageBox.warning(self, "操作失败", e.message)
        self.refresh()

    def _delete(self, pid) -> None:
        if QMessageBox.question(self, "删除配置", "确认删除该 LLM 配置？") != QMessageBox.Yes:
            return
        try:
            self.client.delete(f"/api/llm/profiles/{pid}")
            self.refresh()
        except ApiError as e:
            QMessageBox.warning(self, "删除失败", e.message)

    def _test(self, pid) -> None:
        try:
            r = self.client.post("/api/llm/test", json={"profile_id": int(pid)})
        except ApiError as e:
            self.toast(e.message, COL["danger"])
            return
        if not isinstance(r, dict):
            self.toast(str(r))
            return
        if r.get("ok"):
            lat = r.get("latency_ms")
            self.toast(f"测试成功{f' · {lat}ms' if lat else ''}")
        else:
            self.toast(r.get("error") or "测试失败", COL["danger"])


class LLMProfileDialog(QDialog):
    """新建 / 编辑 LLM profile 表单。"""

    def __init__(self, app, profile=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.profile = profile or {}
        self.setWindowTitle("编辑配置" if profile else "新建配置")
        self.setFixedWidth(480)

        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit(self.profile.get("name", ""))
        self.name.setPlaceholderText("配置名称")
        self.format_cb = QComboBox()
        for f in FORMATS:
            self.format_cb.addItem(f, f)
        if self.profile and self.profile.get("format") in FORMATS:
            self.format_cb.setCurrentIndex(FORMATS.index(self.profile["format"]))
        self.model = QLineEdit(self.profile.get("model", ""))
        self.model.setPlaceholderText("如 gpt-4o / claude-3-5-sonnet")
        self.base_url = QLineEdit(self.profile.get("base_url", ""))
        self.base_url.setPlaceholderText("如 https://api.openai.com/v1")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("留空则不修改" if profile else "必填")
        self.proxy = QLineEdit(self.profile.get("proxy", ""))
        self.proxy.setPlaceholderText("可选，如 http://127.0.0.1:7890")
        self.priority = QSpinBox()
        self.priority.setRange(0, 9999)
        self.priority.setValue(int(self.profile.get("priority", 0) or 0))
        self.streaming = QCheckBox("流式 (SSE)")
        self.streaming.setChecked(bool(self.profile.get("streaming", True)))
        self.make_active = QCheckBox("保存后设为激活")
        self.make_active.setChecked(bool(self.profile.get("is_default")))

        form.addRow("名称", self.name)
        form.addRow("格式", self.format_cb)
        form.addRow("模型", self.model)
        form.addRow("Base URL", self.base_url)
        form.addRow("API Key", self.api_key)
        form.addRow("代理", self.proxy)
        form.addRow("优先级", self.priority)
        form.addRow("", self.streaming)
        form.addRow("", self.make_active)
        lay.addLayout(form)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {COL['danger']};")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)

        btns = QHBoxLayout()
        ok = button("保存", primary=True)
        ok.clicked.connect(self._submit)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _submit(self) -> None:
        if not self.name.text().strip():
            self.error.setText("名称不能为空")
            return
        body = {
            "name": self.name.text().strip(),
            "format": self.format_cb.currentData(),
            "model": self.model.text().strip(),
            "base_url": self.base_url.text().strip() or None,
            "proxy": self.proxy.text().strip() or None,
            "priority": self.priority.value(),
            "streaming": self.streaming.isChecked(),
        }
        ak = self.api_key.text().strip()
        if ak:
            body["api_key"] = ak
        if self.profile:
            body["id"] = int(self.profile["id"])
        try:
            r = self.client.post("/api/llm/profiles", json=body)
            saved_id = int(self.profile["id"]) if self.profile else int(
                (r or {}).get("id", 0)
            )
            if self.make_active.isChecked() and saved_id:
                self.client.post("/api/llm/profiles/active", json={"id": saved_id})
            self.accept()
        except ApiError as e:
            self.error.setText(e.message)
"""后端日志页：SSE 实时流 + 历史回拉 + 分级筛选。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, msg_label
from .base import BasePage

LEVEL_COLORS = {
    "debug": "#8ea0b0", "info": "#e2e8f0", "warning": "#fbbf24",
    "error": "#f87171", "critical": "#ef4444",
}


class SystemLogsPage(BasePage):
    def title_text(self) -> str:
        return "后端日志"

    def _build(self) -> None:
        super()._build()
        self.level = QComboBox()
        for lv in ("all", "info", "warning", "error", "critical"):
            self.level.addItem(lv.upper(), lv)
        self.level.currentIndexChanged.connect(self._apply_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("过滤关键字…")
        self.search.setMaximumWidth(200)
        self.search.returnPressed.connect(self._apply_filter)
        self.pause = QCheckBox("暂停")
        self.pause.toggled.connect(self._apply_filter)
        self.add_toolbar(self.level)
        self.add_toolbar(self.search)
        self.add_toolbar(self.pause)

        self.text = QTextBrowser()
        self.text.setReadOnly(True)
        f = QFont("Consolas")
        f.setPointSize(9)
        self.text.setFont(f)
        self.add_to_content(self.text)

        self._lines = []  # 原始行
        self._seq = 0
        self._sse = None
        self._connect_sse()

    def on_shown(self) -> None:
        self._connect_sse()

    def _connect_sse(self) -> None:
        if self._sse:
            self._sse.shutdown()
        from ..api_client import SseManager
        self._sse = SseManager(self.client, self)
        worker = self._sse.subscribe(f"logs-{id(self)}", "/api/logs/stream", {"since": 0})
        worker.event.connect(self._on_sse)
        self._load_history()

    def _load_history(self) -> None:
        try:
            r = self.client.get("/api/logs/history", params={"before": 2 ** 62, "limit": 300})
        except ApiError:
            return
        items = r.get("items", [])
        self.text.clear()
        self._lines = []
        for e in items:
            self._lines.append(e)
            self._seq = max(self._seq, e.get("seq", 0))
        self._render()

    def _on_sse(self, ev: dict) -> None:
        if self.pause.isChecked():
            self._lines.append(ev)
            return
        self._lines.append(ev)
        self._seq = max(self._seq, ev.get("seq", 0))
        if len(self._lines) > 3000:
            self._lines = self._lines[-2000:]
        self._render()

    def _apply_filter(self) -> None:
        self._render()

    def _render(self) -> None:
        level = self.level.currentData()
        q = self.search.text().strip().lower()
        self.text.clear()
        for e in self._lines:
            lv = e.get("level", "info")
            if level != "all" and lv != level:
                continue
            line = f"{e.get('ts', '')}  [{lv.upper()}] {e.get('tag', '')}  {e.get('text', '')}"
            if q and q not in line.lower():
                continue
            color = LEVEL_COLORS.get(lv, "#e2e8f0")
            self.text.append(f"<span style='color:#5b6b7a'>{e.get('ts','')}</span> "
                             f"<span style='color:{color}; font-weight:600'>[{lv.upper()}]</span> "
                             f"<span style='color:#8ea0b0'>{e.get('tag','')}</span> "
                             f"<span style='color:#e2e8f0'>{e.get('text','')}</span>")
        self.text.moveCursor(QTextCursor.End)

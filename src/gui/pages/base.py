"""页面基类：统一轮询 / 导航 / 布局工具。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from ..theme import COL


class BasePage(QFrame):
    """所有页面的基类。封装 QTimer 轮询、标题栏、内容区。"""

    def __init__(self, app, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self._timers: list[QTimer] = []
        self._build()
        self._setup_polling()

    # ---- 布局 ---------------------------------------------------------
    def _build(self) -> None:
        self.root_lay = QVBoxLayout(self)
        self.root_lay.setContentsMargins(24, 16, 24, 16)
        self.root_lay.setSpacing(14)

        title = QLabel(self.title_text())
        title.setObjectName("TopbarTitle")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.root_lay.addWidget(title)

        if hasattr(self, "subtitle_text"):
            sub = QLabel(self.subtitle_text())
            sub.setStyleSheet(f"color: {COL['text_dim']}; font-size: 12px;")
            self.root_lay.addWidget(sub)

        # 工具栏（可选）
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(8)
        self.root_lay.addLayout(self.toolbar)

        # 滚动内容区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.content_lay = QVBoxLayout(self.content)
        self.content_lay.setContentsMargins(0, 0, 0, 0)
        self.content_lay.setSpacing(12)
        scroll.setWidget(self.content)
        self.root_lay.addWidget(scroll, 1)

    def title_text(self) -> str:
        return self.__class__.__name__

    # ---- 轮询 ---------------------------------------------------------
    def _setup_polling(self) -> None:
        intervals = getattr(self, "poll_intervals", {})
        for attr, ms in intervals.items():
            timer = QTimer(self)
            timer.setInterval(ms)
            timer.timeout.connect(lambda a=attr: getattr(self, a)())
            timer.start()
            self._timers.append(timer)

    def on_shown(self) -> None:
        """页面显示时刷新。"""
        for attr in getattr(self, "refresh_on_show", []):
            getattr(self, attr)()

    def stop_polling(self) -> None:
        for t in self._timers:
            t.stop()

    # ---- 工具 ---------------------------------------------------------
    def add_toolbar(self, w: QWidget) -> None:
        self.toolbar.addWidget(w)
        self.toolbar.addStretch()

    def add_to_content(self, w: QWidget) -> None:
        self.content_lay.addWidget(w)

    def clear_content(self) -> None:
        while self.content_lay.count():
            item = self.content_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def toast(self, text: str, color: str = COL["ok"]) -> None:
        """底部状态提示。"""
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {color}; padding: 4px 12px;")
        lbl.setAlignment(Qt.AlignCenter)
        self.add_to_content(lbl)
        from PySide6.QtCore import QTimer as _T
        _T.singleShot(3000, lbl.deleteLater)


class NotFoundPage(BasePage):
    def __init__(self, app, route: str):
        self._route = route
        super().__init__(app)

    def title_text(self) -> str:
        return "页面未找到"

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lbl = QLabel(f"未找到页面：{self._route}")
        lbl.setStyleSheet("color: #f87171; font-size: 16px;")
        lay.addWidget(lbl)
        lay.addStretch()

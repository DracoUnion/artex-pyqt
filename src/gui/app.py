"""ARTEX 桌面主程序：主窗口装配（无登录，直进主界面）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget,
)

from .theme import build_qss


def apply_theme(app) -> None:
    app.setStyleSheet(build_qss())


class ArtexApplication(QMainWindow):
    """主窗口：侧边栏 + 页面容器。"""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.setWindowTitle("ARTEX — AI 自主渗透测试")
        self.resize(1440, 900)

        self._current_page = None
        self._pages = {}
        self._build_layout()

    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        from .sidebar import Sidebar
        self.sidebar = Sidebar(self)
        root.addWidget(self.sidebar)

        self.page_host = QFrame()
        root.addWidget(self.page_host, 1)
        self.page_lay = QVBoxLayout(self.page_host)
        self.page_lay.setContentsMargins(0, 0, 0, 0)

    def navigate(self, route: str) -> None:
        from .pages import get_page
        page = get_page(self, route)
        # 清空现有页面
        while self.page_lay.count():
            item = self.page_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.page_lay.addWidget(page)
        self._current_page = page
        if hasattr(page, "on_shown"):
            page.on_shown()
        self.sidebar.set_current(route)
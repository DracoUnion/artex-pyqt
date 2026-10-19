"""ARTEX 桌面主程序：认证流程 + 主窗口装配。"""
from __future__ import annotations

import json
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .api_client import ApiClient, ApiError
from .theme import build_qss, COL
from .widgets import button, msg_label


def apply_theme(app) -> None:
    app.setStyleSheet(build_qss())


class SetupDialog(QDialog):
    """首次设置管理员密码。"""

    def __init__(self, client: ApiClient, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.client = client
        self.token: Optional[str] = None
        self.setWindowTitle("ARTEX 首次初始化")
        self.setFixedSize(420, 320)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(14)

        title = QLabel("ARTEX")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {COL['accent']};")
        sub = QLabel("设置管理员密码以完成初始化")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #8ea0b0;")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(8)

        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("新密码（至少 8 位）")
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.pw2.setPlaceholderText("确认密码")
        lay.addWidget(self.pw)
        lay.addWidget(self.pw2)

        self.error_lbl = msg_label("", COL["danger"])
        lay.addWidget(self.error_lbl)

        self.btn = button("初始化并进入系统", primary=True)
        self.btn.clicked.connect(self._submit)
        lay.addWidget(self.btn)
        lay.addStretch()

    def _submit(self) -> None:
        p1, p2 = self.pw.text(), self.pw2.text()
        if len(p1) < 8:
            self.error_lbl.setText("密码至少 8 位")
            return
        if p1 != p2:
            self.error_lbl.setText("两次输入不一致")
            return
        try:
            r = self.client.post("/api/auth/init", json={"password": p1})
            self.token = r["token"]
            self.accept()
        except ApiError as e:
            self.error_lbl.setText(e.message)


class LoginDialog(QDialog):
    """登录对话框。"""

    def __init__(self, client: ApiClient, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.client = client
        self.token: Optional[str] = None
        self.setWindowTitle("登录 ARTEX")
        self.setFixedSize(420, 320)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(14)

        title = QLabel("ARTEX")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {COL['accent']};")
        sub = QLabel("AI 自主渗透测试系统")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #8ea0b0;")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(8)

        self.user = QLineEdit("ARTEX")
        self.user.setPlaceholderText("用户名")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("密码")
        self.pw.returnPressed.connect(self._submit)
        lay.addWidget(self.user)
        lay.addWidget(self.pw)

        self.error_lbl = msg_label("", COL["danger"])
        lay.addWidget(self.error_lbl)

        self.btn = button("登 录", primary=True)
        self.btn.clicked.connect(self._submit)
        lay.addWidget(self.btn)
        lay.addStretch()

    def _submit(self) -> None:
        try:
            r = self.client.post("/api/auth/login", json={
                "username": self.user.text(), "password": self.pw.text(),
            })
            self.token = r["token"]
            self.accept()
        except ApiError as e:
            self.error_lbl.setText(e.message)


class ArtexApplication(QMainWindow):
    """主窗口：侧边栏 + 页面容器。"""

    def __init__(self, client, skip_auth: bool = False, token: Optional[str] = None):
        super().__init__()
        self.client = client
        self.setWindowTitle("ARTEX — AI 自主渗透测试")
        self.resize(1440, 900)

        self._current_page = None
        self._pages = {}
        if token:
            self.client.set_token(token)
        if not skip_auth:
            self._init_auth()
        self._build_layout()

    # ------------------------------------------------------------------
    def _init_auth(self) -> None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        apply_theme(app)
        try:
            status = self.client.get("/api/auth/status")
        except ApiError as e:
            QMessageBox.critical(self, "无法连接后端", e.message)
            raise SystemExit(1)
        if not status.get("initialized"):
            dlg = SetupDialog(self.client, self)
            if dlg.exec() != SetupDialog.Accepted:
                raise SystemExit(1)
            self.client.set_token(dlg.token)
        else:
            dlg = LoginDialog(self.client, self)
            if dlg.exec() != LoginDialog.Accepted:
                raise SystemExit(1)
            self.client.set_token(dlg.token)

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

    def logout(self) -> None:
        self.client.set_token(None)
        dlg = LoginDialog(self.client, self)
        if dlg.exec() == LoginDialog.Accepted:
            self.client.set_token(dlg.token)
            self.navigate("dashboard")

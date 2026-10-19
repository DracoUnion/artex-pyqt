"""ARTEX Standalone 桌面应用入口。

职责：直接装配后端 services（进程内，无 HTTP 服务）→ 主窗口 → 登录/首密设置 → 主界面。
不再启动 uvicorn / FastAPI。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def base_dir() -> Path:
    """应用根目录（含 data/、skills/、workspace/）。"""
    return Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    d = base_dir() / "data"
    os.makedirs(d, exist_ok=True)
    return d


def skills_dir() -> Path:
    d = base_dir() / "skills"
    os.makedirs(d, exist_ok=True)
    return d


def main() -> None:
    if sys.platform.startswith("win"):
        # 修复 Windows 高 DPI 模糊
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    from app.backend.gateway import Gateway
    from app.ui.api_client import ApiClient

    gateway = Gateway(str(app_data_dir()), str(skills_dir()))
    client = ApiClient(gateway)

    from PySide6.QtWidgets import QApplication
    from app.ui.app import ArtexApplication

    app = QApplication(sys.argv)
    app.setApplicationName("ARTEX")
    window = ArtexApplication(client)
    window.show()
    code = app.exec()
    gateway.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
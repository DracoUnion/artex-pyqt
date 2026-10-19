"""ARTEX Standalone 桌面应用入口。

职责：起内嵌 FastAPI 后端（:8787）→ 主窗口 → 登录/首密设置 → 主界面。
"""
from __future__ import annotations

import os
import sys
import threading
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


def _start_backend(data_dir: str, skills: str):
    """后台线程里启动 uvicorn。"""
    import uvicorn
    from app.backend.server import create_app

    app = create_app(data_dir, skills)
    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning", access_log=False)


def main() -> None:
    if sys.platform.startswith("win"):
        # 修复 Windows 高 DPI 模糊
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    data = str(app_data_dir())
    skills = str(skills_dir())
    thread = threading.Thread(target=_start_backend, args=(data, skills), daemon=True)
    thread.start()

    # 等后端就绪
    import time
    import requests
    for _ in range(60):
        try:
            r = requests.get("http://127.0.0.1:8787/api/health", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.25)

    from PySide6.QtWidgets import QApplication
    from app.ui.app import ArtexApplication

    app = QApplication(sys.argv)
    app.setApplicationName("ARTEX")
    window = ArtexApplication("http://127.0.0.1:8787")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

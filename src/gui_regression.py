"""GUI 回归测试：离屏实例化主窗口，遍历所有页面并触发刷新/交互，捕获异常。

用法（在 src/ 下）：
    PYTHONPATH=. python gui_regression.py

不依赖桌面控制权（QT_QPA_PLATFORM=offscreen），但实例化的是真实的
ArtexApplication 与全部页面，能复现页面构建/刷新/交互路径上的崩溃。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)

from bootstrap import build_context
from gui.api_client import ApiClient
from gui.pages import ROUTES

FAILURES: list = []
OK_COUNT = 0


def check(name: str, fn) -> None:
    global OK_COUNT
    try:
        fn()
        OK_COUNT += 1
        print(f"[OK] {name}")
    except Exception:
        FAILURES.append((name, traceback.format_exc()))
        print(f"[FAIL] {name}")
        print("      " + traceback.format_exc().splitlines()[-1])


def main() -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    data_dir = tempfile.mkdtemp(prefix="regress-data-")
    skills_dir = tempfile.mkdtemp(prefix="regress-skills-")
    print(f"temp data: {data_dir}\ntemp skills: {skills_dir}")

    ctx = build_context(data_dir=data_dir, skills_dir=skills_dir)
    client = ApiClient(ctx.gateway)

    # ---- 1. 后端健康 ----
    check("backend health", lambda: client.get("/api/health"))

    # ---- 2. 播种演示数据：任务 + 引擎空转几秒 ----
    task = None
    def seed_task():
        nonlocal task
        r = client.post("/api/tasks", json={
            "name": "回归演示任务",
            "description": "GUI 回归测试任务",
            "goal": "对 demo.test.example 做基础渗透测试",
        })
        assert r and r.get("id"), f"create task failed: {r}"
        task = r
    check("seed demo task", seed_task)

    if task:
        time.sleep(6)  # 让演示引擎产出节点/发现/资产
        t = client.get(f"/api/tasks/{task['id']}")
        print(f"   task after run: status={t.get('status')} in_flight={t.get('in_flight')} "
              f"findings={t.get('findings')} goals={t.get('goals_met')}/{t.get('goals_total')}")

    # 会话与消息
    conv_id = None
    def seed_conv():
        nonlocal conv_id
        c = client.post("/api/conversations", json={"agent_key": "assistant", "title": "回归会话"})
        conv_id = c["id"]
        client.post(f"/api/conversations/{conv_id}/messages", json={"message": "你好，测试"})
    check("seed conversation", seed_conv)

    # 同步演示数据（资产）
    check("seed demo assets", lambda: client.post("/api/sync/scopesentry/sync",
                                                  json={"company_name": "回归企业"}))

    # ---- 3. 装配 GUI ----
    app = QApplication(sys.argv)
    from gui.theme import COL
    from gui.app import apply_theme
    apply_theme(app)

    from gui.app import ArtexApplication
    window = ArtexApplication(client)
    window.show()
    app.processEvents()

    # ---- 4. 遍历全部路由 ----
    for route in ROUTES:
        def go(r=route):
            window.navigate(r)
            page = window._current_page
            app.processEvents()
            if hasattr(page, "on_shown"):
                page.on_shown()
            for attr in ("refresh", "refresh_fast", "refresh_slow", "refresh_list",
                         "refresh_archives", "refresh_conversations", "refresh_sessions"):
                if hasattr(page, attr):
                    getattr(page, attr)()
                    app.processEvents()
        check(f"page:{route}", go)

    # ---- 5. 任务列表交互 ----
    def tasks_interact():
        window.navigate("tasks")
        page = window._current_page
        app.processEvents()
        # 切到归档 Tab
        if hasattr(page, "_tabs"):
            page._tabs.setCurrentIndex(1)
            page.refresh()
            app.processEvents()
            page._tabs.setCurrentIndex(0)
    check("tasks: archive tab", tasks_interact)

    # ---- 6. 任务详情（10 Tab） ----
    dlg = None
    if task:
        def task_detail():
            nonlocal dlg
            from gui.pages.task_detail import TaskDetailDialog
            dlg = TaskDetailDialog(window, str(task["id"]), window)
            dlg.show()
            app.processEvents()
            for i in range(dlg.tabs.count()):
                dlg.tabs.setCurrentIndex(i)
                app.processEvents()
        check("task detail 10 tabs", task_detail)

    # ---- 7. 对话页交互 ----
    def chat_interact():
        window.navigate("chat")
        page = window._current_page
        app.processEvents()
        if conv_id:
            for i in range(page.conv_list.count()):
                it = page.conv_list.item(i)
                if it.data(2) == conv_id:  # Qt.UserRole
                    page._select_conv(it)
                    break
    check("chat: select conv", chat_interact)

    # ---- 8. 系统页交互 ----
    def sys_pages():
        for route in ("system/llm", "system/mcp", "system/skills", "system/intercept",
                      "system/approvals", "system/notify", "system/logs", "system/settings"):
            window.navigate(route)
            page = window._current_page
            app.processEvents()
            if hasattr(page, "refresh"):
                page.refresh()
            app.processEvents()
    check("system pages refresh", sys_pages)

    # ---- 9. 任务详情各 Tab 再刷新一轮 ----
    if dlg is not None:
        def detail_refresh():
            for i in range(dlg.tabs.count()):
                tab = dlg.tabs.widget(i)
                dlg.tabs.setCurrentIndex(i)
                if hasattr(tab, "refresh"):
                    tab.refresh()
                app.processEvents()
        check("task detail tabs refresh", detail_refresh)

    # ---- 10. 工作空间/流量/发现/资产 ----
    for route, attr in (("workspace", "_load_root"), ("traffic", "refresh"),
                        ("findings", "refresh_list"), ("assets", "refresh"),
                        ("commands", "refresh"), ("llm-records", "refresh"),
                        ("sync", "refresh")):
        def go(r=route, a=attr):
            window.navigate(r)
            page = window._current_page
            app.processEvents()
            if hasattr(page, a):
                getattr(page, a)()
            app.processEvents()
        check(f"interact:{route}", go)

    # 工作空间：展开树
    def ws_tree():
        window.navigate("workspace")
        page = window._current_page
        app.processEvents()
        if page.tree.topLevelItemCount():
            root = page.tree.topLevelItem(0)
            page._load_children(root)
    check("workspace: expand tree", ws_tree)

    # 流量：翻页
    def traffic_page():
        window.navigate("traffic")
        page = window._current_page
        page.refresh()
        app.processEvents()
        page._page(1)
        page._page(-1)
    check("traffic: pagination", traffic_page)

    # ---- 11. 关闭清理 ----
    print("[trace] start cleanup", flush=True)
    if dlg is not None:
        print("[trace] closing dlg", flush=True)
        dlg.close()
        app.processEvents()
    print("[trace] dlg closed", flush=True)
    window.close()
    app.processEvents()
    print("[trace] window closed", flush=True)
    from gui.api_client import shutdown_all_sse
    shutdown_all_sse()
    print("[trace] sse shutdown", flush=True)
    ctx.gateway.close()
    print("[trace] gateway closed", flush=True)
    ctx.tasks.shutdown()
    print("[trace] tasks shutdown", flush=True)

    print("\n" + "=" * 60)
    print(f"PASS: {OK_COUNT}   FAIL: {len(FAILURES)}")
    for name, tb in FAILURES:
        print(f"\n--- FAIL: {name} ---")
        print(tb)
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
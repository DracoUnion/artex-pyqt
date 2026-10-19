"""任务列表：当前/归档双 Tab + 筛选 + 新建 + 批量操作。"""
from __future__ import annotations

import json
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import (
    Card, button, fmt_status, fmt_time, format_duration, make_badge, severity_color,
    status_color, table_item,
)
from .base import BasePage

STATUS_FILTERS = [("all", "全部状态"), ("running", "运行中"), ("paused", "已暂停"),
                  ("created", "已创建"), ("done", "已完成"), ("failed", "失败")]


class TasksPage(BasePage):
    interval_refresh = None

    def title_text(self) -> str:
        return "任务"

    def _build(self) -> None:
        super()._build()
        self._state = {"archived": False}
        self._tabs = QTabWidget()
        self._tasks_tab = QWidget()
        self._archive_tab = QWidget()
        self._tabs.addTab(self._tasks_tab, "当前任务")
        self._tabs.addTab(self._archive_tab, "已归档")
        self._tabs.currentChanged.connect(lambda i: (self._state.update(archived=i == 1), self.refresh()))
        self.add_to_content(self._tabs)
        self._build_tasks_tab()
        self._build_archive_tab()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.start(8000)
        self.refresh()

    # ---- 当前任务 Tab --------------------------------------------------
    def _build_tasks_tab(self) -> None:
        lay = QVBoxLayout(self._tasks_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索任务…")
        self.search.setMaximumWidth(220)
        self.search.returnPressed.connect(self.refresh)
        self.status_filter = QComboBox()
        for v, t in STATUS_FILTERS:
            self.status_filter.addItem(t, v)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.cat_filter = QComboBox()
        self.cat_filter.addItem("全部分类", None)
        self.cat_filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.search)
        bar.addWidget(self.status_filter)
        bar.addWidget(self.cat_filter)
        bar.addStretch()
        new_btn = button("＋ 新建任务", primary=True)
        new_btn.clicked.connect(self._new_task)
        cat_btn = button("分类管理")
        cat_btn.clicked.connect(self._manage_categories)
        bar.addWidget(cat_btn)
        bar.addWidget(new_btn)
        lay.addLayout(bar)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            ["☑", "ID", "任务", "描述", "状态", "目标", "漏洞(严/高/中/低)", "在途", "创建时间", "运行时长", "操作"]
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table)

        ops = QHBoxLayout()
        for text, cb, danger in [
            ("批量暂停", self._batch_control("pause"), False),
            ("批量继续", self._batch_control("resume"), False),
            ("批量归档", self._batch_archive, False),
            ("批量删除", self._batch_delete, True),
        ]:
            b = button(text, danger=danger)
            b.clicked.connect(cb)
            ops.addWidget(b)
        ops.addStretch()
        lay.addLayout(ops)

    def _status_count(self, findings: dict) -> str:
        return f"{findings.get('critical',0)}/{findings.get('high',0)}/{findings.get('medium',0)}/{findings.get('low',0)}"

    def _check_item(self, row: int) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item.setCheckState(Qt.CheckState.Unchecked)
        return item

    # ---- 归档 Tab -----------------------------------------------------
    def _build_archive_tab(self) -> None:
        lay = QVBoxLayout(self._archive_tab)
        lay.setContentsMargins(0, 8, 0, 0)
        self._archive_table = QTableWidget(0, 7)
        self._archive_table.setHorizontalHeaderLabels(
            ["☑", "ID", "任务名", "归档前状态", "进度", "归档时间", "操作"])

        self._archive_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._archive_table.verticalHeader().setVisible(False)
        self._archive_table.setAlternatingRowColors(True)
        lay.addWidget(self._archive_table)
        ops = QHBoxLayout()
        for text, cb, danger in [("还原选中", self._restore_batch, False),
                                 ("永久删除", self._delete_archives, True)]:
            b = button(text, danger=danger)
            b.clicked.connect(cb)
            ops.addWidget(b)
        ops.addStretch()
        lay.addLayout(ops)

    # ---- 刷新 ---------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._refresh_categories()
            if self._state["archived"]:
                self._refresh_archives()
            else:
                self._refresh_tasks()
        except ApiError:
            pass

    def _refresh_categories(self) -> None:
        cats = self.client.get("/api/task-categories")
        cur = self.cat_filter.currentData()
        self.cat_filter.blockSignals(True)
        self.cat_filter.clear()
        self.cat_filter.addItem("全部分类", None)
        for c in cats:
            self.cat_filter.addItem(c["name"], int(c["id"]))
        idx = self.cat_filter.findData(cur)
        self.cat_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.cat_filter.blockSignals(False)

    def _refresh_tasks(self) -> None:
        params = {}
        if self.search.text().strip():
            params["q"] = self.search.text().strip()
        if self.status_filter.currentData() not in (None, "all"):
            params["status"] = self.status_filter.currentData()
        if self.cat_filter.currentData():
            params["category_id"] = self.cat_filter.currentData()
        rows = self.client.get("/api/tasks", params=params)
        items = rows.get("items", []) if isinstance(rows, dict) else rows
        table = self.table
        table.setRowCount(0)
        for t in items:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, self._check_item(r))
            table.setItem(r, 1, table_item(t["id"]))
            table.setItem(r, 2, table_item(t.get("name") or t["description"]))
            table.setItem(r, 3, table_item(t.get("goal") or ""))
            table.setItem(r, 4, table_item(fmt_status(t["status"]), status_color(t["status"])))
            table.setItem(r, 5, table_item(f"{t.get('goals_met',0)}/{t.get('goals_total',0)}"))
            table.setItem(r, 6, table_item(self._status_count(t.get("findings", {}))))
            table.setItem(r, 7, table_item(t.get("in_flight", 0)))
            table.setItem(r, 8, table_item(fmt_time(t.get("created_at") if t.get("created_unix") is None else None)))
            dur = (t.get("last_activity_unix") or t.get("created_unix"))
            table.setItem(r, 9, table_item(self._duration(t)))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            for text, cb in [("详情", lambda _, i=t["id"]: self._open_detail(i)),
                             ("暂停", lambda _, i=t["id"]: self._control(i, "pause")),
                             ("继续", lambda _, i=t["id"]: self._control(i, "resume")),
                             ("归档", lambda _, i=t["id"]: self._archive_task(i)),
                             ("删除", lambda _, i=t["id"]: self._delete_task(i))]:
                b = button(text, obj="Link")
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(cb)
                h.addWidget(b)
            h.addStretch()
            table.setCellWidget(r, 10, cell)
        table.resizeRowsToContents()

    def _duration(self, t: dict) -> str:
        created = t.get("created_unix")
        if not created:
            return "-"
        end = t.get("completed_unix") or t.get("last_activity_unix") or int(time.time())
        return format_duration(end - created)

    def _refresh_archives(self) -> None:
        data = self.client.get("/api/task-archives")
        item = data.get("items", [])
        table = self._archive_table
        table.setRowCount(0)
        for a in item:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, self._check_item(r))
            table.setItem(r, 1, table_item(a["id"]))
            table.setItem(r, 2, table_item(a["task_name"] or a["task_description"] or ""))
            table.setItem(r, 3, table_item(a["original_status"]))
            table.setItem(r, 4, table_item(f"{a['progress']}%"))
            table.setItem(r, 5, table_item(fmt_time(a.get("archived_at") or a.get("created_at"))))
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(2, 2, 2, 2)
            for text, cb in [("还原", lambda _, aid=a["id"]: self._restore_one(aid))]:
                b = button(text, obj="Link")
                b.clicked.connect(cb)
                h.addWidget(b)
            h.addStretch()
            table.setCellWidget(r, 6, cell)
        table.resizeRowsToContents()

    def _selected_ids(self, table: QTableWidget) -> list:
        ids = []
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                cell_id = table.item(r, 1).text()
                ids.append(cell_id)
        return ids

    # ---- 操作 ---------------------------------------------------------
    def _open_detail(self, tid: str) -> None:
        self._open_task_detail(tid)

    def _open_task_detail(self, tid: str) -> None:
        from .task_detail import TaskDetailDialog
        dlg = TaskDetailDialog(self.app, tid, self)
        dlg.show()

    def _control(self, tid: str, action: str) -> None:
        if action == "delete":
            self._delete_task(tid)
            return
        self.client.post(f"/api/tasks/{tid}/control", json={"action": action})
        self.refresh()

    def _archive_task(self, tid: str) -> None:
        self.client.post(f"/api/tasks/{tid}/archive")
        self.refresh()

    def _delete_task(self, tid: str) -> None:
        ret = QMessageBox.question(self, "删除任务", f"确认删除任务 #{tid}？\n（可勾选的清理项在下一对话框设置）")
        if ret != QMessageBox.Yes:
            return
        try:
            self._delete_with_options([tid])
        except ApiError as e:
            QMessageBox.warning(self, "删除失败", e.message)

    def _delete_with_options(self, ids: list) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("删除任务")
        dlg.setFixedWidth(360)
        lay = QVBoxLayout(dlg)
        q1 = QCheckBox("同时清理关联资产")
        q2 = QCheckBox("同时清理关联流量")
        q3 = QCheckBox("同时清理漏洞记录")
        lay.addWidget(QLabel("选择清理范围："))
        lay.addWidget(q1)
        lay.addWidget(q2)
        lay.addWidget(q3)
        btns = QHBoxLayout()
        ok = button("删除", danger=True)
        ok.clicked.connect(dlg.accept)
        cancel = button("取消")
        cancel.clicked.connect(dlg.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        if dlg.exec() != QDialog.Accepted:
            return
        cleanup = {"assets": q1.isChecked(), "traffic": q2.isChecked()}
        for i in ids:
            self.client.request("DELETE", f"/api/tasks/{i}", json=cleanup)
        self.refresh()

    def _batch_control(self, action: str):
        def go():
            ids = self._selected_ids(self.table)
            if not ids:
                return
            self.client.post("/api/tasks/control/batch", json={"tasks": ids, "action": action})
            self.refresh()
        return go

    def _batch_archive(self):
        def go():
            ids = self._selected_ids(self.table)
            if ids:
                self.client.post("/api/tasks/archive/batch", json={"task_ids": ids})
                self.refresh()
        return go

    def _batch_delete(self):
        def go():
            ids = self._selected_ids(self.table)
            if ids:
                self._delete_with_options(ids)
        return go

    def _restore_one(self, aid: str) -> None:
        self.client.post(f"/api/task-archives/{aid}/restore")
        self.refresh()

    def _restore_batch(self):
        ids = self._selected_ids(self._archive_table)
        if ids:
            self.client.post("/api/task-archives/restore/batch", json={"archive_ids": ids})
            self.refresh()

    def _delete_archives(self):
        ids = self._selected_ids(self._archive_table)
        if ids:
            self.client.post("/api/task-archives/delete/batch", json={"archive_ids": ids})
            self.refresh()

    # ---- 新建任务 -----------------------------------------------------
    def _new_task(self) -> None:
        dlg = NewTaskDialog(self.app)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    # ---- 分类管理 -----------------------------------------------------
    def _manage_categories(self) -> None:
        dlg = CategoryDialog(self.app, self)
        dlg.exec()
        self.refresh()


class NewTaskDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.setWindowTitle("新建任务")
        self.setFixedSize(560, 620)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit()
        self.name.setPlaceholderText("可选")
        self.description = QLineEdit()
        self.description.setPlaceholderText("任务描述")
        self.goal = QLineEdit()
        self.goal.setPlaceholderText("测试目标，如 app.example.com")
        self.timeout = QSpinBox()
        self.timeout.setRange(0, 86400 * 7)
        self.timeout.setValue(0)
        self.timeout.setSuffix(" 秒 (0=不限)")
        self.coverage = QCheckBox("启用资产覆盖度跟踪")
        self.coverage.setChecked(True)

        # LLM 链
        self.llm_pick = QComboBox()
        try:
            profs = self.client.get("/api/llm/profiles")
            for p in profs:
                self.llm_pick.addItem(f"{p['name']} ({p['model']})", p["id"])
        except Exception:
            pass
        self.llm_pick.addItem("使用全局激活配置", None)

        form.addRow("名称", self.name)
        form.addRow("描述", self.description)
        form.addRow("目标", self.goal)
        form.addRow("LLM 配置", self.llm_pick)
        form.addRow("超时", self.timeout)
        form.addRow("", self.coverage)
        lay.addLayout(form)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {COL['danger']};")
        lay.addWidget(self.error)

        btns = QHBoxLayout()
        ok = button("创建并开始", primary=True)
        ok.clicked.connect(self._submit)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def _submit(self) -> None:
        desc = self.description.text().strip()
        goal = self.goal.text().strip()
        if not goal and not desc:
            self.error.setText("目标与描述至少填一个")
            return
        body = {
            "name": self.name.text().strip(),
            "description": desc,
            "goal": goal,
            "timeout_seconds": self.timeout.value(),
            "coverage_enabled": self.coverage.isChecked(),
        }
        pid = self.llm_pick.currentData()
        if pid:
            body["llm_profile_ids"] = [pid]
        try:
            self.client.post("/api/tasks", json=body)
            self.accept()
        except ApiError as e:
            self.error.setText(e.message)


class CategoryDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.client = app.client
        self.setWindowTitle("分类管理")
        self.setFixedSize(420, 400)
        lay = QVBoxLayout(self)
        self.list_widget = QTableWidget(0, 3)
        self.list_widget.setHorizontalHeaderLabels(["ID", "名称", "操作"])
        self.list_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.list_widget.verticalHeader().setVisible(False)
        lay.addWidget(self.list_widget)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("新分类名")
        add = button("添加", primary=True)
        add.clicked.connect(self._add)
        row.addWidget(self.input)
        row.addWidget(add)
        lay.addLayout(row)
        self._load()

    def _load(self) -> None:
        cats = self.client.get("/api/task-categories")
        self.list_widget.setRowCount(0)
        for c in cats:
            r = self.list_widget.rowCount()
            self.list_widget.insertRow(r)
            self.list_widget.setItem(r, 0, table_item(c["id"]))
            self.list_widget.setItem(r, 1, table_item(c["name"]))
            b = button("删除", danger=True)
            b.clicked.connect(lambda _, cid=c["id"]: self._delete(cid))
            self.list_widget.setCellWidget(r, 2, b)

    def _add(self) -> None:
        name = self.input.text().strip()
        if name:
            self.client.post("/api/task-categories", json={"name": name})
            self.input.clear()
            self._load()

    def _delete(self, cid: str) -> None:
        if QMessageBox.question(self, "删除分类", "确认删除该分类？任务将回到未分类。") == QMessageBox.Yes:
            self.client.delete(f"/api/task-categories/{cid}")
            self._load()
"""总览 Tab：目标管理 / 约束 / 覆盖度 / Token / 测试范围。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...api_client import ApiError
from ...theme import COL
from ...widgets import (
    Card, button, fmt_time, make_badge, msg_label, status_color, table_item,
)
from ..base import BasePage


class OverviewTab(QWidget):
    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.client = dlg.client
        self.tid = dlg.tid
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(8000)

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)

        # 目标卡
        goal_card = Card("目标")
        self.goal_list = QListWidget()
        goal_card.body.addWidget(self.goal_list)
        goal_row = QHBoxLayout()
        self.goal_input = QLineEdit()
        self.goal_input.setPlaceholderText("新目标内容")
        add_btn = button("添加", primary=True)
        add_btn.clicked.connect(self._add_goal)
        goal_row.addWidget(self.goal_input)
        goal_row.addWidget(add_btn)
        goal_card.body.addLayout(goal_row)
        lay.addWidget(goal_card)

        # 约束卡
        con_card = Card("操作约束")
        self.con_list = QListWidget()
        con_card.body.addWidget(self.con_list)
        con_row = QHBoxLayout()
        self.con_input = QLineEdit()
        self.con_input.setPlaceholderText("约束文本，如 禁止扫描内网 10.0.0.0/8")
        add_con = button("添加", primary=True)
        add_con.clicked.connect(self._add_constraint)
        con_row.addWidget(self.con_input)
        con_row.addWidget(add_con)
        con_card.body.addLayout(con_row)
        lay.addWidget(con_card)

        # 覆盖度 + Token
        self.coverage_card = Card("资产覆盖度")
        lay.addWidget(self.coverage_card)
        self.token_card = Card("Token 用量（按 worker）")
        lay.addWidget(self.token_card)

        # 测试范围
        self.scope_card = Card("测试范围")
        self.scope_list = QListWidget()
        self.scope_card.body.addWidget(self.scope_list)
        lay.addWidget(self.scope_card)

    def on_shown(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            self._refresh_goals()
            self._refresh_constraints()
            self._refresh_coverage()
            self._refresh_tokens()
            self._refresh_scope()
        except ApiError:
            pass

    # ---- 目标 ---------------------------------------------------------
    def _refresh_goals(self) -> None:
        goals = self.client.get(f"/api/tasks/{self.tid}/goals")
        self.goal_list.clear()
        for g in goals:
            item = QListWidgetItem(f"{g['text']}  [{g['state']}]")
            item.setData(Qt.UserRole, g["id"])
            item.setForeground(COL["text"] if g["state"] == "open" else COL["ok"])
            self.goal_list.addItem(item)

    def _add_goal(self) -> None:
        text = self.goal_input.text().strip()
        if text:
            self.client.post(f"/api/tasks/{self.tid}/goals", json={"text": text})
            self.goal_input.clear()
            self.refresh()

    # ---- 约束 ---------------------------------------------------------
    def _refresh_constraints(self) -> None:
        cons = self.client.get(f"/api/tasks/{self.tid}/constraints")
        self.con_list.clear()
        for c in cons:
            marker = "✅ 允许" if c["kind"] == "allow" else "🚫 禁止"
            self.con_list.addItem(f"{marker}  {c['text']}")

    def _add_constraint(self) -> None:
        text = self.con_input.text().strip()
        if text:
            self.client.post(f"/api/tasks/{self.tid}/constraints",
                             json={"kind": "deny", "text": text})
            self.con_input.clear()
            self.refresh()

    # ---- 覆盖度 -------------------------------------------------------
    def _refresh_coverage(self) -> None:
        cov = self.client.get(f"/api/tasks/{self.tid}/coverage")
        while self.coverage_card.body.count():
            it = self.coverage_card.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        pct = cov.get("percent", 0)
        row = QHBoxLayout()
        row.addWidget(msg_label(f"覆盖度 {pct}%", COL["accent"]))
        row.addWidget(msg_label(f"范围内资产 {cov.get('denominator', 0)}", COL["text_dim"]))
        row.addWidget(msg_label(f"已测试 {cov.get('tested', 0)}", COL["ok"]))
        row.addWidget(msg_label(f"未测试 {cov.get('untested', 0)}", COL["warn"]))
        row.addStretch()
        self.coverage_card.body.addLayout(row)

    # ---- Token --------------------------------------------------------
    def _refresh_tokens(self) -> None:
        toks = self.client.get(f"/api/exploration/tokens?task={self.tid}")
        while self.token_card.body.count():
            it = self.token_card.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["worker", "输入", "输出", "调用次数"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        for t in toks:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(t.get("worker", "")))
            table.setItem(r, 1, table_item(t.get("input_tokens", 0)))
            table.setItem(r, 2, table_item(t.get("output_tokens", 0)))
            table.setItem(r, 3, table_item(t.get("calls", 0)))
        self.token_card.body.addWidget(table)

    # ---- 测试范围 -----------------------------------------------------
    def _refresh_scope(self) -> None:
        scopes = self.client.get(f"/api/tasks/{self.tid}/scope")
        self.scope_list.clear()
        for s in scopes:
            val = s.get("value") or s.get("domain") or s.get("net") or ""
            self.scope_list.addItem(f"[{s['kind']}] {val} ({s['source']})")

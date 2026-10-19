"""仪表盘：统计卡 / Token 用量 / 活动流 / 发现 / 任务表。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QGridLayout,
)
from PySide6.QtCore import Qt

from ..theme import COL
from ..widgets import (
    Card, StatCard, fmt_status, fmt_time, make_badge, msg_label, rel_time,
    severity_color, table_item, status_color,
)
from .base import BasePage


class DashboardPage(BasePage):
    title = "总览"

    poll_intervals = {"refresh_fast": 6000, "refresh_slow": 15000}

    def title_text(self) -> str:
        return "总览"

    def _build(self) -> None:
        super()._build()
        self._stats_row()
        self._token_card()
        self._lower_grid()
        self.refresh_fast()
        self.refresh_slow()

    # ---- 统计卡 -------------------------------------------------------
    def _stats_row(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        cards = [
            ("活跃任务", COL["ok"]), ("确认发现", COL["severity"]["high"]),
            ("资产节点", COL["accent"]), ("流量交互", COL["info"]), ("Token 用量", COL["purple"]),
        ]
        self._stat_cards = {}
        for label, color in cards:
            c = StatCard(label, color)
            self._stat_cards[label] = c
            row.addWidget(c, 1)
        self.add_to_content(_layout_widget(row))

    # ---- Token 卡 -----------------------------------------------------
    def _token_card(self) -> None:
        self.token_card = Card("LLM Token 消耗")
        row = QHBoxLayout()
        self.tok_input_lbl = msg_label("输入 0")
        self.tok_output_lbl = msg_label("输出 0")
        self.tok_cache_lbl = msg_label("缓存 0")
        self.tok_total_lbl = msg_label("总计 0", COL["accent"])
        for lbl in (self.tok_input_lbl, self.tok_output_lbl, self.tok_cache_lbl, self.tok_total_lbl):
            lbl.setStyleSheet(lbl.styleSheet() + "; font-size: 14px;")
            row.addWidget(lbl)
        row.addStretch()
        self.token_card.body.addLayout(row)
        self._bar_widget = None
        self.add_to_content(self.token_card)

    # ---- 下排 ---------------------------------------------------------
    def _lower_grid(self) -> None:
        self.activity_card = Card("活动流")
        self.finding_card = Card("最近发现")
        self.task_card = Card("任务动态")
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.addWidget(self.activity_card, 0, 0)
        grid.addWidget(self.finding_card, 0, 1)
        grid.addWidget(self.task_card, 1, 0, 1, 2)
        self.add_to_content(_layout_widget(grid))

    # ---- 数据刷新 -----------------------------------------------------
    def refresh_fast(self) -> None:
        try:
            stats = self.client.get("/api/stats")
            tokens = self.client.get("/api/tokens/usage", params={"days": 7})
        except Exception:
            return
        try:
            self._stat_cards["活跃任务"].set_value(stats.get("active_tasks", 0))
            fstats = self.client.get("/api/exploration/findings/stats")
            self._stat_cards["确认发现"].set_value(fstats.get("total", 0))
            ac = self.client.get("/api/assets/counts")
            self._stat_cards["资产节点"].set_value(sum(a["count"] for a in ac))
            tr = self.client.get("/api/traffic", params={"size": 1})
            self._stat_cards["流量交互"].set_value(tr.get("total", 0))
            t = tokens
            total = t.get("total_input", 0) + t.get("total_output", 0)
            self._stat_cards["Token 用量"].set_value(total)
        except Exception:
            pass

        self._fill_token_card(tokens)
        self._fill_activity()
        self._fill_findings()
        self._fill_tasks()

    def refresh_slow(self) -> None:
        pass

    # ---- 各区块 -------------------------------------------------------
    def _fill_token_card(self, tokens: dict) -> None:
        self.tok_input_lbl.setText(f"输入 {tokens.get('total_input', 0):,}")
        self.tok_output_lbl.setText(f"输出 {tokens.get('total_output', 0):,}")
        self.tok_cache_lbl.setText(f"缓存 {tokens.get('total_cache_read', 0):,}")
        self.tok_total_lbl.setText(f"总计 {tokens.get('total_input', 0) + tokens.get('total_output', 0):,}")

    def _fill_activity(self) -> None:
        rows = self.client.get("/api/tasks")
        items = rows.get("items", []) if isinstance(rows, dict) else rows
        while self.activity_card.body.count():
            it = self.activity_card.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        n = 0
        for t in items[:8]:
            last = t.get("last_activity_unix")
            lbl = QLabel(f"• 任务 #{t['id']} · {fmt_status(t['status'])} · {rel_time(iso_of(last))}")
            lbl.setStyleSheet("color: #8ea0b0; font-size: 12px;")
            self.activity_card.body.addWidget(lbl)
            n += 1
        if not n:
            self.activity_card.body.addWidget(msg_label("暂无任务"))

    def _fill_findings(self) -> None:
        while self.finding_card.body.count():
            it = self.finding_card.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        try:
            fs = self.client.get("/api/exploration/findings", params={"size": 8})
            items = fs.get("items", [])
        except Exception:
            items = []
        for f in items:
            row = QHBoxLayout()
            color = severity_color(f.get("severity", ""))
            row.addWidget(make_badge(fmt_status(f.get("severity", "")), color))
            name = QLabel(f.get("name") or f.get("vulnclass") or "漏洞")
            name.setStyleSheet("font-size: 12px;")
            row.addWidget(name, 1)
            row.addWidget(msg_label(rel_time(f.get("ts")), COL["text_mute"]))
            self.finding_card.body.addLayout(row)
        if not items:
            self.finding_card.body.addWidget(msg_label("暂无发现"))

    def _fill_tasks(self) -> None:
        while self.task_card.body.count():
            it = self.task_card.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        try:
            rows = self.client.get("/api/tasks")
            items = rows.get("items", []) if isinstance(rows, dict) else rows
        except Exception:
            items = []
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["ID", "任务", "状态", "目标进度", "在途", "最近活动"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for t in items[:6]:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, table_item(t["id"]))
            table.setItem(r, 1, table_item(t.get("name") or t.get("description") or ""))
            table.setItem(r, 2, table_item(fmt_status(t["status"]), status_color(t["status"])))
            table.setItem(r, 3, table_item(f"{t.get('goals_met',0)}/{t.get('goals_total',0)}"))
            table.setItem(r, 4, table_item(t.get("in_flight", 0)))
            table.setItem(r, 5, table_item(rel_time(iso_of(t.get("last_activity_unix")))))
        table.setColumnWidth(1, 320)
        table.horizontalHeader().setStretchLastSection(True)
        self.task_card.body.addWidget(table)


def _layout_widget(layout) -> "QWidget":
    from PySide6.QtWidgets import QWidget
    holder = QWidget()
    holder.setLayout(layout)
    return holder


def iso_of(unix_sec) -> str:
    """unix -> RFC3339 近似，用于 rel_time。"""
    if not unix_sec:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(unix_sec, timezone.utc).isoformat(timespec="seconds")

"""可交互力导向图组件（QGraphicsView）。

用于任务「探索链路」和「资产覆盖图」。支持缩放/拖拽/点击选中，
点击节点回调 detail_cb(node)。
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPainter
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsScene,
    QGraphicsTextItem, QGraphicsView, QWidget,
)

from .theme import COL

NODE_COLORS = {
    "begin": "#8ea0b0", "goal": "#a78bfa", "intent": "#22c3ee",
    "fact": "#34d399", "finding": "#f87171", "hint": "#fbbf24", "digest": "#60a5fa",
    "root_domain": "#a78bfa", "subdomain": "#22c3ee", "ip": "#60a5fa",
    "service": "#34d399", "app": "#fbbf24", "endpoint": "#f87171",
}

EDGE_REL_COLORS = {
    "spawns": "#4b6a80", "derived_from": "#7c5f9e", "yields": "#3c7d5e",
    "proves": "#8a4f4f", "covers": "#3c5f7d",
}

NODE_SIZE = {"begin": 12, "goal": 18, "intent": 14, "fact": 11, "finding": 16, "hint": 11, "digest": 15}


class NodeItem(QGraphicsEllipseItem):
    def __init__(self, x: float, y: float, r: float, color: str, data: dict):
        super().__init__(-r, -r, r * 2, r * 2)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor(color).lighter(120)))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.data = data
        self.vx = 0.0
        self.vy = 0.0
        self.setZValue(10)

    def set_label(self, text: str, color: str) -> None:
        lbl = QGraphicsTextItem(text)
        lbl.setDefaultTextColor(QColor(color))
        f = QFont()
        f.setPointSize(8)
        lbl.setFont(f)
        lbl.setPos(self.rect().width() / 2 + 4, -6)
        lbl.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.scene().addItem(lbl)


class GraphWidget(QGraphicsView):
    """force-directed 图。nodes: [{id,label,type,tested?}] edges: [{src,dst,rel}]"""

    def __init__(self, detail_cb: Optional[Callable[[dict], None]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.detail_cb = detail_cb
        self._nodes: list[NodeItem] = []
        self._edges: list[tuple] = []  # (node_a, node_b, line)
        self._layout_timer = QTimer(self)
        self._layout_timer.setInterval(30)
        self._layout_timer.timeout.connect(self._layout_step)
        self._max_steps = 0
        self.setBackgroundBrush(QColor(COL["bg_elevated"]))

    # ------------------------------------------------------------------
    def set_graph(self, nodes: list[dict], edges: list[dict]) -> None:
        self.scene.clear()
        self._nodes = []
        self._edges = []
        node_map = {}
        w = max(400, self.viewport().width())
        h = max(300, self.viewport().height())
        for n in nodes:
            nid = str(n["id"])
            color = NODE_COLORS.get(n.get("type"), "#8ea0b0")
            if n.get("tested"):
                color = COL["ok"]
            r = NODE_SIZE.get(n.get("type"), 12)
            node = NodeItem(random.uniform(0, w), random.uniform(0, h), r, color, n)
            self.scene.addItem(node)
            node.set_label(n.get("label") or nid, color)
            self._nodes.append(node)
            node_map[nid] = node
        for e in edges:
            a = node_map.get(str(e.get("src")))
            b = node_map.get(str(e.get("dst")))
            if not a or not b:
                continue
            line = QGraphicsLineItem()
            line.setPen(QPen(QColor(EDGE_REL_COLORS.get(e.get("rel"), "#3a4a58")), 1))
            line.setZValue(1)
            self.scene.addItem(line)
            self._edges.append((a, b, line))
        self._max_steps = min(600, max(200, len(nodes) * 30))
        self._layout_timer.start()

    def _layout_step(self) -> None:
        if not self._nodes:
            self._layout_timer.stop()
            return
        self._step_force()
        self._max_steps -= 1
        if self._max_steps <= 0:
            self._layout_timer.stop()
            self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

    def _step_force(self) -> None:
        w = max(400, self.viewport().width())
        h = max(300, self.viewport().height())
        for i in range(len(self._nodes)):
            a = self._nodes[i]
            for j in range(i + 1, len(self._nodes)):
                b = self._nodes[j]
                dx = a.pos().x() - b.pos().x()
                dy = a.pos().y() - b.pos().y()
                d2 = dx * dx + dy * dy
                if d2 < 1:
                    dx, dy, d2 = random.uniform(-1, 1), random.uniform(-1, 1), 2
                f = 5000.0 / d2
                d = math.sqrt(d2)
                ax, ay = f * dx / d, f * dy / d
                a.vx += ax; a.vy += ay
                b.vx -= ax; b.vy -= ay
        for a, b, _line in self._edges:
            dx = b.pos().x() - a.pos().x()
            dy = b.pos().y() - a.pos().y()
            d = math.sqrt(dx * dx + dy * dy) or 1
            f = 0.003 * d
            a.vx += f * dx; a.vy += f * dy
            b.vx -= f * dx; b.vy -= f * dy
        cx, cy = w / 2, h / 2
        for a in self._nodes:
            a.vx += (cx - a.pos().x()) * 0.0006
            a.vy += (cy - a.pos().y()) * 0.0006
        for a in self._nodes:
            a.vx *= 0.85
            a.vy *= 0.85
            sp = math.hypot(a.vx, a.vy)
            if sp > 3:
                a.vx *= 3 / sp
                a.vy *= 3 / sp
            a.setPos(
                max(20, min(w - 20, a.pos().x() + a.vx)),
                max(20, min(h - 20, a.pos().y() + a.vy)),
            )
        for a, b, line in self._edges:
            line.setLine(a.pos().x(), a.pos().y(), b.pos().x(), b.pos().y())

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        node = self._find_node(item)
        if node is not None and self.detail_cb:
            self.detail_cb(node.data)
            return
        super().mousePressEvent(event)

    def _find_node(self, item) -> Optional[NodeItem]:
        n = item
        while n is not None:
            if isinstance(n, NodeItem):
                return n
            n = n.parentItem()
        return None

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._nodes:
            self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

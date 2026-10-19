"""工作空间页：任务工作目录文件浏览器。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QMessageBox, QPlainTextEdit, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, fmt_time, msg_label
from .base import BasePage

PATH_ROLE = Qt.UserRole
LOADED_ROLE = Qt.UserRole + 1
DIR_ROLE = Qt.UserRole + 2


class WorkspacePage(BasePage):
    def title_text(self) -> str:
        return "工作空间"

    def _build(self) -> None:
        super()._build()
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        # ---- 左侧：目录树 ----
        left = QWidget()
        lay = QVBoxLayout(left)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["路径", "大小", "修改时间"])
        self.tree.setColumnWidth(0, 240)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        lay.addWidget(self.tree)
        ops = QHBoxLayout()
        mkdir_btn = button("新建目录")
        mkdir_btn.clicked.connect(self._mkdir)
        del_btn = button("删除", danger=True)
        del_btn.clicked.connect(self._delete_selected)
        ops.addWidget(mkdir_btn)
        ops.addWidget(del_btn)
        ops.addStretch()
        lay.addLayout(ops)
        splitter.addWidget(left)

        # ---- 右侧：文件内容 ----
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        self.path_lbl = msg_label("未选择文件")
        self.path_lbl.setStyleSheet(f"color: {COL['text_dim']};")
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("双击左侧文件以查看内容")
        save_btn = button("保存", primary=True)
        save_btn.clicked.connect(self._save)
        self._save_btn = save_btn
        save_row = QHBoxLayout()
        save_row.addWidget(self.path_lbl, 1)
        save_row.addWidget(save_btn)
        rlay.addWidget(self.editor, 1)
        rlay.addLayout(save_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.add_to_content(splitter)

        self.current_path: str = ""
        self._load_root()

    # ---- 目录树 -------------------------------------------------------
    def _load_root(self) -> None:
        root = QTreeWidgetItem(["工作空间", "", ""])
        root.setData(0, PATH_ROLE, "")
        root.setData(0, LOADED_ROLE, False)
        root.setData(0, DIR_ROLE, True)
        root.addChild(QTreeWidgetItem(["…"]))
        self.tree.addTopLevelItem(root)
        self._load_children(root)

    def _load_children(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, PATH_ROLE)
        try:
            data = self.client.get("/api/workspace/list", params={"path": path})
        except ApiError:
            return
        entries = data.get("entries", [])
        item.takeChildren()
        for e in entries:
            name = e.get("name", "")
            is_dir = bool(e.get("dir"))
            size = "" if is_dir else str(e.get("size", ""))
            mtime = fmt_time(e.get("mtime"))
            child = QTreeWidgetItem([name, size, mtime])
            child.setData(0, PATH_ROLE, e.get("path", ""))
            child.setData(0, LOADED_ROLE, False)
            child.setData(0, DIR_ROLE, is_dir)
            if is_dir:
                child.addChild(QTreeWidgetItem(["…"]))
            item.addChild(child)
        item.setData(0, LOADED_ROLE, True)
        item.setExpanded(True)

    def _on_item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        # 点击目录节点时加载子目录
        if item.data(0, DIR_ROLE) and not item.data(0, LOADED_ROLE):
            self._load_children(item)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        if item.data(0, DIR_ROLE):
            self._load_children(item)
            return
        self._read_file(item)

    def _read_file(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, PATH_ROLE)
        self.current_path = path
        self.path_lbl.setText(path)
        try:
            data = self.client.get("/api/workspace/read", params={"path": path})
        except ApiError as e:
            self.editor.setPlainText(f"读取失败: {e.message}")
            self.editor.setReadOnly(True)
            self._save_btn.setEnabled(False)
            return
        if data.get("binary"):
            self.editor.setPlainText("[二进制文件，无法以文本方式编辑]")
            self.editor.setReadOnly(True)
            self._save_btn.setEnabled(False)
            return
        if data.get("too_large"):
            self.editor.setPlainText("[文件过大，仅展示预览]\n\n" + (data.get("content") or ""))
            self.editor.setReadOnly(True)
            self._save_btn.setEnabled(False)
            return
        self.editor.setPlainText(data.get("content") or "")
        self.editor.setReadOnly(False)
        self._save_btn.setEnabled(True)

    # ---- 操作 ---------------------------------------------------------
    def _save(self) -> None:
        if not self.current_path or not self._save_btn.isEnabled():
            return
        try:
            self.client.post("/api/workspace/write",
                             json={"path": self.current_path, "content": self.editor.toPlainText()})
        except ApiError as e:
            self.toast(f"保存失败: {e.message}", COL["danger"])
            return
        self.toast("已保存")

    def _mkdir(self) -> None:
        cur = self.tree.currentItem()
        parent = None
        if cur:
            # 选中目录则作为父级，否则取其父目录
            if cur.data(0, DIR_ROLE):
                parent = cur
            elif cur.parent():
                parent = cur.parent()
        base = parent.data(0, PATH_ROLE) if parent else ""
        name, ok = QInputDialog.getText(self, "新建目录", "目录名:")
        if not ok or not name.strip():
            return
        path = f"{base.rstrip('/')}/{name.strip()}" if base else name.strip()
        try:
            self.client.post("/api/workspace/mkdir", json={"path": path})
        except ApiError as e:
            self.toast(f"创建失败: {e.message}", COL["danger"])
            return
        self.toast("目录已创建")
        # 重新加载父目录
        if parent:
            self._load_children(parent)
        else:
            self.tree.clear()
            self._load_root()

    def _delete_selected(self) -> None:
        item = self.tree.currentItem()
        if not item or not item.data(0, PATH_ROLE):
            return
        path = item.data(0, PATH_ROLE)
        if QMessageBox.question(self, "删除", f"确认删除 {path}？") != QMessageBox.Yes:
            return
        try:
            self.client.delete("/api/workspace/delete", params={"path": path})
        except ApiError as e:
            self.toast(f"删除失败: {e.message}", COL["danger"])
            return
        self.toast("已删除")
        parent = item.parent()
        if parent:
            self._load_children(parent)

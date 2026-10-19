"""技能页：列表 / 上传 / 文件树浏览 / 编辑器。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..api_client import ApiError
from ..theme import COL
from ..widgets import button, table_item, msg_label
from .base import BasePage


class SystemSkillsPage(BasePage):
    def title_text(self) -> str:
        return "技能"

    def _build(self) -> None:
        super()._build()
        new_btn = button("＋ 新建技能", primary=True)
        new_btn.clicked.connect(self._new)
        upload_btn = button("上传 ZIP")
        upload_btn.clicked.connect(self._upload)
        self.missing_btn = button("缺失技能")
        self.missing_btn.clicked.connect(self._missing)
        self.add_toolbar(new_btn)
        self.add_toolbar(upload_btn)
        self.add_toolbar(self.missing_btn)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        self.skill_list = QTreeWidget()
        self.skill_list.setHeaderLabel("技能")
        self.skill_list.itemClicked.connect(self._select)
        splitter.addWidget(self.skill_list)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("选择技能文件查看/编辑内容…")
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(1, 1)
        self.add_to_content(splitter)

        save_row = QHBoxLayout()
        save = button("保存文件", primary=True)
        save.clicked.connect(self._save)
        save_row.addStretch()
        save_row.addWidget(save)
        self.add_to_content(_wrap(save_row))

        self._current: dict = {}
        self.refresh()

    def refresh(self) -> None:
        try:
            skills = self.client.get("/api/skills")
        except ApiError:
            return
        self.skill_list.blockSignals(True)
        self.skill_list.clear()
        for s in skills:
            top = QTreeWidgetItem([f"{s['name']}  ({s.get('usage_count', 0)})"])
            top.setData(0, Qt.UserRole, {"type": "skill", "name": s["name"]})
            self.skill_list.addTopLevelItem(top)
            for f in s.get("files", []):
                child = QTreeWidgetItem([f["name"]])
                child.setData(0, Qt.UserRole, {"type": "file", "name": s["name"], "rel": f["path"]})
                top.addChild(child)
        self.skill_list.blockSignals(False)

    def _select(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data.get("type") == "skill":
            self.editor.setPlainText(f"技能 {data['name']}：双击文件编辑，或在左侧展开查看结构。")
            return
        try:
            r = self.client.get(f"/api/skills/{data['name']}/files/{data['rel']}")
            self.editor.setPlainText(r.get("content", ""))
            self._current = data
        except ApiError:
            self.editor.setPlainText("读取失败（可能为二进制或目录）")

    def _save(self) -> None:
        if not self._current:
            return
        try:
            self.client.put(f"/api/skills/{self._current['name']}/files/{self._current['rel']}",
                            json={"content": self.editor.toPlainText()})
            self.toast("已保存")
        except ApiError as e:
            QMessageBox.warning(self, "保存失败", e.message)

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "新建技能", "技能名称（目录名）")
        if ok and name.strip():
            try:
                self.client.post("/api/skills", json={"name": name.strip(),
                                                      "description": "新建技能"})
            except ApiError as e:
                QMessageBox.warning(self, "创建失败", e.message)
            self.refresh()

    def _upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择技能 ZIP", "", "ZIP(*.zip)")
        if path:
            try:
                self.client.upload("/api/skills/upload", path)
                self.toast("技能已安装")
            except ApiError as e:
                QMessageBox.warning(self, "上传失败", e.message)
            self.refresh()

    def _missing(self) -> None:
        try:
            missing = self.client.get("/api/skills/missing")
        except ApiError:
            missing = []
        if not missing:
            QMessageBox.information(self, "缺失技能", "无缺失技能记录")
            return
        QMessageBox.information(self, "缺失技能", "\n".join(missing))


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w
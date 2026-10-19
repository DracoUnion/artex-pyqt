"""Theme：深色 QSS 主题，对齐 ARTEX 的深色科技风。"""
from __future__ import annotations

# 色板
COL = {
    "bg": "#0f1418",
    "bg_elevated": "#161d24",
    "bg_card": "#1b242d",
    "bg_hover": "#22303d",
    "border": "#2a3a48",
    "border_light": "#33485a",
    "text": "#e2e8f0",
    "text_dim": "#8ea0b0",
    "text_mute": "#5b6b7a",
    "accent": "#22c3ee",
    "accent_dim": "#0f8bb0",
    "ok": "#34d399",
    "warn": "#fbbf24",
    "danger": "#f87171",
    "info": "#60a5fa",
    "purple": "#a78bfa",
    "severity": {
        "critical": "#ef4444",
        "high": "#f97316",
        "medium": "#fbbf24",
        "low": "#60a5fa",
    },
    "status": {
        "running": "#34d399",
        "created": "#60a5fa",
        "paused": "#fbbf24",
        "done": "#34d399",
        "failed": "#f87171",
        "timeout": "#f87171",
        "pending": "#fbbf24",
        "open": "#60a5fa",
        "confirmed": "#fbbf24",
        "resolved": "#34d399",
        "fixed": "#34d399",
        "false_positive": "#8ea0b0",
        "ignored": "#8ea0b0",
        "duplicate": "#8ea0b0",
        "risk_accepted": "#a78bfa",
        "in_progress": "#60a5fa",
        "active": "#34d399",
        "met": "#34d399",
        "achieved": "#34d399",
        "queued": "#8ea0b0",
    },
}

QCARD = "background: #1b242d; border: 1px solid #2a3a48; border-radius: 8px;"


def build_qss() -> str:
    c = COL
    return f"""
* {{ font-family: "Microsoft YaHei"; font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; background: transparent; }}
QToolTip {{ background: {c['bg_card']}; color: {c['text']}; border: 1px solid {c['border_light']}; }}

/* ---- 侧边栏 ---- */
QFrame#Sidebar {{ background: {c['bg_elevated']}; border-right: 1px solid {c['border']}; }}
QLabel#SidebarTitle {{ font-size: 15px; font-weight: 700; color: {c['accent']}; padding: 4px 12px; }}
QLabel#SidebarGroup {{ color: {c['text_mute']}; font-size: 11px; padding: 10px 14px 4px 14px; }}
QPushButton#SidebarItem {{
    text-align: left; padding: 8px 14px; border: none; background: transparent;
    color: {c['text_dim']}; border-radius: 6px; margin: 1px 8px; font-size: 13px;
}}
QPushButton#SidebarItem:hover {{ background: {c['bg_hover']}; color: {c['text']}; }}
QPushButton#SidebarItem:checked {{ background: {c['accent_dim']}33; color: {c['accent']}; border-left: 3px solid {c['accent']}; }}

/* ---- 顶部栏 ---- */
QFrame#Topbar {{ background: {c['bg_elevated']}; border-bottom: 1px solid {c['border']}; }}
QLabel#TopbarTitle {{ font-size: 16px; font-weight: 600; }}

/* ---- 卡片 / 面板 ---- */
QFrame#Card {{ {QCARD} }}
QFrame#CardHeader {{ background: transparent; border: none; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 600; color: {c['text']}; }}
QLabel#Badge {{ padding: 2px 8px; border-radius: 9px; font-size: 11px; color: white; }}

/* ---- 按钮 ---- */
QPushButton {{
    background: {c['bg_elevated']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ background: {c['bg_hover']}; border-color: {c['border_light']}; }}
QPushButton:pressed {{ background: {c['bg_card']}; }}
QPushButton:disabled {{ color: {c['text_mute']}; }}
QPushButton#Primary {{
    background: {c['accent_dim']}; color: white; border: 1px solid {c['accent_dim']}; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {c['accent']}; }}
QPushButton#Danger {{ background: transparent; color: {c['danger']}; border: 1px solid {c['danger']}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {c['text_dim']}; }}
QPushButton#Ghost:hover {{ background: {c['bg_hover']}; color: {c['text']}; }}
QPushButton#Link {{ background: transparent; border: none; color: {c['accent']}; padding: 2px 6px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}

/* ---- 输入 ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{
    background: {c['bg_elevated']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 6px; padding: 5px 8px; selection-background-color: {c['accent_dim']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: {c['accent_dim']};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {c['text_dim']};
}}
QComboBox QAbstractItemView {{
    background: {c['bg_card']}; border: 1px solid {c['border']}; selection-background-color: {c['accent_dim']};
}}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['border_light']}; border-radius: 4px; background: {c['bg_elevated']}; }}
QCheckBox::indicator:checked {{ background: {c['accent_dim']}; border-color: {c['accent']}; }}

/* ---- 表格 ---- */
QTableView, QTreeView, QListWidget, QTreeWidget {{
    background: {c['bg_elevated']}; border: 1px solid {c['border']}; border-radius: 8px;
    alternate-background-color: {c['bg_card']}; gridline-color: {c['border']};
    selection-background-color: {c['accent_dim']}55; selection-color: {c['text']};
}}
QHeaderView::section {{
    background: {c['bg_card']}; color: {c['text_dim']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 7px 8px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {c['bg_card']}; border: none; }}

/* ---- Tab ---- */
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; background: {c['bg']}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {c['text_dim']}; padding: 8px 16px; margin-right: 2px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom-color: {c['accent']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['text']}; }}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['border_light']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---- 弹窗 / 分割 ---- */
QDialog QPushButton {{ min-width: 72px; }}
QSplitter::handle {{ background: {c['border']}; width: 1px; }}
QMenu {{ background: {c['bg_card']}; border: 1px solid {c['border']}; }}
QMenu::item {{ padding: 6px 24px 6px 12px; }}
QMenu::item:selected {{ background: {c['accent_dim']}55; }}
QProgressBar {{
    background: {c['bg_elevated']}; border: none; border-radius: 5px; height: 10px; text-align: center;
}}
QProgressBar::chunk {{ background: {c['accent_dim']}; border-radius: 5px; }}
QStatusBar {{ background: {c['bg_elevated']}; color: {c['text_dim']}; }}
QScrollArea {{ border: none; }}
"""
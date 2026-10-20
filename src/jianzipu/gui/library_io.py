"""GUI 侧库文件助手：文件对话框、目录树填充与构件补齐。

两个窗口（主窗口、部件浏览器）共用这里的实现，避免同一段文件/树操作各写一遍。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QTreeWidgetItem, QWidget

from jianzipu.core.library import JianziEntry, JianziLibrary, fill_missing_components
from jianzipu.core.scripts import equivalence_key

DB_FILTER = "SQLite 数据库 (*.sqlite *.jdb);;所有文件 (*)"
TEXT_ROLE = Qt.ItemDataRole.UserRole       # 树节点上挂的条目编码（InputText）
LIB_ROLE = Qt.ItemDataRole.UserRole + 1    # 树节点上挂的所属库路径


def entry_label(entry: JianziEntry) -> str:
    """条目文字：中文键名 + 值，明确每个字段的含义。"""
    return f"编码：{entry.input_text}　标题：{entry.title or '（无标题）'}"


def build_library_root(
    label: str,
    path: Path,
    entries: Iterable[JianziEntry],
    query_key: str,
    *,
    sort_key: Callable[[JianziEntry], object] | None = None,
) -> QTreeWidgetItem:
    """构造一个库目录节点：目录名 + 按搜索词过滤后的条目子节点。"""
    root = QTreeWidgetItem([label])
    root.setData(0, LIB_ROLE, str(path))
    items = sorted(entries, key=sort_key) if sort_key is not None else entries
    for entry in items:
        if query_key and query_key not in equivalence_key(entry.input_text):
            continue
        child = QTreeWidgetItem([entry_label(entry)])
        child.setData(0, TEXT_ROLE, entry.input_text)
        child.setData(0, LIB_ROLE, str(path))
        root.addChild(child)
    return root


def fill_missing_from_style(
    custom: JianziLibrary | None, styles: list[JianziLibrary]
) -> int:
    """用第一个风格库补齐自定义库缺失的构件轮廓，返回补齐数量。"""
    if custom is None or not styles:
        return 0
    return fill_missing_components(custom.path, styles[0].path)


def choose_open_db(parent: QWidget, title: str) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", DB_FILTER)
    return Path(path) if path else None


def choose_save_db(parent: QWidget, title: str, suggested: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, suggested, DB_FILTER)
    return Path(path) if path else None

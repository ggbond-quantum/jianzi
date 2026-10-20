"""自定义库工作副本会话：临时副本、脏标记、保存/另存为与未保存确认。

主窗口与部件浏览器都通过本会话编辑同一个工作副本：写操作只落在临时文件上，
「保存」才刷回磁盘原路径，从而避免编辑中途污染用户文件。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QMessageBox

from jianzipu.core.library import JianziLibrary, create_custom_db, save_custom_db_as

AskUnsaved = Callable[[], QMessageBox.StandardButton]


class CustomSession:
    """一个自定义库的编辑会话（工作副本 + 保存状态）。

    library 指向临时副本；saved_path 是磁盘上的真实位置；is_dirty 标记副本
    是否有未保存的更改。关闭会话时临时目录一并清理，未保存的更改随之消失。
    """

    def __init__(self, ask_unsaved: AskUnsaved) -> None:
        self._ask_unsaved = ask_unsaved
        self._library: JianziLibrary | None = None
        self._saved_path: Path | None = None
        self._dirty = False
        self._work_dir: tempfile.TemporaryDirectory | None = None

    # ---- 状态 ----

    @property
    def library(self) -> JianziLibrary | None:
        return self._library

    @property
    def saved_path(self) -> Path | None:
        return self._saved_path

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @is_dirty.setter
    def is_dirty(self, value: bool) -> None:
        self._dirty = value

    def display_name(self) -> str:
        """界面上的名字（取磁盘保存路径的文件名，与临时副本路径无关）。"""
        return self._saved_path.stem if self._saved_path else "未命名"

    # ---- 生命周期 ----

    def open(self, path: Path) -> None:
        """把磁盘库复制到临时目录后打开（编辑只作用于副本）。"""
        self.close()
        self._work_dir = tempfile.TemporaryDirectory(prefix="jianzipu-")
        work = Path(self._work_dir.name) / path.name
        shutil.copy2(path, work)
        self._saved_path = Path(path)
        self._library = JianziLibrary.load(work)
        self._dirty = False

    def create_blank(self, path: Path) -> None:
        """新建空白库的工作副本；磁盘文件等第一次保存时才出现。"""
        self.close()
        self._work_dir = tempfile.TemporaryDirectory(prefix="jianzipu-")
        work = Path(self._work_dir.name) / path.name
        create_custom_db(work)
        self._saved_path = Path(path)
        self._library = JianziLibrary.load(work)
        self._dirty = False

    def close(self) -> None:
        """丢弃工作副本：关闭读取连接并清理临时目录。"""
        if self._library is not None:
            self._library.close()
            self._library = None
        self._saved_path = None
        self._dirty = False
        if self._work_dir is not None:
            self._work_dir.cleanup()
            self._work_dir = None

    # ---- 保存 ----

    def save(self) -> str | None:
        """把工作副本刷回磁盘原路径；未打开自定义库时返回 None。"""
        if self._library is None or self._saved_path is None:
            return None
        save_custom_db_as(self._library.path, self._saved_path)
        self._dirty = False
        return f"已保存 {self._saved_path}"

    def save_as(self, dest: Path, source_style: JianziLibrary | None = None) -> str:
        """另存为：已有工作副本则存入新路径并改挂；否则从风格库创建。

        已有副本时原路径保持不动，后续「保存」写入新路径。
        """
        dest = Path(dest)
        if dest.exists():
            dest.unlink()
        if self._library is not None:
            save_custom_db_as(self._library.path, dest)
            self._saved_path = dest
            self._dirty = False
            return f"已另存为 {dest}"
        if source_style is None:
            raise ValueError("没有工作副本时必须提供风格库来源")
        result = create_custom_db(dest, source=source_style.path)
        self.open(dest)
        return (
            f"已从「{source_style.author}」另存为自定义库"
            f"（{result.copied} 个减字，{result.converted} 条转简体）"
        )

    def confirm_continue(self) -> bool:
        """继续之前确认未保存的更改：返回 True 表示可以安全继续（已保存或放弃）。"""
        if self._library is None or not self._dirty:
            return True
        answer = self._ask_unsaved()
        if answer == QMessageBox.StandardButton.Save:
            return self.save() is not None
        return answer == QMessageBox.StandardButton.Discard

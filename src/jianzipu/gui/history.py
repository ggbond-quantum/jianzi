"""浏览历史：类浏览器式的条目栈（返回 / 前进 / 历史列表）。

每个条目是一个「(编码, 来源库路径)」对：path 为 None 表示自定义库版本，
否则是风格库路径。用一个小类型承载这对字段，避免各处手工拆元组、
也免去「None 代表什么」的隐式约定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class HistoryEntry:
    """一条历史记录：显示的编码 + 来源库路径（None = 自定义库）。"""

    text: str
    path: str | None


class History:
    """带游标的历史栈：record 入栈、back/forward 移动游标。"""

    def __init__(self) -> None:
        self._entries: list[HistoryEntry] = []
        self._index = -1

    # ---- 查询 ----

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return self._index + 1 < len(self._entries)

    def current(self) -> HistoryEntry | None:
        if 0 <= self._index < len(self._entries):
            return self._entries[self._index]
        return None

    def text(self) -> str:
        entry = self.current()
        return entry.text if entry is not None else ""

    def path(self) -> str | None:
        entry = self.current()
        return entry.path if entry is not None else None

    def newest_first(self) -> Iterator[tuple[int, str, str | None]]:
        """按最新在前的顺序产出 (下标, 编码, 来源库路径)，供菜单列出。"""
        for index in range(len(self._entries) - 1, -1, -1):
            entry = self._entries[index]
            yield index, entry.text, entry.path

    # ---- 变更 ----

    def record(self, text: str, path: str | None) -> None:
        """入栈一条记录；新导航清空「前进」分支；与当前项重复则不入栈。"""
        del self._entries[self._index + 1:]
        if self._index >= 0 and self._entries[self._index] == HistoryEntry(text, path):
            return
        self._entries.append(HistoryEntry(text, path))
        self._index = len(self._entries) - 1

    def back(self) -> str | None:
        """游标后退一格，返回该条编码；已在最早时返回 None。"""
        if not self.can_back:
            return None
        self._index -= 1
        return self.text()

    def forward(self) -> str | None:
        """游标前进一格，返回该条编码；已到最新时返回 None。"""
        if not self.can_forward:
            return None
        self._index += 1
        return self.text()

    def jump(self, index: int) -> None:
        """直接跳到某条历史（右键菜单点击选取）。"""
        if 0 <= index < len(self._entries):
            self._index = index

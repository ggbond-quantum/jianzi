"""风格库 / 自定义库的统一访问（core 层，无 GUI 依赖）。

数据库路径一律由调用方以参数传入，源码中不写死路径。
风格库以只读方式打开；自定义库的写入接口见 create_custom_db / pick_entry。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from jianzipu.core.scripts import equivalence_key
from jianzipu.core.synthesis import Placement


@dataclass(frozen=True)
class JianziEntry:
    input_text: str
    title: str
    source_author: str
    source_id: int


class JianziLibrary:
    """对一个风格库 / 自定义库的读取访问。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    @classmethod
    def load(cls, path: Path) -> "JianziLibrary":
        return cls(path)

    def close(self) -> None:
        self._conn.close()

    @property
    def author(self) -> str:
        row = self._conn.execute(
            "SELECT SourceAuthor FROM jianzi WHERE SourceAuthor IS NOT NULL LIMIT 1"
        ).fetchone()
        return row[0] if row else ""

    def entries(self) -> list[JianziEntry]:
        return [
            JianziEntry(input_text, title or "", author or "", source_id)
            for input_text, title, author, source_id in self._conn.execute(
                "SELECT InputText, Title, SourceAuthor, SourceID"
                " FROM jianzi ORDER BY SourceID, InputText"
            )
        ]

    def placements(self, text: str) -> list[Placement]:
        return [
            Placement(stroke_id, dx, dy, dw, dh)
            for stroke_id, dx, dy, dw, dh in self._conn.execute(
                "SELECT StrokeID, DistX, DistY, DistWidth, DistHeight"
                " FROM node WHERE InputText = ? ORDER BY Seq",
                (text,),
            )
        ]

    def components(self) -> dict[int, str]:
        return dict(self._conn.execute("SELECT StrokeID, PathData FROM component"))

    def find(self, query: str) -> list[JianziEntry]:
        """等价匹配检索：按等价键比较，任意写法都能命中库中已存的另一种写法。"""
        key = equivalence_key(query)
        return [e for e in self.entries() if equivalence_key(e.input_text) == key]
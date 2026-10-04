"""风格库 / 自定义库的统一访问（core 层，无 GUI 依赖）。

数据库路径一律由调用方以参数传入，源码中不写死路径。
风格库以只读方式打开；自定义库的写入接口见 create_custom_db / pick_entry。
"""

from __future__ import annotations

import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path

from jianzipu.core.schema import SCHEMA
from jianzipu.core.scripts import equivalence_key, to_simplified
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


@dataclass(frozen=True)
class CreateResult:
    copied: int      # 成功复制的减字数
    converted: int   # 编码发生简体变化的条数
    conflicts: int   # 转换后与已有条目撞键而跳过的条数（防御；实测数据为 0）


def create_custom_db(dest: Path, source: Path | None = None) -> CreateResult:
    """创建自定义库：source=None 新建空库；否则整体复制并批量转简体（ADR 0006）。

    目标已存在时拒绝创建，避免覆盖用户数据（另存为请换路径）。
    """
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"自定义库已存在：{dest}")
    out = sqlite3.connect(dest)
    try:
        out.executescript(SCHEMA)
        result = (
            _copy_as_simplified(Path(source), out)
            if source is not None
            else CreateResult(copied=0, converted=0, conflicts=0)
        )
        out.commit()
    finally:
        out.close()
    return result


def _copy_as_simplified(source: Path, out: sqlite3.Connection) -> CreateResult:
    """整体复制来源库：部件、减字、node 全部写入，InputText 逐条转简体。"""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        out.executemany(
            "INSERT INTO component (StrokeID, Title, PathData, OrgWidth, OrgHeight)"
            " VALUES (?, ?, ?, ?, ?)",
            src.execute("SELECT StrokeID, Title, PathData, OrgWidth, OrgHeight FROM component"),
        )
        copied = converted = conflicts = 0
        for input_text, title, author, source_id in src.execute(
            "SELECT InputText, Title, SourceAuthor, SourceID FROM jianzi ORDER BY SourceID, InputText"
        ):
            normalized = to_simplified(input_text)
            nodes = list(
                src.execute(
                    "SELECT Seq, StrokeID, DistX, DistY, DistWidth, DistHeight"
                    " FROM node WHERE InputText = ? ORDER BY Seq",
                    (input_text,),
                )
            )
            try:
                out.execute(
                    "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID)"
                    " VALUES (?, ?, ?, ?)",
                    (normalized, title, author, source_id),
                )
            except sqlite3.IntegrityError:
                conflicts += 1
                warnings.warn(f"编码「{input_text}」转为「{normalized}」后与已有条目冲突，已跳过")
                continue
            out.executemany(
                "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(normalized, *node_row) for node_row in nodes],
            )
            copied += 1
            if normalized != input_text:
                converted += 1
        return CreateResult(copied=copied, converted=converted, conflicts=conflicts)
    finally:
        src.close()
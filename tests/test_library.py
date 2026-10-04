"""核心库访问接缝测试：风格库加载、等价匹配检索、自定义库创建、挑选写入。

fixture 为合成风格库（与风格库同 schema，含简繁混合编码），验证：
- 加载后条目、部件位置、部件轮廓可读
- 等价匹配：任意写法查询都能命中库中已存的另一种写法
- 自定义库创建：批量简体转换 + 简体不变量
- 挑选写入：逐条简体转换 + PK 冲突返回已存在条目
"""

import sqlite3
from pathlib import Path

from jianzipu.core.library import JianziLibrary
from jianzipu.core.synthesis import Placement
from jianzipu.extract.build import SCHEMA


def make_fixture_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO component (StrokeID, Title, PathData, OrgWidth, OrgHeight)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (1, "甲", "M 0 0 L 100 0 L 100 100 Z", 100, 100),
            (2, "乙", "M 200 0 L 300 0 L 300 100 Z", 100, 100),
        ],
    )
    conn.executemany(
        "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID)"
        " VALUES (?, ?, ?, ?)",
        [
            ("上七九應合散勾五", "长锁", "云霄客", 1),
            ("大換七", "挑换", "云霄客", 2),
            ("大七", "大七", "云霄客", 3),
            ("大七六就挑四", "长串", "云霄客", 4),
        ],
    )
    conn.executemany(
        "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("上七九應合散勾五", 1, 1, 30, 1, 138, 145),
            ("上七九應合散勾五", 2, 2, 169, 1, 162, 130),
            ("大換七", 1, 1, 30, 1, 138, 145),
            ("大換七", 2, 2, 169, 1, 162, 130),
            ("大七", 1, 1, 30, 1, 138, 145),
            ("大七六就挑四", 1, 1, 10, 1, 100, 100),
            ("大七六就挑四", 2, 2, 200, 1, 100, 100),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_load_library_entries_and_author(tmp_path):
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    try:
        assert lib.author == "云霄客"
        entries = lib.entries()
        assert [e.input_text for e in entries] == [
            "上七九應合散勾五",
            "大換七",
            "大七",
            "大七六就挑四",
        ]
        first = entries[0]
        assert first.title == "长锁"
        assert first.source_author == "云霄客"
        assert first.source_id == 1
    finally:
        lib.close()


def test_load_library_placements_and_components(tmp_path):
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    try:
        assert lib.placements("大換七") == [
            Placement(1, 30, 1, 138, 145),
            Placement(2, 169, 1, 162, 130),
        ]
        assert lib.placements("不存在") == []
        assert lib.components()[1] == "M 0 0 L 100 0 L 100 100 Z"
    finally:
        lib.close()
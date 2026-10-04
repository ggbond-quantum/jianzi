"""核心库访问接缝测试：风格库加载、等价匹配检索、自定义库创建、挑选写入。

fixture 为合成风格库（与风格库同 schema，含简繁混合编码），验证：
- 加载后条目、部件位置、部件轮廓可读
- 等价匹配：任意写法查询都能命中库中已存的另一种写法
- 自定义库创建：批量简体转换 + 简体不变量
- 挑选写入：逐条简体转换 + PK 冲突返回已存在条目
"""

import sqlite3
from pathlib import Path

import pytest
from zhconv import convert as zh_convert

from jianzipu.core.library import (
    ComponentEntry,
    CreateResult,
    JianziLibrary,
    create_component_entry,
    create_custom_db,
    default_component_code,
    pick_entry,
)
from jianzipu.core.synthesis import Placement, place_components
from jianzipu.extract.build import SCHEMA


def is_simplified(text: str) -> bool:
    """库内不变量：每字满足 zh-hans(ch) == ch（无法单字转换者原样保留，视为满足）。"""
    return all(zh_convert(ch, "zh-hans") == ch for ch in text)


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


def test_find_matches_across_scripts(tmp_path):
    """等价匹配：查询与候选按同一等价键比较，任意写法都能命中另一种写法。"""
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    try:
        # 库中存繁体写法，简体查询命中
        assert [e.input_text for e in lib.find("上七九应合散勾五")] == ["上七九應合散勾五"]
        # 库中写法原样查询命中；繁体查询简体写法同样命中
        assert [e.input_text for e in lib.find("大換七")] == ["大換七"]
        assert [e.input_text for e in lib.find("大换七")] == ["大換七"]
    finally:
        lib.close()


def test_find_is_whole_string_not_substring(tmp_path):
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    try:
        assert [e.input_text for e in lib.find("大七")] == ["大七"]
        assert lib.find("大七六") == []
        assert lib.find("不存在") == []
    finally:
        lib.close()


def test_find_matches_variant_forms(tmp_path):
    """异体写法互为等价（实测数据：云霄客存「鎻」，其他库存「鎖/锁」），检索不漏。"""
    db = tmp_path / "variant.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID) VALUES (?, ?, ?, ?)",
        ("撞上鎻", "撞锁", "云霄客", 1),
    )
    conn.execute(
        "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("撞上鎻", 1, 1, 0, 0, 100, 100),
    )
    conn.commit()
    conn.close()

    lib = JianziLibrary.load(db)
    try:
        assert [e.input_text for e in lib.find("撞上锁")] == ["撞上鎻"]
        assert [e.input_text for e in lib.find("撞上鎖")] == ["撞上鎻"]
    finally:
        lib.close()


def test_create_custom_db_from_style_converts_all_to_simplified(tmp_path):
    source = make_fixture_db(tmp_path / "style.sqlite")
    dest = tmp_path / "custom.sqlite"
    result = create_custom_db(dest, source=source)
    assert result.copied == 4
    assert result.converted == 2  # 上七九應合散勾五 → 上七九应合散勾五；大換七 → 大换七
    assert result.conflicts == 0

    lib = JianziLibrary.load(dest)
    try:
        entries = lib.entries()
        assert [e.input_text for e in entries] == [
            "上七九应合散勾五",
            "大换七",
            "大七",
            "大七六就挑四",
        ]
        assert entries[0].title == "长锁"
        assert entries[0].source_author == "云霄客"
        assert entries[0].source_id == 1
        # node 表随主键同步：位置参数在新的简体编码下可读
        assert lib.placements("上七九应合散勾五") == [
            Placement(1, 30, 1, 138, 145),
            Placement(2, 169, 1, 162, 130),
        ]
        # 部件轮廓整体复制，自定义库自包含
        assert lib.components()[1] == "M 0 0 L 100 0 L 100 100 Z"
    finally:
        lib.close()


def test_create_custom_db_invariant_all_simplified(tmp_path):
    """验收不变量：任意来源创建的自定义库中，所有 InputText 均为简体形式。"""
    source = make_fixture_db(tmp_path / "style.sqlite")
    dest = tmp_path / "custom.sqlite"
    create_custom_db(dest, source=source)
    lib = JianziLibrary.load(dest)
    try:
        assert lib.entries()
        assert all(is_simplified(e.input_text) for e in lib.entries())
    finally:
        lib.close()


def test_create_empty_custom_db(tmp_path):
    dest = tmp_path / "custom.sqlite"
    assert create_custom_db(dest) == CreateResult(0, 0, 0)
    lib = JianziLibrary.load(dest)
    try:
        assert lib.entries() == []
        assert lib.components() == {}
    finally:
        lib.close()


def test_create_custom_db_refuses_existing_dest(tmp_path):
    dest = tmp_path / "custom.sqlite"
    dest.write_bytes(b"")
    with pytest.raises(FileExistsError):
        create_custom_db(dest)


def test_pick_entry_inserts_simplified_copy(tmp_path):
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    result = pick_entry(custom, style, "上七九應合散勾五")
    assert result.status == "inserted"
    assert result.normalized_text == "上七九应合散勾五"
    assert result.existing is None

    lib = JianziLibrary.load(custom)
    try:
        entries = lib.entries()
        assert [e.input_text for e in entries] == ["上七九应合散勾五"]
        assert entries[0].source_author == "云霄客"
        assert entries[0].source_id == 1
        assert lib.placements("上七九应合散勾五") == [
            Placement(1, 30, 1, 138, 145),
            Placement(2, 169, 1, 162, 130),
        ]
    finally:
        lib.close()


def test_pick_entry_conflict_returns_existing_entry(tmp_path):
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "上七九應合散勾五")  # 繁体写法，入库为简体

    # 同一来源再挑一次：简体判重命中，返回冲突与已存在条目，不写入
    result = pick_entry(custom, style, "上七九應合散勾五")
    assert result.status == "conflict"
    assert result.normalized_text == "上七九应合散勾五"
    assert result.existing.input_text == "上七九应合散勾五"
    assert result.existing.title == "长锁"
    assert result.existing.source_author == "云霄客"

    lib = JianziLibrary.load(custom)
    try:
        assert len(lib.entries()) == 1
    finally:
        lib.close()


def test_pick_entry_unknown_source_text_raises(tmp_path):
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    with pytest.raises(ValueError):
        pick_entry(custom, style, "来源中不存在的编码")


def test_pick_entry_copies_component_outlines(tmp_path):
    """挑选入库必须把用到的构件轮廓一并写入自定义库（自包含，ADR 0002）。

    否则该条目后续无法合成/编译（构件缺失 → KeyError）。
    """
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)  # 空库：component 表为空

    pick_entry(custom, style, "大換七")

    lib = JianziLibrary.load(custom)
    try:
        assert set(lib.components()) >= {1, 2}
        # 端到端：构件齐备，能合成（修复前这里会 KeyError）
        assert place_components(lib.placements("大换七"), lib.components())
    finally:
        lib.close()


def test_component_list_reads_metadata(tmp_path):
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    try:
        comps = lib.component_list()
        assert [(c.stroke_id, c.title, c.org_width, c.org_height) for c in comps] == [
            (1, "甲", 100, 100),
            (2, "乙", 100, 100),
        ]
        assert comps[0].path_data == "M 0 0 L 100 0 L 100 100 Z"
    finally:
        lib.close()


def test_default_component_code_strips_trailing_digits():
    """默认编码：〇 + 构件名去尾部数字（泛2 → 〇泛、跪指2 → 〇跪指）。"""
    assert default_component_code(ComponentEntry(322, "蠲2", "", 10, 10)) == "〇蠲"
    assert default_component_code(ComponentEntry(305, "勾", "", 10, 10)) == "〇勾"


def test_create_component_entry_fits_and_centers(tmp_path):
    """构件入库：按原始比例适配居中（5% 边界）写入单构件减字条目。"""
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    component = ComponentEntry(1, "甲", "M 0 0 L 100 0 L 100 100 Z", 100, 100)

    result = create_component_entry(custom, component, "〇甲")
    assert result.status == "inserted"
    assert result.normalized_text == "〇甲"

    lib = JianziLibrary.load(custom)
    try:
        entries = lib.entries()
        assert [e.input_text for e in entries] == ["〇甲"]
        assert entries[0].title == "甲"
        # 内容区 350*(1-2*0.05)=315，正方形构件贴合长边 315、居中 (350-315)//2=17
        assert lib.placements("〇甲") == [Placement(1, 17, 17, 315, 315)]
        # 构件轮廓一并入库，自定义库自包含
        assert lib.components()[1] == "M 0 0 L 100 0 L 100 100 Z"
    finally:
        lib.close()


def test_create_component_entry_keeps_aspect_ratio(tmp_path):
    """非正方形构件按原始比例缩放，不拉伸。"""
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    component = ComponentEntry(9, "丁", "M 0 0 L 100 0 L 100 40 Z", 100, 40)

    create_component_entry(custom, component, "〇丁")

    lib = JianziLibrary.load(custom)
    try:
        # 长边 315，短边 40/100*315=126
        assert lib.placements("〇丁") == [Placement(9, 17, 112, 315, 126)]
    finally:
        lib.close()


def test_create_component_entry_uses_default_code(tmp_path):
    """不传编码时用默认值：〇 + 构件名去尾部数字。"""
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    component = ComponentEntry(1, "泛2", "M 0 0 L 100 0 L 100 100 Z", 100, 100)

    result = create_component_entry(custom, component)
    assert result.normalized_text == "〇泛"
    assert result.status == "inserted"


def test_create_component_entry_conflict_returns_existing(tmp_path):
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    component = ComponentEntry(1, "甲", "M 0 0 L 100 0 L 100 100 Z", 100, 100)
    create_component_entry(custom, component, "〇甲")

    result = create_component_entry(custom, component, "〇甲")
    assert result.status == "conflict"
    assert result.existing.input_text == "〇甲"
    assert result.existing.title == "甲"

    lib = JianziLibrary.load(custom)
    try:
        assert len(lib.entries()) == 1
    finally:
        lib.close()
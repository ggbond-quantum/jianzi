"""字体编译接缝测试：build_font() 生成的字体读回验证。

fixture 库为合成数据（非原始库），验证行为：PUA 映射、空白汉字字形、
连字替换（HarfBuzz 塑形）、轮廓去重、度量、OTF 输出、映射 JSON。
"""

import json
import sqlite3
import sys
import warnings
from pathlib import Path

import pytest
import uharfbuzz as hb
from fontTools.ttLib import TTFont

from jianzipu.compile.builder import build_font
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
            (3, "双走线", "M 0 0 L 50 0 L 50 50 Z M 0 0 L 50 0 L 50 50 Z", 50, 50),
        ],
    )
    conn.executemany(
        "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID)"
        " VALUES (?, ?, ?, ?)",
        [
            ("大九挑七", "t", "测试作者", 1),
            ("大七", "t", "测试作者", 2),
            ("大七六就挑四", "t", "测试作者", 3),
            ("泛", "t", "测试作者", 4),
            ("大換七", "t", "测试作者", 5),
            ("大头七", "t", "测试作者", 6),
            ("應", "t", "测试作者", 7),
            ("〇言", "言部件", "测试作者", 8),
        ],
    )
    conn.executemany(
        "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("大九挑七", 1, 1, 30, 1, 138, 145),
            ("大九挑七", 2, 2, 169, 1, 162, 130),
            ("大七", 1, 1, 30, 1, 138, 145),
            ("大七六就挑四", 1, 1, 10, 1, 100, 100),
            ("大七六就挑四", 2, 2, 200, 1, 100, 100),
            ("泛", 1, 3, 20, 20, 100, 100),
            ("大換七", 1, 1, 30, 1, 138, 145),
            ("大头七", 1, 1, 30, 1, 138, 145),
            ("應", 1, 1, 30, 1, 138, 145),
            ("〇言", 1, 1, 17, 17, 315, 315),
        ],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    db = make_fixture_db(tmp_path_factory.mktemp("db") / "fixture.sqlite")
    out = tmp_path_factory.mktemp("out")
    return build_font(db, out, formats=("ttf", "otf"))


def shape(font_path: Path, text: str) -> list[tuple[int, int]]:
    """用 HarfBuzz 塑形，返回 (glyph_id, x_advance) 列表。"""
    face = hb.Face(hb.Blob.from_file_path(str(font_path)))
    font = hb.Font(face)
    font.scale = (1000, 1000)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [(i.codepoint, p.x_advance) for i, p in zip(buf.glyph_infos, buf.glyph_positions)]


def test_jianzi_mapped_to_pua_with_full_advance(built):
    font = TTFont(built.ttf_path)
    cmap = font.getBestCmap()
    assert cmap[0xE000] == "uniE000"
    assert font["hmtx"]["uniE000"][0] == 1000


def test_blank_hanzi_glyphs_carry_invisible_marker(built):
    """空白汉字字形带 1 单位退化轮廓：完全空轮廓会被 Word 当作缺字并替换字体（ADR 0011）。"""
    font = TTFont(built.ttf_path)
    glyph = font["glyf"]["uni5927"]  # 大
    assert glyph.numberOfContours == 1
    assert len(glyph.coordinates) == 3
    assert max(x for x, y in glyph.coordinates) <= 1
    assert font["hmtx"]["uni5927"][0] == 0
    assert font["glyf"]["uni3007"].numberOfContours == 1  # 〇 前缀字形


def test_mapping_json_written(built):
    data = json.loads(built.mapping_path.read_text(encoding="utf-8"))
    assert data["mapping"]["大九挑七"] == "U+E000"
    assert data["author"] == "测试作者"


def test_ligature_replaces_sequence_with_jianzi(built):
    gid = TTFont(built.ttf_path).getGlyphID("uniE000")
    assert shape(built.ttf_path, "大九挑七") == [(gid, 1000)]


def test_longest_ligature_wins_over_prefix(built):
    font = TTFont(built.ttf_path)
    assert shape(built.ttf_path, "大七六就挑四") == [(font.getGlyphID("uniE002"), 1000)]
    assert shape(built.ttf_path, "大七") == [(font.getGlyphID("uniE001"), 1000)]


def test_single_char_jianzi_not_ligated(built):
    gid_blank = TTFont(built.ttf_path).getGlyphID("uni6CDB")  # 泛 的空白字形
    assert shape(built.ttf_path, "泛") == [(gid_blank, 0)]


def test_otf_also_has_ligature(built):
    gid = TTFont(built.otf_path).getGlyphID("uniE000")
    assert shape(built.otf_path, "大九挑七") == [(gid, 1000)]


def test_duplicate_contours_deduped(built):
    font = TTFont(built.ttf_path)
    glyph = font["glyf"]["uniE003"]  # 泛 的减字字形，组件含双重走线轮廓
    assert glyph.numberOfContours == 1
    assert len(glyph.coordinates) == 3


def test_simplified_input_matches_traditional_codes(built):
    font = TTFont(built.ttf_path)
    gid = font.getGlyphID(f"uni{built.mapping['大換七']:04X}")
    assert shape(built.ttf_path, "大換七") == [(gid, 1000)]
    assert shape(built.ttf_path, "大换七") == [(gid, 1000)]


def test_simplified_stored_code_accepts_both_scripts(built):
    """库中存简体编码（自定义库场景）时，简体与繁体输入都必须能触发。"""
    font = TTFont(built.ttf_path)
    gid = font.getGlyphID(f"uni{built.mapping['大头七']:04X}")
    assert shape(built.ttf_path, "大头七") == [(gid, 1000)]
    assert shape(built.ttf_path, "大頭七") == [(gid, 1000)]


def test_script_variants_share_one_blank_glyph(built):
    cmap = TTFont(built.ttf_path).getBestCmap()
    assert cmap[ord("头")] == cmap[ord("頭")]
    assert cmap[ord("换")] == cmap[ord("換")]


def test_build_result_stats(built):
    assert built.jianzi_count == 8
    assert built.ligature_count == 6
    assert built.prefix_count == 2
    assert built.blank_count == 13
    assert built.alias_count == 3


def test_entries_with_prefix_char_trigger_and_keep_glyph_order_unique(built):
    """单构件减字的编码自带转义前缀 〇：正常触发，且不产生重复字形。"""
    font = TTFont(built.ttf_path)
    order = font.getGlyphOrder()
    assert len(order) == len(set(order)), "字形名出现重复"
    gid = font.getGlyphID(f"uni{built.mapping['〇言']:04X}")
    assert shape(built.ttf_path, "〇言") == [(gid, 1000)]
    # 〇 本身仍是空白（单独输入不产生字形）；缀后汉字保持空白（中间态不可见）
    assert shape(built.ttf_path, "〇") == [(font.getGlyphID("uni3007"), 0)]
    assert shape(built.ttf_path, "言") == [(font.getGlyphID("uni8A00"), 0)]


def test_prefix_char_produces_no_spurious_variant_warning(tmp_path):
    """转义前缀 〇 不应被当作脚本变体类而触发假告警。"""
    db = make_fixture_db(tmp_path / "fixture.sqlite")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build_font(db, tmp_path, formats=("ttf",))
    messages = [str(w.message) for w in caught]
    assert not any("U+3007" in m for m in messages), messages


def test_font_named_custom_when_library_has_no_author(tmp_path):
    """自定义库不记录作者：字体名回落到 Custom，不被首个来源作者误导。"""
    db = make_fixture_db(tmp_path / "fixture.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE jianzi SET SourceAuthor = NULL")
    conn.commit()
    conn.close()

    result = build_font(db, tmp_path, formats=("ttf",))

    assert result.author == "Custom"
    assert result.ttf_path.name == "JianziPu-Custom.ttf"


def test_single_char_code_requires_prefix(built):
    """单字编码不自动连字（避免劫持正文）；〇 前缀显式触发。"""
    font = TTFont(built.ttf_path)
    gid = font.getGlyphID(f"uni{built.mapping['泛']:04X}")
    assert shape(built.ttf_path, "〇泛") == [(gid, 1000)]


def test_single_char_without_prefix_stays_blank(built):
    blank = TTFont(built.ttf_path).getGlyphID("uni6CDB")  # 泛 的空白字形
    assert shape(built.ttf_path, "泛") == [(blank, 0)]


def test_prefix_accepts_script_variants(built):
    """〇 后接简体或繁体写法都触发同一减字（cmap 已归并到同一空白字形）。"""
    font = TTFont(built.ttf_path)
    gid = font.getGlyphID(f"uni{built.mapping['應']:04X}")
    assert shape(built.ttf_path, "〇應") == [(gid, 1000)]
    assert shape(built.ttf_path, "〇应") == [(gid, 1000)]


def test_prefix_alone_is_blank(built):
    """单独输入 〇（后面没有单字编码）渲染为空白，不产生字形。"""
    font = TTFont(built.ttf_path)
    assert shape(built.ttf_path, "〇") == [(font.getGlyphID("uni3007"), 0)]


def test_os2_vertical_metrics_nonzero(built):
    """Windows 字体校验要求 OS/2 垂直度量非零；FontBuilder 默认全 0 会导致无法安装。"""
    for path in (built.ttf_path, built.otf_path):
        os2 = TTFont(path)["OS/2"]
        assert os2.usWinAscent > 0 and os2.usWinDescent > 0
        assert os2.sTypoAscender > 0 and os2.sTypoDescender < 0


def test_os2_declares_gb2312_codepage_for_word(built):
    """Word 以 OS/2 码页位判定字体是否支持中文；缺 936 位会被替换成其他中文字体。

    CJK Unicode range 位由 recalcUnicodeRanges 依 cmap 推导（汉字空白字形 + 〇），
    码页位推不出（FontForge 启发式要求 cmap 含 ㄅ U+3105），必须显式声明。
    """
    for path in (built.ttf_path, built.otf_path):
        os2 = TTFont(path)["OS/2"]
        assert os2.ulCodePageRange1 & (1 << 18)  # 936 GB2312
        assert os2.ulUnicodeRange2 & (1 << 16)   # CJK Symbols And Punctuation（〇 U+3007）
        assert os2.ulUnicodeRange2 & (1 << 27)   # CJK Unified Ideographs


def test_name_table_contains_unique_id(built):
    """Windows 字体校验要求 nameID 3（唯一标识）；FontBuilder 不生成它。"""
    for path in (built.ttf_path, built.otf_path):
        records = TTFont(path)["name"].names
        assert any(r.nameID == 3 and r.platformID == 3 for r in records)


@pytest.mark.skipif(sys.platform != "win32", reason="GDI 注册仅 Windows 可用")
def test_windows_gdi_accepts_built_fonts(built):
    """真实安装校验：GDI 必须接受构建产物（进程内私有注册，立即移除，不改动系统）。"""
    import ctypes

    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    gdi.AddFontResourceExW.restype = ctypes.c_int
    gdi.RemoveFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    for path in (built.ttf_path, built.otf_path):
        added = gdi.AddFontResourceExW(str(path), 0x10, None)  # FR_PRIVATE
        assert added > 0, f"{path.name} 被 Windows 拒绝，无法安装"
        gdi.RemoveFontResourceExW(str(path), 0x10, None)
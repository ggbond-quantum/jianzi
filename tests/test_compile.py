"""字体编译接缝测试：build_font() 生成的字体读回验证。

fixture 库为合成数据（非原始库），验证行为：PUA 映射、空白汉字字形、
连字替换（HarfBuzz 塑形）、轮廓去重、度量、OTF 输出、映射 JSON。
"""

import json
import sqlite3
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


def test_blank_hanzi_glyphs_are_empty(built):
    font = TTFont(built.ttf_path)
    glyph = font["glyf"]["uni5927"]  # 大
    assert glyph.numberOfContours == 0
    assert font["hmtx"]["uni5927"][0] == 0


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


def test_build_result_stats(built):
    assert built.jianzi_count == 4
    assert built.ligature_count == 3
    assert built.blank_count == 8
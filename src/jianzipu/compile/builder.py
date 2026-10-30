"""字体编译：风格库 / 自定义库 → TTF / OTF。

数据库路径一律由调用方以参数传入（函数参数或 CLI --db），源码中不写死路径。
"""

from __future__ import annotations

import json
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._n_a_m_e import NameRecord
from zhconv import convert as zh_convert

from jianzipu.core.scripts import PREFIX_CHAR, canonical_char
from jianzipu.core.synthesis import Placement, normalize_contours, place_components

UPEM = 1000
GRID = 350.0
MARGIN = 0.05          # 每侧留白比例
JIANZI_ADVANCE = 1000  # 减字全宽等宽
BLANK_ADVANCE = 0      # 基础汉字空白字形
SPACE_ADVANCE = 500
NOTDEF_ADVANCE = 500
PUA_START = 0xE000     # Unicode 私有使用区起点
CU2QU_MAX_ERR = 0.5
# 垂直度量：Windows 字体校验要求 OS/2 度量非零，FontBuilder 默认全 0 会导致字体无法安装
TYPO_ASCENDER = 880
TYPO_DESCENDER = -120
WIN_ASCENT = 1000
WIN_DESCENT = 200


@dataclass(frozen=True)
class FontBuildResult:
    ttf_path: Path | None
    otf_path: Path | None
    mapping_path: Path
    fea_path: Path
    author: str
    jianzi_count: int
    ligature_count: int
    prefix_count: int
    blank_count: int
    alias_count: int
    mapping: dict[str, int]


def _glyph_name(codepoint: int) -> str:
    return f"uni{codepoint:04X}"


PREFIX_GLYPH_NAME = _glyph_name(ord(PREFIX_CHAR))


def _to_font_units(x: float, y: float) -> tuple[float, float]:
    content = UPEM * (1 - 2 * MARGIN)
    s = content / GRID
    return (MARGIN * UPEM + x * s, MARGIN * UPEM + content - y * s)


BLANK_MARKER_POINTS = ((0, 0), (1, 0), (0, 1))


def _draw_blank_marker(pen) -> None:
    """退化三角占位：Word 把完全空轮廓的 CJK 字形当作缺字并替换字体（ADR 0011）。"""
    pen.moveTo(BLANK_MARKER_POINTS[0])
    pen.lineTo(BLANK_MARKER_POINTS[1])
    pen.lineTo(BLANK_MARKER_POINTS[2])
    pen.closePath()


def _draw_paths(pen, paths) -> None:
    for contour in normalize_contours(paths):
        first = contour[0].start
        pen.moveTo(_to_font_units(first.real, first.imag))
        for seg in contour:
            kind = type(seg).__name__
            if kind == "Line":
                pen.lineTo(_to_font_units(seg.end.real, seg.end.imag))
            elif kind == "CubicBezier":
                pen.curveTo(
                    _to_font_units(seg.control1.real, seg.control1.imag),
                    _to_font_units(seg.control2.real, seg.control2.imag),
                    _to_font_units(seg.end.real, seg.end.imag),
                )
            elif kind == "QuadraticBezier":
                pen.qCurveTo(
                    _to_font_units(seg.control.real, seg.control.imag),
                    _to_font_units(seg.end.real, seg.end.imag),
                )
            else:
                raise TypeError(f"未知段类型: {kind}")
        pen.closePath()


def _build_fea(
    ligature_rules: list[tuple[str, int]], prefix_rules: list[tuple[str, int]]
) -> str:
    """按长度降序生成 liga 规则（长序列优先匹配，解决前缀嵌套）。

    单字编码生成 〇 前缀规则：〇 只作触发符、被规则消费（如「〇勾」→ 减字「勾」）。
    """
    lines = ["feature liga {"]
    for text, codepoint in sorted(ligature_rules, key=lambda r: (-len(r[0]), r[0])):
        components = " ".join(_glyph_name(ord(canonical_char(ch))) for ch in text)
        lines.append(f"    sub {components} by {_glyph_name(codepoint)};")
    for text, codepoint in sorted(prefix_rules, key=lambda r: r[0]):
        char_glyph = _glyph_name(ord(canonical_char(text)))
        lines.append(f"    sub {PREFIX_GLYPH_NAME} {char_glyph} by {_glyph_name(codepoint)};")
    lines.append("} liga;")
    return "\n".join(lines) + "\n"


def _add_unique_id(font, name_table: dict) -> None:
    """Windows 字体校验要求 name 表含 nameID 3（唯一标识）；FontBuilder 不生成它。"""
    record = NameRecord()
    record.nameID = 3
    record.platformID, record.platEncID, record.langID = 3, 1, 0x409
    record.string = f"{name_table['psName']};{name_table['version']}"
    font["name"].names.append(record)


def _setup_os2(fb) -> None:
    """写入健全的 OS/2：垂直度量非零（Windows 拒装）并显式声明 936 码页位（Word 中文识别）。"""
    fb.setupOS2(
        sTypoAscender=TYPO_ASCENDER,
        sTypoDescender=TYPO_DESCENDER,
        sTypoLineGap=0,
        usWinAscent=WIN_ASCENT,
        usWinDescent=WIN_DESCENT,
        fsSelection=1 << 6,  # REGULAR
        fsType=0,            # 允许安装与嵌入
        achVendID="JZPU",
        yStrikeoutSize=50,
        yStrikeoutPosition=250,
        ySubscriptXSize=600,
        ySubscriptYSize=600,
        ySubscriptXOffset=0,
        ySubscriptYOffset=100,
        ySuperscriptXSize=600,
        ySuperscriptYSize=600,
        ySuperscriptXOffset=0,
        ySuperscriptYOffset=400,
        sxHeight=500,
        sCapHeight=700,
    )
    os2 = fb.font["OS/2"]
    os2.recalcCodePageRanges(fb.font)
    # Word 按码页位判定字体是否“支持中文”：recalcCodePageRanges 的 FontForge 启发式
    # 要求 cmap 含 ㄅ（U+3105）才会置 936，本字体推不出，缺位时汉字 run 会被替换成
    # 其他中文字体（只改西文字体槽位不够）。见 ADR 0010。
    os2.ulCodePageRange1 |= 1 << 18  # 936 GB2312


def build_font(db_path: Path, out_dir: Path, formats: tuple[str, ...] = ("ttf", "otf")) -> FontBuildResult:
    db_path, out_dir = Path(db_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    row = conn.execute("SELECT SourceAuthor FROM jianzi LIMIT 1").fetchone()
    author = row[0] if row and row[0] else "Custom"
    texts = [r[0] for r in conn.execute("SELECT InputText FROM jianzi ORDER BY SourceID, InputText")]
    nodes: dict[str, list[Placement]] = {}
    for text, sid, dx, dy, dw, dh in conn.execute(
        "SELECT InputText, StrokeID, DistX, DistY, DistWidth, DistHeight"
        " FROM node ORDER BY InputText, Seq"
    ):
        nodes.setdefault(text, []).append(Placement(sid, dx, dy, dw, dh))
    components = dict(conn.execute("SELECT StrokeID, PathData FROM component"))
    conn.close()

    mapping = {text: PUA_START + i for i, text in enumerate(texts)}
    used_chars = sorted({ch for text in texts for ch in text})

    # 脚本变体归并：同一语义字的简体/繁体/异体写法归入一个等价类，类内所有码位
    # 映射到同一个空白字形。无论库中存的是哪种写法（自定义库可由简体输入法录入），
    # 也无论用户输入哪种写法（含混输），都能触发同一批连字规则。
    script_variants: dict[str, set[int]] = {}
    for c in used_chars:
        if c == PREFIX_CHAR:
            continue  # 转义前缀由专用空白字形承载，不参与脚本变体归并
        canonical = canonical_char(c)
        members = script_variants.setdefault(canonical, set())
        members.add(ord(canonical))
        members.add(ord(c))
        simplified = zh_convert(canonical, "zh-hans")
        if len(simplified) == 1:
            members.add(ord(simplified))

    blank_names = [_glyph_name(ord(canonical)) for canonical in script_variants]
    glyph_order = (
        [".notdef", "space", PREFIX_GLYPH_NAME]
        + [_glyph_name(cp) for cp in mapping.values()]
        + blank_names
    )
    cmap = {0x20: "space", ord(PREFIX_CHAR): PREFIX_GLYPH_NAME}
    cmap.update({cp: _glyph_name(cp) for cp in mapping.values()})
    alias_count = 0
    for canonical, members in script_variants.items():
        name = _glyph_name(ord(canonical))
        for point in sorted(members):
            if point in cmap:
                warnings.warn(f"码位 U+{point:04X} 被多个脚本变体类声明，先到先得")
                continue
            cmap[point] = name
            if name != _glyph_name(point):
                alias_count += 1

    metrics = {".notdef": (NOTDEF_ADVANCE, 0), "space": (SPACE_ADVANCE, 0)}
    metrics[PREFIX_GLYPH_NAME] = (BLANK_ADVANCE, 0)
    metrics.update({_glyph_name(cp): (JIANZI_ADVANCE, 0) for cp in mapping.values()})
    metrics.update({_glyph_name(ord(canonical)): (BLANK_ADVANCE, 0) for canonical in script_variants})

    placed = {text: place_components(nodes.get(text, []), components) for text in texts}
    # 绘制以字形名为索引（placed 的键是 InputText）
    glyph_paths = {_glyph_name(mapping[text]): paths for text, paths in placed.items()}

    # 连字规则：多字编码直接连字；单字编码不自动连字（避免劫持正文——这些字同时是
    # 普通中文，且大量作为长编码的前缀），改由 〇 前缀显式触发。
    # 简繁等价的编码归并后只保留一条（先到先得并告警），其余仍可通过 PUA 码位使用。
    ligature_rules: list[tuple[str, int]] = []
    prefix_rules: list[tuple[str, int]] = []
    canonical_seen: dict[str, str] = {}
    for text in texts:
        canonical_text = "".join(canonical_char(ch) for ch in text)
        if canonical_text in canonical_seen:
            warnings.warn(f"编码「{text}」与「{canonical_seen[canonical_text]}」简繁等价，字体中只保留后者")
            continue
        canonical_seen[canonical_text] = text
        if len(text) < 2:
            prefix_rules.append((text, mapping[text]))
        else:
            ligature_rules.append((text, mapping[text]))

    fea_text = _build_fea(ligature_rules, prefix_rules)
    family = f"JianziPu {author}"
    name_table = {
        "familyName": family,
        "styleName": "Regular",
        "psName": "JianziPu-Regular",
        "fullName": f"{family} Regular",
        "version": "Version 0.1",
    }

    blank_glyphs = set(blank_names) | {PREFIX_GLYPH_NAME}
    ttf_path = otf_path = None
    if "ttf" in formats:
        ttf_path = _build_ttf(
            out_dir, author, glyph_order, cmap, metrics, glyph_paths, blank_glyphs, fea_text, name_table
        )
    if "otf" in formats:
        otf_path = _build_otf(
            out_dir, author, glyph_order, cmap, metrics, glyph_paths, blank_glyphs, fea_text, name_table
        )

    mapping_path = out_dir / f"char_to_unicode-{author}.json"
    mapping_path.write_text(
        json.dumps(
            {
                "author": author,
                "units_per_em": UPEM,
                "advance": JIANZI_ADVANCE,
                "mapping": {t: f"U+{cp:04X}" for t, cp in mapping.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    fea_path = out_dir / f"features-{author}.fea"
    fea_path.write_text(fea_text, encoding="utf-8")

    return FontBuildResult(
        ttf_path=ttf_path,
        otf_path=otf_path,
        mapping_path=mapping_path,
        fea_path=fea_path,
        author=author,
        jianzi_count=len(texts),
        ligature_count=len(ligature_rules),
        prefix_count=len(prefix_rules),
        blank_count=len(used_chars),
        alias_count=alias_count,
        mapping=mapping,
    )


def _build_ttf(
    out_dir, author, glyph_order, cmap, metrics, glyph_paths, blank_glyphs, fea_text, name_table
) -> Path:
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(None)
        if name in glyph_paths:
            _draw_paths(Cu2QuPen(pen, CU2QU_MAX_ERR), glyph_paths[name])
        elif name in blank_glyphs:
            _draw_blank_marker(pen)
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=1000, descent=0)
    fb.setupNameTable(name_table, mac=False)
    _add_unique_id(fb.font, name_table)
    _setup_os2(fb)
    fb.setupPost(keepGlyphNames=True)
    addOpenTypeFeaturesFromString(fb.font, fea_text)
    path = out_dir / f"JianziPu-{author}.ttf"
    fb.save(path)
    return path


def _build_otf(
    out_dir, author, glyph_order, cmap, metrics, glyph_paths, blank_glyphs, fea_text, name_table
) -> Path:
    fb = FontBuilder(UPEM, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    charstrings = {}
    for name in glyph_order:
        pen = T2CharStringPen(metrics[name][0], None)
        if name in glyph_paths:
            _draw_paths(pen, glyph_paths[name])
        elif name in blank_glyphs:
            _draw_blank_marker(pen)
        charstrings[name] = pen.getCharString()
    # CFF 字符串仅支持 latin-1，元数据用 ASCII；中文家族名保留在 name 表（Windows Unicode 记录）
    fb.setupCFF(
        "JianziPu-Regular",
        {"FullName": "JianziPu Regular", "FamilyName": "JianziPu", "Weight": "Regular"},
        charstrings,
        {},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=1000, descent=0)
    fb.setupNameTable(name_table, mac=False)
    _add_unique_id(fb.font, name_table)
    _setup_os2(fb)
    fb.setupPost()
    addOpenTypeFeaturesFromString(fb.font, fea_text)
    path = out_dir / f"JianziPu-{author}.otf"
    fb.save(path)
    return path
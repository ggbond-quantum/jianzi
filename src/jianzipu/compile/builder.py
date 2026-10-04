"""字体编译：风格库 / 自定义库 → TTF / OTF。

数据库路径一律由调用方以参数传入（函数参数或 CLI --db），源码中不写死路径。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen

from jianzipu.core.synthesis import Placement, place_components

UPEM = 1000
GRID = 350.0
MARGIN = 0.05          # 每侧留白比例
JIANZI_ADVANCE = 1000  # 减字全宽等宽
BLANK_ADVANCE = 0      # 基础汉字空白字形
SPACE_ADVANCE = 500
NOTDEF_ADVANCE = 500
PUA_START = 0xE000     # Unicode 私有使用区起点
CU2QU_MAX_ERR = 0.5


@dataclass(frozen=True)
class FontBuildResult:
    ttf_path: Path | None
    otf_path: Path | None
    mapping_path: Path
    fea_path: Path
    author: str
    jianzi_count: int
    ligature_count: int
    blank_count: int
    mapping: dict[str, int]


def _glyph_name(codepoint: int) -> str:
    return f"uni{codepoint:04X}"


def _to_font_units(x: float, y: float) -> tuple[float, float]:
    content = UPEM * (1 - 2 * MARGIN)
    s = content / GRID
    return (MARGIN * UPEM + x * s, MARGIN * UPEM + content - y * s)


def _contours(path) -> list[list]:
    """按段连续性把 Path 拆成轮廓（子路径在同一点接续时视为同一轮廓）。"""
    segs = list(path)
    if not segs:
        return []
    groups, cur = [], [segs[0]]
    for prev, seg in zip(segs, segs[1:]):
        if abs(seg.start - prev.end) > 1e-6:
            groups.append(cur)
            cur = []
        cur.append(seg)
    groups.append(cur)
    return groups


def _seg_key(seg) -> tuple:
    values = []
    for name in ("start", "control1", "control2", "control", "end"):
        z = getattr(seg, name, None)
        if z is not None:
            values.append((round(z.real, 3), round(z.imag, 3)))
    return (type(seg).__name__, tuple(values))


def _dedupe_contours(contours: list[list]) -> list[list]:
    """去掉重复轮廓：源数据中同一闭合轮廓可能被原样走两遍（[A, A]）。"""
    result, seen = [], set()
    for contour in contours:
        if contour and len(contour) % 2 == 0:
            half = len(contour) // 2
            first, second = contour[:half], contour[half:]
            if [_seg_key(s) for s in first] == [_seg_key(s) for s in second]:
                contour = first
        key = tuple(_seg_key(s) for s in contour)
        if key in seen:
            continue
        seen.add(key)
        result.append(contour)
    return result


def _draw_paths(pen, paths) -> None:
    contours = []
    for path in paths:
        contours.extend(_contours(path))
    for contour in _dedupe_contours(contours):
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


def _build_fea(ligature_rules: list[tuple[str, int]]) -> str:
    """按长度降序生成 liga 规则（长序列优先匹配，解决前缀嵌套）。"""
    lines = ["feature liga {"]
    for text, codepoint in sorted(ligature_rules, key=lambda r: (-len(r[0]), r[0])):
        components = " ".join(_glyph_name(ord(ch)) for ch in text)
        lines.append(f"    sub {components} by {_glyph_name(codepoint)};")
    lines.append("} liga;")
    return "\n".join(lines) + "\n"


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
    ligature_rules = [(t, mapping[t]) for t in texts if len(t) >= 2]

    glyph_order = (
        [".notdef", "space"]
        + [_glyph_name(cp) for cp in mapping.values()]
        + [_glyph_name(ord(c)) for c in used_chars]
    )
    cmap = {0x20: "space"}
    cmap.update({cp: _glyph_name(cp) for cp in mapping.values()})
    cmap.update({ord(c): _glyph_name(ord(c)) for c in used_chars})

    metrics = {".notdef": (NOTDEF_ADVANCE, 0), "space": (SPACE_ADVANCE, 0)}
    metrics.update({_glyph_name(cp): (JIANZI_ADVANCE, 0) for cp in mapping.values()})
    metrics.update({_glyph_name(ord(c)): (BLANK_ADVANCE, 0) for c in used_chars})

    placed = {text: place_components(nodes.get(text, []), components) for text in texts}
    # 绘制以字形名为索引（placed 的键是 InputText）
    glyph_paths = {_glyph_name(mapping[text]): paths for text, paths in placed.items()}

    fea_text = _build_fea(ligature_rules)
    family = f"JianziPu {author}"
    name_table = {
        "familyName": family,
        "styleName": "Regular",
        "psName": "JianziPu-Regular",
        "fullName": f"{family} Regular",
        "version": "Version 0.1",
    }

    ttf_path = otf_path = None
    if "ttf" in formats:
        ttf_path = _build_ttf(out_dir, author, glyph_order, cmap, metrics, glyph_paths, fea_text, name_table)
    if "otf" in formats:
        otf_path = _build_otf(out_dir, author, glyph_order, cmap, metrics, glyph_paths, fea_text, name_table)

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
        blank_count=len(used_chars),
        mapping=mapping,
    )


def _build_ttf(out_dir, author, glyph_order, cmap, metrics, glyph_paths, fea_text, name_table) -> Path:
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(None)
        if name in glyph_paths:
            _draw_paths(Cu2QuPen(pen, CU2QU_MAX_ERR), glyph_paths[name])
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=1000, descent=0)
    fb.setupNameTable(name_table, mac=False)
    fb.setupOS2()
    fb.setupPost(keepGlyphNames=True)
    addOpenTypeFeaturesFromString(fb.font, fea_text)
    path = out_dir / f"JianziPu-{author}.ttf"
    fb.save(path)
    return path


def _build_otf(out_dir, author, glyph_order, cmap, metrics, glyph_paths, fea_text, name_table) -> Path:
    fb = FontBuilder(UPEM, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    charstrings = {}
    for name in glyph_order:
        pen = T2CharStringPen(metrics[name][0], None)
        if name in glyph_paths:
            _draw_paths(pen, glyph_paths[name])
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
    fb.setupOS2()
    fb.setupPost()
    addOpenTypeFeaturesFromString(fb.font, fea_text)
    path = out_dir / f"JianziPu-{author}.otf"
    fb.save(path)
    return path
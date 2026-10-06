# pyright: reportAttributeAccessIssue=false
# uharfbuzz 的 API 来自 C 扩展 _harfbuzz.pyd，无类型存根，Pylance 无法静态解析其属性
"""生成字体测试页与样张，并做发布校验（开发工具）。

用法：
    uv run python tools/make_test_page.py --ttf path/to/font.ttf \\
        --mapping path/to/char_to_unicode.json --out-dir path/to/output

功能：
1. 全量校验：映射中每条连字编码经 HarfBuzz 塑形必须恰好等于其 PUA 字形
2. 简繁等价校验：含繁体字的编码，逐字转简体后塑形结果必须与繁体一致
3. 单字前缀校验：〇 + 单字触发减字；单字直打保持空白
4. 可安装性校验：Windows 的 GDI 必须接受该字体（OS/2 度量为零或缺 nameID 3 会被拒装，见 ADR 0008）
5. 生成 specimen.png 样张
6. 生成 测试页.html（字体 base64 内嵌，双击即可在浏览器测试连字，无需安装字体）
"""

import argparse
import base64
import ctypes
import json
import sys
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
from zhconv import convert as zh_convert


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成字体测试页与样张并校验")
    parser.add_argument("--ttf", type=Path, required=True, help="TTF 字体文件")
    parser.add_argument("--mapping", type=Path, required=True, help="char_to_unicode JSON")
    parser.add_argument("--out-dir", type=Path, required=True, help="输出目录")
    return parser.parse_args()


def make_shaper(path: Path):
    face = hb.Face(hb.Blob.from_file_path(str(path)))
    font = hb.Font(face)
    font.scale = (1000, 1000)
    return font


def shape(hbfont, text: str) -> list[tuple[int, int]]:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf)
    return [(i.codepoint, p.x_advance) for i, p in zip(buf.glyph_infos, buf.glyph_positions)]


def convert_text(text: str, target: str) -> str | None:
    """逐字转换整条编码（zh-hans / zh-hant），任一字符无法单字转换则返回 None。"""
    out = []
    for ch in text:
        s = zh_convert(ch, target)
        if len(s) != 1:
            return None
        out.append(s)
    return "".join(out)


def main() -> None:
    args = parse_args()
    data = json.loads(args.mapping.read_text(encoding="utf-8"))
    author: str = data["author"]
    mapping: dict[str, str] = data["mapping"]

    ttfont = TTFont(args.ttf)
    hbfont = make_shaper(args.ttf)
    multi = [t for t in mapping if len(t) >= 2]

    # 1. 全量连字校验
    bad = [
        t
        for t in multi
        if shape(hbfont, t) != [(ttfont.getGlyphID("uni" + mapping[t][2:]), 1000)]
    ]
    print(f"连字校验: {len(multi)} 条，失败 {len(bad)}")
    for t in bad[:5]:
        print(f"   FAIL: {t}")

    # 2. 简繁等价校验（双向：简体版、繁体版输入都应触发同一字形）
    variant_map: dict[str, list[str]] = {}
    for t in multi:
        variants = []
        for target in ("zh-hans", "zh-hant"):
            v = convert_text(t, target)
            if v is not None and v != t:
                variants.append(v)
        if variants:
            variant_map[t] = variants

    equiv_bad = []
    checked = 0
    for t, variants in variant_map.items():
        gid = ttfont.getGlyphID("uni" + mapping[t][2:])
        for v in variants:
            checked += 1
            if shape(hbfont, v) != [(gid, 1000)]:
                equiv_bad.append((t, v))
    print(f"简繁等价校验（双向）: {checked} 个变体输入，失败 {len(equiv_bad)}")
    for t, v in equiv_bad[:5]:
        print(f"   FAIL: {t} -> {v}")

    # 3. 单字编码前缀校验：〇 + 单字（含简繁变体）触发减字；单字直打保持空白不被劫持
    singles = [t for t in mapping if len(t) == 1]
    prefix_bad: list[tuple[str, str]] = []
    for t in singles:
        gid = ttfont.getGlyphID("uni" + mapping[t][2:])
        for v in (t, convert_text(t, "zh-hans"), convert_text(t, "zh-hant")):
            if v is None:
                continue
            if shape(hbfont, "〇" + v) != [(gid, 1000)]:
                prefix_bad.append((t, f"〇{v} 未触发"))
        plain = shape(hbfont, t)
        if len(plain) != 1 or plain[0][1] != 0:
            prefix_bad.append((t, "直打未保持空白"))
    print(f"单字前缀校验: {len(singles)} 个单字编码，失败 {len(prefix_bad)}")
    for t, reason in prefix_bad[:5]:
        print(f"   FAIL: {t} -> {reason}")

    # 4. 可安装性校验：GDI 私有注册（进程内，立即移除；不改动系统）
    if sys.platform == "win32":
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        gdi.AddFontResourceExW.restype = ctypes.c_int
        gdi.RemoveFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        added = gdi.AddFontResourceExW(str(args.ttf), 0x10, None)
        if added:
            gdi.RemoveFontResourceExW(str(args.ttf), 0x10, None)
        print(f"可安装性校验（GDI）: {'接受' if added else '拒绝'}")
    else:
        print("可安装性校验（GDI）: 跳过（非 Windows 平台）")

    # 5. 样张（从库内真实编码中挑选）
    by_len: dict[int, list[str]] = {}
    for t in sorted(multi):
        by_len.setdefault(len(t), []).append(t)
    samples = ["大九挑七"] if "大九挑七" in mapping else []
    for length in (3, 4, 5, 6):
        samples.extend(by_len.get(length, [])[:2])
    if variant_map:
        samples.append(min(variant_map, key=len))
    samples = list(dict.fromkeys(samples))

    cols, cell, size, pad = 4, 240, 180, 24
    rows = (len(samples) + cols - 1) // cols
    img = Image.new("RGB", (cols * cell, rows * cell), "white")
    draw = ImageDraw.Draw(img)
    spec_font = ImageFont.truetype(str(args.ttf), size)
    for i, s in enumerate(samples):
        cp = int(mapping[s][2:], 16)
        x = (i % cols) * cell + pad
        y = (i // cols) * cell + pad
        draw.text((x, y), chr(cp), font=spec_font, fill="black")
        draw.line([(x - 8, y + size + 10), (x + size, y + size + 10)], fill="#cccccc")
    img.save(args.out_dir / "specimen.png")
    print(f"样张: {args.out_dir / 'specimen.png'}")

    # 6. 测试页（字体内嵌 base64）
    b64 = base64.b64encode(args.ttf.read_bytes()).decode("ascii")
    example_single = f"〇{singles[0]}" if singles else ""
    single_note = f"（如 {example_single}）" if example_single else ""
    default_text = " ".join(samples[:4] + ([example_single] if example_single else []))
    hints = "".join(f"<code>{s}</code>" for s in samples)
    if example_single:
        hints += f"<code>{example_single}</code>"
    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>减字谱字体测试页 · {author}</title>
<style>
  @font-face {{
    font-family: "JianziPu";
    src: url(data:font/ttf;base64,{b64}) format("truetype");
  }}
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
  h1 {{ font-size: 20px; }}
  .note {{ color: #666; font-size: 13px; line-height: 1.7; }}
  #input {{ width: 100%; font-size: 20px; padding: 12px; box-sizing: border-box; }}
  #preview {{
    font-family: "JianziPu";
    font-size: 130px;
    line-height: 1.35;
    min-height: 210px;
    margin-top: 16px;
    padding: 16px;
    border: 1px dashed #bbb;
    border-radius: 8px;
    font-feature-settings: "liga" 1;
    word-break: break-all;
  }}
  code {{ background: #f3f3f3; padding: 2px 6px; border-radius: 4px; margin-right: 6px; font-size: 13px; }}
</style>
</head>
<body>
<h1>减字谱字体测试页 · {author}</h1>
<p class="note">
  输入框输入减字编码，多个减字用空格分隔，连字（liga）生效时会实时替换为减字字形。
  输入过程中汉字不可见属正常现象（基础汉字为空白字形）。<b>简体、繁体输入均可</b>（如 换 与 換 等价），混输亦可。
  本字体共 {len(mapping)} 个减字：{len(multi)} 个多字编码直接连字；{len(singles)} 个单字编码需加 <code>〇</code> 前缀触发{single_note}，单字直打保持空白、不劫持正文。
</p>
<input id="input" value="{default_text}" autocomplete="off">
<div id="preview">{default_text}</div>
<p class="note">库内可用的测试编码：{hints}</p>
<script>
  const input = document.getElementById("input");
  const preview = document.getElementById("preview");
  input.addEventListener("input", () => {{ preview.textContent = input.value; }});
</script>
</body>
</html>
"""
    page = args.out_dir / "测试页.html"
    page.write_text(html, encoding="utf-8")
    print(f"测试页: {page}  ({page.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
import argparse
from pathlib import Path

from jianzipu.compile.builder import build_font

DEFAULT_OUT_DIR = Path("dist")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="jianzi-compile", description="风格库 / 自定义库 → 字体文件（TTF/OTF）"
    )
    parser.add_argument("--db", type=Path, required=True, help="输入数据库（风格库或自定义库）")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="输出目录（默认 dist/）")
    parser.add_argument("--formats", default="ttf,otf", help="输出格式，逗号分隔（默认 ttf,otf）")
    args = parser.parse_args(argv)

    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
    result = build_font(args.db, args.out_dir, formats=formats)

    print(f"作者: {result.author}")
    print(f"减字: {result.jianzi_count}  连字规则: {result.ligature_count}  空白汉字: {result.blank_count}")
    if result.ttf_path:
        print(f"TTF: {result.ttf_path}")
    if result.otf_path:
        print(f"OTF: {result.otf_path}")
    print(f"映射: {result.mapping_path}")
    print(f"特性: {result.fea_path}")


if __name__ == "__main__":
    main()
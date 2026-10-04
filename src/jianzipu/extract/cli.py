import argparse
import sqlite3
from pathlib import Path

from jianzipu.extract.build import build_style_db

DEFAULT_SOURCE = Path(".raw/x.jdb")
DEFAULT_STK = Path(".raw/stk")
DEFAULT_OUT_DIR = Path("styles")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jianzi-extract", description="原始库 → 风格库抽取")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--author", help="只抽取指定作者")
    group.add_argument("--all", action="store_true", help="抽取全部作者")
    parser.add_argument("--min-chars", type=int, default=100,
                        help="--all 时只保留减字数不少于该值的作者（默认 100）")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--stk", type=Path, default=DEFAULT_STK)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.all:
        src = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
        authors = [
            r[0]
            for r in src.execute(
                "SELECT Author FROM qchar WHERE Author IS NOT NULL"
                " GROUP BY Author HAVING COUNT(*) >= ? ORDER BY Author",
                (args.min_chars,),
            )
        ]
        src.close()
    else:
        authors = [args.author]

    for author in authors:
        out_path = args.out_dir / f"{author}.sqlite"
        build_style_db(args.source, args.stk, author=author, out_path=out_path)
        print(f"生成 {out_path}")


if __name__ == "__main__":
    main()

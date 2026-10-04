from pathlib import Path

from jianzipu.extract.cli import main

RAW_DB = Path(__file__).parent.parent / ".raw" / "x.jdb"
RAW_STK = Path(__file__).parent.parent / ".raw" / "stk"


def test_cli_extract_single_author(tmp_path):
    main(["--author", "云霄客", "--source", str(RAW_DB), "--stk", str(RAW_STK), "--out-dir", str(tmp_path)])
    assert (tmp_path / "云霄客.sqlite").exists()


def test_cli_extract_all_authors_with_min_chars(tmp_path):
    main(["--all", "--min-chars", "3000", "--source", str(RAW_DB),
          "--stk", str(RAW_STK), "--out-dir", str(tmp_path)])
    produced = {p.name for p in tmp_path.glob("*.sqlite")}
    assert produced == {"云霄客.sqlite", "少昊紫琴.sqlite"}

from pathlib import Path

from jianzipu.core.source import read_source_stats

RAW_DB = Path(__file__).parent.parent / ".raw" / "x.jdb"


def test_source_db_stats_match_known_facts():
    stats = read_source_stats(RAW_DB)

    assert stats.qchar_count == 60619
    assert stats.qstroke_count == 258
    assert "云霄客" in stats.authors
    assert "少昊紫琴" in stats.authors


def test_source_db_dangling_strokes():
    stats = read_source_stats(RAW_DB)

    assert len(stats.dangling_stroke_ids) == 5

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceStats:
    qchar_count: int
    qstroke_count: int
    authors: list[str]
    dangling_stroke_ids: list[int]


def read_source_stats(db_path: Path) -> SourceStats:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        qchar_count = cur.execute("SELECT COUNT(*) FROM qchar").fetchone()[0]
        qstroke_count = cur.execute("SELECT COUNT(*) FROM qstroke").fetchone()[0]
        authors = [
            row[0]
            for row in cur.execute(
                "SELECT DISTINCT Author FROM qchar WHERE Author IS NOT NULL ORDER BY Author"
            )
        ]
        dangling = [
            row[0]
            for row in cur.execute(
                "SELECT DISTINCT n.StrokeID FROM qnode n"
                " LEFT JOIN qstroke s ON n.StrokeID = s.ID WHERE s.ID IS NULL"
                " ORDER BY n.StrokeID"
            )
        ]
        return SourceStats(
            qchar_count=qchar_count,
            qstroke_count=qstroke_count,
            authors=authors,
            dangling_stroke_ids=dangling,
        )
    finally:
        conn.close()

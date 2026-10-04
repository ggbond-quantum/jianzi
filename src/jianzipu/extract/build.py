import re
import sqlite3
import warnings
from pathlib import Path

from jianzipu.extract.emf import extract_component_paths

CJK_INPUT_RE = re.compile(r"^[一-鿿]{1,10}$")

SCHEMA = """
CREATE TABLE jianzi (
    InputText    TEXT PRIMARY KEY,
    Title        TEXT,
    SourceAuthor TEXT,
    SourceID     INTEGER
);
CREATE TABLE node (
    InputText  TEXT REFERENCES jianzi(InputText),
    Seq        INTEGER,
    StrokeID   INTEGER,
    DistX      INTEGER,
    DistY      INTEGER,
    DistWidth  INTEGER,
    DistHeight INTEGER,
    PRIMARY KEY (InputText, Seq)
);
CREATE TABLE component (
    StrokeID  INTEGER PRIMARY KEY,
    Title     TEXT,
    PathData  TEXT,
    OrgWidth  INTEGER,
    OrgHeight INTEGER
);
"""


def build_style_db(source_db: Path, stk_dir: Path, author: str, out_path: Path) -> None:
    source_db = Path(source_db)
    stk_dir = Path(stk_dir)
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()

    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    out = sqlite3.connect(out_path)
    try:
        out.executescript(SCHEMA)
        _fill_components(src, stk_dir, out)
        _fill_jianzi(src, author, out)
        out.commit()
    finally:
        out.close()
        src.close()


def _fill_components(src: sqlite3.Connection, stk_dir: Path, out: sqlite3.Connection) -> None:
    rows = src.execute(
        "SELECT ID, Title, FileName, OrgWidth, OrgHeight FROM qstroke ORDER BY ID"
    ).fetchall()
    for stroke_id, title, file_name, org_w, org_h in rows:
        emf_file = stk_dir / file_name
        paths = extract_component_paths(emf_file)
        path_data = " ".join(p.to_svg_d() for p in paths)
        out.execute(
            "INSERT INTO component (StrokeID, Title, PathData, OrgWidth, OrgHeight)"
            " VALUES (?, ?, ?, ?, ?)",
            (stroke_id, title, path_data, org_w, org_h),
        )


def _fill_jianzi(src: sqlite3.Connection, author: str, out: sqlite3.Connection) -> None:
    valid_strokes = {r[0] for r in src.execute("SELECT ID FROM qstroke")}
    rows = src.execute(
        "SELECT ID, Title, InputText FROM qchar WHERE Author = ? ORDER BY ID",
        (author,),
    ).fetchall()
    nodes_by_char: dict[int, list[tuple]] = {}
    for char_id, sid, dx, dy, dw, dh in src.execute(
        "SELECT n.CharID, n.StrokeID, n.DistX, n.DistY, n.DistWidth, n.DistHeight"
        " FROM qnode n JOIN qchar q ON n.CharID = q.ID"
        " WHERE q.Author = ? ORDER BY n.ID",
        (author,),
    ):
        nodes_by_char.setdefault(char_id, []).append((sid, dx, dy, dw, dh))
    seen: set[str] = set()
    for char_id, title, input_text in rows:
        if not input_text or not CJK_INPUT_RE.match(input_text):
            continue
        if input_text in seen:
            continue
        nodes = nodes_by_char.get(char_id, [])
        dangling = [n[0] for n in nodes if n[0] not in valid_strokes]
        if dangling:
            warnings.warn(f"跳过 {input_text}（源 ID {char_id}）：悬空 StrokeID {dangling}")
            continue
        seen.add(input_text)
        out.execute(
            "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID)"
            " VALUES (?, ?, ?, ?)",
            (input_text, title, author, char_id),
        )
        out.executemany(
            "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (input_text, seq, sid, dx, dy, dw, dh)
                for seq, (sid, dx, dy, dw, dh) in enumerate(nodes, start=1)
            ],
        )

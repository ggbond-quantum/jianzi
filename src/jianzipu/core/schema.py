"""风格库 / 自定义库共用的数据库 schema。

两个库必须同 schema（ADR 0002），因此定义只此一处：
抽取环节（extract）与自定义库创建（core.library）都从这里取。
"""

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
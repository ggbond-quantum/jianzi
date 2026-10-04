import sqlite3
from pathlib import Path

import pytest

from jianzipu.extract.build import build_style_db

RAW_DB = Path(__file__).parent.parent / ".raw" / "x.jdb"
RAW_STK = Path(__file__).parent.parent / ".raw" / "stk"


@pytest.fixture(scope="module")
def style_db(tmp_path_factory):
    out = tmp_path_factory.mktemp("styles") / "云霄客.sqlite"
    build_style_db(RAW_DB, RAW_STK, author="云霄客", out_path=out)
    return out


@pytest.fixture(scope="module")
def style_db_shaohao(tmp_path_factory):
    out = tmp_path_factory.mktemp("styles2") / "少昊紫琴.sqlite"
    build_style_db(RAW_DB, RAW_STK, author="少昊紫琴", out_path=out)
    return out


def query(db_path, sql, args=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_component_table_has_all_258_components(style_db):
    rows = query(style_db, "SELECT COUNT(*) FROM component")
    assert rows[0][0] == 258


def test_component_outline_stored_as_path_data(style_db):
    rows = query(
        style_db,
        "SELECT OrgWidth, OrgHeight, PathData FROM component WHERE Title = '散'",
    )
    assert len(rows) == 1
    org_w, org_h, path_data = rows[0]
    assert (org_w, org_h) == (203, 110)
    assert path_data.startswith("M ")
    assert "C " in path_data


def test_jianzi_count_after_filter_and_dedup(style_db):
    rows = query(style_db, "SELECT COUNT(*), COUNT(DISTINCT InputText) FROM jianzi")
    assert rows[0] == (3368, 3368)


def test_worked_example_dajiutiaoqi(style_db_shaohao):
    rows = query(
        style_db_shaohao,
        "SELECT SourceID, SourceAuthor FROM jianzi WHERE InputText = '大九挑七'",
    )
    assert rows == [(10303, "少昊紫琴")]
    nodes = query(
        style_db_shaohao,
        "SELECT Seq, StrokeID, DistX, DistY, DistWidth, DistHeight"
        " FROM node WHERE InputText = '大九挑七' ORDER BY Seq",
    )
    assert nodes == [
        (1, 319, 30, 1, 138, 145),
        (2, 109, 169, 1, 162, 130),
        (3, 304, 22, 201, 314, 145),
        (4, 107, 80, 144, 217, 148),
    ]


def test_dedup_keeps_smallest_source_id(style_db):
    rows = query(
        style_db,
        "SELECT SourceID FROM jianzi WHERE InputText = '潑剌大七半七散六'",
    )
    assert rows == [(13266,)]


def test_no_dangling_stroke_references(style_db):
    rows = query(
        style_db,
        "SELECT COUNT(*) FROM node n"
        " LEFT JOIN component c ON n.StrokeID = c.StrokeID"
        " WHERE c.StrokeID IS NULL",
    )
    assert rows[0][0] == 0

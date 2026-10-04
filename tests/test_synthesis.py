import sqlite3
from pathlib import Path

import pytest
from svgpathtools import parse_path

from jianzipu.core.synthesis import Placement, place_components, render_svg
from jianzipu.extract.build import build_style_db

RAW_DB = Path(__file__).parent.parent / ".raw" / "x.jdb"
RAW_STK = Path(__file__).parent.parent / ".raw" / "stk"


def test_place_components_maps_bbox_onto_placement_rect():
    components = {1: "M 10 10 L 20 10 L 20 30 Z"}
    placements = [Placement(stroke_id=1, x=50, y=60, w=100, h=200)]

    (placed,) = place_components(placements, components)

    xmin, xmax, ymin, ymax = placed.bbox()
    assert (xmin, xmax, ymin, ymax) == pytest.approx((50, 150, 60, 260))


def test_place_components_scales_axes_independently():
    components = {1: "M 0 0 L 10 0 L 10 40 Z"}
    placements = [Placement(stroke_id=1, x=0, y=0, w=100, h=100)]

    (placed,) = place_components(placements, components)

    xmin, xmax, ymin, ymax = placed.bbox()
    assert (xmax - xmin) == pytest.approx(100)
    assert (ymax - ymin) == pytest.approx(100)


@pytest.fixture(scope="module")
def shaohao_db(tmp_path_factory):
    out = tmp_path_factory.mktemp("styles") / "少昊紫琴.sqlite"
    build_style_db(RAW_DB, RAW_STK, author="少昊紫琴", out_path=out)
    return out


def test_compose_dajiutiaoqi_from_style_db(shaohao_db):
    conn = sqlite3.connect(shaohao_db)
    placements = [
        Placement(stroke_id=sid, x=dx, y=dy, w=dw, h=dh)
        for sid, dx, dy, dw, dh in conn.execute(
            "SELECT StrokeID, DistX, DistY, DistWidth, DistHeight"
            " FROM node WHERE InputText='大九挑七' ORDER BY Seq"
        )
    ]
    components = dict(conn.execute("SELECT StrokeID, PathData FROM component"))
    conn.close()

    paths = place_components(placements, components)
    assert len(paths) == 4

    expected_rects = [(30, 1, 138, 145), (169, 1, 162, 130), (22, 201, 314, 145), (80, 144, 217, 148)]
    for path, (dx, dy, dw, dh) in zip(paths, expected_rects):
        xmin, xmax, ymin, ymax = path.bbox()
        assert (xmin, ymin, xmax - xmin, ymax - ymin) == pytest.approx((dx, dy, dw, dh))

    svg = render_svg(paths)
    assert 'viewBox="0 0 350 350"' in svg

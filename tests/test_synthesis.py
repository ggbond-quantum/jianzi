import sqlite3
from pathlib import Path

import pytest
from svgpathtools import parse_path

from jianzipu.core.synthesis import (
    Placement,
    contour_area,
    normalize_contours,
    place_components,
    render_svg,
)
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


def test_normalize_contours_keeps_fill_and_hole_drops_mirror_copy():
    """同一图形被正反画两遍（EMF FillPath/StrokePath）时只保留一份，孔洞保留。

    保留两份会互相抵消 → 非零环绕下字形渲染成空心轮廓（实测「聲」）。
    """
    doubled = parse_path(
        "M 0 0 L 100 0 L 100 100 L 0 100 Z"      # 外框（正向）
        " M 20 20 L 20 80 L 80 80 L 80 20 Z"     # 孔洞（反向）
        " M 0 0 L 0 100 L 100 100 L 100 0 Z"     # 外框（反向，重复图形）
        " M 20 20 L 80 20 L 80 80 L 20 80 Z"     # 孔洞（正向，重复图形）
    )

    contours = normalize_contours([doubled])

    assert len(contours) == 2
    areas = sorted(abs(contour_area(c)) for c in contours)
    assert areas[0] < areas[1]  # 一小孔、一大外框
    assert contour_area(contours[0]) * contour_area(contours[1]) < 0  # 方向相反


def test_normalize_contours_pairs_slightly_offset_mirror_copy():
    """描边副本相对填充副本整体偏移（如「聲」左孔），仍应配对去重。

    副本偏移 5% 边长，超过按边长的中心容差（4%），旧判据会漏配而把孔洞填实。
    """
    letter = parse_path(
        "M 0 0 L 100 0 L 100 100 L 0 100 Z"      # 外框（正向）
        " M 20 20 L 20 80 L 80 80 L 80 20 Z"     # 孔洞（反向）
        " M 5 0 L 5 100 L 105 100 L 105 0 Z"     # 副本外框（反向，右移 5）
        " M 23 20 L 83 20 L 83 80 L 23 80 Z"     # 副本孔洞（正向，右移 3）
    )

    contours = normalize_contours([letter])

    assert len(contours) == 2
    assert contour_area(contours[0]) * contour_area(contours[1]) < 0  # 一外框一孔洞


def test_normalize_contours_keeps_both_sheng_holes(shaohao_db):
    """真实数据「聲」：外框与两个孔洞各有正反两份，规整后恰保留 3 条轮廓。"""
    conn = sqlite3.connect(shaohao_db)
    data = conn.execute("SELECT PathData FROM component WHERE Title = '聲'").fetchone()[0]
    conn.close()

    contours = normalize_contours([parse_path(data)])

    assert len(contours) == 3  # 外框 + 两个孔洞，六份镜像副本各留一份
    areas = sorted(contour_area(c) for c in contours)
    assert areas[0] < 0 < areas[1]  # 一孔洞与外框方向相反


def test_normalize_contours_keeps_distinct_overlapping_shapes():
    """位置不同的异号轮廓不是重复图形，不能误删（如「四」的孔洞）。"""
    nested = parse_path(
        "M 0 0 L 100 0 L 100 100 L 0 100 Z"
        " M 30 30 L 30 60 L 60 60 L 60 30 Z"
    )

    assert len(normalize_contours([nested])) == 2

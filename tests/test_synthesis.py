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
    """真实数据「聲」：外框与两个孔洞各有正反两份，规整后恰保留 3 条轮廓。

    朝向统一后（外轮廓翻正）：外框为正、两个孔洞均为负（与外框异号）。
    """
    conn = sqlite3.connect(shaohao_db)
    data = conn.execute("SELECT PathData FROM component WHERE Title = '聲'").fetchone()[0]
    conn.close()

    contours = normalize_contours([parse_path(data)])

    assert len(contours) == 3  # 外框 + 两个孔洞，六份镜像副本各留一份
    areas = [contour_area(c) for c in contours]
    main = max(areas, key=abs)
    holes = [a for a in areas if abs(a) < abs(main)]
    assert main > 0                  # 外轮廓统一为正
    assert len(holes) == 2
    assert all(h < 0 for h in holes)  # 两个孔洞都保持空心


def test_normalize_contours_keeps_distinct_overlapping_shapes():
    """位置不同的异号轮廓不是重复图形，不能误删（如「四」的孔洞）。"""
    nested = parse_path(
        "M 0 0 L 100 0 L 100 100 L 0 100 Z"
        " M 30 30 L 30 60 L 60 60 L 60 30 Z"
    )

    assert len(normalize_contours([nested])) == 2


def test_normalize_unifies_direction_across_overlapping_parts():
    """方向相反的部件统一朝正：否则重叠区在非零环绕下相互抵消成白色空洞。

    源材料（EMF+）里不同构件的环绕方向不一致（实测少昊紫琴库 2626 对
    重叠部件方向相反），两个部件部分重叠时重叠区 winding 归零、渲染成空洞。
    """
    forward = parse_path("M 0 0 L 100 0 L 100 100 L 0 100 Z")     # 正向
    backward = parse_path("M 50 0 L 50 100 L 150 100 L 150 0 Z")  # 反向

    # 单个整体反向的部件也要翻正（与放入哪个字无关）
    contours = normalize_contours([backward])
    assert contour_area(contours[0]) > 0

    contours = normalize_contours([forward, backward])
    assert len(contours) == 2
    areas = [contour_area(c) for c in contours]
    assert areas[0] > 0 and areas[1] > 0  # 同向 → 重叠区叠加填充


def test_normalize_keeps_hole_opposite_after_flip():
    """整体反向的部件（外框负 + 孔洞正）翻转后外框正、孔洞负：孔洞仍空心。"""
    reversed_letter = parse_path(
        "M 0 0 L 0 200 L 200 200 L 200 0 Z"       # 外框（反向）
        " M 50 50 L 100 50 L 100 100 L 50 100 Z"  # 孔洞（正向，在外框内）
    )

    contours = normalize_contours([reversed_letter])

    assert len(contours) == 2
    areas = [contour_area(c) for c in contours]
    main = max(areas, key=abs)
    hole = min(areas, key=abs)
    assert main > 0   # 外轮廓统一为正
    assert hole < 0   # 孔洞与外轮廓异号（非零环绕下保持空心）

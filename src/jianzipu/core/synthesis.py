from dataclasses import dataclass

from svgpathtools import CubicBezier, Line, Path, QuadraticBezier, parse_path

GRID_SIZE = 350  # 减字部件摆放网格（350×350，Y 轴向下）


@dataclass(frozen=True)
class Placement:
    stroke_id: int
    x: float
    y: float
    w: float
    h: float


def _transform_path(path: Path, sx: float, sy: float, tx: float, ty: float) -> Path:
    def xf(z: complex) -> complex:
        return complex(z.real * sx + tx, z.imag * sy + ty)

    segments = []
    for seg in path:
        seg_type = type(seg)
        attrs = {
            name: xf(getattr(seg, name))
            for name in ("start", "control1", "control2", "end")
            if hasattr(seg, name)
        }
        segments.append(seg_type(**attrs))
    return Path(*segments)


def place_components(placements: list[Placement], components: dict[int, str]) -> list[Path]:
    placed = []
    for p in placements:
        path = parse_path(components[p.stroke_id])
        xmin, xmax, ymin, ymax = path.bbox()
        bw = xmax - xmin
        bh = ymax - ymin
        if bw == 0 or bh == 0:
            raise ValueError(f"部件 {p.stroke_id} 的路径包围盒退化")
        sx = p.w / bw
        sy = p.h / bh
        placed.append(_transform_path(path, sx, sy, p.x - xmin * sx, p.y - ymin * sy))
    return placed


def render_svg(paths: list[Path], grid_size: int = GRID_SIZE) -> str:
    d = " ".join(p.d() for p in paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {grid_size} {grid_size}"><path d="{d}"/></svg>'
    )


# ---- 轮廓规整：切分与去重 ----
#
# 源材料（EMF+）里同一图形可能被画两遍：FillPath 与 StrokePath 各写一份，
# 两份的环绕方向相反、曲线几乎重合（描边版本略偏移）。两份并存时非零环绕
# 互相抵消，字形会渲染成空心轮廓（实测「聲」）。编译与预览共用这里的
# 规整逻辑，保证"预览 = 编译结果"。
#
# 镜像重复的判据用「曲线平均距离」而不是包围盒容差：「聲」左孔的描边副本
# 比填充副本中心偏 43 单位（4.4% 边长），超过按边长 4% 的中心容差而漏配，
# 结果一份孔洞两份并存被填实。全库实测：三对副本距离比 0.5%~1.8%，
# 其余所有异号轮廓对均 ≥6.2%，5% 阈值有 3 倍以上余量。

_MIRROR_BOX_IOU = 0.5     # 粗筛：包围盒重合度（便宜的前置过滤，真正的判据是曲线距离）
_MIRROR_DIST_TOL = 0.05   # 精判：曲线平均距离 / 包围盒对角线
_SOURCE_SAMPLES = 16      # 距离计算：源轮廓每段采样点数
_TARGET_SAMPLES = 64      # 距离计算：目标轮廓每段采样点数


def path_contours(path: Path) -> list[list]:
    """把 Path 拆成轮廓（子路径在同一点接续时视为同一轮廓）。"""
    segments = list(path)
    if not segments:
        return []
    groups, current = [], [segments[0]]
    for previous, segment in zip(segments, segments[1:]):
        if abs(segment.start - previous.end) > 1e-6:
            groups.append(current)
            current = []
        current.append(segment)
    groups.append(current)
    return groups


def _segment_key(segment) -> tuple:
    values = []
    for name in ("start", "control1", "control2", "control", "end"):
        point = getattr(segment, name, None)
        if point is not None:
            values.append((round(point.real, 3), round(point.imag, 3)))
    return (type(segment).__name__, tuple(values))


def contour_area(contour: list) -> float:
    """轮廓的有向面积（符号表示环绕方向）。"""
    points = [segment.start for segment in contour]
    total = 0.0
    for index in range(len(points)):
        following = (index + 1) % len(points)
        total += (
            points[index].real * points[following].imag
            - points[following].real * points[index].imag
        )
    return total / 2


def _contour_box(contour: list) -> tuple[float, float, float, float]:
    xs = [point.real for segment in contour for point in (segment.start, segment.end)]
    ys = [point.imag for segment in contour for point in (segment.start, segment.end)]
    return min(xs), min(ys), max(xs), max(ys)


def drop_exact_duplicates(contours: list[list]) -> list[list]:
    """去掉完全重复的轮廓：源数据中同一闭合轮廓可能被原样走两遍（[A, A]）。"""
    result, seen = [], set()
    for contour in contours:
        if contour and len(contour) % 2 == 0:
            half = len(contour) // 2
            first, second = contour[:half], contour[half:]
            if [_segment_key(s) for s in first] == [_segment_key(s) for s in second]:
                contour = first
        key = tuple(_segment_key(s) for s in contour)
        if key in seen:
            continue
        seen.add(key)
        result.append(contour)
    return result


def _box_overlap_ratio(first: list, second: list) -> float:
    """两条轮廓包围盒的 IoU（交并比），镜像配对的粗筛。"""
    ax0, ay0, ax1, ay1 = _contour_box(first)
    bx0, by0, bx1, by1 = _contour_box(second)
    width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    height = max(0.0, min(ay1, by1) - max(ay0, by0))
    intersection = width * height
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - intersection
    return intersection / union if union > 0 else 0.0


def _mean_min_distance(source: list, target: list) -> float:
    """源轮廓各采样点到目标轮廓采样点的最近距离均值。"""
    target_points = [
        segment.point(index / _TARGET_SAMPLES)
        for segment in target
        for index in range(_TARGET_SAMPLES)
    ]
    total, count = 0.0, 0
    for segment in source:
        for index in range(_SOURCE_SAMPLES):
            point = segment.point(index / _SOURCE_SAMPLES)
            total += min(abs(point - other) for other in target_points)
            count += 1
    return total / count if count else float("inf")


def _curve_distance_ratio(first: list, second: list) -> float:
    """两条轮廓的对称平均距离 / 包围盒对角线。"""
    distance = _mean_min_distance(first, second) + _mean_min_distance(second, first)
    x0, y0, x1, y1 = _contour_box(first)
    diagonal = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    if diagonal <= 0:
        return float("inf")
    return distance / 2 / diagonal


def _is_mirror_of(first: list, second: list) -> bool:
    """两条轮廓是否同一图形被正反画了两遍（FillPath 与 StrokePath 各写一份）。

    判据：环绕方向相反 + 包围盒高度重合 + 曲线几乎重合。描边版本相对填充
    版本整体略有偏移（「聲」左孔中心偏 4.4%），因此用曲线平均距离而不是
    中心/边长容差。
    """
    if contour_area(first) * contour_area(second) >= 0:
        return False
    if _box_overlap_ratio(first, second) < _MIRROR_BOX_IOU:
        return False
    return _curve_distance_ratio(first, second) <= _MIRROR_DIST_TOL


def drop_mirrored_duplicates(contours: list[list]) -> list[list]:
    """去掉同一图形的镜像重复份（FillPath/StrokePath 各写一份的情形）。"""
    dropped: set[int] = set()
    for i in range(len(contours)):
        if i in dropped:
            continue
        for j in range(i + 1, len(contours)):
            if j in dropped:
                continue
            if _is_mirror_of(contours[i], contours[j]):
                dropped.add(j)
                break
    return [c for index, c in enumerate(contours) if index not in dropped]


def normalize_contours(paths: list[Path]) -> list[list]:
    """渲染/编译前的统一规整：逐部件 切分轮廓 → 去完全重复 → 去镜像重复 → 统一朝向。

    逐部件处理（而不是把全部轮廓混在一起）：朝向统一是以部件为单位的外轮廓
    翻正，部件之间不构成嵌套关系，混合处理会丢失这一分组语义。
    """
    result: list[list] = []
    for path in paths:
        group = drop_mirrored_duplicates(drop_exact_duplicates(path_contours(path)))
        result.extend(orient_contours(group))
    return result


def _reverse_contour(contour: list) -> list:
    """反转轮廓的环绕方向：段序倒置、每段首尾互换。"""
    result = []
    for segment in reversed(contour):
        kind = type(segment).__name__
        if kind == "Line":
            result.append(Line(segment.end, segment.start))
        elif kind == "CubicBezier":
            result.append(
                CubicBezier(segment.end, segment.control2, segment.control1, segment.start)
            )
        elif kind == "QuadraticBezier":
            result.append(QuadraticBezier(segment.end, segment.control, segment.start))
        else:
            raise TypeError(f"未知段类型: {kind}")
    return result


def orient_contours(contours: list[list]) -> list[list]:
    """统一部件朝向：以最大轮廓为准，整体翻转为「外轮廓为正」。

    部件内部的方向关系（孔洞与外轮廓相反）由源材料保证，这里只做整体翻转
    开关。必须统一的原因：不同构件的源数据环绕方向不一致（实测少昊紫琴库
    有 2626 对方向相反又重叠的部件），非零环绕下重叠区 winding 归零，
    渲染成白色空洞；字体编译（TrueType 同用非零环绕）同样受影响。
    """
    if not contours:
        return contours
    areas = [contour_area(c) for c in contours]
    main = max(range(len(contours)), key=lambda index: abs(areas[index]))
    if areas[main] > 0:
        return contours
    return [_reverse_contour(c) for c in contours]

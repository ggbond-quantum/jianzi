import struct
from dataclasses import dataclass
from pathlib import Path

_EMF_COMMENT = 70
_EMF_PLUS_MAGIC = b"EMF+"
_PLUS_OBJECT = 0x4008
_PLUS_FILL_PATH = 0x4014
_PLUS_DRAW_PATH = 0x4015
_OBJECT_TYPE_PATH = 3

_POINT_TYPE_MASK = 0x07
_POINT_CLOSE = 0x80
_POINT_START = 0
_POINT_LINE = 1
_POINT_BEZIER = 3


class EmfParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComponentPath:
    points: list[tuple[float, float]]
    point_types: list[int]

    def to_svg_d(self) -> str:
        parts = []
        i = 0
        n = len(self.points)
        while i < n:
            ptype = self.point_types[i] & _POINT_TYPE_MASK
            close = bool(self.point_types[i] & _POINT_CLOSE)
            x, y = self.points[i]
            if ptype == _POINT_START:
                parts.append(f"M {x:g} {y:g}")
            elif ptype == _POINT_LINE:
                parts.append(f"L {x:g} {y:g}")
            elif ptype == _POINT_BEZIER:
                if i + 2 >= n:
                    raise EmfParseError("贝塞尔点不足三个")
                x2, y2 = self.points[i + 1]
                x3, y3 = self.points[i + 2]
                parts.append(f"C {x:g} {y:g} {x2:g} {y2:g} {x3:g} {y3:g}")
                close = close or bool(self.point_types[i + 2] & _POINT_CLOSE)
                i += 2
            else:
                raise EmfParseError(f"未知点类型: {ptype}")
            if close:
                parts.append("Z")
            i += 1
        return " ".join(parts)

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


def _walk_emf_records(data: bytes):
    off = 0
    while off + 8 <= len(data):
        rtype, rsize = struct.unpack_from("<II", data, off)
        if rsize < 8:
            break
        yield rtype, data[off: off + rsize]
        off += rsize


def _walk_plus_records(payload: bytes):
    off = 0
    while off + 12 <= len(payload):
        rtype, flags, rsize, dsize = struct.unpack_from("<HHII", payload, off)
        if rsize < 12:
            break
        yield rtype, flags, payload[off + 12: off + 12 + dsize]
        off += rsize


def _parse_path_object(data: bytes) -> ComponentPath:
    _version, count, _flags = struct.unpack_from("<III", data, 0)
    floats = struct.unpack_from(f"<{count * 2}f", data, 12)
    points = [(floats[i * 2], floats[i * 2 + 1]) for i in range(count)]
    types_off = 12 + count * 8
    point_types = list(data[types_off: types_off + count])
    if len(point_types) != count:
        raise EmfParseError("点类型数组长度不足")
    return ComponentPath(points=points, point_types=point_types)


def extract_component_paths(emf_path: Path) -> list[ComponentPath]:
    data = Path(emf_path).read_bytes()
    objects: dict[int, ComponentPath] = {}
    filled: list[ComponentPath] = []
    for rtype, record in _walk_emf_records(data):
        if rtype != _EMF_COMMENT:
            continue
        cmt_len = struct.unpack_from("<I", record, 8)[0]
        payload = record[12: 12 + cmt_len]
        if not payload.startswith(_EMF_PLUS_MAGIC):
            continue
        for ptype, flags, pdata in _walk_plus_records(payload[4:]):
            if ptype == _PLUS_OBJECT and (flags >> 8) == _OBJECT_TYPE_PATH:
                objects[flags & 0xFF] = _parse_path_object(pdata)
            elif ptype in (_PLUS_FILL_PATH, _PLUS_DRAW_PATH):
                path_id = flags & 0xFF
                if path_id not in objects:
                    raise EmfParseError(f"引用了未定义的 Path 对象 id={path_id}")
                filled.append(objects[path_id])
    if not filled:
        raise EmfParseError(f"{Path(emf_path).name} 中未找到 EMF+ 填充路径")
    return filled


def convert_emf_to_svg(emf_path: Path) -> str:
    paths = extract_component_paths(emf_path)
    x0, y0, x1, y1 = paths[0].bbox()
    for p in paths[1:]:
        bx0, by0, bx1, by1 = p.bbox()
        x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
    w = x1 - x0
    h = y1 - y0
    path_tags = "".join(f'<path d="{p.to_svg_d()}"/>' for p in paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{x0:g} {y0:g} {w:g} {h:g}">{path_tags}</svg>'
    )

from dataclasses import dataclass

from svgpathtools import Path, parse_path

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

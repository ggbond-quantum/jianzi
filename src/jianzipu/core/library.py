"""风格库 / 自定义库的统一访问（core 层，无 GUI 依赖）。

数据库路径一律由调用方以参数传入，源码中不写死路径。
风格库以只读方式打开；自定义库的写入接口见 create_custom_db / pick_entry。
"""

from __future__ import annotations

import re
import sqlite3
import warnings
from dataclasses import dataclass
from pathlib import Path

from jianzipu.core.schema import SCHEMA
from jianzipu.core.scripts import PREFIX_CHAR, equivalence_key, to_simplified
from jianzipu.core.synthesis import GRID_SIZE, Placement

FIT_MARGIN = 0.05  # 单构件入库时的字身框留白比例
_TRAILING_DIGITS = re.compile(r"\d+$")


@dataclass(frozen=True)
class JianziEntry:
    input_text: str
    title: str
    source_author: str
    source_id: int


@dataclass(frozen=True)
class ComponentEntry:
    """一个基本构件（含轮廓与原始尺寸），用于部件浏览与单构件减字入库。"""

    stroke_id: int
    title: str
    path_data: str
    org_width: int
    org_height: int


class JianziLibrary:
    """对一个风格库 / 自定义库的读取访问。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    @classmethod
    def load(cls, path: Path) -> "JianziLibrary":
        return cls(path)

    def close(self) -> None:
        self._conn.close()

    @property
    def author(self) -> str:
        row = self._conn.execute(
            "SELECT SourceAuthor FROM jianzi WHERE SourceAuthor IS NOT NULL LIMIT 1"
        ).fetchone()
        return row[0] if row else ""

    def entries(self) -> list[JianziEntry]:
        return [
            JianziEntry(input_text, title or "", author or "", source_id)
            for input_text, title, author, source_id in self._conn.execute(
                "SELECT InputText, Title, SourceAuthor, SourceID"
                " FROM jianzi ORDER BY SourceID, InputText"
            )
        ]

    def placements(self, text: str) -> list[Placement]:
        return [
            Placement(stroke_id, dx, dy, dw, dh)
            for stroke_id, dx, dy, dw, dh in self._conn.execute(
                "SELECT StrokeID, DistX, DistY, DistWidth, DistHeight"
                " FROM node WHERE InputText = ? ORDER BY Seq",
                (text,),
            )
        ]

    def components(self) -> dict[int, str]:
        return dict(self._conn.execute("SELECT StrokeID, PathData FROM component"))

    def component_list(self) -> list[ComponentEntry]:
        """全部构件（含名称与原始尺寸），按 StrokeID 排序。"""
        return [
            ComponentEntry(stroke_id, title or "", path_data or "", org_w, org_h)
            for stroke_id, title, path_data, org_w, org_h in self._conn.execute(
                "SELECT StrokeID, Title, PathData, OrgWidth, OrgHeight FROM component ORDER BY StrokeID"
            )
        ]

    def component_entries(self, stroke_ids) -> list[ComponentEntry]:
        """指定构件的完整信息（用于把构件轮廓复制进自定义库）。"""
        wanted = set(stroke_ids)
        return [c for c in self.component_list() if c.stroke_id in wanted]

    def find(self, query: str) -> list[JianziEntry]:
        """等价匹配检索：按等价键比较，任意写法都能命中库中已存的另一种写法。"""
        key = equivalence_key(query)
        return [e for e in self.entries() if equivalence_key(e.input_text) == key]


@dataclass(frozen=True)
class CreateResult:
    copied: int      # 成功复制的减字数
    converted: int   # 编码发生简体变化的条数
    conflicts: int   # 转换后与已有条目撞键而跳过的条数（防御；实测数据为 0）


def create_custom_db(dest: Path, source: Path | None = None) -> CreateResult:
    """创建自定义库：source=None 新建空库；否则整体复制并批量转简体（ADR 0006）。

    目标已存在时拒绝创建，避免覆盖用户数据（另存为请换路径）。
    """
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"自定义库已存在：{dest}")
    out = sqlite3.connect(dest)
    try:
        out.executescript(SCHEMA)
        result = (
            _copy_as_simplified(Path(source), out)
            if source is not None
            else CreateResult(copied=0, converted=0, conflicts=0)
        )
        out.commit()
    finally:
        out.close()
    return result


def _copy_as_simplified(source: Path, out: sqlite3.Connection) -> CreateResult:
    """整体复制来源库：部件、减字、node 全部写入，InputText 逐条转简体。"""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        out.executemany(
            "INSERT INTO component (StrokeID, Title, PathData, OrgWidth, OrgHeight)"
            " VALUES (?, ?, ?, ?, ?)",
            src.execute("SELECT StrokeID, Title, PathData, OrgWidth, OrgHeight FROM component"),
        )
        copied = converted = conflicts = 0
        for input_text, title, _author, source_id in src.execute(
            "SELECT InputText, Title, SourceAuthor, SourceID FROM jianzi ORDER BY SourceID, InputText"
        ):
            normalized = to_simplified(input_text)
            nodes = list(
                src.execute(
                    "SELECT Seq, StrokeID, DistX, DistY, DistWidth, DistHeight"
                    " FROM node WHERE InputText = ? ORDER BY Seq",
                    (input_text,),
                )
            )
            try:
                # 自定义库不记录作者（只保留来源 ID 以便溯源）
                out.execute(
                    "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID)"
                    " VALUES (?, ?, NULL, ?)",
                    (normalized, title, source_id),
                )
            except sqlite3.IntegrityError:
                conflicts += 1
                warnings.warn(f"编码「{input_text}」转为「{normalized}」后与已有条目冲突，已跳过")
                continue
            out.executemany(
                "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(normalized, *node_row) for node_row in nodes],
            )
            copied += 1
            if normalized != input_text:
                converted += 1
        return CreateResult(copied=copied, converted=converted, conflicts=conflicts)
    finally:
        src.close()


@dataclass(frozen=True)
class PickResult:
    status: str                      # "inserted" | "conflict"
    normalized_text: str             # 逐条转换后的简体编码（实际写入/判重用的键）
    existing: JianziEntry | None = None


def _insert_components(conn: sqlite3.Connection, components: list[ComponentEntry]) -> None:
    """把构件轮廓写入库中（已存在则跳过），保证自定义库自包含。"""
    conn.executemany(
        "INSERT OR IGNORE INTO component (StrokeID, Title, PathData, OrgWidth, OrgHeight)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (c.stroke_id, c.title, c.path_data, c.org_width, c.org_height)
            for c in components
        ],
    )


def fill_missing_components(custom_path: Path, source_path: Path) -> int:
    """用来源风格库补齐自定义库中缺失的构件轮廓（只补缺、不覆盖），返回补齐数量。

    历史上经挑选入库的条目可能只有 node 而缺 component 行；实测各风格库的
    component 表内容一致，因此任意一个风格库都能补齐任意自定义库。
    """
    conn = sqlite3.connect(custom_path)
    try:
        used = {row[0] for row in conn.execute("SELECT DISTINCT StrokeID FROM node")}
        present = {row[0] for row in conn.execute("SELECT StrokeID FROM component")}
        missing = used - present
    finally:
        conn.close()
    if not missing:
        return 0

    source = JianziLibrary.load(source_path)
    try:
        components = source.component_entries(missing)
    finally:
        source.close()

    conn = sqlite3.connect(custom_path)
    try:
        _insert_components(conn, components)
        conn.commit()
    finally:
        conn.close()
    return len(components)


def default_component_code(component: ComponentEntry) -> str:
    """构件的默认编码：转义前缀 〇 + 构件名去掉尾部数字（泛2 → 〇泛、跪指2 → 〇跪指）。"""
    name = _TRAILING_DIGITS.sub("", component.title) or component.title
    return f"{PREFIX_CHAR}{name}"


def fit_component_placement(component: ComponentEntry) -> Placement:
    """按原始比例缩放到字身框（留 FIT_MARGIN 边距）并居中，不拉伸。"""
    content = GRID_SIZE * (1 - 2 * FIT_MARGIN)
    scale = min(content / component.org_width, content / component.org_height)
    w = round(component.org_width * scale)
    h = round(component.org_height * scale)
    return Placement(component.stroke_id, (GRID_SIZE - w) // 2, (GRID_SIZE - h) // 2, w, h)


def create_component_entry(
    custom_path: Path, component: ComponentEntry, input_text: str | None = None
) -> PickResult:
    """把一个基本构件写为自定义库中的单构件减字条目（省略记谱法用）。

    编码默认取 `〇` + 构件名（去尾部数字），适配居中摆放；构件轮廓一并入库，
    自定义库保持自包含。编码已存在时返回冲突与已存在条目，不覆盖。
    """
    normalized = to_simplified(input_text or default_component_code(component))
    placement = fit_component_placement(component)
    conn = sqlite3.connect(custom_path)
    try:
        row = conn.execute(
            "SELECT Title, SourceAuthor, SourceID FROM jianzi WHERE InputText = ?",
            (normalized,),
        ).fetchone()
        if row:
            return PickResult(
                status="conflict",
                normalized_text=normalized,
                existing=JianziEntry(normalized, row[0] or "", row[1] or "", row[2]),
            )
        _insert_components(conn, [component])
        conn.execute(
            "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID) VALUES (?, ?, NULL, NULL)",
            (normalized, component.title),
        )
        conn.execute(
            "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (normalized, placement.stroke_id, placement.x, placement.y, placement.w, placement.h),
        )
        conn.commit()
    finally:
        conn.close()
    return PickResult(status="inserted", normalized_text=normalized)


def pick_entry(
    custom_path: Path,
    source_path: Path,
    source_text: str,
    target_text: str | None = None,
    target_title: str | None = None,
) -> PickResult:
    """把一个风格库中的减字挑入自定义库：逐条转简体后写入。

    source_text 是来源库中的编码；target_text / target_title 是写进自定义库的
    编码与标题，省略时用来源条目的对应值（编码转简体；两个来源的同名减字
    可借此共存）。目标编码已存在时返回冲突与已存在条目，由调用方提示，
    不覆盖、不写入。
    """
    style = JianziLibrary.load(source_path)
    try:
        source_entry = next((e for e in style.entries() if e.input_text == source_text), None)
        if source_entry is None:
            raise ValueError(f"来源库中不存在编码「{source_text}」")
        placements = style.placements(source_text)
        components = style.component_entries(p.stroke_id for p in placements)
    finally:
        style.close()

    normalized = to_simplified(target_text or source_text)
    conn = sqlite3.connect(custom_path)
    try:
        row = conn.execute(
            "SELECT Title, SourceAuthor, SourceID FROM jianzi WHERE InputText = ?",
            (normalized,),
        ).fetchone()
        if row:
            return PickResult(
                status="conflict",
                normalized_text=normalized,
                existing=JianziEntry(normalized, row[0] or "", row[1] or "", row[2]),
            )
        _insert_components(conn, components)
        # 自定义库不记录作者：它是混合来源的工作库（作者只存在于风格库）
        conn.execute(
            "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID)"
            " VALUES (?, ?, NULL, ?)",
            (normalized, target_title or source_entry.title, source_entry.source_id),
        )
        conn.executemany(
            "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (normalized, seq, p.stroke_id, p.x, p.y, p.w, p.h)
                for seq, p in enumerate(placements, start=1)
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return PickResult(status="inserted", normalized_text=normalized)


def rename_entry(custom_path: Path, old_text: str, new_text: str) -> PickResult:
    """重命名自定义库中的条目：主键改写并级联更新 node 表。

    新编码同样执行简体转换（库不变量）；与既有条目撞键时返回冲突，不做改动。
    """
    normalized = to_simplified(new_text)
    conn = sqlite3.connect(custom_path)
    try:
        if not conn.execute("SELECT 1 FROM jianzi WHERE InputText = ?", (old_text,)).fetchone():
            raise ValueError(f"库中不存在编码「{old_text}」")
        if normalized == old_text:
            return PickResult(status="inserted", normalized_text=normalized)
        row = conn.execute(
            "SELECT Title, SourceAuthor, SourceID FROM jianzi WHERE InputText = ?",
            (normalized,),
        ).fetchone()
        if row:
            return PickResult(
                status="conflict",
                normalized_text=normalized,
                existing=JianziEntry(normalized, row[0] or "", row[1] or "", row[2]),
            )
        conn.execute("UPDATE jianzi SET InputText = ? WHERE InputText = ?", (normalized, old_text))
        conn.execute("UPDATE node SET InputText = ? WHERE InputText = ?", (normalized, old_text))
        conn.commit()
    finally:
        conn.close()
    return PickResult(status="inserted", normalized_text=normalized)


def delete_entry(custom_path: Path, input_text: str) -> bool:
    """从自定义库删除一个条目（连同其 node 行），返回是否确实删除了内容。"""
    conn = sqlite3.connect(custom_path)
    try:
        cursor = conn.execute("DELETE FROM jianzi WHERE InputText = ?", (input_text,))
        conn.execute("DELETE FROM node WHERE InputText = ?", (input_text,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def save_custom_db_as(source_path: Path, dest_path: Path) -> Path:
    """把自定义库整体另存到新路径（备份/分发），当前库继续可用。"""
    source_path, dest_path = Path(source_path), Path(dest_path)
    if source_path.resolve() == dest_path.resolve():
        raise ValueError("目标路径与当前库相同")
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()
    return dest_path
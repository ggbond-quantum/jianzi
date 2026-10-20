"""减字预览控件与中栏版本面板。

预览直接调用 core.synthesis 合成轮廓（所见即所得 = 编译结果），不在 GUI 重复
实现摆放逻辑；填充规则与 TrueType 一致（非零环绕）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jianzipu.core.library import JianziEntry, JianziLibrary
from jianzipu.core.synthesis import normalize_contours, place_components

GRID = 350.0
PREVIEW_SIZE = 140
TILE_SIZE = 88
MIN_ZOOM = 0.25    # Ctrl+滚轮缩放下限
MAX_ZOOM = 16.0    # Ctrl+滚轮缩放上限
ZOOM_STEP = 1.25   # 每一格滚轮的缩放倍率

# 「挑选」回调：把来源库的条目写入自定义库（编码与标题可在列内编辑）
PickHandler = Callable[[JianziEntry, JianziLibrary, str, str], None]


def to_painter_path(paths: list, size: int) -> QPainterPath:
    """svgpathtools 的 Path 列表 → QPainterPath（350 网格等比映射到 size×size）。

    轮廓先经 core 的规整（去完全重复、去镜像重复），与编译层同一条代码路径；
    填充用非零环绕（与 TrueType 一致）。源数据中「三/七/九/十」的轮廓被原样走
    两遍、「聲」有正反两份重复图形，不规整会在 even-odd 或抵消下渲染成空心。
    """
    scale = size / GRID
    result = QPainterPath()
    result.setFillRule(Qt.FillRule.WindingFill)
    for contour in normalize_contours(paths):
        if not contour:
            continue
        cursor = contour[0].start
        result.moveTo(cursor.real * scale, cursor.imag * scale)
        for seg in contour:
            if abs(seg.start - cursor) > 1e-6:
                result.moveTo(seg.start.real * scale, seg.start.imag * scale)
            kind = type(seg).__name__
            if kind == "Line":
                result.lineTo(seg.end.real * scale, seg.end.imag * scale)
            elif kind == "CubicBezier":
                result.cubicTo(
                    seg.control1.real * scale,
                    seg.control1.imag * scale,
                    seg.control2.real * scale,
                    seg.control2.imag * scale,
                    seg.end.real * scale,
                    seg.end.imag * scale,
                )
            elif kind == "QuadraticBezier":
                result.quadTo(
                    seg.control.real * scale,
                    seg.control.imag * scale,
                    seg.end.real * scale,
                    seg.end.imag * scale,
                )
            else:
                raise TypeError(f"未知段类型: {kind}")
            cursor = seg.end
        if abs(cursor - contour[0].start) < 1e-6:
            result.closeSubpath()
    return result


def clear_layout(layout: QLayout) -> None:
    """清空布局：控件立即摘出控件树，再交由事件循环销毁。"""
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class JianziPreview(QWidget):
    """单个减字的预览控件：绘制 core 合成的轮廓，与编译结果一致。

    默认固定尺寸（部件缩略图）：Ctrl+滚轮以中心为锚缩放。
    interactive=True（中栏大预览）：随布局扩展填充（字形适配居中的方形视口），
    Ctrl+滚轮以鼠标位置为锚缩放，中键（滚轮键）按住拖动平移；
    右键经 context_menu_requested 交给上层（历史导航菜单）。
    """

    context_menu_requested = Signal(QPoint)  # 全局坐标

    def __init__(self, size: int = PREVIEW_SIZE, *, interactive: bool = False) -> None:
        super().__init__()
        self._paths: list = []
        self._size = size
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._interactive = interactive
        self._drag_start: QPointF | None = None
        self._pan_start = QPointF(0, 0)
        if interactive:
            self.setMinimumSize(size, size)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedSize(size, size)

    def set_paths(self, paths: list) -> None:
        self._paths = paths
        self.update()

    def reset_view(self) -> None:
        """复位缩放与平移（切换条目 / Ctrl+0）。"""
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    def _viewport(self) -> tuple[float, float, float]:
        """interactive 模式的居中方形视口：返回 (左偏移, 上偏移, 边长)。"""
        side = min(self.width(), self.height())
        return (self.width() - side) / 2, (self.height() - side) / 2, side

    def content_point_at(self, pos: QPointF) -> QPointF:
        """控件坐标 → 内容坐标（屏幕点 = 视口原点 + 平移 + 缩放×内容点）。"""
        ox, oy, _side = self._viewport()
        return (pos - QPointF(ox, oy) - self._pan) / self._zoom

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt 命名约定
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        steps = event.angleDelta().y() / 120
        if steps:
            if self._interactive:
                # 以光标为锚：缩放前后光标下的内容点不动
                content = self.content_point_at(event.position())
                self._zoom = min(MAX_ZOOM, max(MIN_ZOOM, self._zoom * ZOOM_STEP**steps))
                ox, oy, _side = self._viewport()
                self._pan = event.position() - QPointF(ox, oy) - self._zoom * content
            else:
                self._zoom = min(MAX_ZOOM, max(MIN_ZOOM, self._zoom * ZOOM_STEP**steps))
            self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名约定
        if self._interactive and event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = event.position()
            self._pan_start = QPointF(self._pan)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名约定
        if self._drag_start is not None:
            self._pan = self._pan_start + (event.position() - self._drag_start)
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名约定
        if self._drag_start is not None and event.button() == Qt.MouseButton.MiddleButton:
            self._drag_start = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        if self._interactive:
            self.context_menu_requested.emit(event.globalPos())
            return
        super().contextMenuEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt 命名约定
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.GlobalColor.black, 0))
        painter.setBrush(Qt.GlobalColor.black)
        if self._interactive:
            ox, oy, side = self._viewport()
            painter.translate(ox + self._pan.x(), oy + self._pan.y())
            painter.scale(self._zoom, self._zoom)
            painter.drawPath(to_painter_path(self._paths, side))
        else:
            center = self._size / 2
            painter.translate(center, center)
            painter.scale(self._zoom, self._zoom)
            painter.translate(-center, -center)
            painter.drawPath(to_painter_path(self._paths, self._size))


@dataclass(frozen=True)
class VersionItem:
    """中栏可显示的一个版本：条目 + 所属库 + 是否自定义库（只读，不可挑选）。"""

    entry: JianziEntry
    library: JianziLibrary
    is_custom: bool


class PreviewPanel(QWidget):
    """中栏单版本预览面板。

    中央大预览（缩放/平移/右键历史）满铺；左上叠加编码/来源信息，右上叠加
    「同编码版本」索引框（样式同侧栏目录树，单击切换版本）；底部操作区随版本
    类型切换：风格库版本为编码/标题输入框 + 挑选按钮，自定义库版本为只读标题。
    """

    version_selected = Signal(int)           # 索引框点了第 i 个版本
    history_menu_requested = Signal(QPoint)  # 预览区右键（全局坐标）

    def __init__(self, on_pick: PickHandler) -> None:
        super().__init__()
        self._on_pick = on_pick
        self._versions: list[VersionItem] = []
        self._current = -1

        self._preview = JianziPreview(interactive=True)
        self._preview.context_menu_requested.connect(self.history_menu_requested)

        self._info_host = QWidget()  # 左上：编码/来源（叠加在预览上）
        QVBoxLayout(self._info_host)
        self._index = QTreeWidget()  # 右上：同编码版本索引（叠加在预览上）
        self._index.setHeaderLabel("同编码版本")
        self._index.setFixedSize(170, 160)
        self._index.hide()
        self._index.currentItemChanged.connect(self._on_index_changed)

        stage = QWidget()
        grid = QGridLayout(stage)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self._preview, 0, 0)
        grid.addWidget(
            self._info_host, 0, 0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        grid.addWidget(
            self._index, 0, 0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self._bottom = QWidget()  # 底部操作区（随版本类型重建）
        self._bottom.setFixedHeight(96)
        QVBoxLayout(self._bottom)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(stage, 1)
        layout.addWidget(self._bottom)

    # ---- 对外操作 ----

    def show_versions(self, versions: list[VersionItem], current: int) -> None:
        """设置同编码的版本列表与当前版本；空列表等价于 clear()。"""
        self._versions = list(versions)
        self._current = current if self._versions else -1
        self._preview.reset_view()
        self._render_current()

    def clear(self) -> None:
        self._versions = []
        self._current = -1
        self._render_current()

    def set_current(self, index: int) -> None:
        """切换显示的版本（索引框单击；切换即复位缩放/平移）。"""
        if index == self._current or not 0 <= index < len(self._versions):
            return
        self._current = index
        self._preview.reset_view()
        self._render_current()

    def current_item(self) -> VersionItem | None:
        """当前显示的版本（无显示时为 None）。"""
        if 0 <= self._current < len(self._versions):
            return self._versions[self._current]
        return None

    def reset_view(self) -> None:
        self._preview.reset_view()

    # ---- 内部 ----

    def _on_index_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        index = self._index.indexOfTopLevelItem(current)
        if 0 <= index < len(self._versions):
            self.set_current(index)               # 面板自己完成切换
            self.version_selected.emit(index)     # 通知上层（历史记录）

    def _render_current(self) -> None:
        for host in (self._info_host, self._bottom):
            clear_layout(host.layout())
        if not 0 <= self._current < len(self._versions):
            self._preview.set_paths([])
            self._index.hide()
            return
        item = self._versions[self._current]
        entry, library = item.entry, item.library

        info_layout = self._info_host.layout()
        code_info = QLabel(f"编码：{entry.input_text}")
        source_info = QLabel("来源：自定义库" if item.is_custom else f"作者：{library.author}")
        info_layout.addWidget(code_info)
        info_layout.addWidget(source_info)

        bottom_layout = self._bottom.layout()
        try:
            self._preview.set_paths(
                place_components(library.placements(entry.input_text), library.components())
            )
        except KeyError as exc:  # 历史数据或手工 SQL 可能缺构件：降级提示
            self._preview.set_paths([])
            bottom_layout.addWidget(QLabel(f"缺少构件数据 {exc}，无法预览"))

        if item.is_custom:
            bottom_layout.addWidget(QLabel(f"标题：{entry.title or '（无标题）'}"))
        else:
            code = QLineEdit(entry.input_text)
            code.setToolTip("写入自定义库的编码（InputText），保存时转简体")
            title = QLineEdit(entry.title)
            title.setToolTip("写入自定义库的标题（Title），默认为风格库中的值")
            button = QPushButton("挑选")
            button.clicked.connect(
                lambda: self._on_pick(entry, library, code.text().strip(), title.text().strip())
            )
            bottom_layout.addWidget(self._row("编码：", code))
            bottom_layout.addWidget(self._row("标题：", title))
            bottom_layout.addWidget(button)

        self._refresh_index()

    def _refresh_index(self) -> None:
        self._index.blockSignals(True)
        try:
            self._index.clear()
            for version in self._versions:
                name = (
                    "自定义库"
                    if version.is_custom
                    else version.library.author or Path(version.library.path).stem
                )
                QTreeWidgetItem(self._index, [f"{name}：{version.entry.input_text}"])
            if 0 <= self._current < self._index.topLevelItemCount():
                self._index.setCurrentItem(self._index.topLevelItem(self._current))
        finally:
            self._index.blockSignals(False)
        self._index.setVisible(bool(self._versions))

    @staticmethod
    def _row(label: str, field: QLineEdit) -> QWidget:
        row = QWidget()
        inner = QHBoxLayout(row)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(QLabel(label))
        inner.addWidget(field)
        return row

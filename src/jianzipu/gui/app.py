"""减字浏览器：多库加载、跨作者版本对比、挑选写入自定义库。

预览直接调用 core.synthesis 合成轮廓（所见即所得 = 编译结果）；
检索、判重、简体转换复用 core.library / core.scripts，不在 GUI 重复实现。
部件浏览器另见 ComponentBrowserWindow：预览 258 个基本构件并把构件写为单构件减字。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jianzipu.core.library import (
    ComponentEntry,
    JianziEntry,
    JianziLibrary,
    PickResult,
    create_component_entry,
    create_custom_db,
    default_component_code,
    delete_entry,
    fill_missing_components,
    fit_component_placement,
    pick_entry,
    rename_entry,
    save_custom_db_as,
)
from jianzipu.core.scripts import equivalence_key
from jianzipu.core.synthesis import normalize_contours, place_components

GRID = 350.0
PREVIEW_SIZE = 140
TILE_SIZE = 88
DB_FILTER = "SQLite 数据库 (*.sqlite *.jdb);;所有文件 (*)"
TEXT_ROLE = Qt.ItemDataRole.UserRole       # 树节点上挂的条目编码（InputText）
LIB_ROLE = Qt.ItemDataRole.UserRole + 1    # 树节点上挂的所属库路径
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
            self._clear_layout(host.layout())
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
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # 立即摘出控件树，再交由事件循环销毁
                widget.deleteLater()

    @staticmethod
    def _row(label: str, field: QLineEdit) -> QWidget:
        row = QWidget()
        inner = QHBoxLayout(row)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(QLabel(label))
        inner.addWidget(field)
        return row


class ComponentTile(QWidget):
    """部件网格中的一格：构件缩略图 + 名称，点击即选中。"""

    clicked = Signal(int)

    def __init__(self, component: ComponentEntry, index: int) -> None:
        super().__init__()
        self.index = index
        self.component = component
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        preview = JianziPreview(TILE_SIZE)
        preview.set_paths(
            place_components(
                [fit_component_placement(component)], {component.stroke_id: component.path_data}
            )
        )
        label = QLabel(f"名称：{component.title or '（无名）'}")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        self.setToolTip(f"编号：{component.stroke_id}　名称：{component.title}")
        self.setAutoFillBackground(True)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt 命名约定
        self.clicked.emit(self.index)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet("background-color: #cfe4ff;" if selected else "")


class ComponentBrowserWindow(QMainWindow):
    """基本部件浏览器：浏览全部构件，并把选中的构件写为单构件减字条目。

    数据来自已加载的风格库（每个风格库都含全量构件）；入库时构件轮廓一并
    写入自定义库，编码默认 〇 + 构件名（可在下方编辑框修改）。
    """

    COLUMNS = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("基本部件")
        self.resize(920, 720)
        self._style: JianziLibrary | None = None
        self._custom: JianziLibrary | None = None
        self._components: list[ComponentEntry] = []
        self._tiles: list[ComponentTile] = []
        self._selected: int | None = None
        # 主窗口挂的回调：本窗口写入自定义库（工作副本）后置脏，供关闭时提示保存
        self.on_custom_changed: Callable[[], None] | None = None
        self._build_ui()
        self._refresh_status()

    # ---- 公开操作（菜单/按钮与测试共用） ----

    def load_style_library(self, path: Path) -> None:
        """加载风格库作为构件来源（只读），刷新部件网格。"""
        if self._style is not None:
            self._style.close()
        self._style = JianziLibrary.load(Path(path))
        self._components = self._style.component_list()
        self._refresh_grid()
        self._refresh_status()
        self._fill_missing_components()

    def load_custom_library(self, path: Path) -> None:
        """打开自定义库作为入库目标；若已有风格库，顺带补齐缺失的构件轮廓。"""
        if self._custom is not None:
            self._custom.close()
        self._custom = JianziLibrary.load(Path(path))
        self._refresh_status()
        self._fill_missing_components()

    def _fill_missing_components(self) -> None:
        if self._custom is None or self._style is None:
            return
        filled = fill_missing_components(self._custom.path, self._style.path)
        if filled:
            self.statusBar().showMessage(f"已从风格库补齐 {filled} 个构件轮廓", 8000)
            if self.on_custom_changed is not None:
                self.on_custom_changed()

    def component_count(self) -> int:
        return len(self._components)

    def select_component(self, index: int) -> None:
        if not 0 <= index < len(self._components):
            return
        self._selected = index
        for tile in self._tiles:
            tile.set_selected(tile.index == index)
        self._code.setText(default_component_code(self._components[index]))

    def add_selected(self) -> PickResult | None:
        """把当前选中的构件写为单构件减字条目。"""
        if self._selected is None:
            self.statusBar().showMessage("请先选中一个构件。", 5000)
            return None
        if self._custom is None:
            self.statusBar().showMessage("请先打开或新建一个自定义库。", 5000)
            return None
        component = self._components[self._selected]
        code = self._code.text().strip() or default_component_code(component)
        result = create_component_entry(self._custom.path, component, code)
        if result.status == "inserted":
            self.statusBar().showMessage(f"已加入「{result.normalized_text}」", 5000)
            if self.on_custom_changed is not None:
                self.on_custom_changed()
        else:
            self.statusBar().showMessage(f"「{result.normalized_text}」已存在，未改动", 5000)
        return result

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        bar = QHBoxLayout()
        load_style = QPushButton("加载风格库…")
        self._open_custom_button = QPushButton("打开自定义库…")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索构件名称或编号")
        bar.addWidget(load_style)
        bar.addWidget(self._open_custom_button)
        bar.addWidget(self._search, 1)
        outer.addLayout(bar)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self._grid_host)
        outer.addWidget(area, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("编码："))
        self._code = QLineEdit()
        self._code.setPlaceholderText("选中构件后自动填入，可修改")
        add = QPushButton("加入自定义库")
        bottom.addWidget(self._code, 1)
        bottom.addWidget(add)
        outer.addLayout(bottom)

        self.setCentralWidget(central)
        load_style.clicked.connect(self._choose_load_style)
        self._open_custom_button.clicked.connect(self._choose_open_custom)
        add.clicked.connect(self.add_selected)
        self._search.textChanged.connect(self._refresh_grid)

    def _refresh_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tiles = []
        query_key = equivalence_key(self._search.text().strip())
        row = col = 0
        for index, component in enumerate(self._components):
            target = f"{component.stroke_id} {equivalence_key(component.title)}"
            if query_key and query_key not in target:
                continue
            tile = ComponentTile(component, index)
            tile.clicked.connect(self.select_component)
            self._grid.addWidget(tile, row, col)
            self._tiles.append(tile)
            col += 1
            if col >= self.COLUMNS:
                col, row = 0, row + 1

    def _refresh_status(self) -> None:
        source = "未加载" if self._style is None else Path(self._style.path).stem
        target = "未加载" if self._custom is None else Path(self._custom.path).stem
        self.statusBar().showMessage(
            f"构件来源：{source}（共 {len(self._components)} 个部件）；入库目标：{target}"
        )

    # ---- 文件操作 ----

    def _choose_load_style(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载风格库（提供构件）", "", DB_FILTER)
        if path:
            self.load_style_library(Path(path))

    def _choose_open_custom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开自定义库", "", DB_FILTER)
        if path:
            self.load_custom_library(Path(path))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名约定
        if self._style is not None:
            self._style.close()
        if self._custom is not None:
            self._custom.close()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    """减字浏览器主窗口：左=自定义库目录树，中=版本对比预览，右=风格库目录树。

    自定义库按「工作副本」方式编辑：打开/新建时复制到临时文件，挑选、重命名、
    删除等写入只落在副本上（core 层写函数按路径操作，天然支持）；「保存」才把
    副本刷回磁盘原路径，「另存为」刷到新路径并改挂。有未保存更改时关闭窗口或
    取消加载会按 Word 方式提示（保存/不保存/取消）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("减字浏览器")
        self.resize(1000, 680)
        self._libraries: list[JianziLibrary] = []
        self._custom: JianziLibrary | None = None
        self._custom_saved_path: Path | None = None  # 磁盘上的保存位置（工作副本的原路径）
        self._custom_dirty = False                   # 工作副本有未保存的更改
        self._work_dir: tempfile.TemporaryDirectory | None = None
        self._current_text: str | None = None        # 中栏面板当前显示的编码
        self._history: list[tuple[str, str | None]] = []  # (编码, 风格库路径；None=自定义库)
        self._history_index = -1
        self._component_browser: ComponentBrowserWindow | None = None
        self._build_ui()
        self._refresh_status()

    # ---- 公开操作（菜单/按钮与测试共用） ----

    def load_style_library(self, path: Path) -> None:
        """加载一个风格库（只读）并刷新浏览列表。"""
        path = Path(path)
        if any(lib.path == path for lib in self._libraries):
            return
        self._libraries.append(JianziLibrary.load(path))
        self._refresh_list()
        self._refresh_status()
        self._fill_missing_components()

    def load_custom_library(self, path: Path) -> None:
        """打开一个自定义库作为挑选目标（有未保存更改时先询问）。

        实际编辑的是临时工作副本；磁盘文件要等「保存」才更新。若已有风格库，
        顺带补齐副本中缺失的构件轮廓（补齐也算未保存的更改）。
        """
        if not self._confirm_save_custom():
            return
        path = Path(path)
        self._current_text = None
        self._panel.clear()  # 先摘面板对旧库连接的引用，再丢弃工作副本
        self._discard_work_copy()
        self._work_dir = tempfile.TemporaryDirectory(prefix="jianzipu-")
        work = Path(self._work_dir.name) / path.name
        shutil.copy2(path, work)
        self._custom_saved_path = path
        self._custom = JianziLibrary.load(work)
        self._custom_dirty = False
        self._refresh_status()
        self._refresh_custom_list()
        self._fill_missing_components()

    def _fill_missing_components(self) -> None:
        if self._custom is None or not self._libraries:
            return
        filled = fill_missing_components(self._custom.path, self._libraries[0].path)
        if filled:
            self.statusBar().showMessage(f"已从风格库补齐 {filled} 个构件轮廓", 8000)
            self._mark_custom_dirty()

    # ---- 工作副本与保存 ----

    def _custom_display_name(self) -> str:
        """自定义库在界面上的名字（取磁盘保存路径的文件名，与临时副本路径无关）。"""
        return self._custom_saved_path.stem if self._custom_saved_path else "未命名"

    def _mark_custom_dirty(self) -> None:
        """标记自定义库有未保存的更改，并刷新状态栏提示。"""
        self._custom_dirty = True
        self._refresh_status()

    def _discard_work_copy(self) -> None:
        """丢弃工作副本：关闭读取连接并清理临时目录（未保存的更改随之消失）。"""
        if self._custom is not None:
            self._custom.close()
            self._custom = None
        self._custom_saved_path = None
        self._custom_dirty = False
        if self._work_dir is not None:
            self._work_dir.cleanup()
            self._work_dir = None

    def _ask_unsaved_changes(self) -> QMessageBox.StandardButton:
        """Word 式三选：保存 / 不保存 / 取消。独立成方法便于测试替换。"""
        return QMessageBox.question(
            self,
            "未保存的更改",
            f"是否保存对自定义库「{self._custom_display_name()}」的更改？\n"
            "如果不保存，你的更改将会丢失。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    def _confirm_save_custom(self) -> bool:
        """继续之前确认未保存的更改：返回 True 表示可以安全继续（已保存或放弃）。"""
        if self._custom is None or not self._custom_dirty:
            return True
        answer = self._ask_unsaved_changes()
        if answer == QMessageBox.StandardButton.Save:
            return self.save_custom_library() is not None
        return answer == QMessageBox.StandardButton.Discard

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        bar = QHBoxLayout()
        load_style = QPushButton("加载风格库…")
        new_custom = QPushButton("新建自定义库…")
        open_custom = QPushButton("打开自定义库…")
        save_custom = QPushButton("保存")
        save_custom.setToolTip("把自定义库的更改写回磁盘（Ctrl+S）")
        save_custom_as = QPushButton("另存为…")
        save_custom_as.setToolTip("把自定义库副本存到新路径，后续保存写入新路径（Ctrl+Shift+S）")
        browse_components = QPushButton("浏览基本部件…")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编码")
        bar.addWidget(load_style)
        bar.addWidget(new_custom)
        bar.addWidget(open_custom)
        bar.addWidget(save_custom)
        bar.addWidget(save_custom_as)
        bar.addWidget(browse_components)
        bar.addWidget(self._search, 1)
        outer.addLayout(bar)

        middle = QHBoxLayout()

        custom_box = QVBoxLayout()
        custom_box.addWidget(QLabel("自定义库（目录右键可取消加载；条目右键可重命名/删除）"))
        self._custom_tree = QTreeWidget()
        self._custom_tree.setHeaderLabel("自定义库")
        self._custom_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._custom_tree.customContextMenuRequested.connect(self._custom_context_menu)
        self._custom_tree.currentItemChanged.connect(self._on_custom_selection)
        custom_box.addWidget(self._custom_tree, 1)
        middle.addLayout(custom_box, 2)

        self._panel = PreviewPanel(self._pick)
        middle.addWidget(self._panel, 5)

        style_box = QVBoxLayout()
        style_box.addWidget(QLabel("风格库（只读；目录右键可取消加载）"))
        self._style_tree = QTreeWidget()
        self._style_tree.setHeaderLabel("风格库")
        self._style_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._style_tree.customContextMenuRequested.connect(self._style_context_menu)
        self._style_tree.currentItemChanged.connect(self._on_style_selection)
        style_box.addWidget(self._style_tree, 1)
        middle.addLayout(style_box, 2)

        outer.addLayout(middle, 1)
        self.setCentralWidget(central)

        load_style.clicked.connect(self._choose_load_style)
        new_custom.clicked.connect(self._choose_new_custom)
        open_custom.clicked.connect(self._choose_open_custom)
        save_custom.clicked.connect(self._save_custom_clicked)
        save_custom_as.clicked.connect(self._choose_save_custom_as)
        browse_components.clicked.connect(self._open_component_browser)
        self._search.textChanged.connect(self._refresh_list)

        save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        save_shortcut.activated.connect(self._save_custom_clicked)
        save_as_shortcut = QShortcut(QKeySequence.StandardKey.SaveAs, self)
        save_as_shortcut.activated.connect(self._choose_save_custom_as)
        back_shortcut = QShortcut(QKeySequence.StandardKey.Back, self)       # Alt+←
        back_shortcut.activated.connect(self._history_back)
        forward_shortcut = QShortcut(QKeySequence.StandardKey.Forward, self)  # Alt+→
        forward_shortcut.activated.connect(self._history_forward)
        reset_shortcut = QShortcut(QKeySequence("Ctrl+0"), self)
        reset_shortcut.activated.connect(self._panel.reset_view)
        self._panel.history_menu_requested.connect(self._preview_context_menu)
        self._panel.version_selected.connect(self._on_version_selected)

    # ---- 浏览与对比 ----

    def _refresh_list(self) -> None:
        """刷新风格库目录树：每个库一个可展开目录，条目挂在目录下。

        重建期间屏蔽树信号：clear() 会触发 currentItemChanged(None)，
        否则挑选/搜索刷新时会把中栏对比连带清掉。
        """
        query_key = equivalence_key(self._search.text())
        self._style_tree.blockSignals(True)
        try:
            self._style_tree.clear()
            for library in self._libraries:
                root = QTreeWidgetItem([Path(library.path).stem])  # 目录名：库文件名（无后缀）
                root.setData(0, LIB_ROLE, str(library.path))
                for entry in library.entries():
                    if query_key and query_key not in equivalence_key(entry.input_text):
                        continue
                    child = QTreeWidgetItem([self._entry_label(entry)])
                    child.setData(0, TEXT_ROLE, entry.input_text)
                    child.setData(0, LIB_ROLE, str(library.path))
                    root.addChild(child)
                self._style_tree.addTopLevelItem(root)
                root.setExpanded(bool(query_key))
        finally:
            self._style_tree.blockSignals(False)
        self._refresh_custom_list()

    def _refresh_custom_list(self) -> None:
        """刷新自定义库目录树（目录 + 按 InputText 排序的条目，与搜索框联动）。

        重建期间屏蔽树信号：clear() 会触发 currentItemChanged(None)，
        否则挑选/重命名后的刷新会把中栏对比连带清掉。
        """
        query_key = equivalence_key(self._search.text())
        self._custom_tree.blockSignals(True)
        try:
            self._custom_tree.clear()
            if self._custom is None:
                return
            root = QTreeWidgetItem([self._custom_display_name()])
            root.setData(0, LIB_ROLE, str(self._custom.path))
            for entry in sorted(self._custom.entries(), key=lambda e: e.input_text):
                if query_key and query_key not in equivalence_key(entry.input_text):
                    continue
                child = QTreeWidgetItem([self._entry_label(entry)])
                child.setData(0, TEXT_ROLE, entry.input_text)
                child.setData(0, LIB_ROLE, str(self._custom.path))
                root.addChild(child)
            self._custom_tree.addTopLevelItem(root)
            root.setExpanded(True)  # 工作库默认展开，便于查看已入库条目
        finally:
            self._custom_tree.blockSignals(False)

    @staticmethod
    def _entry_label(entry: JianziEntry) -> str:
        """条目文字：中文键名 + 值，明确每个字段的含义。"""
        return f"编码：{entry.input_text}　标题：{entry.title or '（无标题）'}"

    def _on_style_selection(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        """点击风格库条目：中栏显示该条目（默认显示该风格库版本）；点目录则清空。"""
        text = current.data(0, TEXT_ROLE) if current is not None else None
        library = self._library_by_path(current.data(0, LIB_ROLE)) if text else None
        self._show_entry(text, library)

    def _on_custom_selection(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        """点击自定义库条目：中栏显示该条目（默认显示自定义库版本）；点目录则清空。"""
        text = current.data(0, TEXT_ROLE) if current is not None else None
        self._show_entry(text, None)

    def _library_by_path(self, path: str | None) -> JianziLibrary | None:
        for library in self._libraries:
            if str(library.path) == path:
                return library
        return None

    def _show_entry(
        self, text: str | None, source_library: JianziLibrary | None, *, record: bool = True
    ) -> None:
        """中栏显示某编码：版本列表 = 自定义库（若命中）+ 各风格库命中（等价匹配）；
        当前版本取点击来源（source_library=None 表示来自自定义库）。
        record=False 用于历史回退与内部刷新（不产生新历史）。"""
        self._current_text = text
        if not text:
            self._panel.clear()
            return
        versions: list[VersionItem] = []
        current = 0
        if self._custom is not None:
            matches = self._custom.find(text)
            if matches:
                versions.append(VersionItem(matches[0], self._custom, True))
        for library in self._libraries:
            matches = library.find(text)
            if not matches:
                continue
            if library is source_library:
                current = len(versions)
            versions.append(VersionItem(matches[0], library, False))
        if not versions:
            self._panel.clear()
            return
        self._panel.show_versions(versions, current)
        if record:
            shown = self._panel.current_item()
            path = None if shown is None or shown.is_custom else str(shown.library.path)
            self._record_history(text, path)

    # ---- 浏览历史（类浏览器：返回/前进/历史列表） ----

    def _record_history(self, text: str, path: str | None) -> None:
        """入栈一条历史；新导航清空「前进」分支；与当前项重复则不入栈。"""
        del self._history[self._history_index + 1:]
        if (
            self._history_index >= 0
            and self._history[self._history_index] == (text, path)
        ):
            return
        self._history.append((text, path))
        self._history_index = len(self._history) - 1

    def _on_version_selected(self, index: int) -> None:
        """索引框切换版本：面板已自行切换，这里把「同条目的另一版本」记入历史。"""
        del index  # 面板已完成切换，只需读当前版本
        item = self._panel.current_item()
        if item is None or self._current_text is None:
            return
        path = None if item.is_custom else str(item.library.path)
        self._record_history(self._current_text, path)

    def _history_back(self) -> None:
        if self._history_index > 0:
            self._history_index -= 1
            self._restore_history()

    def _history_forward(self) -> None:
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            self._restore_history()

    def _restore_history(self) -> None:
        text, path = self._history[self._history_index]
        self._show_entry(text, self._library_by_path(path), record=False)

    def _build_history_menu(self) -> QMenu:
        """预览区右键菜单：返回 / 前进 / 分隔线 / 历史列表（最新在前，action data=下标）。"""
        menu = QMenu(self)
        back = menu.addAction("返回")
        back.setEnabled(self._history_index > 0)
        forward = menu.addAction("前进")
        forward.setEnabled(self._history_index + 1 < len(self._history))
        menu.addSeparator()
        for index in range(len(self._history) - 1, -1, -1):
            text, path = self._history[index]
            name = "自定义库" if path is None else Path(path).stem
            action = menu.addAction(f"{name}：{text}")
            action.setData(index)
        return menu

    def _preview_context_menu(self, global_pos: QPoint) -> None:
        menu = self._build_history_menu()
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        actions = menu.actions()
        if chosen is actions[0]:
            self._history_back()
        elif chosen is actions[1]:
            self._history_forward()
        elif chosen.data() is not None:
            self._history_index = chosen.data()
            self._restore_history()

    def _pick(
        self, entry: JianziEntry, library: JianziLibrary, target_text: str, target_title: str
    ) -> None:
        if self._custom is None:
            QMessageBox.warning(self, "挑选", "请先打开或保存一个自定义库。")
            return
        result = pick_entry(
            self._custom.path,
            library.path,
            entry.input_text,
            target_text or None,
            target_title or None,
        )
        if result.status == "inserted":
            self.statusBar().showMessage(f"已挑入：编码「{result.normalized_text}」", 5000)
            self._mark_custom_dirty()
        else:
            existing = result.existing
            existing_title = existing.title if existing is not None else ""
            QMessageBox.information(
                self,
                "已存在",
                f"已存在编码「{result.normalized_text}」（标题：{existing_title}）。"
                "\n可在左侧自定义库中右键重命名，或换个编码。",
            )
        self._refresh_custom_list()
        self._refresh_status()
        if (
            result.status == "inserted"
            and self._current_text is not None
            and equivalence_key(self._current_text) == equivalence_key(result.normalized_text)
        ):
            # 索引框立即补入新的自定义库版本；当前显示版本保持不变
            current_item = self._panel.current_item()
            preferred = (
                current_item.library
                if current_item is not None and not current_item.is_custom
                else None
            )
            self._show_entry(self._current_text, preferred)

    # ---- 库加载 / 取消加载 ----

    def _unload_style_library(self, path: str) -> None:
        for index, library in enumerate(self._libraries):
            if str(library.path) == path:
                library.close()
                del self._libraries[index]
                break
        self._refresh_list()
        if self._current_text is not None:
            self._show_entry(self._current_text, None)  # 重建版本列表（剔除被卸载的库）
        self._refresh_status()

    def _unload_custom_library(self) -> None:
        if not self._confirm_save_custom():
            return
        self._current_text = None
        self._panel.clear()
        self._discard_work_copy()
        self._refresh_custom_list()
        self._refresh_status()

    # ---- 右键菜单 ----

    def _custom_context_menu(self, pos: QPoint) -> None:
        item = self._custom_tree.itemAt(pos)
        if item is None or self._custom is None:
            return
        text = item.data(0, TEXT_ROLE)
        menu = QMenu(self)
        if text is None:  # 点中目录：取消加载
            unload_action = menu.addAction(f"取消加载「{self._custom_display_name()}」")
            if menu.exec(self._custom_tree.mapToGlobal(pos)) == unload_action:
                self._unload_custom_library()
            return
        rename_action = menu.addAction("重命名…")
        delete_action = menu.addAction("删除")
        chosen = menu.exec(self._custom_tree.mapToGlobal(pos))
        if chosen == rename_action:
            self._rename_custom_entry(text)
        elif chosen == delete_action:
            self._delete_custom_entry(text)

    def _style_context_menu(self, pos: QPoint) -> None:
        item = self._style_tree.itemAt(pos)
        if item is None or item.data(0, TEXT_ROLE) is not None:
            return  # 仅目录可操作（风格库条目只读）
        path = item.data(0, LIB_ROLE)
        menu = QMenu(self)
        unload_action = menu.addAction(f"取消加载「{Path(path).stem}」")
        if menu.exec(self._style_tree.mapToGlobal(pos)) == unload_action:
            self._unload_style_library(path)

    def _rename_custom_entry(self, text: str) -> None:
        if self._custom is None:
            return
        new_text, ok = QInputDialog.getText(self, "重命名", "新编码（保存时转简体）：", text=text)
        if not ok or not new_text.strip():
            return
        result = rename_entry(self._custom.path, text, new_text.strip())
        if result.status == "conflict":
            QMessageBox.warning(self, "重命名", f"已存在「{result.normalized_text}」，未改动。")
        else:
            self.statusBar().showMessage(f"已重命名为「{result.normalized_text}」", 5000)
            if result.normalized_text != text:
                self._mark_custom_dirty()
        self._refresh_custom_list()

    def _delete_custom_entry(self, text: str) -> None:
        if self._custom is None:
            return
        answer = QMessageBox.question(self, "删除", f"确定从自定义库删除「{text}」？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if delete_entry(self._custom.path, text):
            self.statusBar().showMessage(f"已删除「{text}」", 5000)
            self._mark_custom_dirty()
        self._refresh_custom_list()

    # ---- 文件操作 ----

    def _choose_load_style(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载风格库", "", DB_FILTER)
        if path:
            self.load_style_library(Path(path))

    def new_custom_library(self, path: Path) -> None:
        """新建空白自定义库并作为当前工作库（有未保存更改时先询问）。

        只在临时目录建工作副本；磁盘上的真实文件要等第一次「保存」才出现。
        """
        if not self._confirm_save_custom():
            return
        path = Path(path)
        self._current_text = None
        self._panel.clear()
        self._discard_work_copy()
        self._work_dir = tempfile.TemporaryDirectory(prefix="jianzipu-")
        work = Path(self._work_dir.name) / path.name
        create_custom_db(work)
        self._custom_saved_path = path
        self._custom = JianziLibrary.load(work)
        self._custom_dirty = False
        self._refresh_status()
        self._refresh_custom_list()

    def save_custom_library(self) -> str | None:
        """保存：把工作副本刷回磁盘原路径；未加载自定义库时返回 None。"""
        if self._custom is None or self._custom_saved_path is None:
            return None
        save_custom_db_as(self._custom.path, self._custom_saved_path)
        self._custom_dirty = False
        self._refresh_status()
        return f"已保存 {self._custom_saved_path}"

    def save_custom_as(self, dest: Path) -> str:
        """另存为：已打开自定义库则把工作副本存到新路径并改挂（更改并入新路径，
        原路径保持不动）；未打开时从第一个风格库创建（批量转简体）并打开。"""
        dest = Path(dest)
        if dest.exists():
            dest.unlink()
        if self._custom is not None:
            save_custom_db_as(self._custom.path, dest)
            self._custom_saved_path = dest
            self._custom_dirty = False
            self._refresh_custom_list()  # 目录名随新文件名更新
            self._refresh_status()
            return f"已另存为 {dest}"
        style = self._libraries[0]
        result = create_custom_db(dest, source=style.path)
        self.load_custom_library(dest)
        return (
            f"已从「{style.author}」另存为自定义库"
            f"（{result.copied} 个减字，{result.converted} 条转简体）"
        )

    def _save_custom_clicked(self) -> None:
        """保存（Ctrl+S）：把自定义库的更改写回磁盘原路径。"""
        message = self.save_custom_library()
        if message is None:
            QMessageBox.information(self, "保存", "请先打开或新建一个自定义库。")
        else:
            self.statusBar().showMessage(message, 8000)

    def _choose_save_custom_as(self) -> None:
        """另存为（Ctrl+Shift+S）：未打开自定义库但已加载风格库时，从该风格库创建。"""
        if self._custom is None and not self._libraries:
            QMessageBox.information(
                self, "另存为", "请先打开一个自定义库，或加载一个风格库以便从其另存。"
            )
            return
        suggested = (
            f"{self._custom_display_name()}.sqlite"
            if self._custom is not None
            else "自定义库.sqlite"
        )
        path, _ = QFileDialog.getSaveFileName(self, "另存为", suggested, DB_FILTER)
        if path:
            self.statusBar().showMessage(self.save_custom_as(Path(path)), 8000)

    def _choose_new_custom(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "新建空白自定义库", "自定义库.sqlite", DB_FILTER
        )
        if path:
            self.new_custom_library(Path(path))

    def _choose_open_custom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开自定义库", "", DB_FILTER)
        if path:
            self.load_custom_library(Path(path))

    def _open_component_browser(self) -> None:
        """打开基本部件浏览器（构件数据随风格库提供，需先加载风格库）。"""
        if not self._libraries:
            QMessageBox.information(self, "基本部件", "请先加载一个风格库：构件数据随风格库提供。")
            return
        browser = ComponentBrowserWindow(self)
        browser.load_style_library(self._libraries[0].path)
        if self._custom is not None:
            browser.load_custom_library(self._custom.path)  # 与主窗口共用工作副本
            browser.on_custom_changed = self._mark_custom_dirty
            browser._open_custom_button.setVisible(False)  # 避免绕过工作副本直写磁盘
        browser.show()
        self._component_browser = browser  # 持有引用，避免窗口被回收

    # ---- 状态与收尾 ----

    def _refresh_status(self) -> None:
        if self._custom is None:
            custom = "未加载"
        else:
            custom = f"{self._custom_display_name()}（{len(self._custom.entries())} 个减字）"
            if self._custom_dirty:
                custom += "（有未保存的更改）"
        self.statusBar().showMessage(
            f"已加载风格库：{len(self._libraries)} 个；当前自定义库：{custom}"
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名约定
        if not self._confirm_save_custom():
            event.ignore()
            return
        if self._component_browser is not None:
            self._component_browser.close()
        for library in self._libraries:
            library.close()
        self._current_text = None
        self._panel.clear()  # 先摘面板对库连接的引用，再丢弃工作副本
        self._discard_work_copy()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
"""基本部件浏览器：浏览全部构件，并把选中的构件写为单构件减字条目。

数据来自已加载的风格库（每个风格库都含全量构件）；入库时构件轮廓一并
写入自定义库，编码默认 〇 + 构件名（可在下方编辑框修改）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from jianzipu.core.library import (
    ComponentEntry,
    JianziLibrary,
    PickResult,
    create_component_entry,
    default_component_code,
    fit_component_placement,
)
from jianzipu.core.scripts import equivalence_key
from jianzipu.core.synthesis import place_components
from jianzipu.gui.library_io import choose_open_db, fill_missing_from_style
from jianzipu.gui.preview import TILE_SIZE, JianziPreview, clear_layout


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

    def __init__(self, parent: QWidget | None = None, *, allow_opening_custom: bool = True) -> None:
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
        # 托管模式下隐藏「打开自定义库」：避免绕过主窗口工作副本直写磁盘
        self._open_custom_button.setVisible(allow_opening_custom)
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
        styles = [self._style] if self._style is not None else []
        filled = fill_missing_from_style(self._custom, styles)
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
        clear_layout(self._grid)
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
        path = choose_open_db(self, "加载风格库（提供构件）")
        if path:
            self.load_style_library(path)

    def _choose_open_custom(self) -> None:
        path = choose_open_db(self, "打开自定义库")
        if path:
            self.load_custom_library(path)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名约定
        if self._style is not None:
            self._style.close()
        if self._custom is not None:
            self._custom.close()
        super().closeEvent(event)

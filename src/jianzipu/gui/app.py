"""减字浏览器：多库加载、跨作者版本对比、挑选写入自定义库。

预览直接调用 core.synthesis 合成轮廓（所见即所得 = 编译结果）；
检索、判重、简体转换复用 core.library / core.scripts，不在 GUI 重复实现。
部件浏览器另见 ComponentBrowserWindow：预览 258 个基本构件并把构件写为单构件减字。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
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
from jianzipu.core.synthesis import place_components

GRID = 350.0
PREVIEW_SIZE = 140
TILE_SIZE = 88
DB_FILTER = "SQLite 数据库 (*.sqlite *.jdb);;所有文件 (*)"
TEXT_ROLE = Qt.ItemDataRole.UserRole       # 树节点上挂的条目编码（InputText）
LIB_ROLE = Qt.ItemDataRole.UserRole + 1    # 树节点上挂的所属库路径


def to_painter_path(paths: list, size: int) -> QPainterPath:
    """svgpathtools 的 Path 列表 → QPainterPath（350 网格等比映射到 size×size）。

    用非零环绕填充（而非 Qt 默认的 even-odd）：源数据中部分构件（三/七/九/十）
    的闭合轮廓被原样走两遍，even-odd 下会互相抵消而渲染成空心；字体按非零环绕
    规则渲染（编译层另有轮廓去重），此处与之一致才能所见即所得。
    """
    scale = size / GRID
    result = QPainterPath()
    result.setFillRule(Qt.FillRule.WindingFill)
    for path in paths:
        segments = list(path)
        if not segments:
            continue
        cursor = segments[0].start
        result.moveTo(cursor.real * scale, cursor.imag * scale)
        for seg in segments:
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
        if abs(cursor - segments[0].start) < 1e-6:
            result.closeSubpath()
    return result


class JianziPreview(QWidget):
    """单个减字的预览控件：绘制 core 合成的轮廓，与编译结果一致。"""

    def __init__(self, size: int = PREVIEW_SIZE):
        super().__init__()
        self._paths: list = []
        self._size = size
        self.setFixedSize(size, size)

    def set_paths(self, paths: list) -> None:
        self._paths = paths
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.GlobalColor.black, 0))
        painter.setBrush(Qt.GlobalColor.black)
        painter.drawPath(to_painter_path(self._paths, self._size))


class VersionColumn(QWidget):
    """一个作者版本的对比列：上贴顶为来源信息，中央为减字预览，下贴底为入库编码/标题/挑选。"""

    def __init__(
        self,
        entry: JianziEntry,
        library: JianziLibrary,
        on_pick: Callable[[JianziEntry, JianziLibrary, str, str], None],
    ):
        super().__init__()
        layout = QVBoxLayout(self)
        preview = JianziPreview()
        preview.set_paths(
            place_components(library.placements(entry.input_text), library.components())
        )
        code_info = QLabel(f"编码：{entry.input_text}")   # 上行：来源编码
        author_info = QLabel(f"作者：{library.author}")   # 下行：作者
        for label in (code_info, author_info):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        code = QLineEdit(entry.input_text)
        code.setFixedWidth(PREVIEW_SIZE)
        code.setToolTip("写入自定义库的编码（InputText），保存时转简体")
        title = QLineEdit(entry.title)
        title.setFixedWidth(PREVIEW_SIZE)
        title.setToolTip("写入自定义库的标题（Title），默认为风格库中的值")
        button = QPushButton("挑选")
        button.clicked.connect(
            lambda: on_pick(entry, library, code.text().strip(), title.text().strip())
        )

        top = QWidget()
        top_box = QVBoxLayout(top)
        top_box.setContentsMargins(0, 0, 0, 0)
        top_box.addWidget(code_info)
        top_box.addWidget(author_info)

        bottom = QWidget()
        bottom_box = QVBoxLayout(bottom)
        bottom_box.setContentsMargins(0, 0, 0, 0)
        bottom_box.addWidget(self._row("编码：", code), alignment=Qt.AlignmentFlag.AlignCenter)
        bottom_box.addWidget(self._row("标题：", title), alignment=Qt.AlignmentFlag.AlignCenter)
        bottom_box.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

        # 上下两块等高 → 两侧弹簧相等时预览恰好落在列的正中心
        block_height = max(top.sizeHint().height(), bottom.sizeHint().height())
        top.setFixedHeight(block_height)
        bottom.setFixedHeight(block_height)

        layout.addWidget(top)                                        # 贴顶
        layout.addStretch(1)
        layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)  # 居中
        layout.addStretch(1)
        layout.addWidget(bottom)                                     # 贴底

    @staticmethod
    def _row(label: str, field: QLineEdit) -> QWidget:
        """「标签 + 输入框」一行（宽度贴合内容，便于随列居中）。"""
        row = QWidget()
        inner = QHBoxLayout(row)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(QLabel(label))
        inner.addWidget(field)
        return row


class ComponentTile(QWidget):
    """部件网格中的一格：构件缩略图 + 名称，点击即选中。"""

    clicked = Signal(int)

    def __init__(self, component: ComponentEntry, index: int):
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

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        self.clicked.emit(self.index)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet("background-color: #cfe4ff;" if selected else "")


class ComponentBrowserWindow(QMainWindow):
    """基本部件浏览器：浏览全部构件，并把选中的构件写为单构件减字条目。

    数据来自已加载的风格库（每个风格库都含全量构件）；入库时构件轮廓一并
    写入自定义库，编码默认 〇 + 构件名（可在下方编辑框修改）。
    """

    COLUMNS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("基本部件")
        self.resize(920, 720)
        self._style: JianziLibrary | None = None
        self._custom: JianziLibrary | None = None
        self._components: list[ComponentEntry] = []
        self._tiles: list[ComponentTile] = []
        self._selected: int | None = None
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
        else:
            self.statusBar().showMessage(f"「{result.normalized_text}」已存在，未改动", 5000)
        return result

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        bar = QHBoxLayout()
        load_style = QPushButton("加载风格库…")
        open_custom = QPushButton("打开自定义库…")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索构件名称或编号")
        bar.addWidget(load_style)
        bar.addWidget(open_custom)
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
        open_custom.clicked.connect(self._choose_open_custom)
        add.clicked.connect(self.add_selected)
        self._search.textChanged.connect(self._refresh_grid)

    def _refresh_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._tiles = []
        query = self._search.text().strip()
        row = col = 0
        for index, component in enumerate(self._components):
            if query and query not in f"{component.stroke_id} {component.title}":
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

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        if self._style is not None:
            self._style.close()
        if self._custom is not None:
            self._custom.close()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    """减字浏览器主窗口：左侧浏览列表，右侧跨作者版本对比。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("减字浏览器")
        self.resize(1000, 680)
        self._libraries: list[JianziLibrary] = []
        self._custom: JianziLibrary | None = None
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
        """打开一个自定义库作为挑选目标；若已有风格库，顺带补齐缺失的构件轮廓。"""
        path = Path(path)
        if self._custom is not None:
            self._custom.close()
        self._custom = JianziLibrary.load(path)
        self._refresh_status()
        self._refresh_custom_list()
        self._fill_missing_components()

    def _fill_missing_components(self) -> None:
        if self._custom is None or not self._libraries:
            return
        filled = fill_missing_components(self._custom.path, self._libraries[0].path)
        if filled:
            self.statusBar().showMessage(f"已从风格库补齐 {filled} 个构件轮廓", 8000)

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        bar = QHBoxLayout()
        load_style = QPushButton("加载风格库…")
        new_custom = QPushButton("新建自定义库…")
        save_custom = QPushButton("保存自定义库…")
        open_custom = QPushButton("打开自定义库…")
        browse_components = QPushButton("浏览基本部件…")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编码")
        bar.addWidget(load_style)
        bar.addWidget(new_custom)
        bar.addWidget(save_custom)
        bar.addWidget(open_custom)
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
        custom_box.addWidget(self._custom_tree, 1)
        middle.addLayout(custom_box, 2)

        self._compare_host = QWidget()
        self._compare_layout = QHBoxLayout(self._compare_host)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self._compare_host)
        middle.addWidget(area, 5)

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
        save_custom.clicked.connect(self._choose_save_custom)
        open_custom.clicked.connect(self._choose_open_custom)
        browse_components.clicked.connect(self._open_component_browser)
        self._search.textChanged.connect(self._refresh_list)

    # ---- 浏览与对比 ----

    def _refresh_list(self) -> None:
        """刷新风格库目录树：每个库一个可展开目录，条目挂在目录下。"""
        query_key = equivalence_key(self._search.text())
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
        self._refresh_custom_list()

    def _refresh_custom_list(self) -> None:
        """刷新自定义库目录树（目录 + 按 InputText 排序的条目，与搜索框联动）。"""
        query_key = equivalence_key(self._search.text())
        self._custom_tree.clear()
        if self._custom is None:
            return
        root = QTreeWidgetItem([Path(self._custom.path).stem])
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

    @staticmethod
    def _entry_label(entry: JianziEntry) -> str:
        """条目文字：中文键名 + 值，明确每个字段的含义。"""
        return f"编码：{entry.input_text}　标题：{entry.title or '（无标题）'}"

    def _on_style_selection(self, current, _previous) -> None:
        text = current.data(0, TEXT_ROLE) if current is not None else None
        if text:
            self._show_comparison(text)
        else:
            self._clear_comparison()

    def _clear_comparison(self) -> None:
        while self._compare_layout.count():
            item = self._compare_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_comparison(self, input_text: str) -> None:
        self._clear_comparison()
        self._compare_layout.addStretch(1)  # 左右弹簧：列组水平居中
        for library in self._libraries:
            matches = library.find(input_text)
            if not matches:
                continue
            try:
                column = VersionColumn(matches[0], library, self._pick)
            except KeyError as exc:
                # 库缺构件轮廓（历史数据或手工 SQL）：降级提示，不影响其余对比
                column = QLabel(f"作者：{library.author}　缺少构件数据 {exc}，无法预览")
                column.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._compare_layout.addWidget(column)
        self._compare_layout.addStretch(1)

    def _column_count(self) -> int:
        """对比区当前列数（不含居中用的弹簧）。"""
        return sum(
            1
            for i in range(self._compare_layout.count())
            if self._compare_layout.itemAt(i).widget() is not None
        )

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
        else:
            existing = result.existing
            QMessageBox.information(
                self,
                "已存在",
                f"已存在编码「{result.normalized_text}」（标题：{existing.title}）。"
                "\n可在左侧自定义库中右键重命名，或换个编码。",
            )
        self._refresh_custom_list()
        self._refresh_status()

    # ---- 库加载 / 取消加载 ----

    def _unload_style_library(self, path: str) -> None:
        for index, library in enumerate(self._libraries):
            if str(library.path) == path:
                library.close()
                del self._libraries[index]
                break
        self._refresh_list()
        self._clear_comparison()
        self._refresh_status()

    def _unload_custom_library(self) -> None:
        if self._custom is not None:
            self._custom.close()
            self._custom = None
        self._refresh_custom_list()
        self._refresh_status()

    # ---- 右键菜单 ----

    def _custom_context_menu(self, pos) -> None:
        item = self._custom_tree.itemAt(pos)
        if item is None or self._custom is None:
            return
        text = item.data(0, TEXT_ROLE)
        menu = QMenu(self)
        if text is None:  # 点中目录：取消加载
            unload_action = menu.addAction(f"取消加载「{Path(self._custom.path).stem}」")
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

    def _style_context_menu(self, pos) -> None:
        item = self._style_tree.itemAt(pos)
        if item is None or item.data(0, TEXT_ROLE) is not None:
            return  # 仅目录可操作（风格库条目只读）
        path = item.data(0, LIB_ROLE)
        menu = QMenu(self)
        unload_action = menu.addAction(f"取消加载「{Path(path).stem}」")
        if menu.exec(self._style_tree.mapToGlobal(pos)) == unload_action:
            self._unload_style_library(path)

    def _rename_custom_entry(self, text: str) -> None:
        new_text, ok = QInputDialog.getText(self, "重命名", "新编码（保存时转简体）：", text=text)
        if not ok or not new_text.strip():
            return
        result = rename_entry(self._custom.path, text, new_text.strip())
        if result.status == "conflict":
            QMessageBox.warning(self, "重命名", f"已存在「{result.normalized_text}」，未改动。")
        else:
            self.statusBar().showMessage(f"已重命名为「{result.normalized_text}」", 5000)
        self._refresh_custom_list()

    def _delete_custom_entry(self, text: str) -> None:
        answer = QMessageBox.question(self, "删除", f"确定从自定义库删除「{text}」？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if delete_entry(self._custom.path, text):
            self.statusBar().showMessage(f"已删除「{text}」", 5000)
        self._refresh_custom_list()

    # ---- 文件操作 ----

    def _choose_load_style(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载风格库", "", DB_FILTER)
        if path:
            self.load_style_library(Path(path))

    def new_custom_library(self, path: Path) -> None:
        """新建空白自定义库并作为当前工作库（已存在的文件按对话框确认覆盖）。"""
        path = Path(path)
        if path.exists():
            path.unlink()
        create_custom_db(path)
        self.load_custom_library(path)

    def save_custom_as(self, dest: Path) -> str:
        """另存为：已打开自定义库则复制副本；否则从第一个风格库创建（批量转简体）。"""
        dest = Path(dest)
        if dest.exists():
            dest.unlink()
        if self._custom is not None:
            save_custom_db_as(self._custom.path, dest)
            return f"已另存为 {dest}"
        style = self._libraries[0]
        result = create_custom_db(dest, source=style.path)
        self.load_custom_library(dest)
        return (
            f"已从「{style.author}」另存为自定义库"
            f"（{result.copied} 个减字，{result.converted} 条转简体）"
        )

    def _choose_save_custom(self) -> None:
        """保存自定义库：已打开则另存副本；未打开但已加载风格库则从该风格库另存。"""
        if self._custom is None and not self._libraries:
            QMessageBox.information(
                self, "保存自定义库", "请先打开一个自定义库，或加载一个风格库以便从其另存。"
            )
            return
        suggested = (
            Path(self._custom.path).name if self._custom is not None else "自定义库.sqlite"
        )
        path, _ = QFileDialog.getSaveFileName(self, "保存自定义库", suggested, DB_FILTER)
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
            browser.load_custom_library(self._custom.path)
        browser.show()
        self._component_browser = browser  # 持有引用，避免窗口被回收

    # ---- 状态与收尾 ----

    def _refresh_status(self) -> None:
        if self._custom is None:
            custom = "未加载"
        else:
            custom = f"{self._custom.path.stem}（{len(self._custom.entries())} 个减字）"
        self.statusBar().showMessage(
            f"已加载风格库：{len(self._libraries)} 个；当前自定义库：{custom}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名约定
        for library in self._libraries:
            library.close()
        if self._custom is not None:
            self._custom.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
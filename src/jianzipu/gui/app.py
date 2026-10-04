"""减字浏览器：多库加载、跨作者版本对比、挑选写入自定义库。

预览直接调用 core.synthesis 合成轮廓（所见即所得 = 编译结果）；
检索、判重、简体转换复用 core.library / core.scripts，不在 GUI 重复实现。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from jianzipu.core.library import JianziEntry, JianziLibrary, create_custom_db, pick_entry
from jianzipu.core.scripts import equivalence_key
from jianzipu.core.synthesis import place_components

GRID = 350.0
PREVIEW_SIZE = 140
DB_FILTER = "SQLite 数据库 (*.sqlite *.jdb);;所有文件 (*)"


def to_painter_path(paths: list, size: int) -> QPainterPath:
    """svgpathtools 的 Path 列表 → QPainterPath（350 网格等比映射到 size×size）。"""
    scale = size / GRID
    result = QPainterPath()
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
    """一个作者版本的对比列：预览 + 信息 + 挑选按钮。"""

    def __init__(
        self,
        entry: JianziEntry,
        library: JianziLibrary,
        on_pick: Callable[[JianziEntry, JianziLibrary], None],
    ):
        super().__init__()
        layout = QVBoxLayout(self)
        header = QLabel(f"{library.author}　{entry.input_text}")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview = JianziPreview()
        preview.set_paths(
            place_components(library.placements(entry.input_text), library.components())
        )
        title = QLabel(entry.title or "（无标题）")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("挑选")
        button.clicked.connect(lambda: on_pick(entry, library))
        layout.addWidget(header)
        layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(button)


class MainWindow(QMainWindow):
    """减字浏览器主窗口：左侧浏览列表，右侧跨作者版本对比。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("减字浏览器")
        self.resize(1000, 680)
        self._libraries: list[JianziLibrary] = []
        self._custom: JianziLibrary | None = None
        self._entries: list[tuple[JianziEntry, JianziLibrary]] = []
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

    def load_custom_library(self, path: Path) -> None:
        """打开一个自定义库作为挑选目标。"""
        path = Path(path)
        if self._custom is not None:
            self._custom.close()
        self._custom = JianziLibrary.load(path)
        self._refresh_status()

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        bar = QHBoxLayout()
        load_style = QPushButton("加载风格库…")
        new_custom = QPushButton("新建自定义库…")
        open_custom = QPushButton("打开自定义库…")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索编码（等价匹配：简繁写法互认）")
        bar.addWidget(load_style)
        bar.addWidget(new_custom)
        bar.addWidget(open_custom)
        bar.addWidget(self._search, 1)
        outer.addLayout(bar)

        middle = QHBoxLayout()
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._show_comparison)
        middle.addWidget(self._list, 2)

        self._compare_host = QWidget()
        self._compare_layout = QHBoxLayout(self._compare_host)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self._compare_host)
        middle.addWidget(area, 5)
        outer.addLayout(middle, 1)

        self.setCentralWidget(central)

        load_style.clicked.connect(self._choose_load_style)
        new_custom.clicked.connect(self._choose_new_custom)
        open_custom.clicked.connect(self._choose_open_custom)
        self._search.textChanged.connect(self._refresh_list)

    # ---- 浏览与对比 ----

    def _refresh_list(self) -> None:
        query_key = equivalence_key(self._search.text())
        self._list.clear()
        self._entries = []
        for library in self._libraries:
            for entry in library.entries():
                if query_key and query_key not in equivalence_key(entry.input_text):
                    continue
                self._entries.append((entry, library))
                self._list.addItem(f"{entry.input_text}　·　{entry.source_author}")

    def _show_comparison(self, row: int) -> None:
        while self._compare_layout.count():
            item = self._compare_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if row < 0 or row >= len(self._entries):
            return
        entry, _ = self._entries[row]
        for library in self._libraries:
            matches = library.find(entry.input_text)
            if matches:
                self._compare_layout.addWidget(VersionColumn(matches[0], library, self._pick))

    def _pick(self, entry: JianziEntry, library: JianziLibrary) -> None:
        if self._custom is None:
            QMessageBox.warning(self, "挑选", "请先新建或打开自定义库。")
            return
        result = pick_entry(self._custom.path, library.path, entry.input_text)
        if result.status == "inserted":
            self.statusBar().showMessage(f"已挑入「{result.normalized_text}」", 5000)
        else:
            existing = result.existing
            QMessageBox.information(
                self,
                "已存在",
                f"已存在「{result.normalized_text}」：{existing.title}"
                f"（{existing.source_author}）。\n如需调整，请在自定义库中编辑/查看该条目。",
            )
        self._refresh_status()

    # ---- 文件操作 ----

    def _choose_load_style(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载风格库", "", DB_FILTER)
        if path:
            self.load_style_library(Path(path))

    def _choose_new_custom(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "新建自定义库", "自定义库.sqlite", DB_FILTER)
        if not path:
            return
        try:
            create_custom_db(Path(path))
        except FileExistsError as exc:
            QMessageBox.warning(self, "新建自定义库", str(exc))
            return
        self.load_custom_library(Path(path))

    def _choose_open_custom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开自定义库", "", DB_FILTER)
        if path:
            self.load_custom_library(Path(path))

    # ---- 状态与收尾 ----

    def _refresh_status(self) -> None:
        if self._custom is None:
            custom = "自定义库：未打开"
        else:
            custom = f"自定义库：{self._custom.path.name}（{len(self._custom.entries())} 个减字）"
        self.statusBar().showMessage(f"已加载风格库 {len(self._libraries)} 个；{custom}")

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
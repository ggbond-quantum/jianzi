"""减字浏览器：多库加载、跨作者版本对比、挑选写入自定义库。

预览直接调用 core.synthesis 合成轮廓（所见即所得 = 编译结果）；
检索、判重、简体转换复用 core.library / core.scripts，不在 GUI 重复实现。
部件浏览器另见 gui.component_browser：预览 258 个基本构件并把构件写为单构件减字。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jianzipu.core.library import (
    JianziEntry,
    JianziLibrary,
    delete_entry,
    pick_entry,
    rename_entry,
)
from jianzipu.core.scripts import equivalence_key
from jianzipu.gui.component_browser import ComponentBrowserWindow
from jianzipu.gui.custom_session import CustomSession
from jianzipu.gui.history import History
from jianzipu.gui.library_io import (
    LIB_ROLE,
    TEXT_ROLE,
    build_library_root,
    choose_open_db,
    choose_save_db,
    fill_missing_from_style,
)
from jianzipu.gui.preview import (
    MAX_ZOOM,
    MIN_ZOOM,
    JianziPreview,
    PreviewPanel,
    VersionItem,
    to_painter_path,
)

__all__ = [
    "LIB_ROLE",
    "MAX_ZOOM",
    "MIN_ZOOM",
    "TEXT_ROLE",
    "ComponentBrowserWindow",
    "JianziPreview",
    "MainWindow",
    "PreviewPanel",
    "VersionItem",
    "main",
    "to_painter_path",
]


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
        # 工作副本会话：动态读取 _ask_unsaved_changes，便于测试替换提示逻辑
        self._session = CustomSession(lambda: self._ask_unsaved_changes())
        self._current_text: str | None = None        # 中栏面板当前显示的编码
        self._history = History()
        self._component_browser: ComponentBrowserWindow | None = None
        self._build_ui()
        self._refresh_status()

    # ---- 会话状态（主窗口接缝：状态由 _session 持有，此处对外暴露） ----

    @property
    def _custom(self) -> JianziLibrary | None:
        """当前自定义库工作副本（无则 None）。"""
        return self._session.library

    @property
    def _custom_dirty(self) -> bool:
        """工作副本是否有未保存的更改。"""
        return self._session.is_dirty

    @_custom_dirty.setter
    def _custom_dirty(self, value: bool) -> None:
        self._session.is_dirty = value

    @property
    def _custom_saved_path(self) -> Path | None:
        """工作副本对应的磁盘保存路径。"""
        return self._session.saved_path

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
        self._current_text = None
        self._panel.clear()  # 先摘面板对旧库连接的引用，再丢弃工作副本
        self._session.open(Path(path))
        self._refresh_status()
        self._refresh_custom_list()
        self._fill_missing_components()

    def _fill_missing_components(self) -> None:
        filled = fill_missing_from_style(self._session.library, self._libraries)
        if filled:
            self.statusBar().showMessage(f"已从风格库补齐 {filled} 个构件轮廓", 8000)
            self._mark_custom_dirty()

    # ---- 工作副本与保存 ----

    def _mark_custom_dirty(self) -> None:
        """标记自定义库有未保存的更改，并刷新状态栏提示。"""
        self._session.is_dirty = True
        self._refresh_status()

    def _ask_unsaved_changes(self) -> QMessageBox.StandardButton:
        """Word 式三选：保存 / 不保存 / 取消。独立成方法便于测试替换。"""
        return QMessageBox.question(
            self,
            "未保存的更改",
            f"是否保存对自定义库「{self._session.display_name()}」的更改？\n"
            "如果不保存，你的更改将会丢失。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    def _confirm_save_custom(self) -> bool:
        """继续之前确认未保存的更改：返回 True 表示可以安全继续（已保存或放弃）。"""
        return self._session.confirm_continue()

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
                root = build_library_root(
                    Path(library.path).stem, library.path, library.entries(), query_key
                )
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
            custom = self._session.library
            if custom is None:
                return
            root = build_library_root(
                self._session.display_name(), custom.path, custom.entries(), query_key,
                sort_key=lambda e: e.input_text,
            )
            self._custom_tree.addTopLevelItem(root)
            root.setExpanded(True)  # 工作库默认展开，便于查看已入库条目
        finally:
            self._custom_tree.blockSignals(False)

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
        custom = self._session.library
        if custom is not None:
            matches = custom.find(text)
            if matches:
                versions.append(VersionItem(matches[0], custom, True))
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
            self._history.record(text, path)

    # ---- 浏览历史（类浏览器：返回/前进/历史列表） ----

    def _on_version_selected(self, index: int) -> None:
        """索引框切换版本：面板已自行切换，这里把「同条目的另一版本」记入历史。"""
        del index  # 面板已完成切换，只需读当前版本
        item = self._panel.current_item()
        if item is None or self._current_text is None:
            return
        path = None if item.is_custom else str(item.library.path)
        self._history.record(self._current_text, path)

    def _history_back(self) -> None:
        text = self._history.back()
        if text is not None:
            self._show_entry(text, self._library_by_path(self._history.path()), record=False)

    def _history_forward(self) -> None:
        text = self._history.forward()
        if text is not None:
            self._show_entry(text, self._library_by_path(self._history.path()), record=False)

    def _build_history_menu(self) -> QMenu:
        """预览区右键菜单：返回 / 前进 / 分隔线 / 历史列表（最新在前，action data=下标）。"""
        menu = QMenu(self)
        back = menu.addAction("返回")
        back.setEnabled(self._history.can_back)
        forward = menu.addAction("前进")
        forward.setEnabled(self._history.can_forward)
        menu.addSeparator()
        for index, text, path in self._history.newest_first():
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
            self._history.jump(chosen.data())
            self._show_entry(
                self._history.text(), self._library_by_path(self._history.path()), record=False
            )

    def _pick(
        self, entry: JianziEntry, library: JianziLibrary, target_text: str, target_title: str
    ) -> None:
        custom = self._session.library
        if custom is None:
            QMessageBox.warning(self, "挑选", "请先打开或保存一个自定义库。")
            return
        result = pick_entry(
            custom.path,
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
        self._session.close()
        self._refresh_custom_list()
        self._refresh_status()

    # ---- 右键菜单 ----

    def _custom_context_menu(self, pos: QPoint) -> None:
        item = self._custom_tree.itemAt(pos)
        if item is None or self._session.library is None:
            return
        text = item.data(0, TEXT_ROLE)
        menu = QMenu(self)
        if text is None:  # 点中目录：取消加载
            unload_action = menu.addAction(f"取消加载「{self._session.display_name()}」")
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
        custom = self._session.library
        if custom is None:
            return
        new_text, ok = QInputDialog.getText(self, "重命名", "新编码（保存时转简体）：", text=text)
        if not ok or not new_text.strip():
            return
        result = rename_entry(custom.path, text, new_text.strip())
        if result.status == "conflict":
            QMessageBox.warning(self, "重命名", f"已存在「{result.normalized_text}」，未改动。")
        else:
            self.statusBar().showMessage(f"已重命名为「{result.normalized_text}」", 5000)
            if result.normalized_text != text:
                self._mark_custom_dirty()
        self._refresh_custom_list()

    def _delete_custom_entry(self, text: str) -> None:
        custom = self._session.library
        if custom is None:
            return
        answer = QMessageBox.question(self, "删除", f"确定从自定义库删除「{text}」？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        if delete_entry(custom.path, text):
            self.statusBar().showMessage(f"已删除「{text}」", 5000)
            self._mark_custom_dirty()
        self._refresh_custom_list()

    # ---- 文件操作 ----

    def _choose_load_style(self) -> None:
        path = choose_open_db(self, "加载风格库")
        if path:
            self.load_style_library(path)

    def new_custom_library(self, path: Path) -> None:
        """新建空白自定义库并作为当前工作库（有未保存更改时先询问）。

        只在临时目录建工作副本；磁盘上的真实文件要等第一次「保存」才出现。
        """
        if not self._confirm_save_custom():
            return
        self._current_text = None
        self._panel.clear()
        self._session.create_blank(Path(path))
        self._refresh_status()
        self._refresh_custom_list()

    def save_custom_library(self) -> str | None:
        """保存：把工作副本刷回磁盘原路径；未加载自定义库时返回 None。"""
        message = self._session.save()
        if message is not None:
            self._refresh_status()
        return message

    def save_custom_as(self, dest: Path) -> str:
        """另存为：已打开自定义库则把工作副本存到新路径并改挂（更改并入新路径，
        原路径保持不动）；未打开时从第一个风格库创建（批量转简体）并打开。"""
        dest = Path(dest)
        if self._session.library is not None:
            message = self._session.save_as(dest)
            self._refresh_custom_list()  # 目录名随新文件名更新
            self._refresh_status()
            return message
        style = self._libraries[0]
        self._current_text = None
        self._panel.clear()
        message = self._session.save_as(dest, source_style=style)
        self._refresh_status()
        self._refresh_custom_list()
        self._fill_missing_components()
        return message

    def _save_custom_clicked(self) -> None:
        """保存（Ctrl+S）：把自定义库的更改写回磁盘原路径。"""
        message = self.save_custom_library()
        if message is None:
            QMessageBox.information(self, "保存", "请先打开或新建一个自定义库。")
        else:
            self.statusBar().showMessage(message, 8000)

    def _choose_save_custom_as(self) -> None:
        """另存为（Ctrl+Shift+S）：未打开自定义库但已加载风格库时，从该风格库创建。"""
        if self._session.library is None and not self._libraries:
            QMessageBox.information(
                self, "另存为", "请先打开一个自定义库，或加载一个风格库以便从其另存。"
            )
            return
        suggested = (
            f"{self._session.display_name()}.sqlite"
            if self._session.library is not None
            else "自定义库.sqlite"
        )
        path = choose_save_db(self, "另存为", suggested)
        if path:
            self.statusBar().showMessage(self.save_custom_as(path), 8000)

    def _choose_new_custom(self) -> None:
        path = choose_save_db(self, "新建空白自定义库", "自定义库.sqlite")
        if path:
            self.new_custom_library(path)

    def _choose_open_custom(self) -> None:
        path = choose_open_db(self, "打开自定义库")
        if path:
            self.load_custom_library(path)

    def _open_component_browser(self) -> None:
        """打开基本部件浏览器（构件数据随风格库提供，需先加载风格库）。"""
        if not self._libraries:
            QMessageBox.information(self, "基本部件", "请先加载一个风格库：构件数据随风格库提供。")
            return
        browser = ComponentBrowserWindow(self, allow_opening_custom=False)
        browser.load_style_library(self._libraries[0].path)
        custom = self._session.library
        if custom is not None:
            browser.load_custom_library(custom.path)  # 与主窗口共用工作副本
            browser.on_custom_changed = self._mark_custom_dirty
        browser.show()
        self._component_browser = browser  # 持有引用，避免窗口被回收

    # ---- 状态与收尾 ----

    def _refresh_status(self) -> None:
        custom = self._session.library
        if custom is None:
            custom_info = "未加载"
        else:
            custom_info = f"{self._session.display_name()}（{len(custom.entries())} 个减字）"
            if self._session.is_dirty:
                custom_info += "（有未保存的更改）"
        self.statusBar().showMessage(
            f"已加载风格库：{len(self._libraries)} 个；当前自定义库：{custom_info}"
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
        self._session.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()

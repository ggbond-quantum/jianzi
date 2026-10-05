"""GUI 冒烟测试：主窗口这一接缝能构造、能加载库并出画面（不测内部实现）。

离屏运行（QT_QPA_PLATFORM=offscreen）；窗口内部的绘制细节不在测试范围内。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QCloseEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox, QPushButton

from jianzipu.core.library import JianziLibrary, create_custom_db, pick_entry
from jianzipu.core.synthesis import Placement, place_components
from jianzipu.extract.build import SCHEMA
from jianzipu.gui.app import (
    MAX_ZOOM,
    MIN_ZOOM,
    TEXT_ROLE,
    ComponentBrowserWindow,
    JianziPreview,
    MainWindow,
    PreviewPanel,
    VersionItem,
    to_painter_path,
)
from test_library import make_fixture_db


def _disk_entries(path) -> list[str]:
    """磁盘上该库文件的条目编码（绕过窗口的工作副本，直接读真实文件）。"""
    lib = JianziLibrary.load(path)
    try:
        return [e.input_text for e in lib.entries()]
    finally:
        lib.close()


def _disk_components(path) -> set[int]:
    """磁盘上该库文件的构件编号集合（直接读真实文件）。"""
    lib = JianziLibrary.load(path)
    try:
        return set(lib.components())
    finally:
        lib.close()


def make_incomplete_db(path, stroke_id: int = 403) -> "os.PathLike":
    """构造缺构件轮廓的库：条目引用了 component 表中不存在的 StrokeID。"""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO jianzi (InputText, Title, SourceAuthor, SourceID) VALUES (?, ?, ?, ?)",
        ("大七", "缺构件", "某作者", 1),
    )
    conn.execute(
        "INSERT INTO node (InputText, Seq, StrokeID, DistX, DistY, DistWidth, DistHeight)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("大七", 1, stroke_id, 0, 0, 100, 100),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _no_modal_unsaved_prompt(monkeypatch):
    """离屏测试不能弹模态框：「未保存更改」默认按「不保存」处理。
    需要验证提示逻辑的测试对实例自行 patch _ask_unsaved_changes 覆盖。"""
    monkeypatch.setattr(
        MainWindow,
        "_ask_unsaved_changes",
        lambda self: QMessageBox.StandardButton.Discard,
    )


def test_main_window_smoke(tmp_path, qapp):
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        window.show()
        qapp.processEvents()

        assert window.isVisible()
        assert not window.grab().isNull()  # 强制渲染一帧，预览绘制不崩溃
    finally:
        window.close()


def test_component_browser_smoke(tmp_path, qapp):
    """部件浏览器：加载风格库的构件、渲染网格、可数出全部构件。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = ComponentBrowserWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        window.show()
        qapp.processEvents()

        assert window.isVisible()
        assert window.component_count() == 2  # fixture 含甲、乙两个构件
        assert not window.grab().isNull()
    finally:
        window.close()


def test_component_browser_add_selected_writes_entry(tmp_path, qapp):
    """选中构件入库：写入单构件减字条目（默认编码〇+构件名）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = ComponentBrowserWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        window.select_component(0)
        result = window.add_selected()
        assert result is not None and result.status == "inserted"
        assert result.normalized_text == "〇甲"
    finally:
        window.close()

    lib = JianziLibrary.load(custom)
    try:
        assert [e.input_text for e in lib.entries()] == ["〇甲"]
        assert lib.placements("〇甲")[0].stroke_id == 1
    finally:
        lib.close()


def test_component_browser_reports_conflict(tmp_path, qapp):
    """重复入库：返回冲突而非覆盖（与主窗口一致）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = ComponentBrowserWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        window.select_component(0)
        window.add_selected()
        window.select_component(0)
        result = window.add_selected()
        assert result.status == "conflict"
        assert result.existing.input_text == "〇甲"
    finally:
        window.close()


def _make_component_title_db(path, titles: list[str]) -> "os.PathLike":
    """构造只含构件的库（检索测试用；轮廓统一取最小三角形）。"""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO component (StrokeID, Title, PathData, OrgWidth, OrgHeight)"
        " VALUES (?, ?, ?, ?, ?)",
        [(i, t, "M 0 0 L 100 0 L 100 100 Z", 100, 100) for i, t in enumerate(titles, 1)],
    )
    conn.commit()
    conn.close()
    return path


def test_component_browser_search_matches_script_variants(tmp_path, qapp):
    """部件检索简繁等价：简体「轮」能命中繁体标题「輪」，编号检索保持可用。"""
    style = _make_component_title_db(tmp_path / "components.sqlite", ["輪", "跪指", "勾"])
    window = ComponentBrowserWindow()
    try:
        window.load_style_library(style)
        assert window.component_count() == 3

        window._search.setText("轮")  # 简体查询 → 命中繁体标题
        qapp.processEvents()
        assert [tile.component.title for tile in window._tiles] == ["輪"]

        window._search.setText("輪")  # 繁体查询同样命中
        qapp.processEvents()
        assert [tile.component.title for tile in window._tiles] == ["輪"]

        window._search.setText("1")   # 编号检索
        qapp.processEvents()
        assert [tile.component.title for tile in window._tiles] == ["輪"]

        window._search.setText("不存在")
        qapp.processEvents()
        assert window._tiles == []
    finally:
        window.close()


def test_to_painter_path_fills_double_traced_contours(qapp):
    """同向重复走线（实测：三/七/九/十 的轮廓被原样走两遍）必须填充，不得空心。

    源数据中这些轮廓前后半段完全相同，在 even-odd 填充规则下会互相抵消而空心；
    字体本身按非零环绕规则渲染（编译层另有去重），因此预览须与之一致。
    """
    double_traced = "M 0 0 L 60 0 L 30 60 Z M 0 0 L 60 0 L 30 60 Z"
    paths = place_components([Placement(1, 0, 0, 100, 100)], {1: double_traced})
    qpath = to_painter_path(paths, 350)

    assert qpath.fillRule() == Qt.FillRule.WindingFill
    assert qpath.contains(QPointF(30, 30))  # 三角形内部


def test_to_painter_path_fills_mirrored_duplicate_contours(qapp):
    """正反两份重复图形（如「聲」）必须实心，孔洞保持空心。"""
    doubled = (
        "M 0 0 L 100 0 L 100 100 L 0 100 Z M 20 20 L 20 80 L 80 80 L 80 20 Z"
        " M 0 0 L 0 100 L 100 100 L 100 0 Z M 20 20 L 80 20 L 80 80 L 20 80 Z"
    )
    paths = place_components([Placement(1, 0, 0, 350, 350)], {1: doubled})
    qpath = to_painter_path(paths, 350)

    assert qpath.contains(QPointF(35, 175))        # 笔画带内 → 填充
    assert not qpath.contains(QPointF(175, 175))   # 孔洞 → 空心


def test_to_painter_path_fills_overlap_of_opposite_parts(qapp):
    """两个方向相反的部件部分重叠：重叠区必须填充（修复前渲染成白色空洞）。"""
    components = {
        1: "M 0 0 L 100 0 L 100 100 L 0 100 Z",     # 部件1：正向矩形
        2: "M 50 0 L 50 100 L 150 100 L 150 0 Z",   # 部件2：反向矩形，与部件1 重叠
    }
    paths = place_components(
        [Placement(1, 0, 0, 100, 100), Placement(2, 50, 0, 100, 100)], components
    )
    qpath = to_painter_path(paths, 350)

    assert qpath.contains(QPointF(25, 50))     # 部件1 独占区
    assert qpath.contains(QPointF(125, 50))    # 部件2 独占区
    assert qpath.contains(QPointF(75, 50))     # 重叠区：不得抵消成空洞


def test_to_painter_path_hole_stays_hollow_among_parts(qapp):
    """整体反向且带孔洞的部件：孔洞位置保持空心，其余重叠区填充。"""
    components = {
        1: "M 0 0 L 100 0 L 100 100 L 0 100 Z",                        # 正向矩形
        2: "M 50 0 L 50 100 L 150 100 L 150 0 Z"                       # 反向外框
           " M 105 20 L 140 20 L 140 60 L 105 60 Z",                   # 孔洞（正向，部件1 之外）
    }
    paths = place_components(
        [Placement(1, 0, 0, 100, 100), Placement(2, 50, 0, 100, 100)], components
    )
    qpath = to_painter_path(paths, 350)

    assert qpath.contains(QPointF(75, 50))          # 部件1、2 重叠区 → 填充
    assert qpath.contains(QPointF(130, 80))         # 部件2 实心区 → 填充
    assert not qpath.contains(QPointF(120, 40))     # 部件2 孔洞 → 空心


def _wheel_event(delta_y: int, ctrl: bool = True, pos: QPointF | None = None) -> QWheelEvent:
    """构造一次滚轮事件：delta_y=±120 表示一格；ctrl 决定是否按住 Ctrl。"""
    modifiers = (
        Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    )
    point = pos if pos is not None else QPointF(10, 10)
    return QWheelEvent(
        point,
        point,
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_preview_ctrl_wheel_zooms(qapp):
    """Ctrl+滚轮以 1.25 倍/格缩放预览；不带 Ctrl 不缩放（交还父级滚动）；有上下限。"""
    preview = JianziPreview()
    assert preview._zoom == 1.0

    preview.wheelEvent(_wheel_event(120))
    assert preview._zoom == pytest.approx(1.25)
    preview.wheelEvent(_wheel_event(-120))
    assert preview._zoom == pytest.approx(1.0)

    preview.wheelEvent(_wheel_event(120, ctrl=False))
    assert preview._zoom == pytest.approx(1.0)  # 普通滚动不拦截

    for _ in range(40):
        preview.wheelEvent(_wheel_event(120))
    assert preview._zoom == MAX_ZOOM
    for _ in range(80):
        preview.wheelEvent(_wheel_event(-120))
    assert preview._zoom == MIN_ZOOM


def _mouse_event(
    event_type: QMouseEvent.Type,
    pos: QPointF,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier
    )


def test_interactive_preview_expands_with_widget(qapp):
    """interactive 预览（中栏大预览）随控件扩展填充，不再是固定小方块。"""
    preview = JianziPreview(interactive=True)
    preview.resize(600, 400)
    preview.show()
    qapp.processEvents()
    assert preview.width() == 600 and preview.height() == 400
    assert not preview.grab().isNull()


def test_interactive_preview_zooms_around_cursor(qapp):
    """Ctrl+滚轮以鼠标位置为锚：缩放前后光标下的内容点保持不动。"""
    preview = JianziPreview(interactive=True)
    preview.resize(400, 400)
    cursor = QPointF(120, 90)
    before = preview.content_point_at(cursor)
    preview.wheelEvent(_wheel_event(120, pos=cursor))
    assert preview._zoom > 1.0
    after = preview.content_point_at(cursor)
    assert after.x() == pytest.approx(before.x())
    assert after.y() == pytest.approx(before.y())


def test_interactive_preview_middle_drag_pans(qapp):
    """中键（滚轮键）按住拖动平移：内容跟手；左键拖动不平移。"""
    preview = JianziPreview(interactive=True)
    preview.resize(400, 400)

    # 左键拖动：不动
    preview.mousePressEvent(
        _mouse_event(QMouseEvent.Type.MouseButtonPress, QPointF(200, 200),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    )
    preview.mouseMoveEvent(
        _mouse_event(QMouseEvent.Type.MouseMove, QPointF(300, 260),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    )
    assert preview._pan == QPointF(0, 0)

    # 中键拖动：内容跟手（拖动终点下的内容点 = 拖动起点下的内容点）
    start = QPointF(200, 200)
    before = preview.content_point_at(start)
    preview.mousePressEvent(
        _mouse_event(QMouseEvent.Type.MouseButtonPress, start,
                     Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
    )
    end = QPointF(260, 230)
    preview.mouseMoveEvent(
        _mouse_event(QMouseEvent.Type.MouseMove, end,
                     Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton)
    )
    after = preview.content_point_at(end)
    assert after.x() == pytest.approx(before.x())
    assert after.y() == pytest.approx(before.y())
    preview.mouseReleaseEvent(
        _mouse_event(QMouseEvent.Type.MouseButtonRelease, end,
                     Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton)
    )

    # 松手后再移动：不再平移
    pan = QPointF(preview._pan)
    preview.mouseMoveEvent(
        _mouse_event(QMouseEvent.Type.MouseMove, QPointF(10, 10),
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    )
    assert preview._pan == pan


def test_interactive_preview_reset_view(qapp):
    """reset_view 复位缩放与平移（切换条目 / Ctrl+0 时调用）。"""
    preview = JianziPreview(interactive=True)
    preview.resize(400, 400)
    preview.wheelEvent(_wheel_event(120, pos=QPointF(100, 100)))
    preview.mousePressEvent(
        _mouse_event(QMouseEvent.Type.MouseButtonPress, QPointF(200, 200),
                     Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
    )
    preview.mouseMoveEvent(
        _mouse_event(QMouseEvent.Type.MouseMove, QPointF(250, 250),
                     Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton)
    )
    assert preview._zoom != 1.0 or preview._pan != QPointF(0, 0)

    preview.reset_view()
    assert preview._zoom == 1.0
    assert preview._pan == QPointF(0, 0)


def test_preview_panel_style_version_has_pick_area(tmp_path, qapp):
    """风格库版本：顶部编码/作者，底部编码/标题输入框 + 挑选按钮（触发回调）。"""
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    picks: list[tuple[str, str]] = []
    panel = PreviewPanel(lambda entry, library, text, title: picks.append((text, title)))
    try:
        entry = next(e for e in lib.entries() if e.input_text == "大七")
        panel.show_versions([VersionItem(entry, lib, False)], 0)
        labels = [label.text() for label in panel.findChildren(QLabel)]
        assert "编码：大七" in labels
        assert "作者：云霄客" in labels
        edits = panel.findChildren(QLineEdit)
        assert [e.text() for e in edits] == ["大七", "大七"]
        edits[0].setText("我的减字")
        edits[1].setText("我的标题")
        panel.findChild(QPushButton).click()
        assert picks == [("我的减字", "我的标题")]
    finally:
        lib.close()


def test_preview_panel_custom_version_is_readonly(tmp_path, qapp):
    """自定义库版本：只读——来源与标题标签，无输入框与挑选按钮。"""
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    panel = PreviewPanel(lambda *args: None)
    try:
        entry = next(e for e in lib.entries() if e.input_text == "大七")
        panel.show_versions([VersionItem(entry, lib, True)], 0)
        labels = [label.text() for label in panel.findChildren(QLabel)]
        assert "来源：自定义库" in labels
        assert "标题：大七" in labels
        assert panel.findChildren(QLineEdit) == []
        assert panel.findChild(QPushButton) is None
    finally:
        lib.close()


def test_preview_panel_clear_empties(tmp_path, qapp):
    """clear() 清空面板（点目录时调用）。"""
    lib = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    panel = PreviewPanel(lambda *args: None)
    try:
        entry = lib.entries()[0]
        panel.show_versions([VersionItem(entry, lib, False)], 0)
        assert any("编码：" in label.text() for label in panel.findChildren(QLabel))
        panel.clear()
        qapp.processEvents()  # deleteLater 的销毁要等事件循环
        assert not any("编码：" in label.text() for label in panel.findChildren(QLabel))
    finally:
        lib.close()


def test_preview_panel_index_lists_versions_and_switches(tmp_path, qapp):
    """索引框：列出同编码全部版本（库名+编码），当前项高亮，单击即切换显示。"""
    style = JianziLibrary.load(make_fixture_db(tmp_path / "style.sqlite"))
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style.path, "大換七")
    custom_lib = JianziLibrary.load(custom)
    try:
        style_entry = next(e for e in style.entries() if e.input_text == "大換七")
        custom_entry = custom_lib.entries()[0]
        panel = PreviewPanel(lambda *args: None)
        versions = [
            VersionItem(custom_entry, custom_lib, True),
            VersionItem(style_entry, style, False),
        ]
        panel.show_versions(versions, 0)

        index = panel._index
        assert not index.isHidden()
        items = [index.topLevelItem(i).text(0) for i in range(index.topLevelItemCount())]
        assert items == ["自定义库：大换七", "云霄客：大換七"]
        assert index.currentItem() is index.topLevelItem(0)  # 当前版本高亮

        switched: list[int] = []
        panel.version_selected.connect(switched.append)
        index.setCurrentItem(index.topLevelItem(1))  # 单击索引 → 切换版本
        qapp.processEvents()
        assert switched == [1]
        assert index.currentItem() is index.topLevelItem(1)
        labels = [label.text() for label in panel.findChildren(QLabel)]
        assert "作者：云霄客" in labels  # 已切到风格库版本
    finally:
        custom_lib.close()
        style.close()


def test_preview_panel_index_hidden_when_empty(qapp):
    """无版本时索引框隐藏（中栏空态）。"""
    panel = PreviewPanel(lambda *args: None)
    assert panel._index.isHidden()


def test_preview_panel_degrades_on_missing_component(tmp_path, qapp):
    """缺构件轮廓时降级为提示，不崩溃。"""
    lib = JianziLibrary.load(make_incomplete_db(tmp_path / "broken.sqlite"))
    try:
        panel = PreviewPanel(lambda *args: None)
        panel.show_versions([VersionItem(lib.entries()[0], lib, False)], 0)
        panel.show()
        qapp.processEvents()
        assert not panel.grab().isNull()
        labels = [label.text() for label in panel.findChildren(QLabel)]
        assert any("缺少构件数据" in text for text in labels)
    finally:
        lib.close()


def _custom_entry_texts(window) -> list[str]:
    """自定义库目录下的条目编码（按树顺序）。"""
    root = window._custom_tree.topLevelItem(0)
    return [root.child(i).data(0, TEXT_ROLE) for i in range(root.childCount())]


def _style_entry_texts(window, library_index: int = 0) -> list[str]:
    root = window._style_tree.topLevelItem(library_index)
    return [root.child(i).data(0, TEXT_ROLE) for i in range(root.childCount())]


def test_style_tree_selection_drives_preview(tmp_path, qapp):
    """选中风格库条目 → 中栏显示该风格库版本（可挑选）；选中目录 → 清空面板。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._style_tree.topLevelItem(0)
        window._style_tree.setCurrentItem(root.child(2))  # 大七
        qapp.processEvents()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "编码：大七" in labels
        assert "作者：云霄客" in labels
        assert not window._panel._index.isHidden()

        window._style_tree.setCurrentItem(root)  # 选中目录 → 清空
        qapp.processEvents()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert not any("编码：" in text for text in labels)
        assert window._panel._index.isHidden()
    finally:
        window.close()


def test_custom_tree_selection_previews_entry(tmp_path, qapp):
    """选中自定义库条目 → 中栏默认显示自定义版本（只读），索引框含风格库版本。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "大換七")

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._custom_tree.topLevelItem(0)
        window._custom_tree.setCurrentItem(root.child(0))
        qapp.processEvents()

        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "编码：大换七" in labels
        assert "来源：自定义库" in labels
        assert "标题：挑换" in labels
        assert window._panel.findChildren(QLineEdit) == []      # 只读：无输入框
        assert window._panel.findChild(QPushButton) is None     # 只读：无挑选按钮
        index = window._panel._index
        items = [index.topLevelItem(i).text(0) for i in range(index.topLevelItemCount())]
        assert items == ["自定义库：大换七", "云霄客：大換七"]
        assert index.currentItem() is index.topLevelItem(0)  # 默认显示点击来源
    finally:
        window.close()


def test_custom_tree_previews_self_made_entry(tmp_path, qapp):
    """自定义库中的自造编码（风格库没有）也能预览：索引框仅自定义库一项。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "大換七", "我的减字")

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._custom_tree.topLevelItem(0)
        window._custom_tree.setCurrentItem(root.child(0))
        qapp.processEvents()
        index = window._panel._index
        items = [index.topLevelItem(i).text(0) for i in range(index.topLevelItemCount())]
        assert items == ["自定义库：我的减字"]
    finally:
        window.close()


def test_sidebars_are_expandable_directories(tmp_path, qapp):
    """两侧边栏以目录形式显示：目录名 = 库文件名（无后缀），展开后是条目。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "大換七")

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)

        assert window._style_tree.topLevelItemCount() == 1
        root = window._style_tree.topLevelItem(0)
        assert root.text(0) == "style"  # 文件名为 style.sqlite，目录名不带后缀
        assert root.childCount() == 4

        custom_root = window._custom_tree.topLevelItem(0)
        assert custom_root.text(0) == "custom"
        assert custom_root.childCount() == 1
        assert _custom_entry_texts(window) == ["大换七"]
        # 条目文字带中文键名
        assert custom_root.child(0).text(0) == "编码：大换七　标题：挑换"
    finally:
        window.close()


def test_unload_style_library(tmp_path, qapp):
    """取消加载风格库：目录与中栏预览一并消失，构件来源不再指向它。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    window = MainWindow()
    try:
        window.load_style_library(style)
        assert window._style_tree.topLevelItemCount() == 1
        window._unload_style_library(str(style))
        assert window._style_tree.topLevelItemCount() == 0
        assert window._libraries == []
        assert window._panel._index.isHidden()
    finally:
        window.close()


def test_unload_custom_library(tmp_path, qapp):
    """取消加载自定义库：目录消失，挑选目标置空。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        assert window._custom_tree.topLevelItemCount() == 1
        window._unload_custom_library()
        assert window._custom_tree.topLevelItemCount() == 0
        assert window._custom is None
    finally:
        window.close()


def test_opening_custom_db_fills_missing_components(tmp_path, qapp):
    """打开自定义库时自动用已加载的风格库补齐缺失构件（只补工作副本，保存才落盘）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = make_incomplete_db(tmp_path / "custom.sqlite", stroke_id=1)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        assert 1 not in _disk_components(custom)  # 补齐在工作副本，磁盘未动
        assert window._custom_dirty               # 补齐也算未保存的更改
        window.save_custom_library()
    finally:
        window.close()

    lib = JianziLibrary.load(custom)
    try:
        assert 1 in lib.components()  # 保存后：该库引用的构件已从风格库补齐
        assert place_components(lib.placements("大七"), lib.components())
    finally:
        lib.close()


def test_custom_sidebar_lists_entries_sorted_by_input_text(tmp_path, qapp):
    """自定义库边栏：列出已入库条目，按 InputText 排序。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "大換七")
    pick_entry(custom, style, "上七九應合散勾五")

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        texts = _custom_entry_texts(window)
        assert texts == sorted(texts)
        assert texts == ["上七九应合散勾五", "大换七"]
    finally:
        window.close()


def test_search_filters_both_sidebars(tmp_path, qapp):
    """搜索框同时过滤两个边栏（等价匹配）：一眼看出哪些已入库、哪些还能挑。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "大換七")

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        assert len(_custom_entry_texts(window)) == 1

        window._search.setText("大换七")  # 简体查询命中库中的「大换七」
        assert _custom_entry_texts(window) == ["大换七"]
        assert _style_entry_texts(window) == ["大換七"]

        window._search.setText("不存在的编码")
        assert _custom_entry_texts(window) == []
        assert _style_entry_texts(window) == []

        window._search.setText("")
        assert len(_custom_entry_texts(window)) == 1
        assert len(_style_entry_texts(window)) == 4
    finally:
        window.close()


def test_pick_from_comparison_uses_edited_code_and_title(tmp_path, qapp):
    """对比列的编码与标题均可自定义：挑选按输入框内容入库，但不落盘直到保存。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._style_tree.topLevelItem(0)
        window._style_tree.setCurrentItem(root.child(0))  # 触发中栏预览
        edits = window._panel.findChildren(QLineEdit)
        edits[0].setText("我的减字")   # 编码
        edits[1].setText("我的标题")   # 标题
        window._panel.findChild(QPushButton).click()
        assert _custom_entry_texts(window) == ["我的减字"]
        assert _disk_entries(custom) == []  # 挑选只写工作副本，磁盘未动
        assert window._custom_dirty

        window.save_custom_library()
        assert not window._custom_dirty
    finally:
        window.close()

    lib = JianziLibrary.load(custom)
    try:
        entry = lib.entries()[0]
        assert (entry.input_text, entry.title) == ("我的减字", "我的标题")
    finally:
        lib.close()


def test_pick_defaults_title_from_style_library(tmp_path, qapp):
    """标题默认取风格库的值（不改输入框直接挑选）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._style_tree.topLevelItem(0)
        window._style_tree.setCurrentItem(root.child(0))
        window._panel.findChild(QPushButton).click()
        window.save_custom_library()
    finally:
        window.close()

    lib = JianziLibrary.load(custom)
    try:
        entry = lib.entries()[0]
        assert entry.title == "长锁"  # fixture 中「上七九應合散勾五」的标题
    finally:
        lib.close()


def test_new_empty_custom_library(tmp_path, qapp):
    """新建空白自定义库：目录为空，可直接挑选入库；磁盘文件等保存时才出现。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    dest = tmp_path / "blank.sqlite"

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.new_custom_library(dest)
        assert window._custom is not None
        assert _custom_entry_texts(window) == []
        assert not dest.exists()  # 只有工作副本，磁盘尚未创建

        # 空库可直接作为挑选目标
        root = window._style_tree.topLevelItem(0)
        window._style_tree.setCurrentItem(root.child(0))
        window._panel.findChild(QPushButton).click()
        assert len(_custom_entry_texts(window)) == 1
        window.save_custom_library()
        assert dest.exists()
    finally:
        window.close()

    lib = JianziLibrary.load(dest)
    try:
        assert len(lib.entries()) == 1
        assert set(lib.components())  # 构件轮廓随之入库，库自包含
    finally:
        lib.close()


def test_save_as_from_style_creates_custom_library(tmp_path, qapp):
    """未打开自定义库时，另存为可从风格库创建（全量复制 + 批量转简体）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    dest = tmp_path / "saved.sqlite"

    window = MainWindow()
    try:
        window.load_style_library(style)
        message = window.save_custom_as(dest)
        assert "另存为" in message
        assert len(_custom_entry_texts(window)) == 4
    finally:
        window.close()

    lib = JianziLibrary.load(dest)
    try:
        assert sorted(e.input_text for e in lib.entries()) == [
            "上七九应合散勾五",
            "大七",
            "大七六就挑四",
            "大换七",
        ]
        assert set(lib.components()) >= {1, 2}
    finally:
        lib.close()


def _pick_first_entry(window) -> None:
    """从风格库挑入第一个条目（使自定义库工作副本变脏）。"""
    root = window._style_tree.topLevelItem(0)
    window._style_tree.setCurrentItem(root.child(0))
    window._panel.findChild(QPushButton).click()


def test_pick_adds_custom_version_to_index(tmp_path, qapp):
    """挑选成功后，索引框立即多出「自定义库」版本（且当前显示仍停留在风格库版本）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)
        qapp.processEvents()
        index = window._panel._index
        items = [index.topLevelItem(i).text(0) for i in range(index.topLevelItemCount())]
        assert items == ["自定义库：上七九应合散勾五", "云霄客：上七九應合散勾五"]
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "作者：云霄客" in labels  # 当前显示不变
    finally:
        window.close()


def test_history_back_forward_between_entries(tmp_path, qapp):
    """浏览历史：条目点击入栈；返回/前进在条目间导航；新导航清空前进栈。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._style_tree.topLevelItem(0)
        window._style_tree.setCurrentItem(root.child(0))  # 上七九應合散勾五
        window._style_tree.setCurrentItem(root.child(2))  # 大七

        window._history_back()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "编码：上七九應合散勾五" in labels

        window._history_back()  # 已到最早：原地不动
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "编码：上七九應合散勾五" in labels

        window._history_forward()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "编码：大七" in labels

        window._history_back()
        window._style_tree.setCurrentItem(root.child(3))  # 新导航 → 前进栈清空
        window._history_forward()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "编码：大七六就挑四" in labels  # 仍是新条目，未前进
    finally:
        window.close()


def test_history_records_version_switch(tmp_path, qapp):
    """索引框切换版本也入栈：可回退到同一条目的另一版本。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    pick_entry(custom, style, "大換七")

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        root = window._custom_tree.topLevelItem(0)
        window._custom_tree.setCurrentItem(root.child(0))  # 自定义版本
        index = window._panel._index
        index.setCurrentItem(index.topLevelItem(1))      # 切到风格库版本
        qapp.processEvents()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "作者：云霄客" in labels

        window._history_back()
        qapp.processEvents()
        labels = [label.text() for label in window._panel.findChildren(QLabel)]
        assert "来源：自定义库" in labels  # 回到自定义版本
    finally:
        window.close()


def test_history_context_menu_contents(tmp_path, qapp):
    """预览区右键菜单：返回/前进（按栈位置启用）+ 历史列表（库名+编码，最新在前）。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        menu = window._build_history_menu()
        back, forward = menu.actions()[0], menu.actions()[1]
        assert not back.isEnabled() and not forward.isEnabled()  # 空历史

        root = window._style_tree.topLevelItem(0)
        window._style_tree.setCurrentItem(root.child(0))
        window._style_tree.setCurrentItem(root.child(2))
        menu = window._build_history_menu()
        back, forward = menu.actions()[0], menu.actions()[1]
        assert back.isEnabled() and not forward.isEnabled()
        entries = [a.text() for a in menu.actions()[3:]]  # 跳过分隔线
        assert entries == ["style：大七", "style：上七九應合散勾五"]

        window._history_back()
        menu = window._build_history_menu()
        assert menu.actions()[1].isEnabled()  # 前进可用
    finally:
        window.close()


def test_close_prompt_save_persists_changes(tmp_path, qapp, monkeypatch):
    """关闭时有未保存更改：选「保存」把更改写回磁盘后正常关闭。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)
        assert _disk_entries(custom) == []

        monkeypatch.setattr(
            window, "_ask_unsaved_changes", lambda: QMessageBox.StandardButton.Save
        )
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        assert _disk_entries(custom) == ["上七九应合散勾五"]
        assert window._custom is None  # 工作副本已随关闭清理
    finally:
        window.close()


def test_close_prompt_discard_loses_changes(tmp_path, qapp, monkeypatch):
    """关闭时选「不保存」：更改丢失，磁盘保持原样。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)

        monkeypatch.setattr(
            window, "_ask_unsaved_changes", lambda: QMessageBox.StandardButton.Discard
        )
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted()
        assert _disk_entries(custom) == []
    finally:
        window.close()


def test_close_prompt_cancel_keeps_window(tmp_path, qapp, monkeypatch):
    """关闭时选「取消」：不关闭、不写盘，工作副本原样保留。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)

        monkeypatch.setattr(
            window, "_ask_unsaved_changes", lambda: QMessageBox.StandardButton.Cancel
        )
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert window._custom is not None
        assert _custom_entry_texts(window) == ["上七九应合散勾五"]
        assert _disk_entries(custom) == []
        window._custom_dirty = False  # 避免 finally 的 close() 再触发询问
    finally:
        window.close()


def test_save_as_repoints_saved_path(tmp_path, qapp):
    """另存为：工作副本写入新路径并改挂；原路径不动，目录名随新文件更新。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    dest = tmp_path / "renamed.sqlite"

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)

        message = window.save_custom_as(dest)
        assert "另存为" in message
        assert window._custom_saved_path == dest
        assert not window._custom_dirty
        assert _disk_entries(dest) == ["上七九应合散勾五"]
        assert _disk_entries(custom) == []  # 原路径不动
        assert window._custom_tree.topLevelItem(0).text(0) == "renamed"
    finally:
        window.close()


def test_save_after_save_as_writes_to_new_path(tmp_path, qapp):
    """另存为之后的「保存」写入新路径（工作副本已改挂），原路径始终不动。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)
    dest = tmp_path / "renamed.sqlite"

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)
        window.save_custom_as(dest)

        window._style_tree.setCurrentItem(window._style_tree.topLevelItem(0).child(2))  # 大七
        window._panel.findChild(QPushButton).click()
        assert window._custom_dirty
        window.save_custom_library()
        assert sorted(_disk_entries(dest)) == ["上七九应合散勾五", "大七"]
        assert _disk_entries(custom) == []
    finally:
        window.close()


def test_unload_custom_library_prompts_when_dirty(tmp_path, qapp, monkeypatch):
    """取消加载有未保存更改的自定义库：选「取消」中止，选「不保存」卸载且磁盘不动。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        _pick_first_entry(window)

        monkeypatch.setattr(
            window, "_ask_unsaved_changes", lambda: QMessageBox.StandardButton.Cancel
        )
        window._unload_custom_library()
        assert window._custom is not None  # 取消：保持加载

        monkeypatch.setattr(
            window, "_ask_unsaved_changes", lambda: QMessageBox.StandardButton.Discard
        )
        window._unload_custom_library()
        assert window._custom is None
        assert _disk_entries(custom) == []
    finally:
        window.close()


def test_component_browser_add_marks_main_window_dirty(tmp_path, qapp):
    """主窗口打开的部件浏览器与其共用工作副本：入库置脏，主窗口保存后落盘。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        window._open_component_browser()
        browser = window._component_browser
        assert not browser._open_custom_button.isVisibleTo(browser)  # 托管模式隐藏直开

        browser.select_component(0)
        result = browser.add_selected()
        assert result is not None and result.status == "inserted"
        assert window._custom_dirty
        assert _disk_entries(custom) == []

        window.save_custom_library()
        assert _disk_entries(custom) == ["〇甲"]
    finally:
        window.close()
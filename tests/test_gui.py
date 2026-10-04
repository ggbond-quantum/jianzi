"""GUI 冒烟测试：主窗口这一接缝能构造、能加载库并出画面（不测内部实现）。

离屏运行（QT_QPA_PLATFORM=offscreen）；窗口内部的绘制细节不在测试范围内。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from jianzipu.core.library import JianziLibrary, create_custom_db, pick_entry
from jianzipu.core.synthesis import Placement, place_components
from jianzipu.extract.build import SCHEMA
from jianzipu.gui.app import ComponentBrowserWindow, MainWindow, to_painter_path
from test_library import make_fixture_db


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


def test_comparison_survives_missing_component_data(tmp_path, qapp):
    """库缺构件轮廓时（历史数据或手工 SQL）对比应降级提示，不得让异常逃逸。"""
    broken = make_incomplete_db(tmp_path / "broken.sqlite")
    window = MainWindow()
    try:
        window.load_style_library(broken)
        window.show()
        qapp.processEvents()
        window._show_comparison(0)  # 直接调用：异常会传播到测试，不会被 Qt 吞掉
        assert window.isVisible()
        assert not window.grab().isNull()
    finally:
        window.close()


def test_opening_custom_db_fills_missing_components(tmp_path, qapp):
    """打开自定义库时自动用已加载的风格库补齐缺失构件，使预览/编译可用。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = make_incomplete_db(tmp_path / "custom.sqlite", stroke_id=1)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
    finally:
        window.close()

    lib = JianziLibrary.load(custom)
    try:
        assert 1 in lib.components()  # 该库引用的构件已从风格库补齐
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
        texts = [
            window._custom_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(window._custom_list.count())
        ]
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
        assert window._custom_list.count() == 1

        window._search.setText("大换七")  # 简体查询命中库中的「大换七」
        assert window._custom_list.count() == 1
        assert window._list.count() == 1

        window._search.setText("不存在的编码")
        assert window._custom_list.count() == 0
        assert window._list.count() == 0

        window._search.setText("")
        assert window._custom_list.count() == 1
        assert window._list.count() == 4
    finally:
        window.close()


def test_pick_from_comparison_uses_edited_code(tmp_path, qapp):
    """对比列的编码可自定义：挑选时按输入框内容入库。"""
    style = make_fixture_db(tmp_path / "style.sqlite")
    custom = tmp_path / "custom.sqlite"
    create_custom_db(custom)

    window = MainWindow()
    try:
        window.load_style_library(style)
        window.load_custom_library(custom)
        window._list.setCurrentRow(0)  # 触发对比渲染
        column = window._compare_layout.itemAt(0).widget()
        column.findChild(QLineEdit).setText("我的减字")
        column.findChild(QPushButton).click()
        assert window._custom_list.count() == 1
        assert window._custom_list.item(0).data(Qt.ItemDataRole.UserRole) == "我的减字"
    finally:
        window.close()

    lib = JianziLibrary.load(custom)
    try:
        assert [e.input_text for e in lib.entries()] == ["我的减字"]
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
        assert window._custom_list.count() == 4
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
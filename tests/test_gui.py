"""GUI 冒烟测试：主窗口这一接缝能构造、能加载库并出画面（不测内部实现）。

离屏运行（QT_QPA_PLATFORM=offscreen）；窗口内部的绘制细节不在测试范围内。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from jianzipu.core.library import JianziLibrary, create_custom_db
from jianzipu.core.synthesis import Placement, place_components
from jianzipu.gui.app import ComponentBrowserWindow, MainWindow, to_painter_path
from test_library import make_fixture_db


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
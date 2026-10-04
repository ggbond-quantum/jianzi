"""GUI 冒烟测试：主窗口这一接缝能构造、能加载库并出画面（不测内部实现）。

离屏运行（QT_QPA_PLATFORM=offscreen）；窗口内部的绘制细节不在测试范围内。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from jianzipu.core.library import create_custom_db
from jianzipu.gui.app import MainWindow
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
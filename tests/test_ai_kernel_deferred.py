# SPDX-License-Identifier: MIT
"""P0-15 反证：Agent 内核（几百个工具对象）不得在启动路径上装载。

事故形态：`_build_capability_drawer()` 在构造期同步调 `_ensure_agent_kernel()`
→ 窗口要等整套工具注册表 / Hooks / 编排器造完才出来；后来改成事件循环首拍
`singleShot(0, …)`，仍把首帧之后的启动路径拖长（实测主窗口可见 11102.3 ms）。

契约：构造返回时 0 次；事件循环首拍仍 0 次；能力矩阵**首次打开**才 1 次，
第二次打开复用已有内核、不重复装载。主窗口可见时间前后各 3 次中位的对比
见提交说明（外部脚本 `output/tmp/startup_visible_probe.py`）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_agent_kernel_build_is_deferred_to_first_open(qtapp, isolated_profile,
                                                      monkeypatch):
    import main_window as mw

    calls = []
    monkeypatch.setattr(mw.MainWindow, "_ensure_agent_kernel",
                        lambda self: calls.append(self))

    w = mw.MainWindow()
    try:
        assert calls == [], \
            "构造期就把 Agent 内核装起来了：首帧被这堆工具对象顶后了"
        qtapp.processEvents()
        assert calls == [], \
            "事件循环首拍就装内核：启动路径仍被这堆工具对象拖长"

        w._show_capability_drawer()
        assert len(calls) == 1, \
            "能力矩阵首次打开没有装载内核：矩阵会一直停在空态"
        w._show_capability_drawer()
        assert len(calls) == 1, "第二次打开又装了一遍内核：应复用已有内核"
    finally:
        w.close()

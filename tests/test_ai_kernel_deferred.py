# SPDX-License-Identifier: MIT
"""P0-15 反证：Agent 内核（几百个工具对象）不得在 MainWindow 构造期装载。

事故形态：`_build_capability_drawer()` 在构造期同步调 `_ensure_agent_kernel()`
→ 窗口要等整套工具注册表 / Hooks / 编排器造完才出来。实测同一负载窗口内
（`output/tmp/startup_visible_probe.py`，各 3 次冷启动取中位）主窗口可见
14217.2 ms → 11102.3 ms（−3115 ms）。

判据：构造返回时调用次数为 0；事件循环第一拍后必须补上（否则能力矩阵
永远停在空态）。
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


def test_agent_kernel_build_is_deferred_to_event_loop(qtapp, isolated_profile,
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
        assert calls, "事件循环第一拍没有补装内核：能力矩阵会一直停在空态"
    finally:
        w.close()

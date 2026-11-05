# SPDX-License-Identifier: MIT
"""冒烟：HEAD 的 main_window 必须能直接 import，且 shortcut 符号真实存在。

这条测试故意不装任何测试垫片。主窗口顶部从 spectrum_widget 导入
`owns_local_shortcuts`；该符号缺失时 `import desktop.main_window` 会
ImportError，应用根本起不来，本测试即红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def test_main_window_imports_without_shim():
    import desktop.spectrum_widget as sw
    import desktop.main_window as mw

    assert callable(getattr(sw, "owns_local_shortcuts", None)), (
        "spectrum_widget 必须导出 owns_local_shortcuts")
    assert callable(getattr(mw, "owns_local_shortcuts", None)), (
        "main_window 必须成功导入 owns_local_shortcuts")

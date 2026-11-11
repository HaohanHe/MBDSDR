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


def test_main_window_single_instance_and_shared_throttle(caplog):
    """裸名 `main_window` 与包名 `desktop.main_window` 必须是同一模块对象。

    两条导入路径各执行一遍模块体，`_WARN_THROTTLE_LAST` 就有两份：同一 key 的
    告警在两个副本里各放行一次（同故障两条日志），而测试里 clear() 的可能不是
    正在打日志的那一份 → 限流状态清不掉，该出现的告警反而被压掉
    （docs/redundancy-register.md §5.10）。
    反证：删掉 main_window.py 顶部的 sys.modules 别名块，本用例必红。
    """
    import logging

    import desktop.main_window as pkg
    import main_window as bare

    assert bare is pkg, "main_window 与 desktop.main_window 是两个模块对象"
    assert bare._WARN_THROTTLE_LAST is pkg._WARN_THROTTLE_LAST, \
        "限流表分裂：两个副本各记一份，clear() 清不到对方"

    key = "import-identity-probe"
    bare._WARN_THROTTLE_LAST.pop(key, None)
    try:
        with caplog.at_level(logging.WARNING, logger=pkg.logger.name):
            bare._warn_throttled(key, "probe A")
            pkg._warn_throttled(key, "probe B")
        hits = [r for r in caplog.records if r.getMessage().startswith("probe")]
        assert len(hits) == 1, f"同一 key 应只落一条，实际 {len(hits)} 条"
    finally:
        bare._WARN_THROTTLE_LAST.pop(key, None)

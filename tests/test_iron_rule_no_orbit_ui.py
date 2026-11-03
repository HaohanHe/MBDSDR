# SPDX-License-Identifier: MIT
"""
tests/test_iron_rule_no_orbit_ui.py
===================================
定轨 iron rule 的全 Qt 层守卫（用户明确要求，最高优先级）：

  「定轨不要作为功能，只有用户跟模型说要定轨才可以。」

落到可断言的形状：desktop/（整个 Qt UI 层）的任何源码都不得出现"定轨"
字样——没有页签/按钮/菜单/提示/工具芯片把它功能化。模型侧的原子积木
（mbdsdr_ai/orbit_tools.py 等）刻意保留：它们只在用户开口要定轨时由
模型现场编排，不是用户可见功能位，故不在本守卫扫描范围。

扫描是纯文本的、确定性的，不依赖 Qt 运行时。
"""
import os
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DESKTOP = _ROOT / "desktop"

FORBIDDEN = "定轨"


def _desktop_py_files():
    return sorted(p for p in _DESKTOP.rglob("*.py") if p.is_file())


def test_desktop_layer_has_no_orbit_feature_string():
    offenders = []
    for p in _desktop_py_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        if FORBIDDEN in text:
            offenders.append(p.relative_to(_ROOT).as_posix())
    assert not offenders, (
        f"定轨 不得在 Qt UI 层作为功能出现（iron rule）: {offenders}")


def test_desktop_layer_exists_and_scanned():
    """反向用例：守卫真的扫到了文件，而不是空目录假绿。"""
    files = _desktop_py_files()
    assert len(files) >= 10, f"desktop 层文件数异常: {len(files)}"
    assert (_DESKTOP / "main_window.py") in files

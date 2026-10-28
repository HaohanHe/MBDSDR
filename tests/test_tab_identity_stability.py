#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
页签身份稳定性测试
==================

红线（沿用 commit 7d3fa3f「panel id 取代译文 tabText」的口径）：布局落盘的
身份绝不能用译文。语言一切换译文就变，按译文存的顺序/可见性再也对不上，
布局恢复会静默丢页签。

本文件覆盖：
  1. _tab_key 优先取 tabData 里的稳定 id，无 id 时才回退译文；
  2. **语言切换**（译文全变）后，按稳定 id 存的顺序仍必须完整恢复；
  3. 旧版落盘文件（order 里存的是译文）仍能恢复 —— 向后兼容；
  4. 恢复后稳定 id 被重新挂回 tabData（下一次落盘仍是 id）。

运行: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_tab_identity_stability.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


PANELS = (
    ("spectrum", "频谱", "Spectrum"),
    ("rf_sky", "射频天空", "RF Sky"),
    ("doppler", "多普勒定轨", "Doppler"),
)


def _build(tab: QTabWidget, titles) -> None:
    """按给定译文建 3 个页签并挂上稳定 id。"""
    for (pid, _zh, _en), title in zip(PANELS, titles):
        tab.addTab(QWidget(), title)
        tab.tabBar().setTabData(tab.count() - 1, pid)


def _keys(tab: QTabWidget):
    from dock_layout_manager import DockLayoutManager as D
    return [D._tab_key(tab, i) for i in range(tab.count())]


def test_tab_key_prefers_stable_id(qapp):
    from dock_layout_manager import DockLayoutManager as D

    tab = QTabWidget()
    tab.addTab(QWidget(), "频谱")
    tab.addTab(QWidget(), "射频天空")
    tab.tabBar().setTabData(0, "spectrum")

    assert D._tab_key(tab, 0) == "spectrum", "有 tabData 时必须用稳定 id"
    assert D._tab_key(tab, 1) == "射频天空", "无 id 时才回退译文（兼容旧数据）"


def test_layout_survives_language_switch(qapp):
    """译文全变（模拟切语言），按稳定 id 存的顺序仍要完整恢复。"""
    from dock_layout_manager import DockLayoutManager as D

    tab = QTabWidget()
    _build(tab, [zh for _pid, zh, _en in PANELS])
    assert _keys(tab) == ["spectrum", "rf_sky", "doppler"]

    # 存的是稳定 id（新格式）
    saved = ["doppler", "spectrum", "rf_sky"]

    # 语言切换：译文全变成英文
    for i, (_pid, _zh, en) in enumerate(PANELS):
        tab.setTabText(i, en)

    D._restore_tab_order(tab, saved, 0, {})
    assert _keys(tab) == saved, \
        f"语言切换后顺序必须按 id 恢复，实际: {_keys(tab)}"
    # 稳定 id 必须被重新挂回，否则下一次落盘又变回译文
    assert all(tab.tabBar().tabData(i) for i in range(tab.count()))


def test_legacy_layout_still_restores(qapp):
    """旧版落盘文件（order 存的是译文）必须仍能恢复 —— 向后兼容。"""
    from dock_layout_manager import DockLayoutManager as D

    tab = QTabWidget()
    _build(tab, [zh for _pid, zh, _en in PANELS])

    # 旧格式：存的是中文译文
    legacy = ["多普勒定轨", "频谱", "射频天空"]
    D._restore_tab_order(tab, legacy, 0, {})
    assert _keys(tab) == ["doppler", "spectrum", "rf_sky"], \
        f"旧版译文落盘必须仍可恢复，实际: {_keys(tab)}"


def test_capture_uses_stable_ids(qapp):
    """落盘产出的 order 必须是稳定 id，不是译文。"""
    from dock_layout_manager import DockLayoutManager as D

    tab = QTabWidget()
    _build(tab, [zh for _pid, zh, _en in PANELS])

    captured = [D._tab_key(tab, i) for i in range(tab.count())]
    assert captured == ["spectrum", "rf_sky", "doppler"], \
        f"落盘必须写 id，实际: {captured}"
    assert "频谱" not in captured, "译文绝不能进落盘键"

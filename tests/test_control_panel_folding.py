# SPDX-License-Identifier: MIT
"""W51 / P1-07：控制列 6 组手风琴折叠（D-10 A）。

旧版 25 个控件 6 组全展开，列高 1,148 px（≈1.95 屏），必须一路滚。
判据：6 组皆可折叠；默认高 < 1 屏；手风琴单展开。

反证：把 `_add_foldable` 改成始终 `expanded=True`（等于去掉折叠），
`test_default_only_one_section_and_under_one_screen` 必须变红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import control_panel as cp  # noqa: E402

_TITLES = ["频率", "频率预设", "书签", "音量", "接收", "录音"]


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qtapp):
    return cp.ControlPanel()


class TestFolding:
    def test_six_sections_in_order(self, panel):
        assert [t for _h, _g, t in panel._fold_sections] == _TITLES
        assert len(panel._fold_sections) == 6

    def test_default_only_one_section_and_under_one_screen(self, panel):
        opened = [t for h, _g, t in panel._fold_sections if h.isChecked()]
        assert opened == ["频率"], f"默认只该展开「频率」，实际 {opened}"
        # 1 屏按 768 高屏扣除任务栏/标题栏后的可用高度取 760。
        assert panel.sizeHint().height() < 760, (
            f"默认高度 {panel.sizeHint().height()} px 超过 1 屏，折叠未生效")

    def test_accordion_single_expand_and_all_reachable(self, panel):
        for header, group, title in panel._fold_sections:
            if not header.isChecked():
                header.click()
            opened = [t for h, _g, t in panel._fold_sections if h.isChecked()]
            assert opened == [title], f"点开「{title}」后应为单展开，实际 {opened}"
            assert group.isVisibleTo(panel)
            # 再点一次收起，全部收起也允许（与右栏一致）
            header.click()
            assert not header.isChecked()
            assert not group.isVisibleTo(panel)

    def test_collapsing_does_not_disable_controls(self, panel):
        # 折叠只是隐藏，不是禁用：程序化回填/快捷键仍应生效
        panel.volume_slider.setValue(37)
        assert panel.volume_slider.value() == 37
        assert panel.agc_check is not None

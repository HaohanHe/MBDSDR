# SPDX-License-Identifier: MIT
"""频率预设：跟随频段、最小宽与条目文本无关（回归钉扎）。

背景：2026-11-06 打包线 overlap 步在 windows 1000×600 上出现 13/15 两态跳变。
根因是 ``_on_mode_changed`` / ``set_mode`` 把解调模式名（``"FM"``）当频段名
传给 ``_populate_presets``：没有一支命中 → ``clear()`` 后列表为空，QComboBox
的最小宽缓存因此在 292↔101 之间摇摆，右栏最小宽随之 356↔278。

本文件钉两条：
1. 切模式不重排/不清空预设列表 —— 预设只跟 ``band_combo`` 的频段走；
2. ``preset_combo`` 的 ``minimumSizeHint`` 与条目文本长短、是否为空无关。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def cp(qapp):
    from control_panel import ControlPanel
    w = ControlPanel()
    yield w
    w.deleteLater()


def _items(combo) -> list:
    return [combo.itemText(i) for i in range(combo.count())]


def test_mode_switch_keeps_band_presets(cp):
    cp.band_combo.setCurrentText("航空")
    before = _items(cp.preset_combo)
    assert before, "航空频段应有预设条目"

    cp.mode_combo.setCurrentText("USB")
    assert _items(cp.preset_combo) == before, "切解调模式不得重排预设"
    cp.set_mode("AM")
    assert _items(cp.preset_combo) == before, "set_mode 不得清空预设"


@pytest.mark.parametrize("band", ["FM 广播", "AM 广播", "航空", "短波"])
def test_presets_never_empty_after_set_mode(cp, band):
    cp.band_combo.setCurrentText(band)
    expected = _items(cp.preset_combo)
    assert expected, f"{band} 频段应有预设（含占位条目）"

    cp.set_mode("CW")
    assert _items(cp.preset_combo) == expected


def test_minimum_width_independent_of_item_text(cp):
    widths = []
    for band in ("FM 广播", "航空", "短波"):
        cp._populate_presets(band)
        widths.append(cp.preset_combo.minimumSizeHint().width())

    cp.preset_combo.clear()
    widths.append(cp.preset_combo.minimumSizeHint().width())

    assert len(set(widths)) == 1, f"最小宽随条目文本变化：{widths}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

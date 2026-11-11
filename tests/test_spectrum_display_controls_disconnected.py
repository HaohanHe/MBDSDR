# SPDX-License-Identifier: MIT
"""
断连时纯显示项仍可用（G12，2026-11-12 用户裁定）
================================================

旧契约：``_set_controls_enabled(False)`` 把 14 个控件一律置灰——没插设备时
调色板/平滑/峰值/保持/书签/频段/自动量程/峰值门一项都配不了，「更多」点开
还是大半灰。这些项只改 ``_state`` 与 LUT，接入后第一帧就能按新设置画。

新契约：
  * 依赖数据的 DSP 项（窗/FFT/平均/性能/调谐模式）仍按连接状态置灰；
  * 纯显示项始终可用；
  * 谱面空态第三行必须写明「显示设置已保存，接入后生效」——只放开控件
    不给这句话，用户改完看不到变化会以为坏了；
  * 断连时设的调色板必须真的驱动接入后的 LUT/绘制像素，不能只断言
    ``_state`` 字段变了（本文件用真实绘制路径反证）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QPushButton, QWidget,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def panel(qapp):
    from spectrum_widget import SpectrumPanel
    p = SpectrumPanel()
    yield p
    p.deleteLater()


def _interactive(widget):
    return [w for w in widget.findChildren(QWidget)
            if isinstance(w, (QComboBox, QCheckBox, QDoubleSpinBox, QPushButton))]


# ---------------------------------------------------------------------------
# ① 断连时纯显示项可用，DSP 项仍置灰
# ---------------------------------------------------------------------------
def test_display_items_enabled_but_dsp_disabled_when_disconnected(panel):
    display = (panel._palette_combo, panel._wf_smooth_chk, panel._peak_chk,
               panel._peak_spin, panel._maxhold_chk, panel._minhold_chk,
               panel._bookmark_chk, panel._bandplan_chk, panel._auto_db_chk)
    dsp = (panel._win_combo, panel._fft_combo, panel._avg_combo,
           panel._perf_combo, panel._tune_mode_btn)

    # 构造后默认就是断开
    assert panel._connected is False
    for w_ in display:
        assert w_.isEnabled(), f"{w_.objectName() or type(w_).__name__} 断连时必须可用"
    for w_ in dsp:
        assert not w_.isEnabled(), "DSP 项断连时必须置灰（旧契约这部分保留）"

    # 接上后全部恢复（防「一律常开」的假修复）
    panel.set_connected(True)
    for w_ in display + dsp:
        assert w_.isEnabled()
    # 再断开：显示项留、DSP 项回灰
    panel.set_connected(False)
    assert all(w_.isEnabled() for w_ in display)
    assert all(not w_.isEnabled() for w_ in dsp)


def test_more_menu_is_not_mostly_gray_when_disconnected(panel):
    """「更多」菜单里 9 个可交互控件可用（调色板 1 + 开关 7 + 峰值门 1）。"""
    rows = [a.defaultWidget() for a in panel._more_menu.actions()
            if a.defaultWidget() is not None]
    assert len(rows) == 10, "菜单行数变了，请同步更新本用例的期望"
    enabled = [(w, r) for r in rows for w in _interactive(r) if w.isEnabled()]
    assert len(enabled) == 9, (
        f"断连时菜单可用控件应为 9 个，实际 {len(enabled)}："
        f"{[type(w).__name__ for w, _ in enabled]}")


# ---------------------------------------------------------------------------
# ② 空态第三行说明（只放开控件不给说明 = 用户以为坏了）
# ---------------------------------------------------------------------------
def test_offline_note_text_and_visibility(panel):
    from spectrum_widget import OFFLINE_SETTINGS_NOTE, _offline_text_lines
    st = panel._state

    lines = _offline_text_lines(st, 238.0)
    assert len(lines) == 3
    assert lines[2][0] == OFFLINE_SETTINGS_NOTE
    assert "接入后生效" in lines[2][0]

    # 接上后不再需要这句说明
    panel.set_connected(True)
    assert len(_offline_text_lines(st, 238.0)) == 2

    # 放不下就不硬塞（星象仪紧凑条约 110px 谱面）
    panel.set_connected(False)
    assert len(_offline_text_lines(st, 80.0)) == 2

    # 空态绘制路径可跑通（不抛异常即通过）
    panel._plot.resize(600, 260)
    panel._plot.grab()


# ---------------------------------------------------------------------------
# ③ 反证：断连时设的调色板，接入后真的按它上色（不是字段变了就算）
# ---------------------------------------------------------------------------
def _rgb(c):
    """QColor 或 '#RRGGBB' 字符串 → (r, g, b)。"""
    q = c if isinstance(c, QColor) else QColor(c)
    return tuple(q.getRgb()[:3])


def _dist2(a, b):
    return sum((int(x) - int(y)) ** 2 for x, y in zip(a, b))


def test_palette_chosen_while_disconnected_drives_lut_after_connect(panel):
    from spectrum_widget import (
        WATERFALL_COLORS, WATERFALL_COLORS_SOFT, _wf_build_lut,
        _wf_paint_row_into, value_to_color,
    )

    st = panel._state
    # 断连时顺手关掉自动量程（量程固定住，像素期望值才可算）
    panel._auto_db_chk.setChecked(False)
    # 先接上，用经典色带画一次，把 LUT 钉在经典色上
    panel.set_connected(True)
    _wf_build_lut(st)
    assert _rgb(WATERFALL_COLORS[0]) == tuple(int(v) for v in st._wf_lut[0])

    # 断开 → 改调色板（用户此时看不到任何画面变化）
    panel.set_connected(False)
    panel._palette_combo.setCurrentIndex(1)
    assert st.wf_colors == list(WATERFALL_COLORS_SOFT)
    assert st._wf_lut is None, "切色带必须让 LUT 失效重建，否则接入后还是旧色"

    # 接入并喂一帧真 IQ：grab 走真实绘制路径（_draw_waterfall 重建 LUT）
    panel.set_connected(True)
    rng = np.random.default_rng(5)
    t = np.arange(16384)
    iq = (0.02 * np.exp(2j * np.pi * 2000 / 16384 * t)
          + 0.01 * (rng.standard_normal(16384)
                    + 1j * rng.standard_normal(16384))).astype(np.complex64)
    panel.update_iq(iq, 100e6, 2.4e6)
    panel._wf_plot.resize(120, 60)
    panel._wf_plot.grab()
    assert st._wf_lut is not None, "接入后瀑布绘制没有重建 LUT"

    # LUT 两端点必须是低饱和色带的端点，而不是经典色带
    assert _rgb(WATERFALL_COLORS_SOFT[0]) == tuple(int(v) for v in st._wf_lut[0])
    assert _rgb(WATERFALL_COLORS_SOFT[-1]) == tuple(int(v) for v in st._wf_lut[-1])
    assert _rgb(WATERFALL_COLORS[0]) != _rgb(WATERFALL_COLORS_SOFT[0])

    # 行→像素的真实映射：中点电平必须落在低饱和色带对应的颜色附近
    rows = np.full(8, -60.0, dtype=np.float32)
    dst = np.zeros((8, 4), dtype=np.uint8)
    _wf_paint_row_into(st, rows, dst)
    painted = tuple(int(v) for v in dst[0, :3])
    soft = _rgb(value_to_color(-60.0, st.db_min, st.db_max, WATERFALL_COLORS_SOFT))
    classic = _rgb(value_to_color(-60.0, st.db_min, st.db_max, WATERFALL_COLORS))
    assert _dist2(painted, soft) <= 30, (
        f"断连时选的色带没有作用到像素：painted={painted} soft={soft}")
    assert _dist2(painted, classic) > _dist2(painted, soft), (
        "像素更像经典色带，说明断连时选的调色板没生效")


def test_hold_enabled_while_disconnected_takes_effect_after_connect(panel):
    """断连时勾的 Max 保持，接入后必须真的攒包络（真实数据路径）。"""
    panel._maxhold_chk.setChecked(True)
    assert panel.generator.show_max_hold is True   # 断连即可设置
    assert panel.generator.max_hold is None

    panel.set_connected(True)
    spec = np.full(panel.generator.num_bins, -95.0, dtype=np.float32)
    spec[7] = -50.0
    panel.generator.spectrum = spec
    panel.generator._has_real_data = True
    panel.generator._update_hold()
    assert panel.generator.max_hold is not None
    assert float(panel.generator.max_hold[7]) == pytest.approx(-50.0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

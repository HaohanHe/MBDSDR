# SPDX-License-Identifier: MIT
"""
tests/test_tune_mode.py
=======================
验证 P0-2 Center/Normal 调谐模式（对标 SDR++ 调谐模式按钮；gqrx 中键 /
SDR# Center 同原语）：

  1. center（默认）：点击调谐重设硬件中心频率，画面跟过去；
  2. normal：点击只移动当前主听 VFO（按 snap 对齐），硬件中心不动，
     瀑布保留多信号轨迹；
  3. normal 无主听 VFO 时退回 center 行为；
  4. 工具栏切换钮联动 state.tune_mode 与按钮文案。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_tune_mode.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _make_panel():
    from spectrum_widget import SpectrumPanel
    from mbdsdr_ai.vfo_manager import VfoManager
    panel = SpectrumPanel()
    mgr = VfoManager()
    panel.vfo_manager = mgr
    panel._plot.resize(800, 600)
    return panel, mgr


class TestCenterMode:
    def test_click_retunes_hardware_center(self):
        panel, mgr = _make_panel()
        gen = panel.generator
        gen.center_freq_hz = 120e6
        gen.sample_rate_hz = 2.4e6
        panel._state.snap_interval = 10_000.0
        tuned = []
        panel.freq_changed.connect(tuned.append)

        panel._plot._tune_click(0.5)   # 点击正中央 = 当前中心
        assert gen.center_freq_hz == 120e6
        panel._plot._tune_click(0.75)  # 点击右 3/4 处
        expect = round((120e6 + 0.25 * 2.4e6) / 10_000.0) * 10_000.0
        assert gen.center_freq_hz == expect
        assert tuned and tuned[-1] == expect / 1e6


class TestNormalMode:
    def test_click_moves_vfo_only(self):
        panel, mgr = _make_panel()
        gen = panel.generator
        gen.center_freq_hz = 120e6
        gen.sample_rate_hz = 2.4e6
        panel._state.snap_interval = 10_000.0
        panel._state.tune_mode = "normal"

        vfo = mgr.add(120e6, 12.5e3, "FM")
        mgr.set_active_context(vfo, temporary=False)

        moved = []
        panel.vfo_moved.connect(lambda vid, hz: moved.append((vid, hz)))
        tuned = []
        panel.freq_changed.connect(tuned.append)

        panel._plot._tune_click(0.75)
        expect = round((120e6 + 0.25 * 2.4e6) / 10_000.0) * 10_000.0
        assert vfo.center_hz == expect            # VFO 跟手
        assert gen.center_freq_hz == 120e6        # 硬件中心不动
        assert moved == [(vfo.vfo_id, float(expect))]
        assert tuned == []                        # 不重调硬件

    def test_normal_without_vfo_falls_back_to_center(self):
        panel, mgr = _make_panel()
        gen = panel.generator
        gen.center_freq_hz = 120e6
        gen.sample_rate_hz = 2.4e6
        panel._state.snap_interval = 10_000.0
        panel._state.tune_mode = "normal"
        # mgr 里无 VFO → current 为 None
        tuned = []
        panel.freq_changed.connect(tuned.append)
        panel._plot._tune_click(0.75)
        assert gen.center_freq_hz != 120e6        # 退回中心调谐
        assert tuned


class TestToolbarToggle:
    def test_toggle_updates_state_and_text(self):
        panel, _mgr = _make_panel()
        assert panel._state.tune_mode == "center"
        assert panel._tune_mode_btn.text() == "调谐:中心"
        panel._tune_mode_btn.setChecked(False)
        assert panel._state.tune_mode == "normal"
        assert panel._tune_mode_btn.text() == "调谐:VFO"
        panel._tune_mode_btn.setChecked(True)
        assert panel._state.tune_mode == "center"

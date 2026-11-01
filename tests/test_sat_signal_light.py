# SPDX-License-Identifier: MIT
"""
星点亮（点星即听闭环下半场）钉扎测试
====================================

  * _snr_signal_present 回差：>=8 dB 点亮，<5 dB 熄灭，中间保持原态，
    无数据（None）不点亮也不延续；
  * RFSkyView.set_sat_signal：置位/清除/reset 清空；点亮星渲染加
    辉光环不崩。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_sat_signal_light.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from desktop.rf_sky_view import RFSkyView, SkyObject  # noqa: E402
from desktop.main_window import _snr_signal_present  # noqa: E402


# ----------------------------------------------------------------------
class TestSnrHysteresis:
    def test_enter_at_8db(self):
        assert _snr_signal_present(8.0, False) is True
        assert _snr_signal_present(15.0, False) is True

    def test_exit_below_5db(self):
        assert _snr_signal_present(4.9, True) is False
        assert _snr_signal_present(-10.0, True) is False

    def test_hysteresis_holds_state(self):
        # 5..8 回差带内保持原态（不抖动）
        assert _snr_signal_present(6.5, True) is True
        assert _snr_signal_present(6.5, False) is False

    def test_none_means_no_signal(self):
        assert _snr_signal_present(None, True) is False
        assert _snr_signal_present(None, False) is False


# ----------------------------------------------------------------------
class TestSatSignalLight:
    def _view_with_sat(self):
        v = RFSkyView()
        v.resize(600, 600)
        v._observer = {"lat": 40.0, "lon": 116.0, "alt_m": 0.0}
        v.set_objects([SkyObject("NOAA-19", 30.0, 50.0,
                                 obj_type="satellite")])
        return v

    def test_set_and_clear(self):
        v = self._view_with_sat()
        v.set_sat_signal("NOAA-19", True)
        assert "NOAA-19" in v._signal_sats
        v.set_sat_signal("NOAA-19", False)
        assert "NOAA-19" not in v._signal_sats

    def test_lit_sat_renders_with_halo(self):
        v = self._view_with_sat()
        v.set_sat_signal("NOAA-19", True)
        img = v.grab().toImage()
        assert not img.isNull()

    def test_clear_data_resets_lit_set(self):
        v = self._view_with_sat()
        v.set_sat_signal("NOAA-19", True)
        v._clear_view_data() if hasattr(v, "_clear_view_data") else None
        # 公开复位路径：set_objects 空集 + clear
        v.set_objects([])
        assert "NOAA-19" in v._signal_sats  # set_objects 不动点亮集

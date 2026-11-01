# SPDX-License-Identifier: MIT
"""
tests/test_sky_landscape_layers.py
===================================
验证星象仪地景 S1 妆面：

  1. `_landscape_layer_profile` 值噪声轮廓：确定性（同参同值）、种子可分、
     输出有界（[base-quant, base+amp+quant]）；
  2. 量化层（房屋）输出落在量化网格上；
  3. offscreen 渲染冒烟：开/关地景两层 + 昼/夜两态都过 paintEvent 不崩。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_landscape_layers.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import (  # noqa: E402
    _LANDSCAPE_LAYERS, _landscape_layer_profile,
)


class TestProfileDeterminism:
    def test_same_args_same_values(self):
        calls = [
            _landscape_layer_profile(az, seed=11, base_alt=1.0, amp_alt=3.2,
                                     lattice_deg=45.0, octaves=3)
            for az in range(0, 360, 7)
        ]
        again = [
            _landscape_layer_profile(az, seed=11, base_alt=1.0, amp_alt=3.2,
                                     lattice_deg=45.0, octaves=3)
            for az in range(0, 360, 7)
        ]
        assert calls == again

    def test_different_seeds_differ(self):
        a = [_landscape_layer_profile(az, seed=11, base_alt=1.0, amp_alt=3.2,
                                      lattice_deg=45.0, octaves=3)
             for az in range(0, 360, 3)]
        b = [_landscape_layer_profile(az, seed=37, base_alt=1.0, amp_alt=3.2,
                                      lattice_deg=45.0, octaves=3)
             for az in range(0, 360, 3)]
        assert a != b

    def test_output_bounded(self):
        for seed, base, amp, lat, octaves, quant in _LANDSCAPE_LAYERS:
            for az in range(0, 360):
                alt = _landscape_layer_profile(
                    float(az), seed=seed, base_alt=base, amp_alt=amp,
                    lattice_deg=lat, octaves=octaves, quant_deg=quant)
                assert base - quant - 1e-9 <= alt <= base + amp + quant + 1e-9

    def test_quantized_layer_snaps_to_grid(self):
        quant = 0.35
        for az in range(0, 360, 5):
            alt = _landscape_layer_profile(az, seed=53, base_alt=0.0,
                                           amp_alt=1.4, lattice_deg=23.0,
                                           octaves=2, quant_deg=quant)
            k = round(alt / quant)
            assert abs(alt - k * quant) < 1e-9


class TestLandscapeRenderSmoke:
    def _make_view(self):
        from rf_sky_view import RFSkyView
        view = RFSkyView()
        view.set_observer(40.0, 116.0, 0.0)
        view.resize(800, 600)
        return view

    def test_night_renders_with_layers(self):
        view = self._make_view()
        view._sky_brightness = 0.0  # 钉死夜态，不绑墙钟
        pm = view.grab()
        assert not pm.isNull()

    def test_day_renders_with_layers(self):
        view = self._make_view()
        view._sky_brightness = 1.0  # 钉死昼态
        pm = view.grab()
        assert not pm.isNull()

    def test_landscape_toggle_off_renders(self):
        view = self._make_view()
        view._sky_brightness = 0.0
        view._show_landscape = False
        pm = view.grab()
        assert not pm.isNull()

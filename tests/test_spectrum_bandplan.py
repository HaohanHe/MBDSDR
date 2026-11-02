# SPDX-License-Identifier: MIT
"""
tests/test_spectrum_bandplan.py
================================
验证 SDR++ 频段彩带仿妆（desktop/bandplan.json 皮进 + 谱面绘制）:

  1. 加载器: 条目数、字段合法 (label 非空 / 0<start<stop / category 五色内);
  2. 几何: _band_x_range 视口内夹紧、视口外 None;
  3. 面板集成: SpectrumPanel offscreen 实例化即有 bandplan 数据与
     开关, repaint 不崩; 关掉开关后绘制路径同样不崩。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_bandplan.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from spectrum_widget import (  # noqa: E402
    _load_bandplan, _band_x_range, BANDPLAN_CATEGORY_COLORS, SpectrumPanel,
)


class TestLoader:
    def test_entries_sane(self):
        bp = _load_bandplan()
        assert len(bp) >= 15
        for b in bp:
            assert b["label"]
            assert 0.0 < b["start_hz"] < b["stop_hz"]
            assert b["category"] in BANDPLAN_CATEGORY_COLORS

    def test_expected_bands_present(self):
        bp = _load_bandplan()
        labels = " ".join(b["label"] for b in bp)
        for want in ("FM 广播", "航空语音", "低轨卫星下行", "40m 业余"):
            assert want in labels

    def test_deterministic_cache(self):
        assert _load_bandplan() is _load_bandplan()


class TestGeometry:
    RECT = QRectF(0.0, 0.0, 1000.0, 100.0)

    def test_in_view(self):
        # 中心 100 MHz, 跨度 2.4 MHz: 99.1-100.9 MHz 全在视口内
        rng = _band_x_range(self.RECT, 100e6, 2.4e6, 99.1e6, 100.9e6)
        assert rng is not None
        x0, x1 = rng
        assert 0.0 < x0 < x1 < 1000.0
        # 中心对齐: 99.1MHz 在左半, 100.9MHz 在右半
        assert x0 < 500.0 < x1

    def test_clamped_at_edge(self):
        rng = _band_x_range(self.RECT, 100e6, 2.4e6, 99.0e6, 101.5e6)
        assert rng is not None
        x0, x1 = rng
        assert x0 >= 0.0 and x1 <= 1000.0

    def test_out_of_view_returns_none(self):
        # 40m 业余 7-7.3 MHz 对 100 MHz±1.2 MHz 视口完全在外
        assert _band_x_range(self.RECT, 100e6, 2.4e6, 7.0e6, 7.3e6) is None
        # 上缘之外同理
        assert _band_x_range(self.RECT, 100e6, 2.4e6, 103.0e6, 108.0e6) is None


class TestPanelIntegration:
    def test_panel_has_bandplan_and_renders(self):
        panel = SpectrumPanel()
        panel.resize(800, 300)
        assert panel._state.bandplan, "面板应自带 bandplan 数据"
        assert panel._state.show_bandplan is True
        # 视图对准 FM 广播段, 让彩带真正进绘制路径
        panel.generator.center_freq_hz = 98.0e6
        panel.show()
        app.processEvents()
        panel.repaint()
        # 关闭开关后路径也不崩
        panel._bandplan_chk.setChecked(False)
        assert panel._state.show_bandplan is False
        panel.repaint()

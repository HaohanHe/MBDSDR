# SPDX-License-Identifier: MIT
"""
Max/Min hold 包络 + 顶栏常驻 SNR 表 钉扎测试（P1-7）
====================================================

SDR++ 签名对齐：
  * Max/Min hold 复选框 → generator 逐帧逐 bin 运行 max/min 包络，
    关闭即丢包络、clear_data 清空、重开从头攒；
  * SNR = 主听 VFO 带内峰值 − 带外中位噪声底；无 VFO 退回全谱峰 − 中位；
    无数据返回 None（顶栏显 "SNR --"，不造假）；
  * 带外强峰不得污染 VFO 带内 SNR（反向用例）。

不依赖硬件 / 声卡 / 真实 IQ（spectrum 数组直接钉值）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Max/Min hold（纯 generator 层）
# ---------------------------------------------------------------------------
def _push_frame(gen, peak_idx: int, peak_db: float, floor_db: float = -90.0):
    spec = np.full(gen.num_bins, floor_db, dtype=np.float32)
    spec[peak_idx] = peak_db
    gen.spectrum = spec
    gen._has_real_data = True
    gen._update_hold()


def test_max_hold_envelope():
    from spectrum_widget import SpectrumDataGenerator
    gen = SpectrumDataGenerator(num_bins=64)
    gen.set_max_hold(True)
    _push_frame(gen, 10, -50.0)
    _push_frame(gen, 40, -60.0, floor_db=-95.0)
    mh = gen.max_hold
    assert mh is not None and len(mh) == 64
    assert mh[10] == -50.0          # 历史最高保留
    assert mh[40] == -60.0
    assert mh[0] == -50.0 or mh[0] == pytest.approx(-90.0)  # 底噪取历史 max
    # 后续帧低于包络：包络不动
    _push_frame(gen, 10, -70.0)
    assert gen.max_hold[10] == -50.0


def test_min_hold_envelope():
    from spectrum_widget import SpectrumDataGenerator
    gen = SpectrumDataGenerator(num_bins=64)
    gen.set_min_hold(True)
    _push_frame(gen, 0, -90.0, floor_db=-90.0)   # 首帧拷贝
    assert gen.min_hold[0] == pytest.approx(-90.0)
    _push_frame(gen, 0, -95.0, floor_db=-95.0)   # 整帧下潜 → 包络跟低
    assert gen.min_hold[0] == pytest.approx(-95.0)
    assert gen.min_hold[40] == pytest.approx(-95.0)
    _push_frame(gen, 20, -97.0, floor_db=-93.0)  # 底噪回抬 + 更深新低
    assert gen.min_hold[0] == pytest.approx(-95.0)   # 新值 -93 > 包络：不动
    assert gen.min_hold[20] == pytest.approx(-97.0)  # 新低被记录


def test_hold_disable_drops_envelope():
    from spectrum_widget import SpectrumDataGenerator
    gen = SpectrumDataGenerator(num_bins=64)
    gen.set_max_hold(True)
    gen.set_min_hold(True)
    _push_frame(gen, 10, -50.0)
    assert gen.max_hold is not None and gen.min_hold is not None
    gen.set_max_hold(False)
    gen.set_min_hold(False)
    assert gen.max_hold is None and gen.min_hold is None


def test_hold_cleared_with_data():
    from spectrum_widget import SpectrumDataGenerator
    gen = SpectrumDataGenerator(num_bins=64)
    gen.set_max_hold(True)
    _push_frame(gen, 10, -50.0)
    gen.clear_data()
    assert gen.max_hold is None and gen.min_hold is None


# ---------------------------------------------------------------------------
# SNR 计算
# ---------------------------------------------------------------------------
def _panel_with_spectrum(qapp, spec_db: np.ndarray, center=100e6, span=2.4e6):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    gen = panel.generator
    gen.num_bins = len(spec_db)
    gen.spectrum = spec_db.astype(np.float32)
    gen.center_freq_hz = float(center)
    gen.sample_rate_hz = float(span)
    gen._has_real_data = True
    return panel


def test_snr_no_data_returns_none(qapp):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    assert panel.compute_snr_db() is None   # 未连接：None，顶栏显 --


def test_snr_peak_vs_floor_no_vfo(qapp):
    spec = np.full(256, -90.0)
    spec[100] = -40.0
    panel = _panel_with_spectrum(qapp, spec)
    snr = panel.compute_snr_db()
    assert snr == pytest.approx(50.0, abs=0.6)


def test_snr_vfo_band_ignores_outband_peak(qapp):
    """反向用例：带外 -30dB 强峰不得污染带内 SNR。"""
    from mbdsdr_ai.vfo_manager import VfoManager
    spec = np.full(256, -90.0)
    center, span = 100e6, 2.4e6
    # VFO 在中心（bin 128 附近），带内信号 -50；带外强峰 -30（bin 40）
    spec[128] = -50.0
    spec[40] = -30.0
    panel = _panel_with_spectrum(qapp, spec, center, span)
    mgr = VfoManager()
    panel.vfo_manager = mgr
    v = mgr.add(center, span * 0.1, "NFM")     # 带宽约 0.24MHz ≈ 26 bins
    mgr.set_active_context(v, temporary=False)
    snr = panel.compute_snr_db()
    # 若错误地用全谱峰：会得到 60；带内语义应 ≈ -50 - (-90) = 40
    assert snr == pytest.approx(40.0, abs=0.6)


def test_no_permanent_snr_readout(qapp):
    """SNR 不再常驻顶部（2026-11-10 裁决）；数据侧仍算得出来。"""
    spec = np.full(128, -90.0)
    spec[64] = -45.0
    panel = _panel_with_spectrum(qapp, spec)
    assert not hasattr(panel, "_snr_label"), "SNR 读数又回到顶栏了"
    panel.generator._has_real_data = True
    panel._refresh_status_labels()          # 数据通路挂钩不得抛
    assert panel.compute_snr_db() is not None
    panel.generator.clear_data()
    panel._refresh_status_labels()
    assert panel.compute_snr_db() is None


# ---------------------------------------------------------------------------
# 工具栏：复选框接线 + 断连置灰 + 渲染冒烟
# ---------------------------------------------------------------------------
def test_toolbar_hold_checkboxes_wire_generator(qapp):
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    assert panel.generator.show_max_hold is False
    panel._maxhold_chk.setChecked(True)
    panel._minhold_chk.setChecked(True)
    assert panel.generator.show_max_hold is True
    assert panel.generator.show_min_hold is True
    panel._maxhold_chk.setChecked(False)
    assert panel.generator.show_max_hold is False


def test_hold_controls_enabled_when_disconnected(qapp):
    """断连不改纯显示项：Max/Min 保持是画法开关，断连也必须可改（G12）。

    2026-11-12 用户裁定：旧契约「断连 → 14 个控件一律置灰」本身是缺陷——
    用户没插设备时一项都配不了，且没有任何说明。保持项属于纯显示项，
    断连改的是接入后怎么画；依赖数据的 DSP 项仍必须置灰（这一半保留）。
    """
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel.set_connected(True)
    assert panel._maxhold_chk.isEnabled()
    assert panel._minhold_chk.isEnabled()
    panel.set_connected(False)
    # 纯显示项：断连仍可用
    assert panel._maxhold_chk.isEnabled()
    assert panel._minhold_chk.isEnabled()
    # DSP 项：仍按连接状态置灰（旧契约这部分不许退化）
    assert not panel._fft_combo.isEnabled()
    assert not panel._win_combo.isEnabled()
    assert not panel._avg_combo.isEnabled()
    assert not panel._perf_combo.isEnabled()
    assert not panel._tune_mode_btn.isEnabled()


def test_hold_lines_render_smoke(qapp):
    """开 hold 喂两帧假谱，离屏渲染不崩。"""
    from spectrum_widget import SpectrumPanel
    panel = SpectrumPanel()
    panel.set_connected(True)
    panel._maxhold_chk.setChecked(True)
    panel._minhold_chk.setChecked(True)
    # 先定画布尺寸再喂数据：resizeEvent 会按画布宽重算送绘根数
    # （_apply_render_bins → generator.reconfigure），而 reconfigure 会按设计
    # 清空 max/min 包络。若在喂数据后才首次触发 resize/grab，包络会被重建清掉，
    # 与 hold 本身无关（HEAD 既有行为，2026-11-12 复现）。
    panel._plot.resize(800, 300)
    panel._plot.grab()
    sr = 2.4e6
    rng = np.random.default_rng(3)
    for peak in (2000, 5000):
        t = np.arange(16384)
        iq = (0.02 * np.exp(2j * np.pi * peak / 16384 * t)
              + 0.01 * (rng.standard_normal(16384)
                        + 1j * rng.standard_normal(16384))).astype(np.complex64)
        panel.update_iq(iq, 100e6, sr)
    panel._plot.grab()   # 强制离屏渲染；不抛异常即通过
    assert panel.generator.max_hold is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

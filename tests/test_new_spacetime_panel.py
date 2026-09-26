"""新时空面板 offscreen 测试
================================

验证 desktop/new_spacetime_panel.py 在 offscreen 平台下：
  1. 面板实例化 / show() 不崩；
  2. 初始 AMR 区域显"未检测到信号"，指标为 "--"；
  3. update_amr_result(FM, 0.85) 后标签含 "FM"；
  4. update_iq 喂一段合成 FM 音调后 AMR 区域不再是"未检测到信号"；
  5. 初始授时状态显"未同步"；
  6. FreqOrbitChart 无数据时 paintEvent 可渲染不崩；
  7. FreqOrbitChart 传入 3 个点后 set_data / update 不崩；
  8. 观测站经纬度输入框初始值不是任何硬编码城市坐标；
  9. 面板存在 AMR / 时间线 / 频率轨道三个区域。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_new_spacetime_panel.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# 仓库根加入 sys.path，保证 `from desktop.xxx` / `from mbdsdr_ai.xxx` 可导入
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

from PySide6.QtWidgets import QApplication, QGroupBox  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


from desktop.new_spacetime_panel import (  # noqa: E402
    NewSpacetimePanel, FreqOrbitChart,
)
from mbdsdr_ai.new_spacetime_amr import AMRStreamResult  # noqa: E402
from mbdsdr_ai.new_spacetime_timeline import FreqOrbitPoint  # noqa: E402


# ---------------------------------------------------------------------------
# 1. offscreen 实例化 + show() 不崩
# ---------------------------------------------------------------------------
def test_panel_instantiation_offscreen(qapp):
    panel = NewSpacetimePanel()
    panel.show()
    panel.hide()
    panel.close()


# ---------------------------------------------------------------------------
# 2. 初始 AMR 区域显"未检测到信号"，指标为 "--"
# ---------------------------------------------------------------------------
def test_amr_no_signal_display(qapp):
    panel = NewSpacetimePanel()
    assert panel.amr_mod_label.text() == "未检测到信号"
    assert panel.bw_label.text() == "--"
    assert panel.peak_label.text() == "--"
    assert panel.snr_label.text() == "--"
    assert panel.offset_label.text() == "--"
    assert panel.amr_conf_bar.value() == 0
    panel.close()


# ---------------------------------------------------------------------------
# 3. 直接喂一个 FM 结果，标签应含 "FM"
# ---------------------------------------------------------------------------
def test_amr_update_with_result(qapp):
    panel = NewSpacetimePanel()
    result = AMRStreamResult(
        signal_detected=True,
        modulation="FM",
        confidence=0.85,
        bandwidth_hz=12500.0,
        peak_count=1,
        center_offset_hz=1000.0,
        noise_floor_db=-90.0,
        signal_power_db=-30.0,
        snr_db=60.0,
        spectral_features={},
        raw_label="FM",
    )
    panel.update_amr_result(result)
    assert "FM" in panel.amr_mod_label.text()
    assert panel.amr_conf_bar.value() == 85
    panel.close()


# ---------------------------------------------------------------------------
# 4. update_iq 喂合成 FM 音调，AMR 区域不再是"未检测到信号"
# ---------------------------------------------------------------------------
def test_feed_iq_triggers_analysis(qapp):
    panel = NewSpacetimePanel()
    sr = 1_000_000.0
    n = 8192
    t = np.arange(n) / sr
    f0 = 10_000.0
    # 带轻微频偏的复指数音调（窄带强信号）
    phase = 2 * np.pi * f0 * t + 2 * np.pi * 5000.0 / 1000.0 * np.sin(2 * np.pi * 1000.0 * t)
    iq = np.exp(1j * phase).astype(np.complex128)

    panel.update_iq(iq, sr, center_freq=0.0)
    # 至少不应崩；强音调应被检出，标签离开"未检测到信号"
    assert panel.amr_mod_label.text() != "未检测到信号"
    panel.close()


# ---------------------------------------------------------------------------
# 5. 初始授时状态显"未同步"
# ---------------------------------------------------------------------------
def test_time_sync_unsynchronized_default(qapp):
    panel = NewSpacetimePanel()
    assert "未同步" in panel.time_sync_label.text()
    panel.close()


# ---------------------------------------------------------------------------
# 6. FreqOrbitChart 无数据：resize + grab 触发 paintEvent 不崩
# ---------------------------------------------------------------------------
def test_freq_orbit_chart_no_data(qapp):
    chart = FreqOrbitChart()
    chart.resize(400, 260)
    pix = chart.grab()  # 触发 paintEvent
    assert not pix.isNull()
    chart.close()


# ---------------------------------------------------------------------------
# 7. FreqOrbitChart 传入 3 个点：set_data / update 不崩
# ---------------------------------------------------------------------------
def test_freq_orbit_chart_with_data(qapp):
    chart = FreqOrbitChart()
    chart.resize(400, 260)
    points = [
        FreqOrbitPoint(name="LEO-A", frequency_mhz=137.5, altitude_km=400.0,
                       inclination_deg=51.6, period_min=92.0,
                       eccentricity=0.001, catnr=25544),
        FreqOrbitPoint(name="LEO-B", frequency_mhz=137.1, altitude_km=850.0,
                       inclination_deg=98.7, period_min=102.0,
                       eccentricity=0.0012, catnr=33591),
        FreqOrbitPoint(name="MEO-C", frequency_mhz=1545.0, altitude_km=20200.0,
                       inclination_deg=55.0, period_min=720.0,
                       eccentricity=0.02, catnr=10001),
    ]
    chart.set_data(points)
    chart.update()
    pix = chart.grab()
    assert not pix.isNull()
    assert chart.has_data()
    chart.close()


# ---------------------------------------------------------------------------
# 8. 观测站经纬度输入框初始值不是硬编码城市坐标
# ---------------------------------------------------------------------------
def test_observer_not_hardcoded(qapp):
    panel = NewSpacetimePanel()
    lat = panel.lat_spin.value()
    lon = panel.lon_spin.value()
    # 不允许出现北京 / 长春的硬编码坐标
    assert lat != pytest.approx(39.9042, abs=1e-6), "纬度不得硬编码北京"
    assert lat != pytest.approx(43.8868, abs=1e-6), "纬度不得硬编码长春"
    assert lon != pytest.approx(125.3245, abs=1e-6), "经度不得硬编码长春"
    panel.close()


# ---------------------------------------------------------------------------
# 9. 面板有三个区域（AMR / 时间线 / 频率轨道）
# ---------------------------------------------------------------------------
def test_panel_layout_sections(qapp):
    panel = NewSpacetimePanel()
    assert panel.findChild(QGroupBox, "amrGroup") is not None, "缺少 AMR 区域"
    assert panel.findChild(QGroupBox, "timelineGroup") is not None, "缺少时间线区域"
    assert panel.findChild(QGroupBox, "freqOrbitGroup") is not None, "缺少频率轨道区域"
    # 图表实例存在
    assert isinstance(panel.chart, FreqOrbitChart)
    panel.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

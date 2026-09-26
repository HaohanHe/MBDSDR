#!/usr/bin/env python3
"""
桌面端 UI 跟手度 offscreen 测试
================================

对标 SDR++ 开箱体验，验证控制层/UI 的六大真实差距已补齐：
  T1  频率输入框：直接键入 MHz（如 98.5）回车就跳到正确 Hz
  T2  增益滑杆：拖一下就触发 gain_changed（实时下发，非松手才生效），标 dB
  T3  RSSI：从真 IQ 算功率，值合理（-120~0 dBFS），非假值
  T4  启动自动选源：枚举无设备时状态栏明确提示，不假装连接 / 不走假后端
  T5  模式切换：WFM/NFM/AM/USB/LSB/CW 立刻切解调 + 改 VFO 带宽
  T6  步进选择器：含 1k/10k/100k/1M 档位
  T7  干净断开：_disconnect 后后端释放、控件置灰、无残留

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_desktop_ui_responsiveness.py -v
"""
import os
import sys
import math

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from unittest.mock import patch, MagicMock

# 仓库根 + desktop 目录入 path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

# 进程级事件循环排空：发射信号后调用，确保 queued connections 投递
def _process_events(ms=50):
    QTimer.singleShot(ms, app.quit)
    app.exec()


# ======================================================================
# T1 / T2 / T5 / T6：ControlPanel 直接测试（轻量，无需 MainWindow）
# ======================================================================

class TestFrequencyInput:
    """T1: 频率输入框直接键入 MHz 回车触发 tune 事件带正确 Hz。"""

    def test_type_985_mhz_returns_correct_hz(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        received = []
        cp.tune_sdr_requested.connect(lambda f, m: received.append((f, m)))
        cp.freq_input.setText("98.5")
        cp.freq_input.returnPressed.emit()
        _process_events()
        assert len(received) >= 1
        freq_hz, mode = received[-1]
        assert abs(freq_hz - 98_500_000.0) < 1.0, f"期望 98500000 Hz, 实际 {freq_hz}"

    def test_type_144390_mhz(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        received = []
        cp.tune_sdr_requested.connect(lambda f, m: received.append((f, m)))
        cp.freq_input.setText("144.390")
        cp.freq_input.returnPressed.emit()
        _process_events()
        assert received, "未收到 tune_sdr_requested 信号"
        assert abs(received[-1][0] - 144_390_000.0) < 1.0

    def test_invalid_input_does_not_crash(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        received = []
        cp.tune_sdr_requested.connect(lambda f, m: received.append((f, m)))
        cp.freq_input.setText("abc")
        cp.freq_input.returnPressed.emit()
        _process_events()
        # 解析失败不应发射 tune 信号
        assert len(received) == 0, "无效输入不应触发调谐"


class TestGainSlider:
    """T2: 增益滑杆实时触发 gain_changed，标 dB 数值。"""

    def test_slider_change_emits_gain_immediately(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        gains = []
        cp.gain_changed.connect(lambda db: gains.append(db))
        # valueChanged 在拖动过程中即发射（Qt tracking 默认 True），非松手才生效
        cp.gain_slider.setValue(25)
        _process_events()
        assert 25 in gains, f"增益滑杆设 25 后 gain_changed 未收到 25, 实际 {gains}"
        # dB 标签同步更新
        assert "25" in cp.gain_val.text(), f"增益标签应显示 25dB, 实际 {cp.gain_val.text()}"

    def test_multiple_slider_changes_each_emit(self):
        """拖滑杆经过多个值时每个值都应实时发射（对标 SDR++ 跟手）。"""
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        gains = []
        cp.gain_changed.connect(lambda db: gains.append(db))
        for v in (10, 20, 30, 40):
            cp.gain_slider.setValue(v)
        _process_events()
        # 至少收到 4 次变更（实时下发，不是只在松手时发一次）
        assert len(gains) >= 4, f"期望至少 4 次 gain_changed, 实际 {len(gains)}"
        assert gains[-1] == 40


class TestModeSwitching:
    """T5: 模式切换立刻改 VFO 带宽（逐模式精确值）。"""

    def test_each_mode_has_correct_bandwidth(self):
        from control_panel import ControlPanel, MODE_VFO_BANDWIDTH
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        bw_changes = []
        cp.vfo_bandwidth_changed.connect(lambda bw: bw_changes.append(bw))
        for mode in ["WFM", "FM", "AM", "USB", "LSB", "CW"]:
            bw_changes.clear()
            cp.mode_combo.setCurrentText(mode)
            _process_events()
            expected = MODE_VFO_BANDWIDTH[mode]
            assert bw_changes, f"模式 {mode} 未发射 vfo_bandwidth_changed"
            assert abs(bw_changes[-1] - expected) < 1.0, \
                f"模式 {mode} 期望带宽 {expected}Hz, 实际 {bw_changes[-1]}Hz"

    def test_mode_change_emits_mode_changed(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        modes = []
        cp.mode_changed.connect(lambda m: modes.append(m))
        cp.mode_combo.setCurrentText("USB")
        _process_events()
        assert "USB" in modes


class TestStepSelector:
    """T6: 步进选择器含 1k/10k/100k/1M 档位。"""

    def test_required_steps_present(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        step_values = []
        for i in range(cp.step_combo.count()):
            step_values.append(float(cp.step_combo.itemData(i)))
        for required in [1_000.0, 10_000.0, 100_000.0, 1_000_000.0]:
            assert required in step_values, \
                f"步进选择器缺少 {required}Hz 档位, 实际 {step_values}"

    def test_step_change_emits_signal(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        cp.set_sdr_connected(True)
        steps = []
        cp.step_changed.connect(lambda s: steps.append(s))
        # 选 100kHz
        idx = cp.step_combo.findData(100_000.0)
        assert idx >= 0
        cp.step_combo.setCurrentIndex(idx)
        _process_events()
        assert 100_000.0 in steps


# ======================================================================
# T3 / T4 / T7：MainWindow 集成测试（offscreen，无真实硬件）
# ======================================================================

@pytest.fixture(scope="module")
def main_window():
    """模块级 MainWindow 实例（offscreen）。创建较重，整个模块复用。"""
    from main_window import MainWindow
    mw = MainWindow()
    mw.show()
    _process_events(100)
    yield mw
    # 清理：断开 + 关闭
    try:
        mw._disconnect()
    except Exception:
        pass
    mw.close()


class TestRSSIFromRealIQ:
    """T3: RSSI 从真 IQ 算功率，值合理，非假值。"""

    def test_full_band_iq_power_reasonable(self, main_window):
        """全带宽真 IQ（正弦+噪声）功率应落在 -120~0 dBFS。"""
        # 生成真实复 IQ：-6dBFS 正弦波 + 低噪声
        n = 16384
        t = np.arange(n) / 2_048_000.0
        iq = 0.5 * np.exp(1j * 2 * np.pi * 100_000.0 * t) + \
             0.01 * (np.random.randn(n) + 1j * np.random.randn(n))
        iq = iq.astype(np.complex64)
        # 直接调用 _update_rssi_from_iq（全带宽兜底路径，因为 generator 无 FFT 数据）
        main_window._update_rssi_from_iq(iq)
        _process_events()
        dbfs = main_window._last_dbfs
        assert -120.0 <= dbfs <= 0.0, f"RSSI 应在 [-120, 0] dBFS, 实际 {dbfs}"
        # 0.5 幅度正弦的平均功率 ≈ 0.125 → -9dBFS（加噪声后略高）
        assert dbfs > -30.0, f"强信号 RSSI 应 > -30dBFS, 实际 {dbfs}"

    def test_weak_signal_iq_power(self, main_window):
        """弱信号 IQ 功率应接近噪声底。"""
        n = 16384
        iq = 0.001 * (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64)
        main_window._update_rssi_from_iq(iq)
        _process_events()
        dbfs = main_window._last_dbfs
        assert -120.0 <= dbfs <= 0.0
        # 0.001 幅度噪声功率 ≈ 1e-6 → -60dBFS
        assert dbfs < -40.0, f"弱信号 RSSI 应 < -40dBFS, 实际 {dbfs}"

    def test_rssi_updates_status_bar(self, main_window):
        """RSSI 计算后状态栏信号标签应更新（非 '--'）。"""
        n = 8192
        iq = 0.3 * (np.random.randn(n) + 1j * np.random.randn(n)).astype(np.complex64)
        main_window._update_rssi_from_iq(iq)
        _process_events()
        txt = main_window.status_rssi.text()
        assert "dBFS" in txt, f"状态栏信号应含 dBFS, 实际 {txt}"
        assert "--" not in txt, f"状态栏信号不应为 '--', 实际 {txt}"

    def test_vfo_rssi_returns_none_without_fft_data(self, main_window):
        """无 FFT 数据时 _compute_vfo_rssi_dbfs 返回 None（不造假）。"""
        # 确保 generator 无数据
        main_window.spectrum.generator.clear_data()
        result = main_window._compute_vfo_rssi_dbfs()
        assert result is None, "无 FFT 数据时应返回 None, 不应造假值"


class TestAutoEnumerateNoDevice:
    """T4: 启动自动选源——枚举无设备时明确提示，不假装连接。"""

    def test_no_device_shows_correct_message(self, main_window):
        """枚举返回空列表时，状态栏应显示 RTL-SDR 排查提示，且无后端连接。"""
        main_window._active_sdr_backend = None
        with patch("mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices", return_value=[]):
            main_window._auto_enumerate_and_connect()
        _process_events()
        # 不应创建假后端
        assert main_window._active_sdr_backend is None, \
            "无设备时不应创建任何后端（含假后端）"
        # 状态栏 / 横幅应显示明确提示（含 RTL-SDR / 驱动 / pyrtlsdr 关键词）
        hint_text = main_window._no_device_hint.text()
        assert "RTL-SDR" in hint_text or "pyrtlsdr" in hint_text or "Zadig" in hint_text, \
            f"无设备提示应含 RTL-SDR/驱动/pyrtlsdr, 实际 {hint_text}"

    def test_enumerate_exception_shows_message(self, main_window):
        """枚举抛异常（如 pyrtlsdr 未安装）时也应明确提示，不崩溃。"""
        main_window._active_sdr_backend = None
        with patch("mbdsdr_ai.sdr_backend.enumerate_all_sdr_devices",
                   side_effect=ImportError("No module named 'rtlsdr'")):
            main_window._auto_enumerate_and_connect()
        _process_events()
        assert main_window._active_sdr_backend is None
        hint_text = main_window._no_device_hint.text()
        assert len(hint_text) > 5, "枚举失败时提示不应为空"

    def test_show_no_rtl_hint_updates_ui(self, main_window):
        """_show_no_rtl_hint 应同时更新状态栏和横幅，且保持未连接状态。"""
        msg = "未找到 RTL-SDR，检查 Zadig WinUSB 驱动 / pip install pyrtlsdr"
        main_window._show_no_rtl_hint(msg)
        _process_events()
        # 横幅文字应包含排查关键词（offscreen 下 isVisible 受 tab 状态影响，
        # 这里以文字内容为准；setVisible(True) 已在方法内调用）
        hint_text = main_window._no_device_hint.text()
        assert "RTL-SDR" in hint_text, f"横幅应含 RTL-SDR, 实际 {hint_text}"
        assert main_window.status_conn.text() == "未连接"


class TestCleanDisconnect:
    """T7: 干净断开——stop 流水线、关流、释放后端、控件置灰。"""

    def test_disconnect_clears_backend(self, main_window):
        """_disconnect 后 _active_sdr_backend 应为 None。"""
        # 先塞一个 mock 后端模拟已连接
        mock_be = MagicMock()
        mock_be.device.name = "Mock RTL"
        mock_be.get_status.return_value = MagicMock(connected=True, error="")
        mock_be.get_frequency.return_value = 98.5e6
        mock_be.get_sample_rate.return_value = 2.048e6
        main_window._active_sdr_backend = mock_be
        main_window._panels_set_sdr_connected(True)
        _process_events()
        # 断开
        main_window._disconnect()
        _process_events()
        assert main_window._active_sdr_backend is None, \
            "断开后 _active_sdr_backend 应为 None"
        mock_be.disconnect.assert_called_once()

    def test_disconnect_disables_controls(self, main_window):
        """断开后控制面板调谐控件应置灰（不暴露假可控状态）。"""
        main_window._disconnect()
        _process_events()
        cp = main_window.control_panel
        assert cp.freq_input.isEnabled() is False, \
            "断开后频率输入框应禁用"
        assert cp.gain_slider.isEnabled() is False, \
            "断开后增益滑杆应禁用"
        assert cp.mode_combo.isEnabled() is False, \
            "断开后模式选择应禁用"

    def test_disconnect_stops_pipeline(self, main_window):
        """断开后 _pipeline 应为 None（流水线已 shutdown）。"""
        main_window._disconnect()
        _process_events()
        assert main_window._pipeline is None, \
            "断开后 _pipeline 应为 None"


class TestGetFrequencyHz:
    """ControlPanel.get_frequency_hz() 返回当前内部频率真值。"""

    def test_returns_current_freq(self):
        from control_panel import ControlPanel
        cp = ControlPanel()
        # 默认 98.5 MHz
        assert abs(cp.get_frequency_hz() - 98_500_000.0) < 1.0
        # 调谐后更新
        cp.freq_input.setText("144.390")
        cp.freq_input.returnPressed.emit()
        _process_events()
        assert abs(cp.get_frequency_hz() - 144_390_000.0) < 1.0

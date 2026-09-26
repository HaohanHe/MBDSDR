#!/usr/bin/env python3
"""
MBDSDR 实时接收机地基 offscreen / 合成信号测试
=================================================

用 offscreen Qt 平台 + 数学正确的合成 IQ 信号，验证实时接收机地基的 5 类交互：

  T1 test_click_spectrum_tunes_vfo      —— 单击谱面跳频到点击频率并发 freq_changed
  T2 test_wheel_step_values             —— 滚轮步进 = snapInterval（Shift×10 / Alt×0.1）
  T3 test_keyboard_up_down_fine_tune   —— 上下箭头细调 snap*0.1，左右箭头整步
  T4 test_record_playback_roundtrip    —— save_iq/load_iq 录→放→FM 解调闭环
  T5 test_squelch_mute_unmute          —— 静噪门控：低噪关闭静音、强信号打开出声、滞回关闭
  T6 test_vfo_drag_calls_set_offset    —— VfoManager.push_offset → dsp_vfo.set_offset
  T7 test_bandwidth_defaults_aligned   —— MODE_VFO_BANDWIDTH 各模式默认带宽对齐

设计要点：
  - QT_QPA_PLATFORM=offscreen 必须在 import PySide6 之前设置。
  - REPO_ROOT / sys.path 参照 tests/test_no_sim_regression.py。
  - 合成信号数学正确：FM 用积分相位法（cumsum），不用随机噪声冒充信号。
  - 不修改任何生产代码（desktop/ 或 mbdsdr_ai/）。
"""

import os
import sys

import pytest

# offscreen 平台必须在 import PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import numpy as np  # noqa: E402

from PySide6.QtCore import Qt, QPointF, QPoint  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# 合成信号工具
# ---------------------------------------------------------------------------

def _make_tone_iq(center_hz: float, sr: float, n: int = 8192,
                  tone_offset_hz: float = 1000.0, amp: float = 0.9):
    """生成一段复 IQ：中心处正偏移音调 + 少量噪声（数学正确）。"""
    t = np.arange(n) / sr
    tone = amp * np.exp(2j * np.pi * tone_offset_hz * t)
    rng = np.random.default_rng(42)  # 测试夹具允许用确定性随机噪声
    noise = 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return (tone + noise).astype(np.complex64)


def _make_fm_iq(sr: float, n: int, mod_freq_hz: float = 1000.0,
                deviation_hz: float = 3000.0, amp: float = 0.5):
    """积分相位法生成数学正确的 FM 复 IQ：
        iq = amp * exp(j * 2π * deviation * cumsum(sin(2π*mod_freq*t)) / sr)
    瞬时频偏 = deviation * sin(2π*mod_freq*t)，鉴频后解出 mod_freq 音调。
    """
    t = np.arange(n) / sr
    mod = np.sin(2.0 * np.pi * mod_freq_hz * t)
    phase = 2.0 * np.pi * deviation_hz * np.cumsum(mod) / sr
    return (amp * np.exp(1j * phase)).astype(np.complex64)


def _make_noise_iq(sr: float, n: int, amp: float = 0.001):
    """低电平复高斯噪声（用于静噪关闭场景）。"""
    rng = np.random.default_rng(123)
    return (amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)


def _new_panel(qapp):
    """offscreen 创建 SpectrumPanel 并给绘图表面一个确定尺寸。"""
    from desktop.spectrum_widget import create_spectrum_widget
    panel = create_spectrum_widget()
    # 直接给绘图表面确定尺寸，避免布局未 settle 导致 width()==0
    panel._plot.resize(800, 400)
    return panel


# ---------------------------------------------------------------------------
# T1：单击谱面跳频到点击位置，并发 freq_changed
# ---------------------------------------------------------------------------

def test_click_spectrum_tunes_vfo(qapp):
    center_hz = 98.5e6
    sr = 2.4e6
    panel = _new_panel(qapp)
    panel.generator.center_freq_hz = center_hz
    panel.generator.sample_rate_hz = sr

    # 喂一段合成复 IQ（音调 + 噪声），使 has_data()=True
    iq = _make_tone_iq(center_hz, sr, n=8192, tone_offset_hz=1000.0)
    panel.update_iq(iq, center_hz, sr)
    assert panel.generator.has_data() is True

    # 监听 freq_changed 信号（单位 MHz）
    emitted = []
    panel.freq_changed.connect(lambda f: emitted.append(f))

    # snap 网格默认 10 kHz；点击 x = width*0.75（谱面高度内 y）
    snap = panel._state.snap_interval
    x = panel._plot.width() * 0.75
    y = panel._plot._spectrum_height() * 0.5

    press = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(x, y),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    panel._plot.mousePressEvent(press)
    release = QMouseEvent(QMouseEvent.MouseButtonRelease, QPointF(x, y),
                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    panel._plot.mouseReleaseEvent(release)

    # 期望频率：center - sr/2 + 0.75*sr = center + 0.25*sr，再对齐 snap 网格
    expected_raw = center_hz - sr / 2.0 + 0.75 * sr
    expected = round(expected_raw / snap) * snap

    new_center = panel.generator.center_freq_hz
    assert abs(new_center - expected) <= snap, (
        f"点击后中心应为 {expected/1e6:.4f} MHz（snap={snap}），"
        f"实际 {new_center/1e6:.4f} MHz")

    # freq_changed 应被发射，值为新中心的 MHz
    assert len(emitted) >= 1, "单击谱面后 freq_changed 信号应被发射"
    assert abs(emitted[-1] - new_center / 1e6) < 1e-6, (
        f"freq_changed 应报 {new_center/1e6:.4f} MHz，实际 {emitted[-1]}")

    panel.close()


# ---------------------------------------------------------------------------
# T2：滚轮步进值 = snapInterval（Shift×10 / Alt×0.1）
# ---------------------------------------------------------------------------

def _send_wheel(plot, x, y, delta_y, modifiers):
    event = QWheelEvent(QPointF(x, y), QPointF(x, y),
                        QPoint(0, delta_y), QPoint(0, delta_y),
                        Qt.NoButton, modifiers, Qt.NoScrollPhase, False)
    plot.wheelEvent(event)


def test_wheel_step_values(qapp):
    panel = _new_panel(qapp)
    panel.generator.sample_rate_hz = 2.4e6
    # 喂数据使面板有内容（滚轮本身不查 has_data，但保持面板处于工作态）
    iq = _make_tone_iq(100e6, 2.4e6, n=4096)
    panel.update_iq(iq, 100e6, 2.4e6)

    x = panel._plot.width() * 0.5
    y = panel._plot._spectrum_height() * 0.5

    def baseline():
        # 把中心设到一个各档 snap 都对齐的网格点上
        panel.generator.center_freq_hz = 100.0e6

    # 1) snap=10 kHz → 上滚一格 +10000
    panel._state.set_snap_interval(10_000.0)
    baseline()
    _send_wheel(panel._plot, x, y, 120, Qt.NoModifier)
    assert abs(panel.generator.center_freq_hz - 100.0e6 - 10_000.0) < 1.0, \
        f"snap=10k 上滚应 +10000，实际 {panel.generator.center_freq_hz - 100e6:.1f}"

    # 2) snap=1 kHz → 上滚一格 +1000
    panel._state.set_snap_interval(1_000.0)
    baseline()
    _send_wheel(panel._plot, x, y, 120, Qt.NoModifier)
    assert abs(panel.generator.center_freq_hz - 100.0e6 - 1_000.0) < 1.0, \
        f"snap=1k 上滚应 +1000，实际 {panel.generator.center_freq_hz - 100e6:.1f}"

    # 3) snap=100 kHz → 上滚一格 +100000
    panel._state.set_snap_interval(100_000.0)
    baseline()
    _send_wheel(panel._plot, x, y, 120, Qt.NoModifier)
    assert abs(panel.generator.center_freq_hz - 100.0e6 - 100_000.0) < 1.0, \
        f"snap=100k 上滚应 +100000，实际 {panel.generator.center_freq_hz - 100e6:.1f}"

    # 4) Shift + 滚轮 → 步进 ×10（snap=10k → 100k）
    panel._state.set_snap_interval(10_000.0)
    baseline()
    _send_wheel(panel._plot, x, y, 120, Qt.ShiftModifier)
    assert abs(panel.generator.center_freq_hz - 100.0e6 - 100_000.0) < 1.0, \
        f"Shift+滚轮应 +100000（×10），实际 {panel.generator.center_freq_hz - 100e6:.1f}"

    # 5) Alt + 滚轮 → 步进 ×0.1（snap=10k → 1k）
    panel._state.set_snap_interval(10_000.0)
    baseline()
    _send_wheel(panel._plot, x, y, 120, Qt.AltModifier)
    assert abs(panel.generator.center_freq_hz - 100.0e6 - 1_000.0) < 1.0, \
        f"Alt+滚轮应 +1000（×0.1），实际 {panel.generator.center_freq_hz - 100e6:.1f}"

    panel.close()


# ---------------------------------------------------------------------------
# T3：上下箭头细调 snap*0.1，左右箭头整步
# ---------------------------------------------------------------------------

def test_keyboard_up_down_fine_tune(qapp):
    panel = _new_panel(qapp)
    panel.generator.sample_rate_hz = 2.4e6
    iq = _make_tone_iq(100e6, 2.4e6, n=4096)
    panel.update_iq(iq, 100e6, 2.4e6)

    panel._state.set_snap_interval(10_000.0)
    base = 100.0e6

    def press(key):
        event = QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier)
        panel._plot.keyPressEvent(event)

    # Key_Up → +1000（snap*0.1 细调）
    panel.generator.center_freq_hz = base
    press(Qt.Key_Up)
    assert abs(panel.generator.center_freq_hz - base - 1_000.0) < 1.0, \
        f"Key_Up 应 +1000，实际 {panel.generator.center_freq_hz - base:.1f}"

    # Key_Down → -1000
    panel.generator.center_freq_hz = base
    press(Qt.Key_Down)
    assert abs(panel.generator.center_freq_hz - base + 1_000.0) < 1.0, \
        f"Key_Down 应 -1000，实际 {panel.generator.center_freq_hz - base:.1f}"

    # Key_Left → -10000（整步）
    panel.generator.center_freq_hz = base
    press(Qt.Key_Left)
    assert abs(panel.generator.center_freq_hz - base + 10_000.0) < 1.0, \
        f"Key_Left 应 -10000，实际 {panel.generator.center_freq_hz - base:.1f}"

    # Key_Right → +10000（整步）
    panel.generator.center_freq_hz = base
    press(Qt.Key_Right)
    assert abs(panel.generator.center_freq_hz - base - 10_000.0) < 1.0, \
        f"Key_Right 应 +10000，实际 {panel.generator.center_freq_hz - base:.1f}"

    panel.close()


# ---------------------------------------------------------------------------
# T4：录→放→解调闭环（纯 DSP 层，不依赖 GUI）
# ---------------------------------------------------------------------------

def test_record_playback_roundtrip(tmp_path):
    from mbdsdr_ai.baseband_io import save_iq, load_iq
    from mbdsdr_ai.analog_demod import NarrowbandReceiver

    sr = 480_000.0
    duration = 0.5
    n = int(sr * duration)

    # 数学正确的 FM 信号：1000Hz 调制、3000Hz 频偏
    iq = _make_fm_iq(sr, n, mod_freq_hz=1000.0, deviation_hz=3000.0, amp=0.5)

    path = str(tmp_path / "roundtrip.iq")
    info = save_iq(iq, path, sample_rate=sr, center_freq_hz=100e6, note="t4")
    assert info["samples"] == n, f"写入样本数应为 {n}，实际 {info['samples']}"

    back = load_iq(path)
    assert back["samples"] == n, f"读回样本数应 {n}，实际 {back['samples']}"
    assert abs(back["sample_rate"] - sr) < 1e-6, \
        f"读回采样率应 {sr}，实际 {back['sample_rate']}"

    iq_back = np.array(back["iq"], dtype=np.complex128)

    # FM 解调（静噪全开，避免门控切断音频）
    rx = NarrowbandReceiver(sample_rate=sr, mode="fm")
    rx.set_squelch_db(-150.0)
    out = rx.process(iq_back)
    audio = np.array(out["audio"], dtype=np.float64)
    assert len(audio) > 0, "解调音频不应为空"

    # FFT 找峰值频率（跳过 DC 附近）
    sp = np.abs(np.fft.rfft(audio))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / out["sample_rate"])
    # 跳过前几个 bin（DC/低频哼声）
    skip = max(1, int(round(20.0 / (freqs[1] - freqs[0]))))
    peak_bin = skip + int(np.argmax(sp[skip:]))
    peak_freq = freqs[peak_bin]

    assert 900.0 <= peak_freq <= 1100.0, \
        f"解调音频谱峰应在 1000Hz 附近(900~1100)，实际 {peak_freq:.1f}Hz"


# ---------------------------------------------------------------------------
# T5：静噪门控 mute/unmute（含滞回关闭）
# ---------------------------------------------------------------------------

def test_squelch_mute_unmute():
    from mbdsdr_ai.analog_demod import NarrowbandReceiver

    sr = 480_000.0
    rx = NarrowbandReceiver(mode="fm", sample_rate=sr)
    rx.set_squelch_db(-40.0)

    block = 8192

    # 1) 低电平噪声 → 多块稳定后 squelch_open=False，音频全零
    for _ in range(30):
        noise = _make_noise_iq(sr, block, amp=0.001)
        out = rx.process(noise)
    assert rx._sql_open is False, \
        f"低电平噪声下静噪应关闭，实际 power={rx.last_signal_power_db:.1f} dB"
    assert out["squelch_open"] is False
    audio_noise = np.array(out["audio"], dtype=np.float64)
    assert np.max(np.abs(audio_noise)) < 1e-6, \
        f"静噪关闭时音频应全零，实际 max|audio|={np.max(np.abs(audio_noise)):.2e}"

    # 2) 强 FM 信号（幅度 0.5，1000Hz 调制）→ 多块后 squelch_open=True，音频非零
    for _ in range(30):
        sig = _make_fm_iq(sr, block, mod_freq_hz=1000.0, deviation_hz=3000.0, amp=0.5)
        out = rx.process(sig)
    assert rx._sql_open is True, \
        f"强 FM 信号下静噪应打开，实际 power={rx.last_signal_power_db:.1f} dB"
    assert out["squelch_open"] is True
    audio_sig = np.array(out["audio"], dtype=np.float64)
    assert np.max(np.abs(audio_sig)) > 0.01, \
        f"静噪打开时音频应非零，实际 max|audio|={np.max(np.abs(audio_sig)):.3f}"

    # 3) 切回低电平噪声 → 滞回后 squelch_open 变回 False
    for _ in range(60):
        noise = _make_noise_iq(sr, block, amp=0.001)
        out = rx.process(noise)
    assert rx._sql_open is False, \
        f"切回低噪后静噪应经滞回关闭，实际 power={rx.last_signal_power_db:.1f} dB"
    audio_back = np.array(out["audio"], dtype=np.float64)
    assert np.max(np.abs(audio_back)) < 1e-6, \
        f"静噪再次关闭后音频应全零，实际 max|audio|={np.max(np.abs(audio_back)):.2e}"


# ---------------------------------------------------------------------------
# T6：VfoManager.push_offset → dsp_vfo.set_offset（纯数据层）
# ---------------------------------------------------------------------------

class _MockDspVfo:
    """记录 set_offset 调用次数与参数。"""

    def __init__(self):
        self.call_count = 0
        self.calls = []

    def set_offset(self, offset_hz):
        self.call_count += 1
        self.calls.append(float(offset_hz))


def test_vfo_drag_calls_set_offset():
    from mbdsdr_ai.vfo_manager import VfoManager

    mgr = VfoManager()
    vfo = mgr.add(center_hz=145.8e6, bw_hz=12_500.0, mode="FM")

    dsp = _MockDspVfo()
    assert mgr.bind_dsp(vfo.vfo_id, dsp) is True

    # 第一次 push +100000
    assert mgr.push_offset(vfo.vfo_id, 100_000.0) is True
    assert dsp.call_count == 1, f"set_offset 应被调用 1 次，实际 {dsp.call_count}"
    assert dsp.calls[-1] == 100_000.0, f"参数应为 100000.0，实际 {dsp.calls[-1]}"

    # 第二次 push -50000
    assert mgr.push_offset(vfo.vfo_id, -50_000.0) is True
    assert dsp.call_count == 2, f"set_offset 应被调用 2 次，实际 {dsp.call_count}"
    assert dsp.calls[-1] == -50_000.0, f"最后一次参数应为 -50000.0，实际 {dsp.calls[-1]}"


# ---------------------------------------------------------------------------
# T7：MODE_VFO_BANDWIDTH 各模式默认带宽对齐
# ---------------------------------------------------------------------------

def test_bandwidth_defaults_aligned():
    from desktop.control_panel import MODE_VFO_BANDWIDTH

    assert MODE_VFO_BANDWIDTH["WFM"] == 180_000, MODE_VFO_BANDWIDTH.get("WFM")
    assert MODE_VFO_BANDWIDTH["NFM"] == 12_500, MODE_VFO_BANDWIDTH.get("NFM")
    assert MODE_VFO_BANDWIDTH["FM"] == 12_500, MODE_VFO_BANDWIDTH.get("FM")
    assert MODE_VFO_BANDWIDTH["AM"] == 6_000, MODE_VFO_BANDWIDTH.get("AM")
    assert MODE_VFO_BANDWIDTH["USB"] == 2_400, MODE_VFO_BANDWIDTH.get("USB")
    assert MODE_VFO_BANDWIDTH["LSB"] == 2_400, MODE_VFO_BANDWIDTH.get("LSB")
    assert MODE_VFO_BANDWIDTH["CW"] == 500, MODE_VFO_BANDWIDTH.get("CW")

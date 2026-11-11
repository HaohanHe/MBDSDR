# SPDX-License-Identifier: MIT
"""G15：悬停读数补 SNR，且与峰值提示**数值相等**。

2026-11-12 用户裁定：
  * 同一帧里「峰值 tooltip」与「悬停读数」给出的 SNR 必须是同一个数
    （不是措辞一致，是数值相等）。两处各自再算一次中位数就会出现两个
    「各自正确」的不同数字，比只显示频率更糟；
  * 反证一：构造一帧已知 IQ，两处读数（频率 / dBFS / SNR）逐个比对；
  * 反证二：故意让其中一处换基准，比对必须变红（变异测试）；
  * 无数据 / 带外悬停 → 不出 SNR 段；
  * 绘制层真的落下第二行，且「复用 noise_floor_db」是唯一底噪出口。

不依赖硬件：IQ 为合成复数正弦 + 固定种子噪声，走真实 push_iq 链路。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import spectrum_widget as sw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# 已知 IQ / 读数解析
# ---------------------------------------------------------------------------
def _feed_tone(panel, frames: int = 4) -> None:
    """喂 `frames` 帧已知复正弦（+ 固定种子噪声），让峰跨帧成熟。

    正弦放在 FFT 第 300 根 bin（fft_size 默认 2048），相对中心
    300/2048 × 2.4 MHz ≈ +351.6 kHz，绝不落在带边。
    """
    gen = panel.generator
    n = gen.fft_size
    t = np.arange(n)
    rng = np.random.default_rng(7)
    iq = (0.12 * np.exp(2j * np.pi * 300.0 * t / n)
          + 0.01 * (rng.standard_normal(n)
                    + 1j * rng.standard_normal(n))).astype(np.complex64)
    for _ in range(frames):
        panel.update_iq(iq, 100e6, 2.4e6)


def _panel_with_tone(qapp, frames: int = 4):
    panel = sw.SpectrumPanel()
    panel.set_connected(True)
    _feed_tone(panel, frames)
    return panel


_SNR_RE = re.compile(r"SNR（相对本帧中位）\s*(-?\d+(?:\.\d+)?)\s*dB")
_DBFS_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*dBFS")
_MHZ_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*MHz")


def _one(pattern, text: str) -> float:
    hits = pattern.findall(text)
    assert len(hits) == 1, f"读数里应恰好一个匹配：{text!r}"
    return float(hits[0])


def _hover_texts(state, freq_hz):
    """按 `_draw_hover_readout` 的同一口径取悬停两行。"""
    gen = state.panel.generator
    vc = sw._view_center_hz(state)
    span = gen.span_hz
    ratio = (freq_hz - (vc - span / 2.0)) / span
    state.mouse_x_ratio = ratio
    state.mouse_in_spectrum = True
    hover_freq = vc - span / 2.0 + ratio * span
    val = gen.value_at_freq(hover_freq)
    first, second = sw._fmt_hover_text(hover_freq, val, gen.noise_floor_db())
    return hover_freq, first, second


def _compare_readouts(state) -> int:
    """峰值提示 vs 悬停读数逐项比对；任何一项不等就 AssertionError。"""
    ann = sw._peak_annotations(state)
    assert ann, "已知 IQ 应产出已成熟的峰（否则比对无从谈起）"
    checked = 0
    for freq_hz, db, snr_db, _label in ann:
        hover_freq, first, second = _hover_texts(state, freq_hz)
        assert second, "有数据时悬停必须出 SNR 段"
        peak_text = sw._fmt_peak_text(freq_hz, db, snr_db, verb="跳到")

        peak_snr = _one(_SNR_RE, peak_text)
        hover_snr = _one(_SNR_RE, second)
        assert hover_snr == peak_snr, (
            f"同一帧两处 SNR 不等：峰值 {peak_snr} vs 悬停 {hover_snr}\n"
            f"  峰值提示：{peak_text}\n  悬停读数：{first} / {second}")

        assert _one(_DBFS_RE, first) == _one(_DBFS_RE, peak_text)
        assert _one(_MHZ_RE, first) == pytest.approx(
            _one(_MHZ_RE, peak_text), abs=1e-4)
        assert _one(_MHZ_RE, first) == pytest.approx(
            hover_freq / 1e6, abs=1e-4)
        checked += 1
    return checked


class _RecPainter:
    """只记录 drawText 的画家替身：覆盖 _draw_hover_readout 用到的三个调用。"""

    def __init__(self):
        self.texts = []

    def setPen(self, *_a, **_k):
        pass

    def setFont(self, *_a, **_k):
        pass

    def drawLine(self, *_a, **_k):
        pass

    def drawText(self, point, text):
        self.texts.append((float(point.y()), str(text)))


# ---------------------------------------------------------------------------
# 反证一：已知 IQ 两处读数逐个相等
# ---------------------------------------------------------------------------
def test_bin_freq_roundtrip_is_identity():
    """bin_to_freq 的中点是 (i+0.5)，freq_to_bin 必须 floor 才互逆。

    这是上一条反证暴露的真缺陷：用 round 时每根 bin 上半段都落到下一根，
    悬停在峰值频率上读到的是邻 bin 功率。
    """
    gen = sw.SpectrumDataGenerator(num_bins=512)
    for idx in (0, 1, 75, 331, 510, 511):
        assert gen.freq_to_bin(gen.bin_to_freq(idx)) == idx
    # 区间归属：下半段/上半段都归本 bin，右端点归下一根
    f0 = gen.center_freq_hz - gen.sample_rate_hz / 2.0
    width = gen.sample_rate_hz / gen.num_bins
    assert gen.freq_to_bin(f0 + 75 * width + 0.1 * width) == 75
    assert gen.freq_to_bin(f0 + 75 * width + 0.9 * width) == 75
    assert gen.freq_to_bin(f0 + 76 * width) == 76


def test_hover_snr_equals_peak_tooltip(qapp):
    panel = _panel_with_tone(qapp)
    assert _compare_readouts(panel._state) >= 1


def test_snr_uses_single_noise_floor_source(qapp):
    """悬停与峰值的底噪必须来自同一个 `noise_floor_db()` 返回值。"""
    panel = _panel_with_tone(qapp)
    gen = panel.generator
    ann = sw._peak_annotations(panel._state)
    assert ann
    floor = gen.noise_floor_db()
    assert floor is not None
    freq_hz, db, snr_db, _label = ann[0]
    # 峰值侧：SNR = 功率 − 底噪（find_peaks_with_snr 的写法）
    assert snr_db == pytest.approx(db - floor, abs=1e-6)
    # 悬停侧：同一底噪、同一功率，差必须逐位相同
    val = gen.value_at_freq(freq_hz)
    assert val is not None
    _first, second = sw._fmt_hover_text(freq_hz, val, floor)
    assert _one(_SNR_RE, second) == float(f"{snr_db:.1f}")


# ---------------------------------------------------------------------------
# 反证二：故意换基准必须红（变异测试）
# ---------------------------------------------------------------------------
def test_mutation_hover_uses_shifted_basis_must_fail(qapp, monkeypatch):
    panel = _panel_with_tone(qapp)
    _compare_readouts(panel._state)          # 先确认原实现是绿的
    orig = sw._fmt_hover_text
    monkeypatch.setattr(
        sw, "_fmt_hover_text",
        lambda f, v, floor: orig(f, v, None if floor is None else floor + 3.0))
    with pytest.raises(AssertionError):
        _compare_readouts(panel._state)


def test_mutation_peak_uses_shifted_basis_must_fail(qapp, monkeypatch):
    panel = _panel_with_tone(qapp)
    _compare_readouts(panel._state)
    orig = sw._fmt_peak_text
    monkeypatch.setattr(
        sw, "_fmt_peak_text",
        lambda f, p, snr, verb="峰值": orig(f, p, snr + 3.0, verb=verb))
    with pytest.raises(AssertionError):
        _compare_readouts(panel._state)


# ---------------------------------------------------------------------------
# 无数据 / 带外：不出 SNR 段
# ---------------------------------------------------------------------------
def test_no_data_hover_has_no_snr_segment(qapp):
    panel = sw.SpectrumPanel()               # 未连接、无 IQ
    state = panel._state
    state.mouse_x_ratio = 0.5
    state.mouse_in_spectrum = True
    rec = _RecPainter()
    sw._draw_hover_readout(rec, state, QRectF(0, 0, 300, 200),
                           QColor("white"), QColor("white"))
    assert len(rec.texts) == 1
    assert "SNR" not in rec.texts[0][1]
    assert "--" in rec.texts[0][1]


def test_out_of_band_hover_has_no_snr_segment(qapp):
    """视口整体平移出采集带宽：value_at_freq 返回 None，不得编 SNR。"""
    panel = _panel_with_tone(qapp)
    state = panel._state
    span = panel.generator.span_hz
    state.view_offset_hz = -span
    state.mouse_x_ratio = 0.5
    state.mouse_in_spectrum = True
    rec = _RecPainter()
    sw._draw_hover_readout(rec, state, QRectF(0, 0, 300, 200),
                           QColor("white"), QColor("white"))
    assert len(rec.texts) == 1
    assert "SNR" not in rec.texts[0][1]
    assert "--" in rec.texts[0][1]


# ---------------------------------------------------------------------------
# 绘制：第二行真的落下，且在首行下方
# ---------------------------------------------------------------------------
def test_draw_hover_renders_second_line(qapp):
    panel = _panel_with_tone(qapp)
    state = panel._state
    ann = sw._peak_annotations(state)
    assert ann
    _hover_freq, first, second = _hover_texts(state, ann[0][0])
    assert second
    rec = _RecPainter()
    sw._draw_hover_readout(rec, state, QRectF(0, 0, 300, 200),
                           QColor("white"), QColor("white"))
    assert len(rec.texts) == 2
    assert rec.texts[0][1] == first
    assert rec.texts[1][1] == second
    assert rec.texts[1][0] > rec.texts[0][0]
    assert "SNR（相对本帧中位）" in rec.texts[1][1]


def test_hover_render_smoke(qapp):
    """离屏整条绘制路径不崩（悬停状态由状态位驱动，无需真实鼠标）。"""
    panel = sw.SpectrumPanel()
    panel.set_connected(True)
    panel._plot.resize(800, 300)
    panel._plot.grab()          # 先定画布：resize 会 reconfigure 清峰跟踪
    _feed_tone(panel, 4)
    ann = sw._peak_annotations(panel._state)
    assert ann
    _hover_texts(panel._state, ann[0][0])
    panel._plot.grab()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

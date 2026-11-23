# SPDX-License-Identifier: MIT
"""
频谱帧元数据与「无设备不画假频谱」机械守卫（Task 97）
=====================================================

两件事：

1. ``SpectrumData`` 必须带来源字段 ``source``：``compute_spectrum`` 默认
   ``"live"``，回放/离线样本可显式传 ``"replay"``；多帧路径
   ``compute_waterfall`` 要把来源统一透传给每一帧并写进 ``WaterfallData``，
   不许在多帧里丢来源。
2. 频谱组件「无连接/无数据」时不得进入谱线/峰值绘制分支，更不许用
   ``np.random`` 造高斯假峰。判定集中到 ``SpectrumDataGenerator.has_live_data``
   一个属性上——本文件用「绘制函数调用计数 + 随机函数哨兵」反证，不做像素比对。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_spectrum_frame_meta.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "mbdsdr_ai"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_FFT = 1024
_SR = 2.4e6
_CF = 98.5e6
_TONE_HZ = 200e3


def _iq(n: int = _FFT, seed: int = 3) -> np.ndarray:
    """单音 + 弱噪声：确定性的「真实」合成 IQ，不含随机造假谱。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / _SR
    tone = 0.5 * np.exp(2j * np.pi * _TONE_HZ * t)
    noise = 0.01 * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return (tone + noise).astype(np.complex64)


# ---------------------------------------------------------------------------
# 1. 单帧谱的元数据
# ---------------------------------------------------------------------------
def test_generator_last_frame_clock():
    """/spectrum 快照要算年龄：只有真实帧才置时钟，清空即复位。"""
    import time as _time

    from spectrum_widget import SpectrumDataGenerator

    gen = SpectrumDataGenerator(num_bins=16)
    assert gen.last_frame_monotonic is None
    assert gen.has_live_data is False

    before = _time.monotonic()
    gen.push_iq(_iq(64), _CF, _SR)
    after = _time.monotonic()
    assert gen.has_live_data is True
    assert before <= gen.last_frame_monotonic <= after

    gen.clear_data()
    assert gen.last_frame_monotonic is None
    assert gen.has_live_data is False


def test_single_frame_meta():
    from spectrum_processor import SpectrumProcessor

    proc = SpectrumProcessor(fft_size=_FFT, window="nuttall")
    data = proc.compute_spectrum(_iq(), _CF, _SR)

    assert data.timestamp > 0, "帧时间戳必须是墙钟时间"
    assert len(data.frequencies) == data.fft_size == _FFT
    assert len(data.powers_db) == _FFT
    # 分辨率 = 采样率 / FFT 点数（每一根 bin 的间隔都必须是它）
    resolution = _SR / _FFT
    np.testing.assert_allclose(np.diff(data.frequencies), resolution,
                               rtol=0, atol=resolution * 1e-9)
    assert data.source == "live", "默认来源应为实时链路"


def test_source_replay_is_distinguishable():
    from spectrum_processor import SpectrumProcessor

    proc = SpectrumProcessor(fft_size=_FFT, window="nuttall")
    live = proc.compute_spectrum(_iq(), _CF, _SR)
    replay = proc.compute_spectrum(_iq(), _CF, _SR, source="replay")

    assert live.source == "live"
    assert replay.source == "replay"
    # 只加元数据，不动数值：同一段 IQ 两条路径的功率轴必须一致。
    np.testing.assert_allclose(live.powers_db, replay.powers_db)


# ---------------------------------------------------------------------------
# 2. 多帧路径保留来源
# ---------------------------------------------------------------------------
def test_waterfall_keeps_source_for_every_frame():
    from spectrum_processor import SpectrumProcessor

    proc = SpectrumProcessor(fft_size=_FFT, window="nuttall")
    blocks = [_iq(seed=s) for s in (1, 2, 3)]

    seen = []
    real = proc.compute_spectrum

    def spy(*args, **kwargs):
        seen.append(kwargs.get("source", "live"))
        return real(*args, **kwargs)

    proc.compute_spectrum = spy
    try:
        wf = proc.compute_waterfall(blocks, _CF, _SR, source="replay")
    finally:
        proc.compute_spectrum = real

    assert seen == ["replay"] * len(blocks), f"多帧路径丢了来源：{seen}"
    assert wf.source == "replay"
    assert wf.powers_db.shape == (len(blocks), _FFT)
    assert len(wf.frequencies) == _FFT
    resolution = _SR / _FFT
    np.testing.assert_allclose(np.diff(wf.frequencies), resolution,
                               rtol=0, atol=resolution * 1e-9)


# ---------------------------------------------------------------------------
# 3. 空态守卫：不画谱线 / 不生成随机假峰
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def panel(qapp):
    import spectrum_widget as sw

    p = sw.SpectrumPanel()
    p.resize(900, 500)
    p.show()
    qapp.processEvents()
    yield p
    p.deleteLater()
    qapp.processEvents()


def _render_counts(panel, monkeypatch):
    """渲染一次谱面，返回谱线/峰值绘制函数被调用的次数。"""
    from PySide6.QtGui import QColor, QImage, QPainter

    import spectrum_widget as sw

    counts = {"spectrum": 0, "peaks": 0}

    def _spec(*_a, **_k):
        counts["spectrum"] += 1

    def _peaks(*_a, **_k):
        counts["peaks"] += 1

    monkeypatch.setattr(sw, "_draw_spectrum", _spec)
    monkeypatch.setattr(sw, "_draw_peaks", _peaks)

    w = max(1, panel._fft_plot.width())
    h = max(1, panel._fft_plot.height())
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(sw.PAL_BG))
    painter = QPainter(img)
    try:
        sw._render_spectrum(painter, panel._state, w, h)
    finally:
        painter.end()
    return counts


def _forbid_random(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("无数据时不得用 np.random/gauss 生成假频谱")

    for name in ("normal", "rand", "randn", "random"):
        monkeypatch.setattr(np.random, name, _boom)


def test_disconnected_component_has_no_live_data_and_draws_no_trace(
        panel, monkeypatch):
    gen = panel.generator
    panel._state.show_peaks = True      # 即使峰值显示开着，也必须不画

    assert gen.has_live_data is False
    assert gen.has_data() is False

    _forbid_random(monkeypatch)
    counts = _render_counts(panel, monkeypatch)

    assert counts == {"spectrum": 0, "peaks": 0}, (
        f"无连接时进入了谱线/峰值绘制分支：{counts}")


def test_flag_without_finite_spectrum_is_not_live(panel):
    """只有标志位、谱线全 NaN（未采到）时同样不算有数据。"""
    gen = panel.generator
    gen._has_real_data = True
    gen.spectrum = np.full(gen.num_bins, np.nan, dtype=np.float32)

    assert gen.has_live_data is False


def test_clear_returns_to_offline_guard(panel, monkeypatch):
    gen = panel.generator
    rng = np.random.default_rng(9)
    iq = (rng.standard_normal(gen.fft_size)
          + 1j * rng.standard_normal(gen.fft_size)).astype(np.complex64)
    gen.push_iq(iq, _CF, _SR)
    assert gen.has_live_data is True

    # 正控：有真数据时必须真的进入谱线分支（防「永不绘制」的假修复）
    counts = _render_counts(panel, monkeypatch)
    assert counts["spectrum"] == 1

    gen.clear_data()
    assert gen.has_live_data is False
    assert not np.any(np.isfinite(gen.spectrum)), "清空后不得残留旧谱线"

    _forbid_random(monkeypatch)
    counts = _render_counts(panel, monkeypatch)
    assert counts == {"spectrum": 0, "peaks": 0}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

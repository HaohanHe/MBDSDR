"""
scanner 单测：合成频段含已知单音 → 检测到活动段在正确频率。
=================================================================
对应 docs/learn/sdrpp_modules.md 第 3 节。完全确定性，无硬件。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from mbdsdr_ai.scanner import (
    SweepScanner, extract_active_segments, classify_segment, ActiveSegment,
)


def _make_psd(center_hz: float, bw_hz: float, tones_hz, noise_db: float = -90.0,
              n: int = 256):
    """造一段合成 PSD：底噪 + 若干单音尖峰。返回 (freqs, psd_db)。"""
    freqs = np.linspace(center_hz - bw_hz / 2, center_hz + bw_hz / 2, n)
    psd = np.full(n, noise_db, dtype=float)
    for f0 in tones_hz:
        # 在最接近 f0 的 bin 上抬升 20 dB
        idx = int(np.argmin(np.abs(freqs - f0)))
        psd[idx] = noise_db + 20.0
    return freqs, psd


def test_extract_single_tone():
    freqs, psd = _make_psd(100e6, 1e6, [100.2e6])
    segs = extract_active_segments(freqs, psd, threshold_db=6.0)
    assert len(segs) == 1
    # 峰值应在 100.2 MHz 附近
    assert abs(segs[0].peak_freq - 100.2e6) < (1e6 / 256) * 2


def test_extract_multiple_tones():
    freqs, psd = _make_psd(100e6, 2e6, [100.3e6, 100.8e6])
    segs = extract_active_segments(freqs, psd, threshold_db=6.0)
    assert len(segs) == 2
    peaks = sorted(s.peak_freq for s in segs)
    assert abs(peaks[0] - 100.3e6) < 2e6 / 256 * 2
    assert abs(peaks[1] - 100.8e6) < 2e6 / 256 * 2


def test_no_signal_returns_empty():
    freqs, psd = _make_psd(100e6, 1e6, [])
    segs = extract_active_segments(freqs, psd, threshold_db=6.0)
    assert segs == []


def test_merge_close_segments():
    # 两个很近的单音，用 min_sep 合并
    freqs, psd = _make_psd(100e6, 1e6, [100.1e6, 100.15e6])
    segs = extract_active_segments(freqs, psd, threshold_db=6.0,
                                   min_sep_hz=100e3)
    assert len(segs) == 1


def test_classify_segment():
    seg = ActiveSegment(98e6, 98.2e6, 98.1e6, -30, 200e3)
    kind, conf = classify_segment(seg)
    assert kind == "WFM"
    seg2 = ActiveSegment(144e6, 144.0125e6, 144.006e6, -40, 12.5e3)
    assert classify_segment(seg2)[0] == "NFM"


def test_sweep_scanner_with_provider():
    """端到端：扫 88-108 MHz，步长 2 MHz，provider 每步返回该窗口 PSD。"""
    # 在 98.1 MHz 和 102.5 MHz 放两个单音
    tones = [98.1e6, 102.5e6]

    def provider(center_hz, bw_hz):
        return _make_psd(center_hz, bw_hz * 1.5, tones)

    scanner = SweepScanner(start_hz=88e6, stop_hz=108e6, step_hz=2e6,
                           threshold_db=6.0)
    scanner.psd_provider = provider
    hits = []
    scanner.on_activity = lambda s: hits.append(s)
    segs = scanner.sweep()

    # 应检测到两个活动段（至少两个峰值落在目标频率附近）
    found_peaks = sorted(s.peak_freq for s in segs)
    # 允许一定 bin 误差
    ok = any(abs(p - 98.1e6) < 1e6 for p in found_peaks) and \
         any(abs(p - 102.5e6) < 1e6 for p in found_peaks)
    assert ok, f"未找到预期单音, peaks={found_peaks}"

    # 优先级队列：next_hottest 按 peak_db 排序（这里两者等强，弹一个即可）
    hottest = scanner.next_hottest()
    assert hottest is not None
    assert hottest.peak_db >= -70.0


def test_sweep_without_provider_raises():
    scanner = SweepScanner(start_hz=88e6, stop_hz=108e6)
    with pytest.raises(RuntimeError):
        scanner.sweep()


def test_invalid_range():
    with pytest.raises(ValueError):
        SweepScanner(start_hz=108e6, stop_hz=88e6)

# SPDX-License-Identifier: MIT
"""信道分析器噪声假阳性门（CFAR）——实测定界，不是拍脑袋阈值。

背景：``classify_signal`` 旧实现只要段峰值越过「中位噪声底 + 6 dB」就
给类型，于是单帧 Welch 的指数分布噪声凸起会被标成「FM/模拟」。
本文件把 2026-11-17 的实测边界钉死：

  * 纯复高斯噪声单帧（fft 512/1024/2048，各 400 个种子）共 22,340 个
    越限凸起，峰值抬升最大 13.21 dB → 全部只能是「噪声/未定」；
  * 真实 OTA FM 样本（tests/fixtures/real_fm_88m1，511 帧平均）真正广播
    18.7 dB、三个残余凸起 6.2/7.3/6.5 dB → 后者不得给类型；
  * 谱平坦度必须在线性功率域：单音 ≈0.001、真实 FM 占用带 0.78。
    旧实现在 dB 域上算，任何抬升段都 ≈1.0（退化），形状判据失效。

样本缺失时诚实 skip（无外网/无夹具不能伪造），不写死期望数字。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.channel_analyzer import (  # noqa: E402
    ChannelAnalyzer,
    NOISE_CFAR_P2M_DB,
    classify_signal,
)

FIXTURE_DIR = Path(_ROOT) / "tests" / "fixtures" / "real_fm_88m1"
DATA_PATH = FIXTURE_DIR / "capture.sigmf-data"
SAMPLE_RATE = 1_024_000
CENTER_HZ = 88.1e6

NOISE_LABEL = "噪声/未定"


def _noise_iq(n: int, seed: int, sigma: float = 1e-3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


@pytest.fixture(scope="module")
def real_fm_iq() -> np.ndarray:
    """真实 OTA FM 样本，按 SigMF 声明读取（cu8，中点 127.5）。"""
    if not DATA_PATH.is_file():
        pytest.skip(f"real OTA fixture missing: {DATA_PATH}")
    raw = np.fromfile(DATA_PATH, dtype=np.uint8)
    v = (raw.astype(np.float64) - 127.5) / 127.5
    return v[0::2] + 1j * v[1::2]


# ---------------------------------------------------------------------------
# 1. 门的口径本身
# ---------------------------------------------------------------------------
class TestGateSemantics:
    def test_below_gate_never_gets_a_type(self):
        """门下的段，无论带宽/平坦度如何，都只能是「噪声/未定」。"""
        for bw in (10.0, 500.0, 3000.0, 15000.0, 40000.0, 200000.0):
            for flat in (0.01, 0.5, 0.99):
                assert classify_signal(bw, NOISE_CFAR_P2M_DB - 0.1, flat) == NOISE_LABEL
                assert classify_signal(bw, 6.2, flat) == NOISE_LABEL

    def test_at_or_above_gate_keeps_type(self):
        """门上是既有规则树，门不许把窄带/宽带判据一起吞掉。"""
        assert classify_signal(150, NOISE_CFAR_P2M_DB, 0.3) == "CW/窄带"
        assert classify_signal(30000, NOISE_CFAR_P2M_DB, 0.8) == "数字/宽带"
        assert classify_signal(90000, NOISE_CFAR_P2M_DB, 0.8) == "未知"

    def test_measured_noise_ceiling(self):
        """实测噪声凸起最大 13.21 dB —— 门必须高于它（回归护栏）。"""
        assert NOISE_CFAR_P2M_DB > 13.21


# ---------------------------------------------------------------------------
# 2. 合成纯噪声：单帧（最坏情形）与多帧平均
# ---------------------------------------------------------------------------
class TestPureNoise:
    @pytest.mark.parametrize("fft_size", [512, 1024, 2048])
    def test_single_frame_noise_never_typed(self, fft_size):
        """单帧 Welch 是假阳性最坏情形：越限凸起必有，但一个类型都不许给。

        断言「凸起数 > 0」是必要的：若某天分段阈值被调高到不产生凸起，
        本用例仍应证明门在工作，而不是空转变绿。
        """
        entries = 0
        for seed in range(60):
            r = ChannelAnalyzer(fft_size=fft_size).analyze(
                _noise_iq(fft_size, seed), 2.4e6, 0.0)
            entries += len(r.signals)
            for s in r.signals:
                assert s["type"] == NOISE_LABEL, (
                    f"pure noise spur typed as {s['type']!r} "
                    f"(snr={s['snr_db']:.2f} dB, bw={s['bandwidth_hz']:.0f} Hz)")
        assert entries > 0, "no spur exceeded the 6 dB segmentation threshold - case is vacuous"

    def test_averaged_noise_has_no_entries(self):
        """64 帧平均后噪声谱足够平滑：连凸起都不该留下。"""
        for seed in range(8):
            r = ChannelAnalyzer(fft_size=2048).analyze(
                _noise_iq(131072, seed), 2.4e6, 0.0)
            assert r.signals == []

    def test_occupancy_still_reports_threshold_truthfully(self):
        """门只影响「类型断言」，不许改占用率（它仍是越限 bin 的真实比例）。"""
        r = ChannelAnalyzer(fft_size=2048).analyze(_noise_iq(2048, 3), 2.4e6, 0.0)
        above = float((r.psd_db > r.noise_floor_db + 6.0).mean())
        assert r.occupancy == pytest.approx(above)
        # 单帧噪声下这份比例本身就是噪声，门不许把它压成 0（那才是造假）。
        assert r.occupancy > 0.0


# ---------------------------------------------------------------------------
# 3. 真实 OTA FM 样本：真信号过门、残余凸起不得给类型
# ---------------------------------------------------------------------------
class TestRealOtaFm:
    def test_broadcast_passes_gate_and_spurs_do_not(self, real_fm_iq):
        r = ChannelAnalyzer(fft_size=2048).analyze(real_fm_iq, SAMPLE_RATE, CENTER_HZ)
        assert r.signals, "real capture must yield at least the broadcast segment"

        typed = [s for s in r.signals if s["type"] != NOISE_LABEL]
        assert typed, "the real broadcast must survive the CFAR gate"
        strongest = max(r.signals, key=lambda s: s["snr_db"])
        assert strongest["snr_db"] >= NOISE_CFAR_P2M_DB
        assert strongest["type"] != NOISE_LABEL
        # 广播占用带远宽于任何噪声凸起（实测 343.5 kHz）
        assert strongest["bandwidth_hz"] > 100_000

        # 反过来：任何门下条目都不许带类型（旧实现这里给的是「FM/模拟」）
        for s in r.signals:
            if s["snr_db"] < NOISE_CFAR_P2M_DB:
                assert s["type"] == NOISE_LABEL

    def test_flatness_is_measured_in_power_domain(self, real_fm_iq):
        """真实 FM 占用带谱顶平坦（≈0.78）；单音必须显著更平（≈0.001）。

        这条钉住「平坦度在线性功率域计算」：旧 dB 域实现在两处都给 ≈1.0，
        断言必然失败——即形状判据退化会被本用例抓住。
        """
        r = ChannelAnalyzer(fft_size=2048).analyze(real_fm_iq, SAMPLE_RATE, CENTER_HZ)
        strongest = max(r.signals, key=lambda s: s["snr_db"])
        assert 0.3 < strongest["spectral_flatness"] <= 1.0

        fs = 200_000
        t = np.arange(fs) / fs
        tone = 0.5 * np.exp(2j * np.pi * 5000.0 * t) + _noise_iq(t.size, 11, sigma=0.005)
        rt = ChannelAnalyzer(fft_size=2048).analyze(tone, fs, 100e6)
        single = max(rt.signals, key=lambda s: s["snr_db"])
        assert single["spectral_flatness"] < 0.2
        assert single["spectral_flatness"] < strongest["spectral_flatness"]

    def test_snr_field_matches_peak_minus_floor(self, real_fm_iq):
        """``snr_db`` 是显式暴露的判据来源，必须与 peak/floor 一致。"""
        r = ChannelAnalyzer(fft_size=2048).analyze(real_fm_iq, SAMPLE_RATE, CENTER_HZ)
        for s in r.signals:
            assert s["snr_db"] == pytest.approx(s["peak_power_db"] - r.noise_floor_db)

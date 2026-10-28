# SPDX-License-Identifier: MIT
"""真实 OTA FM 样本入库验证（tests/fixtures/real_fm_88m1）。

背景：缺口分析报告 P1「真实样本未入仓」——35 个 SigMF 全是测试正弦合成、
未入 git。本夹具是**第一份真实射频录制**：2026-10-28 本机 RTL-SDR
（RTL2832U + FC0012）rtl_sdr 实采 88.1 MHz 广播频段，2^19 复样本
（0.512 s @ 1.024 MS/s）cu8，SigMF（.sigmf-data + .sigmf-meta）入仓。

本文件的职责：不是解码电台内容，而是把"这份样本确实是真实射频、元数据
诚实"钉死——防止后人把合成正弦覆盖进来还标 real_ota，也防止 meta 里的
provenance 被改成 synthetic 或被删。

红线：断言只增不减；期望值从样本数据现算（频谱/音频带能量），不写死
拍脑袋数字；夹具缺失时跳过必须带 reason（诚实 skip 仅限缺样本）。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "real_fm_88m1"
DATA_PATH = FIXTURE_DIR / "capture.sigmf-data"
META_PATH = FIXTURE_DIR / "capture.sigmf-meta"

SAMPLE_RATE = 1_024_000
# 夹具截取整 1 MiB cu8 = 2^19 复样本 = 0.512 s（非拍脑袋 0.5 s）
N_SAMPLES = 1 << 19
DURATION_S = N_SAMPLES / SAMPLE_RATE


def _load_iq() -> np.ndarray:
    raw = np.fromfile(DATA_PATH, dtype=np.uint8)
    iq = (raw.astype(np.float64) - 127.5) / 127.5
    return iq[0::2] + 1j * iq[1::2]


@pytest.fixture(scope="module")
def iq() -> np.ndarray:
    if not DATA_PATH.is_file():
        pytest.skip(f"real OTA fixture missing: {DATA_PATH}")
    return _load_iq()


@pytest.fixture(scope="module")
def meta() -> dict:
    if not META_PATH.is_file():
        pytest.skip(f"real OTA fixture meta missing: {META_PATH}")
    return json.loads(META_PATH.read_text(encoding="utf-8"))


class TestProvenance:
    """元数据诚实性：provenance 声明与文件实体互相钉住。"""

    def test_provenance_is_real_ota(self, meta: dict) -> None:
        g = meta["global"]
        assert g["mbdsdr:provenance"] == "real_ota"
        assert "synthetic" not in json.dumps(meta).lower() or \
            "not synthetic" in json.dumps(meta).lower(), \
            "meta must not claim synthetic while provenance=real_ota"

    def test_meta_sample_rate_and_frequency(self, meta: dict) -> None:
        g = meta["global"]
        assert g["core:sample_rate"] == SAMPLE_RATE
        assert g["core:frequency"] == 88_100_000
        assert g["core:datatype"] == "cu8"

    def test_meta_declares_physical_hardware(self, meta: dict) -> None:
        hw = meta["global"]["core:hw"]
        assert "RTL" in hw and "FC0012" in hw
        # 不许把台号编造成已知电台——样本里没解 RDS
        desc = meta["global"]["core:description"].lower()
        assert "unknown" in desc or "未" in desc

    def test_time_source_honest(self, meta: dict) -> None:
        # 无 GNSS 模块时必须如实标 system，不许冒充 gnss
        cap = meta["captures"][0]
        assert cap["mbdsdr:time_source"] == "system"

    def test_data_file_matches_declared_length(self, meta: dict) -> None:
        declared = meta["mbdsdr:validation"]["samples"]
        actual = DATA_PATH.stat().st_size // 2  # cu8 = 2 B/sample
        assert declared == actual == N_SAMPLES


class TestRealSignal:
    """信号真实性：从数据现算，钉住"不是正弦、不是零、不是全噪声"。"""

    def test_sample_count(self, iq: np.ndarray) -> None:
        assert len(iq) == N_SAMPLES

    def test_not_empty_not_constant(self, iq: np.ndarray) -> None:
        # 纯零/常量直流 → 非真实射频
        assert float(np.mean(np.abs(iq) ** 2)) > 1e-4
        assert float(np.std(np.abs(iq))) > 1e-3

    def test_not_clipped(self, iq: np.ndarray) -> None:
        clip_frac = float(np.mean(np.abs(iq) > 0.98))
        assert clip_frac < 0.01, f"capture too hot: clip_frac={clip_frac}"

    def test_signal_above_noise_floor(self, iq: np.ndarray) -> None:
        n = 8192
        n_seg = min(64, len(iq) // n)
        seg = iq[: n * n_seg].reshape(-1, n)
        psd = (np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0)
        med = float(np.median(psd))
        peak_over_med_db = 10.0 * float(np.log10(psd.max() / med))
        # 实测 ~21 dB；留裕量，纯噪声会 <3 dB，合成单音会 >40 dB 且单 bin
        assert 6.0 < peak_over_med_db < 45.0, (
            f"peak/median={peak_over_med_db:.1f} dB "
            "(too flat=noise-like, too sharp=tone-like)"
        )

    def test_not_narrowband_tone(self, iq: np.ndarray) -> None:
        """真实广播 FM 宽带占用：能量摊在多个 bin，不是单音。"""
        n = 8192
        n_seg = min(64, len(iq) // n)
        seg = iq[: n * n_seg].reshape(-1, n)
        psd = (np.abs(np.fft.fft(seg, axis=1)) ** 2).mean(axis=0)
        power = psd.sum()
        top1_frac = float(psd.max() / power)
        top10_frac = float(np.sort(psd)[-10:].sum() / power)
        # 单音: top1≈1.0; 广播 FM: 能量分散, top1 很小
        assert top1_frac < 0.25, f"looks like a tone: top1_frac={top1_frac:.3f}"
        assert top10_frac < 0.80, (
            f"energy too concentrated: top10_frac={top10_frac:.3f}"
        )

    def test_fm_discriminator_audio_band_energy(self, iq: np.ndarray) -> None:
        """正交鉴频后 50 Hz–15 kHz 音频带应含显著能量（实测 ~0.68）。"""
        ph = np.unwrap(np.angle(iq))
        d = np.diff(ph) * (SAMPLE_RATE / (2 * np.pi))
        d = d - d.mean()
        spec = np.abs(np.fft.rfft(d * np.hanning(len(d)))) ** 2
        f = np.fft.rfftfreq(len(d), 1.0 / SAMPLE_RATE)
        band = (f > 50.0) & (f < 15_000.0)
        frac = float(spec[band].sum() / spec[1:].sum())
        # 纯噪声 ~0.03；实测广播 0.68。区间既排噪声也排直流/镜像。
        assert 0.25 < frac < 0.95, f"audio_band_energy_frac={frac:.3f}"

    def test_file_is_not_embedded_text_or_zero_padding(self) -> None:
        """cu8 样本字节应铺满 0..255 的量化层（真实 ADC 直流 127.5 偏置）。"""
        raw = np.fromfile(DATA_PATH, dtype=np.uint8)
        uniq = len(np.unique(raw))
        assert uniq >= 200, f"only {uniq} distinct byte values - looks synthetic"
        # 前 1 MiB 就是全部；不允许尾部全是 0
        assert raw[-1024:].any(), "tail is zero-padded - truncated capture?"

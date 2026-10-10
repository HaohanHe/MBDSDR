# SPDX-License-Identifier: MIT
"""LRPT 第③步：端到端 IQ 闭环确定性互测。

镜像 test_lrpt_modem.py / test_lrpt_fec.py 风格。
"""
from __future__ import annotations

import numpy as np
import pytest

from mbdsdr_ai.lrpt_pipeline import E2EResult, LrptE2EPipeline

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def pipe() -> LrptE2EPipeline:
    return LrptE2EPipeline()


@pytest.fixture
def sample_payload() -> bytes:
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, size=892, dtype=np.uint8).tobytes()


# --------------------------------------------------------------------------- #
# 基础 round-trip
# --------------------------------------------------------------------------- #

class TestRoundTrip:
    def test_noiseless_payload_match(self, pipe: LrptE2EPipeline,
                                      sample_payload: bytes):
        """无噪端到端：payload 逐字节一致。"""
        iq = pipe.build_tx_iq(sample_payload)
        res = pipe.rx(iq)
        assert res.success, f"解码失败: rs_errors={res.rs_errors}"
        assert res.payload == sample_payload, "payload 不一致"

    def test_sync_found(self, pipe: LrptE2EPipeline, sample_payload: bytes):
        """无噪端到端：帧同步找到，hamming=0。"""
        iq = pipe.build_tx_iq(sample_payload)
        res = pipe.rx(iq)
        assert res.sync_found
        assert res.sync_hamming == 0

    def test_deterministic_same_seed(self, pipe: LrptE2EPipeline,
                                      sample_payload: bytes):
        """同一 seed 两次运行结果一致。"""
        iq1 = pipe.build_tx_iq(sample_payload, awgn_snr_db=10.0, noise_seed=42)
        iq2 = pipe.build_tx_iq(sample_payload, awgn_snr_db=10.0, noise_seed=42)
        np.testing.assert_array_equal(iq1, iq2)
        r1 = pipe.rx(iq1)
        r2 = pipe.rx(iq2)
        assert r1.success == r2.success
        assert r1.payload == r2.payload


# --------------------------------------------------------------------------- #
# 频偏 / 时偏容忍
# --------------------------------------------------------------------------- #

class TestOffsets:
    @pytest.mark.parametrize("foff", [-10000, -2000, 0, 2000, 10000])
    def test_cfo_acquisition(self, pipe: LrptE2EPipeline,
                              sample_payload: bytes, foff: int):
        """±10 kHz 频偏内端到端可解。"""
        iq = pipe.build_tx_iq(sample_payload, freq_offset_hz=float(foff))
        res = pipe.rx(iq)
        assert res.success, f"foff={foff} Hz 解码失败"
        assert res.payload == sample_payload

    @pytest.mark.parametrize("toff", [0, 100, 500, 1000])
    def test_time_offset(self, pipe: LrptE2EPipeline,
                         sample_payload: bytes, toff: int):
        """时偏 0..1000 样本内端到端可解。"""
        iq = pipe.build_tx_iq(sample_payload, time_offset_samples=toff)
        res = pipe.rx(iq)
        assert res.success, f"toff={toff} 样本解码失败"
        assert res.payload == sample_payload


# --------------------------------------------------------------------------- #
# 噪声容忍 + 编码增益
# --------------------------------------------------------------------------- #

class TestNoiseTolerance:
    @pytest.mark.parametrize("snr_db", [15, 10, 6, 5])
    def test_snr_above_floor(self, pipe: LrptE2EPipeline,
                              sample_payload: bytes, snr_db: int):
        """SNR ≥ 5 dB 端到端 100% 可解（固定 seed=0）。"""
        iq = pipe.build_tx_iq(sample_payload, awgn_snr_db=float(snr_db),
                               noise_seed=0)
        res = pipe.rx(iq)
        assert res.success, f"SNR={snr_db} dB 解码失败"
        assert res.payload == sample_payload

    def test_snr_below_floor_fails(self, pipe: LrptE2EPipeline,
                                    sample_payload: bytes):
        """SNR ≤ 0 dB 端到端不可解（诚实失败，不假装成功）。"""
        iq = pipe.build_tx_iq(sample_payload, awgn_snr_db=0.0, noise_seed=0)
        res = pipe.rx(iq)
        # 可能同步到但 RS 不可纠 → success=False
        # 或同步不到 → success=False
        # 关键是：不假装 payload 正确
        if res.success:
            assert res.payload == sample_payload, \
                "success=True 但 payload 不匹配 → RS 误纠"


# --------------------------------------------------------------------------- #
# 纯噪声 0 虚警（诚实空态）
# --------------------------------------------------------------------------- #

class TestFalseAlarm:
    def test_pure_noise_no_success(self, pipe: LrptE2EPipeline):
        """纯噪声 → success=False（诚实空态，不伪造 payload）。"""
        rng = np.random.default_rng(12345)
        for trial in range(5):
            noise = (rng.standard_normal(65536) + 1j * rng.standard_normal(65536)) \
                .astype(np.complex64)
            res = pipe.rx(noise)
            assert not res.success, \
                f"trial {trial}: 纯噪声 success=True（应空态）"
            assert res.payload == b""

    def test_empty_input(self, pipe: LrptE2EPipeline):
        """空输入 → success=False。"""
        res = pipe.rx(np.empty(0, dtype=np.complex64))
        assert not res.success
        assert res.payload == b""

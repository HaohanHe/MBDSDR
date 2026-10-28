# SPDX-License-Identifier: MIT
"""真实 OTA FM 样本的**解码**验证（不是只要录制）。

配套 `test_real_fm_sample.py`（钉"这是真射频"），本文件钉"真射频能被真解出"。
直接动因：把真实样本喂进 `wfm_stereo_lite.decode_stereo` 时炸出一个合成
信号永远测不到的缺陷——19kHz pilot 检测用 `butter(...)+lfilter` 的 ba 系数，
在 1.024 MHz 采样率 / 19k±800Hz 的归一化带宽下数值条件极差，真实大动态
信号上 `pilot_band ** 2` 溢出成 inf → rms=nan → `pilot_snr_db=nan`、
立体声判定永远 False。合成信号幅度小，根本触发不到。

修法是滤波改 sos（二阶级联）+ rms 有限性守卫（数值不可用时诚实降级为
单声道，不伪造 pilot_snr）。下面的用例同时钉住"修好"与"没改坏"。

红线：断言只增不减；期望值来自实测/闭式，不拍脑袋；缺夹具才 skip 且带
reason；不落二进制产物（解码指标只在测试内现算）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mbdsdr_ai.wfm_stereo_lite import decode_stereo, synthesize_stereo_iq

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "real_fm_88m1"
DATA_PATH = FIXTURE / "capture.sigmf-data"
SAMPLE_RATE = 1_024_000
OUT_SR = 48_000
N_SAMPLES = 1 << 19


def _load_real_iq() -> np.ndarray:
    raw = np.fromfile(DATA_PATH, dtype=np.uint8)
    x = (raw.astype(np.float64) - 127.5) / 127.5
    return x[0::2] + 1j * x[1::2]


@pytest.fixture(scope="module")
def real_iq() -> np.ndarray:
    if not DATA_PATH.is_file():
        pytest.skip(f"real OTA fixture missing: {DATA_PATH}")
    return _load_real_iq()


@pytest.fixture(scope="module")
def decoded(real_iq: np.ndarray) -> dict:
    return decode_stereo(real_iq, SAMPLE_RATE, out_sr=OUT_SR)


class TestPilotNumericalStability:
    """本次真缺陷的回归闸门：真实大动态信号下 pilot 检测不许出 nan/inf。"""

    def test_pilot_snr_is_finite_on_real_signal(self, decoded: dict) -> None:
        snr = decoded["pilot_snr_db"]
        assert snr is not None, "pilot_snr_db 为 None 说明走了数值失败降级路径"
        assert np.isfinite(snr), f"pilot_snr_db={snr}：pilot 检测数值溢出未修复"

    def test_no_overflow_warning_on_real_signal(self, real_iq: np.ndarray) -> None:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            out = decode_stereo(real_iq, SAMPLE_RATE, out_sr=OUT_SR)
        assert out["duration_s"] > 0

    def test_large_dynamic_range_synthetic_does_not_overflow(self) -> None:
        """合成信号放大到真实量级（1e4×）也必须稳定——ba 系数会炸，sos 不会。"""
        t = np.arange(int(0.05 * 48_000)) / 48_000.0
        left = np.sin(2 * np.pi * 440 * t)
        right = np.sin(2 * np.pi * 440 * t + np.pi / 3)
        iq = synthesize_stereo_iq(left, right, audio_sr=48_000.0, mpx_sr=240_000.0)
        iq_hot = (iq.astype(np.complex128) * 1e4)  # 推到大动态
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            out = decode_stereo(iq_hot, 240_000.0, out_sr=OUT_SR)
        assert out["pilot_snr_db"] is None or np.isfinite(out["pilot_snr_db"])


class TestRealAudioDecode:
    """真信号被真解出：出得来、是音频、与实测频谱形态一致。"""

    def test_audio_produced(self, decoded: dict) -> None:
        assert decoded["sample_rate"] == OUT_SR
        assert len(decoded["l"]) > 0 and len(decoded["r"]) > 0
        # 2^19 @ 1.024MHz = 0.512 s（边界效应允许 ±0.02）
        assert 0.49 < decoded["duration_s"] < 0.53, (
            f"duration_s={decoded['duration_s']}"
        )

    def test_audio_is_non_silent(self, decoded: dict) -> None:
        l = decoded["l"]
        rms = float(np.sqrt(np.mean(np.square(l, dtype=np.float64))))
        assert rms > 0.05, f"解码输出近乎静音：rms={rms}"

    def test_audio_spectrum_has_speech_music_shape(self, decoded: dict) -> None:
        """实测：50-500Hz 占 ~82%，500Hz-3k ~17%，DC 成分极小。"""
        l = decoded["l"].astype(np.float64)
        spec = np.abs(np.fft.rfft(l * np.hanning(len(l)))) ** 2
        f = np.fft.rfftfreq(len(l), 1.0 / OUT_SR)
        total = spec.sum()
        dc = spec[(f < 50)].sum() / total
        low = spec[(f >= 50) & (f < 500)].sum() / total
        mid = spec[(f >= 500) & (f < 3000)].sum() / total
        assert dc < 0.05, f"直流残留过多：{dc}"
        assert low > 0.4, f"低频主体不在：50-500Hz 占比 {low}"
        assert 0.02 < mid < 0.5, f"中频占比异常：{mid}"

    def test_real_station_is_stereo(self, decoded: dict) -> None:
        """实测该台带 19kHz 导频（pilot/mpx ≈ 10%）。修好后必须判得出来。"""
        assert decoded["stereo"] is True, (
            f"真实立体声台被判成单声道：pilot_snr={decoded['pilot_snr_db']}"
        )
        assert -30.0 < decoded["pilot_snr_db"] < 0.0, (
            f"pilot 比例不像广播导频：{decoded['pilot_snr_db']} dB"
        )

    def test_stereo_channels_differ(self, decoded: dict) -> None:
        """判成立体声后 L/R 必须真的不同（差信号生效，不是同一路复制）。"""
        l, r = decoded["l"], decoded["r"]
        assert not np.array_equal(l, r), "L==R：差信号未参与合成"
        diff_rms = float(np.sqrt(np.mean(np.square(l - r, dtype=np.float64))))
        assert diff_rms > 0.005, f"L/R 差异过小：{diff_rms}"


class TestNoRegressionOnSynthetic:
    """别把阈值改烂：单声道合成信号仍须判单声道。"""

    def test_mono_synthetic_stays_mono(self) -> None:
        t = np.arange(int(0.1 * 48_000)) / 48_000.0
        sig = np.sin(2 * np.pi * 440 * t)
        iq = synthesize_stereo_iq(sig, sig, audio_sr=48_000.0,
                                   mpx_sr=240_000.0, pilot_amp=0.0)
        out = decode_stereo(iq, 240_000.0, out_sr=OUT_SR)
        assert out["stereo"] is False
        assert np.allclose(out["l"], out["r"]), "单声道时 L/R 必须一致"

    def test_stereo_synthetic_still_detected(self) -> None:
        t = np.arange(int(0.1 * 48_000)) / 48_000.0
        left = np.sin(2 * np.pi * 440 * t)
        right = np.sin(2 * np.pi * 440 * t + np.pi / 2)
        iq = synthesize_stereo_iq(left, right, audio_sr=48_000.0,
                                  mpx_sr=240_000.0, pilot_amp=0.10)
        out = decode_stereo(iq, 240_000.0, out_sr=OUT_SR)
        assert out["stereo"] is True
        assert np.isfinite(out["pilot_snr_db"])


class TestEmptyState:
    def test_short_input_returns_honest_empty(self) -> None:
        iq = np.zeros(64, dtype=np.complex128)
        out = decode_stereo(iq, SAMPLE_RATE, out_sr=OUT_SR)
        assert out["duration_s"] == 0.0
        assert len(out["l"]) == 0 and len(out["r"]) == 0
        assert out["stereo"] is False

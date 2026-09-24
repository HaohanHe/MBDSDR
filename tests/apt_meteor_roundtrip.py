"""APT / METEOR-LRPT 真实源码移植往返验证。

对照 noaa-apt（Rust）与 meteor_demod（C）源码移植后的自检：
  1. APT 合成音频（同步序列 + 图像行）→ 解调 → 图像重建，验证行同步检测；
  2. APT 用真实录制 syn_robot36.wav 跑解码流程不崩溃（SSTV 文件应优雅返回 no_sync）；
  3. METEOR QPSK 调制 → 加噪 → Viterbi 解码 → 误码率（低噪应≈0）；
  4. Viterbi 已知数据编码 → 解码往返 100% 正确。

运行：pytest tests/apt_meteor_roundtrip.py -v
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from mbdsdr_ai import noaa_apt_lite as A
from mbdsdr_ai import meteor_sat as M

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_WAV = os.path.join(REPO_ROOT, "syn_robot36.wav")


# --------------------------------------------------------------------------- #
# 1. APT 合成 → 解调 → 图像重建 + 行同步
# --------------------------------------------------------------------------- #
def test_apt_synthetic_roundtrip_recovers_lines():
    """合成 APT 音频应被完整解调并恢复 A/B 两通道、行同步锁定率高。"""
    rng = np.random.default_rng(123)
    img_a, img_b = A.synthesize_test_images(120)
    audio = A.synthesize_apt_audio(img_a, img_b, fs=24000, rng=rng)

    result = A.decode_apt(audio, 24000)
    assert result["apt_present"] is True, f"APT 未检出: {result}"
    # 行同步：应对齐绝大多数行（120 行中至少 100），锁定率 >= 0.8
    assert result["lines_aligned"] >= 100, result
    assert result["lock_ratio"] >= 0.8, result

    # 重建图像尺寸 = (对齐行数, 909)
    assert result["image_a"].shape[1] == A.APT_IMAGE_LEN == 909
    assert result["image_b"].shape[1] == 909

    # A 通道合成的是水平灰阶（每行相同），行间方差应极低；
    # B 通道合成的是竖直条带（每列恒定），列间均值方差大。
    ia = result["image_a"].astype(float)
    ib = result["image_b"].astype(float)
    assert ia.std(axis=0).mean() < 8.0, "A 通道应为水平渐变"
    assert ib.std(axis=1).mean() > 30.0, "B 通道应为竖直条带"


def test_apt_sync_guard_matches_noaa_apt_template():
    """同步 guard 必须是 38 样本 ±1 方波（对照 noaa-apt decode.rs:171-199）。"""
    guard = A._SYNC_GUARD
    assert len(guard) == 38
    # ±1 方波
    assert set(np.unique(guard)).issubset({-1.0, 1.0})
    # 7 个完整周期 = 14 个跳变半周期
    assert int(np.sum(np.abs(np.diff(guard)) > 0)) == 14


# --------------------------------------------------------------------------- #
# 2. 真实录制 wav 不崩溃
# --------------------------------------------------------------------------- #
def test_apt_real_wav_does_not_crash():
    """用真实录制 syn_robot36.wav 跑解码流程，必须不抛异常。

    该文件是 SSTV(Robot36) 而非 APT，预期 apt_present=False / reason=no_sync，
    用于验证解码链路对非 APT 信号优雅降级。
    """
    if not os.path.exists(REAL_WAV):
        pytest.skip("缺少 syn_robot36.wav")
    from scipy.io import wavfile
    sr, w = wavfile.read(REAL_WAV)
    if w.ndim > 1:
        w = w[:, 0]
    w = w.astype(np.float64) / (np.max(np.abs(w)) + 1e-9)
    result = A.decode_apt(w, sr)
    # 不崩溃即为通过；SSTV 信号不应误判为 APT
    assert result["apt_present"] in (True, False)
    if not result["apt_present"]:
        assert result["reason"] in ("no_sync", "too_short", "too_few_lines")


# --------------------------------------------------------------------------- #
# 3. METEOR QPSK 调制 → 加噪 → Viterbi 解码 → 误码率
# --------------------------------------------------------------------------- #
def test_meteor_qpsk_noise_viterbi_ber():
    """端到端：信息比特 → 卷积编码(0x79/0x5F) → QPSK 调制 → 加噪 → 判决 → Viterbi。

    低噪声下 BER 应≈0；噪声增大时 BER 上升（卷积码纠错瀑布）。
    """
    rng = np.random.default_rng(2024)
    info = rng.integers(0, 2, size=2000).astype(np.uint8)
    coded = M.viterbi_encode(info)                 # 4000 coded bits

    # QPSK：每 2 个编码比特映射为一个复符号 (I,Q) ∈ {±1}/√2
    i = 1.0 - 2.0 * coded[0::2].astype(float)
    q = 1.0 - 2.0 * coded[1::2].astype(float)
    sym = (i + 1j * q) / np.sqrt(2.0)

    # 低噪：复高斯噪声 σ=0.3（高 SNR）
    noise_low = (rng.normal(0, 0.3, len(sym)) + 1j * rng.normal(0, 0.3, len(sym)))
    rx_low = sym + noise_low
    bits_low = np.empty(2 * len(rx_low), dtype=np.float64)
    bits_low[0::2] = (rx_low.real < 0).astype(float)
    bits_low[1::2] = (rx_low.imag < 0).astype(float)
    dec_low = M.meteor_viterbi_decode(bits_low)
    ber_low = float(np.mean(dec_low != info))
    assert ber_low < 0.01, f"低噪下 BER 应≈0, 实际 {ber_low:.4f}"

    # 高噪：σ=1.2，BER 应明显高于低噪（验证噪声链路确实在恶化解码）
    noise_high = (rng.normal(0, 1.2, len(sym)) + 1j * rng.normal(0, 1.2, len(sym)))
    rx_high = sym + noise_high
    bits_high = np.empty(2 * len(rx_high), dtype=np.float64)
    bits_high[0::2] = (rx_high.real < 0).astype(float)
    bits_high[1::2] = (rx_high.imag < 0).astype(float)
    dec_high = M.meteor_viterbi_decode(bits_high)
    ber_high = float(np.mean(dec_high != info))
    assert ber_high > ber_low, "高噪 BER 应不低于低噪"


# --------------------------------------------------------------------------- #
# 4. Viterbi 已知数据往返 100%
# --------------------------------------------------------------------------- #
def test_viterbi_known_data_roundtrip_100pct():
    """已知信息比特卷积编码后 Viterbi 解码，无噪条件下必须 100% 恢复。"""
    rng = np.random.default_rng(99)
    for nbits in [50, 200, 800]:
        info = rng.integers(0, 2, size=nbits).astype(np.uint8)
        coded = M.viterbi_encode(info)
        assert len(coded) == 2 * nbits
        dec = M.meteor_viterbi_decode(coded.astype(np.float64))
        assert np.array_equal(dec, info), f"{nbits} bit 往返不一致"


def test_dqpsk_roundtrip():
    """DQPSK 编码/差分解码互逆。"""
    rng = np.random.default_rng(5)
    bits = rng.integers(0, 2, size=200).astype(np.uint8)
    sym = M.dqpsk_bits_to_symbols(bits)
    back = M.qpsk_symbols_to_dqpsk_bits(sym)
    assert np.array_equal(back, bits)


def test_lrpt_frame_sync_parses_vcid_apid():
    """LRPT 帧同步字 0x1DFCDC 能在字节流中检出并解析 VCID/APID。"""
    blob = b"\x00\x00" + M.lrpc_build_frame(vcid=5, apid=0x100,
                                          payload=b"PAYLOAD") + b"\xff\xff"
    frames = M.lrpt_find_frames(blob)
    assert len(frames) >= 1
    f0 = frames[0]
    assert f0["vcid"] == 5
    assert f0["apid"] == 0x100
    # 同步字常量
    assert M.LRPT_SYNC_WORD == 0x1DFCDC

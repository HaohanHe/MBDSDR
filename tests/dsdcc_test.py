"""DSDcc lite 往返测试。

对应任务第二步：
  - 4FSK：合成 4FSK 信号 -> 解调 -> 符号序列正确
  - DMR：合成 DMR 帧(同步字+语音帧) -> 同步检测 -> 时隙/语音帧识别
  - P25：合成 P25 帧 -> 同步检测 -> NID(NAC/DUID) 解析
  - 根升余弦：滤波器脉冲响应正确（对照 DSDcc xcoeffs）
  - 参数验证：符号率 4800、alpha=0.2

运行: python -m pytest tests/dsdcc_test.py -v
来源: repos/DSDcc/dsd_{symbol,filters,sync,dmr}.cpp
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import dsdcc_lite as dl  # noqa: E402


# --------------------------------------------------------------------------- #
# 合成工具
# --------------------------------------------------------------------------- #
# dibit -> 频偏电平（与 FourFSKDemod.digitize 的判决一致）
_DIBIT_LEVEL = {0: 1.0, 1: 3.0, 2: -1.0, 3: -3.0}


def dibits_to_discriminator(dibits, sps=dl.SPS_4800, alpha=dl.RRC_ALPHA_DMR):
    """把 dibit 序列合成成判别器输出信号（RRC 脉冲成形）。"""
    levels = np.array([_DIBIT_LEVEL[d] for d in dibits], dtype=float)
    # 零阶保持上采样
    up = np.repeat(levels, sps)
    taps = dl.rrc_impulse_response(sps, alpha)
    sig = np.convolve(up, taps, mode="same")
    return sig


def signs_to_dibits(signs):
    """把 DSDcc 极性同步字 {1,3} 映射成可发射 dibit（1->0=+1, 3->2=-1）。"""
    return [0 if s == 1 else 2 for s in signs]


# --------------------------------------------------------------------------- #
# 参数验证
# --------------------------------------------------------------------------- #
def test_constants():
    assert dl.SYMBOL_RATE_4800 == 4800.0          # dsd_symbol.cpp:41
    assert dl.SPS_4800 == 10                       # dsd_symbol.cpp:52,370
    assert dl.RRC_ALPHA_DMR == 0.2                 # dsd_filters.cpp:29
    assert dl.DMR_TS_DIBITS == 144                 # dmr.h:26 288bit/2
    assert dl.DMR_VOCODER_FRAME_LEN == 72          # dmr.h:34
    assert dl.P25_IMBE_FRAME_BITS == 88            # dsd_mbe.cpp:61


# --------------------------------------------------------------------------- #
# 根升余弦滤波器
# --------------------------------------------------------------------------- #
def test_rrc_impulse_response():
    taps = dl.rrc_impulse_response(sps=10, alpha=0.2, span_symbols=6)
    # 61 抽头 = NZEROS=60（dsd_filters.h:20）
    assert len(taps) == 61
    # 主峰在中心
    peak_idx = int(np.argmax(np.abs(taps)))
    assert peak_idx == 30
    # 能量归一
    assert abs(np.sum(taps ** 2) - 1.0) < 1e-6
    # 与 DSDcc 真实 xcoeffs(alpha=0.2) 形状高度相关。
    # 注：DSDcc xcoeffs 注释标称 Ts=6000 S/s（dsd_filters.cpp:29），与解析公式在
    # 48000/10 下的抽头间隔略有差异，故相关系数阈值取 0.90 而非 0.99。
    ref = np.array(dl.DMR_RRC_COEFFS_ALPHA02)
    corr = np.corrcoef(taps / np.max(np.abs(taps)), ref / np.max(np.abs(ref)))[0, 1]
    assert corr > 0.90, f"RRC 与 DSDcc xcoeffs 相关系数={corr:.3f}"


# --------------------------------------------------------------------------- #
# 4FSK 解调往返
# --------------------------------------------------------------------------- #
def test_fourfsk_roundtrip():
    rng = np.random.default_rng(20260924)
    dibits = rng.integers(0, 4, size=200).tolist()
    sig = dibits_to_discriminator(dibits)

    demod = dl.FourFSKDemod()
    # 用已知符号中心相位：RRC 成形后群时延 = (61-1)/2 = 30 样点
    # 试几个相位找到眼图中心
    best = None
    for phase in range(dl.SPS_4800):
        rec, _ = demod.demodulate(sig, sync_phase=phase)
        # 比较前 190 个符号（忽略卷积边缘）
        n = min(len(rec), len(dibits))
        if n < 100:
            continue
        match = np.mean(rec[:n] == np.array(dibits[:n]))
        if best is None or match > best[1]:
            best = (phase, match)
    assert best is not None
    phase, acc = best
    assert acc > 0.98, f"4FSK 解调正确率 {acc:.3f} @ phase={phase}"


def test_digitize_levels():
    demod = dl.FourFSKDemod()
    demod._max, demod._min = 4.0, -4.0
    demod._center = 0.0
    demod._umid = 2.0
    demod._lmid = -2.0
    # dsd_symbol.cpp:423-450
    assert demod.digitize(+3.0) == 1   # > umid  -> +3
    assert demod.digitize(+1.0) == 0   # (center,umid) -> +1
    assert demod.digitize(-1.0) == 2   # (lmid,center) -> -1
    assert demod.digitize(-3.0) == 3   # < lmid  -> -3


# --------------------------------------------------------------------------- #
# DMR 帧同步 + 语音帧提取
# --------------------------------------------------------------------------- #
def _build_dmr_slot(voice=True):
    """构造一个 144-dibit DMR 时隙：同步字段填 BS voice/data 同步字。"""
    slot = [0] * dl.DMR_TS_DIBITS
    rng = np.random.default_rng(7)
    # 语音帧内容（dibit）
    for i in range(12, 48):
        slot[i] = int(rng.integers(0, 4))
    for i in range(48, 66):
        slot[i] = int(rng.integers(0, 4))
    for i in range(90, 144):
        slot[i] = int(rng.integers(0, 4))
    # 同步字段（66..89）填 BS voice 同步字极性
    sync_pat = dl.DMR_VOICE_BS if voice else dl.DMR_DATA_BS
    for j, s in enumerate(sync_pat):
        slot[dl.DMR_OFF_SYNC + j] = 0 if s == 1 else 2
    return slot


def test_dmr_sync_and_voice_extract():
    slot = _build_dmr_slot(voice=True)
    dec = dl.DMRDecoder()
    res = dec.decode_slot(slot)
    assert res.ok, f"同步未命中 errs={res.sync_errors}"
    assert "BS" in res.burst
    assert res.is_voice is True
    # 3 个 AMBE+2 帧，各 72 bit
    assert len(res.ambe_frames) == 3
    for f in res.ambe_frames:
        assert len(f) == 72
    # 帧内容非全零
    assert any(any(b == 1 for b in f) for f in res.ambe_frames)


def test_dmr_find_sync_in_stream():
    slot = _build_dmr_slot(voice=False)
    # 前面加一段噪声
    noise = [3, 2, 0, 1] * 20
    stream = noise + slot
    sign = dl.dibits_to_sign(np.array(stream, dtype=np.uint8))
    hits = dl.DMRDecoder().find_sync(sign)
    assert len(hits) >= 1
    # 命中位置应在噪声前缀之后
    assert hits[0][0] >= len(noise) - 1
    assert "data" in hits[0][1]


# --------------------------------------------------------------------------- #
# P25 同步检测 + NID(NAC/DUID) 解析
# --------------------------------------------------------------------------- #
def test_p25_sync_detection():
    # 用 P25 同步字极性构造 24 个 dibit
    nid_dibits = signs_to_dibits(dl.P25_SYNC)
    sign = dl.dibits_to_sign(np.array(nid_dibits, dtype=np.uint8))
    hits = dl.P25Decoder().find_sync(sign)
    assert len(hits) >= 1
    assert hits[0][1] <= dl.P25_SYNC_TOL


def test_p25_nid_parse():
    # 手搓 24 dibit NID：NAC=0x293, DUID=0 (语音)
    # dibit 0 -> bits(0,1); dibit 2 -> bits(1,0)。用 0/2 控制位流。
    nac = 0x293  # 12 bit
    duid = 0x0   # 4 bit
    bits = [(nac >> (11 - i)) & 1 for i in range(12)]
    bits += [(duid >> (3 - i)) & 1 for i in range(4)]
    bits += [0] * 32  # 校验位占位
    dibits = []
    for i in range(0, len(bits), 2):
        hi, lo = bits[i], bits[i + 1]
        dibits.append((hi << 1) | lo)
    res = dl.P25Decoder().decode_nid(dibits)
    assert res.ok
    assert res.nac == 0x293, f"NAC={res.nac:#05x}"
    assert res.duid == 0
    assert res.is_voice is True


# --------------------------------------------------------------------------- #
# MBE 参数提取
# --------------------------------------------------------------------------- #
def test_mbe_params_extract():
    ambe = dl.dibits_to_bits([1, 0, 3, 2, 1, 1, 0, 2, 3, 0, 1, 3, 2, 1, 0, 3,
                              2, 0, 1, 3, 2, 1, 0, 3, 2, 1, 0, 2, 3, 1, 0, 3,
                              2, 1, 0, 2])  # 72 bit
    assert len(ambe) == 72
    p = dl.MBEParams(mode="ambe", ambe_bits=ambe)
    s = p.summary()
    assert s["mode"] == "ambe"
    assert s["ambe_len"] == 72
    assert 0 <= s["pitch_index"] <= 255


# --------------------------------------------------------------------------- #
# 一键 dsd_decode_iq
# --------------------------------------------------------------------------- #
def test_dsd_decode_iq_smoke():
    slot = _build_dmr_slot(voice=True)
    sig = dibits_to_discriminator(slot)
    res = dl.dsd_decode_iq(sig, mode="dmr")
    assert res["nsymbols"] > 0
    assert "dmr_syncs" in res

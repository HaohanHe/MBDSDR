# SPDX-License-Identifier: MIT
"""LRPT 真实样本同步层外部真值（2026-11-03 破零）。

样本：TU Berlin lar-tech/satellite-decoder 仓示例实录
`data/meteor_m2_72k.wav`（Meteor-M2 LRPT 72k 过境实录，187.5 kS/s int16
IQ 立体声，105.4 s；SHA-256 c8be339e69db2b192f852c8ab5d2cb7c7b547552
bab41fadd584e0296df0cdf8 钉真值；79MB 第三方字节不入库，按需下载、
离线诚实 skip——FT8/ADS-B golden 同款姿势）。

机制要点（2026-11-03 实测）：
- 原始样本率 187.5 kS/s ÷ 72 ksym/s = 2.604 sps **非整数**，LrptDemodulator
  整数 sps 假设直接失锁（30 s 窗 0 候选）——必须先重采样到整数倍符号率。
- resample_poly(96, 125) → 144 kS/s（恰 2 sps）后，过境中段（52–62 s）
  帧同步锁住：ham=0 完美命中（phase=0，I/Q swap），帧头
  05 0b d1 af 64 76 ba 8c，8 条候选（ham 0/1/3/3/4/…）。

钉扎口径（2026-11-03 协议栈真录破零后）：
- **同步层**（QPSK 解调 + 64-bit 同步字相关）在真实 OTA 上验证：ham=0
  帧存在 + 帧头前缀逐字命中 + 候选数下限 ratchet。
- **解码层**（Viterbi/解扰/RS/VCDU）同日破零：真录软判决位流过完整协议栈
  解出 **2 条 RS 全纠正 VCDU**（4 块全可纠，892B 载荷），ASM 0x1ACFFC1D
  逐字复原。钉扎：VCDU 数地板 + 两条载荷头 32B 逐字 + ASM 约定裁决。

**比特约定裁决（真录实证，2026-11-03）**：真实 Meteor-M2 下行的卷积码
输出位对 I/Q 的映射与本仓合成链相反——解码真录必须对软位流做 **pair-swap**
（c0/c1 互换 ≡ poly1↔poly2）后才能过 Viterbi；合成链自洽（不 swap），
两套约定此前悬而未决（lrpt_fec.py 注释），现由真录裁决。真软判决（符号
幅度）是 RS 纠错边际的关键——硬 ±1 判决下同样帧 RS 直接不可纠。
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mbdsdr_ai.lrpt_modem import LrptDemodulator, SYNC_BITS  # noqa: E402
from mbdsdr_ai.lrpt_fec import (  # noqa: E402
    CADU_SIZE, LrptCaduDecoder, viterbi_decode,
)

WAV_URL = ("https://raw.githubusercontent.com/lar-tech/satellite-decoder/"
           "main/data/meteor_m2_72k.wav")
WAV_SHA256 = ("c8be339e69db2b192f852c8ab5d2cb7c7b547552bab41fadd584e0296df0cdf8")
WAV_NAME = "meteor_m2_72k.wav"

SRC_SR = 187_500
RESAMP_SR = 144_000     # 2 sps @ 72 ksym/s
SYM_RATE = 72_000
WINDOW = (52.0, 62.0)   # 过境中段（实测同步锁住的窗口）
HEAD_PREFIX = "050bd1af"   # ham=0 帧头前 4 字节（同步层真值）


def _cache_path() -> str:
    d = os.path.join(os.path.expanduser("~"), ".mbdsdr", "lrpt_golden")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, WAV_NAME)


def _ensure_wav() -> str:
    path = _cache_path()
    if os.path.isfile(path):
        raw = open(path, "rb").read(1 << 20)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() == WAV_SHA256:
            return path
    try:
        urllib.request.urlretrieve(WAV_URL, path)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LRPT 真实样本不可用（离线？）: {exc}")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != WAV_SHA256:
        os.remove(path)
        raise ValueError("LRPT 真实样本身份漂移（SHA-256 不符）")
    return path


def _window_iq():
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    sr, data = wavfile.read(_ensure_wav())
    assert sr == SRC_SR, f"样本率应为 {SRC_SR}，实际 {sr}"
    iq = (data[:, 0].astype(np.float64)
          + 1j * data[:, 1].astype(np.float64)) / 32768.0
    iq = resample_poly(iq, 96, 125)   # 187.5k -> 144k（整 2 sps）
    t0, t1 = WINDOW
    return iq[int(t0 * RESAMP_SR):int(t1 * RESAMP_SR)]


@pytest.fixture(scope="module")
def cands():
    dem = LrptDemodulator(fs=RESAMP_SR, sym_rate=SYM_RATE)
    return dem.process(_window_iq())


def test_real_recording_syncs_with_hamming_zero(cands):
    """真实 OTA 同步层地板：过境中段必须锁出 ham=0 帧（失守即红）。"""
    best = [c for c in cands if c.hamming == 0]
    assert best, f"真录窗口 {WINDOW}s 内无 ham=0 同步（候选 {len(cands)} 条）"


def test_real_recording_frame_head_matches_truth(cands):
    """ham=0 帧头前缀逐字命中（同步层真值，防解调回归）。"""
    best = [c for c in cands if c.hamming == 0]
    assert best, "无 ham=0 帧"
    head = best[0].frame_bytes[:4].hex()
    assert head == HEAD_PREFIX, \
        f"真录帧头漂移：期望 {HEAD_PREFIX}，实际 {head}"


def test_real_recording_candidate_floor(cands):
    """候选数 ratchet：只增不减（实测 8 条，保守地板 3）。"""
    assert len(cands) >= 3, f"真录候选数回退：{len(cands)} < 3"


# --------------------------------------------------------------------------- #
# 协议栈真录（2026-11-03 破零）：Viterbi → 解扰 → RS → VCDU
# --------------------------------------------------------------------------- #

ASM = bytes([0x1A, 0xCF, 0xFC, 0x1D])
NEED_CODED_BITS = SYNC_BITS + (CADU_SIZE * 8 + 6) * 2

# 真录裁决出的两条 RS 全纠正 VCDU（载荷头 32B 逐字真值）：
PIN_VCDU_HEADS = (
    "407300ecc32df0d3adce120295871f4267552126d223f8989b942ffe5ec7f604",
    "4062000a2608760800020000fddacc73714be1a567413e5b43335792bd927913",
)


def _pair_swap(soft: np.ndarray) -> np.ndarray:
    """c0/c1 位对互换（真录裁决的 Meteor-M2 卷积位→IQ 映射）。"""
    sw = np.empty_like(soft)
    sw[0::2] = soft[1::2]
    sw[1::2] = soft[0::2]
    return sw


@pytest.fixture(scope="module")
def stack():
    """全栈解码结果（模块级缓存，Viterbi 只跑一轮）。

    返回: (valid_payloads, asm_hits) —— valid 是 RS 全纠正的 892B 载荷列表。
    """
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    sr, data = wavfile.read(_ensure_wav())
    iq = (data[:, 0].astype(np.float64)
          + 1j * data[:, 1].astype(np.float64)) / 32768.0
    iq = resample_poly(iq, 96, 125)
    t0, t1 = WINDOW
    win = iq[int(t0 * RESAMP_SR):int(t1 * RESAMP_SR)]

    dem = LrptDemodulator(fs=RESAMP_SR, sym_rate=SYM_RATE)
    win = dem._agc(win.astype(np.complex128))
    cfo = dem._estimate_cfo_4thpower(win)
    if abs(cfo) > 1.0:
        t = np.arange(win.size) / dem.fs
        win = win * np.exp(-1j * 2 * np.pi * cfo * t)

    # 定时偏移扫描：留符号幅度做软判决（硬 ±1 会丢 RS 边际，实测钉死）
    best_syms, best_h = None, 999
    for off in range(dem.sps):
        s = win[off::dem.sps]
        if s.size < 32:
            continue
        b = dem._symbols_to_bits(s)
        if b.size < SYNC_BITS:
            continue
        h = min(int(np.count_nonzero(b[:SYNC_BITS] != hb))
                for hb, _, _ in dem._sync_hypotheses)
        if h < best_h:
            best_h, best_syms = h, s
    assert best_syms is not None, "真录窗口无有效定时"

    soft = np.empty(best_syms.size * 2, dtype=np.float64)
    soft[0::2] = np.real(best_syms)
    soft[1::2] = np.imag(best_syms)

    cands = dem._find_frames(dem._symbols_to_bits(best_syms))
    decoder = LrptCaduDecoder()
    valid = []
    asm_hits = 0
    for c in cands:
        start = c.sync_symbol_index * 2
        end = min(start + NEED_CODED_BITS, soft.size)
        if end - start < NEED_CODED_BITS - 64:
            continue
        sl = soft[start:end]
        # 真录裁决：pair-swap / pair-swap+invert 两变体才出 ASM
        for s in (_pair_swap(sl), -_pair_swap(sl)):
            try:
                info = viterbi_decode(s)
            except Exception:  # noqa: BLE001
                continue
            nbytes = (info.size + 7) // 8
            padded = np.zeros(nbytes * 8, dtype=np.uint8)
            padded[:info.size] = info
            raw = np.packbits(padded).tobytes()
            if raw[:4] != ASM:
                continue
            asm_hits += 1
            try:
                payload, errs = decoder.decode_cadu(raw[:CADU_SIZE])
            except Exception:  # noqa: BLE001
                continue
            if all(e >= 0 for e in errs) and len(payload) == 892:
                valid.append(payload)
    return valid, asm_hits


def test_real_recording_full_stack_vcdu_floor(stack):
    """协议栈真录地板：至少 2 条 RS 全纠正 VCDU（失守即红）。

    真录软位流 → pair-swap → Viterbi → 解扰 → RS(255,223)×4，4 块全可纠
    才算数（诚实锁帧判据）。实测 2 条（2026-11-03）。
    """
    valid, _ = stack
    assert len(valid) >= 2, f"真录协议栈 VCDU 数回退：{len(valid)} < 2"


def test_real_recording_asm_recovered(stack):
    """ASM 0x1ACFFC1D 在真录 Viterbi 输出里逐字复原（协议栈正确性锚）。"""
    _, asm_hits = stack
    assert asm_hits >= 2, f"真录 ASM 命中数回退：{asm_hits} < 2"


def test_real_recording_vcdu_payload_heads_pinned(stack):
    """两条真 VCDU 载荷头 32B 逐字钉死（防解码链语义漂移）。"""
    valid, _ = stack
    heads = {p[:32].hex() for p in valid}
    for pin in PIN_VCDU_HEADS:
        assert pin in heads, f"真录 VCDU 载荷头漂移：{pin} 不在 {heads}"


def test_real_recording_vcdu_header_version(stack):
    """VCDU 主头 version 字段 = 1（CCSDS TM 口径，真录实证）。"""
    valid, _ = stack
    assert valid, "无有效 VCDU"
    for p in valid:
        ver = (p[0] >> 6) & 0x3
        assert ver == 1, f"VCDU version 漂移：{ver}"


def test_pair_swap_convention_adjudicated(stack):
    """比特约定裁决钉扎：真录必须 pair-swap 才能出 ASM（合成链不 swap）。

    这条钉住 lrpt_fec.py 里悬置的「u0 哪端先进寄存器」——真录已裁决：
    真实 Meteor-M2 的卷积位→IQ 映射与本仓合成链相反，解真录必须
    c0/c1 互换。若未来统一约定（改合成链跟真录对齐），此测试需随迁。
    """
    valid, asm_hits = stack
    assert asm_hits >= 2, "约定裁决失守：pair-swap 路径无 ASM"
    assert len(valid) >= 2, "约定裁决失守：pair-swap 路径无全纠正 VCDU"

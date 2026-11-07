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
  帧存在 + 帧头命中真值朝向轨道（8 朝向之一；接收机星座朝向合法变动）+
  候选数下限 ratchet。
- **解码层**（Viterbi/解扰/RS/VCDU）同日破零：真录软判决位流过完整协议栈
  解出 **2 条 RS 全纠正 VCDU**（4 块全可纠，892B 载荷），ASM 0x1ACFFC1D
  逐字复原。钉扎：VCDU 数地板 + 两条载荷头 32B 逐字 + ASM 约定裁决。

**比特约定裁决（真录实证，2026-11-03）**：真实 Meteor-M2 下行的卷积码
输出位对 I/Q 的映射与本仓合成链相反——解码真录必须对软位流做 **pair-swap**
（c0/c1 互换 ≡ poly1↔poly2）后才能过 Viterbi；合成链自洽（不 swap），
两套约定此前悬而未决（lrpt_fec.py 注释），现由真录裁决。真软判决（符号
幅度）是 RS 纠错边际的关键——硬 ±1 判决下同样帧 RS 直接不可纠。

**连帧破层（2026-11-03 续）**：Costas 闭环接入 + 帧同步 FFT 向量化 +
松阈值（ham≤8）捕获与 ASM 验证后，40-80s 宽窗 **16 条 RS 全纠正 VCDU**
（旧锚点窗 2 条）、ASM 命中 91、帧候选 269（≈77% 帧同步可辨）。详见
文件末「连帧破层」段与 capability-map。
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
from mbdsdr_ai.lrpt_fec import CADU_SIZE, LrptCaduDecoder  # noqa: E402

WAV_URL = ("https://raw.githubusercontent.com/lar-tech/satellite-decoder/"
           "main/data/meteor_m2_72k.wav")
WAV_SHA256 = ("c8be339e69db2b192f852c8ab5d2cb7c7b547552bab41fadd584e0296df0cdf8")
WAV_NAME = "meteor_m2_72k.wav"

SRC_SR = 187_500
RESAMP_SR = 144_000     # 2 sps @ 72 ksym/s
SYM_RATE = 72_000
WINDOW = (52.0, 62.0)   # 过境中段（实测同步锁住的窗口）
HEAD_PREFIX = "050bd1af"   # ham=0 帧头前 4 字节（同步层真值，基准朝向）
#: 帧头的 8 朝向轨道（4 相位 × I/Q swap）。帧头原始字节随接收机星座
#: 朝向（Costas 锁相点 / I/Q 定义）在轨道内变动——钉轨道而非单点，
#: 语义级真值由协议栈侧 VCDU 载荷头逐字钉背书（PIN_VCDU_HEADS）。
HEAD_ORBIT = (
    "050bd1af", "0a07e25f", "a0ad48f5", "afa17b05",
    "faf42e50", "f5f81da0", "5f52b70a", "505e84fa",
)


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
    """ham=0 帧头前缀命中真值朝向轨道（同步层真值，防解调回归）。

    帧头字节随接收机星座朝向在 8 朝向轨道内变动（Costas 锁相点不同、
    I/Q 定义不同都合法）；轨道外即回归。轨道 8 个值由 4 相位 × swap
    作用于基准 050bd1af 生成（2026-11-03 实测枚举）。
    """
    best = [c for c in cands if c.hamming == 0]
    assert best, "无 ham=0 帧"
    head = best[0].frame_bytes[:4].hex()
    assert head in HEAD_ORBIT, \
        f"真录帧头漂移：{head} 不在真值朝向轨道 {HEAD_ORBIT}"


def test_real_recording_candidate_floor(cands):
    """候选数 ratchet：只增不减（实测 8 条，保守地板 3）。"""
    assert len(cands) >= 3, f"真录候选数回退：{len(cands)} < 3"


# --------------------------------------------------------------------------- #
# 协议栈真录（2026-11-03 破零）：Viterbi → 解扰 → RS → VCDU
# --------------------------------------------------------------------------- #

ASM = bytes([0x1A, 0xCF, 0xFC, 0x1D])
NEED_CODED_BITS = SYNC_BITS + (CADU_SIZE * 8 + 6) * 2

# 真录真值（2026-11-08 重钉）：40-80s 宽窗 + **order="woven"** + 擦除辅助
# RS + 伴随式验证。**旧值已作废**：原先那两条（407300ec…/4062000a…）经法证
# 是 RS 误纠产物——4 块纠正后重编码与收到码字距离 31-32 字节，远超 t=16，
# 说明纠正结果根本不是合法码字（_probe_vcdu_forensics.log）。
# 新真值有独立协议层背书：帧计数 639186→639195→…→639466 连续递增、
# 图像包 APID 恰为 LRPT 通道 64/65/68。
PIN_VCDU_HEADS = (
    "400509c0d2b4e40046082c283180383cd7ff084426b800590000029621bc0000",
    "400509c0db00000000cc8d29c90f2a658607271d47627a741f89aa17a92449e6",
)


def _pair_swap(soft: np.ndarray) -> np.ndarray:
    """c0/c1 位对互换（真录裁决的 Meteor-M2 卷积位→IQ 映射）。"""
    sw = np.empty_like(soft)
    sw[0::2] = soft[1::2]
    sw[1::2] = soft[0::2]
    return sw


@pytest.fixture(scope="module")
def stack(consecutive):
    """协议栈全栈结果——直接复用连帧 fixture（同一批帧，零额外解调）。

    2026-11-08 合并：此前 stack 用 52-62s 窄窗单独解调一次，consecutive 用
    40-80s 宽窗再解一次。窄窗那 2 条「RS 全纠正 VCDU」经法证是 RS 误纠
    （4 块纠正后重编码与收到码字距离 31-32，远超 t=16），补上伴随式验证后
    归零。故统一到宽窗真值，两处 fixture 只解一次调。

    返回: (valid_payloads, asm_hits)。
    """
    valid, asm_hits, _, _ = consecutive
    return valid, asm_hits


def test_real_recording_full_stack_vcdu_floor(stack):
    """协议栈真录地板：至少 8 条经伴随式验证的 VCDU（实测 10）。

    判据不放宽：仍是「4 块全可纠 + 纠正后伴随式归零」，只是把预算从
    2e ≤ 32 放宽到 2e+f ≤ 32（擦除辅助）。旧口径那个「2 条」已被法证
    证伪（RS 误纠），不构成回退基线。
    """
    valid, _ = stack
    assert len(valid) >= 8, f"真录协议栈 VCDU 数回退：{len(valid)} < 8（实测 10）"


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
    """进入真值集的 VCDU 主头 version 恒为 1（CCSDS TM 口径 + 结构判据生效）。

    这条同时钉两件事：① 真数据本身合法；② 擦除链的 undetected error
    被协议结构判据挡在真值集之外——实测擦除链原始产出 10 条，其中 1 条
    version=2（RS 误纠残留），结构过滤后 9 条全为 1。
    """
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


# --------------------------------------------------------------------------- #
# 连帧破层（2026-11-03 续）：Costas 闭环 + 松阈值捕获 + ASM 验证
# --------------------------------------------------------------------------- #
#
# 旧边界：锚点 ±0.11s 步进仅邻近 2 帧可解（前馈解调残余 CFO → 星座朝向
# 逐帧漂移）。破层机制（本批）：
#   1. Costas 环接入全流（process/demodulate_soft）——载波相位连续跟踪；
#   2. `_find_frames` FFT 向量化——全录逐位滑窗 ~225s 跑不动，向量化后
#      流扫描成为可能；
#   3. **松阈值捕获（ham≤8）+ ASM 验证**——硬判决同步丢卷积编码增益，
#      ham≤4 漏掉 Viterbi 仍可纠的帧；ASM（0x1ACFFC1D）是完美验证器，
#      松阈值假接受（期望 ~0.002/窗）被它归零。
# 实测（40-80s 宽窗）：候选 269（≈77% 帧同步可辨）→ ASM 91 → RS 全纠正
# VCDU **16 条**（旧 10s 窗天花板 2 条）。ham≤10/12 不再增益——天花板
# 是信道软判决质量，不是捕获阈值。RRC 匹配滤波在 sps=2 前馈采样下反而
# 降低 VCDU（10 vs 16），Gardner 原型环 τ 发散（gain 任意符号都漂），
# 二者均诚实留档 lrpt_modem.py/机制笔记，未启用。

CONSEC_WINDOW = (40.0, 80.0)   # 连帧扫描宽窗（过境主体）

#: 连帧 VCDU 载荷头 32B 钉子（流序前 3 条）。
#: **2026-11-08 重钉**：旧钉子（40e30063…/40e60141…/40f200f9…）来自 concat
#: 重组 + 无伴随式验证的解码链，其中至少 1 条已被原作者标注为「疑为 RS
#: 误纠」。补上验证后旧钉子全部不成立（concat 序下头字段本就错位）。
#: 新钉子走 woven 真值序，帧计数 639186→639195→654302 与图像包 APID
#: 64/65/68 双重印证。
PIN_CONSEC_VCDU_HEADS = (
    "400509c0d2b4e40046082c283180383cd7ff084426b800590000029621bc0000",
    "400509c0db00000000cc8d29c90f2a658607271d47627a741f89aa17a92449e6",
    "400509fbde0000000082865f78576090edff9dab8f7fcff215897623857c49eb",
)


def _suppress_ghosts(cands, radius: int = 32):
    """NMS：±radius 符号内只留汉明最优（滑窗 ±1 位鬼影保险）。

    本地实测 269→269 无鬼影（假接受期望 ~0.002，算术一致）；保留为
    跨平台保险，代价可忽略。真帧间隔 8198 符号，radius=32 绝不误伤。
    """
    kept = []
    for c in sorted(cands, key=lambda c: c.hamming):
        if all(abs(c.sync_symbol_index - k.sync_symbol_index) > radius
               for k in kept):
            kept.append(c)
    return kept


@pytest.fixture(scope="module")
def consecutive():
    """连帧全栈解码（模块级缓存）。

    链：宽窗 IQ → demodulate_soft（AGC→粗 CFO→前馈定时→**Costas 闭环**
    →软位流）→ ham≤8 松阈值帧同步 → NMS → pair-swap± → Viterbi →
    解扰 → **擦除辅助 RS(255,223)×4** → **woven 重组**。

    返回: (valid, asm_hits, n_cands, raws) —— valid 为经伴随式验证的 892B
    载荷列表（**流序**，order="woven" 真值序 + 擦除辅助），raws 为
    (sym_idx, raw_cadu) 流序元组（含 4B ASM 的 Viterbi 原始 CADU）。

    2026-11-08 重做：旧版是 concat 重组 + 无伴随式验证，产出的「16 条
    VCDU」经法证全部不成立（纠正后重编码与收到码字距离 31-32 > t=16）。
    现链路的 10 条有独立协议层背书：帧计数连续、图像包 APID 64/65/68。
    """
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    sr, data = wavfile.read(_ensure_wav())
    iq = (data[:, 0].astype(np.float64)
          + 1j * data[:, 1].astype(np.float64)) / 32768.0
    iq = resample_poly(iq, 96, 125)
    t0, t1 = CONSEC_WINDOW
    win = iq[int(t0 * RESAMP_SR):int(t1 * RESAMP_SR)].astype(np.complex128)

    dem = LrptDemodulator(fs=RESAMP_SR, sym_rate=SYM_RATE, costas_bw=0.01)
    soft, cands = dem.demodulate_soft(win, max_hamming=8)
    kept = _suppress_ghosts(cands)

    decoder = LrptCaduDecoder()
    valid = []
    raws = []
    asm_hits = 0
    for c in kept:
        start = c.sync_symbol_index * 2
        end = min(start + NEED_CODED_BITS, soft.size)
        if end - start < NEED_CODED_BITS - 64:
            continue
        sl = soft[start:end]
        for s in (_pair_swap(sl), -_pair_swap(sl)):
            try:
                # 一次拿到字节流 + 每字节可靠性（Viterbi 的 MLM 度量差）
                raw, rel = decoder.decode_viterbi_to_bytes(
                    s, return_reliability=True)
            except Exception:  # noqa: BLE001
                continue
            if raw[:4] != ASM:
                continue
            asm_hits += 1
            try:
                # woven = CCSDS 往返切分真值序；擦除辅助把预算拉到 2e+f ≤ 32
                payload, errs, _used = decoder.decode_cadu_erasure(
                    raw[:CADU_SIZE], rel[:CADU_SIZE], order="woven")
            except Exception:  # noqa: BLE001
                continue
            if all(e >= 0 for e in errs) and len(payload) == 892:
                # 结构判据（协议层，与 RS 层独立）：合法 TM VCDU 的主头
                # version 恒为 1。擦除辅助把预算放宽到 2e+f ≤ 32 后，极小
                # 概率仍会纠到「另一个合法码字」（undetected error，编码
                # 理论极限），此时只能靠协议结构识别——实测 10 条里 1 条
                # version=2 即属此类，剔除后 9 条全 version=1。
                if ((payload[0] >> 6) & 0x3) != 1:
                    continue
                valid.append((c.sync_symbol_index, raw[:CADU_SIZE], payload))
            break
    # 流序输出（kept 按汉明质量排序，不是流序）
    valid.sort(key=lambda v: v[0])
    raws = [(v[0], v[1]) for v in valid]
    return [v[2] for v in valid], asm_hits, len(kept), raws


def test_real_recording_consecutive_vcdu_floor(consecutive):
    """连帧地板：至少 8 条经伴随式验证 + 结构判据的 VCDU（实测 9）。

    对照旧口径：concat + 无验证时报 16 条，但全是 RS 误纠（重编码距离
    31-32 > t=16）。补验证后盲解 0 条，**擦除辅助是把它拉回 9 条的唯一
    原因**（原始产出 10 条，1 条 version=2 被结构判据剔除）——这条地板
    同时是擦除能力的真录证据。
    """
    valid, _, _, _ = consecutive
    assert len(valid) >= 8, f"连帧 VCDU 数回退：{len(valid)} < 8（实测 9）"


def test_real_recording_consecutive_asm_floor(consecutive):
    """连帧 ASM 命中地板：≥80（实测 91）——Viterbi 后 0x1ACFFC1D 逐字复原。"""
    _, asm_hits, _, _ = consecutive
    assert asm_hits >= 80, f"连帧 ASM 命中回退：{asm_hits} < 80（实测 91）"


def test_real_recording_consecutive_frame_density(consecutive):
    """连帧密度地板：宽窗候选 ≥200（实测 269，≈77% 帧同步可辨）。

    钉住「帧被连续找到」而非锚点邻近几帧——连帧能力的直接证据。
    """
    _, _, n_cands, _ = consecutive
    assert n_cands >= 200, f"连帧候选密度回退：{n_cands} < 200（实测 269）"


def test_real_recording_consecutive_heads_pinned(consecutive):
    """连帧 VCDU 载荷头 32B 逐字钉死（按位置前 3 条，防解码链语义漂移）。"""
    valid, _, _, _ = consecutive
    heads = {p[:32].hex() for p in valid}
    for pin in PIN_CONSEC_VCDU_HEADS:
        assert pin in heads, f"连帧 VCDU 载荷头漂移：{pin} 不在 {heads}"


# --------------------------------------------------------------------------- #
# 图像段重组真录链（2026-11-03 深夜破层）：VCDU → M_PDU → 空间包 → MCU → RGB
# --------------------------------------------------------------------------- #
#
# 结构真值：TU Berlin lar-tech/satellite-decoder（同录音第三方解码器）
# extraction.m / jpegdecoding.m / getconfig.m 逐行核对，详见 lrpt_image.py
# 模块 docstring。**比特约定第二处分叉（实证裁决）**：真录 VCDU 重组必须
# decode_cadu(order="woven")（CCSDS 往返切分；TU reedsolomon.m:48-51
# 回织口径）——concat 序下 M_PDU 解析 0 包/FHP 越界，woven 序下 vcduId
# 恒 0x4005、FHP 全域、包结构逐字命中 extraction.m 判据（08-APID-序列-
# 长度-日毫秒-MCU-FFF0-Q）。
#
# 实测（2026-11-08 重钉，40-80s 宽窗，**10 条**经伴随式验证的 VCDU）：
# 10 图像包（APID 64/65/68）、6 个扫描时刻、84 个 8×8 块（21/27/36）、
# RGB (48,1568,3)。覆盖率诚实稀疏（2-3%），缺块填 128 + valid 掩码。
#
# ⚠ 与旧值对比（旧：38 包/13 时刻/386 块/(104,1568,3)）：旧值来自 concat
# 重组 + 无伴随式验证的链路，其 VCDU 已被法证证伪为 RS 误纠。**包数变少
# 不是回退，是把假数据从真值里剔掉了**——旧 38 包里大部分是误纠产生的
# 伪包，恰好通过宽松形状判据。新 10 包的 APID 与帧计数可与 VCDU 层互证。

PIN_FIRST_IMAGE_PKT = (
    "08413fa400d900000296268c00007e0000fff05bfa2f52d2535e558e26b9e064"
)  # 首图像包头 32B（APID=65 seq=16292 日=0 ms=43394700 MCU=126 Q=91）
PIN_FIRST_BLOCK_DC = 139   # 首包首块 DC 系数（整数域，2026-11-08 实测）


@pytest.fixture(scope="module")
def image_chain(consecutive):
    """图像链全栈（直接复用连帧 fixture 的 valid 载荷，零额外解码）。

    2026-11-08 统一：consecutive 的 valid 已用 **order="woven"** + 擦除辅助
    + 伴随式验证产出（真录/CCSDS 往返切分真值序），此处不再二次解码，
    避免「同一批 raw 用两种口径各解一遍」的旧分叉。

    返回: (rgb, channels, istats, pstats, img_pkts)。
    """
    from mbdsdr_ai.lrpt_image import (
        lrpt_packets_to_rgb, parse_image_packet, reassemble_space_packets,
    )

    valid, _, _, _ = consecutive
    pkts, pstats = reassemble_space_packets(valid)
    img_pkts = [ip for p in pkts if (ip := parse_image_packet(p)) is not None]
    rgb, channels, istats = lrpt_packets_to_rgb(img_pkts)
    return rgb, channels, istats, pstats, img_pkts


def test_real_recording_image_packet_floor(image_chain):
    """图像包地板：≥8（实测 10）。

    旧值 38 来自 concat + 误纠链路（见文件头注）；剔除假帧后剩 10 个真包，
    APID 恰为 LRPT 三通道 64/65/68，且与 VCDU 帧计数连续互证。
    """
    _, _, istats, _, _ = image_chain
    assert istats["n_packets"] >= 8, \
        f"图像包回退：{istats['n_packets']} < 8（实测 10）"


def test_real_recording_image_mpdu_structure_sane(image_chain):
    """M_PDU 结构自检：FHP 越界与非图像包都在小量级（woven 结构正确性锚）。

    新真值实测 n_fhp_out_of_range=1、n_skipped=4（10 帧里 1 帧 FHP 越界，
    4 个合法但非图像格式的空间包）。旧口径的 0/0 属于误纠数据的偶然产物，
    不作为基线；此处置地板防结构整体崩坏，而不是要求零瑕疵。
    """
    _, _, _, pstats, _ = image_chain
    assert pstats["n_fhp_out_of_range"] <= 2, \
        f"FHP 越界 {pstats['n_fhp_out_of_range']} 帧——woven 重组口径漂移"
    assert pstats["n_skipped"] <= 8, \
        f"非图像包 {pstats['n_skipped']} 个——包行走/判据漂移"


def test_real_recording_image_geometry(image_chain):
    """图像几何：宽 1568 钉死，高 ≥40（实测 48 = 6 扫描行×8）。"""
    rgb, _, istats, _, _ = image_chain
    assert istats["width"] == 1568
    assert rgb.shape == (istats["height"], 1568, 3)
    assert istats["height"] >= 40, \
        f"图像高回退：{istats['height']} < 40（实测 48）"


def test_real_recording_image_blocks_floor(image_chain):
    """MCU 块地板：三通道合计 ≥60（实测 84 = 21+27+36）。"""
    _, channels, _, _, _ = image_chain
    total = sum(c.n_blocks for c in channels.values())
    assert total >= 60, f"MCU 块回退：{total} < 60（实测 84）"


def test_real_recording_image_first_packet_pinned(image_chain):
    """首图像包字段钉死（包结构真值，防 M_PDU/判据漂移）。"""
    _, _, _, _, img_pkts = image_chain
    assert img_pkts, "无图像包"
    p = img_pkts[0]
    got = (p.apid, p.sequence, p.day, p.ms, p.mcu_index, p.quality)
    assert got == (65, 16292, 0, 43394700, 126, 91), \
        f"首图像包字段漂移：{got}"
    assert p.mcu_bytes[:12].hex() == PIN_FIRST_IMAGE_PKT[40:], \
        f"首包 MCU 数据头漂移：{p.mcu_bytes[:12].hex()}"


def test_real_recording_image_first_block_dc_pinned(image_chain):
    """首包首块 DC 系数钉死（整数域，逐位可复现）。"""
    from mbdsdr_ai.lrpt_image import decode_mcu_packet
    _, _, _, _, img_pkts = image_chain
    blocks = decode_mcu_packet(img_pkts[0].mcu_bytes)
    assert blocks and blocks[0] is not None, "首块未解出"
    dc = int(blocks[0][0])
    assert dc == PIN_FIRST_BLOCK_DC, \
        f"首块 DC 漂移：{dc} != {PIN_FIRST_BLOCK_DC}"

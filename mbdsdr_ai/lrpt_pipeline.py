# SPDX-License-Identifier: MIT
"""LRPT 端到端 IQ 闭环管道（第③步，纯 Python）。

串联第①②步：
  payload → RS 编码 → 交织 → 加扰 → ASM → 卷积编码 → QPSK 调制 → IQ
  → QPSK 解调 → 帧同步 → Viterbi → 解扰 → RS 解码 → payload

另含**真录链**（:func:`decode_recording_to_image`）：真实 SDR 录制的 IQ →
重采样到整数符号倍率 → 软判决解调 → ASM 验证 → 擦除辅助 RS → woven 重组
→ M_PDU 空间包 → MCU 熵解码 → RGB。该链路的口径由第三方真样本钉死
（见 tests/test_lrpt_meteor_real_golden.py，2026-11-07 实测 10 条 VCDU）。

机制证据见 weather-sat-digital-study.md；本文件只做串联，不重复实现算法。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import numpy as np

from .lrpt_modem import (
    SPS,
    SYNC_BITS,
    LrptDemodulator,
    LrptModulator,
)
from .lrpt_fec import (
    CADU_SIZE,
    LrptCaduDecoder,
    convolve_encode,
    viterbi_decode,
)

__all__ = [
    "LrptE2EPipeline",
    "E2EResult",
    "RecordingImageResult",
    "decode_recording_to_image",
    "summarize_rs_errors",
]

#: 真录链的固定符号率与 ASM（Meteor-M2 LRPT 72 ksym/s，CCSDS TM 同步字）
RECORDING_SYM_RATE = 72_000
CADU_ASM = bytes([0x1A, 0xCF, 0xFC, 0x1D])


@dataclass
class E2EResult:
    """端到端解码结果（诚实空态：失败时 payload=b''）。"""
    success: bool = False
    payload: bytes = b""
    sync_found: bool = False
    sync_symbol_index: int = -1
    sync_hamming: int = -1
    rs_errors: list = field(default_factory=list)
    decoded_cadu_len: int = 0


class LrptE2EPipeline:
    """LRPT 端到端 IQ 闭环（合成 → 传输 → 解调 → 解码）。

    用法::

        pipe = LrptE2EPipeline()
        iq = pipe.build_tx_iq(payload, freq_offset_hz=2000, awgn_snr_db=5)
        result = pipe.rx(iq)
        assert result.success
        assert result.payload == payload
    """

    def __init__(self) -> None:
        self.mod = LrptModulator()
        self.demod = LrptDemodulator(max_hamming=4)
        self.decoder = LrptCaduDecoder()

    # ------------------------------------------------------------------ #
    # 发射端：payload → CADU → 卷积编码 → QPSK → IQ
    # ------------------------------------------------------------------ #
    def build_cadu(self, payload: bytes) -> bytes:
        """payload (892B) → CADU (1024B)：RS 编码 + 交织 + 加扰 + ASM。

        切块约定 = **CCSDS 131.0 往返切分**（``payload[j] → 块 j%4``）。
        旧实现按「连续 223 字节切 4 块」（``payload[i*223:(i+1)*223]``），
        编码器与自己的 concat 解码自洽，所以合成链回环恒绿；但真机不是这么
        发的——Meteor-M2 真录只有按往返序解才出得来连续帧计数与合法 VCDU
        （2026-11-07 实证：concat 帧计数乱、图像包 0 个；woven 计数
        639186→…→639466 连续、APID 64/65/68 齐出）。合成链必须跟标准走，
        否则自家 fixture 永远对不上真机。
        """
        if len(payload) != 892:
            raise ValueError(f"payload 须 892 字节（223×4），收到 {len(payload)}")
        blocks = [bytes(payload[b::4]) for b in range(4)]
        encoded = [self.decoder.rs.encode(b) for b in blocks]
        data = self.decoder._interleave_4(encoded)
        scrambled = self.decoder.scrambler.scramble(data)
        asm = bytes([0x1A, 0xCF, 0xFC, 0x1D])
        return asm + scrambled

    def build_tx_iq(self, payload: bytes, *,
                    freq_offset_hz: float = 0.0,
                    phase_offset_rad: float = 0.0,
                    time_offset_samples: int = 0,
                    awgn_snr_db: Optional[float] = None,
                    noise_seed: int = 20261010) -> np.ndarray:
        """payload → 复基带 IQ（完整 LRPT 帧）。"""
        cadu = self.build_cadu(payload)
        cadu_bits = np.unpackbits(np.frombuffer(cadu, dtype=np.uint8))
        coded_bits = convolve_encode(cadu_bits)
        # QPSK 调制：coded_bits → 复符号 → IQ
        sym = self.mod.bits_to_symbols(coded_bits)
        iq = np.repeat(sym, SPS).astype(np.complex128)

        n = iq.size
        t = np.arange(n, dtype=np.float64) / self.mod.fs
        if phase_offset_rad:
            iq = iq * np.exp(1j * phase_offset_rad)
        if freq_offset_hz:
            iq = iq * np.exp(1j * 2 * np.pi * freq_offset_hz * t)
        if time_offset_samples > 0:
            iq = np.concatenate([np.zeros(time_offset_samples, dtype=complex), iq])
        if awgn_snr_db is not None:
            rng = np.random.default_rng(noise_seed)
            sig_pow = float(np.mean(np.abs(iq) ** 2))
            sigma = np.sqrt(sig_pow / (10 ** (awgn_snr_db / 10)))
            noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * (sigma / np.sqrt(2))
            iq = iq + noise
        return iq.astype(np.complex64)

    # ------------------------------------------------------------------ #
    # 接收端：IQ → 解调 → 帧同步 → Viterbi → 解扰 → RS → payload
    # ------------------------------------------------------------------ #
    def rx(self, iq: np.ndarray) -> E2EResult:
        """对一段 IQ 做完整端到端接收。"""
        result = E2EResult()
        iq = np.asarray(iq, dtype=np.complex128)
        if iq.size < SPS * 64:
            return result

        # 1. AGC + CFO 粗校 + 定时恢复（复用第①轮）
        iq = self.demod._agc(iq)
        cfo = self.demod._estimate_cfo_4thpower(iq)
        if abs(cfo) > 1.0:
            t = np.arange(iq.size, dtype=np.float64) / self.demod.fs
            iq = iq * np.exp(-1j * 2 * np.pi * cfo * t)

        # 2. 前馈定时恢复：得到全量硬判决 bits
        bits, _off = self.demod._recover_symbols(iq)
        if bits.size < SYNC_BITS + 100:
            return result

        # 3. 帧同步：找 64-bit 同步字位置
        frames = self.demod._find_frames(bits)
        if not frames:
            return result
        best = frames[0]
        result.sync_found = True
        result.sync_symbol_index = best.sync_symbol_index
        result.sync_hamming = best.hamming

        # 4. 从同步位置开始取软/硬 coded bits（硬判决 ±1.0）
        #    同步字 64 bit + 后续所需位
        start_bit = best.sync_symbol_index * 2
        needed_bits = SYNC_BITS + (CADU_SIZE * 8 + 6) * 2  # coded bits for CADU + tail
        end_bit = min(start_bit + needed_bits, bits.size)
        coded_bits_hard = bits[start_bit:end_bit]
        soft = coded_bits_hard.astype(np.float64) * 2 - 1  # 0→-1, 1→+1

        # 5. Viterbi 译码
        try:
            decoded_bits = viterbi_decode(soft)
        except Exception:
            return result

        # 6. 组回 CADU 字节
        nbytes = (decoded_bits.size + 7) // 8
        padded = np.zeros(nbytes * 8, dtype=np.uint8)
        padded[:decoded_bits.size] = decoded_bits
        decoded_cadu = np.packbits(padded).tobytes()
        result.decoded_cadu_len = len(decoded_cadu)

        # 7. 解扰 + RS 解码
        if len(decoded_cadu) < CADU_SIZE:
            return result
        payload, errors = self.decoder.decode_cadu(decoded_cadu)
        result.rs_errors = errors

        if all(e >= 0 for e in errors):
            result.success = True
            result.payload = payload
        return result


# ---------------------------------------------------------------------- #
# 真录链：真实 SDR 录制 IQ → 图像（2026-11-11 接线，工单 P1-23）
# ---------------------------------------------------------------------- #
@dataclass
class RecordingImageResult:
    """真录 IQ → 图像结果（诚实空态：失败时 rgb=None，并给阶段化原因）。"""
    success: bool = False
    rgb: Optional[np.ndarray] = None
    payloads: List[bytes] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)
    stage: str = "init"
    error: str = ""


def _pair_swap(soft: np.ndarray) -> np.ndarray:
    """c0/c1 位对互换（真录裁决的 Meteor-M2 卷积位→IQ 映射）。

    本仓合成链不 swap 自洽；真实 OTA 必须 swap 后才能过 Viterbi
    （tests/test_lrpt_meteor_real_golden.py 2026-11-03 真录像裁决）。
    """
    sw = np.empty_like(soft)
    sw[0::2] = soft[1::2]
    sw[1::2] = soft[0::2]
    return sw


def _fmt_rad(value) -> str:
    """锁状态相位残差的人类可读形式（空态用破折号，不把 None 说成 0）。"""
    return "—" if value is None else f"{value:.2f} rad"


def _suppress_ghosts(cands, radius: int = 32):
    """NMS：±radius 符号内只留汉明最优（滑窗鬼影保险，真帧间隔 8198 符号）。"""
    kept = []
    for c in sorted(cands, key=lambda c: c.hamming):
        if all(abs(c.sync_symbol_index - k.sync_symbol_index) > radius
               for k in kept):
            kept.append(c)
    return kept


def _resample_for_symbols(iq: np.ndarray, sample_rate: float,
                          sym_rate: int, sps: int
                          ) -> Tuple[np.ndarray, int, int, int]:
    """重采样到 ``sps × sym_rate``（LrptDemodulator 的整数 sps 前提）。

    真实 SDR 录制常是 2.4/1.024 MS/s 等非整数倍符号率；直接喂整数 sps
    解调器会失锁（2026-11-03 实测：187.5k ÷ 72k = 2.604 sps → 0 候选）。
    返回 ``(iq, target_hz, up, down)``；已是整数倍时 up=down=1 不做滤波。
    """
    target = int(sps * sym_rate)
    src = int(round(float(sample_rate)))
    if src <= 0:
        raise ValueError(f"采样率必须为正数，收到 {sample_rate!r}")
    if src == target:
        return iq, target, 1, 1
    ratio = Fraction(target, src).limit_denominator(2000)
    from scipy.signal import resample_poly  # 反混叠抽取，禁止裸抽点
    return (resample_poly(iq, ratio.numerator, ratio.denominator),
            target, ratio.numerator, ratio.denominator)


def summarize_rs_errors(frames) -> Dict:
    """把逐帧 RS 纠错数聚合成质量口径（第二批 12.1）。

    参数:
        frames: 可迭代对象，元素是一帧 4 块的纠错符号数序列
            （``decode_cadu`` / ``decode_cadu_erasure`` 的 ``rs_errors``；
            ``-1`` 表示该块不可纠）。

    口径（与 goestools ``Packetizer::Details.reedSolomonBytes`` 对齐）：
      - 一帧只要有任一块 ``-1`` 即「不可纠」，不参与均值与最大值；
      - ``rs_corrected_frames`` 统计「至少纠了 1 个符号」的帧；
      - ``rs_max_errors`` / ``rs_mean_errors`` 以**帧**为单位（4 块之和），
        单位都是 RS 符号；
      - 没有任何帧时全部给空值，绝不拿 0 冒充「零纠错」。

    返回 dict：``rs_frames`` / ``rs_corrected_frames`` / ``rs_max_errors`` /
    ``rs_uncorrectable_frames`` / ``rs_mean_errors``。
    """
    total_frames = 0
    corrected_frames = 0
    uncorrectable_frames = 0
    max_errors = 0
    symbol_sum = 0
    for frame in frames:
        errs = [int(e) for e in frame]
        total_frames += 1
        if any(e < 0 for e in errs):
            uncorrectable_frames += 1
            continue
        corrected = sum(errs)
        symbol_sum += corrected
        max_errors = max(max_errors, corrected)
        if corrected > 0:
            corrected_frames += 1
    decodable = total_frames - uncorrectable_frames
    return {
        "rs_frames": total_frames,
        "rs_corrected_frames": corrected_frames,
        "rs_max_errors": max_errors if decodable > 0 else None,
        "rs_uncorrectable_frames": uncorrectable_frames,
        "rs_mean_errors": (round(symbol_sum / decodable, 3)
                           if decodable > 0 else None),
    }


def decode_recording_to_image(
    iq: np.ndarray,
    sample_rate: float,
    *,
    sym_rate: int = RECORDING_SYM_RATE,
    sps: int = 2,
    max_hamming: int = 8,
    min_payloads: int = 1,
) -> RecordingImageResult:
    """真实录制 IQ → LRPT 可见光 RGB（失败时给阶段化人话原因）。

    链路（与 tests/test_lrpt_meteor_real_golden.py 的真录口径逐段一致）：
      重采样到 sps×72k → AGC/粗 CFO/定时/Costas → ham≤8 松阈值帧同步
      → NMS → pair-swap± → Viterbi（带每字节可靠性）→ ASM 验证
      → 擦除辅助 RS(255,223)×4 + woven 重组 + version 结构判据
      → M_PDU 空间包重组 → 图像包抽取 → MCU 熵解码 → RGB。

    任何一段没拿到东西都返回 ``success=False``，并把 ``stage`` / ``error``
    写成用户能看懂的原因（绝不拿半幅图或噪声图冒充成功）。
    """
    res = RecordingImageResult()
    # 先把 FEC 聚合键以空态铺好：链路在 sync/decode 前失败时消费者也能拿到
    # 确定的键（计数 0、最大/均值 None），不用猜「没有键」是不是零纠错。
    stats: Dict = {"chain": "lrpt_pipeline.real", **summarize_rs_errors([])}
    res.stats = stats
    try:
        arr = np.asarray(iq, dtype=np.complex128)
    except (TypeError, ValueError) as e:
        res.stage = "input"
        res.error = f"IQ 数据无法转成复数基带：{type(e).__name__}: {e}"
        return res
    if arr.size == 0:
        res.stage = "input"
        res.error = "IQ 录制裁剪为空，无样点可解"
        return res

    # 1. 重采样到整数 sps（真录固定 2 sps = 144 kS/s）
    try:
        arr, target_hz, up, down = _resample_for_symbols(
            arr, sample_rate, sym_rate, sps)
    except (ValueError, ImportError) as e:
        res.stage = "resample"
        res.error = f"重采样失败：{e}"
        return res
    stats.update({
        "sample_rate_hz": float(sample_rate),
        "resampled_hz": target_hz,
        "resample_up": up,
        "resample_down": down,
        "duration_s": float(arr.size) / float(target_hz),
    })

    # 2. 软判决解调 + 帧同步（松阈值，ASM 负责证伪）
    dem = LrptDemodulator(fs=target_hz, sym_rate=sym_rate, costas_bw=0.01)
    soft, cands = dem.demodulate_soft(arr, max_hamming=max_hamming)
    lock = dem.lock_state()
    stats["demod_lock"] = lock
    kept = _suppress_ghosts(cands)
    stats["n_candidates"] = len(kept)
    if soft.size == 0 or not kept:
        res.stage = "sync"
        lock_txt = ("已锁定" if lock["locked"] else
                    "曾锁定后失锁" if lock["locked_once"] else "未锁定")
        res.error = (f"帧同步未锁（软位 {soft.size}，候选 {len(kept)}；"
                     f"载波环{lock_txt}，相位残差 "
                     f"{_fmt_rad(lock['phase_error_avg_rad'])}）："
                     "录制不是 LRPT / 信号过弱 / 采样率标注不符")
        return res

    # 3. 逐帧 Viterbi → ASM → 擦除辅助 RS → woven 重组
    decoder = LrptCaduDecoder()
    need_bits = SYNC_BITS + (CADU_SIZE * 8 + 6) * 2
    payloads: List[bytes] = []
    asm_hits = 0
    frame_errors: List[List[int]] = []
    for c in kept:
        start = c.sync_symbol_index * 2
        end = min(start + need_bits, soft.size)
        if end - start < need_bits - 64:
            continue
        sl = soft[start:end]
        for cand_soft in (_pair_swap(sl), -_pair_swap(sl)):
            try:
                raw, rel = decoder.decode_viterbi_to_bytes(
                    cand_soft, return_reliability=True)
            except (ValueError, IndexError):
                continue
            if raw[:4] != CADU_ASM:
                continue
            asm_hits += 1
            try:
                payload, errs, _used = decoder.decode_cadu_erasure(
                    raw[:CADU_SIZE], rel[:CADU_SIZE], order="woven")
            except (ValueError, IndexError):
                continue
            # 12.1：ASM 已验证的帧都留下逐块纠错数（含后面因结构判据被
            # 拒的帧）——「这帧纠了多少」是信道质量，不因结构判据而消失。
            frame_errors.append(list(errs))
            # 全 4 块可纠 + 结构判据（合法 TM VCDU 主头 version 恒为 1）：
            # 擦除辅助放宽到 2e+f≤32 后仍可能纠到别的合法码字，只能靠
            # 协议结构剔除（真录实测 10 条里 1 条 version=2 属此类）。
            if (all(e >= 0 for e in errs) and len(payload) == 892
                    and ((payload[0] >> 6) & 0x3) == 1):
                payloads.append(payload)
            break
    stats["n_asm"] = asm_hits
    stats["n_valid_vcdu"] = len(payloads)
    stats.update(summarize_rs_errors(frame_errors))
    res.payloads = payloads
    if asm_hits == 0:
        res.stage = "decode"
        res.error = (f"帧同步找到 {len(kept)} 个候选，但 Viterbi 后未见 ASM "
                     "0x1ACFFC1D：符号/比特映射不符或信号质量不足")
        return res
    if len(payloads) < min_payloads:
        res.stage = "decode"
        res.error = (f"ASM 命中 {asm_hits} 次，但 RS(255,223) 全纠正的 VCDU 只有 "
                     f"{len(payloads)} 条（< {min_payloads}）：载噪比不足或录制过短")
        return res

    # 4. M_PDU → 空间包 → 图像包
    from .lrpt_image import (
        lrpt_packets_to_rgb, parse_image_packet, reassemble_space_packets,
    )
    try:
        pkts, pstats = reassemble_space_packets(payloads)
    except (ValueError, IndexError) as e:
        res.stage = "mpdu"
        res.error = f"VCDU→M_PDU 重组失败：{type(e).__name__}: {e}"
        return res
    img_pkts = [ip for p in pkts if (ip := parse_image_packet(p)) is not None]
    stats.update({k: pstats[k] for k in ("n_packets", "n_skipped",
                                        "n_resets", "n_fhp_out_of_range")})
    stats["n_image_packets"] = len(img_pkts)
    if not img_pkts:
        res.stage = "mpdu"
        res.error = (f"{len(payloads)} 条 VCDU 重组出 {len(pkts)} 个空间包，"
                     "但无 LRPT 图像包（APID 64/65/68）——本段可能不含图像")
        return res

    # 5. MCU 熵解码 → RGB
    rgb, channels, istats = lrpt_packets_to_rgb(img_pkts)
    stats.update({
        "n_blocks": int(sum(c.n_blocks for c in channels.values())),
        "width": int(istats["width"]),
        "height": int(istats["height"]),
        "n_scan_rows": int(istats["n_scan_rows"]),
    })
    if rgb.size == 0 or rgb.shape[0] < 8 or stats["n_blocks"] == 0:
        res.stage = "image"
        res.error = ("图像包已解出，但 MCU 熵解码没解出任何有效块"
                     "（包头损坏/量化表不符），不出空白图")
        return res
    res.success = True
    res.rgb = rgb
    res.stage = "done"
    return res

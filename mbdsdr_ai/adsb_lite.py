"""ADS-B / Mode S (1090 MHz) lite 解码器。

定位：把最容易用 RTL-SDR 在城市里真实收到的数字航空链路做成 AI 可调用工具。
本模块只依赖 numpy，纯函数、可离线复现，便于无棒环境用合成帧严格验证。

覆盖（对标 dump1090 的最小可用子集）：
  - 8 µs preamble 检测（0/1/3.5/4.5 µs 四个 0.5 µs 脉冲，窗口能量判决，任意采样率 >=1 MHz）；
  - 112 bit 长帧（DF17/18/…）与 56 bit 短帧（DF11/…）的 PPM 位判决；
  - CRC-24（生成多项式 0xFFF409）校验，剔除噪声/损坏帧；
  - DF/CA、ICAO 24bit 地址、DF17 type code 与消息类型分类；
  - TC1-4 呼号（callsign）解码；CPR 经纬度具体位置标注 needs_cpr（后续）。

不做：CPR 奇偶位置解算、MB 数据链（BDS）细分解码、前向纠错。这些留给完整
dump1090 后端；本模块目标是“收到 → 知道有哪几架飞机、什么消息、CRC 是否可信”。

参考：RTCA DO-260B / Mode S 标准；CRC 多项式与位序同 dump1090 modesChecksum。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import numpy as np

# Mode S CRC 生成多项式（省略最高位 x^24）
MODES_CRC_GENERATOR = 0xFFF409

# preamble 四个 0.5µs 脉冲的起始时刻（µs），数据从 8µs 开始
PREAMBLE_PULSES_US = (0.0, 1.0, 3.5, 4.5)
PREAMBLE_GUARD_US = ((0.5, 1.0), (1.5, 3.5), (5.0, 8.0))
DATA_START_US = 8.0
BIT_US = 1.0
HALF_US = 0.5

# ADS-B 6bit 字符表（TC1-4 呼号）
_ADSB_CHAR = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


# --------------------------------------------------------------------------- #
# 位 / CRC 基础
# --------------------------------------------------------------------------- #
def crc24(bits: Sequence[int], nbits: int) -> int:
    """对前 nbits 个 MSB-first 位计算 Mode S CRC-24。

    对完整长帧（112bit，含 24bit PI）计算结果为 0 即校验通过；
    短帧为 56bit。
    """
    crc = 0
    for i in range(nbits):
        high = (crc >> 23) & 1
        crc = (crc << 1) & 0xFFFFFF
        if high ^ (bits[i] & 1):
            crc ^= MODES_CRC_GENERATOR
    return crc


def bytes_to_bits(data: bytes) -> List[int]:
    bits: List[int] = []
    for byte in data:
        bits.extend((byte >> (7 - k)) & 1 for k in range(8))
    return bits


def bits_to_bytes(bits: Sequence[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - len(bits) % 8, 8):
        v = 0
        for bit in bits[i:i + 8]:
            v = (v << 1) | (bit & 1)
        out.append(v)
    return bytes(out)


def build_long_frame(df_ca: int, icao: int, payload7: bytes) -> bytes:
    """构造 14 字节长帧（112bit）：DF/CA + ICAO + 7B ME + CRC。测试/信号源用。"""
    if len(payload7) != 7:
        raise ValueError("长帧 ME 负载必须为 7 字节")
    head = bytes([df_ca]) + int(icao & 0xFFFFFF).to_bytes(3, "big") + payload7
    crc = crc24(bytes_to_bits(head), 88)
    return head + crc.to_bytes(3, "big")


def build_short_frame(df_ca: int, icao: int) -> bytes:
    """构造 7 字节短帧（56bit）：DF/CA + ICAO + CRC。测试/信号源用。"""
    head = bytes([df_ca]) + int(icao & 0xFFFFFF).to_bytes(3, "big")
    crc = crc24(bytes_to_bits(head), 32)
    return head + crc.to_bytes(3, "big")


# --------------------------------------------------------------------------- #
# 合成 IQ（离线验证 / 自检信号源）
# --------------------------------------------------------------------------- #
def synthesize_modes_iq(frames: Sequence[bytes], sample_rate: float = 2_000_000,
                        amplitude: float = 0.6, noise_std: float = 0.03,
                        frame_gap_us: Sequence[float] = (250.0,),
                        rng: Optional[np.random.Generator] = None) -> np.ndarray:
    """把一个或多个 Mode S 帧合写成复基带 IQ（PPM 幅度键控，OOK 等效）。

    帧布局：8µs preamble + N bit（每 bit 1µs，1=前半高、0=后半高）。
    """
    if rng is None:
        rng = np.random.default_rng(0)
    us = 1e-6
    total_us = 0.0
    layout = []
    for i, frame_bytes in enumerate(frames):
        gap = frame_gap_us[i % len(frame_gap_us)] if i else 60.0
        total_us += gap
        start_us = total_us
        nbits = len(frame_bytes) * 8
        total_us += DATA_START_US + nbits * BIT_US
        layout.append((start_us, bytes_to_bits(frame_bytes), nbits))

    n = int(total_us * us * sample_rate) + int(sample_rate * 20 * us)
    iq = (noise_std * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex128)
    sps = sample_rate * 1e-6  # 样本 / µs

    def put(t0_us: float, dur_us: float):
        # 用 round 最近样本量化，避免 int 截断在整数边界（如 2MHz 下半码片仅 1 样本）错位
        a = int(round((start_us_ref[0] + t0_us) * sps))
        b = int(round((start_us_ref[0] + t0_us + dur_us) * sps))
        iq[max(0, a):min(n, max(a + 1, b))] += amplitude

    for start_us, bits, nbits in layout:
        start_us_ref = [start_us]
        for p in PREAMBLE_PULSES_US:
            put(p, HALF_US)
        for i, bit in enumerate(bits):
            on = 0.0 if bit == 1 else HALF_US
            put(DATA_START_US + i * BIT_US + on, HALF_US)
    return iq


# --------------------------------------------------------------------------- #
# 同步与解码
# --------------------------------------------------------------------------- #
@dataclass
class ADSBFrame:
    df: int
    icao_hex: str
    nbits: int
    crc_ok: bool
    ca: int = 0
    tc: Optional[int] = None
    msg_type: str = ""
    callsign: Optional[str] = None
    start_us: float = 0.0
    confidence: float = 0.0
    raw_hex: str = ""
    needs_cpr: bool = False

    def to_dict(self) -> dict:
        return {
            "df": self.df, "ca": self.ca, "icao": self.icao_hex, "bits": self.nbits,
            "crc_ok": self.crc_ok, "type_code": self.tc, "message_type": self.msg_type,
            "callsign": self.callsign, "start_us": round(self.start_us, 2),
            "confidence": round(self.confidence, 3), "raw_hex": self.raw_hex,
            "needs_cpr": self.needs_cpr,
        }


def _window_mean(amp: np.ndarray, sr: float, k: int, t0_us: float, dur_us: float) -> float:
    sps = sr * 1e-6  # 样本 / µs，统一用 round 最近样本量化（与合成器一致）
    a = k + int(round(t0_us * sps))
    b = k + int(round((t0_us + dur_us) * sps))
    a = max(0, a)
    b = min(len(amp), max(a + 1, b))
    return float(np.mean(amp[a:b])) if b > a else 0.0


def _preamble_score(amp: np.ndarray, sr: float, k: int, threshold: float):
    """返回某起点的 preamble 得分 (pulse_level-guard_level, pulse_level)；
    四脉冲不全过门限则返回 None。"""
    pulses = [_window_mean(amp, sr, k, p, 0.5) for p in PREAMBLE_PULSES_US]
    if min(pulses) < threshold:
        return None
    guard = [_window_mean(amp, sr, k, a, b - a) for a, b in PREAMBLE_GUARD_US]
    pulse_level = float(np.mean(pulses))
    guard_level = float(np.mean(guard))
    return pulse_level - guard_level, pulse_level, guard_level


def find_preambles(amp: np.ndarray, sr: float, threshold: float,
                   pulse_tol: float = 0.6, phase_search: int = 2) -> List[int]:
    """返回通过 preamble 模式校验的样本起点（已按帧长去重）。

    对超门限样本聚类后，在 ±phase_search 个样本内做相位搜索取相关峰对齐
    （2.4MHz 下半码片仅约 1.2 样本，相位偏差会让能量窗取空，必须对齐）。
    """
    min_sep = int(DATA_START_US * 1e-6 * sr)
    hot = np.flatnonzero(amp > threshold)
    starts: List[int] = []
    i = 0
    while i < len(hot):
        # 聚类相距 < min_sep 的超门限样本为一个候选区
        j = i + 1
        while j < len(hot) and hot[j] - hot[j - 1] < min_sep // 4 + 1:
            j += 1
        cluster = hot[i:j]
        center = int(cluster[len(cluster) // 2])
        best = None
        for k in range(center - phase_search, center + phase_search + 1):
            if k < 0 or k >= len(amp):
                continue
            sc = _preamble_score(amp, sr, k, threshold)
            if sc is not None and sc[2] < sc[1] * pulse_tol:
                if best is None or sc[0] > best[1][0]:
                    best = (k, sc)
        if best is not None:
            k = best[0]
            if not starts or k - starts[-1] >= min_sep:
                starts.append(k)
        i = j
    return starts


def _decode_bits_at(amp: np.ndarray, sr: float, k: int, nbits: int):
    """从 preamble 起点 k 解 nbits 个 PPM 位，返回 (bits, confidence)。"""
    bits = np.zeros(nbits, dtype=np.uint8)
    conf = np.zeros(nbits, dtype=np.float64)
    for i in range(nbits):
        t = DATA_START_US + i * BIT_US
        e1 = _window_mean(amp, sr, k, t, 0.5)          # 前半高 -> 1
        e0 = _window_mean(amp, sr, k, t + 0.5, 0.5)    # 后半高 -> 0
        tot = e1 + e0 + 1e-12
        bits[i] = 1 if e1 > e0 else 0
        conf[i] = abs(e1 - e0) / tot
    return bits, float(np.mean(conf))


def _decode_callsign(bits: Sequence[int]) -> Optional[str]:
    """TC1-4：ME 字节 1-6（全帧 bit40..88）为 6bit 字符呼号。"""
    chars = []
    for i in range(40, 88, 6):
        v = 0
        for b in bits[i:i + 6]:
            v = (v << 1) | b
        if v >= len(_ADSB_CHAR):
            return None
        chars.append(_ADSB_CHAR[v])
    cs = "".join(chars).replace("@", " ").strip()
    return cs or None


def message_type_for_tc(tc: int) -> tuple[str, bool]:
    """返回 (类型描述, 是否含 CPR 位置待解)。"""
    if 1 <= tc <= 4:
        return "aircraft identification", False
    if 5 <= tc <= 8:
        return "surface position (CPR)", True
    if 9 <= tc <= 18:
        return "airborne position (baro/GNSS alt, CPR)", True
    if tc == 19:
        return "airborne velocity", False
    if tc == 28:
        return "aircraft status", False
    if tc == 29:
        return "target state & status", False
    if tc == 31:
        return "operation status", False
    if 20 <= tc <= 22:
        return "airborne position (GNSS alt, CPR)", True
    return f"DF17 type code {tc}", False


def parse_frame(bits: Sequence[int], nbits: int, start_us: float,
                confidence: float) -> Optional[ADSBFrame]:
    if nbits not in (56, 112) or len(bits) < nbits:
        return None
    df = int("".join(str(b) for b in bits[0:5]), 2)
    crc_rem = crc24(bits, nbits)
    # DF20/DF21: Address/Parity — crc 余数是 ICAO 地址，不要求 ==0
    if df in (20, 21) and nbits == 112:
        icao = crc_rem
        crc_ok = True  # Address/Parity 模式，余数即地址
    # DF11: Parity/Interrogator — 低7bit为 IID，允许非零
    elif df == 11 and nbits == 56:
        icao = crc_rem & 0xFFFFFF80  # 高17bit为地址
        crc_ok = True
    elif crc_rem != 0:
        return None
    else:
        icao = int("".join(str(b) for b in bits[8:32]), 2)
        crc_ok = True
    ca = int("".join(str(b) for b in bits[5:8]), 2)
    raw = bits_to_bytes(bits[:nbits]).hex()
    tc = None
    msg_type = ""
    callsign = None
    needs_cpr = False
    if nbits == 112:
        tc = int("".join(str(b) for b in bits[32:37]), 2)
        msg_type, needs_cpr = message_type_for_tc(tc)
        if 1 <= tc <= 4:
            callsign = _decode_callsign(bits)
    else:
        msg_type = {0: "short air-to-air", 4: "altitude reply",
                    5: "identity reply", 11: "all-call reply"}.get(df, "short reply")
    return ADSBFrame(df=df, icao_hex=f"{icao:06X}", nbits=nbits, crc_ok=crc_ok, ca=ca,
                     tc=tc, msg_type=msg_type, callsign=callsign, start_us=start_us,
                     confidence=confidence, raw_hex=raw, needs_cpr=needs_cpr)


def expected_len_for_df(df: int) -> int:
    if df in (0, 4, 5, 11):
        return 56
    return 112


def decode_adsb(iq: np.ndarray, sample_rate: float, threshold_sigma: float = 4.0,
               return_invalid: bool = False) -> dict:
    """从一段 1090MHz 复基带 IQ 中解码 Mode S 帧。

    threshold_sigma: 自适应门限 = 幅度中位数 + k×(1.4826·MAD)。用中位数/MAD 而非
    均值/标准差，因为 OOK 高幅脉冲本身会抬高标准差，把门限顶到脉冲之上造成漏检。
    返回 {frames:[ADSBFrame.to_dict], aircraft:{icao:摘要}, candidates, crc_ok}。
    同一 ICAO 多帧只保留置信度最高的一帧进 aircraft 摘要，frames 保留全部 CRC 有效帧。
    """
    iq = np.asarray(iq)
    amp = np.abs(iq).astype(np.float64)
    med = float(np.median(amp))
    mad = float(np.median(np.abs(amp - med)))
    sigma_n = 1.4826 * mad if mad > 0 else float(np.std(amp))
    thr = med + threshold_sigma * sigma_n

    frames: List[ADSBFrame] = []
    invalid = 0
    used = []  # (start_sample,end_sample) 去重
    for k in find_preambles(amp, sample_rate, thr):
        # 先解前 5 bit 判 DF 决定长短帧
        head_bits, head_conf = _decode_bits_at(amp, sample_rate, k, 5)
        df = int("".join(str(b) for b in head_bits), 2)
        nbits = expected_len_for_df(df)
        end = k + int((DATA_START_US + nbits) * 1e-6 * sample_rate)
        if end > len(amp):
            continue
        if any(not (end <= a or k >= b) for a, b in used):
            continue
        bits, conf = _decode_bits_at(amp, sample_rate, k, nbits)
        start_us = k / (sample_rate * 1e-6)
        fr = parse_frame(bits, nbits, start_us, conf)
        if fr is None:
            # 长帧失败再试短帧（DF 误判兜底）
            if nbits == 112:
                end56 = k + int((DATA_START_US + 56) * 1e-6 * sample_rate)
                if end56 <= len(amp):
                    b56, c56 = _decode_bits_at(amp, sample_rate, k, 56)
                    fr = parse_frame(b56, 56, start_us, c56)
                    if fr is not None:
                        nbits, end = 56, end56
            if fr is None:
                invalid += 1
                continue
        used.append((k, end))
        frames.append(fr)

    aircraft = {}
    for fr in frames:
        cur = aircraft.get(fr.icao_hex)
        if cur is None or fr.confidence > cur["confidence"]:
            aircraft[fr.icao_hex] = {
                "icao": fr.icao_hex, "df": fr.df, "type_code": fr.tc,
                "message_type": fr.msg_type, "callsign": fr.callsign,
                "needs_cpr": fr.needs_cpr, "confidence": round(fr.confidence, 3),
            }
    out = {
        "frames": [f.to_dict() for f in frames],
        "aircraft": aircraft,
        "candidates": len(frames) + invalid,
        "crc_ok": len(frames),
        "crc_failed": invalid,
        "noise_floor_amp": round(med, 5),
        "threshold_amp": round(thr, 5),
        "duration_s": round(len(iq) / sample_rate, 4),
    }
    if return_invalid:
        out["invalid_preambles"] = invalid
    return out

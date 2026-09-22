"""ADS-B / Mode-S (1090 ES) 纯 Python 调制与解调。

只依赖 NumPy，用于在没有 dump1090 / RTL-SDR 的情况下做可复现的端到端
软件实验：合成 DF17 机载识别报文（TC=11，含 CRC-24）-> 复基带 OOK/PPM
信号 -> 加噪 -> 前导检测 -> 位判决 -> CRC 校验 -> 字段解码。

物理参数（ICAO Annex 10 / RTCA DO-260）：
- 载频 1090 MHz，码率 1 Mbit/s，每比特 1 us；
- 前导 8 us：0.0 / 1.0 / 3.5 / 4.5 us 处各 0.5 us 脉冲；
- 数据位为位置调制：每比特前 0.5 us 高、后 0.5 us 低 = 1，反之为 0；
- 长报文 112 bit = 88 bit 数据 + 24 bit CRC，CRC 生成多项式对应 0xFFF409。

注意：这是基带级实验实现，服务于 AI 工具与论文的软件实测，不替代经过
适航认证的接收机；真实空口还需重采样、多帧、CPR 位置解码等。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

__all__ = [
    "mode_s_crc24",
    "build_identification_frame",
    "encode_callsign",
    "decode_callsign",
    "modulate_baseband",
    "decode_baseband",
]

# 每微秒样本数在调制/解调函数里由采样率推出（默认 4 MHz -> 4 样本/us）。
PREAMBLE_US: Tuple[float, ...] = (0.0, 1.0, 3.5, 4.5)
PREAMBLE_LEN_US = 8.0
DATA_BITS_LONG = 112
DATA_BITS_SHORT = 56
CRC_POLY = 0xFFF409  # Mode-S 24 位 CRC 生成多项式（低 24 位）

# Mode-S 6-bit 呼号字符表（ICAO Annex 10），索引即 6 bit 编码。
# idx0=space, 1-26=A-Z, 27-31=space, 32-41=0-9, 42-46=..=+:, 47=?, 其余 space
CHARSET = (" " + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + " " * 5 +
           "0123456789" + "..=+:?" + " " * 16)


def _bytes_to_bits(data: bytes) -> List[int]:
    bits: List[int] = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return bits


def _bits_to_bytes(bits: List[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits) - 7, 8):
        b = 0
        for bit in bits[i:i + 8]:
            b = (b << 1) | (bit & 1)
        out.append(b)
    return bytes(out)


def mode_s_crc24(bits: List[int]) -> int:
    """对给定比特序列做 Mode-S CRC-24，返回 24 位校验值。

    MSB-first 长除法：输入位从寄存器最高位侧进入（与移出位异或后决定
    是否减去生成多项式）。对 DF17 的前 88 个数据位运行得到 FCS；对完整
    112 位（数据 + FCS）运行，合法报文余数为 0。已用 pyModeS 的标准
    测试向量与随机帧交叉验证。
    """
    reg = 0
    for bit in bits:
        top = (reg >> 23) & 1
        reg = (reg << 1) & 0xFFFFFF
        if top ^ (bit & 1):
            reg ^= CRC_POLY
    return reg & 0xFFFFFF


def encode_callsign(callsign: str) -> bytes:
    """把最多 6 字符航班号编码为 6 字节（每字符 6 bit），不足右侧补空格。"""
    cs = callsign.upper().ljust(6)[:6]
    out = bytearray()
    for ch in cs:
        if ch not in CHARSET:
            ch = " "
        out.append(CHARSET.index(ch))
    return bytes(out)


def decode_callsign(six_bytes: bytes) -> str:
    chars = []
    for byte in six_bytes:
        idx = byte & 0x3F
        chars.append(CHARSET[idx] if idx < len(CHARSET) else " ")
    return "".join(chars).strip()


def build_identification_frame(icao_hex: str, callsign: str,
                               category: int = 0) -> bytes:
    """构造 DF17 机载识别长报文（14 字节，含 CRC-24）。

    DF=17（10001），CA=5 -> 首字节 0x8D；TC=1（00001）机载识别（标准 TC 1-4）。
    """
    icao = int(icao_hex.replace("0x", "").replace(" ", ""), 16) & 0xFFFFFF
    data = bytearray(11)
    data[0] = 0x8D  # DF=17, CA=5
    data[1] = (icao >> 16) & 0xFF
    data[2] = (icao >> 8) & 0xFF
    data[3] = icao & 0xFF
    data[4] = (1 << 3) | (category & 0x07)  # TC=1（机载识别 TC 1-4）
    data[5:11] = encode_callsign(callsign)
    crc = mode_s_crc24(_bytes_to_bits(bytes(data)))
    frame = bytes(data) + bytes([(crc >> 16) & 0xFF,
                                 (crc >> 8) & 0xFF,
                                 crc & 0xFF])
    return frame


def modulate_baseband(frame: bytes, fs: float = 4e6,
                      lead_us: float = 0.0,
                      amplitude: float = 1.0) -> np.ndarray:
    """把报文调制成复基带 OOK/PPM 样本（含前导）。

    lead_us 为前导前的静默（噪声）长度，便于测试前导搜索。
    """
    sps = int(round(fs / 1e6))  # 样本/微秒 = 样本/比特
    chip = max(sps // 2, 1)
    lead = int(round(lead_us * sps))
    preamble = np.zeros(int(PREAMBLE_LEN_US * sps), dtype=np.complex128)
    half = chip  # 0.5 us
    for start_us in PREAMBLE_US:
        s = int(round(start_us * sps))
        preamble[s:s + half] = amplitude
    bits = _bytes_to_bits(frame)
    data = np.zeros(len(bits) * sps, dtype=np.complex128)
    for i, bit in enumerate(bits):
        s = i * sps
        if bit == 1:  # 前半高
            data[s:s + chip] = amplitude
        else:         # 后半高
            data[s + chip:s + 2 * chip] = amplitude
    if lead > 0:
        return np.concatenate([np.zeros(lead, dtype=np.complex128),
                               preamble, data])
    return np.concatenate([preamble, data])


def _find_preamble(env: np.ndarray, sps: int) -> Optional[int]:
    """完整 8 us 前导模板的归一化匹配滤波，再加脉冲/保护对比度判决。

    模板在 0/1/3.5/4.5 us 处为 1，其余为 0；数据区即便连续高电平，与该
    稀疏四脉冲模板的余弦相似度也明显低于真正导，从而避免误锁到数据区。
    """
    plen = int(PREAMBLE_LEN_US * sps)
    if len(env) < plen + sps:
        return None
    chip = max(sps // 2, 1)
    template = np.zeros(plen, dtype=np.float64)
    for start_us in PREAMBLE_US:
        s = int(round(start_us * sps))
        template[s:s + chip] = 1.0
    envf = env.astype(np.float64)
    # 归一化互相关（余弦相似度），逐滑窗。
    corr = np.convolve(envf, template[::-1], mode="valid")
    local_energy = np.convolve(envf * envf, np.ones(plen), mode="valid")
    denom = np.sqrt(np.maximum(local_energy * template.sum(), 1e-12))
    score = corr / denom
    k = int(np.argmax(score))
    seg = envf[k:k + plen]
    if len(seg) < plen:
        return None
    pulse_off = [int(round(u * sps)) for u in PREAMBLE_US]
    guard_off = [int(round(u * sps)) for u in (1.5, 2.5, 5.5, 6.5)]
    pulse_min = min(seg[o:o + chip].mean() for o in pulse_off)
    guard_max = max(seg[o:o + chip].mean() for o in guard_off)
    # 归一化相关门限 + 脉冲必须显著亮于保护间隙（纯噪声难以同时满足）。
    if score[k] < 0.60:
        return None
    if pulse_min < 1.8 * guard_max:
        return None
    return k


def decode_baseband(iq: np.ndarray, fs: float = 4e6,
                    long_frame: bool = True) -> Dict[str, Any]:
    """从复基带样本解码单个最强 Mode-S 报文。

    返回 found / preamble_index / crc_ok / df / icao / tc / callsign /
    bits / raw_bytes 等；CRC 不通过时仍返回判决比特，便于统计 BER。
    """
    sps = int(round(fs / 1e6))
    chip = max(sps // 2, 1)
    env = np.abs(iq) ** 2
    env = env / max(float(env.max()), 1e-12)  # 归一化便于门限
    k = _find_preamble(env, sps)
    if k is None:
        return {"found": False, "crc_ok": False, "reason": "preamble_not_found"}
    data_start = k + int(PREAMBLE_LEN_US * sps)
    nbits = DATA_BITS_LONG if long_frame else DATA_BITS_SHORT
    bits: List[int] = []
    for i in range(nbits):
        s = data_start + i * sps
        front = env[s:s + chip].sum()
        back = env[s + chip:s + 2 * chip].sum()
        bits.append(1 if front > back else 0)
    raw = _bits_to_bytes(bits)
    crc_ok = (mode_s_crc24(bits) == 0) if len(bits) == DATA_BITS_LONG else False
    result: Dict[str, Any] = {
        "found": True,
        "crc_ok": bool(crc_ok),
        "preamble_index": int(k),
        "bits": bits,
        "raw_bytes": raw.hex(),
    }
    if len(raw) >= 4:
        result["df"] = raw[0] >> 3
        result["icao"] = f"{raw[1]:02X}{raw[2]:02X}{raw[3]:02X}"
    if len(raw) >= 5:
        result["tc"] = raw[4] >> 3
    if crc_ok and len(raw) >= 11 and result.get("tc") in (1, 2, 3, 4):
        result["callsign"] = decode_callsign(raw[5:11])
    return result


def add_awgn(iq: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    """按信号功率给复基带信号加复高斯白噪声到指定 SNR（dB）。"""
    sig_power = float(np.mean(np.abs(iq) ** 2))
    noise_power = sig_power * 10 ** (-snr_db / 10.0)
    noise = (rng.standard_normal(len(iq)) +
             1j * rng.standard_normal(len(iq))) * np.sqrt(noise_power / 2)
    return iq + noise


def ber_against(received: List[int], transmitted: List[int]) -> float:
    n = min(len(received), len(transmitted))
    if n == 0:
        return 1.0
    return float(np.mean([a != b for a, b in zip(received[:n], transmitted[:n])]))

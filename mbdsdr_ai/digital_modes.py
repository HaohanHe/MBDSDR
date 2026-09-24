"""
MBDSDR AI - FT8/FT4 数字模式工具
==================================

参考：WSJT-X (GPL-3.0)
FT8: 15秒周期，8FSK，79Hz频偏，~15W
FT4: 7.5秒周期，8FSK，~3W

工具化：调用外部解码器/编码器，解析结果
"""

import subprocess
import json
import os
from typing import Dict, List, Optional


# ========================================================================
# FT8/FT4 标准参数
# ========================================================================

FT8_PARAMS = {
    "mode": "FT8",
    "period_s": 15,
    "modulation": "8FSK",
    "tone_spacing_hz": 6.25,
    "symbol_duration_ms": 256,
    "bandwidth_hz": 79,
    "min_wpm": 5,
    "typical_wpm": 12,
    "power_w": 15,
    "coding": "LDPC (K=79, N=174)",
    "crc": "12-bit",
}

FT4_PARAMS = {
    "mode": "FT4",
    "period_s": 7.5,
    "modulation": "8FSK",
    "tone_spacing_hz": 6.25,
    "symbol_duration_ms": 48,
    "bandwidth_hz": 79,
    "min_wpm": 12,
    "typical_wpm": 18,
    "power_w": 3,
    "coding": "LDPC (K=79, N=170)",
    "crc": "12-bit",
}

# 常用频率
FT_FREQUENCIES = {
    "160m": {"FT8": 1.8400e6, "FT4": 1.8400e6},
    "80m": {"FT8": 3.5730e6, "FT4": 3.5750e6},
    "40m": {"FT8": 7.0740e6, "FT4": 7.0800e6},
    "30m": {"FT8": 10.1360e6, "FT4": 10.1430e6},
    "20m": {"FT8": 14.0740e6, "FT4": 14.0800e6},
    "17m": {"FT8": 18.1000e6, "FT4": 18.1040e6},
    "15m": {"FT8": 21.0740e6, "FT4": 21.0800e6},
    "12m": {"FT8": 24.9150e6, "FT4": 24.9250e6},
    "10m": {"FT8": 28.0740e6, "FT4": 28.0800e6},
    "6m": {"FT8": 50.3130e6, "FT4": 50.3180e6},
    "2m": {"FT8": 144.1740e6, "FT4": 144.1700e6},
}


def list_ft_frequencies() -> List[Dict]:
    """列出所有FT8/FT4常用频率。"""
    result = []
    for band, freqs in FT_FREQUENCIES.items():
        result.append({
            "band": band,
            "ft8_mhz": freqs["FT8"] / 1e6,
            "ft4_mhz": freqs["FT4"] / 1e6,
        })
    return result


def get_ft_params(mode: str = "FT8") -> Dict:
    """获取FT8/FT4参数。"""
    if mode.upper() == "FT8":
        return FT8_PARAMS
    else:
        return FT4_PARAMS


# ========================================================================
# WSJT-X 接口
# ========================================================================

class WSJTInterface:
    """
    WSJT-X 网络接口。

    通过UDP与WSJT-X通信，获取解码结果。
    WSJT-X默认UDP端口：2237
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2237):
        self.host = host
        self.port = port

    def get_decoded_messages(self, wsjtx_log: str = None) -> List[Dict]:
        """
        从WSJT-X日志读取解码结果。

        参数:
            wsjtx_log: WSJT-X日志文件路径

        返回:
            解码消息列表
        """
        messages = []

        if wsjtx_log and os.path.exists(wsjtx_log):
            with open(wsjtx_log, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # 解析WSJT-X日志格式
                    parts = line.split()
                    if len(parts) >= 5:
                        msg = {
                            "time": parts[0],
                            "snr": parts[1] if parts[1].lstrip('-').isdigit() else None,
                            "dt": parts[2] if parts[2].replace('.', '').isdigit() else None,
                            "freq": parts[3] if parts[3].replace('.', '').isdigit() else None,
                            "message": ' '.join(parts[4:]),
                        }
                        messages.append(msg)

        return messages

    def list_available_modes(self) -> List[str]:
        """列出WSJT-X支持的模式。"""
        return ["FT8", "FT4", "JT9", "JT65", "WSPR", "Echo", "FSK CW", "QRA64"]


# ========================================================================
# AIS 船舶自动识别系统
# ========================================================================

AIS_PARAMS = {
    "frequency_hz": 162e6,  # 162 MHz
    "channel_A": 161.975e6,  # 87B
    "channel_B": 162.025e6,  # 88B
    "modulation": "GMSK",
    "baud_rate": 9600,
    "encoding": "NRZI",
    "packet_length_bits": 256,
}


def get_ais_params() -> Dict:
    """获取AIS参数。"""
    return AIS_PARAMS


# ========================================================================
# ADS-B 飞机广播
# ========================================================================

ADSB_PARAMS = {
    "frequency_hz": 1090e6,  # 1090 MHz
    "modulation": "PPM",
    "bit_rate": 1e6,  # 1 Mbps
    "packet_length_bits": 112,
    "crc": "24-bit",
}


def get_adsb_params() -> Dict:
    """获取ADS-B参数。"""
    return ADSB_PARAMS


# ========================================================================
# DVB-S/S2 卫星电视
# ========================================================================

DVBS_PARAMS = {
    "dvbs": {
        "modulation": "QPSK",
        "fec": "1/2, 2/3, 3/4, 5/6, 7/8",
        "symbol_rate": "1-45 MSym/s",
        "band": ["C-band (4-8 GHz)", "Ku-band (10.7-12.75 GHz)"],
    },
    "dvbs2": {
        "modulation": "QPSK, 8PSK, 16APSK, 32APSK",
        "fec": "1/2-9/10",
        "symbol_rate": "1-45 MSym/s",
        "band": ["C-band", "Ku-band"],
    },
}


def get_dvbs_params() -> Dict:
    """获取DVB-S/S2参数。"""
    return DVBS_PARAMS


# ========================================================================
# QPSK / Viterbi 数字解调桥接（接线到 demod.py 真实实现）
# ========================================================================
# 此前本模块只存 FT8/AIS/ADS-B/DVB-S 的参数表；真实 QPSK 解调（RRC 匹配
# 滤波 + Costas 载波恢复 + Gardner 位同步 + 判决）与 K=7 r=1/2 卷积码
# Viterbi 解码实现在 demod.py（meteor_sat 也复用）。这里把它接到本数字模式
# 入口，提供统一函数，避免 demod 沦为旁路模块；demod.py 保持不动、不删除。
try:  # 包内相对导入优先，兼容直接脚本运行
    from .demod import QPSKDemodulator, ViterbiDecoder
except Exception:  # pragma: no cover
    try:
        from mbdsdr_ai.demod import QPSKDemodulator, ViterbiDecoder
    except Exception:
        QPSKDemodulator = None
        ViterbiDecoder = None


def get_qpsk_viterbi():
    """返回 (QPSKDemodulator, ViterbiDecoder) 类；numpy 不可用时为 (None, None)。"""
    return QPSKDemodulator, ViterbiDecoder


def demodulate_qpsk(iq, sps: int = 4, beta: float = 0.35, num_taps: int = 101):
    """统一 QPSK 解调入口：匹配滤波→Costas→Gardner→四相判决，返回复符号数组。"""
    if QPSKDemodulator is None:
        raise RuntimeError("demod.QPSKDemodulator 不可用（需要 numpy）")
    return QPSKDemodulator(sps=sps, beta=beta, num_taps=num_taps).demodulate(iq)


def decode_viterbi(bits, K: int = 7, G1: int = 171, G2: int = 133):
    """统一 Viterbi 解码入口（K=7, r=1/2, G1=171/G2=133），返回信息比特数组。"""
    if ViterbiDecoder is None:
        raise RuntimeError("demod.ViterbiDecoder 不可用（需要 numpy）")
    return ViterbiDecoder(K=K, G1=G1, G2=G2).decode(bits)

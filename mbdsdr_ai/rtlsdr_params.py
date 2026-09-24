#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtlsdr_params.py — librtlsdr 真实硬件参数表（纯查表，无设备依赖）。

本模块把 librtlsdr 官方驱动里写死的增益表 / 频率范围 / 采样率区间 / AGC /
ppm / 直采模式 / 异步读取回调等常量原样移植过来，所有数值都标注来源
`librtlsdr src/xxx.c:行号`，禁止凭空编造。

数据单位约定
------------
librtlsdr 的 C API 一律用「0.1 dB」（tenths of a dB）表示增益，
例如 115 == 11.5 dB（来源: librtlsdr include/rtl-sdr.h:197,213）。
本模块对外统一用 **dB 浮点**，表内同时保留原始 0.1dB 整数便于对账。

注意：rtlsdr_get_tuner_gains() 返回的是「驱动实际暴露的离散增益档」，
不是把整个物理量程线性铺开。例如 R820T 物理量程 0~49.6 dB，但驱动只暴露
29 个离散档（LNA/Mixer 步进累加的结果，见 tuner_r82xx.c:967-1017）。
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Optional

# ─────────────────────────────────────────────────────────────────────────
# 调谐器枚举（来源: librtlsdr include/rtl-sdr.h:172-180）
#   enum rtlsdr_tuner { UNKNOWN=0, E4000, FC0012, FC0013, FC2580, R820T, R828D }
# ─────────────────────────────────────────────────────────────────────────
TUNER_E4000 = "E4000"
TUNER_FC0012 = "FC0012"
TUNER_FC0013 = "FC0013"
TUNER_FC2580 = "FC2580"
TUNER_R820T = "R820T"
TUNER_R828D = "R828D"

# rtl-sdr.h:172-180 的枚举序号 → 名称（与 RTLSDRBackend._TUNER_NAMES 对齐）
TUNER_ENUM_TO_NAME: Dict[int, str] = {
    0: "Unknown",
    1: TUNER_E4000,
    2: TUNER_FC0013,   # 注意：枚举序号 2 是 FC0013
    3: "FC0025",
    4: TUNER_FC2580,
    5: TUNER_R820T,
    6: TUNER_R828D,
}


# ─────────────────────────────────────────────────────────────────────────
# 真实增益表（0.1 dB 整数），来源: librtlsdr src/librtlsdr.c:959-969
#   int rtlsdr_get_tuner_gains(...) 里的静态数组，单位 0.1 dB。
#   R820T 与 R828D 共用 r82xx_gains（librtlsdr.c:991-993）。
# ─────────────────────────────────────────────────────────────────────────
# 来源: librtlsdr src/librtlsdr.c:959-960  e4k_gains[]
_E4K_GAIN_TENTHS: List[int] = [
    -10, 15, 40, 65, 90, 115, 140, 165, 190, 215, 240, 290, 340, 420,
]
# 来源: librtlsdr src/librtlsdr.c:961  fc0012_gains[]
_FC0012_GAIN_TENTHS: List[int] = [-99, -40, 71, 179, 192]
# 来源: librtlsdr src/librtlsdr.c:962-964  fc0013_gains[]
_FC0013_GAIN_TENTHS: List[int] = [
    -99, -73, -65, -63, -60, -58, -54, 58, 61, 63, 65, 67, 68, 70, 71,
    179, 181, 182, 184, 186, 188, 191, 197,
]
# 来源: librtlsdr src/librtlsdr.c:966-969  r82xx_gains[]（R820T/R828D 共用）
_R82XX_GAIN_TENTHS: List[int] = [
    0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254,
    280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439, 445, 480, 496,
]


def _tenths_to_db(tenths: List[int]) -> List[float]:
    """把 0.1dB 整数表转成 dB 浮点表（rtl-sdr.h:197: 115 == 11.5 dB）。"""
    return [t / 10.0 for t in tenths]


# ─────────────────────────────────────────────────────────────────────────
# 频率范围（Hz）
# ─────────────────────────────────────────────────────────────────────────
# E4000: tuner_e4k.c:347-353
#   #ifdef OUT_OF_SPEC  → FLO_MIN=50MHz, FLO_MAX=2200MHz（:348-349）
#   否则(默认)        → FLO_MIN=64MHz, FLO_MAX=1700MHz（:351-352）
# R820T/R828D: tuner_r82xx.c:1168 频段划分 HF<=28.8 / VHF 28.8-250 / UHF>250；
#   驱动不硬限幅，公开可调上限约 1766 MHz，下限 24 MHz（低于 24MHz 需直采/上变频）。
# FC0012/FC0013: 公开范围 22 ~ 948.6 MHz。
# ─────────────────────────────────────────────────────────────────────────
RTL_TUNER_PARAMS: Dict[str, Dict] = {
    TUNER_E4000: {
        "gain_tenths": _E4K_GAIN_TENTHS,
        # 来源: tuner_e4k.c:351-352 默认规格；:348-349 为 OUT_OF_SPEC 编译档
        "freq_range_hz": (64_000_000, 1_700_000_000),
        "freq_range_out_of_spec_hz": (50_000_000, 2_200_000_000),
        "note": "E4000 默认规格 64-1700MHz；OUT_OF_SPEC 编译可到 50-2200MHz",
    },
    TUNER_FC0012: {
        "gain_tenths": _FC0012_GAIN_TENTHS,
        "freq_range_hz": (22_000_000, 948_600_000),
        "note": "Fitipower FC0012，5 个离散增益档",
    },
    TUNER_FC0013: {
        "gain_tenths": _FC0013_GAIN_TENTHS,
        "freq_range_hz": (22_000_000, 948_600_000),
        "note": "Fitipower FC0013，23 个离散增益档",
    },
    TUNER_FC2580: {
        "gain_tenths": [],  # librtlsdr.c:965 fc2580_gains = {0 /* no gain values */}
        "freq_range_hz": (22_000_000, 1_000_000_000),
        "note": "FC2580 驱动不暴露增益表（librtlsdr.c:965）",
    },
    TUNER_R820T: {
        "gain_tenths": _R82XX_GAIN_TENTHS,
        "freq_range_hz": (24_000_000, 1_766_000_000),
        "note": "R820T，29 个离散增益档；LNA/Mixer 步进见 tuner_r82xx.c:967-977",
    },
    TUNER_R828D: {
        "gain_tenths": _R82XX_GAIN_TENTHS,  # librtlsdr.c:991-993 R828D 同 R820T
        "freq_range_hz": (24_000_000, 1_766_000_000),
        "note": "R828D 与 R820T 共用 r82xx_gains（librtlsdr.c:992-993）",
    },
}


# ─────────────────────────────────────────────────────────────────────────
# 采样率区间
# 来源: librtlsdr src/librtlsdr.c:1100-1101
#   if ((samp_rate <= 225000) || (samp_rate > 3200000) ||
#       ((samp_rate > 300000) && (samp_rate <= 900000))) → EINVAL
# 即合法: (225000, 300000] ∪ (900000, 3200000]，300k~900k 是死区。
# 来源: librtlsdr include/rtl-sdr.h:260-263 文档:
#   225001-300000 Hz / 900001-3200000 Hz；>2400000 会丢采样。
# ─────────────────────────────────────────────────────────────────────────
SAMPLE_RATE_MIN_HZ = 225_001     # librtlsdr.c:1100 (samp_rate <= 225000 非法)
SAMPLE_RATE_DEAD_LOW = 300_000  # librtlsdr.c:1101 死区下沿
SAMPLE_RATE_DEAD_HIGH = 900_000  # librtlsdr.c:1101 死区上沿（900000 本身非法）
SAMPLE_RATE_MAX_HZ = 3_200_000  # librtlsdr.c:1100 (samp_rate > 3200000 非法)
SAMPLE_RATE_LOSS_WARN_HZ = 2_400_000  # rtl-sdr.h:263 超过此值会丢采样

# 推荐常用采样率档（均落在合法区间内）
SUPPORTED_SAMPLE_RATES: List[int] = [
    250_000,    # rtl_433 默认低段档
    1_024_000,
    1_536_000,
    1_800_000,
    1_920_000,
    2_000_000,
    2_048_000,
    2_400_000,
    2_560_000,
    3_200_000,  # 上限，接近此值注意 USB 丢包
]


# ─────────────────────────────────────────────────────────────────────────
# 其它硬件控制常量（便于后端对齐 C 语义）
# ─────────────────────────────────────────────────────────────────────────
# 数字 AGC（RTL2832 内部）: librtlsdr.c:1157-1163
#   rtlsdr_set_agc_mode: demod reg 0x19, on=0x25 / off=0x05
RTL_DIGITAL_AGC_REG = 0x19
RTL_DIGITAL_AGC_ON = 0x25
RTL_DIGITAL_AGC_OFF = 0x05

# ppm 频率校正: librtlsdr.c:915-938 rtlsdr_set_freq_correction(dev, ppm)
#   ppm 为整数 ppm，会同时校正采样率并重新锁相。

# 直接采样: librtlsdr.c:1165-1226 + rtl-sdr.h:296-305
#   on=0 关闭, on=1 I-ADC 输入, on=2 Q-ADC 输入；此时 center_freq 控制
#   DDC 的 IF，可直采 0 ~ 28.8 MHz（RTL2832 晶振）。
DIRECT_SAMPLING_OFF = 0
DIRECT_SAMPLING_I = 1
DIRECT_SAMPLING_Q = 2
DIRECT_SAMPLING_MAX_HZ = 28_800_000  # rtl-sdr.h:298-299

# 异步读取: rtl-sdr.h:340, 362-373
#   typedef void(*rtlsdr_read_async_cb_t)(unsigned char *buf, uint32_t len, void *ctx);
#   buf_num 默认 15；buf_len 默认 16*32*512 = 262144，须为 512 的倍数。
ASYNC_DEFAULT_BUF_NUM = 15        # rtl-sdr.h:363
ASYNC_DEFAULT_BUF_LEN = 16 * 32 * 512  # rtl-sdr.h:366 = 262144


# ─────────────────────────────────────────────────────────────────────────
# 对外查询 API
# ─────────────────────────────────────────────────────────────────────────
def get_gain_table(tuner_type: str) -> List[float]:
    """返回某调谐器的离散增益表（dB 浮点，升序）。

    数值直接来自 rtlsdr_get_tuner_gains() 的静态数组
    （librtlsdr.c:959-969），单位 0.1dB 已换算成 dB。
    """
    p = RTL_TUNER_PARAMS.get(tuner_type)
    if p is None:
        return []
    return _tenths_to_db(p["gain_tenths"])


def get_gain_table_tenths(tuner_type: str) -> List[int]:
    """返回原始 0.1dB 整数增益表（用于和 C 驱动对账）。"""
    p = RTL_TUNER_PARAMS.get(tuner_type)
    return list(p["gain_tenths"]) if p else []


def get_frequency_range(tuner_type: str) -> Tuple[float, float]:
    """返回 (min_hz, max_hz) 默认规格频率范围。"""
    p = RTL_TUNER_PARAMS.get(tuner_type)
    if p is None:
        return (0.0, 0.0)
    lo, hi = p["freq_range_hz"]
    return (float(lo), float(hi))


def get_supported_sample_rates() -> List[int]:
    """返回推荐采样率档列表（Hz）。"""
    return list(SUPPORTED_SAMPLE_RATES)


def is_valid_sample_rate(rate_hz: float) -> bool:
    """判定采样率是否落在 librtlsdr 合法区间（librtlsdr.c:1100-1101）。"""
    r = float(rate_hz)
    if r <= SAMPLE_RATE_MIN_HZ - 1:   # <= 225000 非法
        return False
    if r > SAMPLE_RATE_MAX_HZ:
        return False
    if SAMPLE_RATE_DEAD_LOW < r <= SAMPLE_RATE_DEAD_HIGH:  # (300k,900k] 死区
        return False
    return True


def nearest_gain(tuner_type: str, target_db: float) -> Optional[float]:
    """在驱动暴露的离散增益档里找最接近 target_db 的一档。

    移植自 convenience.c:116-141 nearest_gain()：遍历增益表，取 |err| 最小者。
    """
    table = get_gain_table(tuner_type)
    if not table:
        return None
    return min(table, key=lambda g: abs(g - target_db))


def get_tuner_summary(tuner_type: str) -> Dict:
    """返回某调谐器的完整参数摘要（供工具/UI 展示）。"""
    p = RTL_TUNER_PARAMS.get(tuner_type)
    if p is None:
        return {"tuner": tuner_type, "known": False}
    lo, hi = p["freq_range_hz"]
    gains = _tenths_to_db(p["gain_tenths"])
    out_of_spec = p.get("freq_range_out_of_spec_hz")
    return {
        "tuner": tuner_type,
        "known": True,
        "gain_table_db": gains,
        "gain_count": len(gains),
        "gain_min_db": gains[0] if gains else None,
        "gain_max_db": gains[-1] if gains else None,
        "freq_range_hz": [lo, hi],
        "freq_range_mhz": [lo / 1e6, hi / 1e6],
        "freq_range_out_of_spec_hz": list(out_of_spec) if out_of_spec else None,
        "note": p.get("note", ""),
    }


if __name__ == "__main__":
    import json
    for name in RTL_TUNER_PARAMS:
        print(json.dumps(get_tuner_summary(name), ensure_ascii=False, indent=2))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
limesuite_params.py — LimeSuite 真实硬件参数表 + 诚实的 LimeSDR 后端。

本模块把 MyriadRF LimeSuite 驱动（LMS7002M 射频芯片 + FPGA 流）里写死的
频率范围 / 采样率区间 / LNA/TIA/PGA 增益分级 / 天线端口 / 数据格式等常量
原样移植过来，所有数值都标注来源 ``repos/LimeSuite/src/xxx/xxx.cpp:行号``
或 ``repos/LimeSuite/src/lime/LimeSuite.h:行号``，**禁止凭空编造**。

两部分
======
1. ``LimeSDRParams``（纯查表，无设备依赖）：
   所有硬件量程/离散增益档都在这里，与 C 驱动逐字对齐。
2. ``LimeSDRBackend``（诚实后端）：
   通过 ctypes 直接加载系统 ``libLimeSuite.so``，调用真实的 C API
   （``LMS_Open`` / ``LMS_SetLOFrequency`` / ``LMS_SetSampleRate`` /
   ``LMS_SetGaindB`` / ``LMS_StartStream`` / ``LMS_RecvStream`` ...）。
   **没有装 libLimeSuite 或没插设备时，connect() 直接返回 False，绝不假成功。**

数据格式约定
------------
LimeSDR 的原生数据链路是 **12-bit 有符号 I/Q**（存在 16-bit 变量里），
来源: src/lime/LimeSuite.h:1103  ``LMS_FMT_I12  ///<12-bit integers stored in 16-bit variables``
      src/lime/LimeSuite.h:1109-1110  linkFmt 默认即 12-bit（当 dataFmt=I12）。
C 侧 ``LMS_RecvStream`` 读出的是交织 int16 I/Q（每复采样 4 字节：I16,Q16）。

增益链（RX path，来源: src/API/lms7_device.cpp:1027-1089）
---------------------------------------------------------
组合增益 ``LMS_SetGaindB`` 范围 [0, 73] dB（src/lime/LimeSuite.h:382），
由驱动内部查表拆成三级：
  - LNA（RFE 级，``G_LNA_RFE`` 寄存器）：0-30 dB，15 个离散档，
    来源: src/lms7002m/LMS7002M.cpp:789-837（SetRFELNA_dB / GetRFELNA_dB switch）。
  - TIA（RFE 级，``G_TIA_RFE`` 寄存器）：0-12 dB，3 个离散档（0/9/12），
    来源: src/lms7002m/LMS7002M.cpp:890-914（SetRFETIA_dB / GetRFETIA_dB switch）。
  - PGA（RBB 级，``G_PGA_RBB`` 寄存器）：5-bit 寄存器域 0-31，
    来源: src/lms7002m/LMS7002M.cpp:763-787（SetRBBPGA_dB，>0x1f 钳位）。
组合分配表在 src/API/lms7_device.cpp:1057-1071（lnaTbl/pgaTbl，maxGain=74）。
"""

from __future__ import annotations

import ctypes
import logging
import queue
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# 一、纯参数表（LimeSDRParams）—— 全部标注 C 源码 file:line
# ═════════════════════════════════════════════════════════════════════════

# ── 频率范围 ─────────────────────────────────────────────────────────────
# LimeSDR USB（通用 LMS7_Device）：100 kHz ~ 3.8 GHz。
# 来源: src/API/lms7_device.cpp:1384  return Range(100e3, 3.8e9);
# LimeSDR Mini（LMS7_LimeSDR_mini 覆写）：10 MHz ~ 3.5 GHz。
# 来源: src/API/LimeSDR_mini.cpp:312  return Range(10e6, 3.5e9);
# 注意: LMS_SetLOFrequency 本身不做范围钳幅（src/API/lms7_api.cpp:372 附近
#       只是转发到设备 GetFrequencyRange），量程由各板子类返回的 Range 约束。
LIMESDR_USB_MIN_FREQ_HZ: int = 100_000          # lms7_device.cpp:1384  100 kHz
LIMESDR_USB_MAX_FREQ_HZ: int = 3_800_000_000     # lms7_device.cpp:1384  3.8 GHz
LIMESDR_MINI_MIN_FREQ_HZ: int = 10_000_000       # LimeSDR_mini.cpp:312  10 MHz
LIMESDR_MINI_MAX_FREQ_HZ: int = 3_500_000_000    # LimeSDR_mini.cpp:312  3.5 GHz

# ── 采样率 ───────────────────────────────────────────────────────────────
# LimeSDR USB（通用 LMS7_Device）：100 kHz ~ 61.44 MHz。
# 来源: src/API/lms7_device.cpp:690  return Range(100e3, 61.44e6);
# LimeSDR Mini 覆写：100 kHz ~ 30.72 MHz。
# 来源: src/API/LimeSDR_mini.cpp:307  return Range(100e3, 30.72e6);
# 过采样比合法值: 1,2,4,8,16,32,0(默认) —— src/lime/LimeSuite.h:197,604
LIMESDR_USB_MIN_SR_HZ: int = 100_000            # lms7_device.cpp:690  100 kHz
LIMESDR_USB_MAX_SR_HZ: int = 61_440_000         # lms7_device.cpp:690  61.44 MHz
LIMESDR_MINI_MIN_SR_HZ: int = 100_000           # LimeSDR_mini.cpp:307  100 kHz
LIMESDR_MINI_MAX_SR_HZ: int = 30_720_000        # LimeSDR_mini.cpp:307  30.72 MHz
LIMESDR_DEFAULT_SR_HZ: int = 5_000_000          # 常用默认值（手册/工具常用）
# 合法过采样比（LimeSuite.h:197）
SUPPORTED_OVERSAMPLE: List[int] = [0, 1, 2, 4, 8, 16, 32]

# 推荐常用主机采样率档（均落在 100k-61.44 MHz 合法区间内）。
SUPPORTED_SAMPLE_RATES: List[int] = [
    100_000,
    200_000,
    500_000,
    1_000_000,
    2_000_000,
    4_000_000,
    5_000_000,
    8_000_000,
    10_000_000,
    15_360_000,
    20_000_000,
    30_720_000,
    61_440_000,
]

# ── 组合增益（LMS_SetGaindB）─────────────────────────────────────────────
# 来源: src/lime/LimeSuite.h:382  "Desired gain, range [0, 73]"
# 来源: src/API/lms7_device.cpp:1032  const int maxGain = 74;  (索引 0..73)
COMBINED_GAIN_MIN_DB: int = 0                   # LimeSuite.h:382
COMBINED_GAIN_MAX_DB: int = 73                  # LimeSuite.h:382; lms7_device.cpp:1032

# ── LNA 增益（RFE 级，G_LNA_RFE 寄存器）─────────────────────────────────
# 合法离散档（dB）由 GetRFELNA_dB 的 switch 表写死：
#   来源: src/lms7002m/LMS7002M.cpp:814-837
#     case 15: return 30;  case 14: return 29;  case 13: return 28;
#     case 12: return 27;  case 11: return 26;  case 10: return 25;
#     case  9: return 24;  case  8: return 21;  case  7: return 18;
#     case  6: return 15;  case  5: return 12;  case  4: return  9;
#     case  3: return  6;  case  2: return  3;  case  1: return  0;
# 来源: src/lms7002m/LMS7002M.cpp:791  const double gmax = 30;
# 寄存器码: lna+1（lms7_device.cpp:1086），即 1..15，共 15 档。
LNA_GAIN_MIN_DB: int = 0       # LMS7002M.cpp:834 case 1 → gmax-30 = 0
LNA_GAIN_MAX_DB: int = 30      # LMS7002M.cpp:791 gmax=30; :820 case 15 → 30
LNA_GAIN_LEVELS_DB: List[int] = [
    0, 3, 6, 9, 12, 15, 18, 21, 24, 25, 26, 27, 28, 29, 30
]  # LMS7002M.cpp:818-835 switch 全部 case

# ── TIA 增益（RFE 级，G_TIA_RFE 寄存器）─────────────────────────────────
# 合法离散档（dB）由 GetRFETIA_dB 的 switch 表写死：
#   来源: src/lms7002m/LMS7002M.cpp:903-914
#     case 3: return 12;  case 2: return 9;  case 1: return 0;
# 来源: src/lms7002m/LMS7002M.cpp:892  const double gmax = 12;
# 寄存器码: tia+1（lms7_device.cpp:1087），即 1..3，共 3 档。
# 组合分配: lms7_device.cpp:1080-1081  value>51→tia=2, value>42→tia=1。
TIA_GAIN_MIN_DB: int = 0       # LMS7002M.cpp:911 case 1 → gmax-12 = 0
TIA_GAIN_MAX_DB: int = 12      # LMS7002M.cpp:892 gmax=12; :909 case 3 → 12
TIA_GAIN_LEVELS_DB: List[int] = [0, 9, 12]  # LMS7002M.cpp:907-912 switch

# ── PGA 增益（RBB 级，G_PGA_RBB 寄存器）─────────────────────────────────
# G_PGA_RBB 是 5-bit 寄存器域（0..0x1f = 0..31）。
# 来源: src/lms7002m/LMS7002M.cpp:766  if (g_pga_rbb > 0x1f) g_pga_rbb = 0x1f;
# 写入映射: g_pga_rbb = (int)(value + 12.5)（LMS7002M.cpp:765）
# 回读映射: return g_pga_rbb - 12（LMS7002M.cpp:786）
# 组合分配表 pgaTbl 取值 0..31（lms7_device.cpp:1065-1071），共 32 个寄存器码。
PGA_GAIN_MIN_REG: int = 0      # LMS7002M.cpp:767 if (g_pga_rbb < 0) = 0
PGA_GAIN_MAX_REG: int = 31      # LMS7002M.cpp:766 0x1f = 31
PGA_GAIN_LEVELS_REG: List[int] = list(range(32))  # 5-bit 域 0..31

# ── 天线端口 ─────────────────────────────────────────────────────────────
# 来源: src/lime/LimeSuite.h:280-289
#   LMS_PATH_NONE = 0
#   LMS_PATH_LNAH = 1  (RX LNA_H 口)
#   LMS_PATH_LNAL = 2  (RX LNA_L 口)
#   LMS_PATH_LNAW = 3  (RX LNA_W 口)
#   LMS_PATH_TX1  = 1  (TX 口 1)
#   LMS_PATH_TX2  = 2  (TX 口 2)
#   LMS_PATH_AUTO = 255
# 路径名表: src/API/lms7_device.cpp:693-699
#   TX: {"NONE","BAND1","BAND2"}  RX: {"NONE","LNAH","LNAL","LNAW","LB1","LB2"}
PATH_NONE: int = 0              # LimeSuite.h:282
PATH_LNAH: int = 1              # LimeSuite.h:283
PATH_LNAL: int = 2              # LimeSuite.h:284
PATH_LNAW: int = 3              # LimeSuite.h:285
PATH_TX1: int = 1               # LimeSuite.h:286
PATH_TX2: int = 2               # LimeSuite.h:287
PATH_AUTO: int = 255            # LimeSuite.h:288

RX_PORTS: Dict[str, int] = {
    "LNAH": PATH_LNAH,
    "LNAL": PATH_LNAL,
    "LNAW": PATH_LNAW,
}
TX_PORTS: Dict[str, int] = {
    "TX1": PATH_TX1,
    "TX2": PATH_TX2,
}

# ── 数据格式 / 位宽 ──────────────────────────────────────────────────────
# 来源: src/lime/LimeSuite.h:1099-1104
#   LMS_FMT_F32=0, LMS_FMT_I16=1, LMS_FMT_I12=2
#   LMS_FMT_I12 = "12-bit integers stored in 16-bit variables"
SAMPLE_BITS: int = 12           # LimeSuite.h:1103 LMS_FMT_I12
SAMPLE_FORMAT: str = "sc16"     # 交织 int16 I/Q（12-bit 数据存在 16-bit 容器里）
# C 侧 dataFmt 枚举值（LimeSuite.h:1101-1103）
LMS_FMT_F32: int = 0            # LimeSuite.h:1101
LMS_FMT_I16: int = 1            # LimeSuite.h:1102
LMS_FMT_I12: int = 2            # LimeSuite.h:1103

# ── 通道选择常量 ─────────────────────────────────────────────────────────
# 来源: src/lime/LimeSuite.h:128-129
#   LMS_CH_TX = true, LMS_CH_RX = false
LMS_CH_TX: bool = True          # LimeSuite.h:128
LMS_CH_RX: bool = False         # LimeSuite.h:129

# C API 返回码（src/lime/LimeSuite.h:64）
LMS_SUCCESS: int = 0            # LimeSuite.h:64


@dataclass
class LimeSDRParams:
    """LimeSDR 真实硬件参数（纯查表，无设备依赖）。

    所有字段都对应 LimeSuite / LMS7002M 源码里的写死常量，见类级注释与
    每个字段的 ``# 来源: file:line`` 标注。

    默认按 LimeSDR USB（通用 LMS7_Device）量程；
    LimeSDR Mini 覆写见类方法 ``for_mini()``。
    """

    # 频率（默认 LimeSDR USB 量程）
    min_freq_hz: int = field(default=LIMESDR_USB_MIN_FREQ_HZ)
    max_freq_hz: int = field(default=LIMESDR_USB_MAX_FREQ_HZ)
    # 采样率
    min_sr_hz: int = field(default=LIMESDR_USB_MIN_SR_HZ)
    max_sr_hz: int = field(default=LIMESDR_USB_MAX_SR_HZ)
    default_sr_hz: int = field(default=LIMESDR_DEFAULT_SR_HZ)
    # 组合增益
    gain_min_db: int = field(default=COMBINED_GAIN_MIN_DB)
    gain_max_db: int = field(default=COMBINED_GAIN_MAX_DB)

    @classmethod
    def for_mini(cls) -> "LimeSDRParams":
        """返回 LimeSDR Mini 的参数表（覆写频率/采样率上限）。

        来源: src/API/LimeSDR_mini.cpp:307 (sr 30.72e6), :312 (freq 10e6-3.5e9)。
        """
        p = cls()
        p.min_freq_hz = LIMESDR_MINI_MIN_FREQ_HZ
        p.max_freq_hz = LIMESDR_MINI_MAX_FREQ_HZ
        p.max_sr_hz = LIMESDR_MINI_MAX_SR_HZ
        return p

    # ── 增益离散档表 ────────────────────────────────────────────────────
    def lna_gain_levels_db(self) -> List[int]:
        """返回 LNA(RFE) 全部合法离散档：[0,3,6,...,30]，共 15 档。

        来源: src/lms7002m/LMS7002M.cpp:814-837 GetRFELNA_dB switch
              (case 1..15 → 0..30 dB，非线性步进)。
        """
        return list(LNA_GAIN_LEVELS_DB)

    def tia_gain_levels_db(self) -> List[int]:
        """返回 TIA(RFE) 全部合法离散档：[0, 9, 12]，共 3 档。

        来源: src/lms7002m/LMS7002M.cpp:903-914 GetRFETIA_dB switch
              (case 1→0, case 2→9, case 3→12)。
        """
        return list(TIA_GAIN_LEVELS_DB)

    def pga_gain_levels_reg(self) -> List[int]:
        """返回 PGA(RBB) G_PGA_RBB 寄存器全部合法码：0..31，共 32 码。

        来源: src/lms7002m/LMS7002M.cpp:766 (>0x1f 钳位)，5-bit 域。
        """
        return list(PGA_GAIN_LEVELS_REG)

    # ── 校验 / 吸附 ────────────────────────────────────────────────────
    def is_valid_frequency(self, freq_hz: float) -> bool:
        """频率是否落在量程内（默认 USB 量程 100k-3.8G）。"""
        return self.min_freq_hz <= float(freq_hz) <= self.max_freq_hz

    def is_valid_sample_rate(self, rate_hz: float) -> bool:
        """采样率是否落在量程内（默认 USB 100k-61.44M）。"""
        return self.min_sr_hz <= float(rate_hz) <= self.max_sr_hz

    def clamp_combined_gain(self, db: float) -> int:
        """把请求的组合增益 dB 钳到 [0, 73]。

        与 lms7_device.cpp:1035-1038 一致：超出 maxGain-1 钳到 73。
        """
        v = int(round(db))
        return max(self.gain_min_db, min(v, self.gain_max_db))

    def clamp_lna_gain(self, db: float) -> int:
        """把请求的 LNA dB 吸附到最近的合法档（≤目标，向下取档）。

        与 LMS7002M.cpp:794-809 的 if-else 阶梯一致：从高到低匹配阈值。
        """
        v = float(db)
        if v >= 30: return 30
        if v >= 29: return 29
        if v >= 28: return 28
        if v >= 27: return 27
        if v >= 26: return 26
        if v >= 25: return 25
        if v >= 24: return 24
        if v >= 21: return 21
        if v >= 18: return 18
        if v >= 15: return 15
        if v >= 12: return 12
        if v >= 9:  return 9
        if v >= 6:  return 6
        if v >= 3:  return 3
        return 0

    def clamp_tia_gain(self, db: float) -> int:
        """把请求的 TIA dB 吸附到合法档（0/9/12）。

        与 LMS7002M.cpp:895-898 一致：>=12→3(12dB), >=9→2(9dB), else→1(0dB)。
        """
        v = float(db)
        if v >= 12: return 12
        if v >= 9:  return 9
        return 0

    def summary(self) -> Dict[str, Any]:
        """返回完整参数摘要（供工具/UI 展示）。"""
        return {
            "device": "LimeSDR (MyriadRF, LMS7002M)",
            "freq_range_hz": [self.min_freq_hz, self.max_freq_hz],
            "freq_range_mhz": [self.min_freq_hz / 1e6, self.max_freq_hz / 1e6],
            "sample_rate_range_hz": [self.min_sr_hz, self.max_sr_hz],
            "default_sample_rate_hz": self.default_sr_hz,
            "combined_gain_db": [self.gain_min_db, self.gain_max_db],
            "lna_gain_db": self.lna_gain_levels_db(),
            "lna_gain_count": len(self.lna_gain_levels_db()),
            "tia_gain_db": self.tia_gain_levels_db(),
            "tia_gain_count": len(self.tia_gain_levels_db()),
            "pga_gain_reg": self.pga_gain_levels_reg(),
            "pga_gain_count": len(self.pga_gain_levels_reg()),
            "rx_ports": list(RX_PORTS.keys()),
            "tx_ports": list(TX_PORTS.keys()),
            "sample_bits": SAMPLE_BITS,
            "sample_format": SAMPLE_FORMAT,
            "sources": {
                "freq_usb": "src/API/lms7_device.cpp:1384 (100e3, 3.8e9)",
                "freq_mini": "src/API/LimeSDR_mini.cpp:312 (10e6, 3.5e9)",
                "sr_usb": "src/API/lms7_device.cpp:690 (100e3, 61.44e6)",
                "sr_mini": "src/API/LimeSDR_mini.cpp:307 (100e3, 30.72e6)",
                "combined_gain": "src/lime/LimeSuite.h:382 [0,73]; src/API/lms7_device.cpp:1032 maxGain=74",
                "lna": "src/lms7002m/LMS7002M.cpp:789-837 (gmax=30, switch case 1..15)",
                "tia": "src/lms7002m/LMS7002M.cpp:890-914 (gmax=12, switch case 1..3)",
                "pga": "src/lms7002m/LMS7002M.cpp:763-787 (G_PGA_RBB 5-bit, >0x1f clamp)",
                "antenna": "src/lime/LimeSuite.h:280-289; src/API/lms7_device.cpp:693-699",
                "iq_format": "src/lime/LimeSuite.h:1099-1104 (LMS_FMT_I12)",
            },
        }


# 模块级默认实例（LimeSDR USB 量程）
DEFAULT_PARAMS = LimeSDRParams()


# ═════════════════════════════════════════════════════════════════════════
# 二、诚实后端 LimeSDRBackend（ctypes → 真实 libLimeSuite.so）
# ═════════════════════════════════════════════════════════════════════════

# lms_stream_meta_t 布局（src/lime/LimeSuite.h:1038-1058）:
#   uint64_t timestamp; bool waitForTimestamp; bool flushPartialPacket;
class _LMSStreamMeta(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_uint64),
        ("waitForTimestamp", ctypes.c_bool),
        ("flushPartialPacket", ctypes.c_bool),
    ]


# lms_stream_t 布局（src/lime/LimeSuite.h:1072-1114）:
#   size_t handle; bool isTx; uint32_t channel; uint32_t fifoSize;
#   float throughputVsLatency; int dataFmt; int linkFmt;
class _LMSStream(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_size_t),
        ("isTx", ctypes.c_bool),
        ("channel", ctypes.c_uint32),
        ("fifoSize", ctypes.c_uint32),
        ("throughputVsLatency", ctypes.c_double),
        ("dataFmt", ctypes.c_int),
        ("linkFmt", ctypes.c_int),
    ]


class LimeSDRBackend:
    """LimeSDR 诚实后端：ctypes 直连 libLimeSuite，无设备绝不假成功。

    接口对齐 sdr_backend.SDRBackend 中与 LimeSDR 相关的子集：
      connect()/disconnect()
      set_frequency()/get_frequency()
      set_sample_rate()/get_sample_rate()
      set_gain(lna_db, tia_db, pga_db) / set_combined_gain(db)
      set_antenna(rx_or_tx, port_name)
      start_rx()/stop_rx()/read_samples()

    真实 C API 调用对应关系（来源: src/lime/LimeSuite.h）：
      connect      → LMS_Open + LMS_Init            (LimeSuite.h:103,164)
      set_freq     → LMS_SetLOFrequency             (LimeSuite.h:251)
      set_sr       → LMS_SetSampleRate             (LimeSuite.h:205)
      set_gain     → LMS_SetGaindB                  (LimeSuite.h:385)
      antenna      → LMS_SetAntenna                 (LimeSuite.h:315)
      start_rx     → LMS_SetupStream + LMS_StartStream (LimeSuite.h:1149,1168)
      recv         → LMS_RecvStream                 (LimeSuite.h:1191)
      stop_rx      → LMS_StopStream + LMS_DestroyStream (LimeSuite.h:1177,1159)
      close        → LMS_Close                      (LimeSuite.h:115)

    降级策略：
      - 找不到 ``libLimeSuite.so`` → connect() 返回 False，所有 setter 返回 False，
        read_samples 返回 None。**绝不伪造"已连接"或假样本**。
    """

    # 候选 soname（按出现顺序尝试加载）
    _LIBNAMES = (
        "libLimeSuite.so.23",
        "libLimeSuite.so.22",
        "libLimeSuite.so.21",
        "libLimeSuite.so.20",
        "libLimeSuite.so",
        "libLimeSuite.dylib",
        "LimeSuite.dll",
    )

    def __init__(self, device_index: int = 0, params: Optional[LimeSDRParams] = None):
        self.params = params or LimeSDRParams()
        self.device_index = device_index
        self._lib: Optional[ctypes.CDLL] = None
        self._dev: Optional[ctypes.c_void_p] = None
        self._stream: Optional[_LMSStream] = None
        self.connected = False
        self._rx_running: bool = False
        self._freq_hz: float = 100_000_000.0
        self._sample_rate_hz: float = float(self.params.default_sr_hz)
        self._gain_db: int = 0
        self._rx_port: str = "LNAL"

    # ── 库加载 ─────────────────────────────────────────────────────────
    def _load_lib(self) -> Optional[ctypes.CDLL]:
        """加载 libLimeSuite；失败返回 None（调用方据此优雅降级）。"""
        if self._lib is not None:
            return self._lib
        for name in self._LIBNAMES:
            try:
                lib = ctypes.CDLL(name)
                self._configure_prototypes(lib)
                self._lib = lib
                logger.info("已加载 %s", name)
                return lib
            except OSError:
                continue
        logger.warning("未找到 libLimeSuite（%s），LimeSDRBackend 将以未连接状态运行",
                       " / ".join(self._LIBNAMES))
        return None

    @staticmethod
    def _configure_prototypes(lib: ctypes.CDLL) -> None:
        """按 LimeSuite.h 声明设置 ctypes 函数原型（argtypes/restype）。"""
        # LMS_GetDeviceList(lms_info_str_t *dev_list) → int
        # 来源: LimeSuite.h:87
        lib.LMS_GetDeviceList.argtypes = [ctypes.POINTER(ctypes.c_char * 256)]
        lib.LMS_GetDeviceList.restype = ctypes.c_int
        # LMS_Open(lms_device_t** device, const char* info, void* args) → int
        # 来源: LimeSuite.h:103
        lib.LMS_Open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p, ctypes.c_void_p]
        lib.LMS_Open.restype = ctypes.c_int
        # LMS_Close(lms_device_t* device) → int
        # 来源: LimeSuite.h:115
        lib.LMS_Close.argtypes = [ctypes.c_void_p]
        lib.LMS_Close.restype = ctypes.c_int
        # LMS_Init(lms_device_t* device) → int
        # 来源: LimeSuite.h:164
        lib.LMS_Init.argtypes = [ctypes.c_void_p]
        lib.LMS_Init.restype = ctypes.c_int
        # LMS_EnableChannel(device, dir_tx, chan, enabled) → int
        # 来源: LimeSuite.h:189
        lib.LMS_EnableChannel.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t, ctypes.c_bool]
        lib.LMS_EnableChannel.restype = ctypes.c_int
        # LMS_SetSampleRate(device, rate, oversample) → int
        # 来源: LimeSuite.h:205
        lib.LMS_SetSampleRate.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_size_t]
        lib.LMS_SetSampleRate.restype = ctypes.c_int
        # LMS_SetLOFrequency(device, dir_tx, chan, frequency) → int
        # 来源: LimeSuite.h:251
        lib.LMS_SetLOFrequency.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t, ctypes.c_double]
        lib.LMS_SetLOFrequency.restype = ctypes.c_int
        # LMS_SetAntenna(dev, dir_tx, chan, index) → int
        # 来源: LimeSuite.h:315
        lib.LMS_SetAntenna.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t, ctypes.c_size_t]
        lib.LMS_SetAntenna.restype = ctypes.c_int
        # LMS_SetGaindB(device, dir_tx, chan, gain) → int
        # 来源: LimeSuite.h:385
        lib.LMS_SetGaindB.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_size_t, ctypes.c_uint]
        lib.LMS_SetGaindB.restype = ctypes.c_int
        # LMS_SetupStream(device, stream) → int
        # 来源: LimeSuite.h:1149
        lib.LMS_SetupStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(_LMSStream)]
        lib.LMS_SetupStream.restype = ctypes.c_int
        # LMS_DestroyStream(dev, stream) → int
        # 来源: LimeSuite.h:1159
        lib.LMS_DestroyStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(_LMSStream)]
        lib.LMS_DestroyStream.restype = ctypes.c_int
        # LMS_StartStream(stream) → int
        # 来源: LimeSuite.h:1168
        lib.LMS_StartStream.argtypes = [ctypes.POINTER(_LMSStream)]
        lib.LMS_StartStream.restype = ctypes.c_int
        # LMS_StopStream(stream) → int
        # 来源: LimeSuite.h:1177
        lib.LMS_StopStream.argtypes = [ctypes.POINTER(_LMSStream)]
        lib.LMS_StopStream.restype = ctypes.c_int
        # LMS_RecvStream(stream, void* samples, size_t count, meta*, timeout_ms) → int
        # 来源: LimeSuite.h:1191
        lib.LMS_RecvStream.argtypes = [
            ctypes.POINTER(_LMSStream), ctypes.c_void_p,
            ctypes.c_size_t, ctypes.POINTER(_LMSStreamMeta), ctypes.c_uint,
        ]
        lib.LMS_RecvStream.restype = ctypes.c_int

    # ── 连接 / 断开 ────────────────────────────────────────────────────
    def connect(self) -> bool:
        """真实打开 libLimeSuite 设备；失败（无库/无硬件）返回 False，不造假。"""
        lib = self._load_lib()
        if lib is None:
            self.connected = False
            return False
        try:
            dev = ctypes.c_void_p()
            rc = lib.LMS_Open(ctypes.byref(dev), None, None)  # LimeSuite.h:103
            if rc != LMS_SUCCESS or dev.value is None:
                logger.warning("LMS_Open 失败 rc=%d（未插 LimeSDR？）", rc)
                self.connected = False
                return False
            if lib.LMS_Init(dev) != LMS_SUCCESS:  # LimeSuite.h:164
                logger.warning("LMS_Init 失败")
                lib.LMS_Close(dev)
                self.connected = False
                return False
            # 使能 RX 通道 0
            lib.LMS_EnableChannel(dev, LMS_CH_RX, 0, True)  # LimeSuite.h:189
            self._dev = dev
            self.connected = True
            return True
        except Exception as e:  # pragma: no cover - 依赖硬件环境
            logger.warning("LimeSDR connect 异常: %s", e)
            self.connected = False
            return False

    def disconnect(self) -> None:
        if self._rx_running:
            self.stop_rx()
        if self._lib is not None and self._dev is not None:
            try:
                self._lib.LMS_Close(self._dev)  # LimeSuite.h:115
            except Exception:
                pass
        self._dev = None
        self.connected = False

    # ── 频率 / 采样率 ──────────────────────────────────────────────────
    def set_frequency(self, freq_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_frequency(freq_hz):
            logger.warning("频率 %s Hz 超出 LimeSDR 量程 %d-%d Hz",
                           freq_hz, self.params.min_freq_hz, self.params.max_freq_hz)
            return False
        rc = self._lib.LMS_SetLOFrequency(self._dev, LMS_CH_RX, 0, float(freq_hz))
        # LimeSuite.h:251
        if rc == LMS_SUCCESS:
            self._freq_hz = float(freq_hz)
            return True
        return False

    def get_frequency(self) -> float:
        return self._freq_hz

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_sample_rate(rate_hz):
            logger.warning("采样率 %s Hz 超出 LimeSDR 量程 %d-%d Hz",
                           rate_hz, self.params.min_sr_hz, self.params.max_sr_hz)
            return False
        # oversample=0 → 设备默认过采样（LimeSuite.h:197）
        rc = self._lib.LMS_SetSampleRate(self._dev, float(rate_hz), 0)
        # LimeSuite.h:205
        if rc == LMS_SUCCESS:
            self._sample_rate_hz = float(rate_hz)
            return True
        return False

    def get_sample_rate(self) -> float:
        return self._sample_rate_hz

    # ── 增益 ──────────────────────────────────────────────────────────
    def set_combined_gain(self, db: int) -> bool:
        """设置组合 RX 增益，范围 [0,73] dB（LimeSuite.h:382）。"""
        if not self.connected or self._lib is None:
            return False
        v = self.params.clamp_combined_gain(db)
        rc = self._lib.LMS_SetGaindB(self._dev, LMS_CH_RX, 0, v)  # LimeSuite.h:385
        if rc == LMS_SUCCESS:
            self._gain_db = v
            return True
        return False

    def set_gain(self, lna_db: int = 0, tia_db: int = 0, pga_db: int = 0) -> bool:
        """分别设置 LNA/TIA/PGA 增益档。

        注意: LimeSuite 高层 API ``LMS_SetGaindB`` 是组合增益自动分配；
        分级设置需走低层寄存器 API。此处通过组合增益近似实现——
        先按 clamp 吸附到合法档，再把三级求和作为组合增益下发。
        真实分级寄存器直写见 LMS7002M::SetRFELNA_dB 等（需 SoapySDR 桥接）。
        """
        if not self.connected or self._lib is None:
            return False
        lna = self.params.clamp_lna_gain(lna_db)
        tia = self.params.clamp_tia_gain(tia_db)
        # PGA: 寄存器码 0..31，近似映射到 dB 贡献（组合表内分配）
        pga_reg = max(PGA_GAIN_MIN_REG, min(int(pga_db), PGA_GAIN_MAX_REG))
        total = lna + tia + pga_reg
        return self.set_combined_gain(total)

    # ── 天线 ──────────────────────────────────────────────────────────
    def set_antenna(self, port_name: str, direction: str = "RX") -> bool:
        """选择天线端口（RX: LNAH/LNAL/LNAW; TX: TX1/TX2）。

        来源: LimeSuite.h:280-289 路径枚举; LimeSuite.h:315 LMS_SetAntenna。
        """
        if not self.connected or self._lib is None:
            return False
        port_map = RX_PORTS if direction.upper() == "RX" else TX_PORTS
        idx = port_map.get(port_name.upper())
        if idx is None:
            logger.warning("未知天线端口 %s", port_name)
            return False
        is_tx = direction.upper() == "TX"
        rc = self._lib.LMS_SetAntenna(self._dev, is_tx, 0, idx)  # LimeSuite.h:315
        if rc == LMS_SUCCESS and not is_tx:
            self._rx_port = port_name.upper()
        return rc == LMS_SUCCESS

    # ── RX 流 ──────────────────────────────────────────────────────────
    def start_rx(self) -> bool:
        """真实启动接收流（LMS_SetupStream + LMS_StartStream）。"""
        if not self.connected or self._lib is None:
            return False
        try:
            stream = _LMSStream()
            stream.isTx = False
            stream.channel = 0
            stream.fifoSize = 1024 * 1024
            stream.throughputVsLatency = 0.0
            stream.dataFmt = LMS_FMT_I16   # 16-bit 容器装 12-bit 数据
            stream.linkFmt = 0              # 默认
            rc = self._lib.LMS_SetupStream(self._dev, ctypes.byref(stream))
            # LimeSuite.h:1149
            if rc != LMS_SUCCESS:
                logger.warning("LMS_SetupStream 失败 rc=%d", rc)
                return False
            rc = self._lib.LMS_StartStream(ctypes.byref(stream))  # LimeSuite.h:1168
            if rc != LMS_SUCCESS:
                logger.warning("LMS_StartStream 失败 rc=%d", rc)
                self._lib.LMS_DestroyStream(self._dev, ctypes.byref(stream))
                return False
            self._stream = stream
            self._rx_running = True
            return True
        except Exception as e:  # pragma: no cover
            logger.warning("start_rx 异常: %s", e)
            self._rx_running = False
            return False

    def stop_rx(self) -> bool:
        """停止接收流（LMS_StopStream + LMS_DestroyStream）。"""
        if not self.connected or self._lib is None or self._stream is None:
            self._rx_running = False
            return False
        try:
            self._lib.LMS_StopStream(ctypes.byref(self._stream))   # LimeSuite.h:1177
            self._lib.LMS_DestroyStream(self._dev, ctypes.byref(self._stream))
            # LimeSuite.h:1159
        except Exception:
            pass
        self._stream = None
        self._rx_running = False
        return True

    def read_samples(self, num_samples: int) -> Optional[bytes]:
        """读取 num_samples 个复采样（= 4*num_samples 字节，交织 int16 I/Q）。

        返回原始 bytes；无设备/未收齐时返回 None（不造假数据）。
        来源: LimeSuite.h:1191 LMS_RecvStream。
        """
        if not self._rx_running or self._lib is None or self._stream is None:
            return None
        buf_size = num_samples * 4  # 每复采样 I16+Q16 = 4 字节
        buf = (ctypes.c_int16 * (num_samples * 2))()
        meta = _LMSStreamMeta()
        meta.timestamp = 0
        meta.waitForTimestamp = False
        meta.flushPartialPacket = False
        try:
            got = self._lib.LMS_RecvStream(
                ctypes.byref(self._stream), buf, num_samples,
                ctypes.byref(meta), 1000,  # 1000 ms timeout
            )
        except Exception as e:  # pragma: no cover
            logger.warning("LMS_RecvStream 异常: %s", e)
            return None
        if got <= 0:
            return None
        return bytes(buf)[: got * 4]

    # ── 状态 ───────────────────────────────────────────────────────────
    def status_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "freq_hz": self._freq_hz,
            "sample_rate_hz": self._sample_rate_hz,
            "combined_gain_db": self._gain_db,
            "rx_port": self._rx_port,
            "rx_running": self._rx_running,
            "has_libLimeSuite": self._load_lib() is not None,
        }


if __name__ == "__main__":
    import json
    p = LimeSDRParams()
    print(json.dumps(p.summary(), ensure_ascii=False, indent=2))
    print("\n--- LimeSDR Mini ---")
    pm = LimeSDRParams.for_mini()
    print(json.dumps(pm.summary(), ensure_ascii=False, indent=2))

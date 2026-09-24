#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bladerf_params.py — libbladeRF 真实硬件参数表 + 诚实的 bladeRF 后端。

本模块把 Nuand bladeRF 开源驱动 libbladeRF 里写死的频率范围 / 采样率区间 /
带宽 / RX/TX 增益分级（legacy VGA stage API）/ 数据格式等常量原样移植过来，
所有数值都标注来源 ``repos/bladeRF/host/libraries/libbladeRF/include/bladeRF*.h:行号``
或 ``repos/bladeRF/fpga_common/include/bladerf2_common.h:行号``，**禁止凭空编造**。

两部分
======
1. ``BladeRFParams``（纯查表，无设备依赖）：
   - 同时给出 **bladeRF 1.0 (x40/x115, LMS6002D)** 与 **bladeRF 2.0 Micro (AD9361)**
     两套量程。两者 RFIC 不同，频率/采样率/带宽/增益级命名都不一样。
2. ``BladeRFBackend``（诚实后端）：
   通过 ctypes 直接加载系统 ``libbladeRF.so``，调用真实的 C API
   （``bladerf_open`` / ``bladerf_set_frequency`` / ``bladerf_set_sample_rate`` /
   ``bladerf_set_bandwidth`` / ``bladerf_set_gain`` / ``bladerf_sync_config`` /
   ``bladerf_sync_rx`` / ``bladerf_enable_module`` ...）。
   **没有装 libbladeRF 或没插设备时，connect() 直接返回 False，绝不假成功。**

数据格式约定
------------
libbladeRF 原生同步流格式 ``BLADERF_FORMAT_SC16_Q11``（libbladeRF.h:2141）：
  - 有符号复数 16-bit Q11，I 在前 Q 在后，小端 int16_t 交织
    （libbladeRF.h:2087-2097 "right-aligned, little-endian int16_t"）。
  - 量程 [-2048, 2048) ↔ [-1.0, 1.0)（libbladeRF.h:2089-2091）。
  - 物理 ADC/DAC 是 **12-bit**：libbladeRF.h:2144 明确写
    "Signed, Complex 16-bit Q11 using a 12-bit Q11 intermediate format"
    （BLADERF_FORMAT_SC16_Q11_PACKED，FPGA 内部 12-bit 打包）。
  - 每复采样 4 字节（I16 + Q16）。

增益链
------
bladeRF 1.0 (LMS6002D) 的 legacy 分级 API（bladeRF1.h:150-196，已 deprecated
但仍是真实硬件量程）：
  - RXVGA1（pre-LPF VGA）：5-30 dB，1 dB 步进 → 26 档（bladeRF1.h:154,160）
  - RXVGA2（post-LPF VGA）：0-30 dB，1 dB 步进 → 31 档（bladeRF1.h:166,172）
  - 合计 RX 总增益：5-60 dB
  - LNA：三档开关 Bypass(0 dB) / Mid(3 dB) / Max(6 dB)
         （bladeRF1.h:203-222, :215, :222）
  - TXVGA1（post-LPF）：-35 ~ -4 dB，1 dB 步进（bladeRF1.h:178,184）
  - TXVGA2（PA）：0-25 dB，1 dB 步进（bladeRF1.h:190,196）

bladeRF 2.0 Micro (AD9361) 总增益（fpga_common/include/bladerf2_common.h:344-478）：
  - RX 按频段分档（offset 用于 dBm↔dB 换算，用户侧增益范围）：
      * 0–1.3 GHz   : -16 ~ +60 dB（:354-355, 1-17 .. 77-17）
      * 1.3–4.0 GHz : -15 ~ +60 dB（:370-371, -4-11 .. 71-11）
      * 4.0–6.0 GHz : -12 ~ +60 dB（:386-387, -10-2 .. 62-2）
  - TX（全频段 47 MHz–6 GHz）：-23.75 ~ +66 dB，0.25 dB 步进
      （:455-456, 1000*(-89.750+66.0) .. 1000*(0+66.0)，step=250/1000 dB）
"""

from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# 一、纯参数表 —— 全部标注 C 源码 file:line
# ═════════════════════════════════════════════════════════════════════════

# ── bladeRF 1.0 (x40/x115, LMS6002D) 量程 ────────────────────────────────
# 来源: host/libraries/libbladeRF/include/bladeRF1.h
#   :47  BLADERF_SAMPLERATE_MIN  80000u        (80 kSPS 下限)
#   :54  BLADERF_SAMPLERATE_REC_MAX 40000000u (推荐上限 40 MSPS)
#   :60  BLADERF_BANDWIDTH_MIN  1500000u      (1.5 MHz)
#   :66  BLADERF_BANDWIDTH_MAX  28000000u     (28 MHz)
#   :84  BLADERF_FREQUENCY_MIN  237500000u   (237.5 MHz, 无 XB-200)
#   :90  BLADERF_FREQUENCY_MAX  3800000000u  (3.8 GHz)
# 这些常量被 board/bladerf1/bladerf1.c:250,259,260,268,269,276 直接填入
# bladerf_range 结构，是驱动真正做钳幅的边界。
BLADERF1_MIN_FREQ_HZ: int = 237_500_000     # bladeRF1.h:84
BLADERF1_MAX_FREQ_HZ: int = 3_800_000_000   # bladeRF1.h:90
BLADERF1_MIN_SAMPLERATE_HZ: int = 80_000     # bladeRF1.h:47
BLADERF1_REC_MAX_SAMPLERATE_HZ: int = 40_000_000  # bladeRF1.h:54
BLADERF1_MIN_BANDWIDTH_HZ: int = 1_500_000   # bladeRF1.h:60
BLADERF1_MAX_BANDWIDTH_HZ: int = 28_000_000  # bladeRF1.h:66

# ── bladeRF 2.0 Micro (AD9361) 量程 ───────────────────────────────────────
# 来源: fpga_common/include/bladerf2_common.h
#   :518-523  bladerf2_sample_rate_range_base: min=520834, max=61440000, step=2
#   :542-547  bladerf2_bandwidth_range:      min=200000, max=56000000
#   :550-555  bladerf2_rx_frequency_range:   min=70000000, max=6000000000
#   :557-562  bladerf2_tx_frequency_range:    min=47000000, max=6000000000
# board/bladerf2/bladerf2.c:985,1195,1232 直接返回这些 range 给上层
# bladerf_get_sample_rate_range()/get_bandwidth_range()/get_frequency_range()。
BLADERF2_MIN_RX_FREQ_HZ: int = 70_000_000        # bladerf2_common.h:551
BLADERF2_MAX_FREQ_HZ: int = 6_000_000_000       # bladerf2_common.h:552,559
BLADERF2_MIN_TX_FREQ_HZ: int = 47_000_000        # bladerf2_common.h:558
BLADERF2_MIN_SAMPLERATE_HZ: int = 520_834        # bladerf2_common.h:519
BLADERF2_MAX_SAMPLERATE_HZ: int = 61_440_000    # bladerf2_common.h:520
BLADERF2_SAMPLERATE_STEP_HZ: int = 2            # bladerf2_common.h:521
BLADERF2_MIN_BANDWIDTH_HZ: int = 200_000        # bladerf2_common.h:543
BLADERF2_MAX_BANDWIDTH_HZ: int = 56_000_000     # bladerf2_common.h:544

# ── Legacy VGA 增益分级（bladeRF 1.0 LMS6002D，bladeRF1.h:150-196）─────────
# 来源: host/libraries/libbladeRF/include/bladeRF1.h
#   :154  BLADERF_RXVGA1_GAIN_MIN  5
#   :160  BLADERF_RXVGA1_GAIN_MAX  30
#   :166  BLADERF_RXVGA2_GAIN_MIN  0
#   :172  BLADERF_RXVGA2_GAIN_MAX  30
#   :178  BLADERF_TXVGA1_GAIN_MIN  (-35)
#   :184  BLADERF_TXVGA1_GAIN_MAX  (-4)
#   :190  BLADERF_TXVGA2_GAIN_MIN  0
#   :196  BLADERF_TXVGA2_GAIN_MAX  25
# 这些常量对应 deprecated API bladerf_set_rxvga1/rxvga2/txvga1/txvga2
# （bladeRF1.h:268,324,239,337），源码注释明确写"超出范围会被 clamp"。
RXVGA1_GAIN_MIN_DB: int = 5     # bladeRF1.h:154
RXVGA1_GAIN_MAX_DB: int = 30    # bladeRF1.h:160
RXVGA2_GAIN_MIN_DB: int = 0     # bladeRF1.h:166
RXVGA2_GAIN_MAX_DB: int = 30    # bladeRF1.h:172
TXVGA1_GAIN_MIN_DB: int = -35   # bladeRF1.h:178
TXVGA1_GAIN_MAX_DB: int = -4    # bladeRF1.h:184
TXVGA2_GAIN_MIN_DB: int = 0    # bladeRF1.h:190
TXVGA2_GAIN_MAX_DB: int = 25   # bladeRF1.h:196

# LNA 三档开关（bladeRF1.h:203-222）
#   BLADERF_LNA_GAIN_BYPASS = 0 dB
#   BLADERF_LNA_GAIN_MID    = 3 dB  (bladeRF1.h:215)
#   BLADERF_LNA_GAIN_MAX    = 6 dB  (bladeRF1.h:222)
LNA_GAIN_BYPASS_DB: int = 0     # bladeRF1.h:205
LNA_GAIN_MID_DB: int = 3        # bladeRF1.h:215
LNA_GAIN_MAX_DB: int = 6        # bladeRF1.h:222

# ── bladeRF 2.0 (AD9361) 总增益范围 ──────────────────────────────────────
# 来源: fpga_common/include/bladerf2_common.h
#   :354-355  RX 0–1.3 GHz : min=1-17=-16, max=77-17=60
#   :370-371  RX 1.3–4 GHz : min=-4-11=-15, max=71-11=60
#   :386-387  RX 4–6 GHz  : min=-10-2=-12, max=62-2=60
#   :455-456  TX 全频段    : min=-89.75+66=-23.75, max=0+66=66 (dB)
#   :457      TX step = 250 milli-dB = 0.25 dB
BLADERF2_RX_GAIN_MIN_DB_LOW: int = -16   # bladerf2_common.h:354
BLADERF2_RX_GAIN_MAX_DB: int = 60        # bladerf2_common.h:355,371,387
BLADERF2_TX_GAIN_MIN_DB: float = -23.75   # bladerf2_common.h:455
BLADERF2_TX_GAIN_MAX_DB: float = 66.0     # bladerf2_common.h:456
BLADERF2_TX_GAIN_STEP_DB: float = 0.25    # bladerf2_common.h:457 (step=250, scale=0.001)

# ── 数据格式 / 通道枚举（libbladeRF.h）────────────────────────────────────
# libbladeRF.h:2085-2141  typedef enum { BLADERF_FORMAT_SC16_Q11=0, ... }
SAMPLE_FORMAT_DEFAULT: int = 0    # BLADERF_FORMAT_SC16_Q11 (libbladeRF.h:2141)
SAMPLE_BITS_NATIVE: int = 16      # 主机侧 SC16_Q11 是 int16_t (libbladeRF.h:2095)
SAMPLE_BITS_ADC_DAC: int = 12    # 物理 ADC/DAC 12-bit (libbladeRF.h:2144)
BYTES_PER_IQ_PAIR: int = 4        # I16 + Q16 = 4 字节/复采样

# 通道宏（libbladeRF.h:664,679,694,695）
#   BLADERF_CHANNEL_RX(ch) = (ch<<1)|0   → RX0 = 0
#   BLADERF_CHANNEL_TX(ch) = (ch<<1)|1   → TX0 = 1
BLADERF_CHANNEL_RX0: int = 0      # libbladeRF.h:664,694
BLADERF_CHANNEL_TX0: int = 1      # libbladeRF.h:679,695

# 通道布局（libbladeRF.h:715-716）
#   BLADERF_RX_X1 = 0, BLADERF_TX_X1 = 1
BLADERF_LAYOUT_RX_X1: int = 0    # libbladeRF.h:715

# 返回码（libbladeRF.h:4489-4513）
BLADERF_OK: int = 0
BLADERF_ERR_UNEXPECTED: int = -1
BLADERF_ERR_RANGE: int = -2
BLADERF_ERR_INVAL: int = -3
BLADERF_ERR_TIMEOUT: int = -6
BLADERF_ERR_NODEV: int = -7


@dataclass
class BladeRFParams:
    """bladeRF 真实硬件参数（纯查表，无设备依赖）。

    同时携带 bladeRF 1.0 (LMS6002D) 与 bladeRF 2.0 Micro (AD9361) 两套量程。
    所有字段都对应 libbladeRF / fpga_common 源码里的写死常量，见类级注释与
    每个字段的 ``# 来源: file:line`` 标注。
    """

    # 默认按 bladeRF 2.0 Micro 量程（用户清单里的主流型号）
    model: str = field(default="bladerf2_micro")  # 或 "bladerf1"

    # ── 频率 ──
    min_freq_hz: int = field(default=BLADERF2_MIN_RX_FREQ_HZ)
    max_freq_hz: int = field(default=BLADERF2_MAX_FREQ_HZ)
    min_tx_freq_hz: int = field(default=BLADERF2_MIN_TX_FREQ_HZ)

    # ── 采样率 ──
    min_sr_hz: int = field(default=BLADERF2_MIN_SAMPLERATE_HZ)
    max_sr_hz: int = field(default=BLADERF2_MAX_SAMPLERATE_HZ)
    default_sr_hz: int = field(default=2_000_000)  # 常用起步值（example_rx_meta/include/include.h:36）

    # ── 带宽 ──
    min_bw_hz: int = field(default=BLADERF2_MIN_BANDWIDTH_HZ)
    max_bw_hz: int = field(default=BLADERF2_MAX_BANDWIDTH_HZ)

    # ── Legacy VGA 分级（LMS6002D）──
    rxvga1_min_db: int = field(default=RXVGA1_GAIN_MIN_DB)
    rxvga1_max_db: int = field(default=RXVGA1_GAIN_MAX_DB)
    rxvga2_min_db: int = field(default=RXVGA2_GAIN_MIN_DB)
    rxvga2_max_db: int = field(default=RXVGA2_GAIN_MAX_DB)
    txvga1_min_db: int = field(default=TXVGA1_GAIN_MIN_DB)
    txvga1_max_db: int = field(default=TXVGA1_GAIN_MAX_DB)
    txvga2_min_db: int = field(default=TXVGA2_GAIN_MIN_DB)
    txvga2_max_db: int = field(default=TXVGA2_GAIN_MAX_DB)

    # ── bladeRF 2.0 总增益（AD9361）──
    rx_total_gain_min_db: int = field(default=BLADERF2_RX_GAIN_MIN_DB_LOW)
    rx_total_gain_max_db: int = field(default=BLADERF2_RX_GAIN_MAX_DB)
    tx_total_gain_min_db: float = field(default=BLADERF2_TX_GAIN_MIN_DB)
    tx_total_gain_max_db: float = field(default=BLADERF2_TX_GAIN_MAX_DB)

    # ── 增益离散档表 ────────────────────────────────────────────────────
    def rxvga1_levels_db(self) -> List[int]:
        """RXVGA1 (pre-LPF VGA) 全部合法档：5,6,...,30，共 26 档。

        来源: bladeRF1.h:154,160；deprecated API bladerf_set_rxvga1()
              注释 (bladeRF1.h:314-316) 明确"超出范围会被 clamp"，1 dB 步进。
        """
        return list(range(self.rxvga1_min_db, self.rxvga1_max_db + 1, 1))

    def rxvga2_levels_db(self) -> List[int]:
        """RXVGA2 (post-LPF VGA) 全部合法档：0,1,...,30，共 31 档。

        来源: bladeRF1.h:166,172；bladerf_set_rxvga2() (bladeRF1.h:344-346)。
        """
        return list(range(self.rxvga2_min_db, self.rxvga2_max_db + 1, 1))

    def txvga1_levels_db(self) -> List[int]:
        """TXVGA1 (post-LPF) 全部合法档：-35,-34,...,-4，共 32 档。

        来源: bladeRF1.h:178,184；bladerf_set_txvga1() (bladeRF1.h:258-260)。
        """
        return list(range(self.txvga1_min_db, self.txvga1_max_db + 1, 1))

    def txvga2_levels_db(self) -> List[int]:
        """TXVGA2 (PA) 全部合法档：0,1,...,25，共 26 档。

        来源: bladeRF1.h:190,196；bladerf_set_txvga2() (bladeRF1.h:229-231)。
        """
        return list(range(self.txvga2_min_db, self.txvga2_max_db + 1, 1))

    def lna_levels_db(self) -> List[int]:
        """LNA 三档开关：[0, 3, 6] dB。

        来源: bladeRF1.h:203-208 (enum), :215 (MID=3), :222 (MAX=6)。
        """
        return [LNA_GAIN_BYPASS_DB, LNA_GAIN_MID_DB, LNA_GAIN_MAX_DB]

    # ── 校验 / 钳幅 ────────────────────────────────────────────────────
    def is_valid_frequency(self, freq_hz: float) -> bool:
        """RX 频率是否落在量程内（bladeRF2: 70 MHz-6 GHz）。"""
        return self.min_freq_hz <= float(freq_hz) <= self.max_freq_hz

    def is_valid_sample_rate(self, rate_hz: float) -> bool:
        """采样率是否落在量程内（bladeRF2: 520834-61.44 MHz）。"""
        return self.min_sr_hz <= float(rate_hz) <= self.max_sr_hz

    def is_valid_bandwidth(self, bw_hz: float) -> bool:
        """带宽是否落在量程内（bladeRF2: 200 kHz-56 MHz）。"""
        return self.min_bw_hz <= float(bw_hz) <= self.max_bw_hz

    def clamp_rxvga1(self, db: float) -> int:
        """钳 RXVGA1 到 5-30 dB（bladeRF1.h:154,160）。"""
        v = int(round(db))
        return max(self.rxvga1_min_db, min(v, self.rxvga1_max_db))

    def clamp_rxvga2(self, db: float) -> int:
        """钳 RXVGA2 到 0-30 dB（bladeRF1.h:166,172）。"""
        v = int(round(db))
        return max(self.rxvga2_min_db, min(v, self.rxvga2_max_db))

    def clamp_txvga1(self, db: float) -> int:
        """钳 TXVGA1 到 -35..-4 dB（bladeRF1.h:178,184）。"""
        v = int(round(db))
        return max(self.txvga1_min_db, min(v, self.txvga1_max_db))

    def clamp_txvga2(self, db: float) -> int:
        """钳 TXVGA2 到 0-25 dB（bladeRF1.h:190,196）。"""
        v = int(round(db))
        return max(self.txvga2_min_db, min(v, self.txvga2_max_db))

    def summary(self) -> Dict[str, Any]:
        """返回完整参数摘要（供工具/UI 展示）。"""
        return {
            "device": f"Nuand bladeRF ({self.model})",
            "freq_range_hz_rx": [self.min_freq_hz, self.max_freq_hz],
            "freq_range_hz_tx": [self.min_tx_freq_hz, self.max_freq_hz],
            "freq_range_mhz_rx": [self.min_freq_hz / 1e6, self.max_freq_hz / 1e6],
            "sample_rate_range_hz": [self.min_sr_hz, self.max_sr_hz],
            "default_sample_rate_hz": self.default_sr_hz,
            "bandwidth_range_hz": [self.min_bw_hz, self.max_bw_hz],
            "bandwidth_range_mhz": [self.min_bw_hz / 1e6, self.max_bw_hz / 1e6],
            "rxvga1_gain_db": self.rxvga1_levels_db(),
            "rxvga1_count": len(self.rxvga1_levels_db()),
            "rxvga2_gain_db": self.rxvga2_levels_db(),
            "rxvga2_count": len(self.rxvga2_levels_db()),
            "txvga1_gain_db": self.txvga1_levels_db(),
            "txvga1_count": len(self.txvga1_levels_db()),
            "txvga2_gain_db": self.txvga2_levels_db(),
            "txvga2_count": len(self.txvga2_levels_db()),
            "lna_gain_db": self.lna_levels_db(),
            "rx_total_gain_range_db": [self.rx_total_gain_min_db,
                                       self.rx_total_gain_max_db],
            "tx_total_gain_range_db": [self.tx_total_gain_min_db,
                                      self.tx_total_gain_max_db],
            "sample_bits_native": SAMPLE_BITS_NATIVE,
            "sample_bits_adc_dac": SAMPLE_BITS_ADC_DAC,
            "sample_format": "SC16_Q11 (signed complex int16, I/Q interleaved)",
            "bytes_per_iq_pair": BYTES_PER_IQ_PAIR,
            "sources": {
                "bladerf2_freq": "fpga_common/include/bladerf2_common.h:550-562",
                "bladerf2_samplerate": "fpga_common/include/bladerf2_common.h:518-523",
                "bladerf2_bandwidth": "fpga_common/include/bladerf2_common.h:542-547",
                "bladerf2_rx_gain": "fpga_common/include/bladerf2_common.h:344-441",
                "bladerf2_tx_gain": "fpga_common/include/bladerf2_common.h:444-478",
                "rxvga1": "host/libraries/libbladeRF/include/bladeRF1.h:154,160",
                "rxvga2": "host/libraries/libbladeRF/include/bladeRF1.h:166,172",
                "txvga1": "host/libraries/libbladeRF/include/bladeRF1.h:178,184",
                "txvga2": "host/libraries/libbladeRF/include/bladeRF1.h:190,196",
                "lna": "host/libraries/libbladeRF/include/bladeRF1.h:203-222",
                "iq_format": "host/libraries/libbladeRF/include/libbladeRF.h:2085-2157",
                "api_open": "host/libraries/libbladeRF/include/libbladeRF.h:211",
                "api_close": "host/libraries/libbladeRF/include/libbladeRF.h:225",
                "api_set_freq": "host/libraries/libbladeRF/include/libbladeRF.h:1287",
                "api_set_sr": "host/libraries/libbladeRF/include/libbladeRF.h:1066",
                "api_set_bw": "host/libraries/libbladeRF/include/libbladeRF.h:1172",
                "api_set_gain": "host/libraries/libbladeRF/include/libbladeRF.h:838",
                "api_sync_config": "host/libraries/libbladeRF/include/libbladeRF.h:2776",
                "api_sync_rx": "host/libraries/libbladeRF/include/libbladeRF.h:2858",
                "api_enable_module": "host/libraries/libbladeRF/include/libbladeRF.h:2655",
            },
        }


# 模块级默认实例（bladeRF 2.0 Micro 量程）
DEFAULT_PARAMS = BladeRFParams()


# ═════════════════════════════════════════════════════════════════════════
# 二、诚实后端 BladeRFBackend（ctypes → 真实 libbladeRF.so）
# ═════════════════════════════════════════════════════════════════════════

class BladeRFBackend:
    """bladeRF 诚实后端：ctypes 直连 libbladeRF，无设备绝不假成功。

    接口对齐 sdr_backend.SDRBackend 中与 bladeRF 相关的子集：
      connect()/disconnect()
      set_frequency()/get_frequency()
      set_sample_rate()/get_sample_rate()
      set_bandwidth()/get_bandwidth()
      set_gain()（总增益, bladerf_set_gain）
      set_rxvga1()/set_rxvga2()/set_txvga1()/set_txvga2()（legacy 分级）
      start_rx()/stop_rx()/read_samples()

    真实 C API 调用对应关系（来源: host/libraries/libbladeRF/include/libbladeRF.h）：
      connect      → bladerf_open                     (libbladeRF.h:211)
      set_freq     → bladerf_set_frequency           (libbladeRF.h:1287)
      get_freq     → bladerf_get_frequency           (libbladeRF.h:1300)
      set_sr       → bladerf_set_sample_rate         (libbladeRF.h:1066)
      set_bw       → bladerf_set_bandwidth           (libbladeRF.h:1172)
      set_gain     → bladerf_set_gain                (libbladeRF.h:838)
      enable_mod   → bladerf_enable_module           (libbladeRF.h:2655)
      sync_config  → bladerf_sync_config             (libbladeRF.h:2776)
      sync_rx      → bladerf_sync_rx                 (libbladeRF.h:2858)
      close        → bladerf_close                   (libbladeRF.h:225)

    降级策略：
      - 找不到 ``libbladeRF.so`` → connect() 返回 False，所有 setter 返回 False，
        read_samples 返回 None。**绝不伪造"已连接"或假样本**。
    """

    # 候选 soname（按出现顺序尝试加载）
    _LIBNAMES = (
        "libbladeRF.so.2",
        "libbladeRF.so",
        "libbladeRF.dll",
        "libbladeRF.dylib",
    )

    def __init__(self, device_identifier: str = "",
                 params: Optional[BladeRFParams] = None):
        self.params = params or BladeRFParams()
        # device_identifier: "" 打开第一台设备（libbladeRF.h:163-164）
        self.device_identifier = device_identifier
        self._lib: Optional[ctypes.CDLL] = None
        self._dev: Optional[ctypes.c_void_p] = None
        self.connected = False
        self._freq_hz: int = 100_000_000
        self._sample_rate_hz: int = self.params.default_sr_hz
        self._bandwidth_hz: int = self.params.max_bw_hz
        self._rx_gain_db: int = 0
        self._rx_running: bool = False
        # 默认 sync config 参数（libbladeRF.h:2776 附近示例）
        self._num_buffers: int = 16
        self._buffer_size: int = 8192   # samples per buffer (SC16_Q11)
        self._num_transfers: int = 8
        self._timeout_ms: int = 5000

    # ── 库加载 ─────────────────────────────────────────────────────────
    def _load_lib(self) -> Optional[ctypes.CDLL]:
        """加载 libbladeRF；失败返回 None（调用方据此优雅降级）。"""
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
        logger.warning("未找到 libbladeRF（%s），BladeRFBackend 将以未连接状态运行",
                       " / ".join(self._LIBNAMES))
        return None

    @staticmethod
    def _configure_prototypes(lib: ctypes.CDLL) -> None:
        """按 libbladeRF.h 声明设置 ctypes 函数原型（argtypes/restype）。"""
        # int bladerf_open(struct bladerf **device, const char *devstr)  (:211)
        lib.bladerf_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p]
        lib.bladerf_open.restype = ctypes.c_int
        # void bladerf_close(struct bladerf *device)  (:225)
        lib.bladerf_close.argtypes = [ctypes.c_void_p]
        lib.bladerf_close.restype = None
        # int bladerf_set_frequency(dev, ch, uint64_t freq)  (:1287)
        lib.bladerf_set_frequency.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint64]
        lib.bladerf_set_frequency.restype = ctypes.c_int
        # int bladerf_get_frequency(dev, ch, uint64_t *freq)  (:1300)
        lib.bladerf_get_frequency.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.POINTER(ctypes.c_uint64)]
        lib.bladerf_get_frequency.restype = ctypes.c_int
        # int bladerf_set_sample_rate(dev, ch, uint32_t rate, uint32_t *actual)  (:1066)
        lib.bladerf_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                ctypes.c_uint32,
                                                ctypes.POINTER(ctypes.c_uint32)]
        lib.bladerf_set_sample_rate.restype = ctypes.c_int
        # int bladerf_set_bandwidth(dev, ch, uint32_t bw, uint32_t *actual)  (:1172)
        lib.bladerf_set_bandwidth.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.c_uint32)]
        lib.bladerf_set_bandwidth.restype = ctypes.c_int
        # int bladerf_set_gain(dev, ch, int gain)  (:838)
        lib.bladerf_set_gain.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.bladerf_set_gain.restype = ctypes.c_int
        # int bladerf_enable_module(dev, ch, bool enable)  (:2655)
        lib.bladerf_enable_module.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_bool]
        lib.bladerf_enable_module.restype = ctypes.c_int
        # int bladerf_sync_config(dev, layout, format,
        #                         num_buffers, buffer_size, num_transfers, timeout_ms)  (:2776)
        lib.bladerf_sync_config.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_uint32, ctypes.c_uint32,
                                            ctypes.c_uint32, ctypes.c_uint32]
        lib.bladerf_sync_config.restype = ctypes.c_int
        # int bladerf_sync_rx(dev, void *samples, unsigned int n,
        #                      metadata *meta, unsigned int *n_ret)  (:2858)
        lib.bladerf_sync_rx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_uint, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_uint)]
        lib.bladerf_sync_rx.restype = ctypes.c_int

    # ── 连接 / 断开 ────────────────────────────────────────────────────
    def connect(self) -> bool:
        """真实打开 libbladeRF 设备；失败（无库/无硬件）返回 False，不造假。"""
        lib = self._load_lib()
        if lib is None:
            self.connected = False
            return False
        try:
            dev = ctypes.c_void_p()
            ident = self.device_identifier.encode("ascii") if self.device_identifier else None
            rc = lib.bladerf_open(ctypes.byref(dev), ident)  # libbladeRF.h:211
            if rc != BLADERF_OK or dev.value is None:
                logger.warning("bladerf_open 失败 rc=%d（未插 bladeRF？）", rc)
                self.connected = False
                return False
            self._dev = dev
            self.connected = True
            return True
        except Exception as e:  # pragma: no cover - 依赖硬件环境
            logger.warning("BladeRF connect 异常: %s", e)
            self.connected = False
            return False

    def disconnect(self) -> None:
        if self._rx_running:
            self.stop_rx()
        if self._lib is not None and self._dev is not None:
            try:
                self._lib.bladerf_close(self._dev)  # libbladeRF.h:225
            except Exception:
                pass
        self._dev = None
        self.connected = False

    # ── 频率 / 采样率 / 带宽 ───────────────────────────────────────────
    def set_frequency(self, freq_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_frequency(freq_hz):
            logger.warning("频率 %s Hz 超出 bladeRF RX 量程 %d-%d Hz",
                           freq_hz, self.params.min_freq_hz, self.params.max_freq_hz)
            return False
        rc = self._lib.bladerf_set_frequency(
            self._dev, BLADERF_CHANNEL_RX0, int(freq_hz))  # libbladeRF.h:1287
        if rc == BLADERF_OK:
            self._freq_hz = int(freq_hz)
            return True
        return False

    def get_frequency(self) -> int:
        if not self.connected or self._lib is None or self._dev is None:
            return self._freq_hz
        f = ctypes.c_uint64(0)
        rc = self._lib.bladerf_get_frequency(
            self._dev, BLADERF_CHANNEL_RX0, ctypes.byref(f))  # libbladeRF.h:1300
        if rc == BLADERF_OK:
            self._freq_hz = int(f.value)
        return self._freq_hz

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_sample_rate(rate_hz):
            logger.warning("采样率 %s Hz 超出 bladeRF 量程 %d-%d Hz",
                           rate_hz, self.params.min_sr_hz, self.params.max_sr_hz)
            return False
        actual = ctypes.c_uint32(0)
        # libbladeRF.h:1066 — 同时返回实际生效的采样率
        rc = self._lib.bladerf_set_sample_rate(
            self._dev, BLADERF_CHANNEL_RX0, int(rate_hz), ctypes.byref(actual))
        if rc == BLADERF_OK:
            self._sample_rate_hz = int(actual.value)
            return True
        return False

    def get_sample_rate(self) -> int:
        return self._sample_rate_hz

    def set_bandwidth(self, bw_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_bandwidth(bw_hz):
            logger.warning("带宽 %s Hz 超出 bladeRF 量程 %d-%d Hz",
                           bw_hz, self.params.min_bw_hz, self.params.max_bw_hz)
            return False
        actual = ctypes.c_uint32(0)
        # libbladeRF.h:1172 — 设备会选最接近的离散带宽档
        rc = self._lib.bladerf_set_bandwidth(
            self._dev, BLADERF_CHANNEL_RX0, int(bw_hz), ctypes.byref(actual))
        if rc == BLADERF_OK:
            self._bandwidth_hz = int(actual.value)
            return True
        return False

    def get_bandwidth(self) -> int:
        return self._bandwidth_hz

    # ── 增益 ──────────────────────────────────────────────────────────
    def set_gain(self, gain_db: int) -> bool:
        """设置 RX 总增益（bladerf_set_gain, libbladeRF.h:838）。

        bladeRF 2.0 (AD9361) 总增益范围见 bladerf2_common.h:344-441。
        """
        if not self.connected or self._lib is None:
            return False
        rc = self._lib.bladerf_set_gain(
            self._dev, BLADERF_CHANNEL_RX0, int(gain_db))
        if rc == BLADERF_OK:
            self._rx_gain_db = int(gain_db)
            return True
        return False

    def set_rxvga1(self, db: int) -> bool:
        """legacy 分级：pre-LPF VGA，钳到 5-30 dB（bladeRF1.h:154,160）。

        仅 bladeRF 1.0 (LMS6002D) 支持；bladeRF 2.0 走 bladerf_set_gain。
        """
        if not self.connected or self._lib is None:
            return False
        v = self.params.clamp_rxvga1(db)
        # bladerf_set_rxvga1 是 deprecated API（bladeRF1.h:324），在 libbladeRF.so
        # 里仍导出；找不到符号时优雅返回 False。
        try:
            fn = getattr(self._lib, "bladerf_set_rxvga1")
        except AttributeError:
            logger.warning("libbladeRF 未导出 bladerf_set_rxvga1（bladeRF 2.0 用总增益 API）")
            return False
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn.restype = ctypes.c_int
        rc = fn(self._dev, v)
        return rc == BLADERF_OK

    def set_rxvga2(self, db: int) -> bool:
        """legacy 分级：post-LPF VGA，钳到 0-30 dB（bladeRF1.h:166,172）。"""
        if not self.connected or self._lib is None:
            return False
        v = self.params.clamp_rxvga2(db)
        try:
            fn = getattr(self._lib, "bladerf_set_rxvga2")
        except AttributeError:
            return False
        fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
        fn.restype = ctypes.c_int
        rc = fn(self._dev, v)
        return rc == BLADERF_OK

    # ── RX 流 ──────────────────────────────────────────────────────────
    def start_rx(self) -> bool:
        """配置并启动接收流（bladerf_sync_config + bladerf_enable_module）。"""
        if not self.connected or self._lib is None or self._dev is None:
            return False
        try:
            # libbladeRF.h:2776 — 配置同步流：SC16_Q11, RX_X1
            rc = self._lib.bladerf_sync_config(
                self._dev, BLADERF_LAYOUT_RX_X1, SAMPLE_FORMAT_DEFAULT,
                self._num_buffers, self._buffer_size,
                self._num_transfers, self._timeout_ms)
            if rc != BLADERF_OK:
                logger.warning("bladerf_sync_config 失败 rc=%d", rc)
                return False
            # libbladeRF.h:2655 — 使能 RX 模块
            rc = self._lib.bladerf_enable_module(
                self._dev, BLADERF_CHANNEL_RX0, True)
            if rc != BLADERF_OK:
                logger.warning("bladerf_enable_module(RX) 失败 rc=%d", rc)
                return False
            self._rx_running = True
            return True
        except Exception as e:  # pragma: no cover
            logger.warning("start_rx 异常: %s", e)
            return False

    def stop_rx(self) -> bool:
        """停止接收流（bladerf_enable_module(RX, False)）。"""
        if not self.connected or self._lib is None or self._dev is None:
            return False
        rc = self._lib.bladerf_enable_module(
            self._dev, BLADERF_CHANNEL_RX0, False)
        self._rx_running = False
        return rc == BLADERF_OK

    def read_samples(self, num_samples: int) -> Optional[bytes]:
        """读取 num_samples 个复采样（SC16_Q11 = 4 字节/复采样）。

        返回原始 bytes；无设备/未启动/超时返回 None（不造假数据）。
        来源: libbladeRF.h:2858 bladerf_sync_rx(dev, buf, n, NULL, &n_ret)。
        """
        if not self._rx_running or self._lib is None or self._dev is None:
            return None
        n_bytes = num_samples * BYTES_PER_IQ_PAIR
        buf = (ctypes.c_int16 * (num_samples * 2))()
        n_ret = ctypes.c_uint(0)
        try:
            # libbladeRF.h:2858 — metadata=NULL 即普通同步读
            rc = self._lib.bladerf_sync_rx(
                self._dev, buf, num_samples, None, ctypes.byref(n_ret))
        except Exception as e:  # pragma: no cover
            logger.warning("bladerf_sync_rx 异常: %s", e)
            return None
        if rc != BLADERF_OK:
            return None
        got = int(n_ret.value) * BYTES_PER_IQ_PAIR
        return bytes(buf)[:got]

    # ── 状态 ───────────────────────────────────────────────────────────
    def status_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "device_identifier": self.device_identifier or "<first>",
            "freq_hz": self._freq_hz,
            "sample_rate_hz": self._sample_rate_hz,
            "bandwidth_hz": self._bandwidth_hz,
            "rx_gain_db": self._rx_gain_db,
            "rx_running": self._rx_running,
            "has_libbladeRF": self._load_lib() is not None,
            "model": self.params.model,
        }


if __name__ == "__main__":
    import json
    p = BladeRFParams()
    print(json.dumps(p.summary(), ensure_ascii=False, indent=2))
    b = BladeRFBackend()
    print("connect() without device →", b.connect(), "(expect False)")

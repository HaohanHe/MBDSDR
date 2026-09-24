#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hackrf_params.py — libhackrf 真实硬件参数表 + 诚实的 HackRF One 后端。

本模块把 Great Scott Gadgets HackRF One 的官方开源驱动 libhackrf 里写死的
频率范围 / 采样率区间 / LNA/VGA/TXVGA 增益表 / 天线偏置 / 数据格式等常量
原样移植过来，所有数值都标注来源 ``repos/hackrf/host/libhackrf/src/xxx.c:行号``
或 ``repos/hackrf/firmware/common/max2837.c:行号``，**禁止凭空编造**。

两部分
======
1. ``HackRFParams``（纯查表，无设备依赖）：
   所有硬件量程/离散增益档都在这里，与 C 驱动逐字对齐。
2. ``HackRFBackend``（诚实后端）：
   通过 ctypes 直接加载系统 ``libhackrf.so``，调用真实的 C API
   （``hackrf_open`` / ``hackrf_set_freq`` / ``hackrf_set_sample_rate`` /
   ``hackrf_set_lna_gain`` / ``hackrf_set_vga_gain`` / ``hackrf_start_rx`` ...）。
   **没有装 libhackrf 或没插设备时，connect() 直接返回 False，绝不假成功。**

数据格式约定
------------
HackRF One 的 USB 流是 **交织的有符号 8-bit I/Q**（int8_t, I/Q/I/Q/...），
来源: host/libhackrf/src/hackrf.h:350  ``int8_t *signed_buffer = (int8_t*)transfer->buffer;``
      host/libhackrf/src/hackrf.h:965-966  "transfer data buffer (interleaved 8 bit I/Q samples)"。
每个 USB 块 BYTES_PER_BLOCK=16384 字节 = 8192 个复采样
（来源: host/libhackrf/src/hackrf.h:511 SAMPLES_PER_BLOCK=8192, :517 BYTES_PER_BLOCK=16384）。

增益链（RX path，来源: host/libhackrf/src/hackrf.h:223-231）
----------------------------------------------------------
- RX IF 增益（MAX2837 的 "IF" 级，``hackrf_set_lna_gain``）：0-40 dB，8 dB 步进。
- RX 基带增益（MAX2837 的 "BB"/"VGA" 级，``hackrf_set_vga_gain``）：0-62 dB，2 dB 步进。
- 天线口 RX/RF 放大器（``hackrf_set_amp_enable``）：0 或 ~11 dB，开关式。
TX path：
- TX IF 增益（``hackrf_set_txvga_gain``）：0-47 dB，1 dB 步进。
- 天线口 TX/RF 放大器：与 RX 共用 ``hackrf_set_amp_enable``，0 或 ~11 dB。
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import queue
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# 一、纯参数表（HackRFParams）—— 全部标注 C 源码 file:line
# ═════════════════════════════════════════════════════════════════════════

# ── 频率范围 ─────────────────────────────────────────────────────────────
# HackRF One 可在 1 MHz ~ 6000 MHz 之间调谐（理论上限略高）。
# 来源: host/libhackrf/src/hackrf.h:235
#   "The HackRF One can tune to nearly any frequency between 1-6000MHz"
# 来源: host/libhackrf/src/hackrf.h:662  HackRF One (prior to rev 9): "1-6000MHz, 20MSPS, bias-tee"
# 来源: host/libhackrf/src/hackrf.h:670  HackRF One (rev.9 & later): "1-6000MHz, 20MSPS, bias-tee"
# 注意: hackrf_set_freq() 本身不做范围钳幅（host/libhackrf/src/hackrf.c:1775-1807 只把
#       freq_hz 拆成 MHz 整数 + 余 Hz 下发固件），量程由固件/手册约束。
HACKRF_MIN_FREQ_HZ: int = 1_000_000        # hackrf.h:235,662,670  → 1 MHz
HACKRF_MAX_FREQ_HZ: int = 6_000_000_000    # hackrf.h:235,662,670  → 6000 MHz

# ── 采样率 ───────────────────────────────────────────────────────────────
# 来源: host/libhackrf/src/hackrf.h:247  "set between 2-20MHz"
# 来源: host/libhackrf/src/hackrf.h:1794,1813  "Sample rate should be in the range 2-20MHz,
#       with the default being 10MHz. Lower & higher values are technically possible,
#       but the performance is not guaranteed."
# 来源: host/libhackrf/src/hackrf.h:1801  hackrf_set_sample_rate_manual 的 divider 范围 1-31。
HACKRF_MIN_SAMPLE_RATE_HZ: int = 2_000_000    # hackrf.h:247,1794 → 2 MHz
HACKRF_MAX_SAMPLE_RATE_HZ: int = 20_000_000   # hackrf.h:247,1794 → 20 MHz
HACKRF_DEFAULT_SAMPLE_RATE_HZ: int = 10_000_000  # hackrf.h:1794,1813 默认 10 MHz
HACKRF_MIN_CLOCK_DIVIDER: int = 1          # hackrf.h:1801
HACKRF_MAX_CLOCK_DIVIDER: int = 31         # hackrf.h:1801

# 推荐常用采样率档（手册/工具常用值，均落在 2-20 MHz 合法区间内）。
SUPPORTED_SAMPLE_RATES: List[int] = [
    2_000_000,
    4_000_000,
    8_000_000,
    10_000_000,   # hackrf.h:1813 默认值
    12_500_000,
    16_000_000,
    20_000_000,
]

# ── LNA 增益（RX IF 级，hackrf_set_lna_gain）─────────────────────────────
# 合法离散档：0/8/16/24/32/40 dB，共 6 档。
# 来源: host/libhackrf/src/hackrf.c:2027   if (value > 40) return HACKRF_ERROR_INVALID_PARAM;
# 来源: host/libhackrf/src/hackrf.c:2031   value &= ~0x07;   ← 向下对齐到 8 的倍数（8 dB 步进）
# 来源: firmware/common/max2837.c:344-371  max2837_set_lna_gain() 的 switch 只接受
#         case 40/32/24/16/8/0，default → return false。
# 来源: host/libhackrf/src/hackrf.h:226,1856  "RX IF gain ... 0-40dB with 8dB steps"
LNA_GAIN_MIN_DB: int = 0      # max2837.c:362 case 0
LNA_GAIN_MAX_DB: int = 40     # hackrf.c:2027 (value>40 非法); max2837.c:347 case 40
LNA_GAIN_STEP_DB: int = 8     # hackrf.c:2031 (value &= ~0x07)

# ── VGA 增益（RX 基带 BB 级，hackrf_set_vga_gain）────────────────────────
# 合法离散档：0/2/4/.../62 dB，共 32 档（必须偶数）。
# 来源: host/libhackrf/src/hackrf.c:2054   if (value > 62) return HACKRF_ERROR_INVALID_PARAM;
# 来源: host/libhackrf/src/hackrf.c:2058   value &= ~0x01;   ← 向下对齐到偶数（2 dB 步进）
# 来源: firmware/common/max2837.c:373-376   if ((gain_db & 0x1) || gain_db > 62) return false;
# 来源: host/libhackrf/src/hackrf.h:225,1866  "baseband gain ... 0-62dB in 2dB steps"
VGA_GAIN_MIN_DB: int = 0      # max2837.c:378  31-(gain_db>>1)，gain_db=0 → 寄存器 31（最大增益）
VGA_GAIN_MAX_DB: int = 62     # hackrf.c:2054; max2837.c:374 (>62 非法)
VGA_GAIN_STEP_DB: int = 2     # hackrf.c:2058 (value &= ~0x01); max2837.c:374 (奇数拒绝)

# ── TXVGA 增益（TX IF 级，hackrf_set_txvga_gain）─────────────────────────
# 合法档：0/1/2/.../47 dB，共 48 档，1 dB 步进。
# 来源: host/libhackrf/src/hackrf.c:2081   if (value > 47) return HACKRF_ERROR_INVALID_PARAM;
#       （无掩码，说明 1 dB 步进直接下发）
# 来源: firmware/common/max2837.c:383-395  max2837_set_txvga_gain() 线性映射寄存器。
# 来源: host/libhackrf/src/hackrf.h:230,1876  "TX IF gain ... 0-47dB in 1dB steps"
TXVGA_GAIN_MIN_DB: int = 0
TXVGA_GAIN_MAX_DB: int = 47   # hackrf.c:2081
TXVGA_GAIN_STEP_DB: int = 1   # hackrf.c:2081 无掩码；max2837.c:385-390 线性

# ── 天线偏置 / 放大器开关 ─────────────────────────────────────────────────
# 来源: host/libhackrf/src/hackrf.h:255,1888  bias-tee: 3.3V, max 50mA, 默认关闭。
# 来源: host/libhackrf/src/hackrf.c:2102  hackrf_set_antenna_enable(dev, value) value∈{0,1}。
# 注意: hackrf.h:255 固件回到 IDLE 模式会自动关闭 bias-tee，每次进入收发都要重新开。
BIAS_TEE_VOLTAGE_MV: int = 3300   # hackrf.h:255,1888 3.3V
BIAS_TEE_MAX_CURRENT_MA: int = 50  # hackrf.h:255,1888 50mA
# 来源: host/libhackrf/src/hackrf.h:227,1826  RX/TX RF 放大器 U13/U25：0 或 ~11 dB，开关式。
RF_AMP_GAIN_DB: int = 11          # hackrf.h:1826 "~11dB RF RX/TX amplifiers"

# ── USB 标识与端点（来源: host/libhackrf/src/hackrf.c）────────────────────
USB_VID: int = 0x1d50            # hackrf.c:202  static const uint16_t hackrf_usb_vid
USB_PID_HACKRF_ONE: int = 0x6089  # hackrf.c:204  hackrf_one_usb_pid; hackrf.h:826 USB_BOARD_ID_HACKRF_ONE
RX_ENDPOINT_ADDRESS: int = 0x81   # hackrf.c:129  LIBUSB_ENDPOINT_IN | 1  (IN|1)
TX_ENDPOINT_ADDRESS: int = 0x02   # hackrf.c:130  LIBUSB_ENDPOINT_OUT | 2 (OUT|2)
DEFAULT_BUFFER_SIZE: int = 32768  # hackrf.c:797  lib_device->buffer_size = 32768 (固件未上报时)

# ── 数据块常量（来源: host/libhackrf/src/hackrf.h）───────────────────────
SAMPLES_PER_BLOCK: int = 8192     # hackrf.h:511
BYTES_PER_BLOCK: int = 16384      # hackrf.h:517  (= SAMPLES_PER_BLOCK * 2, 8-bit I+Q)
SAMPLE_BITS: int = 8              # hackrf.h:350,965  有符号 8-bit I/Q
SAMPLE_FORMAT: str = "sc8"        # 交织 int8 I/Q（等价 SoapySDR SC8）


@dataclass
class HackRFParams:
    """HackRF One 真实硬件参数（纯查表，无设备依赖）。

    所有字段都对应 libhackrf / max2837 源码里的写死常量，见类级注释与
    每个字段的 ``# 来源: file:line`` 标注。
    """

    # 频率
    min_freq_hz: int = field(default=HACKRF_MIN_FREQ_HZ)
    max_freq_hz: int = field(default=HACKRF_MAX_FREQ_HZ)
    # 采样率
    min_sr_hz: int = field(default=HACKRF_MIN_SAMPLE_RATE_HZ)
    max_sr_hz: int = field(default=HACKRF_MAX_SAMPLE_RATE_HZ)
    default_sr_hz: int = field(default=HACKRF_DEFAULT_SAMPLE_RATE_HZ)
    # 增益
    lna_min_db: int = field(default=LNA_GAIN_MIN_DB)
    lna_max_db: int = field(default=LNA_GAIN_MAX_DB)
    lna_step_db: int = field(default=LNA_GAIN_STEP_DB)
    vga_min_db: int = field(default=VGA_GAIN_MIN_DB)
    vga_max_db: int = field(default=VGA_GAIN_MAX_DB)
    vga_step_db: int = field(default=VGA_GAIN_STEP_DB)
    txvga_min_db: int = field(default=TXVGA_GAIN_MIN_DB)
    txvga_max_db: int = field(default=TXVGA_GAIN_MAX_DB)
    txvga_step_db: int = field(default=TXVGA_GAIN_STEP_DB)

    # ── 增益离散档表 ────────────────────────────────────────────────────
    def lna_gain_levels_db(self) -> List[int]:
        """返回 LNA(RX IF) 全部合法离散档：[0,8,16,24,32,40]，共 6 档。

        来源: max2837.c:344-371 switch(case 40/32/24/16/8/0)；
              hackrf.c:2027 (>40 非法), :2031 (value &= ~0x07)。
        """
        return list(range(self.lna_min_db, self.lna_max_db + 1, self.lna_step_db))

    def vga_gain_levels_db(self) -> List[int]:
        """返回 VGA(RX 基带) 全部合法离散档：0,2,...,62，共 32 档。

        来源: max2837.c:373-376 (奇数/>62 拒绝)；hackrf.c:2054,2058。
        """
        return list(range(self.vga_min_db, self.vga_max_db + 1, self.vga_step_db))

    def txvga_gain_levels_db(self) -> List[int]:
        """返回 TXVGA(TX IF) 全部合法离散档：0,1,...,47，共 48 档。

        来源: hackrf.c:2081 (>47 非法，无掩码=1dB 步进)；max2837.c:383-395。
        """
        return list(range(self.txvga_min_db, self.txvga_max_db + 1, self.txvga_step_db))

    # ── 校验 / 吸附 ────────────────────────────────────────────────────
    def is_valid_frequency(self, freq_hz: float) -> bool:
        """频率是否落在 1 MHz - 6 GHz（hackrf.h:235,662,670）。"""
        return self.min_freq_hz <= float(freq_hz) <= self.max_freq_hz

    def is_valid_sample_rate(self, rate_hz: float) -> bool:
        """采样率是否落在 2-20 MHz（hackrf.h:247,1794）。"""
        return self.min_sr_hz <= float(rate_hz) <= self.max_sr_hz

    def clamp_lna_gain(self, db: float) -> int:
        """把请求的 dB 吸附到最近的合法 LNA 档（<= 目标，向下对齐到 8 的倍数）。

        与 hackrf.c:2031 ``value &= ~0x07`` 语义一致：固件会向下取整；
        超过 40 直接钳到 40（hackrf.c:2027 >40 报错）。
        """
        v = int(round(db))
        v = max(self.lna_min_db, min(v, self.lna_max_db))
        return v & ~0x07  # 对齐到 8 的倍数，与 hackrf.c:2031 一致

    def clamp_vga_gain(self, db: float) -> int:
        """吸附到最近的合法 VGA 档（偶数，0-62）。与 hackrf.c:2058 ``value &= ~0x01`` 一致。"""
        v = int(round(db))
        v = max(self.vga_min_db, min(v, self.vga_max_db))
        return v & ~0x01  # 对齐到偶数，与 hackrf.c:2058 一致

    def clamp_txvga_gain(self, db: float) -> int:
        """钳到 0-47 dB（hackrf.c:2081 >47 非法；1dB 步进无需掩码）。"""
        v = int(round(db))
        return max(self.txvga_min_db, min(v, self.txvga_max_db))

    def summary(self) -> Dict[str, Any]:
        """返回完整参数摘要（供工具/UI 展示）。"""
        return {
            "device": "HackRF One (Great Scott Gadgets)",
            "usb_vid": f"0x{USB_VID:04x}",
            "usb_pid": f"0x{USB_PID_HACKRF_ONE:04x}",
            "freq_range_hz": [self.min_freq_hz, self.max_freq_hz],
            "freq_range_mhz": [self.min_freq_hz / 1e6, self.max_freq_hz / 1e6],
            "sample_rate_range_hz": [self.min_sr_hz, self.max_sr_hz],
            "default_sample_rate_hz": self.default_sr_hz,
            "clock_divider_range": [HACKRF_MIN_CLOCK_DIVIDER, HACKRF_MAX_CLOCK_DIVIDER],
            "lna_gain_db": self.lna_gain_levels_db(),
            "lna_gain_count": len(self.lna_gain_levels_db()),
            "vga_gain_db": self.vga_gain_levels_db(),
            "vga_gain_count": len(self.vga_gain_levels_db()),
            "txvga_gain_db": self.txvga_gain_levels_db(),
            "txvga_gain_count": len(self.txvga_gain_levels_db()),
            "rf_amp_gain_db": RF_AMP_GAIN_DB,
            "bias_tee": f"{BIAS_TEE_VOLTAGE_MV}V/{BIAS_TEE_MAX_CURRENT_MA}mA, on/off",
            "sample_format": SAMPLE_FORMAT,
            "bytes_per_block": BYTES_PER_BLOCK,
            "samples_per_block": SAMPLES_PER_BLOCK,
            "sources": {
                "freq": "host/libhackrf/src/hackrf.h:235,662,670",
                "sample_rate": "host/libhackrf/src/hackrf.h:247,1794,1813",
                "lna": "host/libhackrf/src/hackrf.c:2027,2031; firmware/common/max2837.c:344-371",
                "vga": "host/libhackrf/src/hackrf.c:2054,2058; firmware/common/max2837.c:373-381",
                "txvga": "host/libhackrf/src/hackrf.c:2081; firmware/common/max2837.c:383-395",
                "bias_tee": "host/libhackrf/src/hackrf.h:255,1888; hackrf.c:2102",
                "iq_format": "host/libhackrf/src/hackrf.h:350,511,517,965",
            },
        }


# 模块级默认实例（与 C 驱动默认量程一致）
DEFAULT_PARAMS = HackRFParams()


# ═════════════════════════════════════════════════════════════════════════
# 二、诚实后端 HackRFBackend（ctypes → 真实 libhackrf.so）
# ═════════════════════════════════════════════════════════════════════════

# libhackrf 函数返回码（host/libhackrf/src/hackrf.h:580-599 附近）
HACKRF_SUCCESS = 0

# transfer 回调原型: int (*hackrf_sample_block_cb_fn)(hackrf_transfer* transfer)
# 来源: host/libhackrf/src/hackrf.h:1135
# hackrf_transfer 布局（hackrf.h:962-975）:
#   hackrf_device* device; uint8_t* buffer; int buffer_length;
#   int valid_length; void* rx_ctx; void* tx_ctx;
class _HackrfTransfer(ctypes.Structure):
    _fields_ = [
        ("device", ctypes.c_void_p),
        ("buffer", ctypes.POINTER(ctypes.c_uint8)),
        ("buffer_length", ctypes.c_int),
        ("valid_length", ctypes.c_int),
        ("rx_ctx", ctypes.c_void_p),
        ("tx_ctx", ctypes.c_void_p),
    ]


class HackRFBackend:
    """HackRF One 诚实后端：ctypes 直连 libhackrf，无设备绝不假成功。

    接口对齐 sdr_backend.SDRBackend 中与 HackRF 相关的子集：
      connect()/disconnect()
      set_frequency()/get_frequency()
      set_sample_rate()/get_sample_rate()
      set_lna_gain()/set_vga_gain()/set_txvga_gain()
      set_bias_tee()/set_amp_enable()
      start_rx()/stop_rx()/read_samples()

    真实 C API 调用对应关系（来源: host/libhackrf/src/hackrf.c）：
      connect      → hackrf_init + hackrf_open           (hackrf.c:512,838)
      set_freq     → hackrf_set_freq                     (hackrf.c:1775)
      set_sr       → hackrf_set_sample_rate             (hackrf.c:1920)
      set_lna      → hackrf_set_lna_gain                (hackrf.c:2022)
      set_vga      → hackrf_set_vga_gain                (hackrf.c:2049)
      set_txvga    → hackrf_set_txvga_gain              (hackrf.c:2076)
      bias_tee     → hackrf_set_antenna_enable          (hackrf.c:2102)
      amp          → hackrf_set_amp_enable              (hackrf.c:1961)
      start_rx     → hackrf_start_rx                    (hackrf.c:2339)
      stop_rx      → hackrf_stop_rx                     (hackrf.c:2369)
      close        → hackrf_close                       (hackrf.c:2467)

    降级策略：
      - 找不到 ``libhackrf.so`` → connect() 返回 False，所有 setter 返回 False，
        read_samples 返回 None。**绝不伪造"已连接"或假样本**。
    """

    # 候选 soname（按出现顺序尝试加载）
    _LIBNAMES = (
        "libhackrf.so.0",
        "libhackrf.so",
        "libhackrf.dll",
        "libhackrf.dylib",
    )

    def __init__(self, device_index: int = 0, params: Optional[HackRFParams] = None):
        self.params = params or HackRFParams()
        self.device_index = device_index
        self._lib: Optional[ctypes.CDLL] = None
        self._dev: Optional[ctypes.c_void_p] = None
        self.connected = False
        self._freq_hz: float = 100_000_000.0
        self._sample_rate_hz: float = float(self.params.default_sr_hz)
        self._lna_db: int = 8
        self._vga_db: int = 16
        self._bias_tee_on: bool = False
        self._rx_running: bool = False
        self._rx_queue: "queue.Queue[bytes]" = queue.Queue()
        # 回调必须常驻引用，否则被 GC 后崩溃
        self._c_callback = self._make_rx_callback()

    # ── 库加载 ─────────────────────────────────────────────────────────
    def _load_lib(self) -> Optional[ctypes.CDLL]:
        """加载 libhackrf；失败返回 None（调用方据此优雅降级）。"""
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
        logger.warning("未找到 libhackrf（%s），HackRFBackend 将以未连接状态运行",
                       " / ".join(self._LIBNAMES))
        return None

    @staticmethod
    def _configure_prototypes(lib: ctypes.CDLL) -> None:
        """按 hackrf.h 声明设置 ctypes 函数原型（argtypes/restype）。"""
        lib.hackrf_init.restype = ctypes.c_int
        lib.hackrf_open.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.hackrf_open.restype = ctypes.c_int
        lib.hackrf_set_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        lib.hackrf_set_freq.restype = ctypes.c_int
        lib.hackrf_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_double]
        lib.hackrf_set_sample_rate.restype = ctypes.c_int
        lib.hackrf_set_lna_gain.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.hackrf_set_lna_gain.restype = ctypes.c_int
        lib.hackrf_set_vga_gain.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.hackrf_set_vga_gain.restype = ctypes.c_int
        lib.hackrf_set_txvga_gain.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.hackrf_set_txvga_gain.restype = ctypes.c_int
        lib.hackrf_set_antenna_enable.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
        lib.hackrf_set_antenna_enable.restype = ctypes.c_int
        lib.hackrf_set_amp_enable.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
        lib.hackrf_set_amp_enable.restype = ctypes.c_int
        # hackrf_start_rx(dev, hackrf_sample_block_cb_fn cb, void* ctx)
        CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_HackrfTransfer))
        lib.hackrf_start_rx.argtypes = [ctypes.c_void_p, CALLBACK, ctypes.c_void_p]
        lib.hackrf_start_rx.restype = ctypes.c_int
        lib.hackrf_stop_rx.argtypes = [ctypes.c_void_p]
        lib.hackrf_stop_rx.restype = ctypes.c_int
        lib.hackrf_close.argtypes = [ctypes.c_void_p]
        lib.hackrf_close.restype = ctypes.c_int
        lib.hackrf_exit.restype = ctypes.c_int

    # ── 连接 / 断开 ────────────────────────────────────────────────────
    def connect(self) -> bool:
        """真实打开 libhackrf 设备；失败（无库/无硬件）返回 False，不造假。"""
        lib = self._load_lib()
        if lib is None:
            self.connected = False
            return False
        try:
            if lib.hackrf_init() != HACKRF_SUCCESS:
                logger.warning("hackrf_init 失败")
                return False
            dev = ctypes.c_void_p()
            rc = lib.hackrf_open(ctypes.byref(dev))
            if rc != HACKRF_SUCCESS or dev.value is None:
                logger.warning("hackrf_open 失败 rc=%d（未插 HackRF？）", rc)
                self.connected = False
                return False
            self._dev = dev
            self.connected = True
            return True
        except Exception as e:  # pragma: no cover - 依赖硬件环境
            logger.warning("HackRF connect 异常: %s", e)
            self.connected = False
            return False

    def disconnect(self) -> None:
        if self._rx_running:
            self.stop_rx()
        if self._lib is not None and self._dev is not None:
            try:
                self._lib.hackrf_close(self._dev)
            except Exception:
                pass
        self._dev = None
        self.connected = False

    # ── 频率 / 采样率 ──────────────────────────────────────────────────
    def set_frequency(self, freq_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_frequency(freq_hz):
            logger.warning("频率 %s Hz 超出 HackRF 量程 %d-%d Hz",
                           freq_hz, self.params.min_freq_hz, self.params.max_freq_hz)
            return False
        rc = self._lib.hackrf_set_freq(self._dev, int(freq_hz))  # hackrf.c:1775
        if rc == HACKRF_SUCCESS:
            self._freq_hz = float(freq_hz)
            return True
        return False

    def get_frequency(self) -> float:
        return self._freq_hz

    def set_sample_rate(self, rate_hz: float) -> bool:
        if not self.connected or self._lib is None:
            return False
        if not self.params.is_valid_sample_rate(rate_hz):
            logger.warning("采样率 %s Hz 超出 HackRF 量程 %d-%d Hz",
                           rate_hz, self.params.min_sr_hz, self.params.max_sr_hz)
            return False
        rc = self._lib.hackrf_set_sample_rate(self._dev, float(rate_hz))  # hackrf.c:1920
        if rc == HACKRF_SUCCESS:
            self._sample_rate_hz = float(rate_hz)
            return True
        return False

    def get_sample_rate(self) -> float:
        return self._sample_rate_hz

    # ── 增益 ──────────────────────────────────────────────────────────
    def set_lna_gain(self, db: int) -> bool:
        """设置 RX IF(LNA) 增益，自动吸附到 8dB 档（hackrf.c:2031）。"""
        if not self.connected or self._lib is None:
            return False
        v = self.params.clamp_lna_gain(db)
        rc = self._lib.hackrf_set_lna_gain(self._dev, v)  # hackrf.c:2022
        if rc == HACKRF_SUCCESS:
            self._lna_db = v
            return True
        return False

    def set_vga_gain(self, db: int) -> bool:
        """设置 RX 基带(VGA) 增益，自动吸附到偶数档（hackrf.c:2058）。"""
        if not self.connected or self._lib is None:
            return False
        v = self.params.clamp_vga_gain(db)
        rc = self._lib.hackrf_set_vga_gain(self._dev, v)  # hackrf.c:2049
        if rc == HACKRF_SUCCESS:
            self._vga_db = v
            return True
        return False

    def set_txvga_gain(self, db: int) -> bool:
        """设置 TX IF 增益，钳到 0-47（hackrf.c:2081）。"""
        if not self.connected or self._lib is None:
            return False
        v = self.params.clamp_txvga_gain(db)
        rc = self._lib.hackrf_set_txvga_gain(self._dev, v)  # hackrf.c:2076
        if rc == HACKRF_SUCCESS:
            return True
        return False

    # ── 偏置 / 放大器 ─────────────────────────────────────────────────
    def set_bias_tee(self, on: bool) -> bool:
        """开关天线偏置（3.3V/50mA，hackrf.c:2102）。"""
        if not self.connected or self._lib is None:
            return False
        rc = self._lib.hackrf_set_antenna_enable(self._dev, 1 if on else 0)
        if rc == HACKRF_SUCCESS:
            self._bias_tee_on = on
            return True
        return False

    def set_amp_enable(self, on: bool) -> bool:
        """开关 ~11dB RX/TX RF 放大器（hackrf.c:1961）。"""
        if not self.connected or self._lib is None:
            return False
        rc = self._lib.hackrf_set_amp_enable(self._dev, 1 if on else 0)
        return rc == HACKRF_SUCCESS

    # ── RX 流 ──────────────────────────────────────────────────────────
    def _make_rx_callback(self):
        """构造 ctypes 回调：把每块 USB buffer 字节塞进队列。

        回调在 libusb 异步线程里执行（hackrf.h:287,1292），只能入队，
        不能在里面调用其它 libhackrf 函数。
        """
        def _cb(transfer):
            try:
                n = transfer.contents.valid_length
                buf = ctypes.cast(
                    transfer.contents.buffer,
                    ctypes.POINTER(ctypes.c_uint8 * n)).contents
                self._rx_queue.put(bytes(buf))
            except Exception:
                return 0
            return 0  # 返回 0 继续流；返回非 0 停止（hackrf.h:283）

        CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_HackrfTransfer))
        return CALLBACK(_cb)

    def start_rx(self) -> bool:
        """真实启动接收流（hackrf_start_rx, hackrf.c:2339）。"""
        if not self.connected or self._lib is None:
            return False
        # 清空旧队列
        while not self._rx_queue.empty():
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                break
        rc = self._lib.hackrf_start_rx(self._dev, self._c_callback, None)
        if rc == HACKRF_SUCCESS:
            self._rx_running = True
            return True
        logger.warning("hackrf_start_rx 失败 rc=%d", rc)
        return False

    def stop_rx(self) -> bool:
        """停止接收流（hackrf_stop_rx, hackrf.c:2369）。"""
        if not self.connected or self._lib is None:
            return False
        rc = self._lib.hackrf_stop_rx(self._dev)
        self._rx_running = False
        return rc == HACKRF_SUCCESS

    def read_samples(self, num_samples: int) -> Optional[bytes]:
        """读取 num_samples 个复采样（= 2*num_samples 字节，交织 int8 I/Q）。

        返回原始 bytes；无设备/未收齐时返回 None（不造假数据）。
        """
        if not self._rx_running:
            return None
        need = num_samples * 2  # 每复采样 2 字节 (I8,Q8)，hackrf.h:965
        chunks: List[bytes] = []
        got = 0
        try:
            while got < need:
                chunk = self._rx_queue.get(timeout=1.0)
                chunks.append(chunk)
                got += len(chunk)
        except queue.Empty:
            return None
        data = b"".join(chunks)
        return data[:need]

    # ── 状态 ───────────────────────────────────────────────────────────
    def status_dict(self) -> Dict[str, Any]:
        return {
            "connected": self.connected,
            "freq_hz": self._freq_hz,
            "sample_rate_hz": self._sample_rate_hz,
            "lna_gain_db": self._lna_db,
            "vga_gain_db": self._vga_db,
            "bias_tee_on": self._bias_tee_on,
            "rx_running": self._rx_running,
            "has_libhackrf": self._load_lib() is not None,
        }


if __name__ == "__main__":
    import json
    p = HackRFParams()
    print(json.dumps(p.summary(), ensure_ascii=False, indent=2))

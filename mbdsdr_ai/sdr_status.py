# SPDX-License-Identifier: MIT
"""SDR 状态与采样率原语（零 mbdsdr_ai 依赖的叶子模块）。

从 ``mbdsdr_ai.sdr_backend`` 下沉（2026-11-13）：这几个符号只依赖标准库，
原先长在数千行的后端模块里，导致「只想拿一个默认采样率或状态结构」的调用方
被迫依赖整块后端与硬件驱动。``sdr_backend`` 原处再导出同名符号，既有调用方
一行不改；新调用方应直接指向本模块。
"""

import math
from dataclasses import dataclass
from typing import Optional


# 项目统一默认采样率（Hz）。
# 产品口径（2026-11-05 裁定）：取 RTL-SDR 类上限常见值 2.4 MS/s（SDR++ 新设备
# 默认也是 2.4M，main.cpp:202）。"默认"只是**首选**，不是保证：设备若不支持该档，
# 由 nearest_sample_rate() / 各后端能力表回落到最接近的支持值，并把**实际生效值**
# 如实写进 SDRStatus.sample_rate_hz。desktop / agent 等上层一律引用本常量，
# 不要再写裸字面量；数字链路（ADS-B 等）自己声明所需采样率，不依赖此默认。
DEFAULT_SAMPLE_RATE_HZ = 2_400_000


def format_sample_rate(hz) -> str:
    """采样率唯一显示口径（U-03）：``MS/s``、固定 3 位小数、空格分隔。

    采样率是每秒样本数，量纲是 MS/s（样本/秒），不是 MHz（频率/带宽）；
    ``<1 MS/s`` 也写 ``0.250 MS/s``。缺失/NaN 显「—」，不拿 0 或默认值冒充读数。

    本函数是内核侧唯一实现；``desktop/constants.format_sample_rate`` 与
    ``mbdsdr_ai.sdr_tools._fmt_sample_rate`` 均从这里派生（内核 → 桌面）。
    """
    if hz is None:
        return "—"
    try:
        v = float(hz)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v / 1e6:.3f} MS/s"


#: ``SDRStatus.squelch_applied_by`` 的合法取值（Python 与 C++ 回写共用同一组
#: 字面量；中文文档见 docs/limitations-register.md 附 H）。
SQUELCH_APPLIED_BY_VALUES = ("desktop-margin", "cpp-auto", "cpp-manual", "none")


@dataclass
class SDRStatus:
    """SDR 当前状态。"""
    connected: bool = False
    # 来源: gqrx/src/applications/gqrx/receiver.cpp:66 —— GQRX 默认守 144.8 MHz 业余段；
    # 我们面向普通用户首启体验，落在 98 MHz FM 广播段，一上来就能听到东西。
    frequency_hz: float = 98_000_000.0
    # 频率读数来源：request=只下发了请求值，设备未回执；readback=刚从驱动
    # 接口回读；空串=尚未调谐/未观测。界面与工具据此区分「设备确认」与
    # 「请求值」，不得把 request 冒充成设备实际频率。
    frequency_source: str = ""
    # None 表示"未选定，由后端子类决定"。各后端在 __init__ 里设自己的甜点档
    # （RTL 2.048M / HackRF 8M / USRP 1M / Pluto 2M，来源 gqrx ioconfig.cpp:266,279-302）；
    # connect() 时若仍为 None，用 2.048M 兜底。
    sample_rate_hz: Optional[float] = None
    gain_db: float = 0.0
    agc_enabled: bool = True
    bandwidth_hz: float = 0.0  # 0=自动
    demod_mode: str = "FM"  # FM/AM/SSB/LSB/USB/CW/NFM/WFM
    # 来源: gqrx/src/qtgui/dockrxopt.cpp:697 —— reset 到 -150 = 完全开门，
    # 与 analog_demod.py 静噪默认对齐。
    # **语义固定为「余量刻度」**（-100≈0 dB、-50≈6 dB、-20≈16 dB，见
    # mbdsdr_ai/squelch.py 顶部裁定）：它不是绝对 dBFS 门，禁止原样下发给
    # 解调链；换算只在 margin_threshold_db()/BaselineSquelch 里做。
    squelch_db: float = -150.0
    # 谁最后写的静噪值、按什么语义应用：
    # desktop-margin / cpp-auto / cpp-manual / none（空值=从未设置）。
    squelch_applied_by: str = "none"
    # 只有真正下发**绝对门**时才填（dBFS，由 margin_threshold_db + 实测底噪
    # 推导）；Python 路径按余量口径门控时保持 None。
    squelch_threshold_dbfs: Optional[float] = None
    volume: float = 0.5
    # 信号强度：None=从未采到，**不许拿 0/-100 冒充读数**（U-02）。
    # 物理量随后端而定，读 rssi_db 时必须连 rssi_unit 一起读：
    #   - 本地 IQ 链（RTL/HackRF/USRP…）：dBFS（相对 ADC 满量程）
    #   - ai-sdr mini（SI4732，MCP get_status）：dBµV（0–127）
    rssi_db: Optional[float] = None
    rssi_unit: str = "dBFS"
    snr_db: Optional[float] = None
    recording: bool = False
    recording_path: str = ""
    recording_duration: float = 0.0
    device_temp: float = 0.0
    samples_read: int = 0
    uptime_seconds: float = 0.0
    error: str = ""  # 最近一次 setter 失败/硬件回读失败的错误描述，空串=正常
    # ── 接入层可观测性（网络源/热插拔/独占场景；默认值=未知/未接入）──
    # link_state: connecting / connected / stale / disconnected / failed，空串=未接入观测
    link_state: str = ""
    error_category: str = ""       # 与 link_state.py 的错误词表一致（refused/timeout/reset/idle/...）
    error_code: str = ""           # error_catalog 稳定码（link_timeout/link_refused/link_half_open/link_reset/buffer_overflow）
    dropped_samples: int = 0       # 环形缓冲覆盖/流速率不足导致的丢样估计
    buffer_fill: int = 0           # 环形缓冲当前未读样点数（缓冲水位）
    buffer_capacity: int = 0       # 环形缓冲容量（样点），0=未知/无环形缓冲
    buffer_level: str = ""         # empty/low/ok/high（见 link_state.classify_buffer_level）
    underruns: int = 0             # 读侧因缓冲空而等待超时的次数（真实欠读）
    reconnect_count: int = 0       # 自进程启动以来的重连次数
    last_data_age_s: Optional[float] = None  # 距最后一次真实数据的时间（None=从未收到）
    # ── 采样率/带宽切换瞬态（idle/switching/completed/failed）──
    retune_state: str = "idle"       # 当前切换阶段；completed 表示新流已接管
    retune_kind: str = ""            # sample_rate / bandwidth
    retune_generation: int = 0       # 成功切换次数（单调递增，供消费方判新旧流）
    retune_dropped_samples: int = 0  # 切换时丢弃的旧缓冲样点数（如实计数）
    retune_elapsed_ms: float = 0.0   # 最近一次切换耗时
    retune_error: str = ""           # 切换失败原因（空=无）

# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端共享常量
======================
集中存放 desktop 各面板共用的默认值，避免到处散落裸字面量。

采样率常量优先复用内核 mbdsdr_ai.sdr_backend 里的项目统一首选值
``DEFAULT_SAMPLE_RATE_HZ``（单一事实来源，当前 2.4 MS/s；实际生效值由设备
能力回落决定，以状态回报为准）。采样率显示口径 ``format_sample_rate`` 同样
从内核 ``sdr_backend.format_sample_rate`` 直接复用，桌面不再各写一份字面量。
万一 mbdsdr_ai 缺失（例如桌面端单独打包、未装内核依赖），退回字面量
2_400_000 与同口径格式化函数，保证 desktop 仍可导入运行——这是安全回退，
与内核基准一致，不引入新的"假数字"。
"""

import math as _math

try:
    from mbdsdr_ai.sdr_backend import (  # noqa: F401
        DEFAULT_SAMPLE_RATE_HZ,
        format_sample_rate,
    )
except Exception:  # noqa: BLE001 - mbdsdr_ai 不可用时的安全回退
    # 与 mbdsdr_ai.sdr_backend 模块级 DEFAULT_SAMPLE_RATE_HZ 保持一致。
    DEFAULT_SAMPLE_RATE_HZ = 2_400_000

    def format_sample_rate(hz) -> str:
        """安全回退：与内核 ``sdr_backend.format_sample_rate`` 逐字一致。"""
        if hz is None:
            return "—"
        try:
            v = float(hz)
        except (TypeError, ValueError):
            return "—"
        if not _math.isfinite(v):
            return "—"
        return f"{v / 1e6:.3f} MS/s"


# 项目级可调频率范围（Hz）：10 kHz ~ 6 GHz（SDR 全频段）。
# 原先只在控制面板定义，模块面板又写 1 kHz~7.2 GHz（后者是 SoapySDR
# 无范围信息时的兜底值，超过所有在列后端能力），两个面板对
# "能调多少"说法不一。收口到此，各面板共用；实际可调范围仍以设备为准。
FREQ_MIN_HZ = 10_000.0
FREQ_MAX_HZ = 6.0e9


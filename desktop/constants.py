# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端共享常量
======================
集中存放 desktop 各面板共用的默认值，避免到处散落裸字面量。

采样率常量优先复用内核 mbdsdr_ai.sdr_backend 里的项目统一基准
``DEFAULT_SAMPLE_RATE_HZ``（单一事实来源）。万一 mbdsdr_ai 缺失（例如桌面端
单独打包、未装内核依赖），退回字面量 2_048_000 保证 desktop 仍可导入运行——
这是安全回退值，与内核基准一致，不引入新的"假数字"。
"""

try:
    from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ  # noqa: F401
except Exception:  # noqa: BLE001 - mbdsdr_ai 不可用时的安全回退
    # 与 mbdsdr_ai.sdr_backend 模块级 DEFAULT_SAMPLE_RATE_HZ 保持一致。
    DEFAULT_SAMPLE_RATE_HZ = 2_048_000

# 项目级可调频率范围（Hz）：10 kHz ~ 6 GHz（SDR 全频段）。
# 原先只在控制面板定义，模块面板又写 1 kHz~7.2 GHz（后者是 SoapySDR
# 无范围信息时的兜底值，超过所有在列后端能力），两个面板对
# "能调多少"说法不一。收口到此，各面板共用；实际可调范围仍以设备为准。
FREQ_MIN_HZ = 10_000.0
FREQ_MAX_HZ = 6.0e9

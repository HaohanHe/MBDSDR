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

import logging
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

logger = logging.getLogger(__name__)


def sample_rate_combo_items(device) -> list:
    """采样率下拉的 (显示文案, itemData) 唯一事实源。

    档位只来自设备能力 ``mbdsdr_ai.device_capabilities.sample_rate_options``
    （后端离散表 ∩ 设备量程）；桌面不再持有第二份档位表。设备未报告量程
    （含尚未连接）时只给一项显式「未知」说明，data=None，应用侧按
    ``DEFAULT_SAMPLE_RATE_HZ`` 兜底——文案里的默认值与兜底是同一个常量。
    能力查询失败只记日志并按「未知」处理，不编档位。
    """
    options = None
    try:
        from mbdsdr_ai.device_capabilities import sample_rate_options
    except Exception as exc:  # noqa: BLE001 - 缺内核时按未知降级
        logger.warning("采样率能力模块不可用，下拉按未知处理: %s", exc)
    else:
        try:
            if isinstance(device, dict):
                options = sample_rate_options(device)
        except Exception as exc:  # noqa: BLE001 - 解析失败不编档位
            logger.warning("读取设备采样率档位失败，按未知处理: %s: %s",
                           type(exc).__name__, exc)
    if not options:
        # 文案要能塞进 320px 右栏的下拉（原「未知（设备未报告采样率范围，将用
        # 默认 1.024 MS/s）」有 30 字，实测被下拉右边界整段切掉且没有省略号）。
        # 「为什么未知」放进 tooltip，不占下拉宽度。
        return [(
            f"未知（默认 {format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)}）",
            None,
        )]
    return [(format_sample_rate(rate), float(rate)) for rate in options]


def default_sample_rate_combo_index(items, preferred_hz=None) -> int:
    """默认选中档：优先对齐 ``preferred_hz``，未指定时回 ``DEFAULT_SAMPLE_RATE_HZ``，
    两者都缺则选第一项。

    ``preferred_hz`` 把「桌面默认连接率（性能档，省电 1.024 MS/s）」与「设备
    能力基准（内核 2.4 MS/s）」分开：内核常量不动，通用连接下拉的默认选中档
    随性能档走（用户 2026-11-10「电脑没有那么强大…得让路」）。不传该参数的
    调用方（模块面板宽带解码 / 真值源测试）仍按设备基准对齐，行为不变。
    """
    targets = []
    if preferred_hz is not None:
        targets.append(float(preferred_hz))
    targets.append(float(DEFAULT_SAMPLE_RATE_HZ))
    for target in targets:
        for index, (_text, hz) in enumerate(items):
            if hz is not None and float(hz) == target:
                return index
    return 0


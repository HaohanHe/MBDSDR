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

#: 未连接/未确认时采样率默认值的唯一写法（控制面板 / 模块面板 / 设备面板共用）。
#: 光写「2.400 MS/s」会被读成"设备正在跑 2.400 MS/s"——那是没被确认过的默认
#: 值。把默认值与未确认状态写进同一句，和状态栏空态「—」保持同一诚实口径。
DEFAULT_SR_UNCONFIRMED = (
    f"{format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)}（默认 · 未确认）")

logger = logging.getLogger(__name__)


def sample_rate_combo_items(device) -> list:
    """采样率下拉的 (显示文案, itemData) 唯一事实源。

    档位只来自设备能力 ``mbdsdr_ai.device_capabilities.sample_rate_options``
    （后端离散表 ∩ 设备量程）；桌面不再持有第二份档位表。设备未报告量程
    （含尚未连接）时只给一项显式「档位未知」说明，data=None，**不显示任何
    MS/s 数字**（避免被读成设备能跑的速率）；连接时的兜底值由调用方按
    ``DEFAULT_SAMPLE_RATE_HZ`` 决定，不写进下拉文案。
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
        # 文案要能塞进 320px 右栏的下拉；「为什么未知」放进 tooltip。
        # 「未知」必须保留（能力确实没报），且不得夹带 MS/s 数字。
        return [("档位未知，连接设备后探测", None)]
    return [(format_sample_rate(rate), float(rate)) for rate in options]


#: 连接载荷里「本次请求的采样率」的键名。写入只允许走
#: ``set_connect_sample_rate``（全仓唯一写入口，见 docs/redundancy-register.md §5.11.3）。
CONNECT_SAMPLE_RATE_KEY = "_sample_rate"


def connect_sample_rate_hz(requested_hz=None) -> float:
    """连接请求采样率的唯一取值口径：默认值只在这里给一次。

    ``requested_hz`` 为空/非数/NaN/<=0 一律按 ``DEFAULT_SAMPLE_RATE_HZ``——
    载荷与下拉里绝不出现 0 Hz 这种会让后端崩掉、又会被读成读数的假值。
    调用方（设备面板输入框、模块面板能力下拉）各自解析后都交到这里，
    不再各带一份默认。
    """
    try:
        hz = float(requested_hz)
    except (TypeError, ValueError):
        hz = 0.0
    if not _math.isfinite(hz) or hz <= 0.0:
        return float(DEFAULT_SAMPLE_RATE_HZ)
    return hz


def set_connect_sample_rate(payload: dict, requested_hz=None) -> float:
    """把「本次连接请求的采样率」写进连接载荷——全仓唯一写入口。

    此前设备面板（``device_panel.py::_on_connect_clicked``）与模块面板
    （``module_panel.py::_on_connect``）各写一次 ``payload["_sample_rate"]``，
    主窗口读侧还带了第三个默认值：同一份「没填」在三处有三个答案，改协议
    必须三处一起改，漏一处就是沉默错值。收口到本函数后：

    - 写：只有这里能写这个键；
    - 默认：只有 ``connect_sample_rate_hz`` 决定；
    - 读：``MainWindow._on_panel_device_connect`` 只按键取值，不再自带默认。

    返回实际写进载荷的值（Hz），便于调用方留证/测试。
    """
    hz = connect_sample_rate_hz(requested_hz)
    payload[CONNECT_SAMPLE_RATE_KEY] = hz
    return hz


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


def cons_of_talker(talker: str, prn) -> str:
    """NMEA talker 前缀 → 星座名（唯一实现，星座面板与天图共用）。"""
    t = (talker or "").upper()
    if t == "GP":
        return "GPS"
    if t == "GL":
        return "GLONASS"
    if t == "GA":
        return "Galileo"
    if t in ("GB", "BD"):
        return "BeiDou"
    if t == "GN":
        try:
            p = int(prn)
        except (TypeError, ValueError):
            return "未知"
        if 65 <= p <= 96:
            return "GLONASS"
    return "未知"


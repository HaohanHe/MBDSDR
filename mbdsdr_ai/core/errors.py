# SPDX-License-Identifier: MIT
"""
MBDSDR Core - 统一错误类型
==========================

Headless-first 控制面对外暴露的全部异常。设计原则：

- :class:`SDRUserError` 是唯一基类，携带**用户友好**的 ``message``、
  **可操作**的 ``suggestion`` 和给开发者的 ``technical_detail``。
- CLI / MCP / Web / GUI 任何一层捕获到它，都能直接把 ``suggestion`` 展示给用户，
  而不需要再翻译。
- 子类对应一类明确的故障，每个都给出"下一步怎么做"。

红线：本模块纯标准库，绝不 import PySide6/Qt，也不 import 任何硬件驱动。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class SDRUserError(Exception):
    """所有可预期、可向用户解释的错误的基类。

    Attributes
    ----------
    message :
        一句话、面向用户的错误描述（中文，口语化）。
    suggestion :
        可操作建议：告诉用户下一步具体怎么做。
    technical_detail :
        给开发者/日志看的技术细节（原始异常、堆栈摘要、驱动名等）。
    """

    #: 机器可读错误码，子类覆盖。MCP/CLI 用它做结构化返回。
    code: str = "sdr_error"

    def __init__(
        self,
        message: str,
        suggestion: str = "",
        technical_detail: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.suggestion: str = suggestion or self._default_suggestion()
        self.technical_detail: Any = technical_detail

    # ------------------------------------------------------------------
    def _default_suggestion(self) -> str:
        return "请检查设备连接与参数后重试。"

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 MCP/JSON-RPC 友好的 dict（绝不抛异常）。"""
        return {
            "error": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "technical_detail": (
                str(self.technical_detail) if self.technical_detail is not None else None
            ),
        }

    def __str__(self) -> str:  # pragma: no cover - 直观表示
        base = self.message
        if self.suggestion:
            base += f"（建议：{self.suggestion}）"
        return base


# ======================================================================
# 子类：每一类故障一个明确错误码 + 可操作建议
# ======================================================================
class DriverNotFoundError(SDRUserError):
    """找不到 SDR 驱动（pyrtlsdr / SoapySDR 等未安装）。"""

    code = "driver_not_found"

    def _default_suggestion(self) -> str:
        return (
            "请安装对应驱动：RTL-SDR 运行 `pip install pyrtlsdr`；"
            "通用 SDR 运行 `pip install SoapySDR`；或用 --debug-source 以调试信号源模式运行。"
        )


class DeviceNotFoundError(SDRUserError):
    """枚举列表里找不到指定设备，或设备已拔出。"""

    code = "device_not_found"

    def _default_suggestion(self) -> str:
        return (
            "请先运行 list_devices() 查看当前可见设备，确认序列号/索引；"
            "若设备刚拔出，重新 list_devices() 刷新列表。"
        )


class DeviceBusyError(SDRUserError):
    """设备已被其它进程占用。"""

    code = "device_busy"

    def _default_suggestion(self) -> str:
        return (
            "请关闭正在使用该设备的其它程序（如 GQRX/SDR#/另一个 mbdsdr 实例），"
            "或拔插一次 USB 后重试。"
        )


class USBDisconnectedError(SDRUserError):
    """读样本过程中设备被拔出 / USB 断连。"""

    code = "usb_disconnected"

    def _default_suggestion(self) -> str:
        return "请重新插好 USB 天线棒，然后重新 list_devices() 并 connect()。"


class NoAudioOutputError(SDRUserError):
    """没有可用的音频输出设备（sounddevice 不可用或无输出声道）。"""

    code = "no_audio_output"

    def _default_suggestion(self) -> str:
        return (
            "请确认系统已连接扬声器/耳机，并 `pip install sounddevice`；"
            "无头服务器上可跳过 --start-audio，仅录制或远程查看频谱。"
        )


class SampleRateNotSupportedError(SDRUserError):
    """设备不支持请求的采样率。"""

    code = "sample_rate_not_supported"

    def _default_suggestion(self) -> str:
        return "请改用设备枚举时给出的 sample_rates 列表中的值（如 RTL-SDR 常用 2.4 MSps）。"


class FrequencyOutOfRangeError(SDRUserError):
    """请求频率超出设备可调范围。"""

    code = "frequency_out_of_range"

    def _default_suggestion(self) -> str:
        return "请把频率调到设备覆盖范围内（一般 RTL-SDR 为 24 MHz–1.7 GHz）。"


class DecoderNotAvailableError(SDRUserError):
    """请求的解码器未注册或缺少依赖。"""

    code = "decoder_not_available"

    def _default_suggestion(self) -> str:
        return "可用解码器见 list_decoders()；如需新解码器请先注册或安装其依赖。"


__all__ = [
    "SDRUserError",
    "DriverNotFoundError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "USBDisconnectedError",
    "NoAudioOutputError",
    "SampleRateNotSupportedError",
    "FrequencyOutOfRangeError",
    "DecoderNotAvailableError",
]

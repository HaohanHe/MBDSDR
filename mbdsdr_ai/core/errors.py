# SPDX-License-Identifier: MIT
"""
MBDSDR Core - 统一错误类型（再导出）
====================================

错误树的**唯一定义**在 :mod:`mbdsdr_ai.error_handler`：那里同时维护两套
输出契约——``to_dict()`` 给 MCP / JSON-RPC / CLI（机器可读错误码 +
technical_detail），``to_ui()`` 给桌面端弹窗 / 状态条（title + 文案，
不暴露内部细节）。

本模块只保留历史导入路径：``from mbdsdr_ai.core.errors import DeviceBusyError``
与 ``from mbdsdr_ai.error_handler import DeviceBusyError`` 返回**同一个类对象**
（``isinstance`` 跨两条路径都成立），不复制类定义、不做 ``sys.modules`` 别名。
这样做是因为两棵旧树的构造签名第 2/3 个参数顺序相反
（``(message, technical_detail, suggestion)`` vs
``(message, suggestion, technical_detail)``），机械 alias 会把建议和技术细节
静默对调；签名统一后只留一份定义。

红线不变：``mbdsdr_ai.error_handler`` 与本模块都是纯标准库，绝不 import
PySide6/Qt，也不 import 任何硬件驱动。
"""

from __future__ import annotations

from ..error_handler import (  # noqa: F401 - 再导出（同一类对象）
    SDRUserError,
    DriverNotFoundError,
    DeviceNotFoundError,
    DeviceBusyError,
    OwnerRegistryUnavailableError,
    USBDisconnectedError,
    NoAudioOutputError,
    SampleRateNotSupportedError,
    FrequencyOutOfRangeError,
    InvalidArgumentError,
    ExportPathOutOfRootError,
    DecoderNotAvailableError,
    DecoderNotStreamableError,
)

__all__ = [
    "SDRUserError",
    "DriverNotFoundError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "OwnerRegistryUnavailableError",
    "USBDisconnectedError",
    "NoAudioOutputError",
    "SampleRateNotSupportedError",
    "FrequencyOutOfRangeError",
    "InvalidArgumentError",
    "ExportPathOutOfRootError",
    "DecoderNotAvailableError",
    "DecoderNotStreamableError",
]

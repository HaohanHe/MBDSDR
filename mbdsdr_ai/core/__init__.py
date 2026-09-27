"""
MBDSDR Core —— Headless-first 纯 Python 控制面
=================================================

本包是所有前端（Qt GUI / CLI / Web / MCP）共享的唯一业务逻辑入口。
它**绝不**依赖 PySide6/Qt：

>>> import sys
>>> from mbdsdr_ai.core import SDRController
>>> [m for m in sys.modules if m.startswith(("PySide", "PyQt"))]
[]

快速开始::

    from mbdsdr_ai.core import SDRController, SDRUserError

    ctrl = SDRController()
    print(ctrl.list_devices())          # 无硬件 -> []（外加 debug 项）
    ctrl.connect("debug")               # 显式调试信号源
    ctrl.set_frequency(98_500_000)
    ctrl.set_demod("WFM")
    spec = ctrl.read_spectrum(1024)     # np.ndarray 或 None
    ctrl.shutdown()
"""

from .errors import (
    SDRUserError,
    DriverNotFoundError,
    DeviceNotFoundError,
    DeviceBusyError,
    USBDisconnectedError,
    NoAudioOutputError,
    SampleRateNotSupportedError,
    FrequencyOutOfRangeError,
    DecoderNotAvailableError,
)
from .controller import (
    SDRController,
    AudioDeviceInfo,
    VfoInfo,
    SatInfo,
    PassInfo,
    SystemStatus,
    VALID_MODES,
    SUPPORTED_DECODERS,
)

__version__ = "0.1.0"

__all__ = [
    # errors
    "SDRUserError",
    "DriverNotFoundError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "USBDisconnectedError",
    "NoAudioOutputError",
    "SampleRateNotSupportedError",
    "FrequencyOutOfRangeError",
    "DecoderNotAvailableError",
    # controller + types
    "SDRController",
    "AudioDeviceInfo",
    "VfoInfo",
    "SatInfo",
    "PassInfo",
    "SystemStatus",
    "VALID_MODES",
    "SUPPORTED_DECODERS",
]

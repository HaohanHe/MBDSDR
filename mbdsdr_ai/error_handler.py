"""
MBDSDR AI - 统一用户友好错误处理
==================================

SDR++ / GQRX 把底层异常（``LIBUSB_ERROR_BUSY``、``Device or resource busy``、
``No module named 'rtlsdr'``）直接冒到 UI，用户看不懂也不知道下一步做什么。
本模块在**不改动任何既有共享文件**的前提下，提供一层统一的错误翻译：

- :class:`SDRUserError`：所有"用户可操作错误"的基类，同时携带
  ``message``（用户友好一句话）、``technical_detail``（技术细节，给日志/开发者）、
  ``suggestion``（明确告诉用户下一步怎么修）。
- 7 类常见真机错误（缺 DLL / 无设备 / 设备被占用 / USB 断开 / 无音频输出 /
  采样率不支持 / 频率超范围）。
- :func:`format_error`：把任意异常翻译成 UI 可直接显示的
  ``{title, message, suggestion}``。
- :func:`classify_connect_failure`：把 ``connect()`` 失败时的原始异常 /
  ``status.error`` 字符串翻译成具体的 SDRUserError。

设计红线：
- 绝不吞错后只说"出错了"——每个错误都必须给**可操作建议**；
- 技术细节与用户文案分离：日志里看 technical_detail，弹窗里看 message+suggestion；
- 本模块纯 Python、无硬件依赖，全部可确定性单测。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

__all__ = [
    "SDRUserError",
    "DriverNotFoundError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "USBDisconnectedError",
    "NoAudioOutputError",
    "SampleRateNotSupportedError",
    "FrequencyOutOfRangeError",
    "format_error",
    "classify_connect_failure",
]


class SDRUserError(Exception):
    """用户可操作的错误基类。

    Parameters
    ----------
    message : str
        一句话用户友好描述（弹窗标题正文级别）。
    technical_detail : str
        技术细节（原始异常、错误码等），给日志/开发者看。
    suggestion : str
        明确的下一步操作建议。
    """

    category: str = "generic"
    title: str = "SDR 错误"

    def __init__(
        self,
        message: str,
        technical_detail: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.technical_detail = str(technical_detail)
        self.suggestion = str(suggestion)

    # ------------------------------------------------------------------
    def to_ui(self) -> Dict[str, str]:
        """格式化为 UI 可直接渲染的 dict。"""
        return {
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
        }

    def __str__(self) -> str:  # pragma: no cover - 调试辅助
        base = f"[{self.category}] {self.message}"
        if self.suggestion:
            base += f" -> {self.suggestion}"
        return base


# ----------------------------------------------------------------------
# 具体错误类
# ----------------------------------------------------------------------
class DriverNotFoundError(SDRUserError):
    """找不到 rtlsdr.dll / pyrtlsdr /  librtlsdr 驱动。"""

    category = "driver"
    title = "找不到 SDR 驱动"

    def __init__(
        self,
        message: str = "找不到 rtlsdr.dll（SDR 驱动未就绪）",
        technical_detail: str = "",
        suggestion: str = (
            "请安装 SDR# (https://airspy.com/download/) 或手动指定 DLL 路径；"
            "Linux/macOS 请用包管理器安装 librtlsdr0（如 apt install librtlsdr0）"
        ),
    ) -> None:
        super().__init__(message, technical_detail, suggestion)


class DeviceNotFoundError(SDRUserError):
    """枚举不到任何 SDR 设备。"""

    category = "no_device"
    title = "未检测到 SDR 设备"

    def __init__(
        self,
        message: str = "未检测到 SDR 设备，请检查 USB 连接和驱动",
        technical_detail: str = "",
        suggestion: str = (
            "请检查 USB 连接，确认驱动已安装（Windows 用 Zadig 把 RTL2832 绑定为 "
            "WinUSB），然后点击刷新设备列表"
        ),
    ) -> None:
        super().__init__(message, technical_detail, suggestion)


class DeviceBusyError(SDRUserError):
    """设备被其他程序占用。"""

    category = "busy"
    title = "设备被占用"

    def __init__(
        self,
        message: str = "SDR 设备被其他程序占用，无法打开",
        technical_detail: str = "",
        suggestion: str = (
            "请关闭其他使用该设备的程序（SDR# / GQRX / SDRangel / Virtual Radar "
            "Server 等）后重试"
        ),
    ) -> None:
        super().__init__(message, technical_detail, suggestion)


class USBDisconnectedError(SDRUserError):
    """运行中 USB 设备被拔出 / 连接断开。"""

    category = "usb_disconnected"
    title = "USB 设备已断开"

    def __init__(
        self,
        message: str = "USB 设备已断开，信号中断",
        technical_detail: str = "",
        suggestion: str = (
            "请重新插入设备，系统将自动重连并恢复之前的频率 / 模式 / 增益设置"
        ),
    ) -> None:
        super().__init__(message, technical_detail, suggestion)


class NoAudioOutputError(SDRUserError):
    """没有可用的音频输出设备。"""

    category = "no_audio"
    title = "没有音频输出设备"

    def __init__(
        self,
        message: str = "未找到音频输出设备，无法播放解调音频",
        technical_detail: str = "",
        suggestion: str = (
            "请检查声卡连接（扬声器 / 耳机是否插好），或在设置中选择输出设备"
        ),
    ) -> None:
        super().__init__(message, technical_detail, suggestion)


class SampleRateNotSupportedError(SDRUserError):
    """请求的采样率设备不支持。"""

    category = "bad_samplerate"
    title = "采样率不受支持"

    def __init__(
        self,
        message: str = "请求的采样率不被设备支持",
        technical_detail: str = "",
        suggestion: str = "",
        supported_rates: Optional[Sequence[float]] = None,
    ) -> None:
        if not suggestion:
            if supported_rates:
                pretty = ", ".join(f"{int(round(r))} Hz" for r in supported_rates)
                suggestion = f"该设备支持的采样率：{pretty}"
            else:
                suggestion = "请改用设备默认采样率（通常 2.048 MHz）"
        super().__init__(message, technical_detail, suggestion)
        self.supported_rates = list(supported_rates or [])


class FrequencyOutOfRangeError(SDRUserError):
    """频率超出设备可调范围。"""

    category = "bad_frequency"
    title = "频率超出范围"

    def __init__(
        self,
        message: str = "请求的频率超出设备可调范围",
        technical_detail: str = "",
        suggestion: str = "",
        min_freq: Optional[float] = None,
        max_freq: Optional[float] = None,
    ) -> None:
        if not suggestion and min_freq is not None and max_freq is not None:
            suggestion = (
                f"该设备支持 {min_freq / 1e6:.3f} - {max_freq / 1e6:.3f} MHz"
            )
        super().__init__(message, technical_detail, suggestion)
        self.min_freq = min_freq
        self.max_freq = max_freq


# ----------------------------------------------------------------------
# 格式化与分类
# ----------------------------------------------------------------------
def format_error(error: Any) -> Dict[str, str]:
    """把任意异常格式化为 UI 可直接显示的 ``{title, message, suggestion}``。

    - :class:`SDRUserError` 直接输出其文案；
    - 其它异常包装成一个兜底错误，suggestion 提示"查看日志 / 重试"。
    """
    if isinstance(error, SDRUserError):
        return error.to_ui()
    # 非预期异常：技术细节带进 message 的副标题，给一个保守建议
    name = type(error).__name__
    detail = str(error)
    return {
        "title": "未知错误",
        "message": f"{name}: {detail}" if detail else name,
        "suggestion": (
            "请重试一次；若反复出现，请把日志提交给开发者（见"
            " technical_detail）"
        ),
    }


#: connect() 失败时用于判类的关键字表（小写匹配）
_BUSY_KEYWORDS = (
    "busy",
    "could not open rtlsdr device",
    "device or resource busy",
    "permission",
    "access denied",
    "errno 16",
    "errno -1",
    "libusb_error_busy",
    "usb error -5",
)
_NO_DEVICE_KEYWORDS = (
    "no device",
    "not found",
    "no rtlsdr",
    "cannot open",
    "disconnected",
    "libusb_error_no_device",
    "usb error -3",
    "errno 19",
)
_DRIVER_KEYWORDS = (
    "no module named",
    "importerror",
    "rtlsdr.dll",
    "could not find module",
    "libusb-1.0.dll",
    "dll load failed",
    "undefined symbol",
)


def classify_connect_failure(
    exc: Optional[BaseException] = None,
    status_error: str = "",
) -> SDRUserError:
    """把 ``connect()`` 失败时的原始异常 / ``status.error`` 翻译成具体错误。

    Parameters
    ----------
    exc : Exception, optional
        connect 抛的原始异常。
    status_error : str
        ``backend.status.error`` 里的描述（RTLSDRBackend.connect 失败时会写入）。

    Returns
    -------
    SDRUserError
        具体子类实例（DriverNotFound / DeviceBusy / USBDisconnected / 兜底）。
    """
    text = " ".join(
        [
            str(exc) if exc is not None else "",
            type(exc).__name__ if exc is not None else "",
            str(status_error or ""),
        ]
    ).lower()
    tech = f"{type(exc).__name__ if exc is not None else ''}: {exc}" if exc else str(status_error)

    # 顺序敏感：先判驱动（ImportError），再判占用，最后判设备不在
    if isinstance(exc, ImportError) or any(k in text for k in _DRIVER_KEYWORDS):
        return DriverNotFoundError(technical_detail=tech)
    if any(k in text for k in _BUSY_KEYWORDS):
        return DeviceBusyError(technical_detail=tech)
    if any(k in text for k in _NO_DEVICE_KEYWORDS):
        return USBDisconnectedError(technical_detail=tech)
    # 兜底：未知连接错误
    return SDRUserError(
        message="SDR 设备连接失败",
        technical_detail=tech,
        suggestion="请重新插拔 USB 后重试；若仍失败，查看日志中的技术细节",
    )

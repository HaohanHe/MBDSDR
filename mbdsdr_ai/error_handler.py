# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 统一用户友好错误处理
==================================

SDR++ / GQRX 把底层异常（``LIBUSB_ERROR_BUSY``、``Device or resource busy``、
``No module named 'rtlsdr'``）直接冒到 UI，用户看不懂也不知道下一步做什么。
本模块是**全仓唯一错误树的定义处**（``core.errors`` 只做同一对象的再导出），
提供一层统一的错误翻译：

- :class:`SDRUserError`：所有"用户可操作错误"的基类，同时携带
  ``message``（用户友好一句话）、``suggestion``（明确告诉用户下一步怎么修）、
  ``technical_detail``（技术细节，给日志/开发者），并维护两套输出契约：
  ``to_dict()`` 给 MCP/JSON-RPC/CLI，``to_ui()`` 给桌面弹窗/状态条。
- 13 个具体错误类（缺驱动 / 无设备 / 设备被占用 / 占用登记不可读 / USB 断开 /
  无音频输出 / 采样率不支持 / 频率超范围 / 非法参数 / 导出路径越界 /
  解码器不可用 / 解码器无流式入口）。
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

from typing import Any, Dict, Optional, Sequence

from .error_catalog import spec, suggestion, user_message

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
    "format_error",
    "classify_connect_failure",
    "probe_occupant",
]


class SDRUserError(Exception):
    """用户可操作的错误基类——**全仓唯一一棵**错误树。

    历史上有两棵树：``mbdsdr_ai.error_handler``（桌面/启动链）与
    ``mbdsdr_ai.core.errors``（MCP/CLI 链），构造签名第 2/3 个参数顺序相反
    （``(message, technical_detail, suggestion)`` vs
    ``(message, suggestion, technical_detail)``），且各自 ``isinstance`` 判自己
    那棵——``format_error(core 的 DeviceBusyError)`` 会落进 ``unexpected_error``
    兜底。现在只在**这里**定义一次，``core.errors`` 只做同一对象的再导出，
    全树签名统一为 ``(message, suggestion, technical_detail)``。

    两套输出契约各管一个界面，字段不互相污染：

    - :meth:`to_dict` → ``{error, message, suggestion, technical_detail}``，
      给 **MCP / JSON-RPC / CLI**：错误码做机器分支，``technical_detail``
      （任意对象，序列化时转字符串）给日志；
    - :meth:`to_ui` → ``{title, message, suggestion}``，给**桌面端弹窗/状态条**，
      不暴露内部技术细节。

    Parameters
    ----------
    message : str
        一句话用户友好描述。
    suggestion : str
        明确的下一步操作建议；留空时用子类的 ``_default_suggestion()``。
    technical_detail : Any
        技术细节（原始异常、错误码等），给日志/开发者看。
    """

    code: str = "sdr_error"
    category: str = "generic"
    title: str = "SDR 错误"

    def __init__(
        self,
        message: str = "",
        suggestion: str = "",
        technical_detail: Any = None,
    ) -> None:
        message = str(message)
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion or self._default_suggestion()
        self.technical_detail = technical_detail

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

    def to_ui(self) -> Dict[str, str]:
        """格式化为桌面 UI 可直接渲染的 dict。"""
        return {
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
        }

    def __str__(self) -> str:
        base = self.message
        if self.suggestion:
            base += f"（建议：{self.suggestion}）"
        return base


# ----------------------------------------------------------------------
# 具体错误类
# ----------------------------------------------------------------------
class DriverNotFoundError(SDRUserError):
    """找不到 rtlsdr.dll / pyrtlsdr / librtlsdr 驱动。"""

    code = "driver_not_found"
    category = "driver"
    title = "找不到 SDR 驱动"
    default_message = "找不到 rtlsdr.dll（SDR 驱动未就绪）"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None) -> None:
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        return (
            "请安装对应驱动：本机 RTL-SDR USB 默认走 SoapySDR"
            "（pip install SoapySDR + SoapyRTLSDR）；Windows 也可用 SDR# "
            "(https://airspy.com/download/) 安装 WinUSB 驱动；"
            "Linux/macOS 用包管理器安装 librtlsdr0（如 apt install librtlsdr0）；"
            "或用 --debug-source 以调试信号源模式运行。"
        )


class DeviceNotFoundError(SDRUserError):
    """枚举列表里找不到指定设备，或设备已拔出。"""

    code = "device_not_found"
    category = "no_device"
    title = "未检测到 SDR 设备"
    default_message = "未检测到 SDR 设备，请检查 USB 连接和驱动"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None) -> None:
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        return (
            "请检查 USB 连接，确认驱动已安装（Windows 用 Zadig 把 RTL2832 "
            "绑定为 WinUSB）；然后刷新设备列表（list_devices()），确认序列号/索引"
        )


class DeviceBusyError(SDRUserError):
    """设备已被其它进程占用。

    **只用于「登记里有真实持有者」的拒绝**：能指认是谁、pid 多少。
    登记读不出来（没有可指认的持有者）是
    :class:`OwnerRegistryUnavailableError`，两者的下一步动作完全不同。
    """

    code = "device_busy"
    category = "busy"
    title = "设备被占用"
    default_message = "SDR 设备被其他程序占用，无法打开"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None) -> None:
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        return (
            "请先关闭正在使用该设备的程序，或在那个程序里释放该设备后重试"
            "（如桌面端、GQRX、SDR#、另一个 mbdsdr 实例）；"
            "也可以换用其它可用设备。"
        )


class OwnerRegistryUnavailableError(SDRUserError):
    """设备占用登记不可读，无法判断设备是否被占用。

    与 :class:`DeviceBusyError` **不是同一件事**：这里没有可指认的持有者，
    说的是「读不出来」。补救动作是修/移除登记文件，不是去关别的软件——
    所以单独一个错误码，别让上层按 busy 去引导用户。
    """

    code = "owner_registry_unavailable"
    category = "busy_registry"
    title = "设备占用登记不可读"

    def _default_suggestion(self) -> str:
        return (
            "请检查运行日志里的设备占用登记路径：文件损坏就修复或移除它"
            "（下次启动会重建），或到桌面端「设备」面板点「查看诊断」按提示处理。"
        )


class USBDisconnectedError(SDRUserError):
    """读样本过程中设备被拔出 / USB 断连。"""

    code = "usb_disconnected"
    category = "usb_disconnected"
    title = "USB 设备已断开"
    default_message = "USB 设备已断开，信号中断"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None) -> None:
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        return (
            "请重新插入设备，系统将自动重连并恢复之前的频率 / 模式 / 增益设置"
        )


class NoAudioOutputError(SDRUserError):
    """没有可用的音频输出设备（sounddevice 不可用或无输出声道）。"""

    code = "no_audio_output"
    category = "no_audio"
    title = "没有音频输出设备"
    default_message = "未找到音频输出设备，无法播放解调音频"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None) -> None:
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        return (
            "请检查声卡连接（扬声器 / 耳机是否插好），或在设置中选择输出设备；"
            "无头服务器可跳过 --start-audio，仅录制或远程查看频谱"
        )


class SampleRateNotSupportedError(SDRUserError):
    """请求的采样率设备不支持。"""

    code = "sample_rate_not_supported"
    category = "bad_samplerate"
    title = "采样率不受支持"
    default_message = "请求的采样率不被设备支持"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None,
                 supported_rates: Optional[Sequence[float]] = None) -> None:
        # 先落 supported_rates 再 super()：_default_suggestion() 在基类构造里调用。
        self.supported_rates = list(supported_rates or [])
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        if self.supported_rates:
            pretty = ", ".join(f"{int(round(r))} Hz" for r in self.supported_rates)
            return f"该设备支持的采样率：{pretty}"
        # 项目默认采样率是唯一真值源（``sdr_status.DEFAULT_SAMPLE_RATE_HZ``）。
        from .sdr_status import (DEFAULT_SAMPLE_RATE_HZ,
                                 format_sample_rate)
        return ("请改用设备默认采样率"
                f"（通常 {format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)}）")


class FrequencyOutOfRangeError(SDRUserError):
    """请求频率超出设备可调范围。"""

    code = "frequency_out_of_range"
    category = "bad_frequency"
    title = "频率超出范围"
    default_message = "请求的频率超出设备可调范围"

    def __init__(self, message: str = "", suggestion: str = "",
                 technical_detail: Any = None,
                 min_freq: Optional[float] = None,
                 max_freq: Optional[float] = None) -> None:
        self.min_freq = min_freq
        self.max_freq = max_freq
        super().__init__(message or self.default_message, suggestion,
                         technical_detail)

    def _default_suggestion(self) -> str:
        if self.min_freq is not None and self.max_freq is not None:
            return f"该设备支持 {self.min_freq / 1e6:.3f} - {self.max_freq / 1e6:.3f} MHz"
        return "请把频率调到设备覆盖范围内（一般 RTL-SDR 为 24 MHz–1.7 GHz）。"


class InvalidArgumentError(SDRUserError):
    """入参不合法（非有限数值、越界等）——用户可修正的参数错误。

    与内部故障区分开：这类错误应报 ``invalid_argument``，让调用方知道
    改参数就行，而不是让人以为系统崩了。
    """

    code = "invalid_argument"
    category = "invalid_argument"
    title = "参数不合法"


class ExportPathOutOfRootError(SDRUserError):
    """导出路径越出允许根（路径遍历防护）。"""

    code = "export_path_out_of_root"
    category = "export_path"
    title = "导出路径越界"


class DecoderNotAvailableError(SDRUserError):
    """请求的解码器未注册或缺少依赖。"""

    code = "decoder_not_available"
    category = "decoder"
    title = "解码器不可用"

    def _default_suggestion(self) -> str:
        return "可用解码器见 list_decoders()；如需新解码器请先注册或安装其依赖。"


class DecoderNotStreamableError(DecoderNotAvailableError):
    """解码器有文件级实现，但没有流式 IQ 入口。

    与 :class:`DecoderNotAvailableError` 分开：这是「我们没做流式入口」，
    但**有替代做法**（``--decode-file``）；``not_implemented`` 是能力根本
    未接线，两者对用户是两句话，不许合并。
    """

    code = "decoder_not_streamable"
    title = "解码器无流式入口"

    def _default_suggestion(self) -> str:
        return (
            "该解码器仅支持文件级入口，请用 --decode-file <文件> --mode <类型>；"
            "流式解码仅支持 adsb/rds。"
        )


# ----------------------------------------------------------------------
# 格式化与分类
# ----------------------------------------------------------------------
def format_error(error: Any) -> Dict[str, str]:
    """把任意异常格式化为 UI 可直接显示的 ``{title, message, suggestion}``。

    - :class:`SDRUserError`（含 ``mbdsdr_ai.core.errors`` 再导出的同一棵树）
      直接输出其文案；
    - 其它异常包装成一个兜底错误，suggestion 提示"查看日志 / 重试"。
    """
    if isinstance(error, SDRUserError):
        return error.to_ui()
    # 非预期异常：必带原始异常类型与最小上下文，不用空话兜底。
    return {
        "title": spec("unexpected_error").meaning,
        "message": user_message(
            "unexpected_error", exc_type=type(error).__name__,
            detail=str(error) or "（异常无文本描述）"),
        "suggestion": suggestion("unexpected_error"),
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
    # librtlsdr 的 "usb_open error -3" 是 libusb LIBUSB_ERROR_ACCESS：
    # Windows 上通常表示设备已被其它进程独占；Linux 上也可能是 udev 权限。
    "usb_open error -3",
    "usb error -3",
    "unable to open device",
    "rtlsdr_open failed",
    "open device failed",
)
_NO_DEVICE_KEYWORDS = (
    "no device",
    "not found",
    "no rtlsdr",
    "cannot open",
    "disconnected",
    "libusb_error_no_device",
    "errno 19",
)

#: 常见会独占 SDR 的进程名（小写匹配，仅用于"尽力而为"的占用者提示）
_KNOWN_SDR_PROCESSES = (
    "sdrsharp", "sdr#", "sdr++.exe", "sdrpp", "gqrx", "sdrangel",
    "cubicsdr", "rtl_tcp", "rtl_test", "rtl_sdr", "rtl_433",
    "dump1090", "gpredicty", "gpredict", "virtualradarserver",
)


def probe_occupant(processes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """尽力而为地探测"谁占用了 SDR 设备"。

    Parameters
    ----------
    processes : Sequence[str], optional
        调用方采集到的可见进程名列表。**本函数不主动扫描系统进程**：
        Windows 的 WinUSB/libusb 不暴露"设备句柄持有者"，主动扫描既慢又不可靠，
        这里如实返回"探测不到"，而不是编造占用者。

    Returns
    -------
    dict
        ``{"detected": bool, "processes": [...], "reason": str}``。
        ``detected=False`` 时 reason 明确说明是"未发现"还是"无法探测"。
    """
    if processes is None:
        return {
            "detected": False,
            "processes": [],
            "reason": (
                "无法探测：RTL-SDR 的 WinUSB/libusb 驱动不暴露设备句柄持有者；"
                "请手动关闭 SDR# / GQRX / SDR++ / SDRangel 等独占程序后重试"
            ),
        }
    matched = sorted({
        str(p) for p in processes
        if str(p) and any(k in str(p).lower() for k in _KNOWN_SDR_PROCESSES)
    })
    if matched:
        return {
            "detected": True,
            "processes": matched,
            "reason": "在可见进程列表中发现可能占用设备的 SDR 程序",
        }
    return {
        "detected": False,
        "processes": [],
        "reason": "可见进程列表中未发现常见 SDR 程序；占用者可能是驱动/系统服务或探测被权限限制",
    }


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

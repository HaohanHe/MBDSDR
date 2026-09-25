"""
SDR# (SDRSharp, airspy) UI/接收机架构移植（纯 numpy，无 C#/.NET 运行时）
======================================================================
本模块把 SDR# (repos/sdrsharp, Build 1632) 里经过十几年实践验证的
三件事原样翻译成 Python(numpy)：

  1. 插件接口契约（IPlugin / IFrontendController / ISharpControl）
     —— 来源: SDRSharp.Common/SDRSharp.Common/ISharpPlugin.cs:5-20
              SDRSharp.Radio/SDRSharp.Radio/IFrontendController.cs:3-8
              SDRSharp.Common/SDRSharp.Common/ISharpControl.cs:10-111
  2. 设备枚举表（Source 下拉框）
     —— 来源: SDRSharp/SDRSharp/MainForm.cs:3258-3283
  3. 频谱 FFT 窗函数系数 + 功率谱/字节缩放
     —— 来源: SDRSharp.Radio/SDRSharp.Radio/FilterBuilder.cs:9-79 (MakeWindow)
              SDRSharp.Radio/SDRSharp.Radio/Fourier.cs:44-69 (SpectrumPower/ScaleFFT)

链路契约（SDR# 的插件模型）：
    前端(IFrontendController.Open/Close) → 复IQ流 → VFO → FFT窗 → FFT →
    功率谱(dB) → 字节缩放 → 频谱/waterfall 面板；
    ISharpPlugin 只拿一个 ISharpControl 句柄，通过属性(CenterFrequency/
    FilterBandwidth/SquelchThreshold…)读写全局状态，不直接碰硬件。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  1. 设备枚举表 —— 移植自 MainForm.cs:3258-3283
# ═══════════════════════════════════════════════════════════════════════
#: MainForm.cs:3258-3272 LoadSource(name, controller, access) 的内置前端表。
#: access 字段：INT_MAX(2147483647)=随 exe 静态链接；10=可选 ExtIO/DLL；0=Plugins.xml 第三方。
#: 末尾两条 (3282-3283) 是内置的 "IQ File (*.wav)" 与 "IQ from Sound Card"。
SDRSHARP_DEVICE_TABLE: List[Dict] = [
    {"name": "AIRSPY",                 "fqdn": "SDRSharp.FrontEnds.Airspy.AirspyIO",        "access": 2147483647, "builtin": True},   # MainForm.cs:3258
    {"name": "AIRSPY HF+",             "fqdn": "SDRSharp.FrontEnds.AirspyHF.AirspyHFIO",   "access": 2147483647, "builtin": True},   # MainForm.cs:3259
    {"name": "Spy Server",             "fqdn": "SDRSharp.FrontEnds.SpyServer.SpyServerIO", "access": 2147483647, "builtin": True},   # MainForm.cs:3260
    {"name": "UHD / USRP",             "fqdn": "SDRSharp.USRP.UsrpIO,SDRSharp.USRP",       "access": 10,         "builtin": True},   # MainForm.cs:3261
    {"name": "HackRF",                 "fqdn": "SDRSharp.HackRF.HackRFIO,SDRSharp.HackRF", "access": 10,         "builtin": True},   # MainForm.cs:3262
    {"name": "RTL-SDR (R820T)",        "fqdn": "SDRSharp.R820T.RtlSdrIO,SDRSharp.R820T",   "access": 10,         "builtin": True},   # MainForm.cs:3263
    {"name": "RTL-SDR (USB)",          "fqdn": "SDRSharp.RTLSDR.RtlSdrIO,SDRSharp.RTLSDR", "access": 10,         "builtin": True},   # MainForm.cs:3264
    {"name": "RTL-SDR (TCP)",          "fqdn": "SDRSharp.RTLTCP.RtlTcpIO,SDRSharp.RTLTCP", "access": 10,         "builtin": True},   # MainForm.cs:3265
    {"name": "FUNcube Dongle Pro",     "fqdn": "SDRSharp.FUNcube.FunCubeIO,SDRSharp.FUNcube", "access": 10,      "builtin": True},   # MainForm.cs:3266
    {"name": "FUNcube Dongle Pro+",    "fqdn": "SDRSharp.FUNcubeProPlus.FunCubeProPlusIO", "access": 10,        "builtin": True},   # MainForm.cs:3267
    {"name": "SoftRock (Si570)",       "fqdn": "SDRSharp.SoftRock.SoftRockIO,SDRSharp.SoftRock", "access": 10,  "builtin": True},   # MainForm.cs:3268
    {"name": "RFSPACE SDR-IQ (USB)",   "fqdn": "SDRSharp.SDRIQ.SdrIqIO,SDRSharp.SDRIQ",    "access": 10,         "builtin": True},   # MainForm.cs:3269
    {"name": "RFSPACE Networked Radios", "fqdn": "SDRSharp.SDRIP.SdrIpIO,SDRSharp.SDRIP", "access": 10,          "builtin": True},   # MainForm.cs:3270
    {"name": "AFEDRI Networked Radios", "fqdn": "SDRSharp.AfedriSDRNet.AfedriSdrNetIO",  "access": 10,          "builtin": True},   # MainForm.cs:3271
    {"name": "File Player",            "fqdn": "SDRSharp.WAVPlayer.WAVFileIO,SDRSharp.WAVPlayer", "access": 10, "builtin": True},   # MainForm.cs:3272
    {"name": "IQ File (*.wav)",        "fqdn": "(internal wave file source)",              "access": 0,          "builtin": True},   # MainForm.cs:3282
    {"name": "IQ from Sound Card",     "fqdn": "(internal soundcard source)",             "access": 0,          "builtin": True},   # MainForm.cs:3283
]

#: 默认 FFT 窗下标。来源: MainForm.cs:3237
#:   fftWindowComboBox.SelectedIndex = Utils.GetIntSetting("fftWindowType", 3)
#: combo 下标与 WindowType 枚举差 1（combo 第 0 项是 "None"）。
#: 默认 index=3 → WindowType.BlackmanHarris4（见 WindowType.cs:3-12）。
SDRSHARP_DEFAULT_FFT_WINDOW_INDEX = 3


# ═══════════════════════════════════════════════════════════════════════
#  2. FFT 窗函数 —— 逐系数移植自 FilterBuilder.MakeWindow
# ═══════════════════════════════════════════════════════════════════════
#: 枚举对齐 WindowType.cs:3-12（None=0, Hamming=1, ...）。
SDRSHARP_WINDOW_TYPES = (
    "none", "hamming", "blackman", "blackmanharris4",
    "blackmanharris7", "hannpoisson", "youssef",
)


def make_sdrsharp_window(window: str, length: int) -> np.ndarray:
    """逐系数移植 FilterBuilder.MakeWindow (FilterBuilder.cs:9-79)。

    注意 C# 里 length-- 后 for(i=0;i<=length;i++)，即区间 [0, length-1] 共 length 点，
    分母用 (length-1)。这里 n = arange(length)，分母 (length-1)，完全对齐。
    """
    length = int(length)
    if length < 2:
        return np.ones(length, dtype=np.float32)
    wtype = (window or "blackmanharris4").lower()
    n = np.arange(length, dtype=np.float64)
    L = float(length - 1)
    a = 2.0 * np.pi * n / L
    win = np.ones(length, dtype=np.float64)

    if wtype in ("none", "rectangular"):
        pass
    elif wtype == "hamming":
        # FilterBuilder.cs:20-24  a0=0.54, a1=0.46
        win = 0.54 - 0.46 * np.cos(a)
    elif wtype == "blackman":
        # FilterBuilder.cs:29-33  a0=0.42,a1=0.5,a2=0.08
        win = 0.42 - 0.50 * np.cos(a) + 0.08 * np.cos(2.0 * a)
    elif wtype == "blackmanharris4":
        # FilterBuilder.cs:38-42
        win = (0.35875 - 0.48829 * np.cos(a)
               + 0.14128 * np.cos(2.0 * a) - 0.01168 * np.cos(3.0 * a))
    elif wtype == "blackmanharris7":
        # FilterBuilder.cs:47-54  7-term Blackman-Harris (a0..a6)
        win = (0.2710514 - 0.433297932 * np.cos(a)
               + 0.218123 * np.cos(2.0 * a) - 0.06592545 * np.cos(3.0 * a)
               + 0.0108117424 * np.cos(4.0 * a) - 0.000776584842 * np.cos(5.0 * a)
               + 1.38872174e-05 * np.cos(6.0 * a))
    elif wtype == "hannpoisson":
        # FilterBuilder.cs:59-61  alpha=0.005, 中心对称
        v = n - L / 2.0
        win = 0.5 * (1.0 + np.cos(2.0 * np.pi * v / L)) * np.exp(-2.0 * 0.005 * np.abs(v) / L)
    elif wtype == "youssef":
        # FilterBuilder.cs:66-73  BH4 外形 * Hann-Poisson 指数衰减
        v = n - L / 2.0
        win = (0.35875 - 0.48829 * np.cos(a)
               + 0.14128 * np.cos(2.0 * a) - 0.01168 * np.cos(3.0 * a))
        win *= np.exp(-2.0 * 0.005 * np.abs(v) / L)
    else:
        raise ValueError(f"未知 SDR# 窗: {window}（可选 {SDRSHARP_WINDOW_TYPES}）")
    return win.astype(np.float32)


# ═══════════════════════════════════════════════════════════════════════
#  3. 功率谱 / 字节缩放 —— 移植自 Fourier.cs:44-69
# ═══════════════════════════════════════════════════════════════════════
def sdrsharp_spectrum_power(iq: np.ndarray, window: str = "blackmanharris4",
                            offset_db: float = 0.0) -> np.ndarray:
    """SDR# 频谱功率谱（dB）。

    步骤对齐 Fourier.cs:169 ApplyFFTWindow → Fourier.cs:178 ForwardTransform →
    Fourier.cs:44 SpectrumPower:
        power[i] = 10*log10(1e-60 + |X[i]|^2) + offset
    """
    iq = np.asarray(iq, dtype=np.complex128)
    w = make_sdrsharp_window(window, len(iq))
    windowed = iq * w
    X = np.fft.fft(windowed)
    # Fourier.cs:196-206 rearrange: 把 [-fs/2,0) 与 [0,fs/2) 交换 → 0频在中间
    X = np.fft.fftshift(X)
    power = 10.0 * np.log10(1e-60 + (X.real ** 2 + X.imag ** 2)) + offset_db
    return power.astype(np.float32)


def sdrsharp_scale_fft(power_db: np.ndarray, min_power: float,
                       max_power: float) -> np.ndarray:
    """把 dB 功率谱线性映射到 0..255 字节。移植 Fourier.cs:53-69 ScaleFFT。

        scale = 255/(maxPower-minPower)
        out[i] = (clamp(power[i],min,max) - min) * scale
    """
    power_db = np.asarray(power_db, dtype=np.float64)
    scale = 255.0 / (max_power - min_power)
    clamped = np.clip(power_db, min_power, max_power)
    return ((clamped - min_power) * scale).astype(np.uint8)


# ═══════════════════════════════════════════════════════════════════════
#  4. 插件接口契约（Python 镜像）—— ISharpPlugin / IFrontendController
# ═══════════════════════════════════════════════════════════════════════
class SdrSharpFrontendController:
    """IFrontendController 的 Python 镜像。

    来源: IFrontendController.cs:3-8  只有 Open()/Close() 两个方法。
    真实硬件未连接时 Open() 置 connected=False 并返回"未连接"。
    """
    def __init__(self, name: str, fqdn: str):
        self.name = name
        self.fqdn = fqdn
        self.connected = False

    def open(self) -> Dict:
        # 无真实硬件 → 模拟。来源 MainForm.cs:3305 LoadExtension 反射创建后 Open()
        self.connected = False
        return {"device": self.name, "connected": False,
                "status": "未连接（无真实硬件，模拟）", "fqdn": self.fqdn}

    def close(self) -> Dict:
        self.connected = False
        return {"device": self.name, "connected": False, "status": "closed"}


class SdrSharpControl:
    """ISharpControl 的只读/可写属性镜像（ISharpControl.cs:10-111）。

    插件通过持有的 ISharpControl 句柄读写这些全局状态，而不直接碰硬件。
    """
    def __init__(self):
        self.center_frequency = 100_000_000   # ISharpControl.cs:30 CenterFrequency
        self.frequency = 100_000_000           # ISharpControl.cs:72 Frequency
        self.audio_gain = 0                    # ISharpControl.cs:24 AudioGain
        self.filter_bandwidth = 3_000          # ISharpControl.cs:54 FilterBandwidth
        self.filter_order = 500                # ISharpControl.cs:60 FilterOrder (=DefaultFilterOrder)
        self.squelch_enabled = False           # ISharpControl.cs:102
        self.squelch_threshold = -100         # ISharpControl.cs:108
        self.fm_stereo = False                 # ISharpControl.cs:66
        self.cw_shift = 0                      # ISharpControl.cs:36


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_sdrsharp_tools(registry) -> None:
    """把 SDR# 设备枚举 / FFT窗函数 / 插件契约 注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _list_devices(args):
        """返回 SDR# Source 下拉框的完整设备枚举表。"""
        try:
            data = {
                "devices": SDRSHARP_DEVICE_TABLE,
                "count": len(SDRSHARP_DEVICE_TABLE),
                "default_window_index": SDRSHARP_DEFAULT_FFT_WINDOW_INDEX,
                "source": "SDRSharp MainForm.cs:3258-3283",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"设备枚举失败: {e}")

    def _make_window(args):
        """按 SDR# FilterBuilder.MakeWindow 生成窗系数并校验能量和。"""
        wtype = str(args.get("window", "blackmanharris4"))
        length = int(args.get("length", 1024))
        try:
            win = make_sdrsharp_window(wtype, length)
            data = {
                "window": wtype, "length": length,
                "sum": float(np.sum(win)),
                "mean": float(np.mean(win)),
                "edge_left": float(win[0]), "edge_right": float(win[-1]),
                "peak": float(np.max(win)),
                "source": "SDRSharp FilterBuilder.cs:9-79",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"窗函数生成失败: {e}")

    def _spectrum_fft(args):
        """对一段复 IQ 跑 SDR# 风格 FFT（窗+fftshift+dB功率谱+字节缩放）。"""
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表")
        try:
            arr = np.array(iq, dtype=complex)
            win = str(args.get("window", "blackmanharris4"))
            min_p = float(args.get("min_power", -120.0))
            max_p = float(args.get("max_power", -20.0))
            power = sdrsharp_spectrum_power(arr, window=win)
            byte_spec = sdrsharp_scale_fft(power, min_p, max_p)
            data = {
                "bins": int(len(power)),
                "power_db_min": float(np.min(power)),
                "power_db_max": float(np.max(power)),
                "byte_min": int(np.min(byte_spec)),
                "byte_max": int(np.max(byte_spec)),
                "window": win,
                "source": "SDRSharp Fourier.cs:44-69,169-206",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"FFT 失败: {e}")

    def _plugin_contract(args):
        """镜像 SDR# ISharpPlugin/IFrontendController 接口契约并实例化一个前端。"""
        name = str(args.get("device", "RTL-SDR (USB)"))
        try:
            entry = next((d for d in SDRSHARP_DEVICE_TABLE if d["name"] == name), None)
            if entry is None:
                return ToolResult(False, f"未知设备 {name}")
            ctrl = SdrSharpFrontendController(entry["name"], entry["fqdn"])
            opened = ctrl.open()
            sh = SdrSharpControl()
            data = {
                "plugin_interface": {
                    "ISharpPlugin": ["Gui", "DisplayName", "Initialize(ISharpControl)", "Close()"],
                    "IFrontendController": ["Open()", "Close()"],
                },
                "source": "SDRSharp ISharpPlugin.cs:5-20, IFrontendController.cs:3-8",
                "frontend_open": opened,
                "control_snapshot": {
                    "center_frequency": sh.center_frequency,
                    "filter_bandwidth": sh.filter_bandwidth,
                    "squelch_threshold": sh.squelch_threshold,
                },
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"插件契约失败: {e}")

    registry.register(
        name="sdrsharp_list_devices",
        description=("SDR# Source 下拉框设备枚举表：移植 MainForm.cs:3258-3283 的 "
                     "LoadSource 序列（AIRSPY/AIRSPY HF+/Spy Server/UHD/HackRF/"
                     "RTL-SDR R820T/USB/TCP/FUNcube/SoftRock/SDR-IQ/File Player/"
                     "IQ File/IQ SoundCard），含 access 权限位与 fqdn。"),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_list_devices,
        category="sdr_ui",
    )

    registry.register(
        name="sdrsharp_make_window",
        description=("SDR# FFT 窗函数（移植 FilterBuilder.MakeWindow）：支持 "
                     "none/hamming/blackman/blackmanharris4/blackmanharris7/"
                     "hannpoisson/youssef，返回窗系数和/均值/边缘值用于校验。"),
        parameters={
            "type": "object",
            "properties": {
                "window": {"type": "string", "enum": list(SDRSHARP_WINDOW_TYPES),
                           "default": "blackmanharris4"},
                "length": {"type": "integer", "default": 1024},
            },
            "required": [],
        },
        handler=_make_window,
        category="sdr_dsp",
    )

    registry.register(
        name="sdrsharp_spectrum_fft",
        description=("SDR# 频谱流水线：窗函数→FFT→fftshift→10log10(1e-60+|X|^2)→"
                     "[min,max] 钳位映射到 0..255 字节。移植 Fourier.cs:44-69/169-206。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"}},
                "window": {"type": "string", "default": "blackmanharris4"},
                "min_power": {"type": "number", "default": -120.0},
                "max_power": {"type": "number", "default": -20.0},
            },
            "required": ["iq"],
        },
        handler=_spectrum_fft,
        category="sdr_dsp",
    )

    registry.register(
        name="sdrsharp_plugin_contract",
        description=("SDR# 插件接口契约：实例化 IFrontendController(Open/Close) 与 "
                     "ISharpControl 句柄（CenterFrequency/FilterBandwidth/Squelch 等属性），"
                     "无硬件时返回'未连接'。"),
        parameters={
            "type": "object",
            "properties": {
                "device": {"type": "string", "default": "RTL-SDR (USB)"},
            },
            "required": [],
        },
        handler=_plugin_contract,
        category="sdr_ui",
    )


if __name__ == "__main__":
    # 自测：窗函数能量和应合理（Hamming 和 ≈ 0.54*N）
    for w in SDRSHARP_WINDOW_TYPES:
        win = make_sdrsharp_window(w, 1024)
        print(f"{w:18s} sum={np.sum(win):10.2f} mean={np.mean(win):.4f} "
              f"edge=({win[0]:.4f},{win[-1]:.4f})")
    print("设备数:", len(SDRSHARP_DEVICE_TABLE))

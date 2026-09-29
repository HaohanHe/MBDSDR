# SPDX-License-Identifier: MIT
"""
MBDSDR - 统一 SDR 设备源抽象（独立实现）
==========================================

本模块提供一个与设备无关的 SDR 接收源接口，把形如
``"rtl=0"`` / ``"hackrf=..."`` / ``"bladerf=..."`` / ``"uhd,type=b200"`` /
``"soapy=0,driver=..."`` 的设备描述串路由到对应后端（RTL-SDR、HackRF、
bladeRF、USRP/UHD、SoapySDR 等），并在没有真实硬件 / 后端库时给出可在
UI 层使用的静态标称参数范围。

设计要点（均依据公开器件手册与通用 SDR 软件接口思想独立实现）：
  * 数值范围 / 增益分级 / 默认采样率等，均为各器件数据手册与公开驱动
    （librtlsdr、libhackrf、libbladerf、UHD、SoapySDR）中的事实性标称值；
  * 设备描述串解析为通用 ``key=value`` 空格/逗号分组语法；
  * 无后端库 / 无设备时绝不伪造 IQ 样本，``read_samples()`` 显式抛错。

gr-osmosdr、SoapySDR 等项目仅作为本接口思想的技术参考与致谢，本模块
不包含其源代码。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# 数值范围表示
# ─────────────────────────────────────────────────────────────────────────
class GainRange:
    """一段连续的、可带量化步长的取值范围 ``[start, stop]``。

    Parameters
    ----------
    start, stop:
        区间端点；``stop`` 缺省时退化为单点范围。
    step:
        量化步长；0 表示连续取值。
    """

    def __init__(self, start: float, stop: Optional[float] = None, step: float = 0.0):
        if stop is None:
            stop = start
        if stop < start:
            raise ValueError(
                f"invalid range: stop ({stop:g}) must be >= start ({start:g})")
        self.start = float(start)
        self.stop = float(stop)
        self.step = float(step)

    def is_discrete(self) -> bool:
        return self.start == self.stop

    def clip(self, value: float, clip_step: bool = False) -> float:
        """把 value 约束到本区间内；可选按 step 就近量化。"""
        if value < self.start:
            return self.start
        if value > self.stop:
            return self.stop
        if not clip_step or self.step == 0:
            return value
        # 半值向远离零的方向取整，避免 banker's rounding 的歧义。
        import math
        q = math.floor((value - self.start) / self.step + 0.5)
        return q * self.step + self.start

    def contains(self, value: float) -> bool:
        return self.start <= value <= self.stop

    def to_pp_string(self) -> str:
        s = f"({self.start:g}"
        if self.start != self.stop:
            s += f", {self.stop:g}"
        if self.step != 0:
            s += f", {self.step:g}"
        return s + ")"

    def values(self) -> List[float]:
        out: List[float] = []
        if self.start == self.stop:
            out.append(self.start)
        elif self.step == 0:
            out += [self.start, self.stop]
        else:
            n = int(round((self.stop - self.start) / self.step))
            out.extend(self.start + i * self.step for i in range(n + 1))
        return out

    def __repr__(self) -> str:
        return f"GainRange({self.start:g},{self.stop:g},{self.step:g})"


class MetaRange:
    """由若干 ``GainRange`` 组成的（可能不连续）范围集合。"""

    def __init__(self, ranges: Optional[List[GainRange]] = None):
        self.ranges: List[GainRange] = list(ranges or [])

    def push_back(self, r: GainRange) -> None:
        self.ranges.append(r)

    def __add__(self, r: GainRange) -> "MetaRange":
        self.ranges.append(r)
        return self

    def empty(self) -> bool:
        return not self.ranges

    def start(self) -> float:
        return min((r.start for r in self.ranges), default=0.0)

    def stop(self) -> float:
        return max((r.stop for r in self.ranges), default=0.0)

    def clip(self, value: float, clip_step: bool = False) -> float:
        """把 value 吸附到集合内最近的合法点（区间间隙取最近端点）。"""
        if not self.ranges:
            return value
        last_stop = self.ranges[0].stop
        for r in self.ranges:
            if value < r.start:
                return r.start if abs(value - r.start) < abs(value - last_stop) else last_stop
            if value <= r.stop:
                if not clip_step or r.step == 0:
                    return value
                import math
                q = math.floor((value - r.start) / r.step + 0.5)
                return q * r.step + r.start
            last_stop = r.stop
        return last_stop

    def contains(self, value: float) -> bool:
        return any(r.contains(value) for r in self.ranges)

    def to_pp_string(self) -> str:
        return "\n".join(r.to_pp_string() for r in self.ranges)

    def __repr__(self) -> str:
        return f"MetaRange({self.ranges!r})"


# ─────────────────────────────────────────────────────────────────────────
# 设备描述串解析（通用 key=value 分组语法）
# ─────────────────────────────────────────────────────────────────────────
#: 已知的后端驱动标识（按常用顺序列出，供路由与 UI 展示）。
BUILTIN_SOURCE_TYPES: Tuple[str, ...] = (
    "file", "fcd", "rtl", "rtl_tcp", "uhd", "miri", "sdrplay",
    "hackrf", "bladerf", "rfspace", "airspy", "airspyhf", "soapy",
    "redpitaya", "freesrp", "xtrx",
)

#: RFSpace 类设备的别名。
RFSPACE_ALIASES = ("sdr-iq", "sdr-ip", "netsdr", "cloudiq", "cloudsdr")

#: 驱动 key -> 别名集合（命中任一即归到该驱动）。
_DRIVER_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "fcd": ("fcd",),
    "file": ("file",),
    "rtl": ("rtl",),
    "rtl_tcp": ("rtl_tcp",),
    "uhd": ("uhd",),
    "miri": ("miri",),
    "sdrplay": ("sdrplay",),
    "hackrf": ("hackrf",),
    "bladerf": ("bladerf",),
    "rfspace": RFSPACE_ALIASES + ("rfspace",),
    "airspy": ("airspy",),
    "airspyhf": ("airspyhf",),
    "soapy": ("soapy",),
    "redpitaya": ("redpitaya",),
    "freesrp": ("freesrp",),
    "xtrx": ("xtrx",),
}


def _split_escaped(s: str, separator: str) -> List[str]:
    """按 separator 切分字符串，支持反斜杠转义与单引号包裹段。"""
    out: List[str] = []
    buf: List[str] = []
    in_quote = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            buf.append(s[i + 1])
            i += 2
            continue
        if c == "'":
            in_quote = not in_quote
            i += 1
            continue
        if c == separator and not in_quote:
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return out


def params_to_dict(params: str) -> Dict[str, str]:
    """把 ``"k1=v1,k2='v 2'"`` 形式的单组参数解析成 dict。"""
    result: Dict[str, str] = {}
    if not params:
        return result
    for token in _split_escaped(params, separator=","):
        if not token:
            continue
        if "=" in token:
            k, v = token.split("=", 1)
        else:
            k, v = token, ""
        v = v.strip()
        if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
            v = v[1:-1]
        result[k.strip()] = v
    return result


def args_to_vector(args: str) -> List[str]:
    """把整个设备串按空白切成多个参数组。"""
    return [t for t in _split_escaped(args, separator=" ") if t != ""]


def _driver_of(d: Dict[str, str]) -> Optional[str]:
    """根据参数 dict 里出现的 key 判定所属后端。"""
    for driver, keys in _DRIVER_KEYWORDS.items():
        if any(k in d for k in keys):
            return driver
    return None


def parse_device_string(device_string: str) -> Dict[str, Dict[str, str]]:
    """解析设备描述串，返回 ``{后端driver: {key: value}}``。

    例: ``"rtl=0,label='My Stick'"`` -> ``{"rtl": {"rtl":"0","label":"My Stick"}}``
        多设备: ``"rtl=0 hackrf=aa11bb22"`` -> ``{"rtl":{...}, "hackrf":{...}}``
    没有显式驱动 key 的组归入 ``"_anon"``。
    """
    out: Dict[str, Dict[str, str]] = {}
    for group in args_to_vector(device_string or ""):
        d = params_to_dict(group)
        driver = _driver_of(d)
        if driver is not None:
            out[driver] = d
        else:
            out.setdefault("_anon", {}).update(d)
    return out


# ─────────────────────────────────────────────────────────────────────────
# 各后端静态标称参数（数据手册 / 公开驱动中的事实性数值）
# ─────────────────────────────────────────────────────────────────────────
# --- RTL-SDR (RTL2832U + 常见调谐器) -----------------------------------
RTL_SAMPLE_RATES_HZ: Tuple[float, ...] = (
    250_000.0, 1_000_000.0, 1_024_000.0, 1_800_000.0, 1_920_000.0,
    2_000_000.0, 2_048_000.0, 2_400_000.0, 2_560_000.0,
)
RTL_DEFAULT_SAMPLE_RATE_HZ = 1_024_000.0
RTL_DEFAULT_IF_GAIN_DB = 24.0

#: 各调谐器的标称调谐频率范围。
RTL_TUNER_FREQ_RANGES: Dict[str, List[GainRange]] = {
    "E4000": [GainRange(52e6, 2.2e9)],
    "FC0012": [GainRange(22e6, 948e6)],
    "FC0013": [GainRange(22e6, 1.1e9)],
    "FC2580": [GainRange(146e6, 308e6), GainRange(438e6, 924e6)],
    "R820T": [GainRange(24e6, 1766e6)],
    "R828D": [GainRange(24e6, 1766e6)],
}
#: 未知调谐器时按最常见的 R820T/R828D 给范围。
RTL_DEFAULT_FREQ_RANGES = RTL_TUNER_FREQ_RANGES["R820T"]
RTL_GAIN_NAMES = ("LNA",)
RTL_GAIN_NAMES_E4000 = ("LNA", "IF")
RTL_E4000_IF_GAIN_RANGE = GainRange(3, 56, 1)


# --- HackRF One ----------------------------------------------------------
HACKRF_GAIN_NAMES = ("RF", "IF", "BB")
HACKRF_RF_GAIN_RANGE = GainRange(0, 14, 14)   # 前置放大器：关/开
HACKRF_IF_GAIN_RANGE = GainRange(0, 40, 8)
HACKRF_BB_GAIN_RANGE = GainRange(0, 62, 2)
HACKRF_DEFAULT_RF_GAIN = 0.0
HACKRF_DEFAULT_IF_GAIN = 16.0
HACKRF_DEFAULT_BB_GAIN = 20.0
HACKRF_SAMPLE_RATES_HZ: Tuple[float, ...] = (
    8e6, 10e6, 12.5e6, 16e6, 20e6,
)
HACKRF_DEFAULT_SAMPLE_RATE_HZ = 8e6
HACKRF_FREQ_RANGE = GainRange(4e6, 7_246e6)


# --- bladeRF -------------------------------------------------------------
BLADERF_GAIN_NAMES = ("LNA", "VGA1", "VGA2")
BLADERF_LNA_GAIN_RANGE = GainRange(0, 6, 3)
BLADERF_VGA1_GAIN_RANGE = GainRange(5, 30, 1)
BLADERF_VGA2_GAIN_RANGE = GainRange(0, 30, 3)
BLADERF_SAMPLE_RATE_RANGES = (
    GainRange(160e3, 200e3, 40e3),
    GainRange(300e3, 900e3, 100e3),
    GainRange(1e6, 40e6, 1e6),
)
BLADERF_FREQ_RANGE = GainRange(280e6, 3.8e9)


# --- UHD/USRP 与 SoapySDR：范围由运行时探测 ------------------------------
UHD_NOTE = "UHD/USRP 范围由 UHD runtime 按主板型号给出，需运行时查询"
SOAPY_NOTE = "SoapySDR 范围由 driver 运行时给出，需运行时查询"


#: 后端静态描述表：driver -> 无设备时可展示的标称参数。
BACKEND_STATIC: Dict[str, Dict[str, Any]] = {
    "rtl": {
        "label": "RTL-SDR (RTL2832U)",
        "device_string_prefix": "rtl=",
        "gain_names": list(RTL_GAIN_NAMES),
        "gain_ranges": {
            "LNA": GainRange(0.0, 49.6, 0.1),
            "IF": RTL_E4000_IF_GAIN_RANGE,
        },
        "default_gains": {"LNA": 0.0, "IF": RTL_DEFAULT_IF_GAIN_DB},
        "sample_rates_hz": list(RTL_SAMPLE_RATES_HZ),
        "default_sample_rate_hz": RTL_DEFAULT_SAMPLE_RATE_HZ,
        "freq_ranges": [r.to_pp_string() for r in RTL_DEFAULT_FREQ_RANGES],
        "tuner_freq_ranges_hz": {
            k: [r.to_pp_string() for r in v] for k, v in RTL_TUNER_FREQ_RANGES.items()
        },
    },
    "hackrf": {
        "label": "HackRF One",
        "device_string_prefix": "hackrf=",
        "gain_names": list(HACKRF_GAIN_NAMES),
        "gain_ranges": {
            "RF": HACKRF_RF_GAIN_RANGE,
            "IF": HACKRF_IF_GAIN_RANGE,
            "BB": HACKRF_BB_GAIN_RANGE,
        },
        "default_gains": {"RF": HACKRF_DEFAULT_RF_GAIN,
                          "IF": HACKRF_DEFAULT_IF_GAIN,
                          "BB": HACKRF_DEFAULT_BB_GAIN},
        "sample_rates_hz": list(HACKRF_SAMPLE_RATES_HZ),
        "default_sample_rate_hz": HACKRF_DEFAULT_SAMPLE_RATE_HZ,
        "freq_ranges": [HACKRF_FREQ_RANGE.to_pp_string()],
    },
    "bladerf": {
        "label": "Nuand bladeRF",
        "device_string_prefix": "bladerf=",
        "gain_names": list(BLADERF_GAIN_NAMES),
        "gain_ranges": {
            "LNA": BLADERF_LNA_GAIN_RANGE,
            "VGA1": BLADERF_VGA1_GAIN_RANGE,
            "VGA2": BLADERF_VGA2_GAIN_RANGE,
        },
        "default_gains": {},
        "sample_rates_hz": [],
        "sample_rate_ranges": [r.to_pp_string() for r in BLADERF_SAMPLE_RATE_RANGES],
        "default_sample_rate_hz": None,
        "freq_ranges": [BLADERF_FREQ_RANGE.to_pp_string()],
    },
    "uhd": {
        "label": "Ettus USRP (UHD)",
        "device_string_prefix": "uhd,",
        "gain_names": [],
        "gain_ranges": {},
        "default_gains": {},
        "sample_rates_hz": [],
        "default_sample_rate_hz": None,
        "freq_ranges": [],
        "note": UHD_NOTE,
    },
    "soapy": {
        "label": "SoapySDR (generic)",
        "device_string_prefix": "soapy=",
        "gain_names": [],
        "gain_ranges": {},
        "default_gains": {},
        "sample_rates_hz": [],
        "default_sample_rate_hz": None,
        "freq_ranges": [],
        "note": SOAPY_NOTE,
    },
}


# ─────────────────────────────────────────────────────────────────────────
# 设备枚举
# ─────────────────────────────────────────────────────────────────────────
class DeviceEnumerator:
    """枚举本机可用的 SDR 设备。

    真实枚举依赖各后端 Python 库（pyrtlsdr / SoapySDR / libhackrf 绑定）；
    库未安装或无设备时返回空列表，绝不伪造设备条目。
    """

    @staticmethod
    def enumerate() -> List[Dict[str, Any]]:
        devices: List[Dict[str, Any]] = []

        # 1) RTL-SDR（pyrtlsdr）
        try:
            import rtlsdr  # type: ignore
            n = getattr(rtlsdr.librtlsdr, "rtlsdr_get_device_count", lambda: 0)()
            for i in range(int(n)):
                try:
                    name = rtlsdr.librtlsdr.rtlsdr_get_device_name(i)
                    if isinstance(name, bytes):
                        name = name.decode("utf-8", "replace")
                except Exception:
                    name = "RTL-SDR"
                try:
                    serial = rtlsdr.librtlsdr.rtlsdr_get_device_serial(i)
                    if isinstance(serial, bytes):
                        serial = serial.decode("utf-8", "replace")
                except Exception:
                    serial = ""
                devices.append({
                    "driver": "rtl",
                    "index": i,
                    "label": f"RTL-SDR #{i} {name}".strip(),
                    "serial": serial,
                    "device_string": f"rtl={i}",
                })
        except Exception as e:  # 无 pyrtlsdr / 无设备
            logger.debug("RTL-SDR 枚举跳过: %s", e)

        # 2) SoapySDR 总线（覆盖 hackrf/bladerf/airspy/uhd/limesdr...）
        try:
            import SoapySDR  # type: ignore
            results = SoapySDR.Device.enumerate()
            for i, kw in enumerate(results):
                drv = kw.get("driver", "unknown")
                serial = kw.get("serial", "")
                devices.append({
                    "driver": "soapy",
                    "subdriver": drv,
                    "index": i,
                    "label": f"SoapySDR {drv} {serial}".strip(),
                    "serial": serial,
                    "device_string": f"soapy={i},driver={drv}",
                })
        except Exception as e:
            logger.debug("SoapySDR 枚举跳过: %s", e)

        # 3) HackRF（libhackrf python 绑定）
        try:
            import hackrf  # type: ignore
            hl = hackrf.HackRF()
            count = getattr(hl, "_device_count", 0)
            for i in range(int(count or 0)):
                devices.append({
                    "driver": "hackrf",
                    "index": i,
                    "label": f"HackRF #{i}",
                    "serial": "",
                    "device_string": f"hackrf={i}",
                })
        except Exception as e:
            logger.debug("HackRF 枚举跳过: %s", e)

        return devices


# ─────────────────────────────────────────────────────────────────────────
# 统一设备源接口
# ─────────────────────────────────────────────────────────────────────────
class OsmoSDRSource:
    """与设备无关的 SDR 接收源：设备串路由 + 参数校验/范围吸附 + 静态表。

    真正的硬件收发由 ``connect()`` 选中的后端库完成；无后端库时
    ``read_samples()`` 抛错，绝不返回假 IQ。
    """

    def __init__(self, device_string: str = ""):
        self.device_string = device_string
        self.driver: Optional[str] = None
        self.device_index: Any = None
        self.extra: Dict[str, str] = {}
        self._connected = False
        self._backend_handle: Any = None
        self._freq_hz = 0.0
        self._sample_rate_hz = 0.0
        self._gains: Dict[str, float] = {}

        if device_string:
            self.connect(device_string)

    # ---- 设备路由 ---------------------------------------------------
    def connect(self, device_string: str) -> str:
        """解析设备串并路由到对应后端，返回 driver 名。"""
        self.device_string = device_string
        parsed = parse_device_string(device_string)
        for driver, d in parsed.items():
            if driver == "_anon":
                continue
            self.driver = driver
            self.extra = d
            self.device_index = d.get(driver, "")
            break

        if self.driver is None:
            found = DeviceEnumerator.enumerate()
            if found:
                self.driver = found[0]["driver"]
                self.device_string = found[0].get("device_string", "")
                self.device_index = found[0].get("index", 0)
            else:
                raise RuntimeError(
                    "No supported devices found (check the connection and/or udev rules).")

        stat = BACKEND_STATIC.get(self.driver, {})
        self._gains = dict(stat.get("default_gains", {}))
        if stat.get("default_sample_rate_hz"):
            self._sample_rate_hz = float(stat["default_sample_rate_hz"])

        self._connected = True
        logger.info("OsmoSDRSource routed to driver=%s device=%r",
                    self.driver, self.device_index)
        return self.driver

    # ---- 参数查询（静态表，无需硬件） -------------------------------
    def get_gain_names(self) -> List[str]:
        return list(BACKEND_STATIC.get(self.driver or "", {}).get("gain_names", []))

    def get_gain_ranges(self) -> Dict[str, GainRange]:
        return dict(BACKEND_STATIC.get(self.driver or "", {}).get("gain_ranges", {}))

    def get_gain_range(self, name: Optional[str] = None) -> Optional[GainRange]:
        if name is None:
            names = self.get_gain_names()
            name = names[0] if names else None
        if name is None:
            return None
        return self.get_gain_ranges().get(name)

    # ---- 参数设置（带范围吸附） ------------------------------------
    def set_freq(self, freq_hz: float, chan: int = 0) -> float:
        self._freq_hz = float(freq_hz)
        return self._freq_hz

    def set_sample_rate(self, rate_hz: float) -> float:
        rate = float(rate_hz)
        stat = BACKEND_STATIC.get(self.driver or "", {})
        known = stat.get("sample_rates_hz") or []
        if known:
            rate = min(known, key=lambda r: abs(r - rate))
        self._sample_rate_hz = rate
        return rate

    def get_sample_rate(self) -> float:
        return self._sample_rate_hz

    def set_gain(self, gain_db: float, name: Optional[str] = None) -> float:
        if name is None:
            names = self.get_gain_names()
            name = names[0] if names else None
        rng = self.get_gain_range(name)
        g = float(gain_db)
        if rng is not None:
            g = rng.clip(g, clip_step=True)
        self._gains[name or ""] = g
        return g

    def get_gain(self, name: Optional[str] = None) -> float:
        if name is None:
            names = self.get_gain_names()
            name = names[0] if names else None
        return self._gains.get(name or "", 0.0)

    # ---- 样本读取（无真实后端时不造假） -----------------------------
    def read_samples(self, n: int) -> Any:
        """读取 n 个复样本。未绑定真实硬件后端时绝不返回假 IQ。"""
        if self._backend_handle is not None:
            raise NotImplementedError(
                "Real backend streaming not wired for driver=%s" % self.driver)
        raise RuntimeError(
            f"No real SDR backend connected for driver={self.driver!r}; "
            "refusing to fabricate IQ samples.")

    def summary(self) -> Dict[str, Any]:
        stat = BACKEND_STATIC.get(self.driver or "", {})
        return {
            "device_string": self.device_string,
            "driver": self.driver,
            "device_index": self.device_index,
            "connected": self._connected,
            "gain_names": self.get_gain_names(),
            "gain_ranges": {k: v.to_pp_string() for k, v in self.get_gain_ranges().items()},
            "current_gains": dict(self._gains),
            "sample_rate_hz": self._sample_rate_hz,
            "freq_hz": self._freq_hz,
            "static_note": stat.get("note", ""),
        }


def osmosdr_list_backends() -> List[Dict[str, Any]]:
    """返回全部后端及其静态参数（供 UI 下拉）。"""
    return [
        {"driver": drv, **{k: v for k, v in meta.items() if k != "gain_ranges"}}
        for drv, meta in BACKEND_STATIC.items()
    ]


# ─────────────────────────────────────────────────────────────────────────
# SoapySDR 真实流（setupStream / activateStream / readStream）
# ─────────────────────────────────────────────────────────────────────────
class SoapySDRSource(OsmoSDRSource):
    """真实 SoapySDR 接收源：setupStream → activateStream → readStream。

    依据 SoapySDR 公开通用设备接口设计。模块未安装 / 无设备 / 未开流时，
    相关方法抛明确 RuntimeError，绝不返回合成 IQ。
    """

    def __init__(self, device_string: str = ""):
        super().__init__(device_string)
        self._soapy_dev: Any = None
        self._soapy_stream: Any = None
        self._stream_open = False

    # ---- 真实打开流 -------------------------------------------------
    def start_stream(self) -> None:
        """真正打开 SoapySDR 设备并建立接收流；失败抛 RuntimeError。"""
        try:
            import SoapySDR  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"SoapySDR Python 模块未安装，无法打开真实 SDR 流: {e}. "
                "（refusing to fabricate IQ samples）") from e

        args: Dict[str, str] = {}
        for _k, v in self.extra.items():
            args[str(_k)] = str(v)
        if self.driver and self.driver != "soapy":
            args.setdefault("driver", self.driver)

        try:
            self._soapy_dev = SoapySDR.Device(args)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"SoapySDR.Device({args}) 打开失败（无设备或驱动缺失）: {e}") from e

        rx_chan = 0
        if self._sample_rate_hz:
            self._soapy_dev.setSampleRate(SoapySDR.SOAPY_SDR_RX, rx_chan,
                                          self._sample_rate_hz)
        if self._freq_hz:
            self._soapy_dev.setFrequency(SoapySDR.SOAPY_SDR_RX, rx_chan,
                                         self._freq_hz)

        self._soapy_stream = self._soapy_dev.setupStream(
            SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32, [rx_chan])
        self._soapy_dev.activateStream(self._soapy_stream)
        self._stream_open = True
        logger.info("SoapySDR stream opened (sr=%g Hz, freq=%g Hz)",
                    self._sample_rate_hz, self._freq_hz)

    def stop_stream(self) -> None:
        """关闭并反激活接收流。"""
        if self._soapy_dev is None or self._soapy_stream is None:
            return
        try:
            import SoapySDR  # type: ignore
            self._soapy_dev.deactivateStream(self._soapy_stream)
            self._soapy_dev.closeStream(self._soapy_stream)
        except Exception as e:  # noqa: BLE001
            logger.warning("SoapySDR closeStream 异常: %s", e)
        finally:
            self._soapy_stream = None
            self._soapy_dev = None
            self._stream_open = False

    # ---- 真实读样本 -------------------------------------------------
    def read_samples(self, n: int) -> np.ndarray:
        """从硬件读 n 个复 IQ（CF32）。未开流时抛错，不返回假数据。"""
        if not self._stream_open or self._soapy_dev is None or self._soapy_stream is None:
            raise RuntimeError(
                "SoapySDR 流未开启；请先 start_stream()。"
                "（refusing to fabricate IQ samples）")
        import SoapySDR  # type: ignore
        buf = np.zeros(n, dtype=np.complex64)
        ret = self._soapy_dev.readStream(self._soapy_stream, [buf], n)
        if isinstance(ret, tuple):
            code, n_read = ret[0], ret[1]
        else:
            code, n_read = 0, int(ret)
        if code < 0:
            raise RuntimeError(f"SoapySDR readStream 错误码={code}")
        return buf[:n_read].copy()

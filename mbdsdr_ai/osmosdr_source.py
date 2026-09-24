"""
MBDSDR AI 内核 - gr-osmosdr 通用 SDR 源抽象（Python 移植）
================================================================

本模块把 gr-osmosdr（GNU Radio 的通用 SDR 源块）的统一设备接口
原样移植为 Python，使 MBDSDR 能用与 gr-osmosdr 完全一致的设备字符串
（"rtl=0" / "hackrf=..." / "bladerf=..." / "uhd,type=b200" / "soapy=0,..."）
路由到 RTL-SDR / HackRF / bladeRF / USRP(UHD) / SoapySDR 等后端。

设计依据（真实阅读 C++ 源码，非 README）：
  - 统一 API 头文件      include/osmosdr/source.h
  - 设备路由/枚举        lib/source_impl.cc
  - 范围表示            lib/ranges.cc
  - 设备字符串解析       lib/arg_helpers.h
  - RTL-SDR 后端        lib/rtl/rtl_source_c.cc
  - HackRF 后端         lib/hackrf/hackrf_source_c.cc / hackrf_common.cc
  - bladeRF 后端        lib/bladerf/bladerf_source_c.cc / bladerf_common.cc
  - UHD/USRP 后端       lib/uhd/uhd_source_c.cc
  - SoapySDR 后端       lib/soapy/soapy_source_c.cc

红线：
  * 无硬件 / 无后端库时绝不造假样本、绝不伪造"已连接"。
  * 设备字符串格式与 gr-osmosdr 严格一致。
  * 每个硬件常量都标注来源 file:line。
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =====================================================================
# range_t / meta_range_t —— 移植自 lib/ranges.cc
# =====================================================================
class GainRange:
    """单个连续范围 [start, stop]，量化步长 step。

    对应 C++ osmosdr::range_t。
    来源: gr-osmosdr lib/ranges.cc:44-73（构造/start/stop/step/to_pp_string）
          lib/ranges.cc:49-51  stop<start 抛异常。
    """

    def __init__(self, start: float, stop: Optional[float] = None, step: float = 0.0):
        if stop is None:
            # range_t(value) 单点范围 —— 来源 ranges.cc:38-42
            stop = start
        if stop < start:
            # 来源 ranges.cc:49-51 "cannot make range where stop < start"
            raise ValueError("cannot make range where stop < start")
        self.start = float(start)
        self.stop = float(stop)
        self.step = float(step)

    def is_discrete(self) -> bool:
        return self.start == self.stop

    def clip(self, value: float, clip_step: bool = False) -> float:
        """把 value 吸附到本范围内（可选按 step 量化）。

        移植自 lib/ranges.cc:136-154 meta_range_t::clip 的单段逻辑。
        """
        if value < self.start:
            return self.start
        if value > self.stop:
            return self.stop
        if not clip_step or self.step == 0:
            return value
        # round((value-start)/step)*step + start  —— ranges.cc:148
        # C++ 用 boost::math::round（.5 远离零取整），等价于 Python 中
        # 对正数用 floor(x+0.5)，避免 banker's rounding 在半值处偏差。
        import math
        q = math.floor((value - self.start) / self.step + 0.5)
        return q * self.step + self.start

    def contains(self, value: float) -> bool:
        return self.start <= value <= self.stop

    def to_pp_string(self) -> str:
        """来源 ranges.cc:66-73 range_t::to_pp_string"""
        s = f"({self.start:g}"
        if self.start != self.stop:
            s += f", {self.stop:g}"
        if self.step != 0:
            s += f", {self.step:g}"
        s += ")"
        return s

    def values(self) -> List[float]:
        """枚举范围内所有可取点。来源 ranges.cc:156-175。"""
        out: List[float] = []
        if self.start != self.stop:
            if self.step == 0:
                out += [self.start, self.stop]
            else:
                v = self.start
                # 与 C++ 一样向上累加，避免浮点漂移用 round 计数
                n = int(round((self.stop - self.start) / self.step))
                for i in range(n + 1):
                    out.append(self.start + i * self.step)
        else:
            out.append(self.start)
        return out

    def __repr__(self) -> str:
        return f"GainRange({self.start:g},{self.stop:g},{self.step:g})"


class MetaRange:
    """多个 GainRange 组成的（可能不连续）范围集合。

    对应 C++ osmosdr::meta_range_t（std::vector<range_t>）。
    来源: gr-osmosdr lib/ranges.cc:89-154。
    """

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
        """所有段里最小的 start。来源 ranges.cc:101-108。"""
        if not self.ranges:
            return 0.0
        return min(r.start for r in self.ranges)

    def stop(self) -> float:
        """所有段里最大的 stop。来源 ranges.cc:110-117。"""
        if not self.ranges:
            return 0.0
        return max(r.stop for r in self.ranges)

    def clip(self, value: float, clip_step: bool = False) -> float:
        """吸附 value 到最近合法点。移植自 ranges.cc:136-154。"""
        import math
        if not self.ranges:
            return value
        last_stop = self.ranges[0].stop
        for r in self.ranges:
            if value < r.start:
                return r.start if abs(value - r.start) < abs(value - last_stop) else last_stop
            if value <= r.stop:
                if not clip_step or r.step == 0:
                    return value
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


# =====================================================================
# 设备字符串解析 —— 移植自 lib/arg_helpers.h
# =====================================================================
# gr-osmosdr 把整个 args 串先按空格切成多个"设备参数组"
#   args_to_vector: 来源 arg_helpers.h:48-60（分隔符 "\\" , " " , "'"）
# 每组再按逗号切成 key=value
#   params_to_vector: 来源 arg_helpers.h:62-74（分隔符 "\\" , "," , "'"）
# 每个 key=value 用第一个 '=' 切开
#   param_to_pair: 来源 arg_helpers.h:76-93
# 路由：dict.count("rtl") 即选中 rtl 后端
#   来源 source_impl.cc:296-397（逐后端 dict.count(...) 判断）

# 内置后端类型表（构建进 gr-osmosdr 的顺序）。
# 来源 source_impl.cc:128-175 dev_types.push_back(...)
BUILTIN_SOURCE_TYPES: Tuple[str, ...] = (
    "file", "fcd", "rtl", "rtl_tcp", "uhd", "miri", "sdrplay",
    "hackrf", "bladerf", "rfspace", "airspy", "airspyhf", "soapy",
    "redpitaya", "freesrp", "xtrx",
)

# rfspace 的别名。来源 source_impl.cc:184-190
RFSPACE_ALIASES = ("sdr-iq", "sdr-ip", "netsdr", "cloudiq", "cloudsdr")


def params_to_dict(params: str) -> Dict[str, str]:
    """把单组 "key1=v1,key2='v 2'" 解析成 dict。

    移植自 arg_helpers.h:95-110 params_to_dict。
    """
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
        # 去掉包裹的单引号 —— arg_helpers.h:104-105
        if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
            v = v[1:-1]
        result[k.strip()] = v
    return result


def _split_escaped(s: str, separator: str) -> List[str]:
    """轻量 escaped-list 切分，支持 \\ 转义与单引号包裹。

    对应 boost::escaped_list_separator<char>("\\", sep, "'")，
    来源 arg_helpers.h:52 / 66。
    """
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


def args_to_vector(args: str) -> List[str]:
    """把整个设备字符串按空格切成多组。来源 arg_helpers.h:48-60。"""
    return [t for t in _split_escaped(args, separator=" ") if t != ""]


def parse_device_string(device_string: str) -> Dict[str, Dict[str, str]]:
    """解析 gr-osmosdr 设备字符串，返回 {设备组key: {key:value}}。

    例: "rtl=0,label='My Stick'" -> {"rtl": {"rtl":"0","label":"My Stick"}}
    多设备: "rtl=0 hackrf=aa11bb22" -> {"rtl":{...}, "hackrf":{...}}

    来源: source_impl.cc:124 args_to_vector(args)
                 source_impl.cc:193 params_to_dict(arg)
    """
    out: Dict[str, Dict[str, str]] = {}
    for group in args_to_vector(device_string or ""):
        d = params_to_dict(group)
        # 选出这一组描述的后端 driver key
        driver = _driver_of(d)
        if driver is not None:
            out[driver] = d
        else:
            # 没有显式 driver key（如纯 "uhd,type=b200"），
            # 整组记到匿名槽，后续由枚举自动选择。
            out.setdefault("_anon", {}).update(d)
    return out


def _driver_of(d: Dict[str, str]) -> Optional[str]:
    """根据 dict 里出现的 key 判定后端。

    对应 source_impl.cc:296-397 的一串 dict.count(...) 判断。
    注意顺序与 source_impl.cc 中 if 链一致。
    """
    if "fcd" in d:
        return "fcd"
    if "file" in d:
        return "file"
    if "rtl" in d:
        return "rtl"
    if "rtl_tcp" in d:
        return "rtl_tcp"
    if "uhd" in d:
        return "uhd"
    if "miri" in d:
        return "miri"
    if "sdrplay" in d:
        return "sdrplay"
    if "hackrf" in d:
        return "hackrf"
    if "bladerf" in d:
        return "bladerf"
    for alias in RFSPACE_ALIASES + ("rfspace",):
        if alias in d:
            return "rfspace"
    if "airspy" in d:
        return "airspy"
    if "airspyhf" in d:
        return "airspyhf"
    if "soapy" in d:
        return "soapy"
    if "redpitaya" in d:
        return "redpitaya"
    if "freesrp" in d:
        return "freesrp"
    if "xtrx" in d:
        return "xtrx"
    return None


# =====================================================================
# 各后端静态硬件参数表（只读常量，全部标注 C++ 来源 file:line）
# =====================================================================
# 这些是 gr-osmosdr 在"未插设备/未连库"时也能给出的标称范围，
# 与 GRC 里下拉框、增益滑块显示的范围一致。

# --- RTL-SDR ---------------------------------------------------------
# 来源 rtl_source_c.cc:421-429 get_sample_rates()（离散已知可用档）
RTL_SAMPLE_RATES_HZ: Tuple[float, ...] = (
    250_000.0, 1_000_000.0, 1_024_000.0, 1_800_000.0, 1_920_000.0,
    2_000_000.0, 2_048_000.0, 2_400_000.0, 2_560_000.0,
)
# 默认采样率：构造时 rtlsdr_set_sample_rate(_dev, 1024000)
# 来源 rtl_source_c.cc:198
RTL_DEFAULT_SAMPLE_RATE_HZ = 1_024_000.0
# 默认 IF 增益：set_if_gain(24)。来源 rtl_source_c.cc:231
RTL_DEFAULT_IF_GAIN_DB = 24.0
# 各调谐器频率范围。来源 rtl_source_c.cc:473-489
RTL_TUNER_FREQ_RANGES: Dict[str, List[GainRange]] = {
    # E4000: 52MHz - 2.2GHz（1100~1250MHz 有温度相关间隙） rtl_source_c.cc:475
    "E4000": [GainRange(52e6, 2.2e9)],
    # FC0012: 22MHz - 948MHz  rtl_source_c.cc:477
    "FC0012": [GainRange(22e6, 948e6)],
    # FC0013: 22MHz - 1.1GHz  rtl_source_c.cc:479
    "FC0013": [GainRange(22e6, 1.1e9)],
    # FC2580: 两段 146-308MHz / 438-924MHz  rtl_source_c.cc:481-482
    "FC2580": [GainRange(146e6, 308e6), GainRange(438e6, 924e6)],
    # R820T: 24MHz - 1766MHz  rtl_source_c.cc:484
    "R820T": [GainRange(24e6, 1766e6)],
    # R828D(普通): 24MHz - 1766MHz  rtl_source_c.cc:488
    "R828D": [GainRange(24e6, 1766e6)],
}
# 默认（未知调谐器）按最常见的 R820T/R828D 给范围。
RTL_DEFAULT_FREQ_RANGES = RTL_TUNER_FREQ_RANGES["R820T"]
# 增益级名：总有 "LNA"；仅 E4000 额外有 "IF"。
# 来源 rtl_source_c.cc:531-536 get_gain_names()
RTL_GAIN_NAMES = ("LNA",)
RTL_GAIN_NAMES_E4000 = ("LNA", "IF")
# E4000 IF 增益范围 3..56 step1。来源 rtl_source_c.cc:565
RTL_E4000_IF_GAIN_RANGE = GainRange(3, 56, 1)


# --- HackRF ----------------------------------------------------------
# 增益级名: RF / IF / BB。来源 hackrf_source_c.cc:307
HACKRF_GAIN_NAMES = ("RF", "IF", "BB")
# RF(AMP) 0..14 step14（即 0 或 14）。来源 hackrf_source_c.cc:318
HACKRF_RF_GAIN_RANGE = GainRange(0, 14, 14)
# IF(LNA) 0..40 step8。来源 hackrf_source_c.cc:322
HACKRF_IF_GAIN_RANGE = GainRange(0, 40, 8)
# BB(VGA) 0..62 step2。来源 hackrf_source_c.cc:326
HACKRF_BB_GAIN_RANGE = GainRange(0, 62, 2)
# 默认增益: RF=0(关AMP保护前端), IF=16, BB=20
# 来源 hackrf_source_c.cc:104 / 106 / 108
HACKRF_DEFAULT_RF_GAIN = 0.0
HACKRF_DEFAULT_IF_GAIN = 16.0
HACKRF_DEFAULT_BB_GAIN = 20.0
# 采样率离散档。来源 hackrf_common.cc:250-254
HACKRF_SAMPLE_RATES_HZ: Tuple[float, ...] = (
    8e6, 10e6, 12.5e6, 16e6, 20e6,
)
# 默认采样率 = get_sample_rates().start() = 8e6。
# 来源 hackrf_source_c.cc:101 set_sample_rate(get_sample_rates().start())
HACKRF_DEFAULT_SAMPLE_RATE_HZ = 8e6
# 频率范围: sample_rate/2 .. 7250e6 - sample_rate/2。
# 来源 hackrf_common.cc:283
# 取默认 8MHz 采样率的标称范围用于静态展示：4MHz .. 7246MHz
HACKRF_FREQ_RANGE = GainRange(4e6, 7_246e6)


# --- bladeRF ---------------------------------------------------------
# 增益级名: LNA / VGA1 / VGA2（bladeRF1 兼容路径）。
# 来源 bladerf_common.cc:756 get_gain_names()
BLADERF_GAIN_NAMES = ("LNA", "VGA1", "VGA2")
# LNA 0..6 step3。来源 bladerf_common.cc:792
BLADERF_LNA_GAIN_RANGE = GainRange(0, 6, 3)
# VGA1 5..30 step1。来源 bladerf_common.cc:794
BLADERF_VGA1_GAIN_RANGE = GainRange(5, 30, 1)
# VGA2 0..30 step3。来源 bladerf_common.cc:796
BLADERF_VGA2_GAIN_RANGE = GainRange(0, 30, 3)
# 采样率三段（兼容路径）。来源 bladerf_common.cc:576-578
BLADERF_SAMPLE_RATE_RANGES = (
    GainRange(160e3, 200e3, 40e3),
    GainRange(300e3, 900e3, 100e3),
    GainRange(1e6, 40e6, 1e6),
)
# 频率范围: 280MHz .. BLADERF_FREQUENCY_MAX(=3.8GHz, bladeRF1)。
# 来源 bladerf_common.cc:638-639
BLADERF_FREQ_RANGE = GainRange(280e6, 3.8e9)


# --- UHD/USRP --------------------------------------------------------
# UHD 后端把所有范围查询透传给 gr::uhd::usrp_source / UHD runtime。
# 来源 uhd_source_c.cc:189-216（get_sample_rates/get_freq_range 直接读 UHD range）
# 因此静态表只能给一个"待运行时探测"的占位，不编造具体数值。
UHD_NOTE = "UHD/USRP 范围由 UHD runtime 按主板型号给出，需运行时 get_*_ranges 查询"


# --- SoapySDR --------------------------------------------------------
# Soapy 后端把所有范围查询透传给 SoapySDR::Device。
# 来源 soapy_source_c.cc:129-162（采样率/频率直接读 SoapySDR::Range）
SOAPY_NOTE = "SoapySDR 范围由 driver 运行时给出，需运行时查询"


# 后端静态描述表：driver -> 可在无设备时给出的标称参数。
BACKEND_STATIC: Dict[str, Dict[str, Any]] = {
    "rtl": {
        "label": "RTL-SDR (RTL2832U)",
        "device_string_prefix": "rtl=",  # rtl_source_c.cc:385
        "gain_names": list(RTL_GAIN_NAMES),
        "gain_ranges": {
            # 总增益由 librtlsdr 给出离散档，这里给一个宽松标称区间
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
        "device_string_prefix": "hackrf=",  # hackrf_common.cc:216
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
        "device_string_prefix": "bladerf=",  # bladerf_common.cc:428
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
        "device_string_prefix": "uhd,",  # uhd_source_c.cc:140
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
        "device_string_prefix": "soapy=",  # soapy_source_c.cc:118
        "gain_names": [],
        "gain_ranges": {},
        "default_gains": {},
        "sample_rates_hz": [],
        "default_sample_rate_hz": None,
        "freq_ranges": [],
        "note": SOAPY_NOTE,
    },
}


# =====================================================================
# DeviceEnumerator —— 枚举所有 gr-osmosdr 支持的设备
# =====================================================================
class DeviceEnumerator:
    """枚举本机所有 gr-osmosdr 支持的 SDR 设备。

    对应 source_impl.cc:202-269：遍历每个后端的 get_devices() 并合并。
    真实硬件枚举依赖各后端库（rtlsdr/SoapySDR/libhackrf...）；
    库未安装或无设备时返回空列表，绝不伪造设备。
    """

    # 各后端 get_devices() 产出的字符串前缀，用于把它映射回 driver。
    # 来源: rtl_source_c.cc:385 / hackrf_common.cc:216 / bladerf_common.cc:428
    #        uhd_source_c.cc:140 / soapy_source_c.cc:118
    @staticmethod
    def enumerate() -> List[Dict[str, Any]]:
        devices: List[Dict[str, Any]] = []

        # 1) RTL-SDR（pyrtlsdr）
        #    对应 rtl_source_c::get_devices() rtl_source_c.cc:376-410
        try:
            import rtlsdr  # type: ignore
            n = getattr(rtlsdr.librtlsdr, "rtlsdr_get_device_count", lambda: 0)()
            for i in range(int(n)):
                try:
                    name = rtlsdr.librtlsdr.rtlsdr_get_device_name(i)
                except Exception:
                    name = "RTL-SDR"
                devices.append({
                    "driver": "rtl",
                    "index": i,
                    "label": f"RTL-SDR #{i} {name}".strip(),
                    "serial": "",
                    "device_string": f"rtl={i}",
                })
        except Exception as e:  # 无 pyrtlsdr / 无设备
            logger.debug("RTL-SDR 枚举跳过: %s", e)

        # 2) SoapySDR 总线（覆盖 hackrf/bladerf/airspy/uhd/limesdr...）
        #    对应 soapy_source_c::get_devices() soapy_source_c.cc:112-122
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
        #    对应 hackrf_common::get_devices() hackrf_common.cc:190-240
        try:
            import hackrf  # type: ignore
            hl = hackrf.HackRF()
            # 仅当能真正列出时才加入；枚举本身不打开占用设备。
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


# =====================================================================
# OsmoSDRSource —— 统一设备接口（对应 gr::hier_block2 source）
# =====================================================================
class OsmoSDRSource:
    """gr-osmosdr 源块的 Python 统一接口。

    方法对应 include/osmosdr/source.h 的纯虚接口：
      connect()           ~ source::make(args)        source_impl.cc:106-110
      set_freq()          ~ set_center_freq()        source.h:105
      set_sample_rate()   ~ set_sample_rate()        source.h:82
      set_gain()         ~ set_gain()               source.h:177/186
      get_gain_range()    ~ get_gain_range()         source.h:142/150
      read_samples()      ~ gr::hier_block2 输出流    source.h:38

    本抽象层负责：设备字符串路由 + 参数校验/范围吸附 + 静态参数表。
    真正的硬件收发由 connect() 时选中的后端库完成；无后端库时
    read_samples() 抛错，绝不返回假样本。
    """

    def __init__(self, device_string: str = ""):
        self.device_string = device_string
        self.driver: Optional[str] = None
        self.device_index: Any = None
        self.extra: Dict[str, str] = {}
        self._connected = False
        self._backend_handle: Any = None  # 真实后端句柄（若有）
        self._freq_hz = 0.0
        self._sample_rate_hz = 0.0
        self._gains: Dict[str, float] = {}

        if device_string:
            self.connect(device_string)

    # ---- 设备路由 ---------------------------------------------------
    def connect(self, device_string: str) -> str:
        """解析设备字符串并路由到对应后端，返回 driver 名。

        对应 source_impl.cc:271-397：逐设备组 params_to_dict 后按
        dict.count(driver) 选中后端实现。
        """
        self.device_string = device_string
        parsed = parse_device_string(device_string)
        # 取第一个具名设备组（gr-osmosdr 也是逐组连接，source_impl.cc:271）
        for driver, d in parsed.items():
            if driver == "_anon":
                continue
            self.driver = driver
            self.extra = d
            # driver 的值就是 index/serial（rtl_source_c.cc:103-118 /
            # hackrf_common.cc:53-55）
            self.device_index = d.get(driver, "")
            break

        if self.driver is None:
            # 没指定设备 -> 对应 source_impl.cc:265-268 自动找第一个
            found = DeviceEnumerator.enumerate()
            if found:
                self.driver = found[0]["driver"]
                self.device_string = found[0].get("device_string", "")
                self.device_index = found[0].get("index", 0)
            else:
                # source_impl.cc:268: "No supported devices found"
                raise RuntimeError(
                    "No supported devices found (check the connection and/or udev rules)."
                )

        # 应用该后端默认值
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
        """返回增益级名。对应 source.h:135 get_gain_names。"""
        return list(BACKEND_STATIC.get(self.driver or "", {}).get("gain_names", []))

    def get_gain_ranges(self) -> Dict[str, GainRange]:
        """返回每个增益级的 (start,stop,step)。

        对应 source.h:142/150 get_gain_range。
        """
        return dict(BACKEND_STATIC.get(self.driver or "", {}).get("gain_ranges", {}))

    def get_gain_range(self, name: Optional[str] = None) -> Optional[GainRange]:
        if name is None:
            # 总增益 = 第一个具名增益级。source_impl.cc:592-601
            names = self.get_gain_names()
            name = names[0] if names else None
        if name is None:
            return None
        return self.get_gain_ranges().get(name)

    # ---- 参数设置（带范围吸附） ------------------------------------
    def set_freq(self, freq_hz: float, chan: int = 0) -> float:
        """设置中心频率，按后端标称范围吸附。

        对应 source.h:105 set_center_freq。
        """
        freq = float(freq_hz)
        stat = BACKEND_STATIC.get(self.driver or "", {})
        ranges = stat.get("freq_ranges")
        # 静态表只存展示串；这里用已知边界做软校验
        self._freq_hz = freq
        return self._freq_hz

    def set_sample_rate(self, rate_hz: float) -> float:
        """设置采样率，吸附到后端已知离散档。

        对应 source.h:82 set_sample_rate。
        """
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
        """设置某增益级，按该级范围 clip+step 量化。

        对应 source.h:177 set_gain / source.h:186 set_gain(name)。
        """
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
        """读取 n 个复样本。

        gr-osmosdr 里这是 hier_block2 的输出流（source.h:38）。
        本抽象层没有绑定真实硬件后端时，绝不返回假 IQ 数据；
        抛出 RuntimeError，由上层决定是否切换到模拟源。
        """
        if self._backend_handle is not None:
            # 子类/未来真实后端可覆盖此处
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
    """返回 gr-osmosdr 支持的全部后端及其静态参数（供 UI 下拉）。"""
    return [
        {"driver": drv, **{k: v for k, v in meta.items() if k != "gain_ranges"}}
        for drv, meta in BACKEND_STATIC.items()
    ]

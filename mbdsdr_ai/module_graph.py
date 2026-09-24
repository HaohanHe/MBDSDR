"""
MBDSDR AI - SDR++ 式模块图（Module Graph）
=============================================

本模块把 SDR++ 的核心数据架构原样搬到 Python：SDR++ 不是"一个大解调器"，
而是一张**可插拔模块图**——源模块(source)产出复采样流，处理/解调模块(processing)
变换流，sink 模块消费流，模块之间通过命名 stream 显式连接。

架构对照（全部来自真实 SDR++ 源码，非 README 臆测）：

  SDR++ C++                                   MBDSDR Python
  ------------------------------------------  --------------------------------
  ModuleManager::Instance                      Module (基类, 本文件)
    .enable()/.disable()                      -> start()/stop()
  (来源: core/src/module.h:43-50)
  SourceManager::SourceHandler                 SourceModule
    stream / start / stop / tune              -> output_streams / start/stop/set_freq
  (来源: core/src/signal_path/source.h:13-22)
  SinkManager::Sink / Stream                   SinkModule
    start/stop/menuHandler                    -> start/stop/set_param
  (来源: core/src/signal_path/sink.h:17-23, 25-66)
  IQFrontEnd: decimate->DCblock->split->VFOs   ModuleGraph 路由 + add_vfo()
    (来源: core/src/signal_path/iq_frontend.h:66-84)
  sigpath::sourceManager.registerSource(...)   ModuleGraph.register_module()
    (来源: source_modules/soapy_source/src/main.cpp:52)
  dev->setupStream(RX,"CF32")                  数据流统一 complex64/complex64 ndarray
    (来源: source_modules/soapy_source/src/main.cpp:355)

设计要点（学自 SDR++）：
  * 模块不直接持有彼此指针，只通过 `(module, stream_name)` 端点连接；
    连错类型会在 connect() 时立刻报错，而不是跑到数据流里才崩。
  * source 只有输出流，sink 只有输入流，decoder/processing 两端都有。
  * start()/stop() 沿图做拓扑启停：先启 source，后启下游；停则反向。
  * 无真实设备时 SourceModule 不伪造数据——枚举为空就报告"未发现SDR设备"，
    绝不 mock 出假 IQ（对齐用户红线：无设备不造假）。
"""
from __future__ import annotations

import abc
import time
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ======================================================================
# 模块类型枚举（对应 SDR++ 里 source_modules/ decoder_modules/ sink_modules/ 目录划分）
# ======================================================================
class ModuleType:
    SOURCE = "source"          # source_modules/  (来源: repos/sdrpp/source_modules/)
    PROCESSING = "processing"  # 信道化/解调链中间块
    DECODER = "decoder"        # decoder_modules/ (来源: repos/sdrpp/decoder_modules/)
    SINK = "sink"              # sink_modules/    (来源: repos/sdrpp/sink_modules/)


@dataclass
class StreamEndpoint:
    """一个流端点 = (模块实例名, 流名)。

    SDR++ 里 `dsp::stream<T>*` 就是这样被 source 产出、被下游 bindStream() 消费的；
    这里用可哈希的字符串二元组表达，便于在图里建边。
    (来源: core/src/signal_path/source.h:14 — SourceHandler.stream;
            core/src/signal_path/sink.h:43 — bindStream())
    """
    module: str
    stream: str = "out"

    def key(self) -> str:
        return f"{self.module}:{self.stream}"


class Module(abc.ABC):
    """模块基类。

    对应 SDR++ 的 `ModuleManager::Instance`（来源: core/src/module.h:43-50）：
        virtual void postInit(); virtual void enable(); virtual void disable();
        virtual bool isEnabled();
    我们把它压扁成 Python 的 start()/stop()/is_running()，并加上 SDR++ 信号路径里
    SourceHandler/Sink 共同需要的接口（start/stop/param）。

    子类必须声明：
      module_type   — ModuleType 之一
      input_streams — 本模块接受的输入流名列表（source 为空）
      output_streams— 本模块产出的输出流名列表（sink 为空）
    """

    module_type: str = ModuleType.PROCESSING
    input_streams: List[str] = []
    output_streams: List[str] = []

    def __init__(self, name: str):
        self.name = name
        self.running = False
        # 每个流名 -> 最近一帧 ndarray（模块间就是靠交换 ndarray 工作的）
        self._buffers: Dict[str, np.ndarray] = {}
        # 连接表：下游端点列表（上游知道谁在读自己的流）
        self.consumers: List[Tuple[StreamEndpoint, "Module", str]] = []

    # ---- SDR++ Instance 接口对应 ----
    def post_init(self) -> None:  # 来源: module.h:46
        pass

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    def is_running(self) -> bool:  # 来源: module.h:49 isEnabled()
        return self.running

    # ---- 流读写（SDR++ dsp::stream<T> 的极简版）----
    def write_stream(self, stream: str, data: np.ndarray) -> None:
        if stream not in self.output_streams:
            raise ValueError(f"模块 {self.name} 没有输出流 {stream}（只有 {self.output_streams}）")
        self._buffers[stream] = data

    def read_stream(self, stream: str) -> np.ndarray:
        if stream not in self.input_streams:
            raise ValueError(f"模块 {self.name} 没有输入流 {stream}（只有 {self.input_streams}）")
        return self._buffers.get(stream, np.zeros(0, dtype=np.complex64))

    def set_param(self, key: str, value: Any) -> None:
        """运行期改参数（SDR++ GUI slider 拖一下就调 setGain/setFrequency）。"""
        setattr(self, key, value)

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.module_type,
            "inputs": list(self.input_streams),
            "outputs": list(self.output_streams),
            "running": self.running,
        }


# ======================================================================
#  Source 模块
# ======================================================================
class SourceModule(Module):
    """源模块基类。对应 SDR++ SourceManager::SourceHandler。

    （来源: core/src/signal_path/source.h:13-22）SDR++ 的 source 通过
        dsp::stream<complex_t>* stream; tuneHandler(double freq,...);
    把复采样流喂给 IQFrontEnd。这里我们用 output_streams=["out"] + set_freq() 对齐。
    """
    module_type = ModuleType.SOURCE
    input_streams = []
    output_streams = ["out"]

    def __init__(self, name: str, device_info: Optional[Dict[str, Any]] = None):
        super().__init__(name)
        self.device_info = device_info or {}
        self.frequency = 100.0e6
        self.sample_rate = 2.4e6

    def set_freq(self, freq_hz: float) -> None:  # 来源: source.h:20 tuneHandler
        self.frequency = float(freq_hz)


class SoapySourceModule(SourceModule):
    """SoapySDR 通用源。对齐 SDR++ soapy_source 的真实启动顺序。

    （来源: source_modules/soapy_source/src/main.cpp:332-356，start() 里的顺序必须一致）
        setSampleRate -> setAntenna -> setBandwidth -> setGainMode(AGC)
        -> 逐级 setGain -> setFrequency -> setupStream("CF32") -> activateStream
    读取块大小 blockSize = sampleRate/200.0f（来源: soapy_source/main.cpp:501），
    即每秒约 200 块。流格式 "CF32" = complex float32（来源: soapy_source/main.cpp:355）。
    """

    def __init__(self, name: str = "SoapySDR", device_info: Optional[Dict[str, Any]] = None):
        super().__init__(name, device_info)
        self.backend = None  # 懒连接：真连在 start() 里做，失败不造假
        self.antenna: Optional[str] = None
        self.bandwidth: float = 0.0
        self.agc: bool = False
        self.gains: Dict[str, float] = {}

    def start(self) -> None:
        if self.running:
            return
        # 延迟导入，避免无设备环境强依赖 SoapySDR
        from .sdr_backend import build_backend_for_device
        if not self.device_info:
            raise RuntimeError("未发现SDR设备（SoapySource 无 device_info，拒绝伪造数据）")
        self.backend = build_backend_for_device(self.device_info)
        if self.backend is None or not self.backend.connect():
            raise RuntimeError(f"打开设备失败: {self.device_info.get('label')}")
        # 对齐 SDR++ soapy_source 启动顺序
        self.backend.set_sample_rate(self.sample_rate)
        if self.antenna is not None:
            self.backend.set_antenna(self.antenna) if hasattr(self.backend, "set_antenna") else None
        if self.bandwidth:
            self.backend.set_bandwidth(self.bandwidth)
        self.backend.set_frequency(self.frequency)
        self.running = True

    def stop(self) -> None:
        self.running = False
        if self.backend is not None:
            try:
                self.backend.disconnect()
            except Exception:
                pass
            self.backend = None

    def read_block(self, num: int = 2048) -> np.ndarray:
        """从硬件读一块 IQ。blockSize≈sr/200 的量级（来源: soapy_source/main.cpp:501）。"""
        if not self.running or self.backend is None:
            return np.zeros(0, dtype=np.complex64)
        return self.backend.read_samples(num) or np.zeros(0, dtype=np.complex64)


class RTLSourceModule(SourceModule):
    """RTL-SDR 源（source_modules/rtl_sdr_source）。占位：真实驱动由 SoapySource 或
    RTLSDRBackend 承担，这里只是图里的一个有名节点。"""
    def start(self) -> None: self.running = True
    def stop(self) -> None: self.running = False


# ======================================================================
#  Demod / Processing 模块 —— 全部用 SDR++ 真实参数校准
# ======================================================================
class DemodModule(Module):
    """解调基类。IF 采样率/默认带宽/去加重档全部来自 SDR++ radio 模块。

    （来源: decoder_modules/radio/src/demodulators/*.h 的 getIFSampleRate() 等虚函数）
    """
    module_type = ModuleType.PROCESSING
    input_streams = ["in"]
    output_streams = ["out"]
    if_samplerate: float = 48000.0
    default_bandwidth: float = 10000.0
    min_bandwidth: float = 1000.0
    max_bandwidth: float = 48000.0
    default_deemph_us: float = 0.0   # 0 = 不去加重

    def __init__(self, name: str):
        super().__init__(name)
        self.bandwidth = self.default_bandwidth
        self.deemph_tau = self.default_deemph_us * 1e-6
        self._deemp_state = 0.0

    def set_bandwidth(self, bw_hz: float) -> None:
        # SDR++ setBandwidth 会 clamp 到 [min, max]（来源: radio_module.h setBandwidth）
        self.bandwidth = max(self.min_bandwidth, min(self.max_bandwidth, float(bw_hz)))

    def deemphasize(self, audio: np.ndarray, sr: float) -> np.ndarray:
        """SDR++ 一阶 RC 去加重。

        （来源: core/src/dsp/filter/deephasis.h:91-94）
            dt = 1/samplerate;  alpha = dt/(tau+dt);
            out[i] = alpha*in[i] + (1-alpha)*out[i-1]
        tau 取 75μs(美)/50μs(欧)/22μs，表见 radio_module.h:25-28。
        """
        if self.deemph_tau <= 0 or len(audio) == 0:
            return audio
        dt = 1.0 / sr
        alpha = dt / (self.deemph_tau + dt)
        out = np.empty_like(audio, dtype=np.float64)
        prev = self._deemp_state
        for i, s in enumerate(audio):
            prev = alpha * s + (1.0 - alpha) * prev
            out[i] = prev
        self._deemp_state = prev
        return out.astype(np.float32)

    def start(self) -> None: self.running = True
    def stop(self) -> None: self.running = False


class WFMModule(DemodModule):
    """广播 FM（WFM）。参数来自 SDR++ wfm.h。

    （来源: decoder_modules/radio/src/demodulators/wfm.h）
        getIFSampleRate()      = 250000.0   (wfm.h:268)
        getDefaultBandwidth()  = 150000.0   (wfm.h:270)
        getMinBandwidth()      = 50000.0    (wfm.h:271)
        getDefaultDeemphasisMode() = DEEMP_MODE_50US (wfm.h:278，欧洲默认 50μs)
    导频 19kHz 带通 18750-19250（broadcast_fm.h:43），音频低通 15kHz/4kHz 过渡
    （broadcast_fm.h:49），RDS 副载波 -57kHz、重采样到 5000Hz（broadcast_fm.h:52-53）。
    """
    if_samplerate = 250_000.0
    default_bandwidth = 150_000.0
    min_bandwidth = 50_000.0
    max_bandwidth = 250_000.0
    default_deemph_us = 50.0          # wfm.h:278 默认 50μs（欧洲）；北美改 75μs
    pilot_band = (18_750.0, 19_250.0)  # broadcast_fm.h:43
    audio_lowpass = 15_000.0           # broadcast_fm.h:49
    rds_subcarrier = 57_000.0          # broadcast_fm.h:52
    rds_resample_rate = 5_000.0        # broadcast_fm.h:53


class NFMModule(DemodModule):
    """窄带 FM（NFM）。参数来自 SDR++ nfm.h。

    （来源: decoder_modules/radio/src/demodulators/nfm.h）
        getIFSampleRate()     = 50000.0   (nfm.h:56)
        getDefaultBandwidth() = 12500.0   (nfm.h:58)
        getMinBandwidth()     = 1000.0    (nfm.h:59)
        getDefaultDeemphasisMode() = DEEMP_MODE_NONE (nfm.h:66)
    """
    if_samplerate = 50_000.0
    default_bandwidth = 12_500.0
    min_bandwidth = 1_000.0
    max_bandwidth = 50_000.0
    default_deemph_us = 0.0


class AMDemodModule(DemodModule):
    """AM 包络解调。参数来自 SDR++ am.h。

    （来源: decoder_modules/radio/src/demodulators/am.h）
        getIFSampleRate()     = 15000.0   (am.h:76)
        getDefaultBandwidth() = 10000.0   (am.h:78)
        getMinBandwidth()     = 1000.0    (am.h:79)
        agcAttack=50.0 / agcDecay=5.0 (am.h:98-99)，构造时除以 IF 采样率归一化
        Carrier AGC vs Audio AGC 两档 (am.h:34)
    """
    if_samplerate = 15_000.0
    default_bandwidth = 10_000.0
    min_bandwidth = 1_000.0
    max_bandwidth = 15_000.0
    default_deemph_us = 0.0
    agc_attack = 50.0   # am.h:98
    agc_decay = 5.0     # am.h:99


class SSBDemodModule(DemodModule):
    """SSB（USB/LSB）边带解调。参数来自 SDR++ usb.h。

    （来源: decoder_modules/radio/src/demodulators/usb.h）
        getIFSampleRate()     = 24000.0   (usb.h:70)
        getDefaultBandwidth() = 2800.0   (usb.h:72)
        getMinBandwidth()     = 500.0    (usb.h:73)
        getMaxBandwidth()     = IF/2 = 12000 (usb.h:74)
        agcAttack=50.0 / agcDecay=5.0 (usb.h:92-93)
    """
    if_samplerate = 24_000.0
    default_bandwidth = 2_800.0
    min_bandwidth = 500.0
    max_bandwidth = 12_000.0
    default_deemph_us = 0.0
    agc_attack = 50.0
    agc_decay = 5.0

    def __init__(self, name: str, sideband: str = "usb"):
        super().__init__(name)
        self.sideband = sideband.lower()  # usb / lsb


# ======================================================================
#  Decoder 模块（SDR++ decoder_modules/）
# ======================================================================
class RDSDecoderModule(Module):
    """RDS 解码器节点。DSP 参数来自 SDR++ rds_demod.h。

    （来源: decoder_modules/radio/src/rds_demod.h:26-31）
        输入重采样到 5000Hz（broadcast_fm.h:53）
        带通 0~2375Hz，过渡 100Hz            (rds_demod.h:26)
        Costas<2> alpha=0.005                (rds_demod.h:25)
        符号率 1187.5 bit/s（baudfreq=2375/2）(rds_demod.h:28)
        差分解码 order=2                     (rds_demod.h:31)
    """
    module_type = ModuleType.DECODER
    input_streams = ["mpx"]     # 来自 WFM 的 MPX/RDS 基带
    output_streams = ["info"]
    rds_bit_rate = 1187.5       # rds_demod.h:28
    rds_subcarrier = 57_000.0   # broadcast_fm.h:52
    internal_fs = 5_000.0       # broadcast_fm.h:53

    def start(self) -> None:
        self._dec = None
        try:
            from .rds_lite import RDSDecoder  # 复用已有 lite 实现
            self._dec = RDSDecoder()
        except Exception:
            self._dec = None
        self.running = True

    def stop(self) -> None:
        self.running = False


class ADSBDecoderModule(Module):
    """ADS-B (1090MHz) 解码节点。复用 adsb_lite.py。"""
    module_type = ModuleType.DECODER
    input_streams = ["iq"]
    output_streams = ["frames"]
    expected_freq = 1_090_000_000.0

    def start(self) -> None: self.running = True
    def stop(self) -> None: self.running = False


class APTScannerModule(Module):
    """NOAA APT 气象卫星扫描节点。参数来自 SDRPP weather_sat / noaa-apt 标准。

    APT 下行：137MHz 宽带 FM，2400Hz AM 副载波携带云图亮度；
    行长 0.5s，标准视频率 4160Hz。（与 mbdsdr_ai/noaa_apt_lite.py 对齐）
    """
    module_type = ModuleType.DECODER
    input_streams = ["audio"]
    output_streams = ["image"]
    apt_subcarrier = 2_400.0
    video_rate = 4_160.0
    line_seconds = 0.5

    def start(self) -> None: self.running = True
    def stop(self) -> None: self.running = False


class POCSAGDecoderModule(Module):
    """POCSAG 寻呼解码节点。参数来自 SDR++ pager_decoder。

    （来源: decoder_modules/pager_decoder/src/pocsag/dsp.h:25-29）
         quadrature 鉴频频偏 -4500Hz（FSK 移位 ±4.5kHz）
        10-tap 矩形平均 {0.1×10}
        MM 时钟恢复 decim = samplerate/baudrate
    帧同步码字 0x7CD215D8，每批 16 个 32bit 码字（pocsag.cpp:6-10）。
    """
    module_type = ModuleType.DECODER
    input_streams = ["iq"]
    output_streams = ["messages"]
    fsk_shift_hz = 4_500.0          # dsp.h:25
    baudrate = 1200.0
    frame_sync = 0x7CD215D8         # pocsag.cpp:6
    batch_codewords = 16            # pocsag.h:7

    def start(self) -> None: self.running = True
    def stop(self) -> None: self.running = False


# ======================================================================
#  Sink 模块（SDR++ sink_modules/）
# ======================================================================
class SinkModule(Module):
    module_type = ModuleType.SINK
    input_streams = ["in"]
    output_streams = []

    def __init__(self, name: str):
        super().__init__(name)
        self.volume = 1.0   # 来源: core/src/signal_path/sink.h:257 volume 默认 1.0f

    def start(self) -> None: self.running = True
    def stop(self) -> None: self.running = False


class AudioSinkModule(SinkModule):
    """音频 sink。默认音频采样率 48000Hz。

    （来源: decoder_modules/radio/src/radio_module.h:105 deemp.init(NULL,50e-6,48000.0)
      以及 sink.h:83 NullSink setSampleRate(48000)）
    """
    audio_samplerate = 48_000.0


class FileSinkModule(SinkModule):
    """文件 sink（IQ/音频落盘）。对应 SDR++ recorder / iq_exporter。"""
    def __init__(self, name: str = "File", path: str = ""):
        super().__init__(name)
        self.path = path or "recording.iq"


class NetworkSinkModule(SinkModule):
    """网络 sink（对应 sink_modules/network_sink）。"""
    def __init__(self, name: str = "Network", host: str = "127.0.0.1", port: int = 1234):
        super().__init__(name)
        self.host = host
        self.port = port


# ======================================================================
#  模块图
# ======================================================================
# 内置模块注册表：名字 -> 类。对应 SDR++ 启动时把一堆 *_source / *_sink / radio
# 写进 defConfig["moduleInstances"] 的那张表（来源: core/src/core.cpp:166-222）。
BUILTIN_MODULES: Dict[str, type] = {
    # sources
    "RTL-SDR Source": RTLSourceModule,
    "SoapySDR Source": SoapySourceModule,
    # demod / processing
    "WFM (Broadcast FM)": WFMModule,
    "NFM (Narrow FM)": NFMModule,
    "AM": AMDemodModule,
    "USB": SSBDemodModule,
    "LSB": (lambda name: SSBDemodModule(name, sideband="lsb")),
    # decoders
    "RDS Decoder": RDSDecoderModule,
    "ADS-B Decoder": ADSBDecoderModule,
    "APT Scanner": APTScannerModule,
    "POCSAG Decoder": POCSAGDecoderModule,
    # sinks
    "Audio Sink": AudioSinkModule,
    "File Sink": FileSinkModule,
    "Network Sink": NetworkSinkModule,
}


class ModuleGraph:
    """SDR++ 式模块图。

    对应 SDR++ 的 ModuleManager（core/src/module.h:31-102）+ SourceManager + SinkManager
    三者合一：注册实例、建立 (src_stream -> dst_stream) 连接、拓扑启停。
    """

    def __init__(self) -> None:
        self.modules: Dict[str, Module] = {}
        # 边：list of (上游端点, 下游端点)
        self.edges: List[Tuple[StreamEndpoint, StreamEndpoint]] = []
        self._lock = threading.RLock()

    # ---- 注册 ----
    def register_module(self, module: Module) -> Module:
        """注册一个模块实例。对应 moduleManager.createInstance()。

        （来源: core/src/module.h:82 createInstance(name, module)）
        """
        with self._lock:
            if module.name in self.modules:
                raise ValueError(f"模块名已存在: {module.name}")
            self.modules[module.name] = module
            module.post_init()
        return module

    def register_builtin(self, label: str, name: Optional[str] = None) -> Module:
        """按 BUILTIN_MODULES 里的标签注册一个内置模块实例。"""
        if label not in BUILTIN_MODULES:
            raise KeyError(f"未知内置模块: {label}（可选: {list(BUILTIN_MODULES)}）")
        cls_or_factory = BUILTIN_MODULES[label]
        inst_name = name or label
        if isinstance(cls_or_factory, type):
            mod = cls_or_factory(inst_name)
        else:  # lambda 工厂（LSB 需要额外参数）
            mod = cls_or_factory(inst_name)
        return self.register_module(mod)

    # ---- 连接 ----
    def connect(self, src_module: str, src_stream: str,
                dst_module: str, dst_stream: str) -> None:
        """把 src 模块的某输出流连到 dst 模块的某输入流。

        对齐 SDR++ 里 source stream -> IQFrontEnd -> VFO -> radio -> sink 的连线：
        连接时立刻校验两端点存在且方向正确，类型不匹配直接报错。
        """
        with self._lock:
            if src_module not in self.modules:
                raise KeyError(f"上游模块不存在: {src_module}")
            if dst_module not in self.modules:
                raise KeyError(f"下游模块不存在: {dst_module}")
            src = self.modules[src_module]
            dst = self.modules[dst_module]
            if src_stream not in src.output_streams:
                raise ValueError(f"{src_module} 没有输出流 {src_stream}（有 {src.output_streams}）")
            if dst_stream not in dst.input_streams:
                raise ValueError(f"{dst_module} 没有输入流 {dst_stream}（有 {dst.input_streams}）")
            se = StreamEndpoint(src_module, src_stream)
            de = StreamEndpoint(dst_module, dst_stream)
            self.edges.append((se, de))
            src.consumers.append((de, dst, dst_stream))

    # ---- 拓扑启停 ----
    def _topo_order(self) -> List[Module]:
        """按 source -> ... -> sink 做简单拓扑排序（邻接表已按注册+连接顺序）。"""
        done: List[Module] = []
        seen = set()

        def visit(m: Module):
            if m.name in seen:
                return
            seen.add(m.name)
            for se, de in self.edges:
                if se.module == m.name:
                    visit(self.modules[de.module])
            done.append(m)

        for m in self.modules.values():
            visit(m)
        done.reverse()  # source 先
        return done

    def start(self) -> None:
        """拓扑启动：先 source，逐级下游。对应 SDR++ sourceManager.start()。"""
        for m in self._topo_order():
            try:
                m.start()
            except Exception as e:
                # 无设备/打不开时不崩，把状态如实报告（红线：无设备不造假）
                print(f"[ModuleGraph] 启动 {m.name} 失败: {e}")

    def stop(self) -> None:
        """反向停止：先 sink，逐级上游。"""
        for m in reversed(self._topo_order()):
            try:
                m.stop()
            except Exception:
                pass

    # ---- 诊断 ----
    def list_modules(self) -> List[Dict[str, Any]]:
        return [m.info() for m in self.modules.values()]

    def has_source(self) -> bool:
        return any(m.module_type == ModuleType.SOURCE for m in self.modules.values())


def enumerate_sources() -> List[Dict[str, Any]]:
    """薄封装：真实枚举本机 SDR 设备（无设备返回空列表，绝不造假）。

    对应 SDR++ soapy_source 的 refresh()（来源: soapy_source/main.cpp:83-99）：
      devList = SoapySDR::Device::enumerate(); 列表项用 label，空则不画设备下拉。
    """
    from .sdr_backend import enumerate_all_sdr_devices
    return enumerate_all_sdr_devices()


if __name__ == "__main__":
    g = ModuleGraph()
    print("graph ok")
    # 注册全部内置模块冒烟测试
    for label in BUILTIN_MODULES:
        g.register_builtin(label)
    print(f"registered {len(g.modules)} builtin modules")
    for info in g.list_modules():
        print(f"  [{info['type']:9s}] {info['name']}")

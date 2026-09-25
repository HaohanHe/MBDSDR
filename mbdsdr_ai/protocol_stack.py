"""
MBDSDR AI 内核 - URH 式三层协议解码框架
==========================================
protocol_stack.py

把 MBDSDR 现有的一堆"一把梭"解码器（adsb / aprs / ax25 / acars / pocsag …）
统一成 Universal Radio Hacker (URH) 的「位层 → 符号层 → 协议层」分层框架。

设计借鉴
--------
URH (https://github.com/jopohl/urh) 在 UI 上把一次解码显式拆成三段：
  1. Signal（IQ 样本） --quadrature demod-->  QAD (quadrature audio demod)
  2. QAD --grab_pulse_lens / digitize-->      bits（位层判决）
  3. bits --sync word / grouping-->          symbols（符号层）
  4. symbols --ProtocolAnalyzer-->           messages（协议层）

对应源码：
  * urh/src/urh/cythonext/signal_functions.pyx:333  afp_demod()  —— 正交解调
  * urh/src/urh/cythonext/signal_functions.pyx:392  grab_pulse_lens() —— 位/符号切片
  * urh/src/urh/signalprocessing/ProtocolAnalyzer.py —— 协议字段解析

本模块只定义抽象基类与注册表，**不强制修改任何现有解码器文件**：
现有解码器通过 ``register_protocol_layer(name, cls)`` 以适配器形式（可选）挂进来。
纯 numpy，不引入 URH 的 PyQt 运行时。
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 三层抽象基类
# ═══════════════════════════════════════════════════════════════════════
class BitLayer(abc.ABC):
    """位层：把（已正交解调的）实数波形判决成 0/1 比特序列。

    对应 URH 的 grab_pulse_lens() / digitize 阶段。
    输入通常是 afp_demod() 输出的实包络/频偏/相位序列。
    """

    #: 协议/调制名（子类覆盖）
    name: str = "bitlayer"

    @abc.abstractmethod
    def demodulate(self, samples: np.ndarray, sample_rate: float) -> np.ndarray:
        """正交解调 IQ 样本 → 实值基带（幅度/频偏/相位）。

        参考 signal_functions.pyx:333 afp_demod()。
        """

    @abc.abstractmethod
    def slice_bits(self, baseband: np.ndarray, samples_per_symbol: int,
                   threshold: float = 0.0) -> np.ndarray:
        """实值基带 → 0/1 比特数组。

        参考 signal_functions.pyx:392 grab_pulse_lens() 的状态判决。
        """


class SymbolLayer(abc.ABC):
    """符号层：把连续比特流按同步字/帧边界切分成符号块/帧。

    对应 URH 的 ProtocolAnalyzer 里的帧同步阶段。
    """

    name: str = "symbollayer"

    #: 同步字（比特列表，子类可覆盖），空表示不做同步字检测
    sync_word: Sequence[int] = ()

    #: 同步字匹配容差（允许错位数）
    sync_tolerance: int = 0

    @abc.abstractmethod
    def find_frames(self, bits: np.ndarray) -> List[np.ndarray]:
        """从比特流中按同步字/帧格式切出一帧帧的比特数组。"""

    def correlate_sync(self, bits: np.ndarray,
                       sync_word: Optional[Sequence[int]] = None) -> List[int]:
        """在比特流里做同步字相关（汉明距离匹配）。

        返回所有匹配位置的起始索引。参考 URH awre 引擎的同步相关。
        """
        sw = list(sync_word if sync_word is not None else self.sync_word)
        if len(sw) == 0 or len(bits) < len(sw):
            return []
        sw_arr = np.asarray(sw, dtype=np.int8)
        n = len(sw_arr)
        hits: List[int] = []
        for i in range(0, len(bits) - n + 1):
            chunk = bits[i:i + n].astype(np.int8)
            mism = int(np.count_nonzero(chunk != sw_arr))
            if mism <= self.sync_tolerance:
                hits.append(i)
        return hits


class ProtocolLayer(abc.ABC):
    """协议层：把一帧比特/字节解析成结构化报文。

    这是现有解码器（ADSB/APRS/AX25/ACARS…）挂进来的位置。
    现有解码器文件不被修改——用 :class:`FunctionProtocolAdapter` 包一层即可。
    """

    name: str = "protocollayer"

    @abc.abstractmethod
    def decode_frame(self, frame: bytes) -> Dict[str, Any]:
        """把一帧（字节）解析成结构化字典。"""


# ═══════════════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class ProtocolStackRegistry:
    """三层组件注册表。按层名归类，支持按名取实例。"""

    bit_layers: Dict[str, type] = field(default_factory=dict)
    symbol_layers: Dict[str, type] = field(default_factory=dict)
    protocol_layers: Dict[type] = field(default_factory=dict)
    # name -> protocol layer instance (for stateless wrappers)
    _proto_instances: Dict[str, ProtocolLayer] = field(default_factory=dict)

    def register_bit_layer(self, cls: type) -> None:
        if not issubclass(cls, BitLayer):
            raise TypeError(f"{cls} 不是 BitLayer 子类")
        self.bit_layers[cls.name] = cls
        logger.info("protocol_stack: 注册 BitLayer '%s'", cls.name)

    def register_symbol_layer(self, cls: type) -> None:
        if not issubclass(cls, SymbolLayer):
            raise TypeError(f"{cls} 不是 SymbolLayer 子类")
        self.symbol_layers[cls.name] = cls
        logger.info("protocol_stack: 注册 SymbolLayer '%s'", cls.name)

    def register_protocol_layer(self, instance: ProtocolLayer) -> None:
        if not isinstance(instance, ProtocolLayer):
            raise TypeError(f"{instance} 不是 ProtocolLayer 实例")
        self._proto_instances[instance.name] = instance
        logger.info("protocol_stack: 注册 ProtocolLayer '%s'", instance.name)

    def get_protocol(self, name: str) -> Optional[ProtocolLayer]:
        return self._proto_instances.get(name)

    def list_protocols(self) -> List[str]:
        return sorted(self._proto_instances.keys())


#: 全局默认注册表（register_protocol_stack_tools 用它）
GLOBAL_STACK = ProtocolStackRegistry()


# ═══════════════════════════════════════════════════════════════════════
# 适配器：把"现有解码器函数"包成 ProtocolLayer（零侵入）
# ═══════════════════════════════════════════════════════════════════════
class FunctionProtocolAdapter(ProtocolLayer):
    """把 ``decode_fn(frame: bytes) -> dict`` 包成 ProtocolLayer。

    这样现有 mbdsdr_ai 解码器（如 ax25.AX25Frame.from_bytes、
    aprs_parser.parse_aprs_frame、adsb_lite）不用改一行代码即可挂入分层框架。
    """

    def __init__(self, name: str, decode_fn: Callable[[bytes], Dict[str, Any]]):
        self.name = name
        self._fn = decode_fn

    def decode_frame(self, frame: bytes) -> Dict[str, Any]:
        try:
            out = self._fn(frame)
            if not isinstance(out, dict):
                out = {"result": out}
            return out
        except Exception as e:  # 单个协议解码失败不影响其它
            return {"error": str(e), "protocol": self.name}


# ═══════════════════════════════════════════════════════════════════════
# 内置：一个通用 OOK/ASK 位层（URH afp_demod + grab_pulse_lens 的 numpy 移植）
# ═══════════════════════════════════════════════════════════════════════
class ASKBitLayer(BitLayer):
    """OOK/ASK 位层。

    解调: result[i] = |c|（包络）   —— signal_functions.pyx:371-372
          elif mod_type == "ASK": result[i] = sqrt(magnitude) / max_magnitude
    切片: 以均值/阈值判决 0/1，按 samples_per_symbol 中点采样。
    """

    name = "ask_ook"

    def demodulate(self, samples: np.ndarray, sample_rate: float) -> np.ndarray:
        mag = np.abs(np.asarray(samples, dtype=complex))
        max_mag = float(np.sqrt(2.0))  # float IQ 归一化包络上限
        return (mag / (max_mag + 1e-12)).astype(np.float32)

    def slice_bits(self, baseband: np.ndarray, samples_per_symbol: int,
                   threshold: float = 0.0) -> np.ndarray:
        if len(baseband) == 0 or samples_per_symbol <= 0:
            return np.zeros(0, dtype=np.uint8)
        # 默认阈值取包络中位数（URH 自动估噪声/中心）
        if threshold == 0.0:
            threshold = float(np.median(baseband))
        n_sym = len(baseband) // samples_per_symbol
        bits = np.zeros(n_sym, dtype=np.uint8)
        half = samples_per_symbol // 2
        for k in range(n_sym):
            # 在符号中点 ±10% 窗内取均值，抗边沿
            lo = k * samples_per_symbol + int(samples_per_symbol * 0.4)
            hi = k * samples_per_symbol + int(samples_per_symbol * 0.6)
            if hi > lo:
                level = float(np.mean(baseband[lo:hi]))
            else:
                level = float(baseband[k * samples_per_symbol + half])
            bits[k] = 1 if level > threshold else 0
        return bits


class FSKBitLayer(BitLayer):
    """2-FSK 位层。

    解调: result[i] = atan2( conj(c[i-1]) * c[i] ) —— signal_functions.pyx:374-376
          即相邻样本相位差分 = 瞬时频偏。
    """

    name = "fsk2"

    def demodulate(self, samples: np.ndarray, sample_rate: float) -> np.ndarray:
        c = np.asarray(samples, dtype=complex)
        if len(c) < 2:
            return np.zeros(len(c), dtype=np.float32)
        # conj(c[i-1]) * c[i] 的辐角 = 相位差分（弧度/样本）
        prod = np.conj(c[:-1]) * c[1:]
        freq = np.angle(prod) * sample_rate / (2 * np.pi)
        return freq.astype(np.float32)

    def slice_bits(self, baseband: np.ndarray, samples_per_symbol: int,
                   threshold: float = 0.0) -> np.ndarray:
        if len(baseband) == 0 or samples_per_symbol <= 0:
            return np.zeros(0, dtype=np.uint8)
        # 双频：中心在 0，按过零判决；默认阈值取 0
        n_sym = len(baseband) // samples_per_symbol
        bits = np.zeros(n_sym, dtype=np.uint8)
        for k in range(n_sym):
            lo = k * samples_per_symbol + int(samples_per_symbol * 0.4)
            hi = k * samples_per_symbol + int(samples_per_symbol * 0.6)
            level = float(np.mean(baseband[lo:hi])) if hi > lo else float(baseband[k * samples_per_symbol])
            bits[k] = 1 if level > threshold else 0
        return bits


# ═══════════════════════════════════════════════════════════════════════
# 内置：一个按固定帧长 + 可选同步字切分的符号层
# ═══════════════════════════════════════════════════════════════════════
class FixedFrameSymbolLayer(SymbolLayer):
    """固定帧长符号层：从同步字位置起，每 frame_len 比特切一帧。"""

    name = "fixed_frame"

    def __init__(self, frame_len: int = 0, sync_word: Sequence[int] = (),
                 sync_tolerance: int = 0):
        self.frame_len = frame_len
        self.sync_word = list(sync_word)
        self.sync_tolerance = sync_tolerance

    def find_frames(self, bits: np.ndarray) -> List[np.ndarray]:
        bits = np.asarray(bits, dtype=np.uint8)
        if self.frame_len <= 0:
            return [bits]
        if len(self.sync_word) > 0:
            starts = self.correlate_sync(bits)
        else:
            starts = list(range(0, len(bits) - self.frame_len + 1, self.frame_len))
        frames = []
        for s in starts:
            if s + self.frame_len <= len(bits):
                frames.append(bits[s:s + self.frame_len].copy())
        return frames


# ═══════════════════════════════════════════════════════════════════════
# 现有解码器适配器（可选注册）—— 不修改原解码器文件
# ═══════════════════════════════════════════════════════════════════════
def _build_existing_protocol_adapters() -> List[ProtocolLayer]:
    """惰性导入现有解码器，包成 ProtocolLayer。任何导入失败都跳过。"""
    adapters: List[ProtocolLayer] = []

    # 1) AX.25 —— ax25.AX25Frame.from_bytes
    try:
        from mbdsdr_ai.ax25 import AX25Frame  # noqa: WPS433
        def _ax25_decode(frame: bytes) -> Dict[str, Any]:
            fr = AX25Frame.from_bytes(frame)
            if fr is None:
                return {"valid": False, "reason": "AX25Frame.from_bytes returned None"}
            return {
                "dest": fr.destination, "source": fr.source,
                "control": hex(fr.control), "pid": hex(fr.pid),
                "info": fr.info.decode("ascii", "replace"),
                "fcs_valid": fr.fcs_valid,
            }
        adapters.append(FunctionProtocolAdapter("ax25", _ax25_decode))
    except Exception as e:  # pragma: no cover
        logger.warning("protocol_stack: 跳过 ax25 适配器: %s", e)

    # 2) APRS —— aprs_parser.parse_aprs_frame(AX25Frame)
    try:
        from mbdsdr_ai.ax25 import AX25Frame  # noqa: WPS433
        from mbdsdr_ai.aprs_parser import parse_aprs_frame  # noqa: WPS433
        def _aprs_decode(frame: bytes) -> Dict[str, Any]:
            fr = AX25Frame.from_bytes(frame)
            if fr is None:
                return {"valid": False, "reason": "bad AX25 frame"}
            return parse_aprs_frame(fr)
        adapters.append(FunctionProtocolAdapter("aprs", _aprs_decode))
    except Exception as e:  # pragma: no cover
        logger.warning("protocol_stack: 跳过 aprs 适配器: %s", e)

    return adapters


# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry 注册入口
# ═══════════════════════════════════════════════════════════════════════
def register_protocol_stack_tools(registry) -> None:
    """把三层框架查询/调用工具注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    # 注册内置位层 / 符号层
    GLOBAL_STACK.register_bit_layer(ASKBitLayer)
    GLOBAL_STACK.register_bit_layer(FSKBitLayer)
    GLOBAL_STACK.register_symbol_layer(FixedFrameSymbolLayer)

    # 注册现有解码器协议适配器（可选，失败自动跳过）
    for ad in _build_existing_protocol_adapters():
        GLOBAL_STACK.register_protocol_layer(ad)

    def _list_protocols(args):
        try:
            data = {
                "protocols": GLOBAL_STACK.list_protocols(),
                "bit_layers": sorted(GLOBAL_STACK.bit_layers.keys()),
                "symbol_layers": sorted(GLOBAL_STACK.symbol_layers.keys()),
                "source": "URH Signal.py:537 auto_detect / signal_functions.pyx:392 grab_pulse_lens",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"列出协议失败: {e}")

    def _decode_protocol(args):
        """对一帧字节调用已注册的协议层解码器。"""
        name = str(args.get("protocol", ""))
        frame_b64 = args.get("frame")  # 字节列表或 hex
        proto = GLOBAL_STACK.get_protocol(name)
        if proto is None:
            return ToolResult(False, f"未注册的协议层: {name}；可用: {GLOBAL_STACK.list_protocols()}")
        try:
            if isinstance(frame_b64, list):
                frame = bytes(int(b) & 0xFF for b in frame_b64)
            elif isinstance(frame_b64, str):
                frame = bytes.fromhex(frame_b64)
            else:
                return ToolResult(False, "frame 必须是字节列表或 hex 字符串")
            out = proto.decode_frame(frame)
            data = {"protocol": name, "decoded": out,
                    "source": "URH ProtocolAnalyzer.py"}
            return ToolResult(True, json.dumps(data, ensure_ascii=False, default=str), data=data)
        except Exception as e:
            return ToolResult(False, f"协议解码失败: {e}")

    def _bits_to_frames(args):
        """用 FixedFrameSymbolLayer 把比特流切帧。"""
        bits = args.get("bits")
        if not isinstance(bits, list):
            return ToolResult(False, "bits 必须是 0/1 列表")
        arr = np.asarray(bits, dtype=np.uint8)
        frame_len = int(args.get("frame_len", 0))
        sync = args.get("sync_word") or []
        tol = int(args.get("sync_tolerance", 0))
        layer = FixedFrameSymbolLayer(frame_len=frame_len, sync_word=sync,
                                      sync_tolerance=tol)
        frames = layer.find_frames(arr)
        data = {"num_frames": len(frames),
                "frames": [f.tolist() for f in frames],
                "source": "signal_functions.pyx:392 grab_pulse_lens"}
        return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)

    registry.register(
        name="protocol_stack_list",
        description=("列出 URH 式三层解码框架中已注册的位层/符号层/协议层。"
                     "协议层即现有解码器（ax25/aprs…）的适配器。"),
        parameters={"type": "object", "properties": {}},
        handler=_list_protocols,
        category="sdr_protocol",
    )
    registry.register(
        name="protocol_stack_decode",
        description=("按协议名对一帧字节做协议层解码（URH 第三层）。"
                     "protocol 取 protocol_stack_list 中的名字，frame 为字节列表或 hex。"),
        parameters={
            "type": "object",
            "properties": {
                "protocol": {"type": "string", "description": "协议层名，如 ax25 / aprs"},
                "frame": {"description": "帧字节（int 列表）或 hex 字符串"},
            },
            "required": ["protocol", "frame"],
        },
        handler=_decode_protocol,
        category="sdr_protocol",
    )
    registry.register(
        name="protocol_stack_split_frames",
        description=("URH 符号层：按固定帧长/同步字把比特流切成一帧帧比特。"),
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "array", "items": {"type": "integer"}},
                "frame_len": {"type": "integer", "default": 0},
                "sync_word": {"type": "array", "items": {"type": "integer"}},
                "sync_tolerance": {"type": "integer", "default": 0},
            },
            "required": ["bits"],
        },
        handler=_bits_to_frames,
        category="sdr_protocol",
    )

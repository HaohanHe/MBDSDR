"""
MBDSDR - 解码器注册表
======================

集中管理新增数字模式解码器，供主调度器按频率/模式自动选择。

设计原则：
  - 懒加载：import 本模块不触发 numpy/scipy，真正调用 decode 时才 import 具体解码器。
  - 可扩展：新增解码器只需调用 register() 或在 _BUILTIN_DECODERS 中加一条。
  - 只读选择：select_by_frequency / get_decoder 均返回描述符，不修改全局状态。

频率段参考（ITU / ICAO 常用业余与航空广播段，不绑定具体台站）：
  - POCSAG 寻呼：VHF 138–174 MHz、UHF 406–470 MHz、900 MHz 段
  - ACARS 航空：VHF 118–137 MHz（常用 131.550 / 131.725 / 131.850）
  - VOR 全向信标：VHF 108–118 MHz（ICAO Annex 10）
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class DecoderDescriptor:
    """一个解码器的完整描述符。"""

    mode: str                          # 模式名，如 "POCSAG"
    description: str                   # 一句话说明
    module: str                        # 懒加载模块路径
    decode_func: str                   # 解码函数名（模块内）
    encode_func: Optional[str] = None  # 编码函数名（仅测试/合成用）
    freq_bands_mhz: List[Tuple[float, float]] = field(default_factory=list)
    baud_rates: List[int] = field(default_factory=list)
    modulation: str = ""               # 调制方式说明

    def _load(self):
        """懒加载模块，返回 (decode_callable, encode_callable_or_None)。"""
        mod = importlib.import_module(self.module)
        decode = getattr(mod, self.decode_func)
        encode = getattr(mod, self.encode_func) if self.encode_func else None
        return decode, encode

    @property
    def decode(self) -> Callable[..., Any]:
        """获取解码可调用对象（首次访问时 import）。"""
        fn, _ = self._load()
        return fn

    @property
    def encode(self) -> Optional[Callable[..., Any]]:
        """获取编码可调用对象（仅合成/测试用）。"""
        _, fn = self._load()
        return fn


# ─────────────────────────────────────────────────────────────────────
# 内置解码器表
# ─────────────────────────────────────────────────────────────────────

_BUILTIN_DECODERS: Dict[str, DecoderDescriptor] = {
    "POCSAG": DecoderDescriptor(
        mode="POCSAG",
        description="POCSAG 寻呼解码，支持 512/1200/2400 bps，BCH(31,21) 纠错",
        module="mbdsdr_ai.pocsag_decoder",
        decode_func="pocsag_decode",
        encode_func="pocsag_encode",
        freq_bands_mhz=[(138.0, 174.0), (406.0, 470.0), (900.0, 960.0)],
        baud_rates=[512, 1200, 2400],
        modulation="FSK (NRZ, mark/space)",
    ),
    "ACARS": DecoderDescriptor(
        mode="ACARS",
        description="ACARS 航空报文解码，2400 bps MSK/FSK，CRC-16-CCITT 块校验",
        module="mbdsdr_ai.acars_protocol",
        decode_func="acars_decode",
        encode_func="acars_encode",
        freq_bands_mhz=[(118.0, 137.0)],
        baud_rates=[1200, 2400],
        modulation="MSK/FSK (mark=2400Hz, space=1200Hz)",
    ),
    "VOR": DecoderDescriptor(
        mode="VOR",
        description="VOR 全向信标解码，30Hz 基准/可变相位测向 + 莫尔斯识别",
        module="mbdsdr_ai.vor_decoder",
        decode_func="vor_decode",
        encode_func="vor_encode",
        freq_bands_mhz=[(108.0, 118.0)],
        baud_rates=[],
        modulation="AM (30Hz FM 副载波 9960Hz)",
    ),
}


# ─────────────────────────────────────────────────────────────────────
# 运行时注册表（可动态追加）
# ─────────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, DecoderDescriptor] = dict(_BUILTIN_DECODERS)


def register(descriptor: DecoderDescriptor) -> None:
    """注册一个新的解码器描述符（覆盖同名模式）。"""
    _REGISTRY[descriptor.mode.upper()] = descriptor


def get_decoder(mode: str) -> Optional[DecoderDescriptor]:
    """按模式名获取解码器描述符，不存在返回 None。"""
    return _REGISTRY.get(mode.upper())


def list_decoders() -> List[DecoderDescriptor]:
    """返回所有已注册解码器描述符列表。"""
    return list(_REGISTRY.values())


def select_by_frequency(freq_mhz: float) -> List[DecoderDescriptor]:
    """
    按载波频率筛选可用解码器。

    参数:
        freq_mhz: 载波频率（MHz）

    返回:
        频率落在该解码器工作频段内的所有描述符，按模式名字典序。
    """
    matches = []
    for desc in _REGISTRY.values():
        for lo, hi in desc.freq_bands_mhz:
            if lo <= freq_mhz <= hi:
                matches.append(desc)
                break
    return sorted(matches, key=lambda d: d.mode)


def select_decoder(freq_mhz: Optional[float] = None,
                   mode: Optional[str] = None) -> Optional[DecoderDescriptor]:
    """
    主调度器入口：按模式优先、频率兜底选择解码器。

    选择逻辑：
      1. 若指定 mode 且已注册 → 直接返回该解码器
      2. 否则若指定 freq_mhz → 返回频段匹配的第一个解码器
      3. 都不满足 → 返回 None
    """
    if mode:
        desc = get_decoder(mode)
        if desc is not None:
            return desc
    if freq_mhz is not None:
        candidates = select_by_frequency(freq_mhz)
        if candidates:
            return candidates[0]
    return None


def decode_audio(mode: str, audio, sample_rate: float,
                 **kwargs) -> Any:
    """
    便捷入口：按模式名直接解码音频。

    参数:
        mode:        模式名（"POCSAG" / "ACARS" / "VOR"）
        audio:       一维实数采样数组
        sample_rate: 采样率 (Hz)
        **kwargs:    透传给具体解码器的额外参数（如 baud）

    返回:
        解码器的原生返回值（列表或 dict）

    异常:
        ValueError: 模式未注册
    """
    desc = get_decoder(mode)
    if desc is None:
        raise ValueError(f"未注册的解码器模式: {mode!r}，"
                         f"可用: {sorted(_REGISTRY.keys())}")
    return desc.decode(audio, sample_rate, **kwargs)

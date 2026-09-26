"""MBDSDR 卫星处理包。

移植自 SatDump (repos/SatDump) 的卫星信号处理链：
  * tracker   — SGP4 传播 / AOS-LOS 过境预测 / 实时多普勒（tracking/, passes/）
  * decoders  — NOAA APT 等气象解码（plugins/analog_support/noaa_apt/）
  * products  — 多通道图像产品 / 假彩色合成 / 地理元数据（products/）

笔记见 docs/learn/satdump.md。
"""
from .tracker import (
    GroundStation,
    SatelliteTracker,
    PassPredictor,
    auto_detect_satellite,
    tune_command,
    DOWNLINK_FREQUENCIES,
)
from .decoders import (
    NOAAAPTDecoder,
    APTDecodeResult,
    load_wav_mono,
    auto_detect_decoder,
)
from .products import ImageProduct

__all__ = [
    "GroundStation",
    "SatelliteTracker",
    "PassPredictor",
    "auto_detect_satellite",
    "tune_command",
    "DOWNLINK_FREQUENCIES",
    "NOAAAPTDecoder",
    "APTDecodeResult",
    "load_wav_mono",
    "auto_detect_decoder",
    "ImageProduct",
]

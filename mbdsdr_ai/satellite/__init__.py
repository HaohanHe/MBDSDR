# SPDX-License-Identifier: MIT
"""MBDSDR 卫星处理包。

依据公开标准与数据手册独立实现的卫星信号处理链：
  * tracker   — SGP4 传播 / AOS-LOS 过境预测 / 实时多普勒
                （SGP4：Spacetrack Report #3；多普勒：-v_r/c·f_c）
  * decoders  — NOAA APT 等气象解码（NOAA APT 公开格式）
  * products  — 多通道图像产品 / 假彩色合成 / 地理元数据

SatDump（https://www.satdump.org/）等开源项目仅作技术参考与致谢，本仓未包含其源代码。
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

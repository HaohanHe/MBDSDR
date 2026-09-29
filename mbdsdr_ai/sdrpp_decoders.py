# SPDX-License-Identifier: MIT
"""常用广播/卫星/寻呼解码器封装层（独立实现）。

把本仓各 lite 解码器封装成统一接口并注册进 ToolRegistry（AI 可调用）。

  RDS 解码器：1187.5 bit/s BPSK @ 57kHz 副载波，差分解码；
      实现复用 mbdsdr_ai/rds_lite.py（EN 300 401 子集）。

  气象卫星 APT：2400Hz AM 副载波 / 视频率 4160Hz / 行 0.5s；
      实现复用 mbdsdr_ai/noaa_apt_lite.py。

  POCSAG 寻呼：FSK ±4.5kHz 频偏，帧同步 0x7CD215D8 / 每批 16×32bit；
      实现复用本仓 POCSAG 解码器。

  ADS-B：1090MHz / 8µs preamble / PPM / CRC-24；
      实现复用 mbdsdr_ai/adsb_lite.py。

以上参数均为公开标准事实。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
import numpy as np


# ======================================================================
# 广播/卫星/寻呼解码器常量
# ======================================================================
class SDRPPConstants:
    """集中放各解码器常量，便于单测。"""
    # RDS
    RDS_BIT_RATE = 1187.5
    RDS_SUBCARRIER_HZ = 57_000.0
    RDS_INTERNAL_FS = 5_000.0
    RDS_BANDPASS = (0.0, 2_375.0)
    # APT
    APT_SUBCARRIER_HZ = 2_400.0
    APT_VIDEO_RATE = 4_160.0
    APT_LINE_SECONDS = 0.5
    # POCSAG
    POCSAG_FSK_SHIFT_HZ = 4_500.0   #
    POCSAG_FRAME_SYNC = 0x7CD215D8
    POCSAG_BATCH_CODEWORDS = 16
    POCSAG_DEFAULT_BAUD = 1200.0


# ======================================================================
# 解码器封装（薄壳，真正算法复用本仓 lite 实现）
# ======================================================================
class RDSDecoder:
    """FM 广播 RDS 解码器。

DSP 链：
      FastAGC -> Costas<2> -> bandpass(0,2375) -> Costas<2> -> MM 时钟恢复 -> 差分解码。
    本壳把鉴频后的 MPX 基带交给 rds_lite.decode_rds（EN50067 块解码子集）。
    """

    def __init__(self, region: str = "eu"):

        self.region = region
        self.bit_rate = SDRPPConstants.RDS_BIT_RATE

    def decode(self, mpx: np.ndarray, sample_rate: float,
               min_groups: int = 2) -> Dict[str, Any]:
        from .rds_lite import decode_rds
        try:
            return decode_rds(np.asarray(mpx), float(sample_rate), min_groups)
        except Exception as e:
            return {"rds_present": False, "error": str(e)}


class APTScanDecoder:
    """NOAA POES APT 云图扫描器。

    APT = 137MHz 宽带 FM 下行，2400Hz AM 副载波携带亮度；行长 0.5s。

    """

    def decode(self, audio: np.ndarray, sample_rate: float,
               polarity: int = 1) -> Dict[str, Any]:
        from .noaa_apt_lite import decode_apt
        try:
            return decode_apt(np.asarray(audio, dtype=np.float64),
                              float(sample_rate), polarity=polarity)
        except Exception as e:
            return {"ok": False, "error": str(e)}


class ADSBDecoder:
    """1090MHz ADS-B / Mode S 解码器。"""

    def decode(self, iq: np.ndarray, sample_rate: float,
               threshold_sigma: float = 4.0) -> Dict[str, Any]:
        from .adsb_lite import decode_adsb
        try:
            return decode_adsb(np.asarray(iq), float(sample_rate),
                               threshold_sigma=threshold_sigma)
        except Exception as e:
            return {"frames": [], "aircraft": {}, "error": str(e)}


class POCSAGDecoder:
    """POCSAG 寻呼解码器（最小同步子集）。


      FSK 鉴频频偏 ±4.5kHz；10-tap 矩形平均；MM 时钟恢复 decim=sr/baud；
      帧同步码字 0x7CD215D8，允许 ≤4 bit 汉明距离；每批 16 个 32bit 码字。
    本壳只做同步检测与批成帧。
    """

    SYNC = SDRPPConstants.POCSAG_FRAME_SYNC
    SYNC_DIST = 4

    def __init__(self, baudrate: float = SDRPPConstants.POCSAG_DEFAULT_BAUD):
        self.baudrate = baudrate

    def _hamming(self, a: int, b: int) -> int:
        return bin(a ^ b).count("1")

    def sync_search(self, bits: np.ndarray) -> List[int]:
        """在比特流里找帧同步码字位置（容差 SYNC_DIST bit）。

。
        """
        positions = []
        sr = 0
        for i, b in enumerate(bits[:512]):
            sr = ((sr << 1) | int(b)) & 0xFFFFFFFF
            if self._hamming(sr, self.SYNC) <= self.SYNC_DIST:
                positions.append(i - 31)
        return positions


# ======================================================================
#  注册进 ToolRegistry
# ======================================================================
def register_sdrpp_decoders(registry) -> None:
    """把各解码器注册成 AI 可调用工具。



    """
    rds = RDSDecoder()
    apt = APTScanDecoder()
    adsb = ADSBDecoder()
    pocsag = POCSAGDecoder()

    registry.register(
        name="sdrpp_decode_rds",
        description="RDS 解码：从 FM 广播复合基带解出 PI 码/电台名(PS)/节目类型。",
        parameters={
            "type": "object",
            "properties": {
                "mpx_file": {"type": "string", "description": "鉴频后 MPX 基带 .npy 文件路径"},
                "sample_rate": {"type": "number", "description": "MPX 采样率 Hz", "default": 250000},
            },
            "required": ["mpx_file"],
        },
        handler=_wrap_file_handler(lambda mpx, sr: rds.decode(mpx, sr)),
        category="sdrpp_decoder",
    )

    registry.register(
        name="sdrpp_decode_apt",
        description="APT 气象云图解码：从 NOAA 卫星音频解出 A/B 灰度图。",
        parameters={
            "type": "object",
            "properties": {
                "audio_file": {"type": "string", "description": "APT 音频 .npy/.wav 路径"},
                "sample_rate": {"type": "number", "default": 4160},
            },
            "required": ["audio_file"],
        },
        handler=_wrap_file_handler(lambda audio, sr: apt.decode(audio, sr)),
        category="sdrpp_decoder",
    )

    registry.register(
        name="sdrpp_decode_adsb",
        description="ADS-B 解码：从 1090MHz 复基带 IQ 解出 Mode S 帧/飞机呼号。",
        parameters={
            "type": "object",
            "properties": {
                "iq_file": {"type": "string", "description": "1090MHz IQ .npy 路径"},
                "sample_rate": {"type": "number", "default": 2000000},
            },
            "required": ["iq_file"],
        },
        handler=_wrap_file_handler(lambda iq, sr: adsb.decode(iq, sr)),
        category="sdrpp_decoder",
    )

    registry.register(
        name="sdrpp_pocsag_sync",
        description="POCSAG 同步检测：在比特流里找帧同步码字 0x7CD215D8。",
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "array", "items": {"type": "integer"},
                         "description": "0/1 比特数组"},
            },
            "required": ["bits"],
        },
        handler=lambda args: {"sync_positions": pocsag.sync_search(
            np.array(args.get("bits", []), dtype=np.uint8))},
        category="sdrpp_decoder",
    )


def _wrap_file_handler(fn: Callable[[np.ndarray, float], Dict]) -> Callable:
    """把 (ndarray, sr)->dict 的解码器包成 ToolRegistry handler：从磁盘 .npy 读输入。"""
    def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
        path = next((args.get(k) for k in ("mpx_file", "audio_file", "iq_file")
                     if args.get(k)), None)
        if not path:
            return {"error": "missing input file"}
        try:
            data = np.load(path, allow_pickle=True)
        except Exception as e:
            return {"error": f"load {path} failed: {e}"}
        sr = float(args.get("sample_rate", 0) or 0)
        return fn(data, sr)
    return _handler


if __name__ == "__main__":
    # 冒烟：POCSAG 同步搜索
    bits = np.random.randint(0, 2, 512).astype(np.uint8)
    p = POCSAGDecoder()
    print("pocsag sync positions on random bits (should be rare):",
          len(p.sync_search(bits)))
    print("constants: RDS bps", SDRPPConstants.RDS_BIT_RATE,
          "POCSAG sync", hex(SDRPPConstants.POCSAG_FRAME_SYNC))

"""
OpenWebRX web SDR 服务器架构移植（纯 numpy，无 pycsdr/csdr 运行时）
=================================================================
本模块把 OpenWebRX (repos/openwebrx) 的 web SDR 服务器核心翻译成 numpy：

  1. 频谱流水线（FFT 平均 / block 尺寸推导）
     —— 来源: csdr/chain/fft.py:25-97 (FftChain/FftAverager)
  2. waterfall 色彩映射
     —— 来源: owrx/waterfall.py:13-302 (GoogleTurbo/Teejeez/Ha7ilm)
  3. SDR 设备抽象层 + 客户端带宽自适应
     —— 来源: owrx/source/__init__.py (SdrSource ABC)
              owrx/source/rtl_sdr.py (getSampleRateRanges)
              owrx/soapy.py (SoapySettings.parse/encode)

流水线拓扑（FftChain，fft.py:37-41）：
    复IQ → Fft(size) → FftAverager(LogPower/LogAveragePower, add_db=-70)
         → FftSwap(左右交换) → [可选 FftAdpcm 压缩] → 推给浏览器
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  1. waterfall 调色板 —— 移植自 owrx/waterfall.py
# ═══════════════════════════════════════════════════════════════════════
#: Teejeez 8 段色标。来源: waterfall.py:279
OWRX_TEEJEEZ_STOPS = [
    0x000000, 0x0000FF, 0x00FFFF, 0x00FF00,
    0xFFFF00, 0xFF0000, 0xFF00FF, 0xFFFFFF,
]
#: HA7ILM 8 段色标。来源: waterfall.py:284
OWRX_HA7ILM_STOPS = [
    0x000000, 0x2E6893, 0x69A5D0, 0x214B69,
    0x9DC4E0, 0xFFF775, 0xFF8A8A, 0xB20000,
]
#: GoogleTurbo 起止色（默认主题，waterfall.py:13-275 是 256 项长表；
#: 这里取首/尾端点，配合线性插值即可复现深蓝→青→黄的 turbo 走势）。
OWRX_GOOGLETURBO_STOPS = [0x30123B, 0x4587FC, 0x4391FF]


def _hex_to_rgb(h: int) -> np.ndarray:
    return np.array([(h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF], dtype=np.float64)


def owx_waterfall_colormap(palette: str = "teejeez", n: int = 256) -> np.ndarray:
    """把离散色标 stops 线性插值成 n×3 uint8 调色板。

    移植 Waterfall.getColors() 的用途（waterfall.py:10-11）：浏览器按功率索引取色。
    palette ∈ {googleturbo, teejeez, ha7ilm}。
    """
    p = (palette or "teejeez").lower()
    if p in ("teejeez", "teejee", "original"):
        stops = OWRX_TEEJEEZ_STOPS
    elif p in ("ha7ilm",):
        stops = OWRX_HA7ILM_STOPS
    elif p in ("googleturbo", "google_turbo", "default"):
        stops = OWRX_GOOGLETURBO_STOPS
    else:
        raise ValueError(f"未知 waterfall 调色板 {p}")

    rgb_stops = np.array([_hex_to_rgb(s) for s in stops], dtype=np.float64)
    m = len(stops)
    # stops 均匀分布在 [0,1]，逐通道线性插值
    x_src = np.linspace(0.0, 1.0, m)
    x_dst = np.linspace(0.0, 1.0, n)
    out = np.zeros((n, 3), dtype=np.float64)
    for c in range(3):
        out[:, c] = np.interp(x_dst, x_src, rgb_stops[:, c])
    return np.clip(out, 0, 255).astype(np.uint8)


def owx_apply_waterfall(power_db: np.ndarray, palette: str = "teejeez",
                        min_db: float = -120.0, max_db: float = -20.0) -> np.ndarray:
    """把一行 dB 功率谱映射成 RGB 像素行（waterfall 一帧的一行）。

    先 [min_db,max_db] 归一化到 [0,1]，再查调色板。
    """
    cmap = owx_waterfall_colormap(palette, 256)
    norm = (np.asarray(power_db, dtype=np.float64) - min_db) / (max_db - min_db)
    idx = np.clip((norm * 255.0).astype(int), 0, 255)
    return cmap[idx]


# ═══════════════════════════════════════════════════════════════════════
#  2. FFT 平均 / block 尺寸推导 —— 移植自 csdr/chain/fft.py
# ═══════════════════════════════════════════════════════════════════════
#: 功率谱偏移量。来源 fft.py:20-22  LogPower/LogAveragePower(add_db=-70)
OWRX_FFT_ADD_DB = -70.0


def owx_plan_fft(samp_rate: float, fft_size: int, fft_fps: float,
                 v_overlap_factor: float = 0.0) -> Dict:
    """复现 FftChain._updateParameters (fft.py:75-85) 的平均/块尺寸推导。

        fftAverages = round(samp_rate / fft_size / fft_fps / (1 - vOverlapFactor))
        blockSize   = samp_rate / fft_fps / fftAverages   (fftAverages>0)
                    = samp_rate / fft_fps                  (fftAverages==0)
    fftAverages==0 时用 LogPower（无平均）；否则用 LogAveragePower。
    """
    if v_overlap_factor > 0:
        fft_averages = int(round(
            1.0 * samp_rate / fft_size / fft_fps / (1.0 - v_overlap_factor)))
    else:
        fft_averages = 0
    if fft_averages == 0:
        block_size = samp_rate / fft_fps
        averager = "LogPower"
    else:
        block_size = samp_rate / fft_fps / fft_averages
        averager = "LogAveragePower"
    return {
        "samp_rate": samp_rate, "fft_size": fft_size, "fft_fps": fft_fps,
        "v_overlap_factor": v_overlap_factor,
        "fft_averages": fft_averages,
        "block_size_samples": block_size,
        "averager": averager,
        "add_db": OWRX_FFT_ADD_DB,
        "source": "openwebrx csdr/chain/fft.py:75-85",
    }


class OwxFftAverager:
    """LogAveragePower 的 numpy 在线移植（指数/滑动平均功率谱）。

    真实 pycsdr 用 C++ 实现滑动对数功率平均；这里用一阶递归平均近似：
        avg = (1-1/N)*avg + (1/N)*new
    N=fft_averages。N==0 时直通单帧 LogPower。
    """
    def __init__(self, fft_size: int, fft_averages: int, add_db: float = OWRX_FFT_ADD_DB):
        self.fft_size = int(fft_size)
        self.fft_averages = max(0, int(fft_averages))
        self.add_db = add_db
        self._avg = np.zeros(fft_size, dtype=np.float64)
        self._n = 0

    def process(self, iq: np.ndarray) -> np.ndarray:
        iq = np.asarray(iq, dtype=np.complex128)
        X = np.fft.fft(iq)
        X = np.fft.fftshift(X)              # 对齐 FftSwap (fft.py:36)
        power = np.abs(X) ** 2
        if self.fft_averages == 0:
            out = 10.0 * np.log10(1e-12 + power) + self.add_db
        else:
            alpha = 1.0 / self.fft_averages
            self._avg = (1.0 - alpha) * self._avg + alpha * power
            out = 10.0 * np.log10(1e-12 + self._avg) + self.add_db
        self._n += 1
        return out.astype(np.float32)


# ═══════════════════════════════════════════════════════════════════════
#  3. 设备抽象层 —— 移植自 owrx/source/rtl_sdr.py / owrx/soapy.py
# ═══════════════════════════════════════════════════════════════════════
#: 每台设备的采样率范围。来源: rtl_sdr.py  RtlSdrDeviceDescription.getSampleRateRanges
#:   return [Range(250000, 3200000)]
OWRX_DEVICE_SAMPLE_RATES = {
    "rtl_sdr":      {"min": 250_000.0, "max": 3_200_000.0},   # rtl_sdr.py
    "airspy":       {"min": 2_500_000.0, "max": 10_000_000.0},
    "hackrf":       {"min": 2_000_000.0, "max": 20_000_000.0},
    "sdrplay":      {"min": 2_000_000.0, "max": 10_000_000.0},
}


def owx_client_bandwidth_adapt(samp_rate: float, device: str = "rtl_sdr") -> Dict:
    """客户端带宽自适应：根据设备采样率范围钳位请求带宽。

    对应 connection.py:202  srh = samp_rate/2（单边半采样率）。
    无真实硬件时返回钳位后的可用带宽，标注[模拟]。
    """
    r = OWRX_DEVICE_SAMPLE_RATES.get(device)
    if r is None:
        return {"device": device, "available": False, "reason": "未知设备类型"}
    clamped = min(max(samp_rate, r["min"]), r["max"])
    return {
        "device": device, "requested_samp_rate": samp_rate,
        "clamped_samp_rate": clamped,
        "usable_half_bandwidth": clamped / 2.0,     # connection.py:202
        "range": r,
        "connected": False, "note": "[模拟] 未连接真实硬件",
        "source": "openwebrx rtl_sdr.py getSampleRateRanges, connection.py:202",
    }


def owx_soapy_settings_parse(dstr: str) -> List:
    """移植 SoapySettings.parse (soapy.py)：'key=val,key2=val2' → list。"""
    out = []
    for c in dstr.split(","):
        kv = c.split("=", 1)
        if len(kv) < 2:
            out.append(c)
        else:
            out.append({kv[0]: kv[1]})
    return out


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_openwebrx_tools(registry) -> None:
    """把 OpenWebRX 频谱流水线 / waterfall / 设备抽象 注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _plan_fft(args):
        samp_rate = float(args.get("samp_rate", 1_920_000))
        fft_size = int(args.get("fft_size", 8192))
        fft_fps = float(args.get("fft_fps", 10.0))
        voverlap = float(args.get("v_overlap_factor", 0.0))
        try:
            data = owx_plan_fft(samp_rate, fft_size, fft_fps, voverlap)
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"FFT 规划失败: {e}")

    def _waterfall_colormap(args):
        palette = str(args.get("palette", "teejeez"))
        n = int(args.get("n", 256))
        try:
            cmap = owx_waterfall_colormap(palette, n)
            data = {
                "palette": palette, "entries": n,
                "rgb_first": cmap[0].tolist(),
                "rgb_mid": cmap[n // 2].tolist(),
                "rgb_last": cmap[-1].tolist(),
                "source": "openwebrx waterfall.py:277-302",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"调色板失败: {e}")

    def _fft_average(args):
        iq = args.get("iq")
        if not isinstance(iq, list):
            return ToolResult(False, "iq 必须是复数采样列表")
        try:
            arr = np.array(iq, dtype=complex)
            plan = owx_plan_fft(float(args.get("samp_rate", 1_920_000)),
                                len(arr), float(args.get("fft_fps", 10.0)),
                                float(args.get("v_overlap_factor", 0.0)))
            avg = OwxFftAverager(len(arr), plan["fft_averages"])
            power = avg.process(arr)
            data = {
                "bins": int(len(power)),
                "power_db_min": float(np.min(power)),
                "power_db_max": float(np.max(power)),
                "averager": plan["averager"],
                "fft_averages": plan["fft_averages"],
                "source": "openwebrx csdr/chain/fft.py:18-22",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"FFT 平均失败: {e}")

    def _device_adapt(args):
        device = str(args.get("device", "rtl_sdr"))
        sr = float(args.get("samp_rate", 2_400_000))
        try:
            data = owx_client_bandwidth_adapt(sr, device)
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"设备适配失败: {e}")

    registry.register(
        name="owrx_plan_fft",
        description=("OpenWebRX FFT 流水线参数推导（FftChain._updateParameters）："
                     "由 samp_rate/fft_size/fft_fps/v_overlap 推 fft_averages 与 block_size，"
                     "决定用 LogPower 还是 LogAveragePower（add_db=-70）。"),
        parameters={
            "type": "object",
            "properties": {
                "samp_rate": {"type": "number", "default": 1920000},
                "fft_size": {"type": "integer", "default": 8192},
                "fft_fps": {"type": "number", "default": 10.0},
                "v_overlap_factor": {"type": "number", "default": 0.0},
            },
            "required": [],
        },
        handler=_plan_fft,
        category="sdr_dsp",
    )

    registry.register(
        name="owrx_waterfall_colormap",
        description=("OpenWebRX waterfall 调色板：teejeez(黑→蓝→青→绿→黄→红→品红→白)/"
                     "ha7ilm/googleturbo，线性插值成 n×3 RGB。移植 waterfall.py:277-302。"),
        parameters={
            "type": "object",
            "properties": {
                "palette": {"type": "string", "enum": ["teejeez", "ha7ilm", "googleturbo"]},
                "n": {"type": "integer", "default": 256},
            },
            "required": [],
        },
        handler=_waterfall_colormap,
        category="sdr_ui",
    )

    registry.register(
        name="owrx_fft_average",
        description=("OpenWebRX 在线 FFT 功率平均：fftshift+|X|^2→10log10+add_db(-70)，"
                     "按 fft_averages 做一阶递归平均。移植 csdr/chain/fft.py:18-22。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array", "items": {"type": "number"}},
                "samp_rate": {"type": "number", "default": 1920000},
                "fft_fps": {"type": "number", "default": 10.0},
                "v_overlap_factor": {"type": "number", "default": 0.0},
            },
            "required": ["iq"],
        },
        handler=_fft_average,
        category="sdr_dsp",
    )

    registry.register(
        name="owrx_device_bandwidth_adapt",
        description=("OpenWebRX 设备抽象+客户端带宽自适应：按设备采样率范围"
                     "(rtl_sdr 250k..3.2M) 钳位请求带宽，返回可用半带宽=samp_rate/2。"),
        parameters={
            "type": "object",
            "properties": {
                "device": {"type": "string", "enum": list(OWRX_DEVICE_SAMPLE_RATES)},
                "samp_rate": {"type": "number", "default": 2400000},
            },
            "required": [],
        },
        handler=_device_adapt,
        category="sdr_ui",
    )


if __name__ == "__main__":
    p = owx_plan_fft(1_920_000, 8192, 10.0, 0.5)
    print("FFT plan:", p)
    cmap = owx_waterfall_colormap("teejeez", 8)
    print("teejeez 8-entry:", cmap.tolist())
    print(owx_client_bandwidth_adapt(5_000_000, "rtl_sdr"))

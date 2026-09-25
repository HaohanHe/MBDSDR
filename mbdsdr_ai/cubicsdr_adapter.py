"""
CubicSDR 多VFO / 视觉调谐 / waterfall 架构移植（纯 numpy，无 wx/C++ 运行时）
=========================================================================
本模块把 CubicSDR (repos/CubicSDR) 的三件事翻译成 Python：

  1. 多 VFO 状态机（DemodulatorMgr）
     —— 来源: src/demod/DemodulatorMgr.h:14-92
  2. waterfall 调色板（ColorTheme）
     —— 来源: src/visual/ColorTheme.h:14-22, ColorTheme.cpp:47-111
  3. 视觉调谐 drag-tune 参数模型（TuningCanvas）
     —— 来源: src/visual/TuningCanvas.cpp:173-289 (StepTuner/OnIdle)

调谐模型（TuningCanvas.cpp:275-285）：
    dragAccum += 5.0 * deltaMouseX（水平像素）；
    每累计 ±1.0 像素 → StepTuner(digit, direction) 一次；
    StepTuner 步进量 amount = ±10^digit。
    PPM 钳位 ±2000（TuningCanvas.cpp:259-265）；
    带宽钳位 CHANNELIZER_RATE_MAX=500000（CubicSDRDefs.h:63）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════════════
#: 通道化器最大带宽。来源: CubicSDRDefs.h:63
CUBIC_CHANNELIZER_RATE_MAX = 500_000
#: PPM 钳位范围。来源: TuningCanvas.cpp:259-265
CUBIC_PPM_LIMIT = 2000
#: drag-tune 灵敏度：每 1 像素累计步进次数。来源: TuningCanvas.cpp:275
CUBIC_DRAG_PIXELS_PER_STEP = 5.0
#: 数字调谐器位数。来源: TuningCanvas.cpp:302,309,317
CUBIC_FREQ_DIGITS = 11
CUBIC_BW_DIGITS = 7
CUBIC_CENTER_DIGITS = 11


# ═══════════════════════════════════════════════════════════════════════
#  1. waterfall 调色板 —— 移植自 ColorTheme.cpp
# ═══════════════════════════════════════════════════════════════════════
#: ColorTheme.cpp:76-80  JET 主题色标：黑→蓝→绿→黄→橙红
CUBIC_JET_STOPS = [(0, 0, 0), (0, 0, 1.0), (0, 1.0, 0), (1.0, 1.0, 0), (1.0, 0.2, 0.0)]
#: ColorTheme.cpp:92-95  SHARP 主题（绿色系）
CUBIC_SHARP_STOPS = [
    (5 / 255, 45 / 255, 10 / 255),
    (30 / 255, 150 / 255, 40 / 255),
    (40 / 255, 240 / 255, 60 / 255),
    (250 / 255, 250 / 255, 250 / 255),
]
#: 状态色。来源 ColorTheme.cpp:51-53
CUBIC_STATE_COLORS = {
    "new": (0, 1, 0),       # waterfallNew
    "hover": (1, 1, 0),     # waterfallHover
    "destroy": (1, 0, 0),   # waterfallDestroy
}


def cubic_waterfall_colormap(theme: str = "jet", n: int = 256) -> np.ndarray:
    """把 CubicSDR 主题色标插值成 n×3 uint8 调色板。

    theme ∈ {jet, sharp, bw}。默认(turbo)在新版里用 turbo_srgb，这里用经典 JET 代表。
    """
    t = (theme or "jet").lower()
    if t == "jet":
        stops = CUBIC_JET_STOPS
    elif t == "sharp":
        stops = CUBIC_SHARP_STOPS
    elif t == "bw":
        stops = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
    else:
        raise ValueError(f"未知 CubicSDR 主题 {theme}")
    arr = np.array(stops, dtype=np.float64)
    x_src = np.linspace(0.0, 1.0, len(stops))
    x_dst = np.linspace(0.0, 1.0, n)
    out = np.zeros((n, 3), dtype=np.float64)
    for c in range(3):
        out[:, c] = np.interp(x_dst, x_src, arr[:, c])
    return (np.clip(out, 0, 1) * 255.0).astype(np.uint8)


# ═══════════════════════════════════════════════════════════════════════
#  2. 多 VFO 状态机 —— 移植自 DemodulatorMgr
# ═══════════════════════════════════════════════════════════════════════
class CubicVFO:
    """单个 VFO（=一个 DemodulatorInstance）。

    来源 DemodulatorInstance.h：每个 VFO 有自己的 frequency/bandwidth/type/tracking。
    """
    def __init__(self, vfo_id: int, frequency: int, bandwidth: int = 3000,
                 mod_type: str = "am"):
        self.id = vfo_id
        self.frequency = int(frequency)
        self.bandwidth = int(bandwidth)
        self.mod_type = mod_type
        self.tracking = True
        self.follow = True


class CubicVFOManager:
    """DemodulatorMgr 的 Python 镜像（DemodulatorMgr.h:14-92）。

    维护 VFO 列表 + 三个活动指针：
      activeContextModem / currentModem / activeVisualDemodulator。
    """
    def __init__(self, center_frequency: int = 100_000_000, sample_rate: int = 2_000_000):
        self.center_frequency = int(center_frequency)
        self.sample_rate = int(sample_rate)
        self.vfos: List[CubicVFO] = []
        self._next_id = 0
        self.current_modem: Optional[CubicVFO] = None
        self.active_visual: Optional[CubicVFO] = None

    def add_vfo(self, frequency: int, bandwidth: int = 3000,
                mod_type: str = "am") -> CubicVFO:
        vfo = CubicVFO(self._next_id, frequency, bandwidth, mod_type)
        self._next_id += 1
        self.vfos.append(vfo)
        self.set_active(vfo)
        return vfo

    def set_active(self, vfo: Optional[CubicVFO]) -> None:
        """setActiveDemodulator（DemodulatorMgr.h:36）。"""
        self.current_modem = vfo
        self.active_visual = vfo

    def select_by_frequency(self, frequency: int, bandwidth: int = 0) -> Optional[CubicVFO]:
        """getDemodulatorsAt(freq, bw)（DemodulatorMgr.h:25）：选覆盖该频率的 VFO。"""
        for v in self.vfos:
            if abs(v.frequency - frequency) <= (v.bandwidth / 2):
                self.set_active(v)
                return v
        return None

    def tune_current(self, amount: int) -> Dict:
        """对当前 VFO 调频；若 VFO 超出采样率半带则平移中心频率。

        对齐 TuningCanvas.cpp:197-199：
            if (sampleRate/2 < diff) setFrequency(demod_freq)
        """
        if self.current_modem is None:
            return {"error": "无活动 VFO"}
        self.current_modem.frequency += int(amount)
        diff = abs(self.sample_rate // 2 - abs(self.current_modem.frequency - self.center_frequency))
        if abs(self.current_modem.frequency - self.center_frequency) > self.sample_rate // 2:
            self.center_frequency = self.current_modem.frequency
        return {
            "vfo_id": self.current_modem.id,
            "frequency": self.current_modem.frequency,
            "center_frequency": self.center_frequency,
            "sample_rate_half": self.sample_rate // 2,
        }


# ═══════════════════════════════════════════════════════════════════════
#  3. drag-tune 数字步进模型 —— 移植自 TuningCanvas.StepTuner
# ═══════════════════════════════════════════════════════════════════════
def cubic_step_tuner(value: int, digit: int, direction: int,
                     prevent_carry: bool = False, zero_out: bool = False) -> int:
    """TuningCanvas.StepTuner (TuningCanvas.cpp:173-195) 的纯函数版。

    digit: 0=个位,1=十位...（StepTuner 里 exponent=10^digit）
    direction: +1 向上 / -1 向下
    prevent_carry: SHIFT 键，数字进位时反向退 9 步（cpp:191-192）
    zero_out: 清零低位数字（cpp:186-189）
    """
    amount = int(round(10.0 ** digit)) * (1 if direction >= 0 else -1)
    if zero_out:
        # modf(value/(exp*10))*exp*10 → 把 digit 以下清零
        step10 = amount * 10 if amount > 0 else -amount * 10
        value = int(value // step10) * step10
        return value
    if prevent_carry:
        carried = (value // (amount * 10)) != ((value + amount) // (amount * 10))
        value += (9 * -amount) if carried else amount
    else:
        value += amount
    return value


def cubic_drag_to_steps(delta_drag: float) -> int:
    """OnIdle drag 累积 → 步进次数。TuningCanvas.cpp:275-285。

        dragAccum += 5.0*deltaMouseX（deltaMouseX 是相对窗口宽的归一化位移）；
        while(dragAccum>1) step++ 并 dragAccum-=1
    返回有符号步进数（正=向上/频率增）。
    """
    accum = CUBIC_DRAG_PIXELS_PER_STEP * delta_drag
    return int(np.trunc(accum))


def cubic_clamp_bw(bw: int) -> int:
    """带宽钳位。来源 TuningCanvas.cpp:224-226。"""
    return int(min(max(bw, 0), CUBIC_CHANNELIZER_RATE_MAX))


def cubic_clamp_ppm(ppm: int) -> int:
    """PPM 钳位 ±2000。来源 TuningCanvas.cpp:259-265。"""
    return int(min(max(ppm, -CUBIC_PPM_LIMIT), CUBIC_PPM_LIMIT))


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_cubicsdr_tools(registry) -> None:
    """把 CubicSDR 多VFO / waterfall / drag-tune 注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _waterfall_cmap(args):
        theme = str(args.get("theme", "jet"))
        n = int(args.get("n", 256))
        try:
            cmap = cubic_waterfall_colormap(theme, n)
            data = {
                "theme": theme, "entries": n,
                "rgb_first": cmap[0].tolist(),
                "rgb_mid": cmap[n // 2].tolist(),
                "rgb_last": cmap[-1].tolist(),
                "state_colors": CUBIC_STATE_COLORS,
                "source": "CubicSDR ColorTheme.cpp:76-111",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"调色板失败: {e}")

    def _vfo_state_machine(args):
        """实例化多VFO管理器，添加/选中VFO并返回状态。"""
        center = int(args.get("center_frequency", 100_000_000))
        sr = int(args.get("sample_rate", 2_000_000))
        freqs = args.get("freqs", [100_000_000])
        try:
            mgr = CubicVFOManager(center, sr)
            for f in freqs:
                mgr.add_vfo(int(f), bandwidth=int(args.get("bandwidth", 3000)),
                            mod_type=str(args.get("mod_type", "am")))
            # 选第一个频率对应的 VFO 验证 select_by_frequency
            picked = mgr.select_by_frequency(int(freqs[0])) if freqs else None
            data = {
                "vfo_count": len(mgr.vfos),
                "vfos": [{"id": v.id, "freq": v.frequency, "bw": v.bandwidth,
                          "type": v.mod_type} for v in mgr.vfos],
                "current_modem_id": mgr.current_modem.id if mgr.current_modem else None,
                "selected_id": picked.id if picked else None,
                "center_frequency": mgr.center_frequency,
                "source": "CubicSDR DemodulatorMgr.h:14-92",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"VFO 状态机失败: {e}")

    def _drag_tune(args):
        """drag-tune：归一化拖拽位移→数字步进，并对频率/带宽/PPM 做钳位。"""
        delta_drag = float(args.get("delta_drag", 0.0))
        digit = int(args.get("digit", 4))
        value = int(args.get("value", 100_000_000))
        try:
            steps = cubic_drag_to_steps(delta_drag)
            direction = 1 if steps >= 0 else -1
            for _ in range(abs(steps)):
                value = cubic_step_tuner(value, digit, direction)
            kind = str(args.get("kind", "freq"))
            if kind == "bw":
                value = cubic_clamp_bw(value)
            elif kind == "ppm":
                value = cubic_clamp_ppm(value)
            data = {
                "delta_drag": delta_drag, "steps": steps, "digit": digit,
                "new_value": value, "kind": kind,
                "drag_sensitivity": CUBIC_DRAG_PIXELS_PER_STEP,
                "bw_max": CUBIC_CHANNELIZER_RATE_MAX,
                "ppm_limit": CUBIC_PPM_LIMIT,
                "source": "CubicSDR TuningCanvas.cpp:173-285, CubicSDRDefs.h:63",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"drag-tune 失败: {e}")

    registry.register(
        name="cubicsdr_waterfall_colormap",
        description=("CubicSDR waterfall 调色板：jet(黑→蓝→绿→黄→橙红)/sharp(绿系)/bw，"
                     "插值成 n×3 RGB；含 VFO 状态色 new=绿/hover=黄/destroy=红。"),
        parameters={
            "type": "object",
            "properties": {
                "theme": {"type": "string", "enum": ["jet", "sharp", "bw"]},
                "n": {"type": "integer", "default": 256},
            },
            "required": [],
        },
        handler=_waterfall_cmap,
        category="sdr_ui",
    )

    registry.register(
        name="cubicsdr_vfo_state_machine",
        description=("CubicSDR 多VFO状态机：DemodulatorMgr 维护 VFO 列表+活动指针"
                     "(currentModem/activeVisual)，add/select_by_frequency；VFO 超出"
                     "采样率半带时平移中心频率。"),
        parameters={
            "type": "object",
            "properties": {
                "center_frequency": {"type": "integer", "default": 100000000},
                "sample_rate": {"type": "integer", "default": 2000000},
                "freqs": {"type": "array", "items": {"type": "integer"}},
                "bandwidth": {"type": "integer", "default": 3000},
                "mod_type": {"type": "string", "default": "am"},
            },
            "required": [],
        },
        handler=_vfo_state_machine,
        category="sdr_ui",
    )

    registry.register(
        name="cubicsdr_drag_tune",
        description=("CubicSDR 视觉调谐 drag-tune：归一化拖拽位移×5.0=步进数，每步步进 "
                     "±10^digit；带宽钳位500k(CHANNELIZER_RATE_MAX)，PPM钳位±2000。"),
        parameters={
            "type": "object",
            "properties": {
                "delta_drag": {"type": "number", "default": 0.4},
                "digit": {"type": "integer", "default": 4},
                "value": {"type": "integer", "default": 100000000},
                "kind": {"type": "string", "enum": ["freq", "bw", "ppm"]},
            },
            "required": [],
        },
        handler=_drag_tune,
        category="sdr_ui",
    )


if __name__ == "__main__":
    mgr = CubicVFOManager(100_000_000, 2_000_000)
    mgr.add_vfo(100_100_000)
    mgr.add_vfo(100_500_000)
    print("VFOs:", len(mgr.vfos), "current:", mgr.current_modem.frequency)
    print("select 100.1MHz ->", mgr.select_by_frequency(100_100_000).id)
    print("drag +2.0 normalized:", cubic_drag_to_steps(2.0), "steps")
    print("step 100000 by 10^4 up:", cubic_step_tuner(100_000_000, 4, 1))
    print("clamp bw 999999 ->", cubic_clamp_bw(999_999))
    print("clamp ppm 99999 ->", cubic_clamp_ppm(99_999))

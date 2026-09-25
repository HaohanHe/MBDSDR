"""
HDSDR 传统 SDR UI / RF 前端架构移植（纯 numpy，无 Windows/ExtIO DLL）
===================================================================
HDSDR (www.hdsdr.de) 是闭源免费 Windows SDR 软件，无公开 git 仓库（见
repos/hdsdr/NOTES.md）。本模块按其公开手册与 ExtIO DLL 接口约定，移植：

  1. RF 前端三级增益表（LNA / Mixer / VGA）
     —— 来源: HDSDR ExtIO 标准接口；R820T 调谐器增益表（HDSDR RF/IF 滑块驱动）
  2. 解调模式参数表 + 带宽预设
     —— 来源: HDSDR 手册 wnew.html；带宽滑块 (F6) 常用预设

HDSDR 的 RF 前端通过 ExtIO DLL 暴露多级增益；经典 RTL-SDR(R820T) 链路为：
    LNA(低噪放) → Mixer(混频) → VGA/IF(中频可变增益)
HDSDR UI 上对应 "RF" 与 "IF" 两个增益滑块 + LNA/Mixer 档位。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


# ═══════════════════════════════════════════════════════════════════════
#  1. RF 前端三级增益表（R820T，HDSDR ExtIO 驱动）
# ═══════════════════════════════════════════════════════════════════════
#: LNA 增益档位（dB），index 0..15。来源: R820T 调谐器 LNA gain table
#: （HDSDR "RF" 滑块控制 LNA+Mixer 组合档位）
HDSDR_LNA_GAIN_DB = [
    0.0, 0.9, 2.4, 3.4, 5.2, 7.0, 8.8, 10.7,
    11.9, 12.8, 13.8, 14.9, 16.0, 17.8, 19.8, 23.1,
]
#: Mixer 增益档位（dB），index 0..15。来源: R820T mixer gain table
HDSDR_MIXER_GAIN_DB = [
    -4.0, -1.0, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5,
    4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5,
]
#: VGA / IF 增益档位（dB），index 0..15。来源: R820T VGA（HDSDR "IF" 滑块）
HDSDR_VGA_GAIN_DB = [
    0.0, 1.6, 3.2, 4.8, 6.4, 8.0, 9.6, 11.2,
    12.8, 14.4, 16.0, 17.6, 19.2, 20.8, 22.4, 24.0,
]


def hdsdr_gain_clamp_stage(stage: str, index: int) -> int:
    """把档位索引钳位到该级合法范围（0..15）。"""
    table = {"lna": HDSDR_LNA_GAIN_DB, "mixer": HDSDR_MIXER_GAIN_DB,
             "vga": HDSDR_VGA_GAIN_DB}.get((stage or "lna").lower())
    if table is None:
        raise ValueError(f"未知增益级 {stage}（lna/mixer/vga）")
    return int(min(max(int(index), 0), len(table) - 1))


def hdsdr_total_gain(lna_idx: int, mixer_idx: int, vga_idx: int) -> Dict:
    """三级增益求和，输入自动钳位到合法档位。"""
    l = hdsdr_gain_clamp_stage("lna", lna_idx)
    m = hdsdr_gain_clamp_stage("mixer", mixer_idx)
    v = hdsdr_gain_clamp_stage("vga", vga_idx)
    total = HDSDR_LNA_GAIN_DB[l] + HDSDR_MIXER_GAIN_DB[m] + HDSDR_VGA_GAIN_DB[v]
    return {
        "lna_index": l, "lna_db": HDSDR_LNA_GAIN_DB[l],
        "mixer_index": m, "mixer_db": HDSDR_MIXER_GAIN_DB[m],
        "vga_index": v, "vga_db": HDSDR_VGA_GAIN_DB[v],
        "total_gain_db": round(total, 2),
        "source": "HDSDR ExtIO R820T LNA/Mixer/VGA gain table",
    }


# ═══════════════════════════════════════════════════════════════════════
#  2. 解调模式参数表 + 带宽预设
# ═══════════════════════════════════════════════════════════════════════
#: HDSDR 解调模式 → 典型带宽预设 (Hz)。来源: HDSDR 手册 / F6 带宽滑块常用档。
HDSDR_BANDWIDTH_PRESETS = {
    "cw":   500,      # CW 窄带
    "usb":  2400,     # 上边带
    "lsb":  2400,     # 下边带
    "am":   6000,     # 调幅
    "nfm":  12500,    # 窄带调频（对讲）
    "wfm":  120000,   # 宽带调频（广播）
}
#: 解调模式列表。来源: HDSDR wnew.html (SSB/AM/FM/CW TX/RX)
HDSDR_MODES = ("am", "fm", "nfm", "wfm", "usb", "lsb", "cw")


def hdsdr_bandwidth_for_mode(mode: str) -> Dict:
    """按解调模式返回 HDSDR 默认带宽预设。fm 视作 nfm 别名。"""
    m = (mode or "am").lower()
    if m == "fm":
        m = "nfm"
    if m not in HDSDR_BANDWIDTH_PRESETS:
        raise ValueError(f"HDSDR 不支持模式 {mode}（{list(HDSDR_BANDWIDTH_PRESETS)}）")
    bw = HDSDR_BANDWIDTH_PRESETS[m]
    return {
        "mode": m, "bandwidth_hz": bw,
        "bandwidth_khz": bw / 1000.0,
        "source": "HDSDR bandwidth presets (F6)",
    }


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_hdsdr_tools(registry) -> None:
    """把 HDSDR RF前端增益 / 带宽预设 注册进 ToolRegistry。"""
    import json
    from mbdsdr_ai.tool_registry import ToolResult

    def _rf_gain(args):
        lna = int(args.get("lna", 7))
        mixer = int(args.get("mixer", 8))
        vga = int(args.get("vga", 8))
        try:
            data = hdsdr_total_gain(lna, mixer, vga)
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"RF 增益计算失败: {e}")

    def _bandwidth_preset(args):
        mode = str(args.get("mode", "am"))
        try:
            data = hdsdr_bandwidth_for_mode(mode)
            data["available_modes"] = list(HDSDR_MODES)
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"带宽预设失败: {e}")

    def _gain_table(args):
        """返回完整三级增益表用于校验/显示。"""
        try:
            data = {
                "lna_db": HDSDR_LNA_GAIN_DB,
                "mixer_db": HDSDR_MIXER_GAIN_DB,
                "vga_db": HDSDR_VGA_GAIN_DB,
                "stages": ["lna", "mixer", "vga"],
                "source": "HDSDR ExtIO R820T gain table",
            }
            return ToolResult(True, json.dumps(data, ensure_ascii=False), data=data)
        except Exception as e:
            return ToolResult(False, f"增益表失败: {e}")

    registry.register(
        name="hdsdr_rf_gain",
        description=("HDSDR RF 前端三级增益：LNA/Mixer/VGA 档位(0..15)自动钳位，"
                     "求和得总增益(dB)。移植 ExtIO R820T 增益表。"),
        parameters={
            "type": "object",
            "properties": {
                "lna": {"type": "integer", "default": 7},
                "mixer": {"type": "integer", "default": 8},
                "vga": {"type": "integer", "default": 8},
            },
            "required": [],
        },
        handler=_rf_gain,
        category="sdr_ui",
    )

    registry.register(
        name="hdsdr_bandwidth_preset",
        description=("HDSDR 解调模式→带宽预设：CW500/SSB2400/AM6000/NFM12500/WFM120000。"),
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(HDSDR_MODES)},
            },
            "required": [],
        },
        handler=_bandwidth_preset,
        category="sdr_demod",
    )

    registry.register(
        name="hdsdr_gain_table",
        description=("HDSDR RF 前端完整三级增益表（LNA/Mixer/VGA 各 16 档 dB 值），"
                     "用于校验档位钳位与总增益计算。"),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=_gain_table,
        category="sdr_ui",
    )


if __name__ == "__main__":
    print(hdsdr_total_gain(7, 8, 8))
    print("越界 lna=99 ->", hdsdr_gain_clamp_stage("lna", 99))
    for m in HDSDR_MODES:
        print(m, "->", hdsdr_bandwidth_for_mode(m)["bandwidth_hz"], "Hz")

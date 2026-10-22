# SPDX-License-Identifier: MIT
"""同步/调制解调原子工具适配层（每块积木一个独立小工具，绝不打包）
================================================================

设计红线（AGENTS.md「原子能力、模型编排」+ 用户明确要求）：**不提供
「解调工具」这种大黑盒**。每个工具只包一块最小原子积木（载波锁频/符号
同步/调制/解调/频率轨迹提取），模型现场一个个调用、自由编排成任意
接收链。工具是积木，拼法是模型的能力。

与 sync_bricks.py（纯物理层、可测）分工：本文件是薄适配层，把纯函数
暴露为 MCP 工具。工具命名通用化（fll/gmsk/symbol_sync/freq_track），
不出现「demod_xxx」这类把流程焊死的复合工具。
"""
from __future__ import annotations

import numpy as np

from .tool_registry import ToolResult
from . import sync_bricks as sb


def _err(e: Exception) -> ToolResult:
    return ToolResult(
        success=False,
        content=f"参数/计算错误: {type(e).__name__}: {e}",
        data={"error": str(e)},
    )


def _arr(x) -> list:
    return np.asarray(x, dtype=float).tolist()


def _as_iq(v) -> np.ndarray:
    """把 [re,im,re,im,...] 或 [[re,im],...] 或复数列表统一成复数数组。"""
    a = np.asarray(v)
    if np.iscomplexobj(a):
        return a.astype(np.complex128)
    a = np.asarray(v, dtype=float)
    if a.ndim == 2 and a.shape[1] == 2:
        return (a[:, 0] + 1j * a[:, 1]).astype(np.complex128)
    if a.ndim == 1:
        if a.size % 2 != 0:
            raise ValueError("IQ 交错数组长度须为偶数")
        return (a[0::2] + 1j * a[1::2]).astype(np.complex128)
    raise ValueError("无法识别的 IQ 数组形状")


def _iq_in(v) -> list:
    return [[float(z.real), float(z.imag)] for z in np.asarray(v)]


# ─────────────────────────────────────────────────────────────
# 载波：FLL
# ─────────────────────────────────────────────────────────────
def _fll_track(args):
    try:
        iq = _as_iq(args["iq"])
        fll = sb.FrequencyLockedLoop(loop_bw=float(args.get("loop_bw", 0.05)))
        out = fll.feed(iq)
        return ToolResult(success=True, content=(
            f"FLL 锁频：频率估计 {fll.frequency_estimate:.6f} rad/sample，"
            f"已校正 {len(out)} 样本"
        ), data={
            "frequency_estimate_rad_per_sample": fll.frequency_estimate,
            "corrected_iq": _iq_in(out),
        })
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 符号同步
# ─────────────────────────────────────────────────────────────
def _symbol_sync(args):
    try:
        iq = _as_iq(args["iq"])
        sync = sb.SymbolSync(
            samples_per_symbol=float(args["samples_per_symbol"]),
            ted=str(args.get("ted", "gardner")),
            loop_gain=float(args.get("loop_gain", 0.05)),
        )
        syms = sync.feed(iq)
        return ToolResult(success=True, content=(
            f"符号同步（{sync.ted_name}）：恢复 {len(syms)} 符号，"
            f"收敛 sps={sync.sps:.3f}"
        ), data={"symbols": _iq_in(syms), "sps_converged": sync.sps,
                 "count": int(len(syms))})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# GMSK
# ─────────────────────────────────────────────────────────────
def _gmsk_modulate(args):
    try:
        bits = [int(b) for b in args["bits"]]
        iq = sb.gmsk_modulate(bits, sps=int(args.get("sps", 8)),
                              bt=float(args.get("bt", 0.3)))
        return ToolResult(success=True, content=(
            f"GMSK 调制：{len(bits)} 比特 -> {len(iq)} 样本（BT={args.get('bt', 0.3)}）"
        ), data={"iq": _iq_in(iq), "samples": int(len(iq))})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _gmsk_demodulate(args):
    try:
        iq = _as_iq(args["iq"])
        bits = sb.gmsk_demodulate(iq, sps=int(args.get("sps", 8)))
        return ToolResult(success=True, content=(
            f"GMSK 解调：{len(bits)} 比特"
        ), data={"bits": [int(b) for b in bits], "count": int(len(bits))})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 频率轨迹提取
# ─────────────────────────────────────────────────────────────
def _iq_freq_track(args):
    try:
        iq = _as_iq(args["iq"])
        times, freqs = sb.iq_freq_track(
            iq, float(args["sample_rate"]),
            window=int(args.get("window", 1024)),
            hop=int(args.get("hop", 512)),
        )
        return ToolResult(success=True, content=(
            f"频率轨迹：{len(freqs)} 个采样点，"
            f"频率 {float(np.min(freqs)):.1f}~{float(np.max(freqs)):.1f} Hz"
        ), data={"times_s": _arr(times), "freqs_hz": _arr(freqs),
                 "count": int(len(freqs))})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ─────────────────────────────────────────────────────────────
# 注册：每块积木一个独立小工具
# ─────────────────────────────────────────────────────────────
def register_sync_brick_tools(tool_registry):
    """把同步/调制解调原子积木逐块注册为独立小工具（模型编排，不打包）。"""
    R = tool_registry.register

    R(
        name="fll_track",
        description="载波锁频：FLL 对复基带做频率跟踪与去频偏，返回频率估计 + 校正后 IQ。原子积木，供模型组合载波恢复。",
        parameters={"type": "object", "properties": {
            "iq": {"type": "array", "description": "复基带 [[re,im],...] 或交错 [re,im,...]"},
            "loop_bw": {"type": "number", "description": "归一化环路带宽 rad/sample，默认 0.05"},
        }, "required": ["iq"]},
        handler=_fll_track, category="sync",
    )
    R(
        name="symbol_sync_recover",
        description="符号定时同步：从复基带恢复符号序列（TED 可选 gardner/mueller_muller/early_late）。原子积木，供模型组合符号恢复。",
        parameters={"type": "object", "properties": {
            "iq": {"type": "array", "description": "复基带"},
            "samples_per_symbol": {"type": "number", "description": "每符号采样数初值"},
            "ted": {"type": "string", "enum": ["gardner", "mueller_muller", "early_late"],
                    "description": "定时误差检测器，默认 gardner"},
            "loop_gain": {"type": "number", "description": "环路增益，默认 0.05"},
        }, "required": ["iq", "samples_per_symbol"]},
        handler=_symbol_sync, category="sync",
    )
    R(
        name="gmsk_modulate",
        description="GMSK 调制：比特序列 -> 复基带（高斯成形 BT 参数化）。原子积木。",
        parameters={"type": "object", "properties": {
            "bits": {"type": "array", "items": {"type": "integer"}, "description": "比特序列 0/1"},
            "sps": {"type": "integer", "description": "每符号采样数，默认 8"},
            "bt": {"type": "number", "description": "带宽时间积，默认 0.3"},
        }, "required": ["bits"]},
        handler=_gmsk_modulate, category="modem",
    )
    R(
        name="gmsk_demodulate",
        description="GMSK 非相干解调：复基带 -> 比特序列（差分鉴频 + 峰值对称窗判决）。原子积木。",
        parameters={"type": "object", "properties": {
            "iq": {"type": "array", "description": "复基带"},
            "sps": {"type": "integer", "description": "每符号采样数，默认 8"},
        }, "required": ["iq"]},
        handler=_gmsk_demodulate, category="modem",
    )
    R(
        name="iq_freq_track",
        description="频率轨迹提取：IQ -> (时刻, 频率) 序列（STFT 峰值轨迹）。输出可喂多普勒换算积木反演距离变化率，再喂 RLS 定轨。原子积木。",
        parameters={"type": "object", "properties": {
            "iq": {"type": "array", "description": "复基带"},
            "sample_rate": {"type": "number", "description": "采样率 Hz"},
            "window": {"type": "integer", "description": "STFT 窗长，默认 1024"},
            "hop": {"type": "integer", "description": "步进，默认 512"},
        }, "required": ["iq", "sample_rate"]},
        handler=_iq_freq_track, category="dsp",
    )

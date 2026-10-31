# SPDX-License-Identifier: MIT
"""
MBDSDR AI - VNA 工具注册面
==========================
把 NanoVNA 后端（nanovna_client / nanovna_dump / vna_pipeline）封装为
LLM 可调用工具。后端 2026-10-23 真机战役（NanoVNA-H 全自动 SOLT）已验证，
此前未进 LLM 注册面（库在、注册 0 件）。

诚实纪律：
- vna_list_ports：只读枚举；无 pyserial / 无设备返回诚实空列表，不伪造。
- vna_sweep：写/动设备工具；无设备诚实失败（NEED_HW 语义），绝不造数据。
- vna_analyze：纯计算；对调用方给出的 S 参数做单口/双口全分析。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .tool_registry import ToolResult
from .nanovna_client import NanoVNADriverError, find_nanovna_ports
from .nanovna_dump import run_dump
from .vna_pipeline import analyze_one_port, analyze_two_port

_NEED_HW_NEXT = (
    "先用 vna_list_ports 确认设备枚举；已连接则用 port 显式指定串口"
    "（Linux /dev/ttyACM0，Windows COMx）；无硬件时不要虚构测量结果。"
)


def _s11_to_complex(s11: List) -> List[complex]:
    out = []
    for i, p in enumerate(s11):
        try:
            re, im = float(p[0]), float(p[1])
        except (TypeError, IndexError, ValueError):
            raise ValueError(f"S11 第 {i} 点不是 [re, im] 复数对: {p!r}")
        out.append(complex(re, im))
    return out


def _analyze_dump(out: Dict[str, Any], z0: float, vf: float,
                  freq_hz) -> Dict[str, Any]:
    freqs = out["frequencies_hz"]
    s11 = _s11_to_complex(out["S11"])
    analysis: Dict[str, Any] = {
        "one_port": analyze_one_port(freqs, s11, z0=z0, vf=vf,
                                     freq_hz=freq_hz),
    }
    s21_raw = out.get("S21") or []
    if s21_raw:
        s21 = _s11_to_complex(s21_raw)
        analysis["two_port"] = analyze_two_port(freqs, s21)
    return analysis


def register_vna_tools(registry) -> None:
    """把 VNA 三工具注册进 tool_registry。"""

    registry.register(
        name="vna_list_ports",
        description=(
            "枚举本机 NanoVNA 串口（按 USB VID:PID 提示过滤）。"
            "纯只读；无 pyserial 或无设备时返回空列表（诚实空态，不伪造）。"
            "返回 [(port, vid, pid), ...]。"
        ),
        parameters={"type": "object", "properties": {}},
        handler=lambda args: _list_ports(),
        category="vna",
    )

    registry.register(
        name="vna_sweep",
        description=(
            "NanoVNA 一次完整扫频（**动设备工具**）：连接设备，按起止频点与"
            "点数采集 S11/S21 及 11 种派生迹线，附单口/双口全分析"
            "（VSWR 极值/谐振/TDR/LC 匹配/双口增益）。无设备时诚实失败"
            "（NEED_HW），绝不伪造测量数据。超过设备单 sweep 点数上限时"
            "自动分段拼接（耗时随段数线性增长）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "port": {"type": "string",
                         "description": "串口路径；空串自动探测（探测不到则诚实失败）"},
                "start_hz": {"type": "integer", "description": "扫频起点 Hz"},
                "stop_hz": {"type": "integer", "description": "扫频终点 Hz"},
                "points": {"type": "integer", "description": "扫频点数（≥2）"},
                "z0": {"type": "number", "description": "参考阻抗 Ω，默认 50"},
                "vf": {"type": "number", "description": "电缆速度因子，默认 0.66"},
                "freq_hz": {"type": "number",
                            "description": "LC 匹配目标频点；缺省取 |S11| 最差点"},
                "seg_points": {"type": "integer",
                               "description": "分段段长（H 固件 101，H4 可 401）"},
            },
            "required": ["start_hz", "stop_hz", "points"],
        },
        handler=_sweep,
        category="vna",
    )

    registry.register(
        name="vna_analyze",
        description=(
            "对调用方给出的 S 参数做纯计算全分析（不动设备）：单口"
            "（VSWR/谐振 Q/TDR/LC 匹配/电缆损耗）与可选双口（增益/群时延）。"
            "输入必须来自真实测量（vna_sweep 产物）或如实标注的合成数据。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "frequencies_hz": {"type": "array", "items": {"type": "number"}},
                "s11": {"type": "array", "items": {"type": "array"},
                        "description": "[[re, im], ...] 复数对，长度与频点一致"},
                "s21": {"type": "array", "items": {"type": "array"},
                        "description": "可选双口 S21，同格式"},
                "z0": {"type": "number", "description": "参考阻抗 Ω，默认 50"},
                "vf": {"type": "number", "description": "电缆速度因子，默认 0.66"},
                "freq_hz": {"type": "number", "description": "LC 匹配目标频点"},
            },
            "required": ["frequencies_hz", "s11"],
        },
        handler=_analyze,
        category="vna",
    )


def _list_ports() -> ToolResult:
    found = find_nanovna_ports()
    if not found:
        return ToolResult(
            success=True,
            content="未探测到 NanoVNA（VID:PID 0x0483:0x5740）。"
                    "设备未连接、被占用或无 pyserial 时均为此诚实空态。",
            data={"ports": []},
        )
    lines = [f"{p} (VID:PID {v:#06x}:{p2:#06x})" for p, v, p2 in found]
    return ToolResult(
        success=True,
        content="发现 NanoVNA 端口：\n" + "\n".join(lines),
        data={"ports": [{"port": p, "vid": v, "pid": pd}
                        for p, v, pd in found]},
    )


def _sweep(args: Dict[str, Any]) -> ToolResult:
    try:
        start_hz = int(args["start_hz"])
        stop_hz = int(args["stop_hz"])
        points = int(args["points"])
    except (KeyError, TypeError, ValueError) as e:
        return ToolResult(success=False, content=f"参数错误：{e}")
    if start_hz <= 0 or stop_hz <= 0:
        return ToolResult(success=False,
                          content="start_hz/stop_hz 必须为正整数（Hz）")
    if start_hz >= stop_hz:
        return ToolResult(success=False,
                          content=f"start_hz({start_hz}) 必须小于 stop_hz({stop_hz})")
    if points < 2:
        return ToolResult(success=False,
                          content=f"points({points}) 必须 ≥2")
    z0 = float(args.get("z0") or 50.0)
    vf = float(args.get("vf") or 0.66)
    freq_hz = args.get("freq_hz")
    if freq_hz is not None:
        freq_hz = float(freq_hz)
    seg_points = args.get("seg_points")
    port = str(args.get("port") or "")

    try:
        out = run_dump(port, start_hz, stop_hz, points,
                       seg_points=int(seg_points) if seg_points else None)
    except SystemExit as e:
        text = str(e) or e.code or ""
        return ToolResult(success=False, error="NEED_HW",
                          content=f"扫频未执行（无可用设备或参数被拒）：{text}",
                          next_step=_NEED_HW_NEXT)
    except NanoVNADriverError as e:
        return ToolResult(success=False, error="NEED_HW",
                          content=f"设备通信失败：{e}",
                          next_step=_NEED_HW_NEXT)

    out["analysis"] = _analyze_dump(out, z0, vf, freq_hz)
    return ToolResult(
        success=True,
        content=json.dumps(out, ensure_ascii=False),
        data={"points": len(out["frequencies_hz"]),
              "port": out.get("port"),
              "model": out.get("model")},
    )


def _analyze(args: Dict[str, Any]) -> ToolResult:
    freqs = args.get("frequencies_hz")
    s11_raw = args.get("s11")
    if not isinstance(freqs, list) or not freqs:
        return ToolResult(success=False,
                          content="frequencies_hz 必须为非空数组")
    if not isinstance(s11_raw, list) or not s11_raw:
        return ToolResult(success=False, content="s11 必须为非空 [[re,im],...] 数组")
    if len(freqs) != len(s11_raw):
        return ToolResult(
            success=False,
            content=f"形状不一致：freqs={len(freqs)} 点 vs S11={len(s11_raw)} 点")
    try:
        freqs_hz = [float(f) for f in freqs]
        s11 = _s11_to_complex(s11_raw)
    except ValueError as e:
        return ToolResult(success=False, content=f"数据格式错误：{e}")

    z0 = float(args.get("z0") or 50.0)
    vf = float(args.get("vf") or 0.66)
    freq_hz = args.get("freq_hz")
    if freq_hz is not None:
        freq_hz = float(freq_hz)

    result: Dict[str, Any] = {
        "one_port": analyze_one_port(freqs_hz, s11, z0=z0, vf=vf,
                                     freq_hz=freq_hz),
    }
    s21_raw = args.get("s21")
    if s21_raw:
        if len(s21_raw) != len(freqs_hz):
            return ToolResult(
                success=False,
                content=f"形状不一致：S21={len(s21_raw)} 点 vs freqs={len(freqs_hz)} 点")
        try:
            s21 = _s11_to_complex(s21_raw)
        except ValueError as e:
            return ToolResult(success=False, content=f"S21 格式错误：{e}")
        result["two_port"] = analyze_two_port(freqs_hz, s21)

    return ToolResult(success=True,
                      content=json.dumps(result, ensure_ascii=False),
                      data={"points": len(freqs_hz)})

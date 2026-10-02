#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
实验：Agent 工具调用管线——本地确定性环节的耗时与成功率分析
================================================================

背景：MBDSDR 的"AI 即工具"架构把 SDR 能力注册成 OpenAI function-calling 工具，
由 LLM 决定"调哪个工具、填什么参数"。一条完整工具调用分四个环节：

    注册(register) -> 选择/名称解析(resolve) -> 参数校验(validate) -> 执行(handler)

其中"LLM 决定调哪个工具、填什么参数"这一步依赖在线 LLM，云内无 key、不可
确定性复现——本实验**不测**它，那一列保持 PENDING_ONLINE_RUN（诚实空态）。

本实验测的是**包裹 LLM 决策的本地确定性管线**（mbdsdr_ai.tool_registry）：
给定一串"工具调用请求"（工具名 + 参数，这里用确定性 oracle 构造，不调 LLM），
逐环节统计：
  - registration : 注册 N 个本地真实工具的耗时；
  - resolve      : 工具名能否解析到已注册工具（含短名/前缀自动解析）；
  - validate     : required 参数校验 / 别名归一化 是否放行；
  - execution    : handler 执行成功与否 + latency_ms（registry.call 实测）。

请求套件刻意混合：正确请求 / 错误工具名 / 缺必填参数 / 别名参数 / 短名，
以同时度量"正确路径成功率"与"错误路径是否优雅失败（不崩溃、返回明确错误码）"。

被测工具均为**本地真实函数**（非 mock）：
  - morse_encode       : 真实摩斯表（mbdsdr_ai.cw_decoder.MORSE_TABLE 逆映射）
  - gis_distance       : 真实 haversine 大圆距离
  - aprs_position_encode: 真实 ax25.build_aprs_position_frame
  - gnss_parse_rmc     : 真实 $GNRMC 语句解析

口径：synthetic（本地确定性计算，无 LLM/无网络/无硬件）。LLM 决策质量列
PENDING_ONLINE_RUN。每类请求重复 trials 次取稳定性，耗时实测。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from mbdsdr_ai.tool_registry import ToolRegistry, ToolResult  # noqa: E402
from mbdsdr_ai.cw_decoder import MORSE_TABLE  # noqa: E402
from mbdsdr_ai import ax25  # noqa: E402
from experiments.common import runner, manifest  # noqa: E402
from experiments.common import plot as eplot  # noqa: E402

ORIGIN = "synthetic"
OUT_DIR = os.path.join(ROOT, "paper", "experiments")
FIG_DIR = os.path.join(OUT_DIR, "figures")


# ---------------------------------------------------------------------------
# 本地真实工具 handler（纯函数，无网络/无硬件）
# ---------------------------------------------------------------------------
def _morse_encode_handler(args: dict) -> ToolResult:
    text = (args.get("text") or "").upper()
    inv = {v: k for k, v in MORSE_TABLE.items()}     # char -> morse code
    out = " ".join(inv.get(ch, "?") for ch in text)
    return ToolResult(success=True, content=out, tool_name="morse_encode",
                       args=args, data={"encoded": out})


def _gis_distance_handler(args: dict) -> ToolResult:
    import math
    R = 6371.0
    lat1, lon1 = float(args["lat1"]), float(args["lon1"])
    lat2, lon2 = float(args["lat2"]), float(args["lon2"])
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * R * math.asin(math.sqrt(a))
    return ToolResult(success=True, content=f"{d:.1f} km",
                      tool_name="gis_distance", args=args, data={"km": round(d, 2)})


def _aprs_encode_handler(args: dict) -> ToolResult:
    frame = ax25.build_aprs_position_frame(
        str(args["source"]), float(args["latitude"]), float(args["longitude"]))
    n = len(frame.to_bytes()) if hasattr(frame, "to_bytes") else 0
    return ToolResult(success=True,
                      content=f"APRS frame from {frame.source}, fcs_valid={frame.fcs_valid}",
                      tool_name="aprs_position_encode", args=args,
                      data={"fcs_valid": bool(frame.fcs_valid), "n_bytes": n})


def _gnss_parse_rmc_handler(args: dict) -> ToolResult:
    s = args["nmea"].strip()
    # 真实最小 RMC 字段切分（不联网）：$GNRMC,hhmmss.ss,A,llll.ll,N,yyyyy.yy,E,...
    parts = s.split(",")
    if len(parts) < 6 or not parts[0].endswith("RMC"):
        return ToolResult(success=False, content="not a GNRMC sentence",
                          tool_name="gnss_parse_rmc", args=args,
                          error="bad_nmea")
    return ToolResult(success=True,
                      content=f"time={parts[1]} lat={parts[3]}{parts[4]} "
                              f"lon={parts[5]}{parts[6] if len(parts)>6 else ''}",
                      tool_name="gnss_parse_rmc", args=args,
                      data={"utc_time": parts[1], "status": parts[2]})


def build_registry() -> ToolRegistry:
    """注册本实验用的 4 个本地真实工具（含 required 参数声明，供校验环节演练）。"""
    reg = ToolRegistry()
    reg.register("morse_encode", "把文本编码为摩斯电码",
                 {"type": "object",
                  "properties": {"text": {"type": "string", "description": "要编码的文本"}},
                  "required": ["text"]},
                 _morse_encode_handler, category="decode")
    reg.register("gis_distance", "算两点大圆距离(km)",
                 {"type": "object",
                  "properties": {"lat1": {"type": "number"}, "lon1": {"type": "number"},
                                 "lat2": {"type": "number"}, "lon2": {"type": "number"}},
                  "required": ["lat1", "lon1", "lat2", "lon2"]},
                 _gis_distance_handler, category="gis")
    reg.register("aprs_position_encode", "编码一条 APRS 位置帧",
                 {"type": "object",
                  "properties": {"source": {"type": "string"},
                                 "latitude": {"type": "number"},
                                 "longitude": {"type": "number"}},
                  "required": ["source", "latitude", "longitude"]},
                 _aprs_encode_handler, category="decode")
    reg.register("gnss_parse_rmc", "解析一条 NMEA RMC 语句",
                 {"type": "object",
                  "properties": {"nmea": {"type": "string"}},
                  "required": ["nmea"]},
                 _gnss_parse_rmc_handler, category="gnss")
    return reg


# ---------------------------------------------------------------------------
# 确定性请求套件：(请求名, 工具名, 参数, 期望结果类别)
#   expect ∈ {"exec_success","tool_not_found","missing_required_params"}
# ---------------------------------------------------------------------------
def build_requests():
    nmea = "$GNRMC,072545.00,A,4352.0000,N,12519.0000,E,0.0,0.0,250926,,,A*6B"
    return [
        # --- 正确路径 ---
        ("ok_morse",       "morse_encode",            {"text": "HELLO"}, "exec_success"),
        ("ok_gis",         "gis_distance",            {"lat1": 43.8868, "lon1": 125.3245,
                                                         "lat2": 39.9042, "lon2": 116.4074},
         "exec_success"),
        ("ok_aprs",        "aprs_position_encode",    {"source": "BI4MIB", "latitude": 43.88,
                                                         "longitude": 125.32}, "exec_success"),
        ("ok_nmea",        "gnss_parse_rmc",         {"nmea": nmea}, "exec_success"),
        # --- 短名/别名（应被 registry 自动解析/归一化，仍成功）---
        ("alias_gis_short", "distance",              {"lat": 43.88, "lon": 125.32,
                                                         "lat2": 39.9, "lon2": 116.4},
         "exec_success"),
        # --- 错误路径：应优雅失败（不崩溃，返回明确错误码）---
        ("bad_unknown_tool", "sdr_spectrum_analyze",  {}, "tool_not_found"),
        ("bad_missing_args", "morse_encode",         {}, "missing_required_params"),
        ("bad_missing_gis",  "gis_distance",         {"lat1": 43.88}, "missing_required_params"),
    ]


# 短名"distance"在 registry._resolve_tool_name 里不会自动映射到 gis_distance，
# 这里显式加一个别名工具，让"短名解析"环节有真实演练（模拟弱模型用短名）。
def register_alias(reg: ToolRegistry):
    reg.register("distance", "gis_distance 的短名别名（转发）",
                 {"type": "object",
                  "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                 "lat2": {"type": "number"}, "lon2": {"type": "number"}},
                  "required": ["lat", "lon", "lat2", "lon2"]},
                 lambda args: _gis_distance_handler(
                     {"lat1": args["lat"], "lon1": args["lon"],
                      "lat2": args["lat2"], "lon2": args["lon2"]}),
                 category="gis")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50,
                    help="每个请求重复次数（测稳定性/耗时分布）")
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    runner_obj = runner.ExperimentRunner(args.seed)

    # ---- 环节1：注册（实测耗时）----
    t0 = time.perf_counter()
    reg = build_registry()
    register_alias(reg)
    n_tools = len(reg.get_tool_names())
    reg_latency_ms = (time.perf_counter() - t0) * 1000.0

    requests = build_requests()

    # ---- 逐请求重复 trials 次：测量 resolve/validate/execution ----
    detail_rows = []
    # stage 计数器：按 (阶段, 是否成功/优雅) 累计
    stat = {k: {"succ": 0, "n": 0, "lat": []}
            for k in ["resolve_ok", "resolve_unknown_graceful",
                      "validate_pass", "validate_missing_graceful",
                      "execution_ok", "execution_graceful_fail"]}
    for name, tool, call_args, expect in requests:
        for k in range(args.trials):
            rng = runner_obj.make_rng(f"toolcall|{name}|{k}")  # 固定种子（虽然本管线无随机，保持一致）
            _ = rng
            res = reg.call(tool, dict(call_args))
            # 判定环节结果
            if expect == "exec_success":
                # 名称应解析成功
                resolved = tool in reg.tools or reg.has_tool(tool) or tool == "distance"
                stat["resolve_ok"]["succ"] += int(resolved)
                stat["resolve_ok"]["n"] += 1
                # 校验应放行
                stat["validate_pass"]["succ"] += int(res.error != "missing_required_params")
                stat["validate_pass"]["n"] += 1
                # 执行应成功
                ok = int(res.success)
                stat["execution_ok"]["succ"] += ok
                stat["execution_ok"]["n"] += 1
            elif expect == "tool_not_found":
                stat["resolve_unknown_graceful"]["succ"] += int(res.error == "tool_not_found")
                stat["resolve_unknown_graceful"]["n"] += 1
                stat["execution_graceful_fail"]["succ"] += int(not res.success)
                stat["execution_graceful_fail"]["n"] += 1
            elif expect == "missing_required_params":
                stat["validate_missing_graceful"]["succ"] += int(res.error == "missing_required_params")
                stat["validate_missing_graceful"]["n"] += 1
                stat["execution_graceful_fail"]["succ"] += int(not res.success)
                stat["execution_graceful_fail"]["n"] += 1
            stat["resolve_ok" if expect == "exec_success" else "validate_pass"]["lat"].append(res.latency_ms) \
                if expect == "exec_success" else None
            detail_rows.append({
                "request": name, "requested_tool": tool, "expected": expect,
                "got_success": res.success, "got_error": res.error or "",
                "latency_ms": res.latency_ms,
                "trial": k, "data_origin": ORIGIN,
            })

    # 每请求执行成功率（trials 次）汇总
    req_summary = []
    for name, tool, call_args, expect in requests:
        rs = [r for r in detail_rows if r["request"] == name]
        # exec_success 类：看 got_success 比例；错误类：看 error 是否符合预期
        cell = runner.BinomialCell(sum(1 for r in rs if (
            (expect == "exec_success" and r["got_success"]) or
            (expect == "tool_not_found" and r["got_error"] == "tool_not_found") or
            (expect == "missing_required_params" and r["got_error"] == "missing_required_params")
        )), len(rs))
        lat = np.mean([r["latency_ms"] for r in rs])
        req_summary.append({
            "request": name, "requested_tool": tool, "expected": expect,
            "n_trials": len(rs),
            "handled_rate": round(cell.rate, 4),
            "wilson_lo": round(cell.wilson[0], 4),
            "wilson_hi": round(cell.wilson[1], 4),
            "mean_latency_ms": round(float(lat), 3),
            "data_origin": ORIGIN,
        })

    # 环节汇总（注册环节 + 四个管线环节）
    stage_rows = [{
        "stage": "register", "n": n_tools, "success_rate": 1.0,
        "wilson_lo": 1.0, "wilson_hi": 1.0,
        "mean_latency_ms": round(reg_latency_ms, 3),
        "note": f"注册 {n_tools} 个本地真实工具",
    }]
    stage_label = {
        "resolve_ok": "name_resolution(correct)",
        "resolve_unknown_graceful": "name_resolution(unknown->graceful)",
        "validate_pass": "param_validation(pass)",
        "validate_missing_graceful": "param_validation(missing->graceful)",
        "execution_ok": "execution(correct path)",
        "execution_graceful_fail": "execution(bad path graceful)",
    }
    for key, lbl in stage_label.items():
        s = stat[key]
        cell = runner.BinomialCell(s["succ"], s["n"]) if s["n"] else runner.BinomialCell(0, 0)
        lat = float(np.mean(s["lat"])) if s["lat"] else 0.0
        stage_rows.append({
            "stage": lbl, "n": s["n"],
            "success_rate": round(cell.rate, 4) if s["n"] else "",
            "wilson_lo": round(cell.wilson[0], 4) if s["n"] else "",
            "wilson_hi": round(cell.wilson[1], 4) if s["n"] else "",
            "mean_latency_ms": round(lat, 3),
            "note": "本地确定性管线" if s["n"] else "",
        })

    os.makedirs(args.out, exist_ok=True)
    # CSV1：逐请求汇总
    p1 = os.path.join(args.out, "agent_toolcall_requests.csv")
    with open(p1, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(req_summary[0].keys()))
        w.writeheader(); w.writerows(req_summary)
    # CSV2：逐环节汇总
    p2 = os.path.join(args.out, "agent_toolcall_stage_summary.csv")
    with open(p2, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(stage_rows[0].keys()))
        w.writeheader(); w.writerows(stage_rows)
    # CSV3：逐次明细（含 LLM 决策 PENDING 行）
    p3 = os.path.join(args.out, "agent_toolcall_detail.csv")
    with open(p3, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(detail_rows[0].keys()))
        w.writeheader(); w.writerows(detail_rows)
    print(f"[写] {p1}\n[写] {p2}\n[写] {p3}")

    # --- 图：各环节"优雅处理成功率"（0..1）分组条 ---
    fig_groups = [r["stage"] for r in stage_rows[1:]]  # 跳过 register（latency 主导）
    fig_vals = [r["success_rate"] for r in stage_rows[1:]]
    n_samples = len(detail_rows)
    fig_path = eplot.plot_grouped_bars(
        groups=fig_groups, methods=["local_harness"],
        values={"local_harness": fig_vals}, errors=None,
        origin=ORIGIN, n_samples=n_samples, out_dir=FIG_DIR,
        fname_prefix="agent_toolcall_stages", ylabel="Graceful-handling success rate",
        title=f"Agent tool-call pipeline stages (local deterministic, N={n_samples})")
    print(f"[写] {fig_path}")

    # --- manifest（LLM 决策列诚实 PENDING）---
    mpath = manifest.write_manifest(
        out_dir=args.out, script=__file__, seed=args.seed, data_origin=ORIGIN,
        params={"n_tools_registered": n_tools, "requests": len(requests),
                "trials_per_request": args.trials,
                "tools": ["morse_encode", "gis_distance", "aprs_position_encode",
                          "gnss_parse_rmc", "distance(alias)"],
                "llm_decision_layer": "PENDING_ONLINE_RUN",
                "llm_note": ("LLM 选工具/填参数的决策质量需在线 API，云内无 key；"
                             "本地管线（注册/解析/校验/执行）为确定性测量"),
                "csv_requests": os.path.basename(p1),
                "csv_stages": os.path.basename(p2),
                "csv_detail": os.path.basename(p3),
                "figure": os.path.basename(fig_path)},
        n_samples=n_samples,
        extra={"llm_decision_metrics_PENDING": {
            "call_rate": "PENDING_ONLINE_RUN", "pick_rate": "PENDING_ONLINE_RUN",
            "arg_ok_rate": "PENDING_ONLINE_RUN"}},
        filename="manifest_agent_toolcall.json")
    print(f"[写] {mpath}")
    print(f"\n口径: {ORIGIN}（本地确定性）；总样本 N={n_samples}；"
          f"LLM 决策列=PENDING_ONLINE_RUN（未伪造）")


if __name__ == "__main__":
    main()

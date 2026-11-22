#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""按观测档案时间轴列出 / 定位 / 试读回放步骤（只读，不碰设备）。

用法：
    python scripts/replay_observation.py <archive.observation.json> [--list]
        [--at SECONDS] [--read SECONDS] [--json]

``--read`` 只从录制的 SigMF 数据里读取并统计样本，不播音频、不打开设备。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.observation_record import load_record, replay_plan  # noqa: E402

_DASH = "—"


def _fmt_freq(value: Optional[float]) -> str:
    return _DASH if value is None else f"{value / 1e6:.3f}MHz"


def _fmt_gain(value: Optional[float]) -> str:
    return _DASH if value is None else f"{value:.1f}dB"


def _fmt_rate(value: Optional[float]) -> str:
    return _DASH if value is None else f"{value / 1e6:.3f}MS/s"


def _fmt_state(state: Dict[str, Any]) -> str:
    return (f"freq={_fmt_freq(state.get('center_freq_hz'))} "
            f"gain={_fmt_gain(state.get('gain_db'))} "
            f"sr={_fmt_rate(state.get('sample_rate_hz'))}")


def _read_stats(path: str, at_s: float, read_s: float,
                rate_hint: Optional[float]) -> Tuple[Optional[Dict[str, Any]],
                                                     Optional[Dict[str, Any]]]:
    """从真实 SigMF 数据读取一段并统计；失败返回 (None, 结构化原因)。"""
    from mbdsdr_ai.playback import IQPlayback

    pb = IQPlayback(loop=False)
    if not pb.open(path):
        return None, {
            "path": path,
            "error": "playback_open_failed",
            "error_code": pb.error_code or "open_failed",
            "reason": pb.last_error or "无法打开回放数据文件",
        }
    try:
        pb.seek(at_s)
        rate = pb.sample_rate if pb.sample_rate > 0 else (rate_hint or 0.0)
        if rate <= 0:
            return None, {
                "path": path,
                "error": "sample_rate_unknown",
                "reason": "采样率未知，无法把秒数换算成样本数",
            }
        n_req = int(read_s * rate)
        block = pb.read_samples(n_req, copy=True)
        n = int(block.size)
        if n == 0:
            return None, {
                "path": path,
                "error": "no_samples",
                "reason": pb.last_error or "该位置没有可读样本",
                "at_s": at_s,
                "requested": n_req,
                "state": pb.state,
            }
        import numpy as np

        mag = np.abs(block)
        rms = float(np.sqrt(np.mean(mag.astype(np.float64) ** 2)))
        peak = float(np.max(mag))
        return {
            "path": path,
            "at_s": at_s,
            "requested": n_req,
            "n": n,
            "rms_dbfs": 20.0 * math.log10(rms + 1e-12),
            "peak_dbfs": 20.0 * math.log10(peak + 1e-12),
            "state": pb.state,
        }, None
    finally:
        pb.close()


def _print_blockers(plan: Dict[str, Any]) -> None:
    if plan["blockers"]:
        print("blockers:")
        for b in plan["blockers"]:
            print(f"  - {b}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="按观测档案时间轴列出/定位/试读回放步骤")
    ap.add_argument("archive", help="*.observation.json 路径")
    ap.add_argument("--list", action="store_true",
                    help="打印步骤表（默认行为）")
    ap.add_argument("--at", type=float, default=None, metavar="SECONDS",
                    help="只显示该时刻生效的步骤")
    ap.add_argument("--read", type=float, default=None, metavar="SECONDS",
                    dest="read_s", help="从该位置读取指定时长的 IQ 并统计")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="以 JSON 输出 replay_plan 与读取统计")
    args = ap.parse_args(argv)

    record, err = load_record(args.archive)
    if record is None:
        payload = {"archive": args.archive, "error": "load_failed",
                   "reason": err}
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"读取档案失败：{err}")
        return 2

    plan = replay_plan(record)
    blocked = bool(plan["blockers"])

    stats: Optional[Dict[str, Any]] = None
    read_error: Optional[Dict[str, Any]] = None
    if args.read_s is not None:
        if not plan["data_paths"]:
            read_error = {"error": "no_data_path", "reason": "没有可读的 IQ 数据文件"}
        else:
            stats, read_error = _read_stats(
                plan["data_paths"][0], args.at if args.at is not None else 0.0,
                args.read_s, plan["sample_rate_hz"])

    if args.as_json:
        payload: Dict[str, Any] = {"archive": args.archive, "plan": plan}
        if stats is not None:
            payload["read"] = stats
        if read_error is not None:
            payload["read_error"] = read_error
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if (not blocked and read_error is None) else 2

    show_list = args.list or (args.at is None and args.read_s is None)
    if show_list:
        print("idx  t(s)  sample  kind  state")
        for i, step in enumerate(plan["steps"]):
            print(f"{i}  {step['at_s']:.6f}  {step['sample']}  "
                  f"{step['kind']}  {_fmt_state(step['state'])}")
        duration = (_DASH if plan["duration_s"] is None
                    else f"{plan['duration_s']:.6f}")
        total = (_DASH if plan["total_samples"] is None
                 else str(plan["total_samples"]))
        print(f"duration={duration}s total={total}")

    if args.at is not None:
        effective = None
        for i, step in enumerate(plan["steps"]):
            if step["at_s"] <= args.at:
                effective = (i, step)
        if effective is None:
            print(f"t={args.at:.6f}s 无生效步骤")
        else:
            i, step = effective
            print(f"t={args.at:.6f}s 生效步骤 idx={i} "
                  f"at_s={step['at_s']:.6f} sample={step['sample']} "
                  f"kind={step['kind']}")
            print(f"state={_fmt_state(step['state'])}")

    if stats is not None:
        print(f"read path={stats['path']} at={stats['at_s']:.6f}s "
              f"n={stats['n']} rms_dbfs={stats['rms_dbfs']:.4f} "
              f"peak_dbfs={stats['peak_dbfs']:.4f}")
    if read_error is not None:
        print(f"读取失败：{read_error.get('error')} "
              f"{read_error.get('reason', '')}".rstrip())
    if blocked:
        print(f"档案 {plan['observation_id']} 无法重演：")
        _print_blockers(plan)
    return 0 if (not blocked and read_error is None) else 2


if __name__ == "__main__":
    sys.exit(main())

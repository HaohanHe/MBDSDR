#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""R5 模拟替代路径（无真云台时可跑，结论恒标 ``SIMULATED``，不判 PASS）。

为什么存在：真机七环里 R5 在没接 rotctld/板载云台时只能 ``SKIP(no_gimbal)``，
但「云台接卫星」的指令链仍需可复跑的量化证据。本脚本用本地 rotctld 模拟
服务器（``rotctld_sim.py``）跑一条合成过境，给出：

* ``command_vs_truth`` 误差分布（max/mean/RMS，按来源分栏）；
* 50/200/500ms 三档延迟下的滞后角（指令角 vs 回读角）；
* 丢指令时的行为（是否停止、文案是否带原因）。

**口径红线**：模拟服务器只有「理想电机 + 传输延迟 + 量化 + 抖动」，
不含机械齿隙、风力、电机加减速曲线，所以：

1. 结论状态**永远**是 ``SIMULATED``，不计入 PASS，不得写进真机结论；
2. ``mechanical_accuracy_claimable`` 在模拟下**永远 False**，
   ``pose_error_by_source.readback`` 应为 0 样本；
3. 真机 R5 仍以 ``acceptance_suite.py`` 的只读回读为准。

单独跑：
    python scripts/hw_acceptance/ring5_gimbal_sim.py
    python scripts/hw_acceptance/ring5_gimbal_sim.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for p in (_HERE, _REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

from rotctld_sim import run_simulated_pass  # noqa: E402

STATUS_SIMULATED = "SIMULATED"
LATENCY_LEVELS = (0.05, 0.2, 0.5)


def _row(d: dict) -> str:
    def _f(v):
        return "—" if v is None else f"{v:.3f}"
    return (f"{int(d.get('samples', 0)):>3}  "
            f"{_f(d.get('max_deg')):>7}  {_f(d.get('mean_deg')):>7}  "
            f"{_f(d.get('rms_deg')):>7}")


def build_result(*, steps: int = 24) -> dict:
    """跑一条过境 + 三档延迟 + 丢指令，返回验收信封（status=SIMULATED）。"""
    distributions = {}
    for lat in LATENCY_LEVELS:
        distributions[lat] = run_simulated_pass(latency_s=lat, steps=steps)
    main = distributions[LATENCY_LEVELS[0]]
    dropped = run_simulated_pass(latency_s=0.05, steps=12,
                                 drop_every=5, drop_mode="rprt")

    by_src = main["pose_error_by_source"]
    readback_n = int((by_src.get("readback") or {}).get("samples", 0))
    if readback_n != 0:
        raise RuntimeError("模拟路径出现了 readback 样本——口径已污染，拒绝出结论")

    lags = {f"{int(lat * 1000)}ms": distributions[lat]["lag_cmd_vs_pose"]
            for lat in LATENCY_LEVELS}
    metrics = {
        "backend": "rotctld_sim（模拟服务器，非真机）",
        "simulated": True,
        "steps": steps,
        "command_vs_truth": main["cmd_vs_truth"],
        "pose_error_by_source": by_src,
        "pose_stats": main["pose_stats"],
        "lag_by_latency": lags,
        "dropped_command": {
            "attempted": dropped["commands_sent"],
            "dropped": dropped["dropped"],
            "stopped": dropped["stopped"],
            "error": dropped["error"],
            "samples_before_stop": dropped["cmd_vs_truth"]["samples"],
        },
        "mechanical_accuracy_claimable": False,
    }
    lag50 = lags["50ms"]["mean_deg"]
    lag500 = lags["500ms"]["mean_deg"]
    monotonic = (lag500 is not None and lag50 is not None and lag500 > lag50)
    checks = [
        {"name": "backend_is_simulated", "ok": False, "skipped": True,
         "detail": "rotctld_sim：只证指令链/量化/延迟，不含机械特性"},
        {"name": "no_readback_samples", "ok": False, "skipped": True,
         "detail": f"readback={readback_n} samples（模拟下必须为 0）"},
        {"name": "lag_monotonic_50_to_500ms", "ok": False, "skipped": True,
         "detail": f"mean lag 50ms={lag50:.3f}° → 500ms={lag500:.3f}°"
                   if (lag50 is not None and lag500 is not None)
                   else "滞后角不可测"},
        {"name": "drop_command_stops", "ok": False, "skipped": True,
         "detail": f"stopped={dropped['stopped']} error={dropped['error']}"},
    ]
    return {
        "ring": "R5",
        "name": "云台回读（模拟替代路径）",
        "status": STATUS_SIMULATED,
        "metrics": metrics,
        "checks": checks,
        "notes": ("SIMULATED：本机无真 rotctld/板载云台，走模拟服务器；"
                  "指令链与量化误差可复跑，但**不是真机结论**，不计 PASS。"
                  "mechanical_accuracy_claimable 在模拟下永远 False。"),
        "user_message_on_failure": "模拟路径异常（非硬件结论）",
        "artifacts": [],
        "device_required": False,
        "screenshots": [],
        "elapsed_ms": None,
    }


def _print_table(res: dict) -> None:
    m = res["metrics"]
    print("=" * 72)
    print("R5 模拟替代路径（rotctld_sim）—— 状态 SIMULATED，不计 PASS")
    print("=" * 72)
    print(f"{'来源':<10}{'样本':>4}  {'max°':>7}  {'mean°':>7}  {'RMS°':>7}")
    print(f"{'readback':<10}{0:>4}  {'—':>7}  {'—':>7}  {'—':>7}"
          "   ← 模拟下必须为 0")
    print(f"{'simulated':<10}{_row(m['pose_error_by_source']['simulated'])}")
    print(f"\n{'延迟档':<8}{'滞后mean°':>10}  {'滞后max°':>9}  {'RMS°':>7}")
    for k, v in m["lag_by_latency"].items():
        print(f"{k:<8}{v['mean_deg']:>10.3f}  {v['max_deg']:>9.3f}  {v['rms_deg']:>7.3f}")
    c = m["command_vs_truth"]
    print(f"\ncommand_vs_truth：samples={c['samples']} "
          f"max={c['max_deg']:.3f}° mean={c['mean_deg']:.3f}° rms={c['rms_deg']:.3f}°")
    d = m["dropped_command"]
    print(f"丢指令：attempted={d['attempted']} dropped={d['dropped']} "
          f"stopped={d['stopped']} 文案={d['error']}")
    print(res["notes"])


def main() -> int:
    ap = argparse.ArgumentParser(description="R5 模拟替代路径（SIMULATED，不计 PASS）")
    ap.add_argument("--json", action="store_true", help="只打印 JSON")
    ap.add_argument("--steps", type=int, default=24, help="过境拍数（默认 24）")
    args = ap.parse_args()
    t0 = time.perf_counter()
    res = build_result(steps=args.steps)
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

    root = os.environ.get("MBDSDR_HW_ACCEPTANCE_DIR") or os.path.join(
        _REPO, ".tmp-tool-results", "hw_acceptance",
        "r5_sim_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    res["artifacts"] = [os.path.join(root, "result.json")]

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _print_table(res)
        print(f"\n证据：{os.path.join(root, 'result.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

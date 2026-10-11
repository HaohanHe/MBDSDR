#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""真机在环预案演练：用合成占位 sweep.json 走「回传 -> vna_rf 复算 -> 填充」流程。

【合成占位】本脚本默认用 nanovna_dump --selftest 的合成数据占位，仅验证流程通顺，
不代表真实测量值。真机到手后：
    python3 -m mbdsdr_ai.nanovna_dump --port /dev/ttyACM0 --json-pretty --out sweep.json
再把下面 demo() 的占位换成读 sweep.json 即可，字段映射不变。
"""
from __future__ import annotations
import json
from .nanovna_dump import run_dump
from .nanovna_client import vswr, return_loss_db


def analyze_resonance_py(freqs_hz, s11):
    """Python 侧轻量谐振分析（与 C++ vna_rf 同公式、独立实现）：
    |Z|极小点为 fr，半功率(-3dB)带宽 BW，fr 处 |Z| 近似 ESR。合成占位用。"""
    import math
    z = [abs(complex(*s)) for s in s11]
    i = min(range(len(z)), key=lambda k: z[k])
    fr = freqs_hz[i]
    esr = z[i]
    # 半功率：|Z| 峰值 / 谷值间，找两边 |Z| 升到谷的 1.414x（-3dB）点
    target = esr * math.sqrt(2.0)
    lo = next((freqs_hz[k] for k in range(i - 1, -1, -1) if z[k] >= target), freqs_hz[0])
    hi = next((freqs_hz[k] for k in range(i, len(z)) if z[k] >= target), freqs_hz[-1])
    bw = max(0.0, hi - lo)
    q = fr / bw if bw > 0 else float("nan")
    return {"fr_hz": fr, "bw_hz": bw, "q": q, "esr": esr}


def demo() -> dict:
    # 合成占位（selftest 数据）：走完整 dump 流程
    import tempfile, os
    from . import nanovna_dump as nd
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(nd._selftest_script(430_000_000, 440_000_000, 101), f)
    data = run_dump("", 430_000_000, 440_000_000, 101, replay=path)

    # 复算示例：最低 VSWR 点（evidence 数值列之一）
    vswrs = [vswr(complex(*s)) for s in data["S11"]]
    i = min(range(len(vswrs)), key=lambda k: vswrs[k])
    res = analyze_resonance_py(data["frequencies_hz"], data["S11"])
    return {
        "_MARKER": "SYNTHETIC_PLACEHOLDER_真机替换",
        "频率": f"{data['frequencies_hz'][i]/1e6:.3f} MHz",
        "VSWR": round(vswrs[i], 2),
        "回波损耗_dB": round(return_loss_db(complex(*data["S11"][i])), 2),
        "谐振_fr_MHz": round(res["fr_hz"] / 1e6, 4),
        "谐振_Q": round(res["q"], 2),
        "谐振_ESR_ohm": round(res["esr"], 2),
    }


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))

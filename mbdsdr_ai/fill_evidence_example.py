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
import math


def tdr_length_py(freqs_hz, s11, vf=0.66):
    """Python 侧 TDR 近似：反射峰位置 -> 电缆长度 m（vf 默认 RG-58 0.66）。
    与 C++ vna_rf::tdrCable 同思路，独立实现。合成占位。"""
    c = 299792458.0
    mag = [abs(complex(*s)) for s in s11]
    k = max(range(len(mag)), key=lambda i: mag[i])
    n = len(freqs_hz)
    span = freqs_hz[-1] - freqs_hz[0]
    if span <= 0:
        return 0.0
    t_roundtrip = (k / n) / span
    return vf * c * t_roundtrip / 2.0


def reactance_lc_py(freq_hz, xs):
    """Z 虚部 -> 串感 nH / 并容 pF（与 vna::reactanceToHenries/Farads 同公式）。"""
    if abs(xs) < 1e-9:
        return (0.0, 0.0)
    if xs > 0:
        return (xs / (2 * math.pi * freq_hz) * 1e9, 0.0)
    return (0.0, -1.0 / (2 * math.pi * freq_hz * xs) * 1e12)


def filter_shape_py(s21_db):
    """四形态判定（带通/带阻/高通/低通）。同 vna::analyzeFilter 思路，占位。"""
    peak = max(s21_db)
    n = len(s21_db)
    low = min(s21_db[: max(1, n // 4)])
    high = min(s21_db[-max(1, n // 4):])
    if peak - low > peak - high:
        return "bandstop" if low > high else "lowpass"
    return "bandpass" if high < low else "highpass"


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
    length = tdr_length_py(data["frequencies_hz"], data["S11"])
    fmid = data["frequencies_hz"][len(data["frequencies_hz"]) // 2]
    xs_mid = complex(*data["S11"][len(data["S11"]) // 2]).imag
    lnh, cpf = reactance_lc_py(fmid, xs_mid)
    from .nanovna_client import s21_gain_db
    fshape = filter_shape_py([s21_gain_db(complex(*s)) for s in data["S21"]])
    return {
        "_MARKER": "SYNTHETIC_PLACEHOLDER_真机替换",
        "频率": f"{data['frequencies_hz'][i]/1e6:.3f} MHz",
        "VSWR": round(vswrs[i], 2),
        "回波损耗_dB": round(return_loss_db(complex(*data["S11"][i])), 2),
        "谐振_fr_MHz": round(res["fr_hz"] / 1e6, 4),
        "谐振_Q": round(res["q"], 2),
        "谐振_ESR_ohm": round(res["esr"], 2),
        "电缆长度_m": round(length, 3),
        "L_nH": round(lnh, 3), "C_pF": round(cpf, 3),
        "滤波器形态": fshape,
    }


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))

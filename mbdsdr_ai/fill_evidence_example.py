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
import math

import numpy as np

from .nanovna_dump import run_dump
from .nanovna_client import s11_phase_deg, s21_phase_deg, vswr, return_loss_db

# TDR 窗三档（官方 Start measurement 页：DISPLAY->TRANSFORM 的窗口 MIN/NORMAL/MAX）。
# MIN=矩形（最高分辨率）/ NORMAL=Kaiser beta=6（默认）/ MAX=Kaiser beta=13（最高动态）。
TDR_WINDOWS = {"rect": 0.0, "kaiser6": 6.0, "kaiser13": 13.0}


def tdr_length_py(freqs_hz, s11, vf=0.66, window="kaiser6"):
    """Python 侧 TDR：窗口化 IDFT -> |时域响应| 峰位 -> 电缆长度 m。

    与官方 bandpass TDR 机制对齐的三档窗（rect/kaiser6/kaiser13，默认 NORMAL）；
    对复 S11 施加频域窗后 IDFT 取峰（跳过 0 延迟 bin——那是参考面本身）。
    仍是轻量实现：无官方 2x 共轭对称扩展与双曲插值峰值精修
    （feature-parity.md 第 6 节登记的精度欠账），物理口径一致。
    vf 默认 RG-58 0.66（官方页面按百分数输入速度因数，同一物）。"""
    c = 299792458.0
    n = len(freqs_hz)
    if n < 4 or not s11 or len(s11) != n:
        return 0.0
    span = freqs_hz[-1] - freqs_hz[0]
    if span <= 0:
        return 0.0
    if window not in TDR_WINDOWS:
        raise ValueError(
            f"unknown window {window!r}：官方三档 rect/kaiser6/kaiser13")
    x = np.array([complex(*s) for s in s11], dtype=complex)
    beta = TDR_WINDOWS[window]
    if beta > 0:
        x = x * np.kaiser(n, beta)
    mag = np.abs(np.fft.ifft(x))
    # 跳过 bin 0（t=0 参考面，含直流分量，不是远端反射峰）
    k = int(np.argmax(mag[1:])) + 1
    t_roundtrip = k / span
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
    if not freqs_hz or not s11 or len(freqs_hz) != len(s11):
        return {"fr_hz": 0.0, "bw_hz": 0.0, "q": 0.0, "esr": 0.0}
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
    return compute_evidence(data)


def compute_evidence(data):
    """从已加载的 sweep dict（nanovna_dump --out 结构）复算六列证据行。"""
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


def _load_sweep(path):
    d = json.load(open(path, encoding="utf-8"))
    for k in ("frequencies_hz", "S11", "S21"):
        if k not in d:
            raise SystemExit(f"sweep.json 缺字段 {k}（应来自 nanovna_dump --out）")
    if not d["frequencies_hz"] or not d["S11"]:
        raise SystemExit("sweep.json 空数据，无法复算")
    return d


def main(argv=None):
    import argparse, csv, sys
    ap = argparse.ArgumentParser(description="回传 sweep.json -> 六列证据表")
    ap.add_argument("--input", default=None, help="真机 sweep.json（缺省走合成占位演示）")
    ap.add_argument("--out", default="-", help="输出路径（缺省 stdout）")
    ap.add_argument("--format", default="json", choices=["json", "csv"])
    ap.add_argument("--json-pretty", action="store_true")
    a = ap.parse_args(argv)

    if a.input:
        row = compute_evidence(_load_sweep(a.input))
        row.pop("_MARKER", None)   # 真机真实数据，不再是占位
    else:
        row = demo()

    if a.format == "csv":
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(row.keys()); w.writerow(row.values())
        text = buf.getvalue()
    else:
        text = json.dumps(row, ensure_ascii=False,
                          indent=2 if a.json_pretty else None)

    if a.out == "-":
        print(text)
    else:
        open(a.out, "w", encoding="utf-8").write(text)
        print(f"已写出 {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

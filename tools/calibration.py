#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""MBDSDR 接收链校准工具：频率 PPM / 电平 dBFS（纯 stdlib+numpy，可复用层）。

红线：
  * 参考频率/参考电平全部由调用方**参数化传入**，本工具不内置任何默认台。
  * 无设备/空 IQ 时诚实返回 FAIL + 下一步提示，绝不 mock 校准结果。
  * 校准结果写 JSON（路径参数化）。

用法（CLI）：
  python3 tools/calibration.py freq --iq rec.iq --ref-hz 437.5e6 --fs 4800000 \
      --cal-json /tmp/cal.json
  python3 tools/calibration.py level --iq rec.iq --ref-dbfs -6.0 \
      --cal-json /tmp/cal.json
"""
from __future__ import annotations
import argparse
import json
import sys
import numpy as np


def measure_peak_hz(iq: np.ndarray, fs: float) -> float:
    """复 IQ → FFT → 最强谱线中心频率 Hz（实/复谱取全局峰）。"""
    z = np.asarray(iq, dtype=np.complex64)
    if z.size == 0 or fs <= 0:
        return float("nan")
    spec = np.abs(np.fft.fftshift(np.fft.fft(z * np.hanning(z.size))))
    freqs = np.fft.fftshift(np.fft.fftfreq(z.size, d=1.0 / fs))
    return float(freqs[int(np.argmax(spec))])


def ppm_correction(measured_hz: float, ref_hz: float) -> dict:
    """由实测峰频 vs 已知参考频算 PPM 修正。

    ppm = offset_hz / ref_hz * 1e6。接收机 LO 偏差 = 测得 - 参考；修正值为其相反数。
    ref_hz 必须 > 0 且由调用方传入（本工具不内置任何台）。
    """
    if ref_hz <= 0:
        raise ValueError("ref_hz 必须 > 0（参考频率由调用方传入，本工具不内置）")
    offset = measured_hz - ref_hz
    ppm = offset / ref_hz * 1e6
    return {"ref_hz": ref_hz, "measured_peak_hz": measured_hz,
            "offset_hz": offset, "ppm": ppm, "correction_hz": -offset}


def measure_dbfs(iq: np.ndarray) -> float:
    """复 IQ → RMS → dBFS（满幅=0 dBFS）。"""
    z = np.asarray(iq, dtype=np.complex64)
    if z.size == 0:
        return float("nan")
    rms = float(np.sqrt(np.mean(np.abs(z) ** 2)))
    if rms <= 0:
        return -float("inf")
    return 20.0 * np.log10(rms)


def level_offset_db(measured_dbfs: float, ref_dbfs: float) -> dict:
    """测得 dBFS vs 已知参考 dBFS → 电平偏差 dB（供增益/衰减修正）。"""
    delta = measured_dbfs - ref_dbfs
    return {"ref_dbfs": ref_dbfs, "measured_dbfs": measured_dbfs,
            "level_offset_db": delta, "gain_correction_db": -delta}


def _read_iq(path: str) -> np.ndarray:
    return np.fromfile(path, dtype=np.complex64)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 频率/电平校准（参数化参考，无硬编码）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freq", help="频率校准：测频偏→PPM")
    f.add_argument("--iq", required=True)
    f.add_argument("--ref-hz", type=float, required=True,
                   help="已知参考信号频率 Hz（调用方传入，工具不内置默认台）")
    f.add_argument("--fs", type=float, required=True)
    f.add_argument("--cal-json", default="")

    l = sub.add_parser("level", help="电平校准：dBFS 标定")
    l.add_argument("--iq", required=True)
    l.add_argument("--ref-dbfs", type=float, required=True,
                   help="已知参考电平 dBFS（调用方传入）")
    l.add_argument("--cal-json", default="")

    a = ap.parse_args(argv)
    result: dict = {"status": "FAIL"}

    try:
        iq = _read_iq(a.iq)
    except OSError as e:
        result["message"] = f"读 IQ 失败: {e}"
        result["next"] = "先录制已知参考信号 IQ"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    if iq.size == 0:
        result["message"] = "IQ 为空（诚实空态，不伪造校准）"
        result["next"] = "录制一段含已知参考信号的 IQ"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    try:
        if a.cmd == "freq":
            peak = measure_peak_hz(iq, a.fs)
            result = {"status": "PASS", **ppm_correction(peak, a.ref_hz)}
        else:
            dbfs = measure_dbfs(iq)
            result = {"status": "PASS", **level_offset_db(dbfs, a.ref_dbfs)}
    except ValueError as e:
        result["status"] = "FAIL"
        result["message"] = str(e)
        result["next"] = "检查参考参数是否合法（ref_hz>0）"

    if a.cal_json:
        with open(a.cal_json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: MIT
"""D2 金标复现：固定输入 → 固定输出（ADS-B / APRS）。

只读随仓 fixture（不联网、不加依赖），把两条解码链的结果压缩成固定字段，
用 `json.dumps(..., sort_keys=True)` 输出稳定 JSON。若与内嵌金标不一致，
打印**首个分歧字段**并以非零退出。

用法：
    python scripts/decode_golden.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "decode_pipeline")
sys.path.insert(0, ROOT)

# 固定输入与期望值（与 manifest.json 同一事实源；fixture 为仓内合成，
# 但输入字节固定，所以输出必须逐字段可复现）。
GOLDEN: dict = {
    "adsb": {
        "fixture": "adsb_three_frames.cf32",
        "fs_hz": 8000000.0,
        "target_fs_hz": 4000000.0,
        "expected": {
            "ok": True,
            "status": "decoded",
            "error_code": "",
            "num_frames": 3,
            "frames_hex": [
                "8f4d2023587f345e35837e2218b2",
                "8d4d2023991094ad487c14fc9e3d",
                "8f4d20232004d0f4cb1820000d24",
            ],
            "icaos": ["4D2023", "4D2023", "4D2023"],
            "callsigns": [None, None, "AMC421"],
        },
    },
    "aprs": {
        "fixture": "aprs_two_frames.cf32",
        "fs_hz": 96000.0,
        "target_fs_hz": 48000.0,
        "expected": {
            "ok": True,
            "status": "decoded",
            "error_code": "",
            "num_frames": 2,
            "frames_hex": [
                "82a0a4a64040e084926282828262ae92888a624062ae92888a64406503f0"
                "21333730302e30304e2f31323230302e3030572d73796e74686574696320"
                "61783235206f6e6599d5",
                "82a0a4a64040e08492628484847303f021333935342e30304e2f31313632"
                "342e3030452d73796e74686574696320617832352074776f257f",
            ],
            "sources": ["BI1AAA-1", "BI1BBB-9"],
            "info_hex": [
                "21333730302e30304e2f31323230302e3030572d73796e74686574696320"
                "61783235206f6e65",
                "21333935342e30304e2f31313632342e3030452d73796e74686574696320"
                "617832352074776f",
            ],
        },
    },
}


def _case_path(name: str) -> str:
    return os.path.join(FIXTURE_DIR, GOLDEN[name]["fixture"])


def decode_adsb() -> dict:
    """ADS-B 固定样本 → 固定字段（不含耗时/样本数等环境相关量）。"""
    from mbdsdr_ai.decode_pipeline import run_adsb_pipeline

    meta = GOLDEN["adsb"]
    r = run_adsb_pipeline(_case_path("adsb"), fs=meta["fs_hz"],
                          target_fs=meta["target_fs_hz"])
    frames = (r.get("content") or {}).get("frames") or []
    return {
        "ok": bool(r.get("ok")),
        "status": str(r.get("status") or ""),
        "error_code": str(r.get("error_code") or ""),
        "num_frames": int(r.get("num_frames") or 0),
        "frames_hex": [str(f.get("hex") or "") for f in frames],
        "icaos": [str(f.get("icao") or "") for f in frames],
        "callsigns": [f.get("callsign") for f in frames],
    }


def decode_aprs() -> dict:
    """APRS 固定样本 → 固定字段（不输出阶段耗时等环境相关量）。"""
    from mbdsdr_ai.decode_pipeline import run_aprs_pipeline

    meta = GOLDEN["aprs"]
    r = run_aprs_pipeline(_case_path("aprs"), fs=meta["fs_hz"],
                          target_fs=meta["target_fs_hz"])
    frames = (r.get("content") or {}).get("frames") or []
    return {
        "ok": bool(r.get("ok")),
        "status": str(r.get("status") or ""),
        "error_code": str(r.get("error_code") or ""),
        "num_frames": int(r.get("num_frames") or 0),
        "frames_hex": [str(f.get("frame_hex") or "") for f in frames],
        "sources": [
            f"{f.get('source')}-{f.get('source_ssid')}" for f in frames
        ],
        "info_hex": [str(f.get("info_hex") or "") for f in frames],
    }


def build_report() -> dict:
    """两次固定解码的结果；键顺序由 sort_keys 固定。"""
    return {"schema": 1, "cases": {"adsb": decode_adsb(), "aprs": decode_aprs()}}


def first_divergence(expected: Any, actual: Any,
                     prefix: str = "") -> Optional[Tuple[str, Any, Any]]:
    """递归找首个分歧，返回 (字段路径, 期望, 实际)；完全一致返回 None。"""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in expected:
                return path, "<缺失>", actual[key]
            if key not in actual:
                return path, expected[key], "<缺失>"
            diff = first_divergence(expected[key], actual[key], path)
            if diff:
                return diff
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"{prefix}.length", len(expected), len(actual)
        for i, (e, a) in enumerate(zip(expected, actual)):
            diff = first_divergence(e, a, f"{prefix}[{i}]")
            if diff:
                return diff
        return None
    if expected != actual:
        return prefix, expected, actual
    return None


def main(argv: Optional[List[str]] = None) -> int:
    report = build_report()
    expected = {name: meta["expected"] for name, meta in GOLDEN.items()}
    diff = first_divergence(expected, report["cases"], "cases")
    if diff:
        field, want, got = diff
        print(f"首个分歧字段: {field}")
        print(f"期望: {want!r}")
        print(f"实际: {got!r}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

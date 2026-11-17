#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""S0 设备探针：把「这台手机到底能干什么」变成可核对的事实。

能力契约与镜头意图裁决在 ``mbdsdr_ai/camera_capability.py``（探针与采集
导演共用同一份，避免两套判断给出两种答案）。本脚本只负责两件事：

  1. **真机原始证据采集（adb）**——只落原始文本，不做投机解析。
     dumpsys 的格式随 Android 版本与厂商变化，在没有真机输出之前写解析器
     就是编造；原始件落盘后，解析器按真格式补。
  2. CLI 入口：collect / intent / fov。

CLI:
  python3 tools/probe_device.py collect --out <dir>
  python3 tools/probe_device.py intent --profile <json> --want periscope \
      --aperture-mm 90 --magnification 40 --eyepiece-afov-deg 52 --true-field-deg 1.2
  python3 tools/probe_device.py fov --sep-deg 5.0 --p1 100,200 --p2 600,200 --axis-px 4000
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mbdsdr_ai.camera_capability import (  # noqa: E402
    CAN, CANNOT, UNKNOWN, TRI_STATES, ROLES,
    PROVENANCE_MEASURED, PROVENANCE_RAW, PROVENANCE_SYNTHETIC, PROVENANCES,
    VERDICT_ALLOW, VERDICT_WARN, VERDICT_NEEDS_PROBE, VERDICT_REFUSE,
    CameraCapability, DeviceProfile, validate_profile,
    exit_pupil_mm, effective_focal_mm, captured_true_field_deg,
    fov_from_star_pair, check_lens_intent,
)


# ---------------------------------------------------------------------------
# 真机原始证据采集（不解析）
# ---------------------------------------------------------------------------
_DUMPS = (
    ("media_camera", ("shell", "dumpsys", "media.camera")),
    ("sensorservice", ("shell", "dumpsys", "sensorservice")),
    ("camera_characteristics", ("shell", "dumpsys", "cameraservice")),
)


def _run(cmd: Sequence[str], timeout: float = 25.0) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def parse_adb_devices(text: str) -> List[str]:
    """``adb devices`` 输出里在线设备的序列号。"""
    out: List[str] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def collect_raw(out_dir: Path, run=_run) -> Dict[str, object]:
    """把真机原始输出落盘。没有设备就诚实 SKIP，不生成假档案。"""
    try:
        r = run(("adb", "devices"))
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"status": "SKIP", "reason": f"adb 不可用: {exc}"}
    online = parse_adb_devices(r.stdout or "")
    if not online:
        return {"status": "SKIP", "reason": "没有在线 adb 设备（USB 调试是否打开？）"}

    serial = online[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    files: List[str] = []
    for name, sub in _DUMPS:
        rr = run(("adb", "-s", serial, *sub))
        p = out_dir / f"{name}.txt"
        p.write_text(rr.stdout or "", encoding="utf-8")
        files.append(str(p))

    try:
        model = (run(("adb", "-s", serial, "shell", "getprop", "ro.product.model")).stdout or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        model = ""
    meta = {
        "schema_version": "device_probe_raw/1",
        "serial": serial,
        "model": model,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": PROVENANCE_RAW,
        "parsed": False,
        "note": "原始证据已落盘；解析器等真机格式确定后补，不凭想象写 dumpsys 解析",
        "files": [Path(f).name for f in files],
    }
    (out_dir / "probe_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(str(out_dir / "probe_meta.json"))
    return {"status": "PASS", "serial": serial, "files": files}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="S0 设备探针（能力档案 / 意图裁决 / 视场反算）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_collect = sub.add_parser("collect", help="采集真机原始 dumpsys（无设备则 SKIP）")
    p_collect.add_argument("--out", required=True)

    p_intent = sub.add_parser("intent", help="裁决一次镜头意图")
    p_intent.add_argument("--profile", required=True)
    p_intent.add_argument("--want", required=True, choices=ROLES)
    p_intent.add_argument("--aperture-mm", type=float)
    p_intent.add_argument("--magnification", type=float)
    p_intent.add_argument("--eyepiece-afov-deg", type=float)
    p_intent.add_argument("--true-field-deg", type=float)

    p_fov = sub.add_parser("fov", help="用一对已知角距的星反算视场")
    p_fov.add_argument("--sep-deg", type=float, required=True)
    p_fov.add_argument("--p1", required=True)
    p_fov.add_argument("--p2", required=True)
    p_fov.add_argument("--axis-px", type=float)

    args = ap.parse_args(argv)

    if args.cmd == "collect":
        res = collect_raw(Path(args.out))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["status"] == "PASS" else 2

    if args.cmd == "intent":
        profile = DeviceProfile.from_dict(json.loads(Path(args.profile).read_text(encoding="utf-8")))
        res = check_lens_intent(profile, args.want, aperture_mm=args.aperture_mm,
                                magnification=args.magnification,
                                eyepiece_afov_deg=args.eyepiece_afov_deg,
                                true_field_deg=args.true_field_deg)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["verdict"] in (VERDICT_ALLOW, VERDICT_WARN) else 1

    if args.cmd == "fov":
        def _pt(s: str) -> Tuple[float, float]:
            a, b = s.split(",")
            return float(a), float(b)

        res = fov_from_star_pair(args.sep_deg, _pt(args.p1), _pt(args.p2), args.axis_px)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    return 3


if __name__ == "__main__":
    sys.exit(main())

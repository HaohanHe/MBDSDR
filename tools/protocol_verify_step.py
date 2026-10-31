#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""acceptance_run.sh 的协议级解码验证步（P0-6 验收脊柱接入真机演练清单）。

两个子步，红线与 acceptance_lib 一致（禁 mock、诚实 SKIP）：
  golden : 合成 ASTU-1 golden（tests/golden/astu1_tm_synthetic_golden.json，
           provenance=synthetic_encoder_generated）跑 verify_sample 全链——
           纯软件无硬件依赖，任何环境都真跑，FAIL 即真 FAIL；
  ota    : 真实 OTA 录制件（--iq complex64 raw + --layout + --assertions）
           跑同一验证链；文件缺失/为空 → SKIP + 原因（绝不假采集假通过）。

CLI（acceptance_run.sh 通过子命令调用，也可手动调试）：
  python3 tools/protocol_verify_step.py golden
  python3 tools/protocol_verify_step.py ota --iq <complex64> --layout \
         astu_1_tm_example --assertions <json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests" / "golden" / "astu1_tm_synthetic_golden.json"


def _emit(step: str, status: str, **kw) -> int:
    titles = {
        "protocol_golden": "协议级解码验证链 · 合成 golden 回归（无硬件依赖）",
        "protocol_ota": "协议级解码验证链 · 真实 OTA 录制件",
    }
    print(json.dumps({"id": step, "title": titles.get(step, step),
                      "status": status, **kw}, ensure_ascii=False))
    return {"PASS": 0, "FAIL": 1, "SKIP": 2}.get(status, 3)


def run_golden() -> int:
    if not GOLDEN.is_file():
        return _emit("protocol_golden", "FAIL",
                     reason=f"golden 缺失: {GOLDEN}（不该发生——入库文件）")
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    if doc.get("sample_provenance") != "synthetic_encoder_generated":
        return _emit("protocol_golden", "FAIL",
                     reason="golden provenance 标注被篡改（诚实纪律钉扎）")
    try:
        from mbdsdr_ai.verify_chain import verify_sample
        vals = doc["field_values"]
        assertions = [
            {"field": "battery_mv", "op": "eq", "value": vals["battery_mv"]},
            {"field": "mode", "op": "eq", "value": vals["mode"]},
            {"field": "seq", "op": "eq", "value": vals["seq"]},
            {"field": "callsign", "op": "eq", "value": vals["callsign"]},
        ]
        r = verify_sample(doc["bits"], layout_name=doc["layout"],
                          profile_name=doc["profile"],
                          assertions=assertions)
        if r["overall_pass"] and r["crc"]["ok"]:
            return _emit("protocol_golden", "PASS",
                         detail="合成 golden 全链（ASM→CRC→字段→断言）PASS",
                         n_pass=r["n_pass"],
                         provenance=doc["sample_provenance"])
        return _emit("protocol_golden", "FAIL",
                     detail=f"断言 {r['n_fail']} 条失败", result=r)
    except Exception as e:  # noqa: BLE001
        return _emit("protocol_golden", "FAIL",
                     reason=f"验证链异常: {type(e).__name__}: {e}")


def run_ota(args) -> int:
    iq_path = Path(args.iq) if args.iq else None
    if iq_path is None or not iq_path.is_file():
        return _emit("protocol_ota", "SKIP",
                     reason=("未提供真实 OTA 录制件（--iq complex64 raw）——"
                             "合成 golden 步已覆盖协议链正确性；真机样本"
                             "录到后传入即真跑，不 mock"))
    try:
        import numpy as np
        from mbdsdr_ai.verify_chain import verify_sample
        from mbdsdr_ai.sat_profile import (load_sat_profile,
                                           profile_to_rx_chain_args)
        from mbdsdr_ai.modulation import demodulate_iq
        iq = np.fromfile(iq_path, dtype=np.complex64)
        if iq.size == 0:
            return _emit("protocol_ota", "SKIP",
                         reason=f"{iq_path} 为空（诚实空态，不伪造样本）")
        layout = args.layout
        prof = load_sat_profile(args.profile) if args.profile else None
        dem_args = profile_to_rx_chain_args(prof) if prof else {}
        dem = demodulate_iq(
            iq, scheme=str(dem_args.get("scheme", "bpsk")),
            sps=int(dem_args.get("sps", 4)),
            shaping="rrc", alpha=float(dem_args.get("alpha", 0.35)))
        assertions = json.loads(args.assertions) if args.assertions else None
        r = verify_sample(dem["bits"], layout_name=layout,
                          profile_name=args.profile,
                          assertions=assertions)
        status = "PASS" if (r["overall_pass"] and r["crc"]["ok"]) else "FAIL"
        return _emit("protocol_ota", status,
                     detail=(f"解调 {len(dem['bits'])} bit → "
                             f"断言 PASS {r['n_pass']} / FAIL {r['n_fail']}"),
                     n_bits=len(dem["bits"]))
    except Exception as e:  # noqa: BLE001
        return _emit("protocol_ota", "FAIL",
                     reason=f"OTA 验证链异常: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("golden", help="合成 golden 协议链回归（无硬件依赖）")
    po = sub.add_parser("ota", help="真实 OTA 录制件协议链验证（缺样本 SKIP）")
    po.add_argument("--iq", help="complex64 raw IQ 录制件路径")
    po.add_argument("--layout", default="astu_1_tm_example")
    po.add_argument("--profile", default=None)
    po.add_argument("--assertions", default=None,
                    help="JSON 字符串：[{field,op,value}]")
    args = ap.parse_args()
    if args.mode == "golden":
        return run_golden()
    return run_ota(args)


if __name__ == "__main__":
    sys.exit(main())

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


def demo() -> dict:
    # 合成占位（selftest 数据）：走完整 dump 流程
    import tempfile, os
    from . import nanovna_dump as nd
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(nd._selftest_script(430_000_000, 440_000_000, 101), f)
    data = run_dump("", 430_000_000, 440_000_000, 101, replay=path)

    # 复算示例：最低 VSWR 点（evidence 数值列之一）
    import cmath
    vswrs = [vswr(complex(*s)) for s in data["S11"]]
    i = min(range(len(vswrs)), key=lambda k: vswrs[k])
    return {
        "_MARKER": "SYNTHETIC_PLACEHOLDER_真机替换",
        "频率": f"{data['frequencies_hz'][i]/1e6:.3f} MHz",
        "VSWR": round(vswrs[i], 2),
        "回波损耗_dB": round(return_loss_db(complex(*data["S11"][i])), 2),
    }


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))

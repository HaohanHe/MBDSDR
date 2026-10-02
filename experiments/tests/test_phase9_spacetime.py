# SPDX-License-Identifier: MIT
"""
Phase9 P3：新时空演示脚本 (experiments/demo_spacetime.py) 确定性测试
=====================================================================

断言一条命令跑通的整条时空链：
  注入固定 GNSS 时间 -> SigMF(time_source=gnss) -> 含多普勒信号 NCO 补偿 ->
  "解码"(残余频偏回零) -> 带时空标注的图产物。

红线验证：
  * 写出去的 SigMF meta 必须 time_source=="gnss" 且 core:datetime == 注入的固定 UTC；
    回放端 parse_sigmf_meta 读回同一字段（与桌面 C++ 同一约定）。
  * 无 GNSS 时的诚实退化必须是 time_source=="system"（绝不伪装成 gnss）。
  * 补偿前残余频偏 ≈ 注入多普勒(+150 Hz)，补偿后 ≈ 0 -> decode_ok=True。
  * 全固定种子：两次 run 摘要 JSON 逐字段一致。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from experiments import demo_spacetime as E  # noqa: E402


def test_demo_pipeline_products(tmp_path):
    out = str(tmp_path / "run")
    rc = E.main(["--out", out])
    assert rc == 0

    # SigMF 产物存在
    data = os.path.join(out, "demo_spacetime.sigmf-data")
    meta = os.path.join(out, "demo_spacetime.sigmf-meta")
    assert os.path.exists(data) and os.path.getsize(data) > 0
    assert os.path.exists(meta)

    # 回放端读回 meta：time_source/datetime 与注入一致
    from mbdsdr_ai import playback
    m = playback.parse_sigmf_meta(meta)
    assert m["time_source"] == "gnss"
    assert m["datetime"] == "2026-10-02T07:25:45Z"
    assert m["sample_rate_hz"] == E.FS_HZ

    # 摘要 JSON：多普勒补偿把残余频偏拉回 0
    with open(os.path.join(out, "spacetime_demo.json"), encoding="utf-8") as f:
        s = json.load(f)
    assert s["time_source"] == "gnss"
    assert s["capture_datetime"] == "2026-10-02T07:25:45Z"
    assert s["doppler_injected_hz"] == 150.0
    assert s["doppler_compensated"] is True
    assert s["residual_offset_before_hz"] == pytest.approx(150.0, abs=6.0)
    assert s["residual_offset_after_hz"] == pytest.approx(0.0, abs=6.0)
    assert s["decode_ok"] is True

    # 诚实退化：无 GNSS -> system，绝不写成 gnss
    fb = s["fallback_when_no_gnss"]
    assert fb["time_source"] == "system"
    assert fb["time_source"] != "gnss"

    # 图产物存在且非空
    fig = os.path.join(out, s["figure"])
    assert os.path.exists(fig) and os.path.getsize(fig) > 1000


def test_demo_deterministic(tmp_path):
    out1 = str(tmp_path / "a")
    out2 = str(tmp_path / "b")
    assert E.main(["--out", out1]) == 0
    assert E.main(["--out", out2]) == 0
    with open(os.path.join(out1, "spacetime_demo.json"), encoding="utf-8") as f:
        a = json.load(f)
    with open(os.path.join(out2, "spacetime_demo.json"), encoding="utf-8") as f:
        b = json.load(f)
    # 全部数值/标注逐字段一致（无墙钟/git sha 漂移）
    assert a == b

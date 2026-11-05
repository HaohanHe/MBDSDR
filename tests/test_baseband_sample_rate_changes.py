# SPDX-License-Identifier: MIT
"""
tests/test_baseband_sample_rate_changes.py
==========================================
S4：录音中改采样率 → 分段落盘 / sidecar 分段读取。

读取端契约：
  1. 分段落盘（sidecar 带 ``segments``）：返回每段路径/速率/样本/时长，
     本段 ``duration_s`` 精确，``session_duration_s`` 是各段真实时长之和；
  2. ``sample_rate_changes`` 带 ``from_sample_rate``/``start_sample``：
     按变更边界重建段表，``duration_s`` 为整场真实总和（不是末次速率）；
  3. 旧版只有 ``time_s``/``sample_rate`` 的混率文件：初始速率与样本边界
     不可重建，``duration_s`` 必须为 None，绝不按末次速率编造数字。
"""
import json
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import baseband_io  # noqa: E402


def _write_iq(path, n):
    """写 n 个复数样本的 cf32 文件。"""
    arr = np.ones(n, dtype=np.complex64) * (1.0 + 0.5j)
    raw = np.empty(n * 2, dtype=np.float32)
    raw[0::2] = arr.real
    raw[1::2] = arr.imag
    raw.tofile(str(path))


def _write_meta(path, meta):
    with open(str(path) + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def test_legacy_sample_rate_changes_has_no_fabricated_duration(tmp_path):
    """旧格式混率文件：缺初始速率/边界 → duration_s 必须是 None（不是 0.059）。"""
    p = tmp_path / "legacy_mixed.iq"
    _write_iq(p, 120000)
    changes = [
        {"time_s": 10, "sample_rate": 2048000},
        {"time_s": 20, "sample_rate": 1024000},
    ]
    _write_meta(p, {
        "format": "cf32", "sample_rate": 1024000, "samples": 120000,
        "sample_rate_changes": changes,
    })
    out = baseband_io.load_iq(str(p))
    assert out["samples"] == 120000
    assert out["duration_s"] is None, "旧格式混率不能按末次速率编造时长"
    assert out["mixed_sample_rates"] is True
    assert out["sample_rate_changes"] == changes
    assert "duration_unknown_reason" in out
    # 修复前：duration_s = 120000 / 1024000 ≈ 0.117（末次速率硬算），且无这些字段
    assert out["duration_s"] != round(120000 / 1024000, 3)


def test_exact_sample_rate_changes_rebuild_segments(tmp_path):
    """带 from_sample_rate + start_sample 的变更表：按段计算真实时长。"""
    p = tmp_path / "exact_mixed.iq"
    _write_iq(p, 3000)
    _write_meta(p, {
        "format": "cf32", "sample_rate": 2000000, "samples": 3000,
        "sample_rate_changes": [{
            "time_s": 0.001, "from_sample_rate": 1000000,
            "sample_rate": 2000000, "start_sample": 1000,
        }],
    })
    out = baseband_io.load_iq(str(p))
    assert out["mixed_sample_rates"] is True
    assert out["duration_s"] == 0.002, out["duration_s"]
    assert out["session_duration_s"] == 0.002
    assert len(out["segments"]) == 2
    assert out["segments"][0]["sample_rate"] == 1000000
    assert out["segments"][0]["samples"] == 1000
    assert out["segments"][1]["sample_rate"] == 2000000
    assert out["segments"][1]["samples"] == 2000
    assert out["segments"][1]["start_sample"] == 1000
    # 修复前：3000 / 2000000 = 0.0015，且完全没有 segments
    assert out["duration_s"] != 0.0015


def test_segment_manifest_reads_session_duration(tmp_path):
    """分段落盘的 sidecar：本段时长精确 + 会话总时长是各段之和。"""
    p1 = tmp_path / "baseband_a.iq"
    p2 = tmp_path / "baseband_a_part2.iq"
    _write_iq(p1, 1000)
    _write_iq(p2, 2000)
    manifest = [
        {"path": str(p1), "segment_index": 1, "start_sample": 0,
         "samples": 1000, "sample_rate": 1000000, "start_time": "T0"},
        {"path": str(p2), "segment_index": 2, "start_sample": 1000,
         "samples": 2000, "sample_rate": 2000000, "start_time": "T1"},
    ]
    for p, sr, n in ((p1, 1000000, 1000), (p2, 2000000, 2000)):
        _write_meta(p, {
            "format": "cf32", "sample_rate": sr, "samples": n,
            "duration_s": round(n / sr, 3),
            "segments": manifest,
            "session_samples": 3000,
            "session_duration_s": 0.002,
        })
    out1 = baseband_io.load_iq(str(p1))
    assert out1["duration_s"] == 0.001
    assert out1["session_duration_s"] == 0.002
    assert out1["mixed_sample_rates"] is True
    assert [s["path"] for s in out1["segments"]] == [str(p1), str(p2)]

    out2 = baseband_io.load_iq(str(p2))
    assert out2["duration_s"] == 0.001
    assert out2["session_duration_s"] == 0.002
    # 单段录音（无 segments）保持原语义
    p3 = tmp_path / "plain.iq"
    _write_iq(p3, 1000)
    _write_meta(p3, {"format": "cf32", "sample_rate": 1000000, "samples": 1000})
    out3 = baseband_io.load_iq(str(p3))
    assert out3["duration_s"] == 0.001
    assert "segments" not in out3
    assert "mixed_sample_rates" not in out3

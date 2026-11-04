# -*- coding: utf-8 -*-
"""延迟/抖动基线守卫（P6.2）。

钉住阈值（只许收紧）、报告结构、以及“超阈必须给成因提示”。
绝对延迟不在 CI 断言（机器负载不可控），由脚本 JSON/退出码给出。
"""
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "bench_latency_jitter.py")


def _load():
    spec = importlib.util.spec_from_file_location("bench_latency_jitter", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_thresholds_are_pinned():
    m = _load()
    assert m.MAX_AUDIO_P99_MS == 50.0
    assert m.MAX_WATERFALL_P99_MS == 80.0
    assert m.MAX_JITTER_P99_MINUS_P50_MS == 30.0
    assert m.MAX_WRITE_GAP_S == 0.15
    assert m.MIN_STAT_SAMPLES == 200
    assert m.AUDIO_SR == 48000
    assert m.BLOCK_SAMPLES == 16384
    assert tuple(m.RATES) == (960000.0, 1200000.0, 2400000.0)


def test_verdict_boundaries_and_causes():
    m = _load()
    good_audio = {"count": 300, "p99_ms": 10.0,
                  "jitter_p99_minus_p50_ms": 5.0, "max_write_gap_s": 0.02}
    good_wf = {"count": 300, "p99_ms": 20.0}
    v = m._verdicts(good_audio, good_wf)
    assert all(v.values())
    assert m._causes(good_audio, good_wf, v) == []

    slow = dict(good_audio, p99_ms=60.0)
    v = m._verdicts(slow, good_wf)
    assert v["audio_p99_ok"] is False
    assert any("音频 P99" in c for c in m._causes(slow, good_wf, v))

    gap = dict(good_audio, max_write_gap_s=0.30)
    v = m._verdicts(gap, good_wf)
    assert v["audio_write_gap_ok"] is False
    assert any("空档" in c or "underrun" in c
               for c in m._causes(gap, good_wf, v))

    few = dict(good_audio, count=10)
    v = m._verdicts(few, dict(good_wf, count=10))
    assert v["enough_samples"] is False
    assert any("样本不足" in c for c in m._causes(few, good_wf, v))


def test_quick_report_has_end_to_end_latency_numbers():
    """真流水线跑 4s：音频/瀑布都要有 P50/P99，且可 JSON 化。"""
    m = _load()
    report = m.build_report(quick=True)
    assert report["schema"] == "mbdsdr.bench.latency_jitter/1"
    assert len(report["rates"]) == 1
    row = report["rates"][0]
    assert row["sample_rate"] == 960000.0
    for path in ("audio", "waterfall"):
        stats = row[path]
        assert stats["count"] >= m.MIN_STAT_SAMPLES
        assert stats["p50_ms"] >= 0.0
        assert stats["p99_ms"] >= stats["p50_ms"]
    assert row["audio_blocks"] == row["audio"]["count"]   # 每块一次音频写出
    assert isinstance(row["causes"], list)
    json.dumps(report, ensure_ascii=False)
    assert m._conclusion(report)

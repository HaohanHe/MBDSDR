# -*- coding: utf-8 -*-
"""DSP 吞吐基线守卫（P6.1）。

两条纪律：

1. 基准脚本必须能在无硬件 CI 跑（合成信号 + 静音声卡替身）；
2. 阈值是契约，只能收紧不能悄悄放宽——本文件逐项钉住常量。

不在此处断言绝对性能（CI 机器负载不可控）；性能结论由
``scripts/bench_dsp_throughput.py`` 的 JSON/退出码给出。
"""
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "bench_dsp_throughput.py")


def _load():
    spec = importlib.util.spec_from_file_location("bench_dsp_throughput", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_thresholds_are_pinned():
    """阈值契约：放宽任何一项都视为回归。"""
    m = _load()
    assert m.MIN_STAGE_RTF == 1.15
    assert m.MIN_E2E_RTF == 1.00
    assert m.E2E_MAX_GAP_S == 0.25
    assert m.RECORD_TOLERANCE == 0.01
    assert m.BLOCK_SAMPLES == 16384
    assert tuple(m.RATES) == (960000.0, 1200000.0, 2400000.0)


def test_stage_set_matches_production_chain():
    """五个环节名称即生产链路：FFT / VFO / 重采样 / WFM 鉴频 / 落盘。"""
    m = _load()
    fns, cleanup = m._make_stage_fns(960000.0, 1024, os.devnull)
    try:
        assert set(fns) == {"fft_2048", "vfo_nbfm", "vfo_resample",
                            "wfm_rx", "record_write"}
        for name in fns:
            assert m._STAGE_NOTES[name]
    finally:
        cleanup()


def test_quick_report_shape_and_json_serializable():
    """快速档（0.96MS/s / 1s）报告结构可被机器读取。"""
    m = _load()
    report = m.build_report(quick=True)
    assert report["schema"] == "mbdsdr.bench.dsp_throughput/1"
    assert report["host"]["cpu_count"]
    assert len(report["rates"]) == 1
    row = report["rates"][0]
    assert row["sample_rate"] == 960000.0
    assert row["block_period_ms"] > 0
    assert set(row["stages"]) == {"fft_2048", "vfo_nbfm", "vfo_resample",
                                  "wfm_rx", "record_write"}
    for stage in row["stages"].values():
        assert stage["median_ms"] > 0
        assert stage["realtime_factor"] > 0
        assert isinstance(stage["rtf_ok"], bool)
    e2e = row["e2e"]
    assert e2e["produced_samples"] > 0
    assert e2e["sustained_msps"] > 0
    assert set(e2e["verdicts"]) == {"sustained_rtf_ge_1",
                                    "record_bytes_match",
                                    "record_no_data_hole",
                                    "no_lingering_threads"}
    assert isinstance(report["overall_ok"], bool)
    # 必须可 JSON 序列化（机器可读结果文件的前提）
    json.dumps(report, ensure_ascii=False)
    assert m._conclusion(report)


def test_hotspots_are_module_function_level_and_sorted():
    """热点归因必须给到模块:函数，并按自身耗时降序。"""
    m = _load()
    rows = m.profile_hotspots(sr=2400000.0, iterations=2, top=5)
    assert rows
    assert len(rows) <= 5
    for row in rows:
        assert set(row) == {"module", "function", "tottime_ms", "share_pct"}
        assert row["module"]
        assert row["function"]
        assert row["tottime_ms"] >= 0
        assert 0 <= row["share_pct"] <= 100
    times = [r["tottime_ms"] for r in rows]
    assert times == sorted(times, reverse=True)


def test_e2e_verdict_boundaries():
    """反向守卫：超阈值必须判失败，避免「总体 ok」掩盖单项失守。"""
    m = _load()
    sr, elapsed, produced = 2400000.0, 1.0, 2400000
    good = m.evaluate_e2e(elapsed, produced, sr, produced * 8, 0.01, [])
    assert good["ok"] is True
    # 落盘空档 0.30s > 0.25s：判失败
    hole = m.evaluate_e2e(elapsed, produced, sr, produced * 8, 0.30, [])
    assert hole["verdicts"]["record_no_data_hole"] is False
    assert hole["ok"] is False
    # 落盘字节缺 3% > 1%：判失败
    loss = m.evaluate_e2e(elapsed, produced, sr, int(produced * 8 * 0.97),
                          0.01, [])
    assert loss["verdicts"]["record_bytes_match"] is False
    # 可持续速率 0.9x：判失败
    slow = m.evaluate_e2e(1.0, int(sr * 0.9), sr, int(sr * 0.9) * 8,
                          0.01, [])
    assert slow["verdicts"]["sustained_rtf_ge_1"] is False
    # 线程残留：判失败
    linger = m.evaluate_e2e(elapsed, produced, sr, produced * 8, 0.01,
                            ["DemodWorker"])
    assert linger["verdicts"]["no_lingering_threads"] is False

# -*- coding: utf-8 -*-
"""长跑判据守卫（P4 口径 + P6.3 GC/对象增长）。

P6 要求“不重复造第二套标准”：旧阈值原样钉住，只允许新增项；
任何放宽（例如 RSS 32→64MB、线程趋势 2→3）都必须让本测试失败。
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "soak_receive_chain.py")


def _load():
    spec = importlib.util.spec_from_file_location("soak_receive_chain", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _samples(n=10, rss=200_000_000, threads=8, handles=300,
             gc_objects=50_000, start=0.0):
    return [{"t": start + 10.0 * i, "rss": rss, "threads": threads,
             "handles": handles, "gc_objects": gc_objects,
             "gc_counts": [0, 0, 0], "gc_collections": 100}
            for i in range(n)]


def test_p4_thresholds_unchanged_and_p6_additions_pinned():
    m = _load()
    assert m.MAX_RSS_GROWTH_MB == 32.0
    assert m.MAX_THREAD_TREND == 2
    assert m.MAX_THREAD_SPAN == 4
    assert m.MAX_HANDLE_GROWTH == 64
    assert m.RECORD_BAND == 0.25
    assert m.MAX_DATA_HOLE_S == 5.0
    assert m.MAX_OBJECT_GROWTH == 5000


def test_evaluate_soak_all_good_series_passes():
    m = _load()
    result = m.evaluate_soak(_samples(), 1_000_000, 1_000_000, 0.1, [])
    assert result["ok"] is True
    assert all(result["verdicts"].values())
    assert result["rss_peak_mb"] > 0
    assert result["gc_peak_objects"] == 50_000
    assert result["gc_collections_total"] == 100


def test_evaluate_soak_each_violation_returns_false():
    m = _load()
    base = _samples()

    def run(**kw):
        return m.evaluate_soak(**kw)["verdicts"]

    # RSS 末段 +40MB（>32MB）；用 20 个样本让首/末 60s 窗口不重叠
    grow = _samples(n=20, rss=200_000_000)
    grow[-6:] = [dict(s, rss=240_000_000) for s in grow[-6:]]
    assert run(samples=grow, expected_bytes=1_000_000,
               record_bytes=1_000_000, max_gap=0.1,
               lingering=[])["rss_growth_mb_lt_32"] is False
    # 线程趋势 +3（>2）
    thr = _samples(n=20, threads=8)
    thr[-6:] = [dict(s, threads=11) for s in thr[-6:]]
    assert run(samples=thr, expected_bytes=1_000_000,
               record_bytes=1_000_000, max_gap=0.1,
               lingering=[])["threads_no_growth_trend"] is False
    # 句柄增长 +100（>64）
    hnd = _samples(handles=300)
    hnd[-1] = dict(hnd[-1], handles=400)
    assert run(samples=hnd, expected_bytes=1_000_000,
               record_bytes=1_000_000, max_gap=0.1,
               lingering=[])["handle_growth_lt_64"] is False
    # 落盘字节偏 30%（>25%）
    assert run(samples=base, expected_bytes=1_000_000,
               record_bytes=700_000, max_gap=0.1,
               lingering=[])["record_bytes_in_band"] is False
    # 数据洞 6s（>5s）
    assert run(samples=base, expected_bytes=1_000_000,
               record_bytes=1_000_000, max_gap=6.0,
               lingering=[])["no_data_hole_lt_5s"] is False
    # 线程残留
    assert run(samples=base, expected_bytes=1_000_000,
               record_bytes=1_000_000, max_gap=0.1,
               lingering=["DemodWorker"])["no_lingering_threads"] is False
    # P6：对象数 +8000（>5000）
    obj = _samples(n=20, gc_objects=50_000)
    obj[-6:] = [dict(s, gc_objects=58_000) for s in obj[-6:]]
    assert run(samples=obj, expected_bytes=1_000_000,
               record_bytes=1_000_000, max_gap=0.1,
               lingering=[])["object_growth_le_5000"] is False


def test_speed_flag_semantics_and_gc_metrics():
    m = _load()
    src = m.SyntheticWfmSource(192000.0, realtime=False, speed=10.0)
    assert src.speed == 10.0
    assert src.read(1024).shape == (1024,)
    assert src.read(1024).dtype.name == "complex64"
    try:
        m.SyntheticWfmSource(192000.0, speed=0)
    except ValueError:
        pass
    else:
        raise AssertionError("speed=0 必须被拒绝")
    gc = m._gc_metrics()
    assert set(gc) == {"gc_objects", "gc_counts", "gc_collections"}
    assert gc["gc_objects"] > 0
    assert len(gc["gc_counts"]) == 3

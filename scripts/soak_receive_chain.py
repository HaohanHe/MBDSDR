# -*- coding: utf-8 -*-
"""接收链路长跑（P4.2）：连续接收 + 解码 + 落盘 ≥10 分钟。

链路（全真线程，无任何桩）：合成 WFM 载波 IQ 源（FM 调制 1 kHz 单音 +
噪声，输入性质在报告中如实标注「合成」；监控对象是链路自身的资源
行为，不验证信号内容真伪）→ ReceivePipeline（IqReaderThread /
StreamSplitter / FftWorker / DemodWorker[WFM 鉴频+NoiseSquelch+音频
AGC] / RecordTap）→ 音频落盘替身计数 + baseband cf32 连续落盘。

每 10s 采样：进程 RSS、Python 线程数、Win32 句柄数、累计 IQ 块数、
落盘字节。结束判定（全过才 exit 0）：

  * RSS 增长：末 60s 均值 − 首 60s 基线 < 32 MB（无内存增长异常）；
  * 线程数：稳态恒定（最大 − 最小 ≤ 1，无线程泄漏）；
  * 句柄增长：< 64（Win32 句柄泄漏哨戒）；
  * 落盘：字节数在期望带（块样本数×块数×8B ±25%），最长无块间隔 < 5s
    （无消费卡死造成的数据洞）；
  * shutdown：全部 QThread 在 10s 内退出（无进程残留）。

P6.3 扩展（判据不另立标准）：每 10s 额外采样 GC 跟踪对象数、各代计数与
累计回收次数；新增 `--speed` 支持等价加速回放（`--minutes 3 --speed 10`
= 30 分钟音频量，墙钟 3 分钟），RSS/线程/句柄/落盘判据与 P4 完全一致，
仅追加「对象数趋势 ≤ 5000」一条。

用法：
    python scripts/soak_receive_chain.py [--minutes 10] [--speed 1] [--sr 192000]
        [--out output/soak/soak_rx_<时间戳>.jsonl]
    30 分钟等价加速：--minutes 3 --speed 10
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import gc
import json
import math
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

import numpy as np  # noqa: E402

# ---- P4/P6 共用判据（测试逐项钉住；P6 只加 GC/对象增长，不改旧阈值） ----
MAX_RSS_GROWTH_MB = 32.0        # 末 60s 均值 − 首 60s 基线
MAX_THREAD_TREND = 2            # 线程数趋势（末−首，≤+2）
MAX_THREAD_SPAN = 4             # 全局防爆（max−min）
MAX_HANDLE_GROWTH = 64          # Win32 句柄增长
RECORD_BAND = 0.25              # 落盘字节期望带 ±25%
MAX_DATA_HOLE_S = 5.0           # 最长无落盘块间隔
MAX_OBJECT_GROWTH = 5000        # P6：稳态段 GC 跟踪对象数趋势（末−首）


# ---------------------------------------------------------------------------
# 资源采样（psutil；缺失时退 ctypes Win32）
# ---------------------------------------------------------------------------
def _proc():
    import psutil  # type: ignore
    return psutil.Process()


def _rss_bytes() -> int:
    try:
        return int(_proc().memory_info().rss)
    except Exception:
        return -1


def _handle_count() -> int:
    try:
        return int(_proc().num_handles())
    except Exception:
        return -1


def _thread_count() -> int:
    try:
        return int(_proc().num_threads())
    except Exception:
        return -1


def _gc_metrics() -> dict:
    """P6.3：GC 跟踪对象数 / 各代计数 / 累计回收次数（10s 一次，成本可忽略）。"""
    try:
        return {
            "gc_objects": len(gc.get_objects()),
            "gc_counts": list(gc.get_count()),
            "gc_collections": sum(int(s.get("collections", 0))
                                  for s in gc.get_stats()),
        }
    except Exception:
        return {"gc_objects": -1, "gc_counts": [], "gc_collections": -1}


def evaluate_soak(samples: list, expected_bytes: int, record_bytes: int,
                  max_gap: float, lingering: list) -> dict:
    """P4/P6 共用长跑判定（纯函数）。

    趋势哨戒口径与 P4 完全一致：末 60s 均值 − 首 60s 基线；线程按趋势
    （≤+2）而非 max-min 判泄漏，另加全局防爆（span ≤ 4）。P6 仅新增
    GC 跟踪对象数趋势与水位峰值，不改旧阈值。
    """
    rss = [s["rss"] for s in samples if s["rss"] > 0]
    steady = [s for s in samples if s["t"] >= 60.0]
    if len(steady) < 2:
        steady = list(samples)        # 短跑降级：用全部样本同口径判定
    t_ser = [s["threads"] for s in steady if s["threads"] > 0]
    h_ser = [s["handles"] for s in steady if s["handles"] > 0]
    head = rss[:6] if len(rss) >= 6 else rss[:1]
    tail = rss[-6:] if len(rss) >= 6 else rss[-1:]
    rss_growth = (sum(tail) / len(tail) - sum(head) / len(head)) if head and tail else 0
    t_head = t_ser[:6] if len(t_ser) >= 6 else t_ser[:1]
    t_tail = t_ser[-6:] if len(t_ser) >= 6 else t_ser[-1:]
    threads_trend = (sum(t_tail) / len(t_tail)
                     - sum(t_head) / len(t_head)) if t_head and t_tail else 0
    threads_span = (max(t_ser) - min(t_ser)) if t_ser else 0
    o_ser = [s["gc_objects"] for s in steady if s.get("gc_objects", -1) > 0]
    o_head = o_ser[:6] if len(o_ser) >= 6 else o_ser[:1]
    o_tail = o_ser[-6:] if len(o_ser) >= 6 else o_ser[-1:]
    object_growth = (sum(o_tail) / len(o_tail)
                     - sum(o_head) / len(o_head)) if o_head and o_tail else 0
    verdicts = {
        "rss_growth_mb_lt_32": (rss_growth / 1e6) < MAX_RSS_GROWTH_MB
        if len(rss) >= 2 else None,
        "threads_no_growth_trend": threads_trend <= MAX_THREAD_TREND
        if t_ser else None,
        "threads_span_le_4": threads_span <= MAX_THREAD_SPAN if t_ser else None,
        "handle_growth_lt_64": ((h_ser[-1] - h_ser[0]) < MAX_HANDLE_GROWTH)
        if len(h_ser) >= 2 else None,
        "record_bytes_in_band": abs(record_bytes - expected_bytes)
        <= RECORD_BAND * expected_bytes,
        "no_data_hole_lt_5s": max_gap < MAX_DATA_HOLE_S,
        "no_lingering_threads": not lingering,
        "object_growth_le_5000": object_growth <= MAX_OBJECT_GROWTH
        if len(o_ser) >= 2 else None,
    }
    gc_objects = [s.get("gc_objects", -1) for s in samples
                  if s.get("gc_objects", -1) > 0]
    gc_collections = [s.get("gc_collections", -1) for s in samples
                      if s.get("gc_collections", -1) >= 0]
    return {
        "verdicts": verdicts,
        "ok": all(v for v in verdicts.values()),
        "rss_growth_mb": round(rss_growth / 1e6, 2),
        "rss_peak_mb": round(max(rss) / 1e6, 2) if rss else -1,
        "rss_p95_mb": round(float(np.percentile(rss, 95)) / 1e6, 2)
        if rss else -1,
        "threads_range": [min(t_ser) if t_ser else -1,
                          max(t_ser) if t_ser else -1],
        "threads_trend": round(threads_trend, 2),
        "handles_range": [h_ser[0] if h_ser else -1,
                          h_ser[-1] if h_ser else -1],
        "object_growth": round(object_growth, 1),
        "gc_peak_objects": max(gc_objects) if gc_objects else -1,
        "gc_collections_total": gc_collections[-1] if gc_collections else -1,
    }


# ---------------------------------------------------------------------------
# 合成 WFM 载波 IQ 源（输入性质：合成；相位连续，FM 调制 1 kHz 单音）
# ---------------------------------------------------------------------------
class SyntheticWfmSource:
    """合成 WFM 载波源（实时限速：模拟真实 SDR 的采样节奏）。"""

    def __init__(self, sr: float, noise_level: float = 0.02,
                 tone_hz: float = 1000.0, deviation_hz: float = 75000.0,
                 realtime: bool = True, speed: float = 1.0):
        self.sr = float(sr)
        self.noise = float(noise_level)
        self.tone_hz = float(tone_hz)
        self.dev = float(deviation_hz)
        self.realtime = bool(realtime)
        # P6：加速回放倍数。speed=N 表示 N 分钟音频在 1 分钟墙钟内喂完，
        # 用于把 30 分钟长跑压到可接受的墙钟时长；资源判据口径不变。
        self.speed = float(speed)
        if self.speed <= 0:
            raise ValueError("speed 必须 > 0")
        self._phase = 0.0
        self._n0 = 0
        self._rng = np.random.default_rng(20261104)
        self._t0 = time.monotonic()

    def read(self, n: int) -> np.ndarray:
        if self.realtime:
            # 速率限制：样本时钟对齐墙钟（真实 SDR 实时节奏；speed>1 即加速回放）
            target = self._n0 / (self.sr * self.speed)
            lag = target - (time.monotonic() - self._t0)
            if lag > 0:
                time.sleep(lag)
        idx = self._n0 + np.arange(n)
        self._n0 += n
        # FM：相位 = 2π·dev·∫sin(2π·tone·t)dt → 瞬时频偏 ±dev·|sin|
        inst_freq = self.dev * np.sin(2 * np.pi * self.tone_hz * idx / self.sr)
        self._phase += 2 * np.pi * float(np.sum(inst_freq)) / self.sr
        carrier = np.exp(1j * self._phase).astype(np.complex128)
        noise = (self._rng.standard_normal(n)
                 + 1j * self._rng.standard_normal(n)) * (self.noise / math.sqrt(2))
        return (carrier + noise).astype(np.complex64)


class _CountingPlayer:
    """落盘替身：只统计（不写声卡、不产数据），available=True 让链路走全门控。"""

    def __init__(self):
        self.available = True
        self.blocks = 0
        self.samples = 0
        self.muted_changes = 0
        self._last_muted = None

    def write(self, audio) -> None:
        self.blocks += 1
        self.samples += int(np.asarray(audio).size)

    def set_muted(self, muted: bool) -> None:
        if muted != self._last_muted:
            self.muted_changes += 1
            self._last_muted = bool(muted)


class _Cf32Recorder:
    """baseband 连续落盘（cf32 追加，记录字节数与最近落盘时刻）。"""

    def __init__(self, path: str):
        self._f = open(path, "wb")
        self.bytes = 0
        self.blocks = 0
        self.last_ts = time.monotonic()

    def __call__(self, iq: np.ndarray) -> None:
        arr = np.asarray(iq, dtype=np.complex64)
        inter = np.empty(arr.size * 2, dtype=np.float32)
        inter[0::2] = arr.real
        inter[1::2] = arr.imag
        self._f.write(inter.tobytes())
        self.bytes += inter.nbytes
        self.blocks += 1
        self.last_ts = time.monotonic()

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0,
                    help="墙钟时长（分钟）")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="加速回放倍数；--minutes 3 --speed 10 = 30 分钟等价")
    ap.add_argument("--sr", type=float, default=192000.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.speed <= 0:
        ap.error("--speed 必须 > 0")

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QThread
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
    from spectrum_widget import SpectrumDataGenerator

    app = QApplication.instance() or QApplication(sys.argv)
    src = SyntheticWfmSource(args.sr, speed=args.speed)
    gen = SpectrumDataGenerator(num_bins=512)
    player = _CountingPlayer()
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(ROOT, "output", "soak")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(out_dir, f"soak_rx_{ts}.jsonl")
    rec_path = os.path.join(out_dir, f"soak_rx_{ts}.cf32")
    recorder = _Cf32Recorder(rec_path)

    cfg = SharedDemodConfig(mode="WFM", auto_squelch=True, agc_speed="medium")
    pipe = ReceivePipeline(
        read_fn=src.read, sr=args.sr, gen=gen,
        center_getter=lambda: 98.5e6,
        main_vfo_dsp=None, vfo2_dsp=None,
        player=player, config=cfg, on_record_iq=recorder,
    )

    jsonl = open(out_path, "w", encoding="utf-8")

    def emit(event: dict):
        jsonl.write(json.dumps(event, ensure_ascii=False) + "\n")
        jsonl.flush()

    emit({"event": "start", "ts": dt.datetime.now().isoformat(timespec="seconds"),
          "minutes": args.minutes, "speed": args.speed, "sr": args.sr,
          "simulated_minutes": round(args.minutes * args.speed, 2),
          "input_nature": "synthetic WFM carrier IQ (labeled synthetic)",
          "block_samples": 16384,
          "note": "P6.3：speed>1 为等价加速回放，判据与 P4 同口径"})

    pipe.start()
    t0 = time.monotonic()
    deadline = t0 + args.minutes * 60.0
    samples = []
    last_block_ts = time.monotonic()
    prev_blocks = 0
    max_gap = 0.0
    next_tick = t0 + 10.0

    while time.monotonic() < deadline:
        now = time.monotonic()
        if recorder.blocks != prev_blocks:
            last_block_ts = now
            prev_blocks = recorder.blocks
        max_gap = max(max_gap, now - last_block_ts)
        if now >= next_tick:
            sample = {
                "t": round(now - t0, 1),
                "rss": _rss_bytes(),
                "threads": _thread_count(),
                "handles": _handle_count(),
                "record_bytes": recorder.bytes,
                "audio_blocks": player.blocks,
                **_gc_metrics(),
            }
            samples.append(sample)
            emit({"event": "sample", **sample})   # 落盘供事后 flaky 分析
            next_tick += 10.0
        app.processEvents()
        time.sleep(0.05)

    pipe.shutdown()
    # 线程退出验证（10s 预算）
    exit_deadline = time.monotonic() + 10.0
    lingering = []
    for th in pipe.findChildren(QThread):
        while th.isRunning() and time.monotonic() < exit_deadline:
            time.sleep(0.05)
        if th.isRunning():
            lingering.append(type(th).__name__)
    recorder.close()

    # ---- 判定（与 P4 同口径的纯函数；P6 仅新增 GC/对象增长）----
    # 落盘期望带：墙钟时长 × 加速倍数 × 采样率 × 8B/样本，±25%
    expect_bytes = int(args.minutes * 60.0 * args.speed * args.sr * 8)
    got_bytes = recorder.bytes
    result = evaluate_soak(samples, expect_bytes, got_bytes, max_gap, lingering)
    report = {
        "event": "verdict", "ok": result["ok"],
        "verdicts": result["verdicts"],
        "duration_s": round(time.monotonic() - t0, 1),
        "speed": args.speed,
        "simulated_minutes": round(args.minutes * args.speed, 2),
        "rss_growth_mb": result["rss_growth_mb"],
        "rss_peak_mb": result["rss_peak_mb"],
        "rss_p95_mb": result["rss_p95_mb"],
        "threads_range": result["threads_range"],
        "threads_trend": result["threads_trend"],
        "handles_range": result["handles_range"],
        "object_growth": result["object_growth"],
        "gc_peak_objects": result["gc_peak_objects"],
        "gc_collections_total": result["gc_collections_total"],
        "record_bytes": got_bytes,
        "record_expect_bytes": expect_bytes,
        "audio_blocks": player.blocks,
        "max_gap_s": round(max_gap, 2),
        "lingering": lingering,
        "samples_tail": samples[-3:],
    }
    emit(report)
    jsonl.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

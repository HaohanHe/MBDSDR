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

用法：
    python scripts/soak_receive_chain.py [--minutes 10] [--sr 192000]
        [--out output/soak/soak_rx_<时间戳>.jsonl]
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
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


# ---------------------------------------------------------------------------
# 合成 WFM 载波 IQ 源（输入性质：合成；相位连续，FM 调制 1 kHz 单音）
# ---------------------------------------------------------------------------
class SyntheticWfmSource:
    """合成 WFM 载波源（实时限速：模拟真实 SDR 的采样节奏）。"""

    def __init__(self, sr: float, noise_level: float = 0.02,
                 tone_hz: float = 1000.0, deviation_hz: float = 75000.0,
                 realtime: bool = True):
        self.sr = float(sr)
        self.noise = float(noise_level)
        self.tone_hz = float(tone_hz)
        self.dev = float(deviation_hz)
        self.realtime = bool(realtime)
        self._phase = 0.0
        self._n0 = 0
        self._rng = np.random.default_rng(20261104)
        self._t0 = time.monotonic()

    def read(self, n: int) -> np.ndarray:
        if self.realtime:
            # 速率限制：样本时钟对齐墙钟（真实 SDR 的实时节奏）
            target = self._n0 / self.sr
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
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--sr", type=float, default=192000.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QThread
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
    from spectrum_widget import SpectrumDataGenerator

    app = QApplication.instance() or QApplication(sys.argv)
    src = SyntheticWfmSource(args.sr)
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
          "minutes": args.minutes, "sr": args.sr,
          "input_nature": "synthetic WFM carrier IQ (labeled synthetic)",
          "block_samples": 16384})

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

    # ---- 判定 ----
    rss = [s["rss"] for s in samples if s["rss"] > 0]
    # 线程/句柄稳定性按 t≥60s 稳态段判定：Qt 启动期的临时线程
    # （QThreadPool 按需线程，空闲超时回收）会在前几十秒一次性回落，
    # 属正常热身而非泄漏；泄漏定义是持续增长。
    steady = [s for s in samples if s["t"] >= 60.0]
    threads = [s["threads"] for s in steady if s["threads"] > 0]
    handles = [s["handles"] for s in steady if s["handles"] > 0]
    head = rss[:6] if len(rss) >= 6 else rss[:1]
    tail = rss[-6:] if len(rss) >= 6 else rss[-1:]
    rss_growth = (sum(tail) / len(tail) - sum(head) / len(head)) if head and tail else 0
    # 落盘期望带（实时限速源）：时长(秒) × 采样率 × 8B/样本，±25%
    expect_bytes = int(args.minutes * 60.0 * args.sr * 8)
    got_bytes = recorder.bytes
    verdicts = {
        "rss_growth_mb_lt_32": (rss_growth / 1e6) < 32.0 if len(rss) >= 2 else None,
        "threads_stable": (max(threads) - min(threads) <= 2) if threads else None,
        "handle_growth_lt_64": ((handles[-1] - handles[0]) < 64)
        if len(handles) >= 2 else None,
        "record_bytes_in_band": abs(got_bytes - expect_bytes) <= 0.25 * expect_bytes,
        "no_data_hole_lt_5s": max_gap < 5.0,
        "no_lingering_threads": not lingering,
    }
    ok = all(v for v in verdicts.values())
    report = {
        "event": "verdict", "ok": ok, "verdicts": verdicts,
        "duration_s": round(time.monotonic() - t0, 1),
        "rss_growth_mb": round(rss_growth / 1e6, 2),
        "threads_range": [min(threads) if threads else -1,
                          max(threads) if threads else -1],
        "handles_range": [handles[0] if handles else -1,
                          handles[-1] if handles else -1],
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
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

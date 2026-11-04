# -*- coding: utf-8 -*-
"""端到端延迟与抖动基准（P6.2）：样本进入 → 音频 / 瀑布输出。

**测量方法（可复核）**

1. 合成 WFM 载波 IQ 源按样本时钟限速（模拟真实 SDR 的到达节奏），
   首个 read 的墙钟记为 ``t0``；流内第 i 个样本的时间戳
   ``ts(i) = t0 + i / sr``。
2. **音频通路**：``_LatencyPlayer.write`` 记录累计音频样本数 ``a``；
   对应输入样本下标 ``i = a * sr / 48000``（WFM 内部重采样比固定）。
   单块延迟 ``lat = t_write - ts(i)``。声卡设备本身缓冲不在内（替身
   不打开声卡，偏移 0；真机需另加设备缓冲——如实标注边界）。
3. **瀑布通路**：``ReceiverPipeline.frame_ready`` 跨线程 queued 投递到
   主线程进程事件循环，槽里记录到达时刻；第 k 帧对应输入下标
   ``i = (k+1) * block``。延迟含 Qt 排队 + 每轮 processEvents 调度，
   不含实际 QPainter 像素绘制（UI 30fps 定时器统一重绘，绘制成本另计）。
4. 抖动 = 音频延迟分布的 ``P99 - P50``；同时统计相邻音频写出的最大
   空档 ``max_write_gap``，用于判断“现象”（断档 → 声卡 underrun 风险）。

阈值（测试钉住，只许收紧）：

  * ``MAX_AUDIO_P99_MS = 250``、``MAX_WATERFALL_P99_MS = 300``；
  * ``MAX_JITTER_P99_MINUS_P50_MS = 100``；
  * ``MAX_WRITE_GAP_S = 0.25``；
  * ``MIN_STAT_SAMPLES = 200``（样本不足则统计不可信，判失败）。

结果写 ``output/bench/latency_jitter_<ts>.json``。成因提示按现象分类输出，
避免“超阈值了但不知为什么”。

用法::

    python scripts/bench_latency_jitter.py
    python scripts/bench_latency_jitter.py --quick     # CI：0.96MS/s / 2s
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

import numpy as np  # noqa: E402

# ---- 固定参数（测试逐项钉住） ----
RATES = (960000.0, 1200000.0, 2400000.0)
BLOCK_SAMPLES = 16384
AUDIO_SR = 48000
MAX_AUDIO_P99_MS = 50.0          # 实测 P99 ~5ms，留 ~10 倍余量
MAX_WATERFALL_P99_MS = 80.0      # 实测 P99 ~7.5ms，含 Qt 排队
MAX_JITTER_P99_MINUS_P50_MS = 30.0
MAX_WRITE_GAP_S = 0.15
MIN_STAT_SAMPLES = 200
RUN_SECONDS = 10.0
QUICK_SECONDS = 4.0   # 0.96MS/s × 16384 块 → 至少 200 个统计样本

_CAUSE_HINTS = {
    "audio_p99": "音频 P99 超限：解调线程单块处理变长或 GIL 争用（FFT 线程/"
                 "落盘 I/O 抢占）；先看 audio_max_gap 与热点表。",
    "waterfall_p99": "瀑布 P99 超限：UI 事件循环排队（processEvents 节流/"
                     "主线程忙），不是 DSP 单块慢——对比同一档音频 P99。",
    "jitter": "抖动（P99-P50）超限：块到达节奏不均，多为 splitter 全量拷贝、"
              "GC 停顿或系统调度抖动。",
    "write_gap": "音频写出空档超限：声卡 underrun 风险；成因见音频 P99 与"
                 "落盘/解调线程阻塞。",
    "few_samples": "统计样本不足：缩短测量时间或降采样率会失真，判失败。",
}


def _pct(values, q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), q)) \
        if values else 0.0


class _PacedSource:
    """按样本时钟限速的合成 WFM 源；记录 t0 供流时间戳换算。"""

    def __init__(self, sr: float, tone_hz: float = 1000.0,
                 deviation_hz: float = 75000.0, noise: float = 0.02):
        self.sr = float(sr)
        self.tone_hz = float(tone_hz)
        self.dev = float(deviation_hz)
        self.noise = float(noise)
        self.produced = 0
        self.t0 = None
        self._phase = 0.0
        self._rng = np.random.default_rng(20261104)

    def read(self, n: int) -> np.ndarray:
        if self.t0 is None:
            self.t0 = time.monotonic()
        target = self.produced / self.sr
        lag = target - (time.monotonic() - self.t0)
        if lag > 0:
            time.sleep(lag)
        idx = self.produced + np.arange(n, dtype=np.float64)
        inst = self.dev * np.sin(2.0 * np.pi * self.tone_hz * idx / self.sr)
        self._phase += 2.0 * np.pi * float(np.sum(inst)) / self.sr
        carrier = np.exp(1j * self._phase).astype(np.complex64)
        noise = (self._rng.standard_normal(n)
                 + 1j * self._rng.standard_normal(n)) * (self.noise / 1.414)
        self.produced += n
        return (carrier + noise).astype(np.complex64)


from PySide6.QtCore import QObject, Slot  # noqa: E402


class _FrameProbe(QObject):
    """主线程探针：接 ReceivePipeline.frame_ready（跨线程 queued 投递）。"""

    def __init__(self, src: "_PacedSource", block: int):
        super().__init__()
        self.src = src
        self.block = block
        self.count = 0
        self.latencies_ms = []

    @Slot(object, float)
    def handle(self, _iq, _sr) -> None:
        if self.src.t0 is None:
            return
        idx = self.count * self.block      # 该帧首样本的流内下标（0 起）
        self.count += 1
        self.latencies_ms.append(
            1000.0 * (time.monotonic() - (self.src.t0 + idx / self.src.sr)))


class _LatencyPlayer:
    """声卡替身：记录每次 write 的墙钟与累计音频样本数（不打开设备）。"""

    available = True

    def __init__(self, src: _PacedSource, block: int = BLOCK_SAMPLES):
        self.src = src
        self.block = int(block)
        self.samples = 0
        self.blocks = 0
        self.latencies_ms = []
        self.write_ts = []
        self.last_ts = None
        self.max_gap_s = 0.0

    def write(self, audio) -> None:
        now = time.monotonic()
        if self.last_ts is not None:
            self.max_gap_s = max(self.max_gap_s, now - self.last_ts)
        self.last_ts = now
        n = int(np.asarray(audio).size)
        self.write_ts.append(now)
        if self.src.t0 is not None:
            # WFM 路径每输入块恰好一次 write：第 k 次 write 对应块 k，
            # 用块首下标而非累计音频样本（后者受逐块重采样余数漂移影响，
            # 会累积出系统性负延迟）。
            in_idx = self.blocks * self.block
            self.latencies_ms.append(
                1000.0 * (now - (self.src.t0 + in_idx / self.src.sr)))
        self.samples += n
        self.blocks += 1

    def set_muted(self, _muted: bool) -> None:
        pass


class _LatencyRecorder:
    """落盘替身：只计数（P6.2 不测写盘延迟，避免把磁盘抖动混进音频延迟）。"""

    def __init__(self):
        self.bytes = 0
        self.blocks = 0

    def __call__(self, iq) -> None:
        self.blocks += 1
        self.bytes += int(np.asarray(iq).size) * 8


def _summarize(latencies_ms, max_gap_s, extra=None) -> dict:
    data = {
        "count": len(latencies_ms),
        "p50_ms": round(_pct(latencies_ms, 50), 2),
        "p95_ms": round(_pct(latencies_ms, 95), 2),
        "p99_ms": round(_pct(latencies_ms, 99), 2),
        "max_ms": round(max(latencies_ms), 2) if latencies_ms else 0.0,
        "jitter_p99_minus_p50_ms": round(
            _pct(latencies_ms, 99) - _pct(latencies_ms, 50), 2)
        if latencies_ms else 0.0,
        "max_write_gap_s": round(max_gap_s, 3),
    }
    if extra:
        data.update(extra)
    return data


def _verdicts(audio: dict, waterfall: dict) -> dict:
    return {
        "enough_samples": (audio["count"] >= MIN_STAT_SAMPLES
                           and waterfall["count"] >= MIN_STAT_SAMPLES),
        "audio_p99_ok": audio["p99_ms"] <= MAX_AUDIO_P99_MS,
        "waterfall_p99_ok": waterfall["p99_ms"] <= MAX_WATERFALL_P99_MS,
        "jitter_ok":
            audio["jitter_p99_minus_p50_ms"] <= MAX_JITTER_P99_MINUS_P50_MS,
        "audio_write_gap_ok": audio["max_write_gap_s"] <= MAX_WRITE_GAP_S,
    }


def _causes(audio: dict, waterfall: dict, verdicts: dict) -> list:
    out = []
    if not verdicts["enough_samples"]:
        out.append(_CAUSE_HINTS["few_samples"])
    if not verdicts["audio_p99_ok"]:
        out.append(_CAUSE_HINTS["audio_p99"])
    if not verdicts["waterfall_p99_ok"]:
        out.append(_CAUSE_HINTS["waterfall_p99"])
    if not verdicts["jitter_ok"]:
        out.append(_CAUSE_HINTS["jitter"])
    if not verdicts["audio_write_gap_ok"]:
        out.append(_CAUSE_HINTS["write_gap"])
    return out


def measure_rate(sr: float, seconds: float = RUN_SECONDS,
                 block: int = BLOCK_SAMPLES) -> dict:
    """单档速率的端到端延迟/抖动测量。"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QThread
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
    from spectrum_widget import SpectrumDataGenerator

    app = QApplication.instance() or QApplication(sys.argv[:1])

    src = _PacedSource(sr)
    gen = SpectrumDataGenerator(num_bins=512)
    gen.fft_size = 2048
    player = _LatencyPlayer(src, block)
    recorder = _LatencyRecorder()
    cfg = SharedDemodConfig(mode="WFM", auto_squelch=True, agc_speed="medium")

    probe = _FrameProbe(src, block)

    pipe = ReceivePipeline(
        read_fn=src.read, sr=sr, gen=gen,
        center_getter=lambda: 98.5e6,
        main_vfo_dsp=None, vfo2_dsp=None,
        player=player, config=cfg, on_record_iq=recorder,
    )
    pipe.frame_ready.connect(probe.handle)
    pipe.start()
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < seconds:
            app.processEvents()
            time.sleep(0.005)
    finally:
        pipe.shutdown()
        exit_deadline = time.monotonic() + 10.0
        lingering = []
        for th in pipe.findChildren(QThread):
            while th.isRunning() and time.monotonic() < exit_deadline:
                time.sleep(0.05)
            if th.isRunning():
                lingering.append(type(th).__name__)

    audio = _summarize(player.latencies_ms, player.max_gap_s)
    waterfall = _summarize(probe.latencies_ms, 0.0)
    verdicts = _verdicts(audio, waterfall)
    text_chunk_ms = 1000.0 * block / sr
    return {
        "sample_rate": sr,
        "sample_rate_msps": round(sr / 1e6, 2),
        "block_samples": block,
        "block_period_ms": round(text_chunk_ms, 2),
        "audio": audio,
        "waterfall": waterfall,
        "audio_blocks": player.blocks,
        "audio_samples": player.samples,
        "recorded_blocks": recorder.blocks,
        "lingering_threads": lingering,
        "verdicts": verdicts,
        "causes": _causes(audio, waterfall, verdicts),
        "ok": all(verdicts.values()) and not lingering,
    }


def build_report(rates=RATES, seconds: float = RUN_SECONDS,
                 quick: bool = False) -> dict:
    if quick:
        rates = RATES[:1]
        seconds = QUICK_SECONDS
    rows = [measure_rate(sr, seconds=seconds) for sr in rates]
    return {
        "schema": "mbdsdr.bench.latency_jitter/1",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "method": (
            "源按样本时钟限速；音频延迟=t_write-(t0+a*4/sr)，"
            "瀑布延迟=frame_ready槽到达-(t0+k*block/sr)。"
            "不含声卡设备缓冲与实际 QPainter 绘制（边界如实标注）。"
        ),
        "thresholds": {
            "max_audio_p99_ms": MAX_AUDIO_P99_MS,
            "max_waterfall_p99_ms": MAX_WATERFALL_P99_MS,
            "max_jitter_p99_minus_p50_ms": MAX_JITTER_P99_MINUS_P50_MS,
            "max_write_gap_s": MAX_WRITE_GAP_S,
            "min_stat_samples": MIN_STAT_SAMPLES,
        },
        "rates": rows,
        "overall_ok": all(r["ok"] for r in rows),
    }


def _conclusion(report: dict) -> str:
    parts = []
    for row in report["rates"]:
        a, w = row["audio"], row["waterfall"]
        parts.append(
            f"{row['sample_rate_msps']}MS/s：音频 P50 {a['p50_ms']}ms / "
            f"P99 {a['p99_ms']}ms，抖动 {a['jitter_p99_minus_p50_ms']}ms；"
            f"瀑布 P99 {w['p99_ms']}ms；最大写出空档 "
            f"{a['max_write_gap_s']}s → "
            f"{'达标' if row['ok'] else '超阈'}"
        )
    return "；".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="端到端延迟与抖动（合成信号）")
    ap.add_argument("--quick", action="store_true", help="CI 快速档")
    ap.add_argument("--seconds", type=float, default=RUN_SECONDS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    report = build_report(seconds=args.seconds, quick=args.quick)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(ROOT, "output", "bench")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(out_dir, f"latency_jitter_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(_conclusion(report))
    for row in report["rates"]:
        for cause in row["causes"]:
            print("  成因提示:", cause)
    print(f"结果: {out_path}")
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

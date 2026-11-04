# -*- coding: utf-8 -*-
"""DSP 吞吐基线（P6.1）：0.96 / 1.2 / 2.4 MS/s 下各环节处理速率与余量。

测量对象（全部为生产代码，非替身）：

  * ``fft_2048``        SpectrumDataGenerator.push_iq（窗×FFT×dBFS×512 bin 抽取）
  * ``vfo_nbfm``        VFO.process（NCO 搬频 + 有理重采样 + 12.5 kHz Nuttall LPF）
  * ``vfo_resample``    仅 NCO + 有理重采样（带宽=输出率，跳过 LPF），隔离重采样成本
  * ``wfm_rx``          WFMReceiver.process（信道 FIR + 鉴频 + 48k 重采样 + 去加重）
  * ``record_write``    cf32 交织 + 缓冲写盘（与 RecordTap 落盘路径同构）

两个口径：

1. **微基准**：单线程逐环节计时。block = 16384 样本。实时倍率
   ``RTF = (block/sr) / 单块耗时``；RTF > 1 表示该环节单独跑得比实时快。
2. **端到端可持续速率**：真 ReceivePipeline（reader → splitter → FFT / WFM
   解调 / 落盘 三线程）喂**不定速**合成 IQ，让它自然被消费者反压；统计
   ``实际产出样本/墙钟``。若 < 标称采样率即「峰值负载下掉样本」。
   输入为合成信号（报告中显式标注），本基准只考察链路吞吐，不验证信号内容。

阈值（测试钉住，禁止悄悄放宽）：

  * ``MIN_STAGE_RTF = 1.15``：每个环节至少 15% 余量；
  * ``MIN_E2E_RTF = 1.00``：端到端可持续速率不得低于标称采样率；
  * ``E2E_MAX_GAP_S = 0.25``：落盘相邻块最长空档（超 5s 才是数据洞，这里更严）。

结果写机器可读 JSON：``output/bench/dsp_throughput_<ts>.json``。

用法::

    python scripts/bench_dsp_throughput.py                # 三档速率，5s 端到端
    python scripts/bench_dsp_throughput.py --quick        # CI 快速档（0.96MS/s，1s）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

import numpy as np  # noqa: E402

# ---- 固定参数（测试 test_bench_dsp_throughput.py 逐项钉住） ----
RATES = (960000.0, 1200000.0, 2400000.0)
BLOCK_SAMPLES = 16384
MIN_STAGE_RTF = 1.15
MIN_E2E_RTF = 1.00
E2E_MAX_GAP_S = 0.25
RECORD_TOLERANCE = 0.01
E2E_SECONDS = 5.0
QUICK_SECONDS = 1.0
MICRO_REPS = 20
MICRO_WARMUP = 3

_STAGE_NOTES = {
    "fft_2048": "窗×FFT×fftshift×dBFS×512bin 抽取（每块一帧，仅取前 2048 样本）",
    "vfo_nbfm": "NCO 搬频 + 有理重采样到 48k + 12.5kHz Nuttall LPF",
    "vfo_resample": "仅 NCO 搬频 + 有理重采样（带宽=输出率，跳过 LPF）",
    "wfm_rx": "信道 FIR + 正交鉴频 + 48k 重采样 + 去加重",
    "record_write": "complex64→float32 交织 + 缓冲写盘（cf32）",
}


def _synth_iq(n: int, seed: int = 20261104) -> np.ndarray:
    """合成复 IQ：单载波 + 高斯噪声（吞吐基准只关心处理成本）。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.float64)
    carrier = np.exp(1j * 2.0 * np.pi * 0.1 * t / n)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.02
    return (carrier + noise).astype(np.complex64)


def _time_calls(fn, reps: int = MICRO_REPS, warmup: int = MICRO_WARMUP) -> float:
    """返回单次调用中位耗时（秒）。中位数抑制偶发 GC/调度尖峰。"""
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t)
    return statistics.median(samples)


def _make_stage_fns(sr: float, block: int, rec_path: str):
    """构造各环节的可调用对象（生产类），返回 (dict, cleanup)。"""
    from mbdsdr_ai.dsp import VFO, WFMReceiver
    from spectrum_widget import SpectrumDataGenerator

    iq = _synth_iq(block)
    gen = SpectrumDataGenerator(num_bins=512)
    gen.fft_size = 2048
    vfo = VFO(sr, 48000, 12500, 4500.0)
    vfo_resample = VFO(sr, 48000, 48000, 4500.0)
    wfm = WFMReceiver(sr, audio_sr=48000)
    rec_file = open(rec_path, "wb")

    def record():
        arr = np.asarray(iq, dtype=np.complex64)
        inter = np.empty(arr.size * 2, dtype=np.float32)
        inter[0::2] = arr.real
        inter[1::2] = arr.imag
        rec_file.write(inter.tobytes())

    fns = {
        "fft_2048": lambda: gen.push_iq(iq, 98.5e6, sr),
        "vfo_nbfm": lambda: vfo.process(iq),
        "vfo_resample": lambda: vfo_resample.process(iq),
        "wfm_rx": lambda: wfm.process(iq),
        "record_write": record,
    }

    def cleanup():
        try:
            rec_file.close()
        except Exception:
            pass

    return fns, cleanup


def bench_micro(sr: float, block: int = BLOCK_SAMPLES,
                reps: int = MICRO_REPS) -> dict:
    """单线程逐环节计时，返回每环节耗时与实时倍率。"""
    fd, path = tempfile.mkstemp(suffix=".cf32")
    os.close(fd)
    fns, cleanup = _make_stage_fns(sr, block, path)
    block_period = block / sr
    out = {}
    try:
        for name, fn in fns.items():
            sec_per_call = _time_calls(fn, reps=reps)
            out[name] = {
                "median_ms": round(sec_per_call * 1e3, 3),
                "block_period_ms": round(block_period * 1e3, 3),
                "realtime_factor": round(block_period / sec_per_call, 2)
                if sec_per_call > 0 else None,
                "rtf_ok": (block_period / sec_per_call) >= MIN_STAGE_RTF
                if sec_per_call > 0 else False,
                "note": _STAGE_NOTES.get(name, ""),
            }
    finally:
        cleanup()
        try:
            os.unlink(path)
        except OSError:
            pass
    return out


class _UnpacedSource:
    """不定速合成源：读者要多少给多少，吞吐上限由下游消费者反压决定。"""

    def __init__(self, sr: float):
        self.sr = float(sr)
        self.produced = 0
        self._rng = np.random.default_rng(20261104)
        self._phase = 0.0

    def read(self, n: int) -> np.ndarray:
        idx = np.arange(n, dtype=np.float64)
        tone = np.sin(2.0 * np.pi * 1000.0 * (self.produced + idx) / 48000.0)
        self._phase += 2.0 * np.pi * 75000.0 * float(np.sum(tone)) / self.sr
        carrier = np.exp(1j * self._phase).astype(np.complex64)
        noise = (self._rng.standard_normal(n)
                 + 1j * self._rng.standard_normal(n)) * 0.02
        self.produced += n
        return (carrier + noise).astype(np.complex64)


class _CountingPlayer:
    """音频落盘替身：只统计（不打开声卡；available=True 走全门控）。"""

    available = True

    def __init__(self):
        self.blocks = 0
        self.samples = 0
        self.last_write_ts = time.monotonic()
        self.max_gap_s = 0.0

    def write(self, audio) -> None:
        now = time.monotonic()
        self.max_gap_s = max(self.max_gap_s, now - self.last_write_ts)
        self.last_write_ts = now
        self.blocks += 1
        self.samples += int(np.asarray(audio).size)

    def set_muted(self, _muted: bool) -> None:
        pass


class _Cf32Recorder:
    """落盘替身：cf32 交织写临时文件，统计字节/块数与最大空档。"""

    def __init__(self, path: str):
        self._f = open(path, "wb")
        self.bytes = 0
        self.blocks = 0
        self.last_ts = time.monotonic()
        self.max_gap_s = 0.0

    def __call__(self, iq: np.ndarray) -> None:
        now = time.monotonic()
        self.max_gap_s = max(self.max_gap_s, now - self.last_ts)
        self.last_ts = now
        arr = np.asarray(iq, dtype=np.complex64)
        inter = np.empty(arr.size * 2, dtype=np.float32)
        inter[0::2] = arr.real
        inter[1::2] = arr.imag
        self._f.write(inter.tobytes())
        self.bytes += inter.nbytes
        self.blocks += 1

    def close(self):
        try:
            self._f.close()
        except Exception:
            pass


def evaluate_e2e(elapsed: float, produced: int, sr: float,
                 recorded_bytes: int, max_gap_s: float,
                 lingering) -> dict:
    """端到端判定的纯函数：阈值口径与常量绑定，便于单测。"""
    sustained = produced / elapsed if elapsed > 0 else 0.0
    rtf = sustained / sr if sr > 0 else 0.0
    expect_bytes = produced * 8
    byte_err = (abs(recorded_bytes - expect_bytes) / expect_bytes
                if expect_bytes else 0.0)
    verdicts = {
        "sustained_rtf_ge_1": rtf >= MIN_E2E_RTF,
        "record_bytes_match": byte_err <= RECORD_TOLERANCE,
        "record_no_data_hole": max_gap_s <= E2E_MAX_GAP_S,
        "no_lingering_threads": not lingering,
    }
    return {
        "sustained_msps": round(sustained / 1e6, 3),
        "sustained_rtf": round(rtf, 2),
        "record_byte_error_pct": round(byte_err * 100.0, 2),
        "verdicts": verdicts,
        "ok": all(verdicts.values()),
    }


def bench_e2e(sr: float, seconds: float = E2E_SECONDS) -> dict:
    """真流水线不定速反压跑：实际可持续速率 vs 标称采样率。"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QThread
    from receive_pipeline import ReceivePipeline, SharedDemodConfig
    from spectrum_widget import SpectrumDataGenerator

    app = QApplication.instance() or QApplication(sys.argv[:1])

    src = _UnpacedSource(sr)
    gen = SpectrumDataGenerator(num_bins=512)
    gen.fft_size = 2048
    player = _CountingPlayer()
    fd, rec_path = tempfile.mkstemp(suffix=".cf32")
    os.close(fd)
    recorder = _Cf32Recorder(rec_path)
    cfg = SharedDemodConfig(mode="WFM", auto_squelch=True, agc_speed="medium")

    pipe = ReceivePipeline(
        read_fn=src.read, sr=sr, gen=gen,
        center_getter=lambda: 98.5e6,
        main_vfo_dsp=None, vfo2_dsp=None,
        player=player, config=cfg, on_record_iq=recorder,
    )
    cpu0 = time.process_time()
    pipe.start()
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < seconds:
            app.processEvents()
            time.sleep(0.01)
    finally:
        elapsed = time.monotonic() - t0
        pipe.shutdown()
        exit_deadline = time.monotonic() + 10.0
        lingering = []
        for th in pipe.findChildren(QThread):
            while th.isRunning() and time.monotonic() < exit_deadline:
                time.sleep(0.05)
            if th.isRunning():
                lingering.append(type(th).__name__)
        recorder.close()
    cpu_seconds = time.process_time() - cpu0

    verdict = evaluate_e2e(elapsed, src.produced, sr, recorder.bytes,
                           recorder.max_gap_s, lingering)
    try:
        os.unlink(rec_path)
    except OSError:
        pass
    return {
        "wall_s": round(elapsed, 2),
        "sustained_msps": verdict["sustained_msps"],
        "sustained_rtf": verdict["sustained_rtf"],
        "margin_pct": round((verdict["sustained_rtf"] - 1.0) * 100.0, 1),
        "cpu_seconds": round(cpu_seconds, 2),
        "cpu_utilization_pct": round(100.0 * cpu_seconds / max(elapsed, 1e-9), 1),
        "produced_samples": src.produced,
        "recorded_bytes": recorder.bytes,
        "recorded_blocks": recorder.blocks,
        "record_byte_error_pct": verdict["record_byte_error_pct"],
        "record_max_gap_s": round(recorder.max_gap_s, 3),
        "audio_blocks": player.blocks,
        "audio_max_gap_s": round(player.max_gap_s, 3),
        "lingering_threads": lingering,
        "verdicts": verdict["verdicts"],
        "ok": verdict["ok"],
    }


def profile_hotspots(sr: float = 2400000.0, iterations: int = 10,
                     top: int = 10) -> list:
    """cProfile 热点归因（模块:函数粒度，按自身耗时 tottime 排序）。

    只做归因，不用它当性能数字：cProfile 本身有插桩开销。
    """
    import cProfile

    fd, path = tempfile.mkstemp(suffix=".cf32")
    os.close(fd)
    fns, cleanup = _make_stage_fns(sr, BLOCK_SAMPLES, path)
    pr = cProfile.Profile()
    try:
        pr.enable()
        for _ in range(iterations):
            for fn in fns.values():
                fn()
        pr.disable()
    finally:
        cleanup()
        try:
            os.unlink(path)
        except OSError:
            pass
    import pstats
    rows = []
    total = 0.0
    for (_file, _line, func), stat in pstats.Stats(pr).stats.items():
        # stat = (call_count, primitive_calls, tottime, cumtime, callers)
        tt = stat[2]
        rows.append({
            "module": os.path.basename(_file),
            "function": func,
            "tottime_s": tt,
        })
        total += tt
    rows.sort(key=lambda r: r["tottime_s"], reverse=True)
    out = []
    for row in rows[:top]:
        out.append({
            "module": row["module"],
            "function": row["function"],
            "tottime_ms": round(row["tottime_s"] * 1e3, 2),
            "share_pct": round(100.0 * row["tottime_s"] / total, 1)
            if total > 0 else 0.0,
        })
    return out


def build_report(rates=RATES, seconds: float = E2E_SECONDS,
                 reps: int = MICRO_REPS, quick: bool = False,
                 include_hotspots: bool = False) -> dict:
    """跑完整基准并返回机器可读报告（纯数据，不落盘）。"""
    if quick:
        rates = RATES[:1]
        seconds = QUICK_SECONDS
        reps = 5
    report = {
        "schema": "mbdsdr.bench.dsp_throughput/1",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "numpy": np.__version__,
        },
        "method": (
            "微基准：单线程逐环节计时，block=16384，中位数为单块耗时；"
            "RTF=(block/sr)/单块耗时。端到端：真 ReceivePipeline 三线程，"
            "不定速合成 IQ 源被消费者反压，统计实际可持续样本率。"
            "输入为合成信号（仅测吞吐，不验信号内容）。"
        ),
        "block_samples": BLOCK_SAMPLES,
        "thresholds": {
            "min_stage_rtf": MIN_STAGE_RTF,
            "min_e2e_rtf": MIN_E2E_RTF,
            "e2e_max_gap_s": E2E_MAX_GAP_S,
            "record_tolerance": RECORD_TOLERANCE,
        },
        "rates": [],
    }
    if include_hotspots:
        # 热点归因跑在最高速率档（峰载下 CPU 分布最有代表性）
        report["hotspots"] = profile_hotspots(sr=max(rates))
    for sr in rates:
        micro = bench_micro(sr, reps=reps)
        e2e = bench_e2e(sr, seconds=seconds)
        stage_min = min(v["realtime_factor"] for v in micro.values()
                        if v["realtime_factor"] is not None)
        rows = {
            "sample_rate": sr,
            "sample_rate_msps": round(sr / 1e6, 2),
            "block_period_ms": round(BLOCK_SAMPLES / sr * 1e3, 2),
            "stages": micro,
            "min_stage_rtf": stage_min,
            "e2e": e2e,
            "verdicts": {
                "all_stages_margin_ge_15pct": stage_min >= MIN_STAGE_RTF,
                "e2e_sustains_nominal": e2e["verdicts"]["sustained_rtf_ge_1"],
                "e2e_no_data_hole": e2e["verdicts"]["record_no_data_hole"],
                "e2e_no_lingering": e2e["verdicts"]["no_lingering_threads"],
            },
        }
        rows["ok"] = all(rows["verdicts"].values())
        report["rates"].append(rows)
    report["overall_ok"] = all(r["ok"] for r in report["rates"])
    return report


def _conclusion(report: dict) -> str:
    parts = []
    for row in report["rates"]:
        e2e = row["e2e"]
        state = "不掉样本" if row["ok"] else "存在瓶颈"
        parts.append(
            f"{row['sample_rate_msps']}MS/s：可持续 {e2e['sustained_msps']}MS/s"
            f"（{e2e['sustained_rtf']}x，余量 {e2e['margin_pct']}%），"
            f"最慢环节 {row['min_stage_rtf']}x → {state}"
        )
    return "；".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DSP 吞吐基线（合成信号，无硬件）")
    ap.add_argument("--quick", action="store_true", help="CI 快速档：单速率 1s")
    ap.add_argument("--seconds", type=float, default=E2E_SECONDS,
                    help="端到端每档时长（秒）")
    ap.add_argument("--reps", type=int, default=MICRO_REPS,
                    help="微基准每环节重复次数")
    ap.add_argument("--out", default=None, help="JSON 输出路径")
    ap.add_argument("--profile", action="store_true",
                    help="附带 cProfile 热点归因（模块:函数）")
    args = ap.parse_args(argv)

    report = build_report(seconds=args.seconds, reps=args.reps,
                          quick=args.quick,
                          include_hotspots=args.profile)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(ROOT, "output", "bench")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(out_dir, f"dsp_throughput_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(_conclusion(report))
    print(f"结果: {out_path}")
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

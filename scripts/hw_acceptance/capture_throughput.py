#!/usr/bin/env python3
"""采集链吞吐探针：块大小 × 读法（同步 / 读线程 / 异步）对照。

回答一个问题：R3 的「请求 6 s 只采到 5.0–5.2 s」丢在哪。四种读法同台同参数
（默认 93.305 MHz / 2.4 MS/s / 请求 6 s）逐块记录读耗时与转换耗时：

  sync+conv   旧 ring3 路径：读块 -> 立刻把该块转 MPX -> 再读（转换期间不读）
  sync_only   只读不转换（同步块读的 USB 天花板）
  thread      独立读线程 + 有界队列，消费线程只做 MPX 转换（ring3 现在的做法）
  async       librtlsdr 异步读（rtlsdr_read_async）+ 队列；消费线程做
              uint8->complex64 与 MPX（与 OpenDevice 同口径）

判定依据：丢样率应与「转换耗时 / 墙钟」同阶；sync_only 与 async 的吞吐差
即「同步块读传输间死区」，与 Python 侧无关。

注意：async 模式绕过 controller 直接开 pyrtlsdr（`device_index=0`），只用于
对照，不用于验收；rtl_tcp 源没有异步读。

用法::

    python scripts/hw_acceptance/capture_throughput.py --seconds 6 \
        --blocks 65536,262144,1048576 --modes sync,sync_only,thread,async

每行一条 JSON；`--out` 缺省写到 `artifacts/hw_acceptance/capture_throughput/`。
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from pathlib import Path

import numpy as np

import common

FS = 2.4e6
MPX_FS = 200000


def to_mpx(x, fs: float = FS):
    from math import gcd

    from scipy import signal as sg

    x = np.asarray(x, dtype=np.complex128)
    x = x - np.mean(x)
    dphi = np.angle(x[1:] * np.conj(x[:-1]))
    mpx = dphi * fs / (2.0 * np.pi)
    g = gcd(int(round(fs)), MPX_FS)
    return sg.resample_poly(mpx, MPX_FS // g, int(round(fs)) // g)


def _stats(v):
    if not v:
        return {}
    a = np.asarray(v, dtype=float) * 1000.0
    return {"n": int(a.size), "median_ms": round(float(np.median(a)), 2),
            "p95_ms": round(float(np.percentile(a, 95)), 2),
            "max_ms": round(float(a.max()), 2), "sum_s": round(float(a.sum()) / 1000.0, 3)}


def _summary(mode, block, seconds, n, wall, reads, convs, **extra):
    out = {
        "mode": mode, "block": int(block), "requested_s": seconds,
        "samples": int(n), "captured_s": round(n / FS, 3), "wall_s": round(wall, 3),
        "throughput_msps": round(n / wall / 1e6, 3) if wall > 0 else None,
        "drop_pct": round(max(0.0, 1.0 - (n / FS) / seconds) * 100.0, 1),
        "reads": _stats(reads), "convs": _stats(convs),
    }
    out.update(extra)
    return out


def _close(ctrl):
    try:
        ctrl.disconnect()
    except Exception:
        pass


def _open_ctrl(freq, gain):
    """每次重解析设备；上一进程刚释放句柄时 DeviceManager 可能给出占位序列号。"""
    last = None
    for _ in range(6):
        devs = common.real_devices()
        if not devs:
            time.sleep(0.4)
            continue
        try:
            ctrl = common.open_controller(f"rtlsdr:{devs[0].serial}")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4)
            continue
        try:
            ctrl.set_sample_rate(FS)
            ctrl.set_frequency(freq)
            ctrl.set_gain(gain)
        except Exception as e:  # noqa: BLE001
            last = e
            _close(ctrl)
            time.sleep(0.4)
            continue
        time.sleep(0.3)
        return ctrl
    raise last if last else RuntimeError("设备枚举始终为空")


def run_sync(block, seconds, freq, gain, convert=True):
    ctrl = _open_ctrl(freq, gain)
    try:
        ctrl.read_iq(block)  # 排空驱动里上一轮残留（librtlsdr 内部缓冲最多 ~0.8 s）
        reads, convs, n = [], [], 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            a = time.perf_counter()
            blk = ctrl.read_iq(block)
            b = time.perf_counter()
            reads.append(b - a)
            if blk is None or len(blk) < 1024:
                break
            n += len(blk)
            if convert:
                c = time.perf_counter()
                to_mpx(blk)
                convs.append(time.perf_counter() - c)
        wall = time.perf_counter() - t0
        return _summary("sync+conv" if convert else "sync_only", block, seconds,
                        n, wall, reads, convs)
    finally:
        _close(ctrl)


def run_thread(block, seconds, freq, gain, qsize=6):
    ctrl = _open_ctrl(freq, gain)
    try:
        ctrl.read_iq(block)
        q: queue.Queue = queue.Queue(maxsize=qsize)
        stop = threading.Event()
        err: list[str] = []
        reads: list[float] = []

        def reader():
            try:
                while not stop.is_set():
                    a = time.perf_counter()
                    blk = ctrl.read_iq(block)
                    reads.append(time.perf_counter() - a)
                    if blk is None or len(blk) < 1024:
                        err.append("read_iq 返回空块")
                        break
                    q.put(blk)
            except Exception as e:  # noqa: BLE001
                err.append(f"{type(e).__name__}: {e}")

        th = threading.Thread(target=reader, name="probe-iq-reader", daemon=True)
        t0 = time.perf_counter()
        th.start()
        convs, n = [], 0
        while time.perf_counter() - t0 < seconds:
            try:
                blk = q.get(timeout=0.2)
            except queue.Empty:
                if err:
                    break
                continue
            n += len(blk)
            c = time.perf_counter()
            to_mpx(blk)
            convs.append(time.perf_counter() - c)
        wall = time.perf_counter() - t0
        stop.set()
        th.join(timeout=2.0)
        return _summary("thread+queue", block, seconds, n, wall, reads, convs,
                        queue_left=q.qsize(), reader_error="; ".join(err) or None)
    finally:
        _close(ctrl)


def run_async(block, seconds, freq, gain, qsize=6):
    from rtlsdr import RtlSdr

    sdr = RtlSdr(device_index=0)
    sdr.sample_rate = FS
    sdr.center_freq = freq
    sdr.gain = gain
    time.sleep(0.3)
    q: queue.Queue = queue.Queue(maxsize=qsize)
    err: list[str] = []
    cb_n = [0]

    def cb(buf, _ctx):
        try:
            q.put_nowait(bytes(buf))
            cb_n[0] += 1
        except queue.Full:
            pass  # 满了即丢（与「消费跟不上」同义，会计入丢样）

    def runner():
        try:
            sdr.read_bytes_async(cb, num_bytes=int(block) * 2)
        except Exception as e:  # noqa: BLE001
            err.append(f"{type(e).__name__}: {e}")

    th = threading.Thread(target=runner, name="probe-async", daemon=True)
    th.start()
    # 不预填队列：预填会让窗口内计入窗口前采到的样本，把 drop 掩盖成 0。
    convs, n = [], 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        try:
            raw = q.get(timeout=0.2)
        except queue.Empty:
            if err:
                break
            continue
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 2)
        i = arr[:, 0].astype(np.float32)
        qd = arr[:, 1].astype(np.float32)
        x = (((i - 128.0) / 128.0) + 1j * ((qd - 128.0) / 128.0)).astype(np.complex64)
        c = time.perf_counter()
        to_mpx(x)
        convs.append(time.perf_counter() - c)
        n += len(x)
    wall = time.perf_counter() - t0
    try:
        sdr.cancel_read_async()
    except Exception as e:  # noqa: BLE001
        err.append(f"cancel: {type(e).__name__}: {e}")
    th.join(timeout=3.0)
    try:
        sdr.close()
    except Exception:
        pass
    return _summary("async+queue", block, seconds, n, wall, [], convs,
                    callbacks=cb_n[0], queue_left=q.qsize(),
                    reader_error="; ".join(err) or None)


MODES = {
    "sync": lambda *a: run_sync(*a, convert=True),
    "sync_only": lambda *a: run_sync(*a, convert=False),
    "thread": run_thread,
    "async": run_async,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", type=float, default=93.305e6)
    ap.add_argument("--gain", type=float, default=17.9)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--blocks", default="65536,262144,1048576")
    ap.add_argument("--modes", default="sync,sync_only,thread,async")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mode-runs", default="sync=3,thread=3,async=1,sync_only=1",
                    help="逐模式覆盖 runs，如 sync=3,async=1")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if not common.device_present():
        print("[probe] 无设备，退出（不伪造数字）")
        return 2

    # 预热：resample_poly 的滤波设计与 scipy 首次导入约 0.7 s，若落在采集窗口内
    # 会把「一次性成本」算成丢样（2 s 窗口实测 39.9%，其中 732 ms 是首块转换）。
    t_w = time.perf_counter()
    to_mpx(np.zeros(262144, dtype=np.complex64))
    print(f"[probe] 预热 {time.perf_counter() - t_w:.3f} s")

    per_mode = {}
    for kv in args.mode_runs.split(","):
        if "=" in kv:
            k, v = kv.split("=")
            per_mode[k.strip()] = int(v)

    rows = []
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        for block in [int(b) for b in args.blocks.split(",")]:
            runs = per_mode.get(mode, args.runs)
            for i in range(runs):
                row = MODES[mode](block, args.seconds, args.freq, args.gain)
                row["run"] = i + 1
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False))
                time.sleep(0.3)

    out = args.out or f"{common.artifact_dir('capture_throughput')}/throughput.json"
    Path(out).write_text(
        json.dumps({"freq_hz": args.freq, "gain_db": args.gain,
                    "requested_s": args.seconds, "runs": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 汇总 ==")
    print(f"{'mode':<11}{'block':>9}{'run':>4}{'captured':>10}{'wall':>8}"
          f"{'MS/s':>8}{'drop%':>7}{'read_med':>10}{'read_p95':>10}{'conv_med':>10}")
    for r in rows:
        print(f"{r['mode']:<11}{r['block']:>9}{r['run']:>4}{r['captured_s']:>10.3f}"
              f"{r['wall_s']:>8.3f}{r['throughput_msps']:>8.3f}{r['drop_pct']:>7.1f}"
              f"{r['reads'].get('median_ms', 0):>10.1f}{r['reads'].get('p95_ms', 0):>10.1f}"
              f"{r['convs'].get('median_ms', 0):>10.1f}")
    print(f"[probe] 明细 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

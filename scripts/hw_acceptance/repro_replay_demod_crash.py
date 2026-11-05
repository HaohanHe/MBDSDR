#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""离线复现：10 s 真机录音回放路径的进程级崩溃（不碰真机）。

背景（2026-11-06，真机录音 ``D:\\mbdsdr\\tmp\\rec_play\\fm_10s.sigmf-data``，
20,316,160 complex64 样本，fs=2,048,000 Hz，9.92 s）：

* 当时写法：``IQPlayback`` 按 1,048,576 样本反复 ``read_samples`` → ``pb.close()``
  → ``np.concatenate(chunks)`` → 进程以 **0xC0000005（访问冲突）** 退出。
* 根因（本脚本三个模式钉住）：``read_samples`` 在 dtype 相同时走
  ``np.asarray(memmap[slice], dtype=complex64)``，拿到的是 **memmap 视图而非拷贝**；
  ``close()`` 关掉映射后这些视图悬空，下一次 ``concatenate`` 读悬空内存 → AV。
  **与 ``wfm_broadcast_demod`` 无关**：只要在 close 前拷贝/用完，整段 20.3M
  一次性解调可正常完成。

用法（PowerShell）
------------------
    $env:PYTHONPATH='d:\\mbdsdr\\MBDSDR'
    python scripts/hw_acceptance/repro_replay_demod_crash.py --mode repro        # 预期 3（可读异常）
    python scripts/hw_acceptance/repro_replay_demod_crash.py --mode safe-oneshot # 预期 OK（整段解调）
    python scripts/hw_acceptance/repro_replay_demod_crash.py --mode chunked      # 预期 OK（4M 分块）

判据（修复后）
--------------
* ``repro``：走零拷贝默认路径，``close()`` 抛 ``PlaybackViewsInUseError``
  （消息含「还有 N 个视图未被使用完」），脚本捕获后**退出码 3**。
  **不得**再出现 ``-1073741819``（0xC0000005）。
* ``safe-oneshot`` / ``chunked``：``read_samples(copy=True)`` 取副本，
  退出 0，打印 audio 样本数与 RMS（与修复前逐位一致：476,160 / 0.24147、
  476,160 / 0.24763）。

修复前（2026-11-06 现场记录）：``repro`` 在 ``[3/5]`` 后进程级 AV，退出码
``-1073741819``，faulthandler 打印 ``Windows fatal exception: access violation``。
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import sys
import time

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from mbdsdr_ai.dsp import wfm_broadcast_demod  # noqa: E402
from mbdsdr_ai.playback import IQPlayback  # noqa: E402

DEFAULT_PATH = r"D:\mbdsdr\tmp\rec_play\fm_10s.sigmf-data"
DEFAULT_FS = 2_048_000.0
READ_CHUNK = 1 << 20      # 1,048,576：与崩溃当时的读取粒度一致
DEMOD_CHUNK = 4_194_304   # 4,194,304：与 2026-11-06 分块成功路径一致


def _read_chunks(pb: IQPlayback, copy: bool) -> tuple[list[np.ndarray], int]:
    chunks = []
    total = 0
    i = 0
    while True:
        c = pb.read_samples(READ_CHUNK, copy=copy)
        if c is None or len(c) == 0:
            break
        i += 1
        chunks.append(c)
        total += int(len(c))
        print("  读块 %d: cursor=%d n=%d dtype=%s"
              % (i, total - len(c), len(c), c.dtype), flush=True)
    return chunks, total


def main() -> int:
    ap = argparse.ArgumentParser(description="离线复现回放路径 0xC0000005")
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--fs", type=float, default=DEFAULT_FS)
    ap.add_argument("--mode",
                    choices=("repro", "safe-oneshot", "chunked"), default="repro")
    args = ap.parse_args()

    faulthandler.enable(all_threads=True)
    if not os.path.exists(args.path):
        print("FAIL 文件不存在: %s" % args.path, flush=True)
        return 2

    t0 = time.time()
    print("[1/5] 打开 %s" % args.path, flush=True)
    pb = IQPlayback()
    if not pb.open(args.path):
        print("FAIL IQPlayback.open: %s" % getattr(pb, "last_error", ""), flush=True)
        return 2
    rate = float(getattr(pb, "sample_rate", 0) or args.fs)
    print("[2/5] fs=%.0f Hz，逐块读取（块大小 %d，copy=%s）"
          % (rate, READ_CHUNK, args.mode != "repro"), flush=True)
    chunks, total = _read_chunks(pb, copy=(args.mode != "repro"))

    if args.mode == "repro":
        # 误用现场：默认零拷贝（视图），却先 close 再 concatenate。
        # 修复后这里不会再 AV，而是 close() 抛出可读的 PlaybackViewsInUseError。
        print("[3/5] close（仍有 %d 个未消费视图）…" % len(chunks), flush=True)
        try:
            pb.close()
        except RuntimeError as e:
            print("[4/5] 可读异常（非崩溃码）：%s: %s"
                  % (type(e).__name__, e), flush=True)
            return 3
        x = np.concatenate(chunks)
        print("[4/5] 未抛异常：concatenate 返回 %s" % (x.shape,), flush=True)
        audio = np.asarray(wfm_broadcast_demod(x, rate, audio_sr=48_000), dtype=np.float32)
        print("[5/5] audio=%d rms=%.5f elapsed=%.1fs"
              % (audio.size, float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0,
                 time.time() - t0), flush=True)
        return 0

    if args.mode == "safe-oneshot":
        # 安全路径：read_samples(copy=True) 返回独立副本，close 后仍可持有
        print("[3/5] 整段 concatenate（20,316,160 样本一次性解调）", flush=True)
        x = np.concatenate(chunks)
        pb.close()
        audio = np.asarray(wfm_broadcast_demod(x, rate, audio_sr=48_000), dtype=np.float32)
        print("[4/5] audio=%d（%.3f s）rms=%.5f elapsed=%.1fs"
              % (audio.size, audio.size / 48_000.0,
                 float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0,
                 time.time() - t0), flush=True)
        print("[5/5] OK", flush=True)
        return 0

    # chunked：对照路径，与 2026-11-06 成功路径一致（4,194,304 样本一块）
    print("[3/5] 分块解调 chunk=%d（共 %d 块）"
          % (DEMOD_CHUNK, (total + DEMOD_CHUNK - 1) // DEMOD_CHUNK), flush=True)
    audios = []
    # 安全路径：chunks 已是副本（copy=True），close 后可继续持有/解调。
    x = np.concatenate(chunks)
    pb.close()
    for i in range(0, len(x), DEMOD_CHUNK):
        seg = x[i:i + DEMOD_CHUNK]
        a = np.asarray(wfm_broadcast_demod(seg, rate, audio_sr=48_000), dtype=np.float32)
        audios.append(a)
        print("  块 %d: 输入 %d → audio %d" % (i // DEMOD_CHUNK + 1, len(seg), a.size),
              flush=True)
    audio = np.concatenate(audios) if audios else np.empty(0, dtype=np.float32)
    print("[4/5] audio=%d（%.3f s）rms=%.5f elapsed=%.1fs"
          % (audio.size, audio.size / 48_000.0,
             float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0,
             time.time() - t0), flush=True)
    print("[5/5] OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
信号活动值守 / 触发录制（signal watch / trigger record）
=======================================================

固定频率守听，先自适应估计噪声底，再在信号出现（功率越过门限并持续
最短确认时间）时自动开始录制，信号消失（持续低于门限超过拖尾时间）
或达到最长录制时长时自动停止。录制带触发前 pre-roll 缓冲，不会丢掉
信号开头。用于：

- 守听突发信号、找干扰源、抓间歇发射；
- 守 SSTV/FT8/卫星过境等时隙性信号；
- 无人值守自动取证。

判定核心与采集编排分离，采集通过 acquire(n)->IQ 回调注入，
便于离线用合成信号严格验证；真实设备由工具层包 set_frequency/read_samples。
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Dict, Optional

import numpy as np


def band_power_db(iq: np.ndarray) -> float:
    """带内平均功率（dB，相对值）。"""
    iq = np.asarray(iq)
    p = float(np.mean(np.abs(iq) ** 2))
    return 10.0 * np.log10(p + 1e-20)


def activity_windows_from_power(power_db: np.ndarray, block_s: float,
                                threshold_db: float,
                                min_active_s: float = 0.15,
                                hang_s: float = 0.5):
    """
    纯函数：在一条按块排列的功率序列上标出活动区间（块下标，闭区间起止）。
    用 hang（拖尾）合并短暂衰落，用 min_active 去毛刺。返回 [(start, end), ...]。
    """
    above = np.asarray(power_db) >= threshold_db
    hang_blocks = max(1, int(round(hang_s / max(block_s, 1e-9))))
    min_blocks = max(1, int(round(min_active_s / max(block_s, 1e-9))))
    windows = []
    n = len(above)
    i = 0
    while i < n:
        if not above[i]:
            i += 1
            continue
        start = i
        j = i
        gap = 0
        k = i
        while k < n:
            if above[k]:
                gap = 0
                j = k
            else:
                gap += 1
                if gap > hang_blocks:
                    break
            k += 1
        if (j - start + 1) >= min_blocks:
            windows.append((start, j))
        i = j + 1
    return windows


def watch_capture(
    acquire: Callable[[int], np.ndarray],
    sample_rate: float,
    block_n: int = 8192,
    noise_dwell_s: float = 0.5,
    max_wait_s: float = 30.0,
    max_record_s: float = 10.0,
    margin_db: float = 8.0,
    min_active_s: float = 0.15,
    hang_s: float = 0.5,
    preroll_s: float = 0.5,
    threshold_db: Optional[float] = None,
) -> Dict:
    """
    单频值守抓取一次活动。

    acquire(n) -> np.ndarray(complex)，调用方负责已把设备调到目标频率。
    threshold_db 给定时跳过自适应噪声估计，直接用绝对门限。
    返回 dict：
      status: "triggered" | "timeout"
      samples: 触发时为含 pre-roll 的复数 IQ，否则空数组
      noise_floor_db / threshold_db / peak_db / recorded_s / waited_s
    """
    sr = float(sample_rate)
    block_s = block_n / sr
    noise_blocks = max(4, int(round(noise_dwell_s / block_s)))
    max_wait_blocks = max(1, int(round(max_wait_s / block_s)))
    min_act_blocks = max(1, int(round(min_active_s / block_s)))
    hang_blocks = max(1, int(round(hang_s / block_s)))
    preroll_blocks = max(1, int(round(preroll_s / block_s)))
    max_record_blocks = max(1, int(round(max_record_s / block_s)))

    ring = deque(maxlen=preroll_blocks)

    # ---- 噪声底估计 ----
    noise_powers = []
    for _ in range(noise_blocks):
        x = np.asarray(acquire(block_n), dtype=np.complex128)
        if len(x) == 0:
            continue
        ring.append(x)
        noise_powers.append(band_power_db(x))
    if not noise_powers:
        return {"status": "timeout", "samples": np.zeros(0, dtype=np.complex128),
                "noise_floor_db": 0.0, "threshold_db": 0.0, "peak_db": 0.0,
                "recorded_s": 0.0, "waited_s": 0.0,
                "reason": "采集无数据"}
    noise_floor = float(np.median(noise_powers))
    thr = float(threshold_db) if threshold_db is not None else noise_floor + margin_db

    # ---- 等待 + 录制状态机 ----
    state = "IDLE"
    candidate = 0
    hang = 0
    rec_blocks = []
    waited_blocks = 0

    while waited_blocks < max_wait_blocks:
        x = np.asarray(acquire(block_n), dtype=np.complex128)
        if len(x) == 0:
            waited_blocks += 1
            continue
        ring.append(x)
        pdb = band_power_db(x)
        active = pdb >= thr

        if state == "IDLE":
            candidate = candidate + 1 if active else 0
            waited_blocks += 1
            if candidate >= min_act_blocks:
                state = "REC"
                hang = 0
                rec_blocks = list(ring)  # 含触发前 pre-roll
            else:
                continue

        if state == "REC":
            rec_blocks.append(x)
            hang = 0 if active else hang + 1
            if hang >= hang_blocks or len(rec_blocks) >= max_record_blocks:
                #去掉结尾的纯噪声拖尾块（保留约一个 hang 的尾巴以含衰落过程）
                tail_keep = max(1, hang_blocks // 2)
                if hang > tail_keep:
                    rec_blocks = rec_blocks[:-(hang - tail_keep)]
                samples = np.concatenate(rec_blocks) if rec_blocks else np.zeros(0, np.complex128)
                peak = max(band_power_db(b) for b in rec_blocks)
                return {
                    "status": "triggered",
                    "samples": samples,
                    "noise_floor_db": noise_floor,
                    "threshold_db": thr,
                    "peak_db": float(peak),
                    "recorded_s": len(samples) / sr,
                    "waited_s": waited_blocks * block_s,
                    "reason": "活动结束" if hang >= hang_blocks else "达到最长录制时长",
                }
            waited_blocks += 1

    return {
        "status": "timeout",
        "samples": np.zeros(0, dtype=np.complex128),
        "noise_floor_db": noise_floor,
        "threshold_db": thr,
        "peak_db": noise_floor,
        "recorded_s": 0.0,
        "waited_s": max_wait_blocks * block_s,
        "reason": "等待窗口内无持续活动",
    }

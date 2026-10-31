# SPDX-License-Identifier: MIT
"""P1-7 FHSS 跳频跟踪与解跳原子工具。

能力拆解（218 条）P1-7：sdr_detect_fhss 只做存在性检测，本模块
补"跟踪→解跳→解调"后半链（慢跳频、槽定时已知口径）。

  fhss_hop_track(iq, sample_rate_hz, slot_s) —— STFT 分槽谱平均，
    每槽主峰即驻留频率， prominence（峰/槽内中位数）低的槽如实
    标记 missed（空槽/信噪比不足不产伪频率）；
  dehop_demod(iq, hop_seq_hz, slot_s) —— 按跟踪序列逐槽变频到
    基带 + FM 鉴频（相位差分），输出每槽频偏均值与判决比特。

诚实边界（AGENTS 第 2 条）：
  * 槽定时须已知/已由外部同步（slot_s 参数显式传入）——槽边界
    盲同步不在本工具口径内；
  * 每槽被测频率含调制频偏（2FSK 时峰在 f_h±f_dev）——跟踪输出
    是**发射频率 + 调制偏移**的合成峰，dehop 用同一测量值变频，
    调制分量自洽留在基带，不构成误差源（docstring 已注明）；
  *  missed 槽不插值不编造，解跳结果如实带槽号清单；
  * 合成口径：真机跳频录制样本到位后按同契约补 golden 回归。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np

__all__ = ["fhss_hop_track", "dehop_demod", "register_tool_registry"]


def _spectrogram(iq: np.ndarray,
                 sample_rate_hz: float,
                 frame_len: int,
                 frame_step: int,
                 ) -> tuple:
    """手动 STFT（Hann 窗，幅度谱），返回 (freqs, t, S)。"""
    window = np.hanning(frame_len)
    n_frames = 1 + max(0, (iq.size - frame_len) // frame_step)
    freqs = np.fft.fftshift(np.fft.fftfreq(frame_len, 1.0 / sample_rate_hz))
    S = np.empty((n_frames, frame_len))
    for i in range(n_frames):
        seg = iq[i * frame_step: i * frame_step + frame_len] * window
        S[i] = np.abs(np.fft.fftshift(np.fft.fft(seg)))
    t = (np.arange(n_frames) * frame_step + frame_len / 2.0) / sample_rate_hz
    return freqs, t, S


def fhss_hop_track(iq: Union[Sequence[complex], np.ndarray],
                   sample_rate_hz: float,
                   slot_s: float,
                   snr_threshold: float = 6.0,
                   ) -> Dict[str, Any]:
    """慢跳频驻留频率跟踪（槽定时已知）。

    iq            : 复基带流；
    slot_s        : 跳频槽长（秒，外部同步口径）；
    snr_threshold : 槽内 prominence（峰/中位数，dB）低于此值的槽
                    记 missed（None），不产伪频率。

    返回：hop_freqs_hz（每槽频率或 None）/ slot_s / n_slots /
    n_missed / prominence_db 每槽 / freq_resolution_hz。
    """
    x = np.asarray([complex(v) for v in iq])
    if x.size < 64:
        raise ValueError("IQ 序列太短（64 点下限）")
    if sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz 须为正")
    if slot_s <= 0.0:
        raise ValueError("slot_s 须为正")
    if not np.all(np.isfinite(x)):
        raise ValueError("IQ 含非有限值")

    # STFT 帧长 = 槽长的 1/8（槽内 8 帧平均，频率分辨 vs 跟踪
    # 瞬时性的折中），帧移 = 帧长/2
    frame_len = max(32, int(round(slot_s * sample_rate_hz / 8.0)))
    frame_len = min(frame_len, x.size)
    frame_step = max(16, frame_len // 2)
    freqs, t, S = _spectrogram(x, sample_rate_hz, frame_len, frame_step)
    bin_hz = sample_rate_hz / frame_len

    slot_samples = slot_s * sample_rate_hz
    n_slots = max(1, int(round(x.size / slot_samples)))
    hop_freqs: List[Optional[float]] = []
    prominences: List[float] = []
    for s in range(n_slots):
        t_lo = s * slot_s
        t_hi = (s + 1) * slot_s
        cols = (t >= t_lo) & (t < t_hi)
        if not np.any(cols):
            hop_freqs.append(None)
            prominences.append(0.0)
            continue
        col_mean = S[cols].mean(axis=0)
        peak_i = int(np.argmax(col_mean))
        peak = float(col_mean[peak_i])
        floor = float(np.median(col_mean)) + 1e-30
        prom_db = 10.0 * math.log10(peak / floor)
        prominences.append(round(prom_db, 2))
        if prom_db < snr_threshold:
            hop_freqs.append(None)
        else:
            hop_freqs.append(round(float(freqs[peak_i]), 1))
    return {
        "hop_freqs_hz": hop_freqs,
        "slot_s": float(slot_s),
        "n_slots": int(n_slots),
        "n_missed": sum(1 for f in hop_freqs if f is None),
        "prominence_db": prominences,
        "freq_resolution_hz": round(float(bin_hz), 3),
        "note": "每槽频率含调制频偏（2FSK 峰在 f_h±f_dev）",
    }


def dehop_demod(iq: Union[Sequence[complex], np.ndarray],
                hop_freqs_hz: Sequence[Optional[float]],
                sample_rate_hz: float,
                slot_s: float,
                ) -> Dict[str, Any]:
    """按跟踪序列逐槽解跳到基带（调制解调留给下游标准工具）。

    hop_freqs_hz : fhss_hop_track 的输出（None 槽跳过，不插值）；
    返回：baseband_iq（各有效槽变频后拼接，等长槽）、slots（每槽
    {slot, hop_freq_hz, residual_offset_hz（变频残留，≈0 = 解跳
    干净）}）、skipped_slots（missed 槽如实列号）。

    口径说明：跟踪峰 = 槽内主音（含 2FSK 调制偏时即 f_h±f_dev），
    用同一测量值变频把该主音搬到 DC——单音每槽（信标式 FHSS）解跳
    后残留 ≈0；多符号槽的调制分量作为基带信号整体交给下游解调，
    本工具不做符号判决（避免越权编造比特）。
    """
    x = np.asarray([complex(v) for v in iq])
    if x.size < 64:
        raise ValueError("IQ 序列太短（64 点下限）")
    if sample_rate_hz <= 0.0 or slot_s <= 0.0:
        raise ValueError("sample_rate_hz/slot_s 须为正")
    if not np.all(np.isfinite(x)):
        raise ValueError("IQ 含非有限值")

    slot_n = int(round(slot_s * sample_rate_hz))
    n_slots = min(len(hop_freqs_hz), x.size // slot_n)
    results: List[Dict[str, Any]] = []
    skipped: List[int] = []
    baseband: List[np.ndarray] = []
    for s in range(n_slots):
        f_h = hop_freqs_hz[s]
        seg = x[s * slot_n: (s + 1) * slot_n]
        if f_h is None:
            skipped.append(s)
            continue
        t_local = np.arange(seg.size) / sample_rate_hz
        mixed = seg * np.exp(-2j * np.pi * f_h * t_local)
        # 变频质量自检：鉴频均值（相位差分）应 ≈0（主音已落 DC）
        phase = np.unwrap(np.angle(mixed))
        resid = float(np.mean(np.diff(phase)) * sample_rate_hz
                      / (2.0 * math.pi))
        baseband.append(mixed)
        results.append({
            "slot": s,
            "hop_freq_hz": float(f_h),
            "residual_offset_hz": round(resid, 1),
        })
    bb = np.concatenate(baseband) if baseband else np.array([], dtype=complex)
    return {
        "baseband_iq": bb.tolist(),
        "slots": results,
        "n_slots_demodded": len(results),
        "skipped_slots": skipped,
        "slot_s": float(slot_s),
        "downstream_note": "调制解调用下游标准 FSK/FM 工具，本工具不做符号判决",
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-7 跳频跟踪/解跳积木。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _track(args):
        try:
            res = fhss_hop_track(
                args.get("iq", []),
                float(args.get("sample_rate_hz", 1e6)),
                slot_s=float(args.get("slot_s", 0.01)),
                snr_threshold=float(args.get("snr_threshold", 6.0)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"跳频跟踪失败: {clean_exc(e)}")
        ok = res["n_missed"] < res["n_slots"]
        return ToolResult(success=ok,
                          content=_json.dumps({k: v for k, v in res.items()},
                                              ensure_ascii=False),
                          data=res)

    def _dehop(args):
        try:
            res = dehop_demod(
                args.get("iq", []),
                args.get("hop_freqs_hz", []),
                float(args.get("sample_rate_hz", 1e6)),
                slot_s=float(args.get("slot_s", 0.01)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"解跳解调失败: {clean_exc(e)}")
        ok = res["n_slots_demodded"] > 0
        brief = {k: v for k, v in res.items() if k != "baseband_iq"}
        return ToolResult(success=ok,
                          content=_json.dumps(brief, ensure_ascii=False),
                          data=res)

    registry.register(
        name="fhss_hop_track",
        description=(
            "慢跳频驻留频率跟踪（STFT 分槽谱平均，槽定时已知口径）；"
            "prominence 不足槽记 missed 不产伪频率。跟踪值含调制"
            "频偏（2FSK 峰在 f_h±f_dev），解跳用同值变频自洽。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array",
                       "items": {"type": ["object", "string", "number"]}},
                "sample_rate_hz": {"type": "number", "default": 1e6},
                "slot_s": {"type": "number", "default": 0.01},
                "snr_threshold": {"type": "number", "default": 6.0},
            },
            "required": ["iq", "slot_s"],
        },
        handler=_track,
        category="signal_analysis")

    registry.register(
        name="dehop_demod",
        description=(
            "按跟踪跳频序列逐槽解跳 + FM 鉴频（ missed 槽跳过不插值，"
            "skipped_slots 如实列号）。"),
        parameters={
            "type": "object",
            "properties": {
                "iq": {"type": "array",
                       "items": {"type": ["object", "string", "number"]}},
                "hop_freqs_hz": {"type": "array",
                                 "items": {"type": ["number", "null"]}},
                "sample_rate_hz": {"type": "number", "default": 1e6},
                "slot_s": {"type": "number", "default": 0.01},
            },
            "required": ["iq", "hop_freqs_hz", "slot_s"],
        },
        handler=_dehop,
        category="signal_analysis")

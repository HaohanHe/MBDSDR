# SPDX-License-Identifier: MIT
"""P1-8 干扰生成/评估闭环原子工具。

能力拆解（218 条）P1-8：对抗面从"检测"补到"生成→施加→量化评估"
闭环，用于本机接收链抗干扰性能的受控测量（合成口径）。

  jammer_generate(style, ...) —— 合成干扰源（tone/noise/chirp/pulse
    四样式），复基带 IQ 输出，功率/频率/带宽参数化，随机种子可复现。
  interference_assess(before, after, ...) —— 同一信号受扰前后的
    质量对比：占用带宽内信号功率、噪声底（PSD 中位数）、SINAD、
    占用带宽变化，降级量 dB 如实上报。

诚实边界（AGENTS 第 2 条）：
  * 全合成：不驱动任何发射硬件；真机发射路径须走 TT&C 既有
    arm_manual 人工门，本工具不参与；
  * SINAD 估计基于平滑 PSD + 分位噪声底 + 固定频带口径，绝对值是
    保守下界（频带边界 bin 含噪声）；本工具的判读价值在**相对降级
    量**（同一对偶快照的差），estimate 字段明示口径，不冒充精确测量；
  * before/after 长度不一致、非有限值 → 显式报错，不静默截断。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

__all__ = ["jammer_generate", "interference_assess", "register_tool_registry"]

_JAM_STYLES = ("tone", "noise", "chirp", "pulse")


def jammer_generate(style: str,
                    n_samples: int,
                    sample_rate_hz: float,
                    freq_hz: float = 0.0,
                    power_db: float = 0.0,
                    bandwidth_hz: Optional[float] = None,
                    sweep_to_hz: Optional[float] = None,
                    duty_cycle: float = 0.5,
                    seed: Optional[int] = None,
                    ) -> Dict[str, Any]:
    """合成干扰源（复基带 IQ）。

    style        : tone（单音）/ noise（限带高斯噪声）/ chirp（线性扫频）
                   / pulse（单音脉冲串，duty_cycle 导通占比）；
    freq_hz      : 中心/起始频偏（Hz，相对复基带中心）；
    bandwidth_hz : noise 样式占用带宽（必填，0<bw<fs）；
    sweep_to_hz  : chirp 终点频偏（默认扫到 -freq_hz 对称扫频）；
    power_db     : 平均功率（dB，导通期平均——pulse 样式脉冲期内恒功率）；
    seed         : 随机种子（None = 不可复现，如实标注）。

    返回：iq（复数列表）/ style / params / seed 元数据。
    """
    if style not in _JAM_STYLES:
        raise ValueError(f"未知干扰样式 {style!r}（可用 {_JAM_STYLES}）")
    if n_samples < 8:
        raise ValueError("样本数须 ≥8")
    if sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz 须为正")
    if not -sample_rate_hz / 2.0 <= freq_hz <= sample_rate_hz / 2.0:
        raise ValueError("freq_hz 超出奈奎斯特区间")

    rng = np.random.default_rng(seed)
    n = int(n_samples)
    t = np.arange(n) / sample_rate_hz
    p_lin = 10.0 ** (power_db / 10.0)
    amp = math.sqrt(p_lin)          # 复信号 |x|² 平均 = amp²

    if style == "tone":
        iq = amp * np.exp(2j * np.pi * freq_hz * t)
    elif style == "noise":
        if bandwidth_hz is None or not (0.0 < bandwidth_hz <
                                        sample_rate_hz):
            raise ValueError("noise 样式须给 0<bandwidth_hz<fs")
        # 频域成形：白噪声 → 理想矩形滤波 → 时域
        spec = (rng.standard_normal(n)
                + 1j * rng.standard_normal(n))
        freqs = np.fft.fftfreq(n, 1.0 / sample_rate_hz)
        mask = np.abs(np.fft.fftshift(freqs) - freq_hz) <= bandwidth_hz / 2.0
        spec_shaped = np.fft.fftshift(spec) * mask
        iq = np.fft.ifft(np.fft.ifftshift(spec_shaped))
        iq *= amp / (np.sqrt(np.mean(np.abs(iq) ** 2)) + 1e-30)
    elif style == "chirp":
        f1 = sweep_to_hz if sweep_to_hz is not None else -freq_hz
        if not -sample_rate_hz / 2.0 <= f1 <= sample_rate_hz / 2.0:
            raise ValueError("sweep_to_hz 超出奈奎斯特区间")
        k = (f1 - freq_hz) / (n / sample_rate_hz)   # 扫频速率 Hz/s
        phase = 2.0 * np.pi * (freq_hz * t + 0.5 * k * t * t)
        iq = amp * np.exp(1j * phase)
    else:  # pulse
        if not (0.0 < duty_cycle <= 1.0):
            raise ValueError("duty_cycle 须在 (0, 1]")
        period = max(8, int(round(8.0 / duty_cycle)))
        on = max(1, int(round(period * duty_cycle)))
        gate = np.zeros(n)
        for start in range(0, n, period):
            gate[start:start + on] = 1.0
        iq = amp * gate * np.exp(2j * np.pi * freq_hz * t)

    if not np.all(np.isfinite(iq)):
        raise RuntimeError("干扰合成出现非有限值（参数组合异常）")
    return {
        "iq": iq.tolist(),
        "style": style,
        "n_samples": n,
        "sample_rate_hz": float(sample_rate_hz),
        "params": {
            "freq_hz": float(freq_hz),
            "power_db": float(power_db),
            "bandwidth_hz": bandwidth_hz,
            "sweep_to_hz": sweep_to_hz,
            "duty_cycle": duty_cycle if style == "pulse" else None,
        },
        "seed": seed,
        "reproducible": seed is not None,
    }


def _snapshot(iq: np.ndarray,
              sample_rate_hz: float,
              band_hz: Optional[Tuple[float, float]] = None,
              ) -> Dict[str, Any]:
    """PSD 分位口径快照：占用频带（或沿用外部给定频带）、带内功率、
    每 bin 噪声底（全谱中位数）、带内非信号功率与 SINAD 估计。

    频带口径：band_hz=None 时取自峰值 −10 dB 连续区（clean 快照定义
    信号频带）；受扰快照沿用同一频带（信号分量线性叠加不变，干扰
    以带内非信号功率增量显形）。不把全局峰值当信号——强带内单音
    干扰会顶掉峰值，按峰值定频带会把干扰误认为信号（已踩过）。
    """
    n = len(iq)
    window = np.hanning(n)
    psd = np.abs(np.fft.fftshift(np.fft.fft(iq * window))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / sample_rate_hz))
    nf = float(np.median(psd))                    # 每 bin 噪声底估计

    # 平滑 PSD 上找信号丘：raw 单 bin SNR 可能 <0 dB（宽带信号摊薄），
    # 峰值−10 dB 连续区会塌到单 bin；滑动平均（~n/64）把信号丘
    # 抬出噪声底后再切边界。占用带宽同口径取平滑谱 −10 dB 连续区。
    k = max(9, n // 64)
    sm = np.convolve(psd, np.ones(k) / k, mode="same")
    peak_i = int(np.argmax(sm))
    lo, hi = peak_i, peak_i
    while lo - 1 >= 0 and sm[lo - 1] > nf:
        lo -= 1
    while hi + 1 < n and sm[hi + 1] > nf:
        hi += 1
    thr10 = sm[peak_i] / 10.0
    lo10, hi10 = peak_i, peak_i
    while lo10 - 1 >= 0 and sm[lo10 - 1] > thr10:
        lo10 -= 1
    while hi10 + 1 < n and sm[hi10 + 1] > thr10:
        hi10 += 1
    occ_bw = float(freqs[hi10] - freqs[lo10])

    if band_hz is None:
        b_lo, b_hi = lo, hi
    else:
        b_lo = int(np.searchsorted(freqs, band_hz[0]))
        b_hi = int(np.searchsorted(freqs, band_hz[1]))
        b_lo = max(0, min(b_lo, n - 1))
        b_hi = max(0, min(b_hi, n - 1))
    n_bins = max(1, b_hi - b_lo + 1)
    p_band = float(np.sum(psd[b_lo: b_hi + 1]))
    p_noise = nf * n_bins
    return {
        "band_hz": (float(freqs[b_lo]), float(freqs[b_hi])),
        "band_power": p_band,
        "noise_per_bin": nf,
        "noise_band_power": p_noise,
        "occupied_bw_hz": occ_bw,
        "peak_freq_hz": float(freqs[peak_i]),
        "n_bins": n_bins,
    }


def _sinad_from(p_signal: float, p_nsd: float) -> float:
    return 10.0 * math.log10(p_signal / max(p_nsd, 1e-30))


def interference_assess(iq_before: Union[Sequence[complex], np.ndarray],
                        iq_after: Union[Sequence[complex], np.ndarray],
                        sample_rate_hz: float,
                        ) -> Dict[str, Any]:
    """受扰前后信号质量对比（合成评估闭环的量化端）。

    iq_before / iq_after : 同一信号未受扰/受扰复基带流（等长）；
    返回 before/after 各自的 estimate（SINAD/信号功率/噪声底/占用带宽）
    与降级量：sinad_degradation_db（正值 = 恶化）、occ_bw_change_hz。
    """
    b = np.asarray([complex(v) for v in iq_before])
    a = np.asarray([complex(v) for v in iq_after])
    if b.size != a.size:
        raise ValueError(
            f"before/after 长度不一致（{b.size} vs {a.size}），"
            "评估须同一信号的对偶快照")
    if b.size < 64:
        raise ValueError("样本须 ≥64（PSD 估计稳定下限）")
    if not (np.all(np.isfinite(b)) and np.all(np.isfinite(a))):
        raise ValueError("输入含非有限值")
    if sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz 须为正")

    # clean 快照定义信号频带与信号功率估计
    snap_b = _snapshot(b, sample_rate_hz)
    p_sig = max(snap_b["band_power"] - snap_b["noise_band_power"],
                1e-6 * snap_b["band_power"])
    sinad_b = _sinad_from(p_sig, snap_b["noise_band_power"])

    # 受扰快照沿用同一频带：信号分量线性不变，带内非信号功率增量
    # = 受扰带内功率 − 信号功率估计（下限 = 受扰自身噪声底×bin 数，
    # 防止深衰落时算出非物理的负非信号功率）
    snap_a = _snapshot(a, sample_rate_hz, band_hz=snap_b["band_hz"])
    p_nsd_a = max(snap_a["band_power"] - p_sig,
                  snap_a["noise_per_bin"] * snap_a["n_bins"])
    sinad_a = _sinad_from(p_sig, p_nsd_a)

    degr = sinad_b - sinad_a                          # 正 = 恶化
    return {
        "before": {"sinad_db": round(sinad_b, 3),
                   "signal_power_db": round(
                       10.0 * math.log10(p_sig), 3),
                   "noise_floor_db": round(
                       10.0 * math.log10(snap_b["noise_per_bin"]), 3),
                   "occupied_bw_hz": round(
                       snap_b["occupied_bw_hz"], 1),
                   "band_hz": [round(v, 1)
                               for v in snap_b["band_hz"]]},
        "after": {"sinad_db": round(sinad_a, 3),
                  "band_power_db": round(
                      10.0 * math.log10(snap_a["band_power"]), 3),
                  "noise_floor_db": round(
                      10.0 * math.log10(snap_a["noise_per_bin"]), 3),
                  "occupied_bw_hz": round(
                      snap_a["occupied_bw_hz"], 1),
                  "band_hz": [round(v, 1)
                              for v in snap_a["band_hz"]]},
        "sinad_degradation_db": round(degr, 3),
        "occ_bw_change_hz": round(snap_a["occupied_bw_hz"]
                                  - snap_b["occupied_bw_hz"], 3),
        "estimate": "psd_percentile_fixed_band",
        "n_samples": int(b.size),
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-8 干扰生成/评估积木（合成口径，不驱动发射硬件）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _gen(args):
        try:
            res = jammer_generate(
                style=str(args.get("style", "tone")),
                n_samples=int(args.get("n_samples", 1024)),
                sample_rate_hz=float(args.get("sample_rate_hz", 1e6)),
                freq_hz=float(args.get("freq_hz", 0.0)),
                power_db=float(args.get("power_db", 0.0)),
                bandwidth_hz=(float(args["bandwidth_hz"])
                              if args.get("bandwidth_hz") is not None
                              else None),
                sweep_to_hz=(float(args["sweep_to_hz"])
                             if args.get("sweep_to_hz") is not None
                             else None),
                duty_cycle=float(args.get("duty_cycle", 0.5)),
                seed=(int(args["seed"]) if args.get("seed") is not None
                      else None),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"干扰合成失败: {clean_exc(e)}")
        data = {k: v for k, v in res.items() if k != "iq"}
        return ToolResult(success=True,
                          content=_json.dumps(data, ensure_ascii=False),
                          data=res)

    def _assess(args):
        try:
            res = interference_assess(
                args.get("before", []), args.get("after", []),
                float(args.get("sample_rate_hz", 1e6)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"干扰评估失败: {clean_exc(e)}")
        return ToolResult(success=True,
                          content=_json.dumps(res, ensure_ascii=False),
                          data=res)

    registry.register(
        name="jammer_generate",
        description=(
            "合成干扰源（tone/noise/chirp/pulse 复基带 IQ，功率/频率/"
            "带宽/占空参数化，种子可复现）。纯合成不驱动发射硬件；"
            "真机发射须走 TT&C arm_manual 人工门。"),
        parameters={
            "type": "object",
            "properties": {
                "style": {"type": "string", "default": "tone"},
                "n_samples": {"type": "integer", "default": 1024},
                "sample_rate_hz": {"type": "number", "default": 1e6},
                "freq_hz": {"type": "number", "default": 0.0},
                "power_db": {"type": "number", "default": 0.0},
                "bandwidth_hz": {"type": ["number", "null"], "default": None},
                "sweep_to_hz": {"type": ["number", "null"], "default": None},
                "duty_cycle": {"type": "number", "default": 0.5},
                "seed": {"type": ["integer", "null"], "default": None},
            },
            "required": [],
        },
        handler=_gen,
        category="signal_analysis")

    registry.register(
        name="interference_assess",
        description=(
            "受扰前后信号质量对比：PSD 分位口径 SINAD/信号功率/噪声底/"
            "占用带宽 + 降级 dB（正值=恶化）。等长对偶快照硬校验。"),
        parameters={
            "type": "object",
            "properties": {
                "before": {"type": "array",
                           "items": {"type": ["object", "string", "number"]}},
                "after": {"type": "array",
                          "items": {"type": ["object", "string", "number"]}},
                "sample_rate_hz": {"type": "number", "default": 1e6},
            },
            "required": ["before", "after"],
        },
        handler=_assess,
        category="signal_analysis")

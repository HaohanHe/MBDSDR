# -*- coding: utf-8 -*-
"""新模块 mbdsdr_ai/interferometry.py：射电干涉测量（几何时延 / 互相关测向 / 可见度）。

材料《通信对抗材料包》里的讲义（radio_interferometry_handout.pdf）讲的就是
这套东西：
  · 几何时延：源在极小仰角 θ 时，两天线波front 到达差 τ = D·sinθ / c；
  · 相关：v1(t)·v2(t) = v1(t)·v1(t+τ)，长时间平均 → I·cos(2πf·D·sinθ/c)
    （可见度函数）；
  · 分辨率：θ_res ≈ λ / D（讲义里的对照表：人眼 500nm/2mm → 1 arcmin；
    单台 VLA 25m/5GHz → 1 arcmin；CygA 5GHz/25km → 0.5 arcsec）。

仓内此前只有"八木 RSSI 方位扫描"测向（gnss_direction_find），没有
**双站互相关测向**（TDOA）——讲义这套方法对模型不可操作。本模块补上。

诚实纪律（AGENTS 第 2 条）：
  · 相关峰不显著时**不报方位角**（只报峰值质量与原因）；
  · 几何/单位口径全部具名（波长、基线长度/方位、采样率、光速常数）；
  · 角度与分辨率给多种单位（rad/deg/arcmin/arcsec），不让人心算换算。
"""
import io
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

#: 光速（m/s，SI 定义值）
C_M_S = 299_792_458.0
#: 1 弧度 = 180/pi 度
RAD_TO_DEG = 180.0 / math.pi


def angular_resolution(wavelength_m: float, aperture_m: float) -> Dict[str, Any]:
    """单孔径/基线的角分辨率 θ_res ≈ λ / D（讲义的分辨率对照表口径）。

    wavelength_m: 波长（米）；aperture_m: 孔径或基线长度（米）。
    """
    if wavelength_m <= 0 or aperture_m <= 0:
        raise ValueError("wavelength_m 与 aperture_m 必须为正")
    theta = wavelength_m / aperture_m          # 弧度
    return {
        "theta_rad": theta,
        "theta_deg": theta * RAD_TO_DEG,
        "theta_arcmin": theta * RAD_TO_DEG * 60.0,
        "theta_arcsec": theta * RAD_TO_DEG * 3600.0,
        "wavelength_m": float(wavelength_m),
        "aperture_m": float(aperture_m),
        "note": "单孔径/基线的角分辨率 ≈ λ/D（瑞利判据量级）",
    }


def visibility(amplitude: float, freq_hz: float, baseline_m: float,
               theta_rad: float) -> Dict[str, Any]:
    """可见度函数 I·cos(2πf·D·sinθ/c)（讲义第 3 页的式子）。

    amplitude: 源强度 I（任意单位）；freq_hz: 观测频率；baseline_m: 基线 D；
    theta_rad: 源相对基线的仰角（弧度）。
    """
    if baseline_m <= 0:
        raise ValueError("baseline_m 必须为正")
    sin_t = math.sin(theta_rad)
    delay_s = baseline_m * sin_t / C_M_S
    vis = float(amplitude) * math.cos(2.0 * math.pi * float(freq_hz) * delay_s)
    return {
        "visibility": vis,
        "normalized": vis / float(amplitude) if amplitude else 0.0,
        "geometric_delay_s": delay_s,
        "sin_theta": sin_t,
        "amplitude": float(amplitude),
        "freq_hz": float(freq_hz),
        "baseline_m": float(baseline_m),
        "theta_deg": theta_rad * RAD_TO_DEG,
    }


def _xcorr_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> Tuple[float, float]:
    """互相关峰值滞后（样点）与峰值相关值。

    用 FFT 互相关（线性补零避免循环卷绕），再在 ±max_lag 内取模最大。
    **复数基带走复数相关**（不丢虚部——此前强转 float64 会静默丢虚部）。
    滞后符号约定：**lag > 0 表示站 B 比站 A 更晚收到**（源偏向站 B 一侧）。
    峰值用抛物线插值做**亚样点**细化（整数滞后会带来 (1/2 样点)×c/(D·fs)
    的系统偏差，真实测向不够）。
    返回 (lag, peak) —— peak 是**归一化**相关系数（|ρ| ≤ 1）。
    """
    n = 1
    while n < len(a) + len(b):
        n <<= 1
    complex_in = np.iscomplexobj(a) or np.iscomplexobj(b)
    if complex_in:
        a = a.astype(np.complex128)
        b = b.astype(np.complex128)
        fa = np.fft.fft(a - np.mean(a), n)
        fb = np.fft.fft(b - np.mean(b), n)
        cc = np.fft.ifft(fa * np.conj(fb), n)
    else:
        a = a.astype(np.float64)
        b = b.astype(np.float64)
        fa = np.fft.rfft(a - np.mean(a), n)
        fb = np.fft.rfft(b - np.mean(b), n)
        cc = np.fft.irfft(fa * np.conj(fb), n)
    # 负滞后在数组尾部
    cc = np.concatenate((cc[-(len(b) - 1):], cc[:len(a)]))
    lags = np.arange(-(len(b) - 1), len(a))
    lim = np.abs(lags) <= max(1, int(max_lag))
    if not np.any(lim):
        return 0, 0.0
    sub = cc[lim]
    sub_lags = lags[lim]
    denom = (np.linalg.norm(a - np.mean(a)) * np.linalg.norm(b - np.mean(b)))
    mag = np.abs(sub)
    peak = float(np.max(mag) / denom) if denom > 0 else 0.0
    idx = int(np.argmax(mag))
    lag = float(sub_lags[idx])
    # 亚样点细化：抛物线拟合峰值三点（真实测向需要亚样点精度，
    # 整数滞后会引入 (1/2 样点)×c/(D·fs) 的系统偏差）
    if 0 < idx < len(mag) - 1:
        y0, y1, y2 = mag[idx - 1], mag[idx], mag[idx + 1]
        denom_p = (y0 - 2.0 * y1 + y2)
        if abs(denom_p) > 1e-12:
            delta = 0.5 * (y0 - y2) / denom_p
            if abs(delta) <= 1.0:
                lag = float(sub_lags[idx]) + delta
    # 符号约定：站 B 比站 A **更晚**收到（源偏向站 B 一侧）时 lag > 0。
    # 原始相关峰在此约定下是反的，故取反。
    return -lag, peak


def tdoa_bearing(sig_a: Sequence[float], sig_b: Sequence[float],
                 sample_rate_hz: float,
                 baseline_m: float,
                 baseline_bearing_deg: float = 0.0,
                 max_delay_s: Optional[float] = None,
                 min_peak: float = 0.2) -> Dict[str, Any]:
    """双站互相关测向：两路基带 → 相关峰滞后 → 几何时延 → 源方位。

    sig_a / sig_b      : 两站的复基带（或实）采样序列，长度相同；
    sample_rate_hz     : 采样率；
    baseline_m         : 两站基线长度（米）；
    baseline_bearing_deg: 从站 A 指向站 B 的基线方位（度，自北顺时针）；
    max_delay_s        : 允许的最大时延（秒）；缺省按基线几何上限 2D/c 取；
    min_peak           : 相关峰显著门限（归一化），低于此**不报方位角**。

    返回：delay_s / peak / sin_theta / 相对基线的角 / 绝对方位 / 质量判定。
    峰不显著时 azimuth_deg 为 None（不编造方位）。
    """
    a = np.asarray(sig_a).ravel()
    b = np.asarray(sig_b).ravel()
    if np.iscomplexobj(a) != np.iscomplexobj(b):
        # 一复一实：把实的取实部（明确口径，不静默混算）
        if np.iscomplexobj(a):
            a = a.real
        else:
            b = b.real
    if a.size == 0 or b.size == 0:
        raise ValueError("两路采样不能为空")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz 必须为正")
    if baseline_m <= 0:
        raise ValueError("baseline_m 必须为正")
    n = min(a.size, b.size)
    a = a[:n]
    b = b[:n]
    if max_delay_s is None:
        max_delay_s = 2.0 * baseline_m / C_M_S
    max_lag = int(round(float(max_delay_s) * float(sample_rate_hz)))
    if max_lag < 1:
        max_lag = 1
    lag, peak = _xcorr_lag(a, b, max_lag)
    delay_s = lag / float(sample_rate_hz)
    # sinθ = τ·c / D（讲义的几何时延式）
    sin_theta = delay_s * C_M_S / float(baseline_m)
    out: Dict[str, Any] = {
        "lag_samples": lag,
        "delay_s": delay_s,
        "peak": peak,
        "min_peak": float(min_peak),
        "baseline_m": float(baseline_m),
        "baseline_bearing_deg": float(baseline_bearing_deg),
        "sample_rate_hz": float(sample_rate_hz),
        "max_delay_s": float(max_delay_s),
        "sin_theta": sin_theta,
    }
    if peak < float(min_peak):
        out["azimuth_deg"] = None
        out["relative_angle_deg"] = None
        out["reason"] = (f"相关峰不显著（峰值 {peak:.3f} < 门限 {min_peak:.3f}）："
                         "两路可能不同源、时延超出搜索范围、或信噪比不足——"
                         "不给方位角")
        return out
    if abs(sin_theta) > 1.0:
        out["azimuth_deg"] = None
        out["relative_angle_deg"] = None
        out["reason"] = (f"时延 {delay_s * 1e6:.3f} µs 超出基线几何上限"
                         f"（|sinθ| = {abs(sin_theta):.3f} > 1）——"
                         "检查基线长度/采样率是否给错")
        return out
    theta = math.asin(sin_theta)
    rel_deg = theta * RAD_TO_DEG
    # 源在基线方位左侧还是右侧：由时延符号定（约定：B 比 A 晚到 → 源在 A→B
    # 方位左侧；反之右侧）——方向约定在返回值里写明，调用方可自行取反。
    side = "left" if rel_deg >= 0 else "right"
    azimuth = (float(baseline_bearing_deg) + rel_deg) % 360.0
    out["relative_angle_deg"] = rel_deg
    out["side"] = side
    out["azimuth_deg"] = azimuth
    out["convention"] = ("相对基线角：正=源在基线方位左侧（站 B 收到更晚）")
    return out


# ============================================================================
# 工具注册
# ============================================================================
def register_tool_registry(registry) -> None:
    """注册射电干涉测量积木（互相关测向 + 角分辨率/可见度）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _tdoa(args):
        """两站基带 → 互相关 → 时延 → 方位角。"""
        import base64 as _b64
        try:
            def _load(path_key, b64_key):
                path = args.get(path_key)
                if path:
                    return np.fromfile(path, dtype=np.complex64)
                b64 = args.get(b64_key)
                if not b64:
                    raise ValueError(
                        f"需要 {path_key}（complex64 raw 文件）或 {b64_key}"
                        "（float32 交错 base64）")
                raw = np.frombuffer(_b64.b64decode(str(b64)), dtype=np.float32)
                if raw.size % 2:
                    raw = raw[:-1]
                return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)

            a = _load("sig_a_path", "sig_a_base64")
            b = _load("sig_b_path", "sig_b_base64")
            res = tdoa_bearing(
                a, b,
                sample_rate_hz=float(args.get("sample_rate_hz", 1e6)),
                baseline_m=float(args.get("baseline_m", 100.0)),
                baseline_bearing_deg=float(args.get("baseline_bearing_deg", 0.0)),
                max_delay_s=args.get("max_delay_s"),
                min_peak=float(args.get("min_peak", 0.2)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"互相关测向失败：{clean_exc(e)}")
        if res.get("azimuth_deg") is None:
            res["note"] = "未给出方位角（见 reason）"
            return ToolResult(
                success=False,
                content=_json.dumps(res, ensure_ascii=False, default=str),
                data=res)
        return ToolResult(
            success=True,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    def _resolution(args):
        """角分辨率（λ/D）与可选的可见度值。"""
        try:
            out: Dict[str, Any] = {}
            if args.get("wavelength_m") and args.get("aperture_m"):
                out["resolution"] = angular_resolution(
                    float(args["wavelength_m"]), float(args["aperture_m"]))
            if args.get("freq_hz") and args.get("baseline_m"):
                theta_deg = float(args.get("theta_deg", 0.0))
                out["visibility"] = visibility(
                    float(args.get("amplitude", 1.0)),
                    float(args["freq_hz"]), float(args["baseline_m"]),
                    math.radians(theta_deg))
            if not out:
                raise ValueError(
                    "需要 wavelength_m+aperture_m（角分辨率）或 "
                    "freq_hz+baseline_m（可见度）")
            # 频率也能换算波长 → 顺手给分辨率
            if args.get("freq_hz") and not out.get("resolution"):
                wl = C_M_S / float(args["freq_hz"])
                out["wavelength_m"] = wl
                out["resolution"] = angular_resolution(wl, float(args["baseline_m"]))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"分辨率/可见度计算失败："
                                      f"{type(e).__name__}: {e}")
        return ToolResult(success=True,
                          content=_json.dumps(out, ensure_ascii=False, default=str),
                          data=out)

    registry.register(
        name="interferometry_tdoa",
        description=(
            "射电干涉测向（双站互相关 TDOA）：两站同时录下的同源基带 → 互相关 → "
            "峰值滞后 → 几何时延 τ → sinθ = τ·c/D → 源方位角。"
            "依据材料《通信对抗材料包》射电干涉讲义的几何时延与相关公式。"
            "前置条件：需给 sig_a_path/sig_b_path（complex64 raw 文件）或 "
            "sig_a_base64/sig_b_base64（float32 交错 base64），两路等长同源；"
            "并给 sample_rate_hz、baseline_m（基线长度）、baseline_bearing_deg"
            "（A→B 基线方位）。"
            "失败恢复：相关峰不显著（默认门限 0.2）或时延超几何上限时，"
            "**不给方位角**，只回峰值/原因（如'两路可能不同源或时延超范围'）。"
            "副作用：纯计算，不动设备、不写文件。"
            "能力边界：只做基带互相关测向（单频/宽带皆可，但要求两路同一信号）；"
            "不涉及阵列成像（成像需 uv 覆盖与反演，不在本工具范围）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "sig_a_path": {"type": "string",
                               "description": "站 A 的 complex64 基带 raw 文件"},
                "sig_b_path": {"type": "string",
                               "description": "站 B 的 complex64 基带 raw 文件"},
                "sig_a_base64": {"type": "string",
                                 "description": "站 A 基带的 base64（float32 交错）"},
                "sig_b_base64": {"type": "string",
                                 "description": "站 B 基带的 base64（float32 交错）"},
                "sample_rate_hz": {"type": "number",
                                   "description": "两路采样率（Hz）"},
                "baseline_m": {"type": "number",
                               "description": "两站基线长度（米）"},
                "baseline_bearing_deg": {"type": "number", "default": 0.0,
                                         "description": "A→B 基线方位（度，自北顺时针）"},
                "max_delay_s": {"type": "number",
                                "description": "允许的最大时延（秒）；缺省按 2D/c"},
                "min_peak": {"type": "number", "default": 0.2,
                             "description": "相关峰显著门限（归一化）"},
            },
            "required": ["sample_rate_hz", "baseline_m"],
        },
        handler=_tdoa,
        category="satellite",
    )

    registry.register(
        name="interferometry_resolution",
        description=(
            "射电干涉的角分辨率与可见度：θ_res ≈ λ/D（讲义分辨率对照表口径："
            "人眼 500nm/2mm → 1 arcmin；单台 VLA 25m/5GHz → 1 arcmin；"
            "CygA 5GHz/25km → 0.5 arcsec）；可见度 I·cos(2πf·D·sinθ/c)。"
            "前置条件：角分辨率需 wavelength_m+aperture_m（或 freq_hz+baseline_m，"
            "波长由光速/频率换算）；可见度需 freq_hz+baseline_m（+theta_deg）。"
            "失败恢复：缺参数即报错并说明缺哪组。"
            "副作用：纯计算。"
            "能力边界：只给单孔径/单基线的标量关系，不做阵列 uv 覆盖与成像反演。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "wavelength_m": {"type": "number", "description": "波长（米）"},
                "aperture_m": {"type": "number",
                               "description": "孔径或基线长度（米）"},
                "freq_hz": {"type": "number", "description": "观测频率（Hz）"},
                "baseline_m": {"type": "number", "description": "基线长度（米）"},
                "theta_deg": {"type": "number", "default": 0.0,
                              "description": "源相对基线的仰角（度）"},
                "amplitude": {"type": "number", "default": 1.0,
                              "description": "源强度 I（任意单位）"},
            },
            "required": [],
        },
        handler=_resolution,
        category="satellite",
    )

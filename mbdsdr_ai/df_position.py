# SPDX-License-Identifier: MIT
"""P0-4 测向/定位原子工具（相控阵相关测向 + 多站 TDOA 定位）。

能力拆解（218 条）里 P0-4 的原子能力此前只有双站互相关测向
（interferometry.tdoa_bearing），缺两块：
  * **单站阵列相关测向**——多阵元同时采样 → 导向矢量相关扫描 → 方位角，
    多峰/栅瓣**歧义显式暴露**（不静默挑峰）；
  * **多站 TDOA 定位**——≥3 站时延差 → 双曲线最小二乘 → 发射源位置，
    残差/GDOP 如实输出。

诚实纪律（AGENTS 第 2 条）：
  * 次峰与主峰差距不足（默认 3 dB）→ 报 ambiguity，不报单一方位；
  * 栅瓣（阵元间距 > λ/2 的周期模糊）在结果里显式标注；
  * TDOA <3 站、不收敛、残差超门限 → 如实报错，不产出位置；
  * 角度给 rad/deg 双单位；定位给 ECEF/ENU 与不确定度（GDOP）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .interferometry import C_M_S, RAD_TO_DEG

__all__ = [
    "array_correlator_df", "multistation_tdoa_fix", "register_tool_registry",
]

#: 默认角度扫描分辨率（度）
_SCAN_STEP_DEG = 0.5
#: 歧义判定：次峰与主峰功率比超过该值（线性）即报多解
_AMBIG_RATIO = 10.0 ** (-3.0 / 10.0)


def array_correlator_df(snapshots: Sequence[Sequence[complex]],
                        sample_rate_hz: float,
                        freq_hz: float,
                        element_positions_m: Sequence[Sequence[float]],
                        scan_step_deg: float = _SCAN_STEP_DEG,
                        ambig_ratio: float = _AMBIG_RATIO,
                        ) -> Dict[str, Any]:
    """单站阵列相关测向（导向矢量相关扫描）。

    snapshots          : K 阵元同时刻复基带快拍（每元长度 N 的复序列）；
    sample_rate_hz     : 采样率；
    freq_hz            : 信号载频（测向用波长 λ = c/f）；
    element_positions_m: 阵元坐标（本地 ENU 平面，米，行 = [x, y] 或 [x, y, z]）；
    scan_step_deg      : 方位扫描步长；
    ambig_ratio        : 歧义门限（次峰/主峰功率比，默认 -3 dB）。

    返回：peaks（按功率降序的全部显著峰，含角度与功率比）/ ambiguity
    （True = 有不可区分的多解）/ grating_lobes（阵元间距 > λ/2 时 True）/
    primary_bearing_deg（歧义时为 None——不静默挑峰）。
    """
    pos = np.asarray(element_positions_m, dtype=float)
    if pos.ndim != 2 or pos.shape[1] not in (2, 3):
        raise ValueError("element_positions_m 须为 K×2 或 K×3 坐标数组")
    k = pos.shape[0]
    if k < 2:
        raise ValueError("测向至少需要 2 个阵元")
    x = np.asarray([np.asarray(s, dtype=np.complex128) for s in snapshots])
    if x.shape[0] != k:
        raise ValueError("快拍数与阵元数不一致")
    n = x.shape[1]
    if n < 8:
        raise ValueError("快拍太短（<8 采样），相关无意义")
    if freq_hz <= 0 or sample_rate_hz <= 0:
        raise ValueError("freq_hz / sample_rate_hz 必须为正")
    wavelength = C_M_S / float(freq_hz)

    # 阵元间距栅瓣检查（相邻间距 > λ/2 会出现周期模糊；阵列孔径天然 > λ/2，
    # 那不是栅瓣——别用孔径判断）
    d_min = float("inf")
    for i in range(k):
        for j in range(i + 1, k):
            d_min = min(d_min, float(np.linalg.norm(pos[i] - pos[j])))
    grating = d_min > wavelength / 2.0

    # 相关矩阵 R = X·X^H（窄带假设：快拍内相位结构由载频与几何决定）
    r = x @ x.conj().T
    # 归一化，去掉整体功率
    scale = np.sqrt(np.abs(np.diag(r)))
    scale[scale == 0] = 1.0
    r = r / scale[:, None] / scale[None, :]

    # 方位扫描：P(θ) = |a(θ)^H · R · a(θ)|，a 为导向矢量
    n_steps = max(2, int(round(360.0 / max(0.05, scan_step_deg))))
    thetas = np.linspace(0.0, 2.0 * math.pi, n_steps, endpoint=False)
    p = np.zeros(n_steps)
    pos3 = np.column_stack([pos[:, 0], pos[:, 1],
                            pos[:, 2] if pos.shape[1] == 3
                            else np.zeros(k)])
    for i, th in enumerate(thetas):
        # 平面波方向 u = [sinθ, cosθ]（自北顺时针方位角口径：θ=0 → +y 北）
        u = np.array([math.sin(th), math.cos(th), 0.0])
        delays = pos3 @ u / C_M_S
        a = np.exp(-2j * math.pi * float(freq_hz) * delays)
        p[i] = float(np.real(a.conj().T @ r @ a)) / k

    # 显著峰提取（局部极大 + 高于底噪门限）
    peaks_idx: List[int] = []
    floor = float(np.median(p))
    for i in range(n_steps):
        prev = p[(i - 1) % n_steps]
        nxt = p[(i + 1) % n_steps]
        if p[i] >= prev and p[i] >= nxt and p[i] > floor * 1.5:
            peaks_idx.append(i)
    peaks_idx.sort(key=lambda i: -p[i])
    if not peaks_idx:
        return {
            "peaks": [],
            "ambiguity": False,
            "primary_bearing_deg": None,
            "grating_lobes": grating,
            "wavelength_m": wavelength,
            "reason": ("扫描无显著峰（功率分布平坦）：信号不在快拍内、"
                       "SNR 不足或快拍不同步——不给方位"),
        }
    p_max = p[peaks_idx[0]]
    # 峰值细化：抛物线插值（亚步长精度）
    peaks: List[Dict[str, Any]] = []
    for i in peaks_idx[:8]:
        y0, y1, y2 = p[(i - 1) % n_steps], p[i], p[(i + 1) % n_steps]
        delta = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 \
            else 0.0
        th = thetas[i] + delta * (thetas[1] - thetas[0])
        bearing = (th * RAD_TO_DEG) % 360.0
        peaks.append({
            "bearing_deg": bearing,
            "bearing_rad": th % (2.0 * math.pi),
            "power_ratio": float(p[i] / p_max) if p_max else 0.0,
        })
    ambig = len(peaks) > 1 and peaks[1]["power_ratio"] >= ambig_ratio
    return {
        "peaks": peaks,
        "ambiguity": ambig,
        "primary_bearing_deg": None if ambig else peaks[0]["bearing_deg"],
        "grating_lobes": grating,
        "wavelength_m": wavelength,
        "n_elements": k,
        "scan_step_deg": 360.0 / n_steps,
    }


def multistation_tdoa_fix(stations_m: Sequence[Sequence[float]],
                          tdoa_s: Sequence[float],
                          reference_idx: int = 0,
                          max_iter: int = 20,
                          conv_m: float = 1e-6,
                          max_resid_m: float = 1.0,
                          ) -> Dict[str, Any]:
    """多站 TDOA 定位（双曲线最小二乘，≥3 站）。

    stations_m   : 各站坐标（米，同一本地平面，2D [x,y]）；
    tdoa_s       : 各站相对参考站的时延差（秒，长度 = 站数；参考站项忽略）；
    reference_idx: 参考站下标。

    未知量 (x, y)：观测 h_i = |x - s_i| - |x - s_ref| = c·τ_i。
    返回：pos_m / gdop / residuals_m / converged；<3 站、不收敛、
    残差超门限如实报错（不产出位置）。
    """
    s = np.asarray(stations_m, dtype=float)
    if s.ndim != 2 or s.shape[1] != 2:
        raise ValueError("stations_m 须为 M×2 平面坐标（米）")
    m = s.shape[0]
    if m < 3:
        raise ValueError(
            f"站点数 {m} < 3——平面 TDOA 定位至少需要 3 站（2 站只能给"
            "方位线，见 interferometry_tdoa_bearing），不降维硬解")
    tau = np.asarray(tdoa_s, dtype=float).ravel()
    if tau.size != m:
        raise ValueError("tdoa_s 长度须等于站点数（参考站项可为 0）")
    ref = int(reference_idx)
    if not 0 <= ref < m:
        raise ValueError("reference_idx 越界")
    c = C_M_S
    obs = c * tau  # 距离差（米）

    others = [i for i in range(m) if i != ref]
    x = s[ref].astype(float) + np.array([1.0e3, 0.0])  # 初值：参考站旁
    history: List[float] = []
    for it in range(1, int(max_iter) + 1):
        d_ref = math.hypot(x[0] - s[ref][0], x[1] - s[ref][1])
        if d_ref < 1.0:
            raise ValueError("迭代落到参考站上（奇异），不产出位置")
        los_ref = (x - s[ref]) / d_ref
        h_rows = []
        resid = []
        for i in others:
            d_i = math.hypot(x[0] - s[i][0], x[1] - s[i][1])
            los_i = (x - s[i]) / max(d_i, 1.0)
            pred = d_i - d_ref
            resid.append(obs[i] - obs[ref] - pred)
            h_rows.append(los_i - los_ref)
        h_mat = np.asarray(h_rows, dtype=float)
        r_vec = np.asarray(resid, dtype=float)
        try:
            delta, *_ = np.linalg.lstsq(h_mat, r_vec, rcond=None)
        except np.linalg.LinAlgError:
            raise ValueError("双曲线方程组奇异（站共线或几何病态），不产出位置")
        x = x + delta
        step = float(np.linalg.norm(delta))
        history.append(step)
        if step < conv_m:
            break
    d_ref = math.hypot(x[0] - s[ref][0], x[1] - s[ref][1])
    los_ref = (x - s[ref]) / d_ref
    residuals = []
    for i in others:
        d_i = math.hypot(x[0] - s[i][0], x[1] - s[i][1])
        pred = d_i - d_ref
        residuals.append(float(obs[i] - obs[ref] - pred))
    resid_m = float(np.sqrt(np.mean(np.asarray(residuals) ** 2)))
    try:
        gdop = float(np.sqrt(np.trace(np.linalg.inv(h_mat.T @ h_mat))))
    except np.linalg.LinAlgError:
        gdop = float("inf")
    converged = history and history[-1] < conv_m
    if not converged:
        raise ValueError(
            f"{max_iter} 次迭代未收敛（末步长 {history[-1]:.3e} m）"
            "——TDOA 与站几何可能不一致，不产出位置")
    if resid_m > max_resid_m:
        raise ValueError(
            f"定位残差 RMS {resid_m:.3f} m 超门限 {max_resid_m} m——"
            "观测含粗差或站坐标有误，不产出位置（粗差剔除后重试）")
    return {
        "pos_m": [float(v) for v in x],
        "gdop": gdop,
        "residuals_m": residuals,
        "residual_rms_m": resid_m,
        "converged": True,
        "iterations": it,
        "history": history,
        "n_stations": m,
    }


# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_tool_registry(registry) -> None:
    """注册 P0-4 测向/定位积木（阵列相关测向 + 多站 TDOA 定位）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _array_df(args):
        try:
            snaps = args.get("snapshots")
            if not snaps:
                raise ValueError(
                    "snapshots 必须提供（K 阵元 × N 复基带快拍，"
                    "复数形如 {\"re\":..,\"im\":..} 列表）")
            parsed = []
            for snap in snaps:
                parsed.append([complex(v["re"], v["im"]) if isinstance(v, dict)
                               else complex(v) for v in snap])
            res = array_correlator_df(
                parsed,
                sample_rate_hz=float(args.get("sample_rate_hz", 1e6)),
                freq_hz=float(args.get("freq_hz", 435.4e6)),
                element_positions_m=args.get("element_positions_m"),
                scan_step_deg=float(args.get("scan_step_deg", 0.5)),
                ambig_ratio=float(args.get("ambig_ratio", _AMBIG_RATIO)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"阵列测向失败: {clean_exc(e)}")
        if res.get("primary_bearing_deg") is None:
            return ToolResult(
                success=False,
                content=_json.dumps(res, ensure_ascii=False, default=str),
                data=res)
        return ToolResult(
            success=True,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    def _tdoa_fix(args):
        try:
            res = multistation_tdoa_fix(
                stations_m=args.get("stations_m"),
                tdoa_s=args.get("tdoa_s"),
                reference_idx=int(args.get("reference_idx", 0)),
                max_iter=int(args.get("max_iter", 20)),
                max_resid_m=float(args.get("max_resid_m", 1.0)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"TDOA 定位失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    registry.register(
        name="array_correlator_df",
        description=(
            "单站阵列相关测向：K 阵元同时刻复基带快拍 → 相关矩阵 → "
            "导向矢量方位扫描 → 方位角。多峰/栅瓣歧义显式暴露"
            "（次峰距主峰不足 3 dB 时不报单一方位，只回峰列表）。"
            "前置条件：snapshots（K×N 复数）、freq_hz、"
            "element_positions_m（K×2/K×3 ENU 坐标米）。"
            "副作用：纯计算。"
            "能力边界：窄带平面波假设；不解相干多径（多径需空间平滑/去相干）。"
        ),
        parameters={"type": "object", "properties": {
            "snapshots": {"type": "array",
                          "description": "K 阵元复基带快拍（复数列表的列表）"},
            "sample_rate_hz": {"type": "number", "default": 1000000.0},
            "freq_hz": {"type": "number", "description": "信号载频（Hz）"},
            "element_positions_m": {"type": "array",
                                    "description": "K×2/K×3 阵元坐标（米）"},
            "scan_step_deg": {"type": "number", "default": 0.5},
            "ambig_ratio": {"type": "number", "default": _AMBIG_RATIO},
        }, "required": ["snapshots", "freq_hz", "element_positions_m"]},
        handler=_array_df, category="satellite",
    )
    registry.register(
        name="multistation_tdoa_fix",
        description=(
            "多站 TDOA 定位：≥3 站坐标 + 各站相对参考站的时延差 → "
            "双曲线最小二乘 → 发射源平面位置（带 GDOP/残差）。"
            "<3 站、不收敛、残差超门限如实报错不产出位置。"
            "前置条件：stations_m（M×2 米）、tdoa_s（秒，长度=站数）。"
            "副作用：纯计算。"
            "能力边界：2D 平面定位；3D 需站间高程差或仰角观测（不在本工具）。"
        ),
        parameters={"type": "object", "properties": {
            "stations_m": {"type": "array", "description": "M×2 站坐标（米）"},
            "tdoa_s": {"type": "array", "description": "各站时延差（秒）"},
            "reference_idx": {"type": "integer", "default": 0},
            "max_iter": {"type": "integer", "default": 20},
            "max_resid_m": {"type": "number", "default": 1.0},
        }, "required": ["stations_m", "tdoa_s"]},
        handler=_tdoa_fix, category="satellite",
    )

# SPDX-License-Identifier: MIT
"""P1-5 比幅测向（双波束幅度比较）简化版原子工具。

能力拆解（218 条）P1-5：P0-4（相关干涉仪 + 多站 TDOA）的渐进路径
——两通道幅度比较的简化测向，适合只有两路接收（或旋转单天线
扫掠）的最小装备配置。

  df_amplitude_compare(power_a_db, power_b_db, ...)
    双波束斜射 ±squint 的高斯波束模型（−3 dB 点 = beamwidth）：
      G(θ) = G0 · 10^(−2.776·(θ∓squint)² / (2·beamwidth²))…
    以功率差 ΔP(θ)=P_A−P_B 的模型曲线反解偏轴角 θ，网格扫描 +
    局部细化，残差如实上报。

诚实边界（AGENTS 第 2 条）：
  * 结果是**模型反演**不是直接测量——model 字段明示所用波束模型；
  * 支持外部注入实测方向图 pattern_table（校准表优先于理想模型）；
  * 双解/无解（曲线非单调区）如实上报 n_solutions；
  * 两通道增益不平衡未标定时引入系统偏置，offset_db 参数显式暴露。

与 P1-2 协同：pattern_table 可由 frame_layout_tool 同风格的配置
文件注入，实现"皮进配置"。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["df_amplitude_compare", "register_tool_registry"]


def _gauss_db(off_deg: float, beamwidth_deg: float) -> float:
    """高斯波束 dB 方向图：off=beamwidth 处恰好 −3 dB。"""
    return -3.0103 * (off_deg / beamwidth_deg) ** 2


def _model_delta(theta: float, squint_deg: float,
                 beamwidth_deg: float, offset_db: float) -> float:
    """两通道功率差模型 ΔP(θ) = P_A − P_B（dB）。"""
    return (_gauss_db(theta - squint_deg, beamwidth_deg)
            - _gauss_db(theta + squint_deg, beamwidth_deg)
            + offset_db)


def _table_delta(theta: float, table: Dict[str, Sequence[float]],
                 ) -> Optional[float]:
    """校准表线性插值 ΔP(θ)；θ 超出表范围返回 None（不外推）。"""
    angles = list(table["angles_deg"])
    deltas = list(table["delta_db"])
    if len(angles) != len(deltas) or len(angles) < 2:
        raise ValueError("pattern_table 须含等长 angles_deg/delta_db（≥2 点）")
    if angles != sorted(angles):
        raise ValueError("pattern_table.angles_deg 须递增")
    if theta < angles[0] or theta > angles[-1]:
        return None
    for i in range(len(angles) - 1):
        if angles[i] <= theta <= angles[i + 1]:
            w = (theta - angles[i]) / (angles[i + 1] - angles[i])
            return deltas[i] + w * (deltas[i + 1] - deltas[i])
    return None


def df_amplitude_compare(power_a_db: float,
                         power_b_db: float,
                         squint_deg: float = 15.0,
                         beamwidth_deg: float = 30.0,
                         offset_db: float = 0.0,
                         pattern_table: Optional[Dict[str, Sequence[float]]]
                         = None,
                         search_half_deg: float = 90.0,
                         ) -> Dict[str, Any]:
    """双通道比幅测向：功率差 → 偏轴角估计。

    power_a_db / power_b_db : 两通道接收功率（dB，同一检波口径）；
    squint_deg              : 波束斜射角（±，度）；
    beamwidth_deg           : 单波束 −3 dB 宽度（度）；
    offset_db               : 通道增益不平衡标定量（dB，A−B 口径）。
                              B 通道偏低 δ dB 时测得 ΔP 偏高 +δ，
                              取 offset_db=+δ 在模型侧补偿回真值；
    pattern_table           : 实测 ΔP(θ) 校准表（优先于高斯模型）；
    search_half_deg         : 搜索半宽（度）。

    返回：off_boresight_deg / n_solutions / residual_db / model /
    power_delta_db。多解（非单调区两个 θ 给出同 ΔP）全部上报，
    主解取 |θ| 最小者。
    """
    if beamwidth_deg <= 0.0:
        raise ValueError("beamwidth_deg 须为正")
    if squint_deg <= 0.0:
        raise ValueError("squint_deg 须为正")
    if search_half_deg <= 0.0:
        raise ValueError("search_half_deg 须为正")

    measured = float(power_a_db) - float(power_b_db)
    lo, hi = -search_half_deg, search_half_deg

    if pattern_table is not None:
        def delta_of(th: float) -> Optional[float]:
            return _table_delta(th, pattern_table)
        model = "pattern_table"
    else:
        def delta_of(th: float) -> float:
            return _model_delta(th, squint_deg, beamwidth_deg, offset_db)
        model = "gaussian_squint"

    # 网格扫描全解（比幅曲线非单调，多解如实保留）。
    # 用**带符号误差** s(θ)=ΔP_model(θ)−ΔP_measured 找变号交叉，
    # 残差取 |s|（绝对值没有符号，不能直接做乘积判据）。
    n_grid = 721
    grid = [lo + (hi - lo) * i / (n_grid - 1) for i in range(n_grid)]
    svals: List[Optional[float]] = []
    for th in grid:
        d = delta_of(th)
        svals.append(None if d is None else d - measured)
    solutions: List[Dict[str, Any]] = []
    for i in range(n_grid - 1):
        s0, s1 = svals[i], svals[i + 1]
        if s0 is None or s1 is None:
            continue
        if s0 == 0.0:
            solutions.append((grid[i], 0.0))
        elif s0 * s1 < 0.0:
            a, b = grid[i], grid[i + 1]
            sa = s0
            for _ in range(60):
                m = 0.5 * (a + b)
                dm = delta_of(m)
                if dm is None:
                    break
                sm = dm - measured
                if sa * sm <= 0.0:
                    b = m
                else:
                    a, sa = m, sm
            th_root = 0.5 * (a + b)
            d_root = delta_of(th_root)
            resid = abs(d_root - measured) if d_root is not None else float("inf")
            solutions.append((th_root, resid))
    # 去重（相邻根）
    uniq: List[Dict[str, Any]] = []
    for th, res in solutions:
        if any(abs(th - u["off_boresight_deg"]) < 0.05 for u in uniq):
            continue
        uniq.append({"off_boresight_deg": round(th, 4),
                     "residual_db": round(res, 4)})
    if not uniq:
        # 无交点：曲线整体偏离，给出最近点 + 诚实理由
        valid = [(i, abs(s)) for i, s in enumerate(svals) if s is not None]
        if not valid:
            raise ValueError("搜索域内无有效模型点（检查 pattern_table 范围）")
        best_i, best_e = min(valid, key=lambda t: t[1])
        return {
            "off_boresight_deg": None,
            "n_solutions": 0,
            "residual_db": round(best_e, 4),
            "model": model,
            "power_delta_db": round(measured, 4),
            "reason": (f"测得 ΔP={measured:.2f} dB 与模型曲线最小偏差 "
                       f"{best_e:.2f} dB 仍不交——增益未标定或"
                       "目标在搜索域外"),
        }
    uniq.sort(key=lambda d: abs(d["off_boresight_deg"]))
    primary = uniq[0]
    return {
        "off_boresight_deg": primary["off_boresight_deg"],
        "n_solutions": len(uniq),
        "solutions": uniq,
        "residual_db": primary["residual_db"],
        "model": model,
        "power_delta_db": round(measured, 4),
        "reason": None,
    }


# ─────────────────────────────────────────────────────────────
# 工具注册
# ─────────────────────────────────────────────────────────────
def register_tool_registry(registry) -> None:
    """注册 P1-5 比幅测向积木。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _df(args):
        try:
            res = df_amplitude_compare(
                float(args.get("power_a_db", 0.0)),
                float(args.get("power_b_db", 0.0)),
                squint_deg=float(args.get("squint_deg", 15.0)),
                beamwidth_deg=float(args.get("beamwidth_deg", 30.0)),
                offset_db=float(args.get("offset_db", 0.0)),
                pattern_table=args.get("pattern_table"),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"比幅测向失败: {clean_exc(e)}")
        ok = res["off_boresight_deg"] is not None
        return ToolResult(
            success=ok,
            content=_json.dumps(res, ensure_ascii=False),
            data=res)

    registry.register(
        name="df_amplitude_compare",
        description=(
            "双通道比幅测向：功率差反演偏轴角（高斯斜射波束模型或"
            "实测校准表）。模型反演非直接测量，多解/无解/残差如实"
            "上报；通道不平衡用 offset_db 显式标定。"),
        parameters={
            "type": "object",
            "properties": {
                "power_a_db": {"type": "number"},
                "power_b_db": {"type": "number"},
                "squint_deg": {"type": "number", "default": 15.0},
                "beamwidth_deg": {"type": "number", "default": 30.0},
                "offset_db": {"type": "number", "default": 0.0},
                "pattern_table": {"type": ["object", "null"], "default": None},
            },
            "required": ["power_a_db", "power_b_db"],
        },
        handler=_df,
        category="satellite")

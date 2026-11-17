#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""
rtk_solver.py — RTK 浮点解 / 固定解（LAMBDA 整数最小二乘）

本模块依据公开 RTK/GNSS 标准方法独立实现：
  - 双差观测组合（double-difference residuals）
  - 浮点解：KF 迭代（``solve``）与单历元批处理加权最小二乘（``solve_fixed``
    内部使用，给出诚实的后验协方差）
  - 固定解：LAMBDA（LDL 分解 + 整数 Gauss 降相关 + 整数最小二乘搜索）

实现边界（诚实标注）：
  - ``fix_type`` 只有 ``FLOAT`` / ``FIXED`` / ``NONE``；固定失败一律回退
    float，并在结果里写 ``failure_reason`` 与「未固定」，不把 float 说成厘米级；
  - 固定解按**单历元**批处理（不跨历元维持模糊度连续），同频双差假设；
  - 测试用合成双差观测验证内部一致性，不代表真实空口厘米级精度。

RTKLIB 仅作为技术参考与致谢，本模块未包含其源代码。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .geodesy import (
    CLIGHT, CoordinateConverter, R2D, PI,
)
from .gnss_environment import uncorrected_error_budget


# ============================================================
# LAMBDA / 整数最小二乘（ILS）工程默认参数
# ============================================================
#: 固定判据阈值：最优/次优整数候选的残差平方和之比（RTKLIB 族常用口径）。
#: 3.0 是长期沿用的**工程经验值**，不是理论保证；弱几何/多路径场景应上调。
DEFAULT_RATIO_THRESHOLD = 3.0
#: 单次整数搜索的节点预算；超出即判定「无法在预算内确证固定」并回退 float。
DEFAULT_SEARCH_NODE_BUDGET = 200_000
#: 尝试固定所需的最少共视卫星数：4 星时 3 个双差与 3 维位置刚好定解、
#: 无多余观测，ratio 不可信，故要求至少 5 星（工程默认）。
MIN_SATS_FOR_FIX = 5
#: 搜索椭球的最大扩张次数（每次 ×4）；仅在「只找到一个候选」时继续扩张。
_MAX_BOUND_EXPANSIONS = 60
#: 双差观测标准差（与 ``_build_dd_equations`` 现有权重保持一致）
_SIGMA_CODE_M = 1.0
_SIGMA_PHASE_M = 0.01


# ============================================================
# 数据结构
# ============================================================
@dataclass
class RTKObs:
    """单测站单卫星观测。"""
    prn: int                       # 卫星号（RINPRN）
    system: str = "GPS"            # GPS/GLONASS/Galileo/BeiDou
    rs: np.ndarray = field(default_factory=lambda: np.zeros(3))  # 卫星 ECEF
    dts: float = 0.0               # 卫星钟差(s)
    pseudorange: float = 0.0       # m
    carrier_phase: float = 0.0     # m (phase range, 未除波长)
    doppler: float = 0.0
    cnr: float = 0.0
    wavelength: float = 0.1903     # L1 波长(m)，默认 GPS L1


@dataclass
class RTKInput:
    """一次 RTK 解算输入。"""
    base_ecef: np.ndarray                  # (3,) 基站坐标
    base_obs: List[RTKObs]                 # 基站观测
    rover_obs: List[RTKObs]               # 流动站观测
    gps_tow: float = 0.0                   # 周内秒


# ============================================================
# RTKFloatSolver
# ============================================================
class RTKFloatSolver:
    """RTK 浮点解算器。

    状态向量：
        x[0:3]  = 流动站 ECEF 位置
        x[3:]   = 各双差模糊度(m)

    双差残差：
        v = (P_rov_i - P_base_i) - (P_rov_j - P_base_j)
    H 行（视线方向单位向量）：
        H[k] = -e_rov_i[k] + e_rov_j[k]   for k=0,1,2

    为什么没有钟差与对流层湿延迟状态：双差对同一接收机、同一卫星对做差，
    接收机钟差与卫星钟差同时消去；对流层/电离层共模项也在两条单差之间
    相消。二者在本设计矩阵里都没有对应列，KF 永远给不出更新——留在状态里
    只会是一个永远估不出来的「假状态」（旧版 x[3] 钟差、x[4] ZWD 即此）。
    因此只保留真正可观测的量：位置与各双差模糊度，每个状态列都必须在
    ``_build_dd_equations`` 的 H 中有非零元。
    """

    def __init__(self, max_iter: int = 10, conv_tol: float = 1e-3,
                 sigma_pos: float = 1.0, sigma_amb: float = 30.0,
                 ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
                 min_fix_sats: int = MIN_SATS_FOR_FIX):
        """初始化 float/fixed 解算器。

        ``ratio_threshold``：LAMBDA 固定判据（次优/最优候选残差平方和之比）。
        默认 ``3.0`` 是 RTKLIB 族实现长期沿用的**工程默认值**（经验门限，
        非理论保证）；噪声大、基线长或几何弱时应上调。

        ``min_fix_sats``：尝试整数固定的最少共视卫星数，默认 ``5``。
        4 星时 3 个双差与 3 维位置刚好定解、没有多余观测，ratio 不可信，
        因此不尝试固定（属于工程约定，不是理论下限）。
        """
        self.max_iter = max_iter
        self.conv_tol = conv_tol
        self.sigma_pos = sigma_pos
        self.sigma_amb = sigma_amb
        self.ratio_threshold = float(ratio_threshold)
        self.min_fix_sats = int(min_fix_sats)
        # 状态与协方差（首次调用时初始化）
        self.x: Optional[np.ndarray] = None
        self.P: Optional[np.ndarray] = None
        self._amb_index: Dict[Tuple[int, int], int] = {}  # (prn_i, prn_j) -> state idx
        # 每个模糊度状态对应的双差对（ref_prn, other_prn，顺序即方程顺序）
        self._amb_pairs: List[Tuple[int, int]] = []
        # 每个模糊度状态对应的波长(m/周)，用于米 <-> 周换算
        self._amb_wavelength: Dict[int, float] = {}

    # ----------------------------------------------------------
    # 选择参考卫星（仰角最高）
    # ----------------------------------------------------------
    @staticmethod
    def _select_ref_sat(obs: List[RTKObs], rr: np.ndarray) -> int:
        """选择仰角最高的卫星作为参考星。"""
        best_i = 0
        best_el = -1.0
        lat, lon, _ = CoordinateConverter.ecef_to_llh(*rr)
        for i, o in enumerate(obs):
            _, e = CoordinateConverter.geodist(o.rs, rr)
            _, el = CoordinateConverter.satazel(lat, lon, e)
            if el > best_el:
                best_el = el
                best_i = i
        return best_i

    # ----------------------------------------------------------
    # 双差残差与设计矩阵
    # ----------------------------------------------------------
    def _build_dd_equations(self, inp: RTKInput,
                            x: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """构造双差残差 v、设计矩阵 H、权阵 R。

        返回 (v, H, R_diag)。
        简化：只处理共视卫星的伪距双差 + 载波相位双差（同频）。
        双差已消去接收机钟差与共模大气延迟，因此状态里不设这两项。
        """
        # 索引对齐：基站/流动站按 prn 匹配
        base_by_prn = {o.prn: o for o in inp.base_obs}
        common = [o for o in inp.rover_obs if o.prn in base_by_prn]
        if len(common) < 4:
            return np.zeros(0), np.zeros((0, len(x))), np.zeros(0)

        ref_i = self._select_ref_sat(common, x[:3])
        ref = common[ref_i]
        ref_base = base_by_prn[ref.prn]

        # ---- 第一遍：收集所有 DD 对的模糊度键，扩展状态 ----
        amb_keys: List[Tuple[int, int]] = []
        for j, o in enumerate(common):
            if j == ref_i:
                continue
            amb_key = (min(ref.prn, o.prn), max(ref.prn, o.prn))
            if amb_key not in self._amb_index:
                new_idx = len(self.x)
                self._amb_index[amb_key] = new_idx
                # 扩展状态与协方差
                self.x = np.append(self.x, 0.0)
                new_row = np.zeros((1, self.P.shape[1]))
                self.P = np.vstack([self.P, new_row])
                new_col = np.zeros((self.P.shape[0], 1))
                self.P = np.hstack([self.P, new_col])
                self.P[-1, -1] = self.sigma_amb ** 2
                # 记录该状态对应的双差对与波长（solve_fixed / fix 用）
                self._amb_pairs.append((ref.prn, o.prn))
                self._amb_wavelength[new_idx] = float(o.wavelength)
            amb_keys.append(amb_key)

        v_list: List[float] = []
        H_list: List[np.ndarray] = []
        R_list: List[float] = []
        nx = len(self.x)

        for j, o in enumerate(common):
            if j == ref_i:
                continue
            ob = base_by_prn[o.prn]

            # ---- 几何距离 ----
            r_rov_i, e_rov_i = CoordinateConverter.geodist(ref.rs, x[:3])
            r_base_i, _ = CoordinateConverter.geodist(ref.rs, inp.base_ecef)
            r_rov_j, e_rov_j = CoordinateConverter.geodist(o.rs, x[:3])
            r_base_j, _ = CoordinateConverter.geodist(o.rs, inp.base_ecef)

            # ---- 伪距双差残差 ----
            sd_pr_i_ = ref.pseudorange - ref_base.pseudorange
            sd_pr_j_ = o.pseudorange - ob.pseudorange
            pred_pr = (r_rov_i - r_base_i) - (r_rov_j - r_base_j)
            v_pr = (sd_pr_i_ - sd_pr_j_) - pred_pr

            H_pr = np.zeros(nx)
            #  H[k] = -e_rov_i[k] + e_rov_j[k]
            H_pr[0:3] = -e_rov_i + e_rov_j
            v_list.append(v_pr)
            H_list.append(H_pr)
            R_list.append(1.0 ** 2)  # 伪距 1m 标准差

            # ---- 载波相位双差残差（含模糊度）----
            sd_cp_i = ref.carrier_phase - ref_base.carrier_phase
            sd_cp_j = o.carrier_phase - ob.carrier_phase
            pred_cp = pred_pr
            amb_key = (min(ref.prn, o.prn), max(ref.prn, o.prn))
            amb_idx = self._amb_index[amb_key]

            v_cp = (sd_cp_i - sd_cp_j) - pred_cp
            H_cp = np.zeros(nx)
            H_cp[0:3] = -e_rov_i + e_rov_j
            H_cp[amb_idx] = -1.0
            v_list.append(v_cp)
            H_list.append(H_cp)
            R_list.append(0.01 ** 2)  # 相位 1cm 标准差

        return np.array(v_list), np.array(H_list), np.array(R_list)

    # ----------------------------------------------------------
    # 卡尔曼滤波更新
    # ----------------------------------------------------------
    def _kf_update(self, v: np.ndarray, H: np.ndarray, R_diag: np.ndarray) -> None:
        """线性卡尔曼更新：x = x + K v, P = (I-KH)P。

        EKF 更新核心公式：
            K = P H' (H P H' + R)^-1
            x += K v
            P = (I - K H) P
        """
        if len(v) == 0:
            return
        R = np.diag(R_diag)
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        dx = K @ v
        self.x = self.x + dx
        self.P = (np.eye(len(self.x)) - K @ H) @ self.P

    def _initialize_state(self, inp: RTKInput) -> None:
        """首次调用初始化状态（位置 3 维；模糊度按需追加）。"""
        # 先用基站坐标作位置初值（基线先验 0）
        x0 = np.array([
            inp.base_ecef[0], inp.base_ecef[1], inp.base_ecef[2],
        ])
        P0 = np.diag([self.sigma_pos ** 2] * 3)
        self.x = x0
        self.P = P0
        self._amb_index = {}
        self._amb_pairs = []
        self._amb_wavelength = {}

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------
    def solve(self, inp: RTKInput, atmosphere: Any = None) -> Dict[str, Any]:
        """执行一次 RTK float 解算。

        返回 dict: {lat, lon, alt, fix_type, num_sats, hdop, residuals}
        fix_type: "RTK_FLOAT"（成功）/ "NONE"（失败）
        整数固定解见 ``solve_fixed()``；本方法只给 float，不宣称厘米级。

        ``atmosphere`` 仅用于在结果里标注当天环境状态与来源：双差已消去
        共模电离层/对流层，不另加单差改正；未注入时标 uncorrected。
        """
        error_budget = (atmosphere.provenance() if atmosphere is not None
                        else uncorrected_error_budget(
                            "未注入电离层/对流层环境输入"))
        if self.x is None:
            self._initialize_state(inp)

        # 共视卫星数
        base_prns = {o.prn for o in inp.base_obs}
        rover_prns = {o.prn for o in inp.rover_obs}
        common = base_prns & rover_prns
        if len(common) < 4:
            return {"fix_type": "NONE", "num_sats": len(common),
                    "error": "insufficient common satellites",
                    "error_budget": error_budget}

        # 迭代最小二乘 + KF 更新
        for it in range(self.max_iter):
            v, H, R = self._build_dd_equations(inp, self.x)
            if len(v) == 0:
                break
            x_prev = self.x.copy()
            self._kf_update(v, H, R)
            if float(np.linalg.norm(self.x[:3] - x_prev[:3])) < self.conv_tol:
                break

        lat, lon, h = CoordinateConverter.ecef_to_llh(
            float(self.x[0]), float(self.x[1]), float(self.x[2]))

        # HDOP 近似
        try:
            Q = self.P[:3, :3]
            hdop = float(np.sqrt(Q[0, 0] + Q[1, 1]))
        except Exception:
            hdop = 99.0

        return {
            "lat_deg": lat * R2D,
            "lon_deg": lon * R2D,
            "alt": h,
            "x": float(self.x[0]), "y": float(self.x[1]), "z": float(self.x[2]),
            "fix_type": "RTK_FLOAT",
            "num_sats": len(common),
            "hdop": hdop,
            "num_ambiguities": max(0, len(self.x) - 3),
            "note": "float only（未固定）: LAMBDA 固定解请用 solve_fixed()",
            "error_budget": error_budget,
        }

    # ==========================================================
    # 整数固定：公共入口（LAMBDA / ILS）
    # ==========================================================
    def fix_ambiguities(self, float_amb=None, Q_amb=None, wavelengths=None,
                        ratio_threshold: Optional[float] = None,
                        max_candidates: int = 2,
                        node_budget: int = DEFAULT_SEARCH_NODE_BUDGET
                        ) -> Dict[str, Any]:
        """对浮点模糊度做 LAMBDA 整数固定（单历元，不重算位置）。

        参数
        ----
        float_amb, Q_amb:
            浮点模糊度与协方差，**默认单位是周（cycles）**。若同时给出
            ``wavelengths``（米/周），则二者按米制输入处理并换算：
            ``a_cycles = a_m / w``、``Q_cycles = Q_m / (w_i * w_j)``。
            两者都不给时使用最近一次 ``solve()`` / ``solve_fixed()`` 的状态
            （此时波长从双差对元数据读取）。
        wavelengths:
            每颗模糊度的载波波长（米/周）；缺省表示输入已是周。
        ratio_threshold:
            固定判据阈值；缺省取构造器的 ``ratio_threshold``（默认 3.0，
            工程经验值，非理论保证）。ratio = 次优/最优候选残差平方和之比。
        max_candidates:
            搜索至少收集的候选数（默认 2，用于计算 ratio）。
        node_budget:
            搜索节点预算；超出即判失败，不硬撑。

        返回 dict：
            ``ok`` / ``fixed`` / ``fix_type``（``FIXED``|``FLOAT``）/
            ``integers``（固定后的整数模糊度，周，失败为 None）/
            ``ratio`` / ``ratio_kind``（``exact``|``lower_bound``）/
            ``num_candidates`` / ``float``（浮点模糊度，周）/
            ``covariance``（标准差摘要）/ ``failure_reason`` / ``failure_detail``。

        诚实语义：任何失败（输入不足、协方差非正定、搜索超预算、ratio 低于
        阈值）都返回 ``fix_type="FLOAT"`` 并在 ``note`` 写「未固定」，
        绝不把 float 结果描述成厘米级。

        注意：``solve()`` 的 P 来自对同一批观测的反复 KF 更新，会偏乐观；
        工程上建议用 ``solve_fixed()``（批处理法方程）或显式传入 Q。
        """
        threshold = (self.ratio_threshold if ratio_threshold is None
                     else float(ratio_threshold))
        if float_amb is None:
            if self.x is None or self.x.size <= 3:
                return _fix_failure(
                    threshold, "insufficient_ambiguities",
                    "尚未解算或没有双差模糊度；请先调用 solve()/solve_fixed()"
                    "或显式传入 float_amb/Q_amb")
            a_m = np.asarray(self.x[3:], dtype=float)
            Q_m = np.asarray(self.P[3:, 3:], dtype=float)
            w = np.array([float(self._amb_wavelength.get(3 + i, 1.0))
                          for i in range(a_m.size)])
            a_cycles = a_m / w
            Q_cycles = Q_m / np.outer(w, w)
            source = "solver_state"
        else:
            if Q_amb is None:
                return _fix_failure(
                    threshold, "invalid_input",
                    "传入了 float_amb 但缺少协方差 Q_amb")
            a_in = np.asarray(float_amb, dtype=float).ravel()
            Q_in = np.asarray(Q_amb, dtype=float)
            if Q_in.shape != (a_in.size, a_in.size):
                return _fix_failure(
                    threshold, "covariance_shape_mismatch",
                    f"协方差形状 {Q_in.shape} 与模糊度个数 {a_in.size} 不匹配")
            if wavelengths is not None:
                w = np.asarray(wavelengths, dtype=float).ravel()
                if (w.size != a_in.size or not np.all(np.isfinite(w))
                        or np.any(w <= 0.0)):
                    return _fix_failure(
                        threshold, "invalid_input",
                        "wavelengths 长度不匹配或存在非正/非有限波长")
                a_cycles = a_in / w
                Q_cycles = Q_in / np.outer(w, w)
            else:
                a_cycles = a_in
                Q_cycles = Q_in
            source = "explicit"

        res = _lambda_integer_search(
            a_cycles, Q_cycles, threshold, max_candidates, node_budget)
        sigma = _ambiguity_sigma(Q_cycles)
        out = {
            "ok": bool(res["ok"]),
            "fixed": bool(res["fixed"]),
            "fix_type": "FIXED" if res["fixed"] else "FLOAT",
            "integers": res["integers"],
            "float": list(res["float"]),
            "ratio": res["ratio"],
            "ratio_kind": res["ratio_kind"],
            "ratio_threshold": threshold,
            "num_candidates": res["num_candidates"],
            "best_distance": res["best_distance"],
            "second_distance": res["second_distance"],
            "covariance": {
                "sigma_cycles": sigma,
                "diag_cycles2": [float(v * v) for v in sigma],
                "source": source,
            },
            "failure_reason": res["failure_reason"],
            "failure_detail": res["failure_detail"],
            "note": ("整数模糊度已固定（LAMBDA/ILS）" if res["fixed"]
                     else "未固定：整数搜索未通过判据，结果仍为 float"),
        }
        return out

    # ==========================================================
    # 固定解：批处理 float（可多历元）+ LAMBDA + 固定位置重解
    # ==========================================================
    def solve_fixed(self, inp, atmosphere: Any = None,
                    ratio_threshold: Optional[float] = None,
                    max_candidates: int = 2,
                    node_budget: int = DEFAULT_SEARCH_NODE_BUDGET
                    ) -> Dict[str, Any]:
        """RTK 固定解入口：float → LAMBDA/ILS → 固定模糊度后重解位置。

        ``inp`` 可以是单个 ``RTKInput``（单历元），也可以是
        ``RTKInput`` 序列（多历元）。**多历元要求基线静止且无周跳**：各历元
        共用同一位置与同一组整数模糊度（这是 RTK 固定解的物理前提）。
        单历元单频时每个相位双差都带独立模糊度，相位对位置无净约束，
        通常固定不了——这不是实现缺陷，而是观测几何本身的限制。

        与 ``solve()`` 的差异：

        - 用**批处理加权最小二乘**求 float 解与后验协方差（多历元时对法方程
          求和）。``solve()`` 对同一批观测反复做 KF 更新会让 P 逐轮收缩，
          不能当作固定判据的协方差；
        - 输出 ``fix_type`` 为 ``FIXED`` / ``FLOAT``（``rtk_fix_type`` 同步给
          ``RTK_FIXED`` / ``RTK_FLOAT``，兼容既有管线口径），并给整数模糊度、
          ``ratio``、残差/协方差摘要与失败原因；
        - 固定失败（共视星不足 / ratio 低于阈值 / 搜索超预算 / 协方差病态 /
          固定后位置解奇异）**一律回退 float**，``failure_reason`` 说明原因，
          ``note`` 明确写「未固定」；float 精度为分米级，不宣称厘米级。

        ``ratio_threshold`` 缺省取构造器值（默认 3.0，工程经验值）。
        """
        threshold = (self.ratio_threshold if ratio_threshold is None
                     else float(ratio_threshold))
        error_budget = (atmosphere.provenance() if atmosphere is not None
                        else uncorrected_error_budget(
                            "未注入电离层/对流层环境输入"))

        epochs = _normalize_epochs(inp)
        if epochs is None:
            return {
                "ok": False, "fix_type": "NONE", "rtk_fix_type": "NONE",
                "fixed": False, "num_sats": 0, "num_ambiguities": 0,
                "integers": None, "ratio": None, "ratio_kind": None,
                "ratio_threshold": threshold, "residuals": None,
                "covariance": None, "failure_reason": "invalid_input",
                "failure_detail": "inp 必须是 RTKInput 或其非空序列",
                "label": "未固定",
                "note": "未固定：输入类型不合法；不得宣称厘米级",
                "error_budget": error_budget,
            }

        fl = self._batch_float_ls(epochs)
        if not fl["ok"]:
            return {
                "ok": False,
                "fix_type": "NONE",
                "rtk_fix_type": "NONE",
                "fixed": False,
                "num_sats": int(fl.get("num_sats", 0)),
                "num_ambiguities": int(fl.get("num_ambiguities", 0)),
                "integers": None,
                "ratio": None,
                "ratio_kind": None,
                "ratio_threshold": threshold,
                "residuals": None,
                "covariance": None,
                "failure_reason": fl["reason"],
                "failure_detail": "float 解不可用，未尝试整数固定",
                "label": "未固定",
                "note": "未固定：float 解也不可用（观测/几何不足）；不得宣称厘米级",
                "error_budget": error_budget,
            }

        if fl["num_sats"] < self.min_fix_sats:
            return self._float_fallback(
                fl, threshold, "insufficient_satellites_for_fix",
                f"共视卫星 {fl['num_sats']} < {self.min_fix_sats}，"
                "固定判据不可信，未尝试整数固定",
                error_budget)

        w = np.asarray(fl["wavelengths"], dtype=float)
        a_cycles = np.asarray(fl["x"][3:], dtype=float) / w
        Q_cycles = np.asarray(fl["Q"][3:, 3:], dtype=float) / np.outer(w, w)
        fix = _lambda_integer_search(
            a_cycles, Q_cycles, threshold, max_candidates, node_budget)
        if not fix["fixed"]:
            return self._float_fallback(
                fl, threshold, fix["failure_reason"], fix["failure_detail"],
                error_budget, fix=fix)

        a_fixed_m = np.asarray(fix["integers"], dtype=float) * w
        pos = self._fixed_position(fl["layout"], a_fixed_m, fl["x"][:3])
        if pos is None:
            return self._float_fallback(
                fl, threshold, "fixed_position_solve_failed",
                "固定模糊度后的位置最小二乘奇异，回退 float",
                error_budget, fix=fix)

        lat, lon, h = CoordinateConverter.ecef_to_llh(*pos["x"])
        std_pos = np.sqrt(np.clip(np.diag(pos["Q"])[:3], 0.0, None))
        return {
            "ok": True,
            "fix_type": "FIXED",
            "rtk_fix_type": "RTK_FIXED",
            "fixed": True,
            "lat_deg": float(lat * R2D),
            "lon_deg": float(lon * R2D),
            "alt": float(h),
            "x": float(pos["x"][0]),
            "y": float(pos["x"][1]),
            "z": float(pos["x"][2]),
            "num_sats": int(fl["num_sats"]),
            "num_ambiguities": int(fl["num_ambiguities"]),
            "hdop": float(fl["hdop"]),
            "integers": [int(v) for v in fix["integers"]],
            "float_ambiguities_cycles": [float(v) for v in a_cycles],
            "ambiguity_keys": [list(k) for k in fl["ambiguity_keys"]],
            "ratio": fix["ratio"],
            "ratio_kind": fix["ratio_kind"],
            "num_candidates": int(fix["num_candidates"]),
            "best_distance": fix["best_distance"],
            "second_distance": fix["second_distance"],
            "ratio_threshold": threshold,
            "residuals": pos["residuals"],
            "covariance": {
                "position_std_m": [float(v) for v in std_pos],
                "ambiguity_std_cycles": _ambiguity_sigma(Q_cycles),
                "source": "fixed_phase_wls",
            },
            "failure_reason": None,
            "failure_detail": None,
            "label": "已固定",
            "note": ("已固定：LAMBDA/ILS 整数模糊度通过 ratio 判据；"
                     "精度仍受多路径与未建模误差限制"),
            "error_budget": error_budget,
        }

    # ----------------------------------------------------------
    # 固定解内部：双差布局 / 法方程 / 固定位置
    # ----------------------------------------------------------
    def _dd_layout(self, epochs: List[RTKInput]) -> Optional[Dict[str, Any]]:
        """跨历元统一双差布局（静止基线 + 无周跳前提）。

        参考星按第一历元仰角选择；参与双差的卫星取所有历元的共视交集。
        不同频的卫星对直接跳过并登记，不静默混算。
        """
        def _maps(ep: RTKInput):
            base = {o.prn: o for o in ep.base_obs}
            rover = {o.prn: o for o in ep.rover_obs}
            return base, rover

        maps = [_maps(ep) for ep in epochs]
        first_base, first_rover = maps[0]
        common = sorted(set(first_base) & set(first_rover))
        for base, rover in maps[1:]:
            common = sorted(set(common) & set(base) & set(rover))
        if len(common) < 4:
            return None
        base_ecef = np.asarray(epochs[0].base_ecef, dtype=float)
        common_obs = [first_rover[p] for p in common]
        ref_i = self._select_ref_sat(common_obs, base_ecef)
        ref_prn = common[ref_i]
        ref_w = float(first_rover[ref_prn].wavelength)
        if not math.isfinite(ref_w) or ref_w <= 0.0:
            # 缺少有效波长就无法把米制模糊度换成周，无法做整数固定
            return None

        pair_prns: List[Tuple[int, int]] = []
        wavelengths: List[float] = []
        skipped: List[Dict[str, Any]] = []
        for prn in common:
            if prn == ref_prn:
                continue
            w_ref = float(first_rover[ref_prn].wavelength)
            w_oth = float(first_rover[prn].wavelength)
            ok = (math.isfinite(w_oth) and w_oth > 0.0
                  and abs(w_oth - w_ref) <= 1e-6)
            if ok:
                for _base, rover in maps[1:]:
                    w_pair = float(rover[prn].wavelength)
                    w_ref_ep = float(rover[ref_prn].wavelength)
                    if (not math.isfinite(w_pair) or w_pair <= 0.0
                            or abs(w_pair - w_ref) > 1e-6
                            or not math.isfinite(w_ref_ep)
                            or abs(w_ref_ep - w_ref) > 1e-6):
                        ok = False
                        break
            if not ok:
                skipped.append({"prns": (ref_prn, prn),
                                "reason": "wavelength_mismatch"})
                continue
            pair_prns.append((ref_prn, prn))
            wavelengths.append(w_ref)

        per_epoch: List[Dict[str, Any]] = [
            {"rover": {p: rover[p] for p in common},
             "base": {p: base[p] for p in common}}
            for base, rover in maps
        ]
        return {
            "base_ecef": base_ecef,
            "common_prns": common,
            "ref_prn": ref_prn,
            "pair_prns": pair_prns,
            "wavelengths": wavelengths,
            "epochs": per_epoch,
            "skipped": skipped,
            "ref_wavelength": ref_w,
        }

    @staticmethod
    def _pair_terms(layout: Dict[str, Any], ep_index: int, pair_index: int,
                    p: np.ndarray) -> Tuple[float, np.ndarray, float, float]:
        """单对 DD 的几何预报、位置雅可比、伪距/相位双差观测量。"""
        ep = layout["epochs"][ep_index]
        ref_prn, oth_prn = layout["pair_prns"][pair_index]
        rr_ref = ep["rover"][ref_prn]
        rb_ref = ep["base"][ref_prn]
        rr_oth = ep["rover"][oth_prn]
        rb_oth = ep["base"][oth_prn]
        base_ecef = layout["base_ecef"]
        r_rov_i, e_i = CoordinateConverter.geodist(rr_ref.rs, p)
        r_base_i, _ = CoordinateConverter.geodist(rr_ref.rs, base_ecef)
        r_rov_j, e_j = CoordinateConverter.geodist(rr_oth.rs, p)
        r_base_j, _ = CoordinateConverter.geodist(rr_oth.rs, base_ecef)
        pred = (r_rov_i - r_base_i) - (r_rov_j - r_base_j)
        geom = -np.asarray(e_i, dtype=float) + np.asarray(e_j, dtype=float)
        pr_dd = ((rr_ref.pseudorange - rb_ref.pseudorange)
                 - (rr_oth.pseudorange - rb_oth.pseudorange))
        cp_dd = ((rr_ref.carrier_phase - rb_ref.carrier_phase)
                 - (rr_oth.carrier_phase - rb_oth.carrier_phase))
        return pred, geom, pr_dd, cp_dd

    def _epoch_rows(self, layout: Dict[str, Any], ep_index: int,
                    p: np.ndarray,
                    amb_fixed_m: Optional[np.ndarray] = None
                    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """单个历元的观测方程。

        ``amb_fixed_m is None``：float 方程（状态 = [位置(3), 模糊度(m)]）；
        否则只解位置（3 列），相位观测量扣除已固定的整数模糊度。
        """
        n_amb = len(layout["pair_prns"])
        if amb_fixed_m is None:
            H = np.zeros((2 * n_amb, 3 + n_amb))
        else:
            H = np.zeros((2 * n_amb, 3))
        y = np.zeros(2 * n_amb)
        R = np.zeros(2 * n_amb)
        for i in range(n_amb):
            pred, geom, pr_dd, cp_dd = self._pair_terms(layout, ep_index, i, p)
            H[2 * i, 0:3] = geom
            y[2 * i] = pr_dd - pred
            R[2 * i] = _SIGMA_CODE_M ** 2
            H[2 * i + 1, 0:3] = geom
            y[2 * i + 1] = cp_dd - pred
            if amb_fixed_m is None:
                # y = cp - pred = geom·δ + a  → 模糊度列取 +1
                H[2 * i + 1, 3 + i] = 1.0
            else:
                y[2 * i + 1] -= float(amb_fixed_m[i])
            R[2 * i + 1] = _SIGMA_PHASE_M ** 2
        return y, H, R

    def _batch_float_ls(self, epochs: List[RTKInput]) -> Dict[str, Any]:
        """批处理加权最小二乘 float 解（多历元法方程累加，诚实后验协方差）。"""
        layout = self._dd_layout(epochs)
        if layout is None:
            return {"ok": False, "reason": "insufficient_common_satellites",
                    "num_sats": 0, "num_ambiguities": 0}
        pairs = layout["pair_prns"]
        n_amb = len(pairs)
        if n_amb < 1:
            return {"ok": False, "reason": "insufficient_ambiguities",
                    "num_sats": len(layout["common_prns"]),
                    "num_ambiguities": 0}
        p = np.asarray(layout["base_ecef"], dtype=float).copy()
        amb = np.zeros(n_amb)
        n_ep = len(epochs)
        for _ in range(self.max_iter):
            A = np.zeros((3 + n_amb, 3 + n_amb))
            b = np.zeros(3 + n_amb)
            for ei in range(n_ep):
                y, H, R = self._epoch_rows(layout, ei, p)
                A += H.T @ (H / R[:, None])
                b += H.T @ (y / R)
            try:
                dx = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                return {"ok": False, "reason": "singular_normal_equations",
                        "num_sats": len(layout["common_prns"]),
                        "num_ambiguities": n_amb}
            # 位置是增量线性化；模糊度每次得到的是「全量」估计，直接覆盖
            p = p + dx[:3]
            amb = dx[3:].copy()
            if float(np.linalg.norm(dx[:3])) < self.conv_tol:
                break

        A = np.zeros((3 + n_amb, 3 + n_amb))
        v_all: List[np.ndarray] = []
        for ei in range(n_ep):
            y, H, R = self._epoch_rows(layout, ei, p)
            A += H.T @ (H / R[:, None])
            # 收敛处增量 δ=0：残差 = y，相位行再扣掉已估模糊度
            v = y.copy()
            v[1::2] -= amb
            v_all.append(v)
        try:
            Q = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            return {"ok": False, "reason": "singular_normal_equations",
                    "num_sats": len(layout["common_prns"]),
                    "num_ambiguities": n_amb}
        Q = 0.5 * (Q + Q.T)
        if not np.all(np.isfinite(Q)):
            return {"ok": False, "reason": "non_finite_covariance",
                    "num_sats": len(layout["common_prns"]),
                    "num_ambiguities": n_amb}
        x = np.concatenate([p, amb])
        residuals = _residual_summary(np.concatenate(v_all))
        residuals["post_fit"] = True
        residuals["num_epochs"] = n_ep

        # 登记状态，使 fix_ambiguities() 无参调用与 solve() 同构
        self.x = x
        self.P = Q
        self._amb_index = {}
        self._amb_pairs = []
        self._amb_wavelength = {}
        wavelengths: List[float] = []
        keys: List[Tuple[int, int]] = []
        for i, (ref_prn, oth_prn) in enumerate(pairs):
            key = (min(ref_prn, oth_prn), max(ref_prn, oth_prn))
            self._amb_index[key] = 3 + i
            self._amb_pairs.append((ref_prn, oth_prn))
            w = float(layout["wavelengths"][i])
            self._amb_wavelength[3 + i] = w
            wavelengths.append(w)
            keys.append((ref_prn, oth_prn))

        lat, lon, h = CoordinateConverter.ecef_to_llh(*x[:3])
        return {
            "ok": True,
            "x": x,
            "Q": Q,
            "layout": layout,
            "num_sats": len(layout["common_prns"]),
            "num_ambiguities": n_amb,
            "wavelengths": wavelengths,
            "ambiguity_keys": keys,
            "residuals": residuals,
            "lat_deg": float(lat * R2D),
            "lon_deg": float(lon * R2D),
            "alt": float(h),
            "hdop": float(np.sqrt(max(Q[0, 0], 0.0) + max(Q[1, 1], 0.0))),
            "skipped": layout["skipped"],
            "num_epochs": n_ep,
        }

    def _fixed_position(self, layout: Dict[str, Any],
                        amb_fixed_m: np.ndarray, p0: np.ndarray
                        ) -> Optional[Dict[str, Any]]:
        """用固定后的整数模糊度、跨历元累加重解位置；奇异返回 None。"""
        x = np.asarray(p0, dtype=float).copy()
        n_ep = len(layout["epochs"])
        for _ in range(self.max_iter):
            A = np.zeros((3, 3))
            b = np.zeros(3)
            for ei in range(n_ep):
                y, H, R = self._epoch_rows(layout, ei, x, amb_fixed_m)
                A += H.T @ (H / R[:, None])
                b += H.T @ (y / R)
            try:
                dx = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                return None
            x = x + dx
            if float(np.linalg.norm(dx)) < self.conv_tol:
                break
        A = np.zeros((3, 3))
        v_all: List[np.ndarray] = []
        for ei in range(n_ep):
            y, H, R = self._epoch_rows(layout, ei, x, amb_fixed_m)
            A += H.T @ (H / R[:, None])
            # 位置已收敛（δ≈0）：固定模型下残差就是观测减几何预报
            v_all.append(y)
        try:
            Q = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            return None
        if not np.all(np.isfinite(Q)):
            return None
        Q = 0.5 * (Q + Q.T)
        residuals = _residual_summary(np.concatenate(v_all))
        residuals["post_fit"] = True
        residuals["num_epochs"] = n_ep
        return {"x": x, "Q": Q, "residuals": residuals,
                "n_obs": int(sum(v.size for v in v_all))}

    def _float_fallback(self, fl: Dict[str, Any], threshold: float,
                        reason: str, detail: str, error_budget: Any,
                        fix: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """固定失败：输出 float 解并明确标注「未固定」。"""
        x = np.asarray(fl["x"][:3], dtype=float)
        lat, lon, h = CoordinateConverter.ecef_to_llh(*x)
        w = np.asarray(fl["wavelengths"], dtype=float)
        Q = np.asarray(fl["Q"], dtype=float)
        std_pos = np.sqrt(np.clip(np.diag(Q)[:3], 0.0, None))
        std_amb = np.sqrt(np.clip(np.diag(Q[3:, 3:]) / (w * w), 0.0, None))
        fix = fix or {}
        max_std = float(np.max(std_pos)) if std_pos.size else float("inf")
        if not np.isfinite(max_std) or max_std > 5.0:
            note = ("未固定：回退 float 解；几何/观测不足，"
                    "float 精度不可保证（不得宣称厘米级）")
        else:
            note = ("未固定：回退 float 解；float 为分米级精度"
                    "（视几何与噪声），不得宣称厘米级")
        return {
            "ok": False,
            "fix_type": "FLOAT",
            "rtk_fix_type": "RTK_FLOAT",
            "fixed": False,
            "lat_deg": float(lat * R2D),
            "lon_deg": float(lon * R2D),
            "alt": float(h),
            "x": float(x[0]), "y": float(x[1]), "z": float(x[2]),
            "num_sats": int(fl["num_sats"]),
            "num_ambiguities": int(fl["num_ambiguities"]),
            "hdop": float(fl["hdop"]),
            "integers": None,
            "float_ambiguities_cycles": [
                float(v) for v in np.asarray(fl["x"][3:]) / w],
            "ambiguity_keys": [list(k) for k in fl["ambiguity_keys"]],
            "ratio": fix.get("ratio"),
            "ratio_kind": fix.get("ratio_kind"),
            "num_candidates": int(fix.get("num_candidates", 0)),
            "best_distance": fix.get("best_distance"),
            "second_distance": fix.get("second_distance"),
            "ratio_threshold": float(threshold),
            "residuals": fl["residuals"],
            "covariance": {
                "position_std_m": [float(v) for v in std_pos],
                "ambiguity_std_cycles": [float(v) for v in std_amb],
                "source": "float_batch_wls",
            },
            "failure_reason": reason,
            "failure_detail": detail,
            "label": "未固定",
            "note": note,
            "error_budget": error_budget,
        }


# ============================================================
# LAMBDA / ILS 辅助实现（模块级，无第三方依赖）
# ============================================================
class _SearchBudgetExceeded(RuntimeError):
    """整数搜索节点数超出预算（协方差过大 / 观测不足）。"""


def _fix_failure(threshold: float, reason: str, detail: str) -> Dict[str, Any]:
    """``fix_ambiguities`` 的统一失败信封（诚实标注未固定）。"""
    return {
        "ok": False,
        "fixed": False,
        "fix_type": "FLOAT",
        "integers": None,
        "float": [],
        "ratio": None,
        "ratio_kind": None,
        "ratio_threshold": float(threshold),
        "num_candidates": 0,
        "best_distance": None,
        "second_distance": None,
        "covariance": None,
        "failure_reason": reason,
        "failure_detail": detail,
        "note": "未固定：输入或协方差不满足整数固定条件",
    }


def _normalize_epochs(inp) -> Optional[List[RTKInput]]:
    """把 ``solve_fixed`` 的输入统一成历元列表；非法返回 None。"""
    if isinstance(inp, RTKInput):
        return [inp]
    if isinstance(inp, (list, tuple)) and len(inp) > 0:
        if all(isinstance(ep, RTKInput) for ep in inp):
            return list(inp)
    return None


def _ambiguity_sigma(Q) -> List[float]:
    """协方差对角线的标准差列表；形状非法时返回空列表。"""
    Q = np.asarray(Q, dtype=float)
    if Q.ndim != 2 or Q.shape[0] != Q.shape[1]:
        return []
    diag = np.diag(Q)
    return [float(v) for v in np.sqrt(np.clip(diag, 0.0, None))]


def _residual_summary(v: np.ndarray) -> Dict[str, Any]:
    """残差摘要：相位/伪距分列 RMS（行顺序为 伪距, 相位 交替）。"""
    v = np.asarray(v, dtype=float)
    if v.size == 0:
        return {"rms_m": 0.0, "phase_rms_m": 0.0, "code_rms_m": 0.0,
                "n_obs": 0}
    code = v[0::2]
    phase = v[1::2]
    return {
        "rms_m": float(np.sqrt(np.mean(v ** 2))),
        "phase_rms_m": float(np.sqrt(np.mean(phase ** 2))) if phase.size else 0.0,
        "code_rms_m": float(np.sqrt(np.mean(code ** 2))) if code.size else 0.0,
        "n_obs": int(v.size),
    }


def _ldl_decompose(Q: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """对称正定矩阵的 LDL^T 分解（L 单位下三角，D 对角）。

    失败（非正定 / 非有限）抛 ``ValueError``，由调用方转成结构化失败。
    """
    n = Q.shape[0]
    L = np.eye(n)
    D = np.zeros(n)
    for i in range(n):
        d = Q[i, i] - math.fsum(L[i, k] ** 2 * D[k] for k in range(i))
        if not math.isfinite(d) or d <= 0.0:
            raise ValueError("covariance is not positive definite")
        D[i] = d
        for j in range(i + 1, n):
            num = Q[j, i] - math.fsum(
                L[j, k] * L[i, k] * D[k] for k in range(i))
            L[j, i] = num / d
    return L, D


def _lll_reduce(Q: np.ndarray, delta: float = 0.75
                ) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """LAMBDA 降相关：对协方差做整数 Gauss（LLL）变换。

    输入 SPD 协方差 ``Q``，返回 ``(Qz, Z)``，满足
    ``Qz = Z Q Z^T``、``z = Z a``，``Z`` 为幺模整数阵（行列式 ±1）。
    失败返回 ``None``（协方差非正定或迭代未收敛），不抛异常。

    复杂度 O(n^4 log B) 量级（n 为模糊度个数，B 为条件数），
    n <= 12 时可直接用于在线解算。
    """
    Q = np.asarray(Q, dtype=float)
    n = Q.shape[0]
    if n <= 1:
        return Q.copy(), np.eye(n, dtype=np.int64)
    try:
        chol = np.linalg.cholesky(Q)
    except np.linalg.LinAlgError:
        return None
    # Q = B^T B，B 的行是格基；LLL 对行做整数变换
    B = chol.T.copy()
    Z = np.eye(n, dtype=np.int64)

    def gram_schmidt():
        Bs = np.zeros_like(B)
        mu = np.zeros((n, n))
        norm = np.zeros(n)
        for i in range(n):
            v = B[i].copy()
            for j in range(i):
                if norm[j] <= 0.0:
                    return None
                mu[i, j] = float(B[i] @ Bs[j] / norm[j])
                v -= mu[i, j] * Bs[j]
            Bs[i] = v
            norm[i] = float(v @ v)
            if norm[i] <= 0.0:
                return None
        return mu, norm

    k = 1
    iterations = 0
    max_iterations = 200 * n * n
    while k < n:
        iterations += 1
        if iterations > max_iterations:
            return None
        gs = gram_schmidt()
        if gs is None:
            return None
        mu, _ = gs
        # 尺寸约化：把 L[k, j] 的整数部分挪进格基
        for j in range(k - 1, -1, -1):
            gs = gram_schmidt()
            if gs is None:
                return None
            mu, _ = gs
            q = int(round(mu[k, j]))
            if q:
                B[k] -= q * B[j]
                Z[k] -= q * Z[j]
        gs = gram_schmidt()
        if gs is None:
            return None
        mu, norm = gs
        # Lovász 条件
        if norm[k] >= (delta - mu[k, k - 1] ** 2) * norm[k - 1]:
            k += 1
        else:
            B[[k, k - 1]] = B[[k - 1, k]]
            Z[[k, k - 1]] = Z[[k - 1, k]]
            k = max(k - 1, 1)

    Zf = Z.astype(float)
    Qz = Zf @ Q @ Zf.T
    return 0.5 * (Qz + Qz.T), Z


class _IntegerGrid:
    """降相关后的整数最小二乘深搜（Teunissen 条件方差递归）。

    目标函数（与 Z 变换前的二次型等价）::

        f(z) = sum_i ( z_i - zf_i + sum_{j>i} L[j,i] (z_j - zf_j) )^2 / D_i

    ``enumerate(bound)`` 完整枚举 ``f(z) <= bound`` 的所有整数点；
    节点数超预算抛 ``_SearchBudgetExceeded``，由调用方转成失败。
    """

    def __init__(self, zf: np.ndarray, L: np.ndarray, D: np.ndarray,
                 node_budget: int):
        self.zf = np.asarray(zf, dtype=float)
        self.L = np.asarray(L, dtype=float)
        self.D = np.asarray(D, dtype=float)
        self.n = int(self.zf.size)
        self.node_budget = int(node_budget)
        self.nodes = 0
        self._z = np.zeros(self.n, dtype=np.int64)

    def distance(self, z) -> float:
        z = np.asarray(z, dtype=float)
        total = 0.0
        for i in range(self.n - 1, -1, -1):
            s = math.fsum(self.L[j, i] * (z[j] - self.zf[j])
                          for j in range(i + 1, self.n))
            total += (z[i] - self.zf[i] + s) ** 2 / self.D[i]
        return float(total)

    def bootstrap(self) -> np.ndarray:
        """顺序取整的自举候选（作为初始最优与初始界）。"""
        z = np.zeros(self.n, dtype=np.int64)
        for i in range(self.n - 1, -1, -1):
            s = math.fsum(self.L[j, i] * (z[j] - self.zf[j])
                          for j in range(i + 1, self.n))
            z[i] = int(round(self.zf[i] - s))
        return z

    def enumerate(self, bound: float) -> List[Tuple[float, np.ndarray]]:
        out: List[Tuple[float, np.ndarray]] = []
        z = self._z
        z[:] = 0

        def rec(i: int, partial: float) -> None:
            self.nodes += 1
            if self.nodes > self.node_budget:
                raise _SearchBudgetExceeded(
                    "integer search node budget exceeded")
            if i < 0:
                out.append((float(partial), z.copy()))
                return
            s = math.fsum(self.L[j, i] * (z[j] - self.zf[j])
                          for j in range(i + 1, self.n))
            center = self.zf[i] - s
            rem = bound - partial
            if rem <= 0.0:
                return
            radius = math.sqrt(rem * self.D[i])
            lo = int(math.ceil(center - radius))
            hi = int(math.floor(center + radius))
            span = hi - lo + 1
            if span <= 0:
                return
            if span > self.node_budget - self.nodes:
                raise _SearchBudgetExceeded(
                    "integer search span too wide")
            # 近邻优先，便于尽早收紧候选
            values = sorted(range(lo, hi + 1), key=lambda v: abs(v - center))
            for zi in values:
                z[i] = zi
                rec(i - 1, partial + (zi - center) ** 2 / self.D[i])

        rec(self.n - 1, 0.0)
        return out


def _lambda_integer_search(a_cycles, Q_cycles, ratio_threshold: float,
                           max_candidates: int = 2,
                           node_budget: int = DEFAULT_SEARCH_NODE_BUDGET
                           ) -> Dict[str, Any]:
    """LAMBDA/ILS 整数搜索核心（输入单位：周）。

    步骤：正定性检查 → LLL 降相关 → LDL^T → 整点深搜（近邻优先，
    搜索椭球仅在「候选不足」时按 ×4 扩张）→ 反变换回原模糊度。

    ratio 口径：次优/最优候选的目标函数值之比。若搜索椭球扩张到上限仍
    只有一个候选，则把 ``ratio_kind`` 标为 ``lower_bound``，``ratio`` 取
    「最终搜索界 / 最优值」这一**保守下界**——它是可复算的事实（界内确实
    没有竞争候选），不是伪造的实测比值。
    """
    a = np.asarray(a_cycles, dtype=float).ravel()
    Q = np.asarray(Q_cycles, dtype=float)
    n = int(a.size)
    out: Dict[str, Any] = {
        "ok": False, "fixed": False, "integers": None,
        "float": [float(v) for v in a],
        "ratio": None, "ratio_kind": None, "num_candidates": 0,
        "best_distance": None, "second_distance": None,
        "failure_reason": None, "failure_detail": None,
    }
    if n < 1:
        out["failure_reason"] = "insufficient_ambiguities"
        out["failure_detail"] = "没有可固定的浮点模糊度"
        return out
    if math.isnan(float(ratio_threshold)):
        out["failure_reason"] = "invalid_ratio_threshold"
        out["failure_detail"] = "ratio 阈值为 NaN，固定判据无意义"
        return out
    if Q.shape != (n, n):
        out["failure_reason"] = "covariance_shape_mismatch"
        out["failure_detail"] = (
            f"协方差形状 {Q.shape} 与模糊度个数 {n} 不匹配")
        return out
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(Q))):
        out["failure_reason"] = "invalid_input"
        out["failure_detail"] = "浮点模糊度或协方差含 NaN/Inf"
        return out
    Q = 0.5 * (Q + Q.T)
    try:
        min_eig = float(np.linalg.eigvalsh(Q)[0])
    except np.linalg.LinAlgError:
        min_eig = -1.0
    if not math.isfinite(min_eig) or min_eig <= 0.0:
        out["failure_reason"] = "covariance_not_positive_definite"
        out["failure_detail"] = "模糊度协方差非正定，整数最小二乘无定义"
        return out

    max_candidates = max(2, int(max_candidates))
    reduced = _lll_reduce(Q)
    if reduced is None:
        out["failure_reason"] = "decorrelation_failed"
        out["failure_detail"] = "LAMBDA 降相关失败（协方差数值病态）"
        return out
    Qz, Z = reduced
    try:
        L, D = _ldl_decompose(Qz)
    except ValueError:
        out["failure_reason"] = "covariance_not_positive_definite"
        out["failure_detail"] = "降相关后协方差非正定，整数搜索不可用"
        return out

    zf = Z.astype(float) @ a
    grid = _IntegerGrid(zf, L, D, node_budget)
    z_boot = grid.bootstrap()
    d_boot = grid.distance(z_boot)
    # 初始搜索界：自举候选附近，至少 1.0（χ² 量级经验下限）
    bound = max(d_boot * 1.01, 1.0)
    if not math.isfinite(bound):
        out["failure_reason"] = "covariance_ill_conditioned"
        out["failure_detail"] = "模糊度协方差病态，搜索界非有限，回退 float"
        return out
    candidates: List[Tuple[float, np.ndarray]] = []
    expansions = 0
    try:
        while True:
            candidates = grid.enumerate(bound)
            if len(candidates) >= max_candidates:
                break
            if expansions >= _MAX_BOUND_EXPANSIONS:
                break
            bound *= 4.0
            expansions += 1
    except _SearchBudgetExceeded:
        out["failure_reason"] = "search_budget_exceeded"
        out["failure_detail"] = (
            f"整数搜索节点数超过预算 {node_budget}；"
            "模糊度协方差过大或观测不足，无法在预算内确证固定")
        out["num_candidates"] = len(candidates)
        return out

    if not candidates:
        out["failure_reason"] = "no_integer_candidate"
        out["failure_detail"] = "整数搜索未找到任何候选"
        return out

    candidates.sort(key=lambda item: item[0])
    d1 = float(candidates[0][0])
    z_best = candidates[0][1]
    if len(candidates) >= 2:
        d2: Optional[float] = float(candidates[1][0])
        ratio = float("inf") if d1 <= 0.0 else d2 / d1
        ratio_kind = "exact"
    else:
        d2 = None
        ratio = float("inf") if d1 <= 0.0 else bound / d1
        ratio_kind = "lower_bound"

    # 幺模反变换：a_int = Z^{-1} z_best（必须是整数）
    try:
        raw = np.linalg.solve(Z.astype(float), z_best.astype(float))
    except np.linalg.LinAlgError:
        out["failure_reason"] = "integer_back_transform_failed"
        out["failure_detail"] = "降相关矩阵奇异，无法还原整数模糊度"
        return out
    ints = np.rint(raw).astype(np.int64)
    if (float(np.max(np.abs(raw - ints))) > 1e-6
            or not np.array_equal(Z @ ints, z_best)):
        out["failure_reason"] = "integer_back_transform_failed"
        out["failure_detail"] = "降相关反变换不是整数/自洽校验失败"
        return out

    out.update({
        "integers": [int(v) for v in ints],
        "ratio": ratio,
        "ratio_kind": ratio_kind,
        "num_candidates": len(candidates),
        "best_distance": d1,
        "second_distance": d2,
    })
    if ratio < float(ratio_threshold):
        out["failure_reason"] = "ratio_below_threshold"
        out["failure_detail"] = (
            f"ratio={ratio:.3f} < 阈值 {float(ratio_threshold):.3f}，"
            "判定整数固定不可信")
        return out
    out["fixed"] = True
    out["ok"] = True
    return out

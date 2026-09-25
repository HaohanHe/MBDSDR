#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtk_solver.py — RTK float 解算接口（移植自 RTKLIB src/rtkpos.c）

本模块提供 RTK 浮点解（float solution）骨架：
  - 双差观测组合（参考 rtkpos.c:1022 ddres()）
  - 扩展卡尔曼滤波：状态 = [位置(3), 接收机钟差, 对流层湿延迟, 各双差模糊度]
  - 至少能跑通 float 解（位置输出）
  - LAMBDA 固定解标注 TODO 并留接口

注意：本实现聚焦「接口可调用 + 残差/位置估计正确」，不追求工程级精度。
所有移植处标注「来源: RTKLIB src/<file>.c:<line>」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .rtklib_adapter import (
    CLIGHT, CoordinateConverter, R2D, PI,
)


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

    状态向量（rtkpos.c: rtk->x）：
        x[0:3]  = 流动站 ECEF 位置
        x[3]    = 流动站接收机钟差(m)
        x[4]    = 对流层湿延迟(ZWD, m)
        x[5:]   = 各双差模糊度(m)

    双差残差（rtkpos.c:1081-1082）：
        v = (P_rov_i - P_base_i) - (P_rov_j - P_base_j)
    H 行（rtkpos.c:1086-1088）：
        H[k] = -e_rov_i[k] + e_rov_j[k]   for k=0,1,2
    """

    def __init__(self, max_iter: int = 10, conv_tol: float = 1e-3,
                 sigma_pos: float = 1.0, sigma_amb: float = 30.0,
                 sigma_tropo: float = 0.3):
        self.max_iter = max_iter
        self.conv_tol = conv_tol
        self.sigma_pos = sigma_pos
        self.sigma_amb = sigma_amb
        self.sigma_tropo = sigma_tropo
        # 状态与协方差（首次调用时初始化）
        self.x: Optional[np.ndarray] = None
        self.P: Optional[np.ndarray] = None
        self._amb_index: Dict[Tuple[int, int], int] = {}  # (prn_i, prn_j) -> state idx

    # ----------------------------------------------------------
    # 选择参考卫星（仰角最高）—— rtkpos.c:1058-1063
    # ----------------------------------------------------------
    @staticmethod
    def _select_ref_sat(obs: List[RTKObs], rr: np.ndarray) -> int:
        """选择仰角最高的卫星作为参考星。rtkpos.c:1058-1062。"""
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
    # 双差残差与设计矩阵 —— rtkpos.c:1022 ddres()
    # ----------------------------------------------------------
    def _build_dd_equations(self, inp: RTKInput,
                            x: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """构造双差残差 v、设计矩阵 H、权阵 R。

        返回 (v, H, R_diag)。
        简化：只处理共视卫星的伪距双差 + 载波相位双差（同频）。
        不做电离层/对流层细分估计，对流层只在状态里加一个 ZWD。
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

            # ---- 伪距双差残差 (rtkpos.c:1081-1082) ----
            sd_pr_i_ = ref.pseudorange - ref_base.pseudorange
            sd_pr_j_ = o.pseudorange - ob.pseudorange
            pred_pr = (r_rov_i - r_base_i) - (r_rov_j - r_base_j)
            v_pr = (sd_pr_i_ - sd_pr_j_) - pred_pr

            H_pr = np.zeros(nx)
            # rtkpos.c:1087  H[k] = -e_rov_i[k] + e_rov_j[k]
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
    # 卡尔曼滤波更新 —— rtkpos.c:557 kfupdate()
    # ----------------------------------------------------------
    def _kf_update(self, v: np.ndarray, H: np.ndarray, R_diag: np.ndarray) -> None:
        """线性卡尔曼更新：x = x + K v, P = (I-KH)P。

        移植 rtkpos.c:557 kfupdate() 的核心公式：
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
        """首次调用初始化状态。"""
        # 先用 SPP 近似位置（基站坐标 + 基线先验 0）
        x0 = np.array([
            inp.base_ecef[0], inp.base_ecef[1], inp.base_ecef[2],
            0.0,   # 钟差
            0.0,   # ZWD
        ])
        P0 = np.diag([self.sigma_pos ** 2] * 3 +
                     [1e4,                    # 钟差
                      self.sigma_tropo ** 2])  # ZWD
        self.x = x0
        self.P = P0
        self._amb_index = {}

    # ----------------------------------------------------------
    # 主入口
    # ----------------------------------------------------------
    def solve(self, inp: RTKInput) -> Dict[str, Any]:
        """执行一次 RTK float 解算。

        返回 dict: {lat, lon, alt, fix_type, num_sats, hdop, residuals}
        fix_type: "RTK_FLOAT"（成功）/ "NONE"（失败）
        LAMBDA 固定解 TODO: 留接口 fix_ambiguities()。
        """
        if self.x is None:
            self._initialize_state(inp)

        # 共视卫星数
        base_prns = {o.prn for o in inp.base_obs}
        rover_prns = {o.prn for o in inp.rover_obs}
        common = base_prns & rover_prns
        if len(common) < 4:
            return {"fix_type": "NONE", "num_sats": len(common),
                    "error": "insufficient common satellites"}

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
            "num_ambiguities": max(0, len(self.x) - 5),
            "note": "float only, fixed pending (LAMBDA TODO)",
        }

    # ----------------------------------------------------------
    # TODO: LAMBDA 固定解接口
    # ----------------------------------------------------------
    def fix_ambiguities(self) -> Dict[str, Any]:
        """LAMBDA 整数固定解 —— TODO 未实现。

        来源: RTKLIB src/lambda.c  lambda()。
        预留接口：后续可调用 lambda.c 的 LAMBDA 算法对 self.x[5:]
        的浮点模糊度做整数搜索，得到固定整周数后重算位置。
        """
        raise NotImplementedError(
            "LAMBDA fixed solution not implemented; see RTKLIB src/lambda.c")

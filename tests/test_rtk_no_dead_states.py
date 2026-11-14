# SPDX-License-Identifier: MIT
"""A3：RTK 状态向量里不许留「永远估不出来」的量。

旧版状态 = [位置(3), 接收机钟差 x[3], 湿延迟 x[4], 双差模糊度...]，但双差
观测方程既没有钟差列也没有湿延迟列——这两个状态在 H 中恒为零，KF 永远
不会更新它们，只是占位。本测试锁定修复后的结构：

  1. 状态数 = 3 + 双差模糊度数，不再有钟差/湿延迟；
  2. 每一个状态列在设计矩阵 H 中都有非零元（可观测）；
  3. 一次解算后每个状态的后验方差都小于先验方差（真的被估计过）。
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

from mbdsdr_ai.geodesy import CoordinateConverter  # noqa: E402
from mbdsdr_ai.rtk_solver import RTKFloatSolver, RTKInput, RTKObs  # noqa: E402


def _make_input():
    lat, lon, h = math.radians(39.9), math.radians(116.4), 50.0
    base_ecef = np.array(CoordinateConverter.llh_to_ecef_m(lat, lon, h))
    rover_ecef = np.array(CoordinateConverter.llh_to_ecef_m(
        lat + 0.001, lon, h))
    sats = [(45, 0), (45, 90), (45, 180), (45, -90), (10, 45), (10, 225)]
    base_obs, rover_obs = [], []
    for k, (slat, slon) in enumerate(sats, start=1):
        rs = np.array(CoordinateConverter.llh_to_ecef_m(
            math.radians(slat), math.radians(slon), 26500000.0))
        r_b = float(np.linalg.norm(rs - base_ecef))
        r_r = float(np.linalg.norm(rs - rover_ecef))
        base_obs.append(RTKObs(prn=k, rs=rs, pseudorange=r_b,
                               carrier_phase=r_b))
        rover_obs.append(RTKObs(prn=k, rs=rs, pseudorange=r_r,
                                carrier_phase=r_r))
    return RTKInput(base_ecef=base_ecef, base_obs=base_obs,
                    rover_obs=rover_obs)


def test_state_layout_has_only_position_and_ambiguities():
    solver = RTKFloatSolver()
    inp = _make_input()
    solver._initialize_state(inp)
    # 初始状态只有位置 3 维：没有钟差、没有 ZWD 占位
    assert len(solver.x) == 3
    assert solver.P.shape == (3, 3)

    v, H, _ = solver._build_dd_equations(inp, solver.x)
    assert H.shape[1] == len(solver.x) == 3 + len(solver._amb_index)
    assert len(solver._amb_index) == 5  # 6 颗共视星 → 5 个双差对
    assert len(v) == 10  # 每个双差对：伪距 + 相位各一行


def test_every_state_column_is_observable_in_design_matrix():
    solver = RTKFloatSolver()
    inp = _make_input()
    solver._initialize_state(inp)
    _, H, _ = solver._build_dd_equations(inp, solver.x)
    for j in range(H.shape[1]):
        assert np.any(np.abs(H[:, j]) > 1e-12), \
            f"状态 x[{j}] 在设计矩阵中恒为零：这是永远估不出来的假状态"


def test_每个状态的后验方差都小于先验():
    solver = RTKFloatSolver()
    inp = _make_input()
    solver._initialize_state(inp)
    prior_diag = dict(enumerate(np.diag(solver.P).copy()))
    sol = solver.solve(inp)

    assert sol["fix_type"] == "RTK_FLOAT"
    assert sol["num_ambiguities"] == len(solver._amb_index) == 5
    assert sol["num_ambiguities"] == len(solver.x) - 3

    for j in range(len(solver.x)):
        prior = prior_diag.get(j, solver.sigma_amb ** 2)
        assert solver.P[j, j] < prior + 1e-9, \
            f"状态 x[{j}] 后验方差未下降（先验 {prior}，后验 {solver.P[j, j]}）"


def test_source_has_no_tropo_state_leftover():
    """反证：源码里不许再出现 sigma_tropo / ZWD 状态初始化。"""
    path = os.path.join(_ROOT, "mbdsdr_ai", "rtk_solver.py")
    src = open(path, encoding="utf-8").read()
    assert "sigma_tropo" not in src
    assert "self.x = np.append(self.x, 0.0)" in src  # 只有模糊度会追加状态

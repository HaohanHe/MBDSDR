# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""Task 27：RTK 固定解（LAMBDA / 整数最小二乘）行为测试。

**全部为合成数据**：这里没有真实接收机、没有真实双差观测。合成数据能验证
接口语义、整数搜索正确性与内部一致性，**不能**证明真实空口场景的厘米级
精度；真实精度需要实测基线与观测，本文件不作此声明。
"""
import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.geodesy import CoordinateConverter  # noqa: E402
from mbdsdr_ai.rtk_solver import (  # noqa: E402
    DEFAULT_RATIO_THRESHOLD, RTKFloatSolver, RTKInput, RTKObs,
)


# ---------------------------------------------------------------------------
# LAMBDA 核心：整数正确性 / 失败回退 / ratio 阈值
# ---------------------------------------------------------------------------
def test_lambda_fixes_known_integer_ambiguities():
    """已知整数真值 + 对角协方差：必须固定成功且整数逐个正确。"""
    truth = np.array([12, -3, 7, 1, 20, -9, 4, 2], dtype=int)
    noise = np.array([0.02, -0.01, 0.03, 0.04, -0.02, 0.05, -0.03, 0.01])
    Q = np.eye(8) * 0.04  # 标准差 0.2 周

    out = RTKFloatSolver().fix_ambiguities(truth + noise, Q)

    assert out["fix_type"] == "FIXED"
    assert out["fixed"] is True
    assert out["ok"] is True
    assert out["integers"] == truth.tolist()
    assert out["ratio"] > DEFAULT_RATIO_THRESHOLD
    assert out["ratio_kind"] in ("exact", "lower_bound")
    assert out["failure_reason"] is None
    assert len(out["covariance"]["sigma_cycles"]) == 8
    assert "已固定" in out["note"]


def test_large_covariance_rejects_fix_and_reports_float():
    """协方差过大（模糊度几乎无信息）：搜索超预算，必须回退 float。"""
    out = RTKFloatSolver().fix_ambiguities([0.1, 0.2], np.eye(2) * 1e6)

    assert out["fixed"] is False
    assert out["fix_type"] == "FLOAT"
    assert out["integers"] is None
    assert out["failure_reason"] == "search_budget_exceeded"
    assert "未固定" in out["note"]


def test_non_positive_definite_covariance_rejected():
    """矛盾协方差（非正定）：不许硬算，结构化失败。"""
    Q = np.array([[1.0, 2.0], [2.0, 0.5]])
    out = RTKFloatSolver().fix_ambiguities([0.2, 0.3], Q)

    assert out["fixed"] is False
    assert out["fix_type"] == "FLOAT"
    assert out["failure_reason"] == "covariance_not_positive_definite"


def test_contradictory_covariance_yields_ratio_below_threshold():
    """浮点解正好落在两个整数中间且方差极小：两候选等距，ratio=1 必须拒绝。"""
    out = RTKFloatSolver().fix_ambiguities([0.5, 0.5], np.eye(2) * 1e-6)

    assert out["fixed"] is False
    assert out["failure_reason"] == "ratio_below_threshold"
    assert out["ratio"] == pytest.approx(1.0, abs=1e-6)


def test_ratio_threshold_boundary():
    """ratio 阈值边界：阈值低于 ratio 通过，高于 ratio 拒绝。"""
    amb = [0.5, 0.5]
    Q = np.eye(2)  # 四个候选等距，(0,0)/(1,1) 最近，ratio 恰为 1

    loose = RTKFloatSolver().fix_ambiguities(
        amb, Q, ratio_threshold=0.99)
    strict = RTKFloatSolver().fix_ambiguities(
        amb, Q, ratio_threshold=1.01)

    assert loose["ratio"] == pytest.approx(1.0, abs=1e-9)
    assert loose["fixed"] is True
    assert strict["ratio"] == pytest.approx(1.0, abs=1e-9)
    assert strict["fixed"] is False
    assert strict["failure_reason"] == "ratio_below_threshold"


def test_insufficient_input_reports_float():
    """输入不足：空模糊度 / 协方差形状不符，必须结构化失败而非抛异常。"""
    empty = RTKFloatSolver().fix_ambiguities([], np.zeros((0, 0)))
    assert empty["fix_type"] == "FLOAT"
    assert empty["fixed"] is False
    assert empty["failure_reason"] == "insufficient_ambiguities"

    mismatch = RTKFloatSolver().fix_ambiguities([0.1, 0.2], np.eye(3))
    assert mismatch["fix_type"] == "FLOAT"
    assert mismatch["failure_reason"] == "covariance_shape_mismatch"


# ---------------------------------------------------------------------------
# 合成双差观测：已知基线
# ---------------------------------------------------------------------------
def _synthetic_epochs(n_sats=7, n_epochs=150, seed=20261117):
    """合成静态短基线多历元双差观测（**合成数据，不是实测**）。

    - 已知基线：ENU (12, -7, 3) m；
    - 已知整数模糊度（周），跨历元不变（无周跳）；
    - 伪距噪声 0.3 m、载波相位噪声 1 mm；
    - 卫星方位/仰角逐历元漂移（模拟几何变化），每历元 0.3° 方位，
      150 历元共 45°。真实 GPS 变化慢得多；这里只是为了让单频静态
      固定问题在合成数据上可解。
    """
    lat, lon, h = math.radians(39.9), math.radians(116.4), 50.0
    base = np.array(CoordinateConverter.llh_to_ecef_m(lat, lon, h))
    dx, dy, dz = CoordinateConverter.enu_to_ecef(lat, lon, 12.0, -7.0, 3.0)
    rover = base + np.array([dx, dy, dz])

    azim = [0, 72, 144, 216, 288, 45, 180][:n_sats]
    elevs = [75, 60, 45, 30, 20, 50, 35][:n_sats]
    wavelength = 0.1903  # GPS L1
    rng = np.random.default_rng(seed)
    true_amb = rng.integers(-20, 20, size=n_sats)

    epochs = []
    for ep in range(n_epochs):
        base_obs, rover_obs = [], []
        for k, (az, el) in enumerate(zip(azim, elevs), start=1):
            azr = math.radians(az + 0.3 * ep)
            elr = math.radians(el + 0.15 * ep)
            u_enu = np.array([
                math.sin(azr) * math.cos(elr),
                math.cos(azr) * math.cos(elr),
                math.sin(elr),
            ])
            u_ecef = np.array(CoordinateConverter.enu_to_ecef(
                lat, lon, *u_enu))
            rs = base + 26560000.0 * u_ecef
            r_base, _ = CoordinateConverter.geodist(rs, base)
            r_rover, _ = CoordinateConverter.geodist(rs, rover)
            base_obs.append(RTKObs(
                prn=k, rs=rs,
                pseudorange=r_base + rng.normal(0.0, 0.3),
                carrier_phase=r_base + rng.normal(0.0, 0.001),
                wavelength=wavelength))
            rover_obs.append(RTKObs(
                prn=k, rs=rs,
                pseudorange=r_rover + rng.normal(0.0, 0.3),
                carrier_phase=(r_rover + wavelength * true_amb[k - 1]
                               + rng.normal(0.0, 0.001)),
                wavelength=wavelength))
        epochs.append(RTKInput(
            base_ecef=base, base_obs=base_obs, rover_obs=rover_obs))
    return {"epochs": epochs, "rover": rover, "true_amb": true_amb}


def test_synthetic_known_baseline_fixed_position_is_cm_level():
    """合成已知基线：固定解整数正确、位置误差在厘米级。"""
    case = _synthetic_epochs()
    sol = RTKFloatSolver().solve_fixed(case["epochs"])

    assert sol["fix_type"] == "FIXED"
    assert sol["rtk_fix_type"] == "RTK_FIXED"
    assert sol["fixed"] is True
    assert sol["ratio"] > DEFAULT_RATIO_THRESHOLD
    assert sol["residuals"]["num_epochs"] == len(case["epochs"])
    assert sol["residuals"]["phase_rms_m"] < 0.01

    assert sol["integers"] is not None
    assert len(sol["integers"]) == len(sol["ambiguity_keys"])
    true_amb = case["true_amb"]
    for (ref_prn, oth_prn), got in zip(
            sol["ambiguity_keys"], sol["integers"]):
        assert got == int(true_amb[ref_prn - 1] - true_amb[oth_prn - 1])

    p_fixed = np.array([sol["x"], sol["y"], sol["z"]])
    err_m = float(np.linalg.norm(p_fixed - case["rover"]))
    assert err_m < 0.05, f"合成数据固定解位置误差 {err_m * 100:.2f} cm"

    assert len(sol["covariance"]["position_std_m"]) == 3
    assert sol["failure_reason"] is None
    assert "已固定" in sol["label"]


def test_solve_fixed_falls_back_when_satellites_insufficient():
    """共视星 < 5：不尝试固定，回退 float 并明确标注「未固定」。"""
    case = _synthetic_epochs(n_sats=4, n_epochs=20)
    sol = RTKFloatSolver().solve_fixed(case["epochs"])

    assert sol["fixed"] is False
    assert sol["fix_type"] == "FLOAT"
    assert sol["integers"] is None
    assert sol["failure_reason"] == "insufficient_satellites_for_fix"
    assert "未固定" in sol["note"]
    assert "未固定" in sol["label"]
    assert sol["covariance"]["source"] == "float_batch_wls"

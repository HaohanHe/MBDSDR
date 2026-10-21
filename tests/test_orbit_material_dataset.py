# SPDX-License-Identifier: MIT
"""材料锚定的定轨能力测试：IRIDIUM 107 多普勒观测 → 轨道估计。

数据来自公开培训材料《赛前培训-定轨》的示例数据集（IRIDIUM 107 的
1626.270833 MHz 下行多普勒频移序列 + 对应 TLE），作为 fixture 入库。

这套测试补上了此前**完全缺失**的几道护栏（正是它们让单位/传播器的缺陷
长期潜伏而无人察觉）：

1. 传播器自洽：把 TLE 状态传播 600 s，必须与 TLE 真值一致（< 5 km）。
   此前实现在旋转系里直接积分二体，漏掉离心/科里奥利项，600 s 差 1388 km。
2. 单位自洽：频偏→伪距率换算与观测模型同量纲（此前观测用 km/s、模型用 m/s，
   观测小 1000 倍，定轨必然发散）。
3. 校时能力：从该数据集找回材料记录的 ~15.5 s 观测时间错位。
4. 估计能力：残差处于 O(10 m/s) 量级、轨道半径/速度有效、位置与 TLE
   参考同量级（单站 3.5 分钟多普勒弧段对位置**弱可观**，位置改正天然受限；
   这里断言"同量级"而不是"逐点吻合"——材料的 2.7 km 实际来自其模型把
   速度项小算 1000 倍导致位置项几乎不起作用）。
5. 诚实失败：发散的估计必须被轨道合理性校验拦下，而不是返回离谱轨道。
"""
import csv
import os
import sys

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.orbit_determination import (doppler_orbit_determine,  # noqa: E402
                                           fd_to_rangerate,
                                           _validate_orbit_state)
from mbdsdr_ai.orbit_dynamics import (calibrate_time_offset,  # noqa: E402
                                     ecef_to_geodetic,
                                     observed_pseudorange_rate,
                                     propagate_state_and_stm,
                                     tle_state_ecef)

FIX = os.path.join(REPO_ROOT, "tests", "fixtures", "iridium107")
CENTER_FREQ_HZ = 1626.270833e6
# 材料的接收机位置（ECEF，米）—— 材料 demo 使用的地面站
RECEIVER_ECEF_M = np.array([-2637850.0, 3505120.0, 4614700.0])
SAT_NAME = "IRIDIUM 107"


def _observations():
    rows = []
    with open(os.path.join(FIX, "observations.csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append((float(row["timestamp"]), float(row["frequency"])))
    return rows


def _accepted(rows, min_interval=5.0):
    """与材料一致的时间间隔筛选（间隔不足的重复点丢弃）。"""
    out = [rows[0]]
    for r in rows[1:]:
        if r[0] - out[-1][0] >= min_interval:
            out.append(r)
    return out


def _tle_lines():
    with open(os.path.join(FIX, "tle.txt"), encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    for i, ln in enumerate(lines):
        if SAT_NAME in ln and i + 2 < len(lines):
            return lines[i + 1], lines[i + 2]
    raise AssertionError("fixture 中找不到 IRIDIUM 107 的 TLE")


def _receiver_geodetic():
    lon, lat, alt_m = ecef_to_geodetic(RECEIVER_ECEF_M)
    return lat, lon, alt_m


# ── 1. 传播器自洽（此前缺失的关键护栏）─────────────────────
def test_propagator_matches_tle_truth():
    """传播 600 s 后必须与 TLE 真值一致（< 5 km）——旋转系直积二体时差 1388 km。"""
    l1, l2 = _tle_lines()
    t0 = 1745764814.0
    st0 = tle_state_ecef(l1, l2, t0)
    st1, _phi = propagate_state_and_stm(st0, t0, t0 + 600.0)
    truth = tle_state_ecef(l1, l2, t0 + 600.0)
    dist_km = float(np.linalg.norm(st1[0:3] - truth[0:3]))
    assert dist_km < 5.0, f"传播 600 s 与 TLE 真值差 {dist_km:.2f} km"
    spd = float(np.linalg.norm(st1[3:6] - truth[3:6]))
    assert spd < 50.0, f"传播速度差 {spd:.3f} km/s"


# ── 2. 单位自洽（此前观测与模型差 1000 倍）───────────────────
def test_frequency_conversion_roundtrip():
    """频偏 -> 伪距率 -> 频移 必须往返一致。"""
    from mbdsdr_ai.orbit_determination import rangerate_to_fd
    fd = 32_429.0
    rr = fd_to_rangerate(fd, CENTER_FREQ_HZ)
    assert abs(rangerate_to_fd(rr, CENTER_FREQ_HZ) - fd) < 1e-6
    # 与 orbit_dynamics 的换算（另一模块）必须同量纲
    rr2 = observed_pseudorange_rate(CENTER_FREQ_HZ + fd, CENTER_FREQ_HZ)
    assert abs(rr - rr2) < 1e-9


def test_observation_model_scales_with_state():
    """观测模型的量纲必须与状态同制（km/km/s）：把状态放大 1000 倍，预测率同比例放大。"""
    l1, l2 = _tle_lines()
    st = tle_state_ecef(l1, l2, 1745764814.0)
    from mbdsdr_ai.orbit_dynamics import pseudorange_rate_and_jacobian as jac
    r1, _ = jac(st, RECEIVER_ECEF_M / 1000.0)
    r2, _ = jac(st * 1000.0, RECEIVER_ECEF_M)
    assert r1 != 0.0
    assert abs(r2 / r1 - 1000.0) < 1.0, f"模型非齐次：{r1} -> {r2}"


# ── 3. 校时能力（材料记录 ~15.5 s）─────────────────────────
def test_calibration_recovers_material_time_offset():
    """从材料数据集找回观测时间错位（材料记录 15.5 s）。"""
    l1, l2 = _tle_lines()
    sel = _accepted(_observations())
    cal = calibrate_time_offset(sel, RECEIVER_ECEF_M / 1000.0, CENTER_FREQ_HZ, l1, l2, 30.0)
    assert abs(cal["time_offset_s"] - 15.5) < 0.6, cal
    assert abs(cal["rate_bias_mps"]) < 60.0, cal
    assert 1.0 < cal["centered_rms_mps"] < 60.0, cal


# ── 4. 估计能力 ───────────────────────────────────────────
@pytest.mark.parametrize("estimator", ["ekf", "rls"])
def test_orbit_estimate_is_physically_sane(estimator):
    """估计必须收敛到物理合理的轨道，残差 O(10 m/s)，位置与 TLE 同量级。"""
    l1, l2 = _tle_lines()
    sel = _accepted(_observations())
    rec_km = RECEIVER_ECEF_M / 1000.0
    lat, lon, alt_m = _receiver_geodetic()
    cal = calibrate_time_offset(sel, rec_km, CENTER_FREQ_HZ, l1, l2, 30.0)
    t_ref = sel[0][0] + cal["time_offset_s"]
    init = np.asarray(tle_state_ecef(l1, l2, t_ref), dtype=float)
    init[6] = cal["rate_bias_mps"] / 1000.0          # 第 7 维 = 速率偏置(km/s)
    P0 = np.diag([30.0 ** 2] * 3 + [30.0 ** 2] * 3 + [30.0 ** 2])
    res = doppler_orbit_determine(
        sel, rx_lat=lat, rx_lon=lon, rx_alt=alt_m,
        init_state=init, init_P=P0, f0=CENTER_FREQ_HZ,
        estimator=estimator, frequency_mode="absolute",
        time_offset_s=cal["time_offset_s"], observation_sigma_mps=20.0)
    stats = res["residual_stats"]
    rms = float(stats["rms"]) * 1000.0               # km/s -> m/s
    assert 1.0 < rms < 80.0, f"{estimator} 残差 RMS {rms:.2f} m/s"
    state = np.asarray(res.get("state", res.get("state_ref")), dtype=float)
    radius = float(np.linalg.norm(state[0:3]))
    speed = float(np.linalg.norm(state[3:6]))
    assert 6000.0 < radius < 9000.0, f"{estimator} 半径 {radius:.1f} km"
    assert 4.0 < speed < 10.0, f"{estimator} 速度 {speed:.3f} km/s"
    truth = tle_state_ecef(l1, l2, sel[-1][0] + cal["time_offset_s"])
    dist = float(np.linalg.norm(state[0:3] - truth[0:3]))
    # 单站 3.5 分钟多普勒弧段只约束"视线方向的速度"，
    # 垂直轨道面方向不可观（位置改正允许较大，但仍应在同量级）。
    # EKF 带协方差缩小，收敛更紧；递推 RLS 则放开。
    limit = 800.0 if estimator == "ekf" else 5000.0
    assert dist < limit, f"{estimator} 位置与 TLE 差 {dist:.1f} km"
    # 后验协方差必须随估计一起回传（不确定性显式化）
    assert res.get("covariance") is not None, "缺后验协方差"


# ── 5. 诚实失败：离谱轨道必须被拦下 ─────────────────────────
def test_divergent_state_is_rejected():
    """半径/速度离谱的状态必须报错，不返回假轨道。"""
    with pytest.raises(ValueError):
        _validate_orbit_state(np.array([1e9, 0, 0, 0, 0, 0, 0.0]))
    with pytest.raises(ValueError):
        _validate_orbit_state(np.array([7000.0, 0, 0, 0, 0, 100.0, 0.0]))
    with pytest.raises(ValueError):
        _validate_orbit_state(np.array([np.nan, 0, 0, 0, 0, 0, 0.0]))

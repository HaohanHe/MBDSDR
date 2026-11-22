# SPDX-License-Identifier: MIT
"""有先验星场解算守卫（spec Task 10.1）。

参考 astrometry.net `solver/quad-utils.c` + `verify.c` 的**思路**（该仓库
整体 GPLv3，只读、代码重写）：quad 旋转/缩放不变量 → 候选 → 相似变换 +
内点验证。本文件锁四件事：

1. quad 描述子对平移/旋转/缩放不变，且与四点输入顺序无关；
2. 已知相似变换能被还原（含少量像素噪声）；
3. 无关星场必须拒绝，不硬凑一个变换；
4. 有先验的 ``solve_field`` 能把「先验偏了」还原成 az/alt/roll/fov 增量。
"""
import math
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import itertools  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from mbdsdr_ai.celestial_geometry import PerspectiveProjection  # noqa: E402
from mbdsdr_ai.sky_field_solve import (  # noqa: E402
    canonical_quad_code,
    solve_field,
    solve_frame_builtin,
    solve_similarity,
)
from mbdsdr_ai.sky_interaction import (  # noqa: E402
    ViewState,
    screen_to_sky,
    sky_to_screen,
)

SIZE = (1000, 1000)
PROJ = PerspectiveProjection()


def _quad_points(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(100.0, 900.0, size=(4, 2))


def _transform(points, scale=2.3, theta_deg=37.0, shift=(120.0, -60.0)):
    th = math.radians(theta_deg)
    r = np.array([[math.cos(th), -math.sin(th)],
                  [math.sin(th), math.cos(th)]])
    return scale * (np.asarray(points) @ r.T) + np.asarray(shift)


def test_quad_code_invariant_to_similarity():
    pts = _quad_points()
    base = canonical_quad_code(pts, (0, 1, 2, 3))
    assert base is not None
    moved = _transform(pts, scale=2.3, theta_deg=37.0, shift=(120.0, -60.0))
    other = canonical_quad_code(moved, (0, 1, 2, 3))
    assert other is not None
    np.testing.assert_allclose(other.code, base.code, atol=1e-9)


def test_quad_code_is_order_independent():
    pts = _quad_points(seed=11)
    codes = {canonical_quad_code(pts, perm).code
             for perm in itertools.permutations((0, 1, 2, 3))}
    assert len(codes) == 1, f"同一四点应只有一个规范描述子，实际 {codes}"


def test_quad_code_rejects_degenerate():
    pts = np.array([[0.0, 0.0], [0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    assert canonical_quad_code(pts, (0, 1, 2, 3)) is None


def _synthetic_ref(n=20, seed=3):
    rng = np.random.default_rng(seed)
    return rng.uniform(200.0, 800.0, size=(n, 2))


def test_solve_similarity_recovers_known_transform():
    ref = _synthetic_ref(seed=5)
    obs = _transform(ref, scale=1.02, theta_deg=1.5, shift=(3.0, -2.0))
    obs = obs + np.random.default_rng(9).normal(scale=0.2, size=obs.shape)
    fit = solve_similarity(obs, ref)
    assert fit["ok"], fit
    assert fit["scale"] == pytest.approx(1.02, abs=2e-3)
    assert fit["rotation_deg"] == pytest.approx(1.5, abs=0.05)
    assert fit["inliers"] >= 18
    assert fit["residual_px"] < 0.4


def test_solve_similarity_rejects_unrelated_field():
    ref = _synthetic_ref(seed=21)
    obs = _synthetic_ref(seed=22)
    fit = solve_similarity(obs, ref)
    assert not fit["ok"], fit
    assert fit["error"] in ("no_match", "no_consistent_transform")


def test_solve_similarity_is_order_independent():
    """检测/星表输入顺序不同不应改变解（quad 描述子与顺序无关）。"""
    ref = _synthetic_ref(seed=13)
    obs = _transform(ref, scale=1.01, theta_deg=-0.8, shift=(-2.0, 4.0))
    rng = np.random.default_rng(15)
    pri = rng.permutation(len(ref))
    pio = rng.permutation(len(obs))
    a = solve_similarity(obs, ref)
    b = solve_similarity(obs[pio], ref[pri])
    assert a["ok"] and b["ok"], (a, b)
    assert b["rotation_deg"] == pytest.approx(a["rotation_deg"], abs=1e-6)
    assert b["scale"] == pytest.approx(a["scale"], abs=1e-6)
    assert b["inliers"] == a["inliers"]


def test_solve_similarity_needs_four_points():
    fit = solve_similarity(np.zeros((3, 2)), np.zeros((3, 2)))
    assert not fit["ok"]
    assert fit["error"] == "too_few_points"


def _catalog_stars(view: ViewState, n: int = 20, seed: int = 31):
    """在**先验视场内部**反投影出 n 个星表地平坐标。"""
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        x = rng.uniform(0.25 * SIZE[0], 0.75 * SIZE[0])
        y = rng.uniform(0.25 * SIZE[1], 0.75 * SIZE[1])
        az, alt = screen_to_sky(x, y, view, PROJ, SIZE)
        out.append((az, alt))
    return out


def _project(stars_altaz, view: ViewState):
    return np.array([sky_to_screen(az, alt, view, PROJ, SIZE)
                     for az, alt in stars_altaz], dtype=float)


def _views():
    prior = ViewState(center_az=120.0, center_alt=40.0, fov_deg=20.0,
                      min_fov=0.5, max_fov=120.0)
    true = ViewState(center_az=121.2, center_alt=39.2, fov_deg=19.7,
                     rotation=1.8, min_fov=0.5, max_fov=120.0)
    return prior, true


def test_solve_field_recovers_pose_deltas():
    prior, true = _views()
    catalog = _catalog_stars(prior, n=20)
    obs = _project(catalog, true)
    obs = obs + np.random.default_rng(41).normal(scale=0.2, size=obs.shape)
    fit = solve_field(obs, catalog, prior, SIZE, projection=PROJ)
    assert fit["ok"], fit
    assert fit["az_delta_deg"] == pytest.approx(1.2, abs=0.05)
    assert fit["alt_delta_deg"] == pytest.approx(-0.8, abs=0.05)
    assert fit["fov_scale"] == pytest.approx(19.7 / 20.0, abs=5e-3)
    assert fit["matched_stars"] >= 15
    assert fit["residual_px"] < 0.6

    # 契约验收：把解出的增量加回先验，重新投影应复现观测画面。
    solved = ViewState(
        center_az=prior.center_az + fit["az_delta_deg"],
        center_alt=prior.center_alt + fit["alt_delta_deg"],
        fov_deg=prior.fov_deg * fit["fov_scale"],
        rotation=prior.rotation + fit["roll_delta_deg"],
        min_fov=0.5, max_fov=120.0,
    )
    reproj = _project(catalog, solved)
    closure = float(np.sqrt(np.mean(np.sum((reproj - obs) ** 2, axis=1))))
    assert closure < 1.0, f"增量加回先验后复现残差 {closure:.2f}px"


def test_solve_field_roll_only_matches_view_rotation():
    """只改横滚时，表观 roll 增量应等于 rotation 差（符号也锁住）。"""
    prior, _true = _views()
    rolled = ViewState(center_az=prior.center_az,
                       center_alt=prior.center_alt,
                       fov_deg=prior.fov_deg, rotation=1.8,
                       min_fov=0.5, max_fov=120.0)
    catalog = _catalog_stars(prior, n=20)
    obs = _project(catalog, rolled)
    fit = solve_field(obs, catalog, prior, SIZE, projection=PROJ)
    assert fit["ok"], fit
    assert fit["roll_delta_deg"] == pytest.approx(1.8, abs=0.05)
    assert abs(fit["az_delta_deg"]) < 0.05
    assert abs(fit["alt_delta_deg"]) < 0.05


def test_solve_field_rejects_wrong_sky_patch():
    prior, _true = _views()
    catalog = _catalog_stars(prior, n=20)
    # 另一片天区在本先验下大多在相机背后；这里用一张画面内的无关点集，
    # 确保拒绝理由是「几何对不上」而不是「点不够」。
    wrong = np.random.default_rng(77).uniform(150.0, 850.0, size=(20, 2))
    fit = solve_field(wrong, catalog, prior, SIZE, projection=PROJ)
    assert not fit["ok"], fit
    assert fit["error"] in ("no_match", "no_consistent_transform",
                            "too_few_catalog_stars", "too_few_points")


def test_solve_field_finishes_in_budget():
    prior, true = _views()
    catalog = _catalog_stars(prior, n=20)
    obs = _project(catalog, true)
    t0 = time.perf_counter()
    fit = solve_field(obs, catalog, prior, SIZE, projection=PROJ)
    elapsed = time.perf_counter() - t0
    assert fit["ok"], fit
    assert elapsed < 2.5, f"20 星解算耗时 {elapsed:.2f}s，超出预算"


def test_solve_frame_builtin_uses_same_contract():
    prior, true = _views()
    catalog = _catalog_stars(prior, n=20)
    obs = _project(catalog, true)
    fit = solve_frame_builtin(
        obs, catalog, SIZE, prior.fov_deg,
        prior={"az_deg": prior.center_az, "alt_deg": prior.center_alt,
               "roll_deg": prior.rotation},
        projection=PROJ,
    )
    assert fit["ok"], fit
    assert fit["az_delta_deg"] == pytest.approx(1.2, abs=0.05)
    assert fit["alt_delta_deg"] == pytest.approx(-0.8, abs=0.05)
    assert fit["fov_scale"] == pytest.approx(19.7 / 20.0, abs=5e-3)


def test_solve_frame_builtin_rejects_bad_input():
    out = solve_frame_builtin([], [], SIZE, 20.0, prior={"az_deg": 1.0})
    assert not out["ok"] and out["error"] == "bad_input"
    out2 = solve_frame_builtin([], [], (0, 100), 20.0,
                               prior={"az_deg": 0, "alt_deg": 40})
    assert not out2["ok"] and out2["error"] == "bad_input"

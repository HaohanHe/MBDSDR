# SPDX-License-Identifier: MIT
"""星点检测：合成图上的算术与失败原因口径。

所有输入都是**合成图**（高斯 PSF + 高斯噪声，固定随机种子），不接相机、
不读磁盘、不联网。质心期望值来自合成时写入的真值坐标，不用被测模块反推。

约定：
* 星点质心坐标顺序为 ``(x_px, y_px)``（x=列，y=行）；
* ``solve_from_frame`` 的成功路径复用 ``tests/test_sky_pointing_pipeline.py``
  的假解算器口径（手算 RA/Dec + 既有坐标链算残差）。
"""

import datetime
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.celestial_geometry import gmst  # noqa: E402
from mbdsdr_ai.sky_pointing_pipeline import (  # noqa: E402
    CameraParams,
    solve_from_frame,
)
from mbdsdr_ai.star_detect import (  # noqa: E402
    DetectConfig,
    detect_stars,
)

_JD_J2000 = 2451545.0
_AT_UTC = datetime.datetime(2000, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)


def _synthetic_frame(height, width, stars, *, background=100.0,
                     noise_sigma=1.0, psf_sigma=1.2, seed=7):
    """合成灰度帧：stars 为 [(x, y, 峰值幅度)]，叠加高斯噪声。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    image = np.full((height, width), background, dtype=float)
    for x, y, amplitude in stars:
        image += amplitude * np.exp(
            -((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * psf_sigma ** 2))
    return image + rng.normal(0.0, noise_sigma, size=image.shape)


def _nearest_distance(centroids, x, y):
    return min(math.hypot(cx - x, cy - y) for cx, cy in centroids)


def _nearest_index(centroid, truths):
    x, y = centroid
    return min(range(len(truths)),
               key=lambda i: math.hypot(x - truths[i][0], y - truths[i][1]))


def _site_for_lst_zero(lat_deg):
    """J2000 时刻把本地恒星时对到 0°：lon = -GMST(J2000)。"""
    lon = (-gmst(_JD_J2000)) % 360.0
    if lon > 180.0:
        lon -= 360.0
    return lat_deg, lon


def _fake_solver(ra_deg, dec_deg, *, matched=12, calls=None):
    def _solver(points, image_size, fov_deg, *, prior, observer):
        if calls is not None:
            calls.append({"points": list(points), "image_size": image_size,
                          "fov_deg": fov_deg})
        return {"ok": True, "ra_deg": ra_deg, "dec_deg": dec_deg,
                "roll_deg": 0.0, "matched_stars": matched}

    return _solver


# ---------------------------------------------------------------------------
# 质心精度（合成图，真值已知）
# ---------------------------------------------------------------------------
def test_synthetic_gaussian_centroids_match_truth_within_fraction_of_pixel():
    truths = [(20.3, 15.7, 300.0), (40.6, 33.2, 250.0), (12.5, 48.4, 220.0)]
    image = _synthetic_frame(64, 64, truths, noise_sigma=2.0)
    result = detect_stars(image)
    assert result.ok is True, result.reason
    assert len(result.centroids) == len(truths)
    assert result.background == pytest.approx(100.0, abs=0.3)
    assert result.noise_sigma == pytest.approx(2.0, abs=0.3)
    for x, y, _ in truths:
        assert _nearest_distance(result.centroids, x, y) < 0.3


# ---------------------------------------------------------------------------
# 输入合法性
# ---------------------------------------------------------------------------
def test_flat_image_is_reported_as_flat_not_as_stars():
    result = detect_stars(np.full((32, 32), 42.0))
    assert result.ok is False
    assert result.error == "flat_image"
    assert result.centroids == ()
    assert result.reason
    assert "已对准" not in result.reason


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"),
                                       float("-inf")])
def test_non_finite_pixels_are_bad_image(bad_value):
    image = _synthetic_frame(32, 32, [(16.0, 16.0, 500.0)])
    image[5, 5] = bad_value
    result = detect_stars(image)
    assert result.ok is False
    assert result.error == "bad_image"
    assert "NaN" in result.reason or "Inf" in result.reason


@pytest.mark.parametrize("bad_image", [
    np.zeros(32, dtype=float),
    np.zeros((4, 4, 4), dtype=float),
])
def test_non_2d_inputs_are_bad_image(bad_image):
    result = detect_stars(bad_image)
    assert result.ok is False
    assert result.error == "bad_image"


def test_tiny_image_is_bad_image():
    result = detect_stars(np.zeros((2, 64), dtype=float))
    assert result.ok is False
    assert result.error == "bad_image"


# ---------------------------------------------------------------------------
# 空态与面积/边界过滤
# ---------------------------------------------------------------------------
def test_pure_noise_frame_reports_no_stars():
    rng = np.random.default_rng(11)
    result = detect_stars(rng.normal(100.0, 1.0, size=(64, 64)))
    assert result.ok is False
    assert result.error == "no_stars"
    assert result.centroids == ()
    assert "已对准" not in result.reason


def test_single_pixel_hotspot_is_rejected_by_min_area():
    rng = np.random.default_rng(12)
    image = rng.normal(100.0, 1.0, size=(48, 48))
    image[24, 24] = 9000.0
    result = detect_stars(image)
    assert result.ok is False
    assert result.error == "no_stars"
    assert result.rejected_low_area == 1
    assert result.rejected_high_area == 0
    assert result.rejected_border == 0


def test_oversized_block_is_rejected_by_max_area():
    rng = np.random.default_rng(13)
    image = rng.normal(100.0, 1.0, size=(64, 64))
    image[15:45, 15:45] = 5000.0    # 30×30 = 900 px，超过 max_area_px=400
    result = detect_stars(image)
    assert result.ok is False
    assert result.error == "no_stars"
    assert result.rejected_high_area == 1
    assert result.rejected_low_area == 0


def test_border_star_is_rejected_by_border_px():
    # 贴着第 0 行的星：质心 y 远小于 border_px=2。
    image = _synthetic_frame(64, 64, [(30.0, 0.5, 400.0)])
    result = detect_stars(image, DetectConfig(border_px=2.0))
    assert result.ok is False
    assert result.error == "no_stars"
    assert result.rejected_border == 1


# ---------------------------------------------------------------------------
# 阈值与截断
# ---------------------------------------------------------------------------
def test_higher_threshold_never_increases_star_count():
    truths = [(15.0, 15.0, 900.0), (40.0, 15.0, 700.0),
              (65.0, 15.0, 500.0), (15.0, 45.0, 350.0),
              (40.0, 45.0, 250.0), (65.0, 45.0, 180.0)]
    image = _synthetic_frame(96, 96, truths, noise_sigma=1.5)
    low = detect_stars(image, DetectConfig(threshold_sigma=3.0))
    high = detect_stars(image, DetectConfig(threshold_sigma=8.0))
    assert low.ok is True, low.reason
    assert len(high.centroids) <= len(low.centroids)


def test_max_stars_truncates_to_brightest_in_flux_order():
    truths = [(15.0, 15.0, 900.0), (40.0, 15.0, 700.0),
              (65.0, 15.0, 500.0), (15.0, 45.0, 350.0),
              (40.0, 45.0, 250.0), (65.0, 45.0, 180.0)]
    image = _synthetic_frame(96, 96, truths, noise_sigma=1.5)
    result = detect_stars(image, DetectConfig(max_stars=3))
    assert result.ok is True, result.reason
    assert len(result.centroids) == 3
    # 保留的必须是三个最亮的星点，且按流量降序返回。
    indices = [_nearest_index(centroid, truths)
               for centroid in result.centroids]
    assert indices == [0, 1, 2]
    amplitudes = [truths[i][2] for i in indices]
    assert amplitudes == sorted(amplitudes, reverse=True)


# ---------------------------------------------------------------------------
# solve_from_frame：检测失败 / 成功
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_frame, expected_error", [
    (np.full((32, 32), 42.0), "flat_image"),
    (np.zeros(32, dtype=float), "bad_image"),
])
def test_solve_from_frame_detection_failure_never_calls_solver(
        bad_frame, expected_error):
    calls = []
    camera = CameraParams(width_px=32, height_px=32, fov_deg=8.0)
    out = solve_from_frame(bad_frame, camera, 180.0, 45.0, 45.0, 0.0,
                           _AT_UTC, solver=_fake_solver(0.0, 0.0, calls=calls))
    assert calls == []
    assert out.ok is False
    assert out.error == expected_error
    assert out.reason.startswith("星点检测失败")
    assert out.ra_deg is None
    assert out.residual_deg is None
    assert "已对准" not in out.reason


def test_solve_from_frame_success_uses_detected_centroids_and_fake_solver():
    truths = [(16.0 + 24.0 * col, 20.0 + 30.0 * row, 300.0)
              for col in range(4) for row in range(3)]
    image = _synthetic_frame(100, 100, truths, noise_sigma=1.0)
    calls = []
    lat, lon = _site_for_lst_zero(45.0)
    camera = CameraParams(width_px=100, height_px=100, fov_deg=8.0)

    out = solve_from_frame(image, camera, 180.0, 45.0, lat, lon, _AT_UTC,
                           solver=_fake_solver(0.0, 0.0, calls=calls))

    assert out.ok is True, out.reason
    assert len(calls) == 1
    assert len(calls[0]["points"]) >= 8
    assert calls[0]["image_size"] == (100.0, 100.0)
    assert out.ra_deg == pytest.approx(0.0, abs=1e-9)
    assert out.dec_deg == pytest.approx(0.0, abs=1e-9)
    assert out.residual_deg == pytest.approx(0.0, abs=0.02)
    assert out.error == ""
    assert out.reason == ""


def test_solve_from_frame_too_few_points_is_decided_by_solve_and_verify():
    # 只有 2 颗星：检测成功，但星数不足交给 solve_and_verify 判
    # too_few_points，解算器同样不能被调用。
    truths = [(20.0, 20.0, 400.0), (60.0, 60.0, 400.0)]
    image = _synthetic_frame(100, 100, truths, noise_sigma=1.0)
    calls = []
    camera = CameraParams(width_px=100, height_px=100, fov_deg=8.0)
    out = solve_from_frame(image, camera, 180.0, 45.0, 45.0, 0.0, _AT_UTC,
                           solver=_fake_solver(0.0, 0.0, calls=calls))
    assert calls == []
    assert out.ok is False
    assert out.error == "too_few_points"
    assert not out.reason.startswith("星点检测失败")

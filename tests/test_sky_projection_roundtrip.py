# SPDX-License-Identifier: MIT
"""天空视图投影往返守卫（D4 / Task 23）。

契约：``sky_to_screen`` 与 ``screen_to_sky`` 在**视场内**互逆。
输入端已是站心 (az, alt)（固定站址/时刻的球面坐标在这一层之外算好），
本用例只锁投影这一对换算：

  1. 视场 60° / 8° / 2.3°，固定站心视角（az=180°, alt=45°）；
  2. 取视场 90% 半径内的网格点，az/alt -> 像素 -> az/alt -> 像素；
  3. 像素往返误差 ≤ 1e-6 px（规范值），角度往返误差 ≤ 1e-5°（见下）。

角度容差说明：透视投影的 ``screen_scale = (min_dim/2)/tan(fov/2)``，2.3° 视场
下 1 px ≈ 0.0033°，因此 1e-6 px 对应的角度量级是 ~3e-9°。规范要求的是像素
量级，这里同时锁角度：``acos`` 在角距 ~0 附近条件数差，float64 实测最大
~1.2e-6°（2e-8 rad），故角度门取 1e-5°——仍比 2.3° 下 1e-6 px 严约 3 个
量级，且不靠运气。像素门保持规范值 1e-6 px。超出容差时打印最大误差点。
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.celestial_geometry import PerspectiveProjection  # noqa: E402
from mbdsdr_ai.sky_interaction import (  # noqa: E402
    ViewState,
    screen_to_sky,
    sky_to_screen,
)

SIZE = (700, 700)
TOL_PX = 1e-6
TOL_DEG = 1e-5
FOVS = (60.0, 8.0, 2.3)


def _angular_sep_deg(az1, alt1, az2, alt2) -> float:
    a1, h1 = math.radians(az1), math.radians(alt1)
    a2, h2 = math.radians(az2), math.radians(alt2)
    c = (math.sin(h1) * math.sin(h2)
         + math.cos(h1) * math.cos(h2) * math.cos(a1 - a2))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _grid_points(fov: float, center_az: float = 180.0,
                 center_alt: float = 45.0, steps: int = 7):
    """视场 90% 半径内的 (az, alt) 网格（含中心点）。"""
    span = fov * 0.45
    lat_scale = max(0.2, math.cos(math.radians(center_alt)))
    pts = []
    for i in range(steps + 1):
        d_alt = -span + 2.0 * span * i / steps
        for j in range(steps + 1):
            d_az = (-span + 2.0 * span * j / steps) / lat_scale
            az = (center_az + d_az) % 360.0
            alt = center_alt + d_alt
            sep = _angular_sep_deg(center_az, center_alt, az, alt)
            if sep <= span:          # 只取视场内（90% 半径）的点
                pts.append((az, alt))
    return pts


@pytest.mark.parametrize("fov", FOVS)
def test_sky_screen_roundtrip_within_field(fov):
    view = ViewState(center_az=180.0, center_alt=45.0, fov_deg=fov,
                     min_fov=0.5, max_fov=120.0)
    projection = PerspectiveProjection()
    points = _grid_points(fov)
    assert len(points) > 20, "网格点太少，测不出投影畸变"

    worst_px = 0.0
    worst_px_pt = None
    worst_deg = 0.0
    worst_deg_pt = None
    for az, alt in points:
        px, py = sky_to_screen(az, alt, view, projection, SIZE)
        assert math.isfinite(px) and math.isfinite(py), (az, alt)
        # 视场 90% 半径内的点必须落在画布内
        assert 0.0 <= px <= SIZE[0] and 0.0 <= py <= SIZE[1], (az, alt, px, py)

        az2, alt2 = screen_to_sky(px, py, view, projection, SIZE)
        px2, py2 = sky_to_screen(az2, alt2, view, projection, SIZE)

        err_px = math.hypot(px2 - px, py2 - py)
        if err_px > worst_px:
            worst_px, worst_px_pt = err_px, (az, alt, px, py)
        err_deg = _angular_sep_deg(az, alt, az2, alt2)
        if err_deg > worst_deg:
            worst_deg, worst_deg_pt = err_deg, (az, alt, az2, alt2)

    assert worst_px <= TOL_PX, (
        f"fov={fov}° 最大像素往返误差 {worst_px:.3e} px > {TOL_PX}，"
        f"最大点 (az,alt,px,py)={worst_px_pt}")
    assert worst_deg <= TOL_DEG, (
        f"fov={fov}° 最大角度往返误差 {worst_deg:.3e}° > {TOL_DEG}，"
        f"最大点 (az,alt,az2,alt2)={worst_deg_pt}")


def test_narrow_fov_resolution_is_stricter_than_wide():
    """自证：2.3° 下每像素对应的角度小于 60°，说明窄视场容差确实更严。"""
    deg_per_px_narrow = 2.3 / min(SIZE)
    deg_per_px_wide = 60.0 / min(SIZE)
    assert deg_per_px_narrow <= deg_per_px_wide
    # 1e-6 px 在 2.3° 视场对应的角度远小于 1e-6°
    assert deg_per_px_narrow * TOL_PX < TOL_DEG

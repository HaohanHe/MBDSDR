#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
moon_albedo.py — 月面反照率采样（数据 + 几何层，**不含绘制**）
=================================================================

数据
----
NASA PDS 的 ``LRO-L-LOLA-4-GDR-V1.0`` 中 ``LDAM_4``（LOLA 1064 nm 月面法向
反照率，4 pix/deg，Lemelin et al. 2016）。本仓随包分发的是 2 pix/deg 降采样
版本 ``mbdsdr_ai/data/moon_albedo_lola_2ppd.npz``；原始文件 URL、SHA256、
许可原文与引用都写在 ``meta`` 里，可离线核对。

几何
----
正射（orthographic）投影的正/逆变换。**约定**（全部写在函数 docstring 里）：

* 圆盘坐标 ``(x, y)`` 是单位圆盘坐标（半径 1），``+x`` = 月面**东**（east），
  ``+y`` = 月面**北**（north），``(0, 0)`` = 次地球点（sub-Earth point）。
* 反照率图是 Simple Cylindrical、纬经度均 planetocentric、经度**正东**、
  ``-180..+180``；**行 0 = +90°N**（PDS IMG 惯例，已用已知月海/第谷坑位置验证）。
* 本模块**只出数值**：反投影、采样、亮度系数。月相盘的视觉形态仍是冻结项，
  绘制层不调用本模块（见 ``docs/limitations-register.md`` 附 Q）。
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

MOON_RADIUS_KM = 1737.4

# 随包分发的降采样网格（2 pix/deg）。改数据文件必须同步改这里与 meta 校验测试。
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "moon_albedo_lola_2ppd.npz"

# 反照率参考值：取全图平均值（由数据本身给出，不凭经验假设一个「月亮亮度」）。
# `albedo_modulation` 用它做归一，保证平均亮度不变、只改纹理对比。
REFERENCE_ALBEDO_SOURCE = "mean of the shipped grid"


def _normalize_lon(lon_deg: float) -> float:
    """经度归一到 (-180, 180]。"""
    lon = (float(lon_deg) + 180.0) % 360.0 - 180.0
    # 180.0 与 -180.0 等价；统一取 +180 端由上面的取模自然给出。
    return 180.0 if lon == -180.0 else lon


@functools.lru_cache(maxsize=2)
def _load(path: Optional[str] = None) -> Tuple[np.ndarray, dict]:
    """读网格与 meta；返回 (albedo[rows, cols], meta dict)。"""
    p = Path(path) if path else DATA_PATH
    with np.load(p, allow_pickle=False) as z:
        grid = np.asarray(z["albedo"], dtype=np.float64)
        meta = json.loads(str(z["meta"].item()))
    if grid.ndim != 2:
        raise ValueError(f"albedo 网格应为二维，实际 {grid.shape}")
    return grid, meta


def albedo_grid(path: Optional[str] = None) -> np.ndarray:
    """反照率网格（行 0 = +90°N，列 0 = -180°E）。"""
    return _load(path)[0]


def albedo_meta(path: Optional[str] = None) -> dict:
    """数据出处/许可/校验信息。"""
    return dict(_load(path)[1])


def grid_resolution_pix_per_deg(path: Optional[str] = None) -> float:
    return float(albedo_meta(path)["resolution_pix_per_deg"])


def reference_albedo(path: Optional[str] = None) -> float:
    """归一用参考反照率 = 网格均值（数据给出，不是假设值）。"""
    grid = albedo_grid(path)
    return float(np.mean(grid))


def _bilinear(grid: np.ndarray, lat_deg: float, lon_deg: float) -> float:
    """在 Simple Cylindrical 网格上做双线性采样（格心在像素中心）。

    **本产品（LDAM_4_FLOAT）的 PDS 标签写死**：``CENTER_LONGITUDE = 180``、
    ``WESTERNMOST_LONGITUDE = 0``、``EASTERNMOST_LONGITUDE = 360``——
    即**行 0 = +90°N、列 0 = 0°E**（东经 0..360，向东增大）。
    这不影响调用方：对外经纬度仍是 -180..180。
    """
    rows, cols = grid.shape
    res = rows / 180.0                    # 行数 / 180° = pix/deg
    lat = max(-90.0, min(90.0, float(lat_deg)))
    lon_east = float(lon_deg) % 360.0     # -180..180 -> 0..360（东经）

    # 连续索引（以格心为整数点）：行 0 格心 = +90 − 0.5/res
    fi = (90.0 - lat) * res - 0.5
    fj = lon_east * res - 0.5

    i0 = int(math.floor(fi))
    j0 = int(math.floor(fj))
    ti = fi - i0
    tj = fj - j0
    i0 = max(0, min(rows - 1, i0))
    i1 = max(0, min(rows - 1, i0 + 1))
    j0m = j0 % cols
    j1m = (j0 + 1) % cols

    v00 = grid[i0, j0m]
    v01 = grid[i0, j1m]
    v10 = grid[i1, j0m]
    v11 = grid[i1, j1m]
    return float((1 - ti) * ((1 - tj) * v00 + tj * v01)
                 + ti * ((1 - tj) * v10 + tj * v11))


def sample_albedo(lat_deg: float, lon_deg: float,
                  path: Optional[str] = None) -> float:
    """月面法向反照率（1064 nm）采样，双线性。"""
    return _bilinear(albedo_grid(path), lat_deg, lon_deg)


# ---------------------------------------------------------------------------
# 正射投影：正/逆变换
# ---------------------------------------------------------------------------

def selenographic_to_disc(lat_deg: float, lon_deg: float,
                          sub_lat_deg: float = 0.0,
                          sub_lon_deg: float = 0.0
                          ) -> Tuple[float, float, bool]:
    """月面经纬度 -> 圆盘坐标 ``(x, y, visible)``。

    正射投影：``x`` 向月面东、``y`` 向月面北，``(0,0)`` 是次地球点。
    ``visible`` 是「该点是否在朝向观测者的半球上」（``w > 0``）。
    """
    phi = math.radians(float(lat_deg))
    lam = math.radians(float(lon_deg))
    phi0 = math.radians(float(sub_lat_deg))
    lam0 = math.radians(float(sub_lon_deg))
    dlam = lam - lam0

    x = math.cos(phi) * math.sin(dlam)
    y = (math.cos(phi0) * math.sin(phi)
         - math.sin(phi0) * math.cos(phi) * math.cos(dlam))
    w = (math.sin(phi0) * math.sin(phi)
         + math.cos(phi0) * math.cos(phi) * math.cos(dlam))
    return x, y, w > 0.0


def disc_to_selenographic(x: float, y: float,
                          sub_lat_deg: float = 0.0,
                          sub_lon_deg: float = 0.0
                          ) -> Optional[Tuple[float, float]]:
    """圆盘坐标 ``(x, y)`` -> 月面经纬度 ``(lat, lon)``；圆盘外返回 ``None``。

    ``x² + y² > 1`` 的点不在可见圆盘内，不猜一个「最近的月面点」。
    """
    xx = float(x)
    yy = float(y)
    rho = math.hypot(xx, yy)
    if rho > 1.0:
        return None
    phi0 = math.radians(float(sub_lat_deg))
    lam0 = math.radians(float(sub_lon_deg))
    if rho == 0.0:
        return (float(sub_lat_deg), _normalize_lon(float(sub_lon_deg)))

    c = math.asin(min(1.0, rho))          # 与次地球点的角距（0..90°）
    sin_c = rho                           # sin(c) = rho
    cos_c = math.sqrt(max(0.0, 1.0 - rho * rho))

    sin_phi = (cos_c * math.sin(phi0)
               + (yy * sin_c * math.cos(phi0)) / rho)
    sin_phi = max(-1.0, min(1.0, sin_phi))
    phi = math.asin(sin_phi)

    lam = lam0 + math.atan2(
        xx * sin_c,
        rho * cos_c * math.cos(phi0) - yy * sin_c * math.sin(phi0))
    return (math.degrees(phi), _normalize_lon(math.degrees(lam)))


def screen_to_disc(px: float, py: float,
                   cx: float, cy: float, radius_px: float,
                   north_angle_deg: float = 0.0
                   ) -> Optional[Tuple[float, float]]:
    """屏幕像素 -> 圆盘坐标 ``(x, y)``；圆盘外返回 ``None``。

    屏幕坐标按 Qt 口径（``+y`` 向下）。``north_angle_deg`` 是月面北在屏幕上的
    方向，从「屏幕正上方」起向「屏幕右方」量（0 = 北朝上，90 = 北朝右）。
    本函数只做坐标换算，不引入天平动/位置角——那些是调用方输入（Q3）。
    """
    r = float(radius_px)
    if not math.isfinite(r) or r <= 0.0:
        return None
    sx = float(px) - float(cx)
    sy = float(py) - float(cy)
    th = math.radians(float(north_angle_deg))
    # 东向 = 「北向」顺时针 90°（北朝上时东在屏幕右）
    east = (math.cos(th), math.sin(th))
    north = (math.sin(th), -math.cos(th))
    x = (sx * east[0] + sy * east[1]) / r
    y = (sx * north[0] + sy * north[1]) / r
    if math.hypot(x, y) > 1.0:
        return None
    return x, y


def albedo_at_disc(x: float, y: float,
                   sub_lat_deg: float = 0.0,
                   sub_lon_deg: float = 0.0,
                   path: Optional[str] = None) -> Optional[float]:
    """圆盘坐标 -> 月面经纬度 -> 反照率；圆盘外返回 ``None``。"""
    ll = disc_to_selenographic(x, y, sub_lat_deg, sub_lon_deg)
    if ll is None:
        return None
    return sample_albedo(ll[0], ll[1], path)


def albedo_modulation(albedo: float, path: Optional[str] = None) -> float:
    """反照率 -> 相对亮度系数（均值归一，平均亮度不变）。

    这是**数据/几何层**的输出；绘制层当前不调用它（视觉冻结）。
    """
    ref = reference_albedo(path)
    if not math.isfinite(float(albedo)) or ref <= 0.0:
        return 1.0
    return float(albedo) / ref

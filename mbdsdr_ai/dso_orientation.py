# SPDX-License-Identifier: MIT
"""深空天体长轴在屏幕上的朝向（纯数学，不 import Qt）。

目录字段 ``position_angle_deg``（OpenNGC 的 PosAng）是**长轴相对北的位置
角，从北向东量**：正北 0°、正东 90°。要把这个角度变成
``QPainter.rotate()`` 的参数，必须先知道「北」和「东」在本帧投影里指向
屏幕的哪个方向；本模块只负责这段纯数学，投影/历元换算由调用方完成，
并把北点、东点的屏幕向量传进来。

角度约定
--------
屏幕坐标 **y 向下**；``major_axis_screen_deg`` 返回 ``atan2(dy, dx)`` 的
度数，与 Qt ``QPainter.rotate`` 同口径（y 向下时正角即顺时针）。

诚实语义
--------
PA 非有限或越界（< 0 或 >= 360）、北/东屏幕向量退化（模长 < 1e-6）或
本身非有限 → 一律返回 ``None``，调用方退回不旋转（画水平椭圆），绝不
拿默认角度冒充目录朝向。
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

__all__ = ["basis_offsets", "major_axis_screen_deg"]

# 北极附近的 cos(dec) 下限：防止东点 RA 偏移除零（dec=±90° 时 cos≈6e-17）。
_COS_DEC_FLOOR = 1e-6
# 北点的赤纬上限：赤纬 ±90° 是极点，留 0.001° 余量避免向量退化。
_DEC_LIMIT_DEG = 89.999
# 屏幕向量视为退化（无法定方向）的模长阈值，单位像素。
_MIN_VEC_PX = 1e-6


def basis_offsets(ra_deg: float, dec_deg: float, step_deg: float = 0.2
                  ) -> Tuple[float, float, float, float]:
    """返回北点/东点在赤道坐标里的位置：``(north_ra, north_dec,
    east_ra, east_dec)``，单位度。

    北点：``dec + step``，夹到 ±89.999°（球面小步长不应越过极点）；
    东点：``ra + step / cos(dec)``，cos 下限 1e-6（极点附近不除零），
    RA 归一 [0, 360)，赤纬与原点相同。

    参数非有限或 ``step_deg <= 0`` 时抛 ``ValueError``——调用方不该拿
    一个不存在的方向去猜朝向。
    """
    ra = float(ra_deg)
    dec = float(dec_deg)
    step = float(step_deg)
    if not (math.isfinite(ra) and math.isfinite(dec) and math.isfinite(step)):
        raise ValueError("basis_offsets 需要有限输入")
    if step <= 0.0:
        raise ValueError("basis_offsets 的 step_deg 必须 > 0")
    ra %= 360.0
    north_dec = max(-_DEC_LIMIT_DEG, min(_DEC_LIMIT_DEG, dec + step))
    cos_dec = max(abs(math.cos(math.radians(dec))), _COS_DEC_FLOOR)
    east_ra = (ra + step / cos_dec) % 360.0
    return (ra, north_dec, east_ra, dec)


def major_axis_screen_deg(pa_deg: float,
                          north_dx: float, north_dy: float,
                          east_dx: float, east_dy: float) -> Optional[float]:
    """把目录位置角换成屏幕上的长轴角度（度），供 ``QPainter.rotate``。

    屏幕坐标 y 向下；``(north_dx, north_dy)`` / ``(east_dx, east_dy)`` 是
    北/东方向在屏幕上的向量（由同一帧投影得到）。长轴方向为
    ``north * cos(pa) + east * sin(pa)``，返回其 ``atan2(dy, dx)`` 度数
    （y 向下，正角顺时针）。

    退化/非法输入（PA 非有限或越界、任一向量模长 < 1e-6、向量含 NaN/Inf、
    合成向量长度过小）→ ``None``，调用方退回不旋转。
    """
    try:
        pa = float(pa_deg)
        ndx = float(north_dx)
        ndy = float(north_dy)
        edx = float(east_dx)
        edy = float(east_dy)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pa) or not (0.0 <= pa < 360.0):
        return None
    if not all(math.isfinite(v) for v in (ndx, ndy, edx, edy)):
        return None
    if math.hypot(ndx, ndy) < _MIN_VEC_PX or math.hypot(edx, edy) < _MIN_VEC_PX:
        return None
    rad = math.radians(pa)
    ux = ndx * math.cos(rad) + edx * math.sin(rad)
    uy = ndy * math.cos(rad) + edy * math.sin(rad)
    if math.hypot(ux, uy) < _MIN_VEC_PX:
        return None
    return math.degrees(math.atan2(uy, ux))

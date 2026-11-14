#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""RTK 几何原语叶子模块：物理常数 + ECEF/LLH/ENU 坐标转换。

``rtk_solver`` 需要这些原语，而 ``rtklib_adapter`` 又在 NTRIP 管线里延迟
import ``rtk_solver``——原语若留在 ``rtklib_adapter``，两者就互为上下游成环。
本模块不 import 任何 ``mbdsdr_ai`` 模块；``rtklib_adapter`` 原处再导出，
外部 ``rtklib_adapter.PI`` / ``CoordinateConverter`` 的用法与对象身份不变。
"""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np

# ============================================================
# 物理与几何常数 —— 逐字抄自 RTKLIB 源码，禁止改动数值
# ============================================================

# rtklib.h:56 #define PI 3.1415926535897932  /* pi */
PI = 3.1415926535897932
# rtklib.h:57 #define D2R (PI/180.0)
D2R = PI / 180.0
# rtklib.h:58 #define R2D (180.0/PI)
R2D = 180.0 / PI

# rtklib.h:59 #define CLIGHT 299792458.0  /* speed of light (m/s) */
CLIGHT = 299792458.0

# rtklib.h:64 #define OMGE 7.2921151467E-5  /* earth angular velocity (IS-GPS) (rad/s) */
OMGE = 7.2921151467e-5

# rtklib.h:66 #define RE_WGS84 6378137.0  /* earth semimajor axis (WGS84) (m) */
RE_WGS84 = 6378137.0
# rtklib.h:67 #define FE_WGS84 (1.0/298.257223563)  /* earth flattening (WGS84) */
FE_WGS84 = 1.0 / 298.257223563


# ============================================================
# 参考椭球参数化 + LLA<->ECEF 唯一实现
# ============================================================
# 两条链必须能表达各自的椭球，**不许统一成一套**：
#   - WGS-84：RTK/GNSS（rtklib 口径，米）
#   - WGS-72：SGP4/TEME（Spacetrack Report #3 口径，与 sgp4 库一致）
# 赤道半径差 2.0 m；统一椭球会让卫星星下点/站心几何产生米级系统差。
#
# 返回顺序全仓统一为 **(lat_deg, lon_deg, alt)**；历史上有实现返回
# (lon, lat, alt)，接错就是经纬对调且不报错。单位由函数名后缀钉死：
# ``_m`` = 米，``_km`` = 公里，不做隐式换算。
from dataclasses import dataclass  # noqa: E402  (保持与上方 rtklib 常量相邻)


@dataclass(frozen=True)
class Ellipsoid:
    """参考椭球（米制定义）。``a_m`` 长半轴，``f`` 扁率。"""

    name: str
    a_m: float
    f: float

    @property
    def e2(self) -> float:
        return self.f * (2.0 - self.f)

    @property
    def b_m(self) -> float:
        return self.a_m * (1.0 - self.f)


#: WGS-84（RTK/GNSS）
WGS84 = Ellipsoid("WGS-84", RE_WGS84, FE_WGS84)
#: WGS-72（SGP4/TEME，Spacetrack Report #3）
WGS72 = Ellipsoid("WGS-72", 6378135.0, 1.0 / 298.26)


def geodetic_to_ecef_m(lat_deg: float, lon_deg: float, alt_m: float,
                       ellipsoid: Ellipsoid = WGS84) -> Tuple[float, float, float]:
    """大地坐标(°,°,m) -> ECEF(m)。返回 (x_m, y_m, z_m)。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sinp, cosp = math.sin(lat), math.cos(lat)
    v = ellipsoid.a_m / math.sqrt(1.0 - ellipsoid.e2 * sinp * sinp)
    return ((v + alt_m) * cosp * math.cos(lon),
            (v + alt_m) * cosp * math.sin(lon),
            (v * (1.0 - ellipsoid.e2) + alt_m) * sinp)


def ecef_to_geodetic_m(x_m: float, y_m: float, z_m: float,
                       ellipsoid: Ellipsoid = WGS84,
                       tol_m: float = 1e-6) -> Tuple[float, float, float]:
    """ECEF(m) -> 大地坐标 **(lat_deg, lon_deg, alt_m)**。

    Bowring 迭代（与 rtklib ``ecef2pos()`` 同算法），``tol_m`` 为米制收敛门限。
    极点附近 (x=y=0) 直接返回 ±90°，避免除零。
    """
    a, e2 = ellipsoid.a_m, ellipsoid.e2
    r2 = x_m * x_m + y_m * y_m
    if r2 < 1e-12:
        lat = math.pi / 2.0 if z_m >= 0 else -math.pi / 2.0
        return math.degrees(lat), 0.0, abs(z_m) - ellipsoid.b_m
    zk, z, v = 0.0, z_m, a
    # z 每轮重置为 z_m + v·e²·sinφ（非累加）；上限防病态输入死循环。
    for _ in range(100):
        if abs(z - zk) < tol_m:
            break
        zk = z
        sinp = z / math.sqrt(r2 + z * z)
        v = a / math.sqrt(1.0 - e2 * sinp * sinp)
        z = z_m + v * e2 * sinp
    lat = math.atan2(z, math.sqrt(r2))
    return math.degrees(lat), math.degrees(math.atan2(y_m, x_m)), \
        math.sqrt(r2 + z * z) - v


def geodetic_to_ecef_km(lat_deg: float, lon_deg: float, alt_km: float,
                        ellipsoid: Ellipsoid = WGS84) -> Tuple[float, float, float]:
    """大地坐标(°,°,km) -> ECEF(km)。返回 (x_km, y_km, z_km)。"""
    x, y, z = geodetic_to_ecef_m(lat_deg, lon_deg, alt_km * 1000.0, ellipsoid)
    return x / 1000.0, y / 1000.0, z / 1000.0


def ecef_to_geodetic_km(x_km: float, y_km: float, z_km: float,
                        ellipsoid: Ellipsoid = WGS84,
                        tol_m: float = 1e-6) -> Tuple[float, float, float]:
    """ECEF(km) -> 大地坐标 **(lat_deg, lon_deg, alt_km)**。"""
    lat, lon, h_m = ecef_to_geodetic_m(x_km * 1000.0, y_km * 1000.0,
                                       z_km * 1000.0, ellipsoid, tol_m)
    return lat, lon, h_m / 1000.0


# ============================================================
# CoordinateConverter —— 
# ============================================================
class CoordinateConverter:
    """ECEF(地心地固直角) <-> LLH(经纬高) <-> ENU(站心东北天)。

    全类固定 **WGS-84 椭球、长度单位米**；卫星轨道链
    （``orbit.wgs72_*``）是 WGS-72 + km，禁止混用。
    """

 # ----  ecef2pos() ----
    @staticmethod
    def ecef_to_llh(x: float, y: float, z: float) -> Tuple[float, float, float]:
        """WGS-84 / 米：ECEF(m) -> (lat[rad], lon[rad], h[m])。 ecef2pos()。

        唯一内核 ``ecef_to_geodetic_m`` 的弧度/度边界转换；椭球与算法见那里。
        """
        lat_deg, lon_deg, h_m = ecef_to_geodetic_m(x, y, z, WGS84)
        return math.radians(lat_deg), math.radians(lon_deg), h_m

 # ----  pos2ecef() ----
    @staticmethod
    def llh_to_ecef_m(lat: float, lon: float, h: float) -> Tuple[float, float, float]:
        """WGS-84 / 米：(lat[rad], lon[rad], h[m]) -> ECEF(m)。 pos2ecef()。

        名字里的 ``_m`` 是硬边界；卫星链请用
        ``orbit.wgs72_geodetic_to_ecef_km``（WGS-72、km）。
        """
        return geodetic_to_ecef_m(math.degrees(lat), math.degrees(lon), h, WGS84)

 # ----  xyz2enu() + 1686 ecef2enu() ----
    @staticmethod
    def ecef_to_enu(lat: float, lon: float, dx: float, dy: float, dz: float
                    ) -> Tuple[float, float, float]:
        """ECEF 向量 -> ENU 向量。 ecef2enu()（经 xyz2enu 旋转阵）。"""
        sinp = math.sin(lat); cosp = math.cos(lat)
        sinl = math.sin(lon); cosl = math.cos(lon)
 #   E 矩阵（行主序 3x3）
        e = -sinl * dx + cosl * dy
        n = -sinp * cosl * dx - sinp * sinl * dy + cosp * dz
        u = cosp * cosl * dx + cosp * sinl * dy + sinp * dz
        return e, n, u

 # ----  enu2ecef() ----
    @staticmethod
    def enu_to_ecef(lat: float, lon: float, e: float, n: float, u: float
                    ) -> Tuple[float, float, float]:
        """ENU 向量 -> ECEF 向量（旋转阵转置）。 enu2ecef()。"""
        sinp = math.sin(lat); cosp = math.cos(lat)
        sinl = math.sin(lon); cosl = math.cos(lon)
        dx = -sinl * e - sinp * cosl * n + cosp * cosl * u
        dy = cosl * e - sinp * sinl * n + cosp * sinl * u
        dz = cosp * n + sinp * u
        return dx, dy, dz

 # ----  geodist() ----
    @staticmethod
    def geodist(rs: np.ndarray, rr: np.ndarray) -> Tuple[float, np.ndarray]:
        """几何距离（含 Sagnac 改正）+ 接收机到卫星单位向量。

         geodist(): r + OMGE*(rs[0]*rr[1]-rs[1]*rr[0])/CLIGHT。
        """
        d = rs - rr
        r = float(np.linalg.norm(d))
        e = d / r
 #   Sagnac
        r_sagnac = r + OMGE * (rs[0] * rr[1] - rs[1] * rr[0]) / CLIGHT
        return r_sagnac, e

 # ----  satazel() ----
    @classmethod
    def satazel(cls, lat: float, lon: float, e: np.ndarray) -> Tuple[float, float]:
        """接收机到卫星单位向量 e(ECEF) -> (方位角 az[rad], 仰角 el[rad])。

         satazel(): az=atan2(enu_e, enu_n), el=asin(enu_u)。
        """
        enu = np.array(cls.ecef_to_enu(lat, lon, float(e[0]), float(e[1]), float(e[2])))
        horiz = float(np.dot(enu[:2], enu[:2]))
        az = 0.0 if horiz < 1e-12 else math.atan2(enu[0], enu[1])
        if az < 0.0:
            az += 2.0 * PI
        el = math.asin(max(-1.0, min(1.0, enu[2])))
        return az, el


__all__ = [
    "PI",
    "D2R",
    "R2D",
    "CLIGHT",
    "OMGE",
    "RE_WGS84",
    "FE_WGS84",
    "Ellipsoid",
    "WGS84",
    "WGS72",
    "geodetic_to_ecef_m",
    "ecef_to_geodetic_m",
    "geodetic_to_ecef_km",
    "ecef_to_geodetic_km",
    "CoordinateConverter",
]

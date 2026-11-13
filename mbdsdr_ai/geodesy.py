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

        WGS84 逆解用 Bowring 迭代（），1e-4 m 内收敛。
        """
        a = RE_WGS84 # rtklib.h:66
        f = FE_WGS84 # rtklib.h:67
        e2 = f * (2.0 - f) #   e2=FE_WGS84*(2-FE_WGS84)
        r2 = x * x + y * y
        rz = z # 原始 z（ 循环内恒用 r[2]）
        z = rz
        zk = 0.0
        v = a
 #   Bowring 迭代（注意 z 每轮重置为 r[2]+...，非累加）
        while abs(z - zk) >= 1e-4:
            zk = z
            sinp = z / math.sqrt(r2 + z * z)
            v = a / math.sqrt(1.0 - e2 * sinp * sinp)
            z = rz + v * e2 * sinp
 # 
        lat = math.atan(z / math.sqrt(r2)) if r2 > 1e-12 else (PI / 2.0 if z > 0 else -PI / 2.0)
        lon = math.atan2(y, x) if r2 > 1e-12 else 0.0
        h = math.sqrt(r2 + z * z) - v
        return lat, lon, h

 # ----  pos2ecef() ----
    @staticmethod
    def llh_to_ecef_m(lat: float, lon: float, h: float) -> Tuple[float, float, float]:
        """WGS-84 / 米：(lat[rad], lon[rad], h[m]) -> ECEF(m)。 pos2ecef()。

        名字里的 ``_m`` 是硬边界；卫星链请用
        ``orbit.wgs72_geodetic_to_ecef_km``（WGS-72、km）。
        """
        a = RE_WGS84
        f = FE_WGS84
        e2 = f * (2.0 - f)
        sinp = math.sin(lat); cosp = math.cos(lat)
        sinl = math.sin(lon); cosl = math.cos(lon)
        v = a / math.sqrt(1.0 - e2 * sinp * sinp) # 
        x = (v + h) * cosp * cosl # 
        y = (v + h) * cosp * sinl # 
        z = (v * (1.0 - e2) + h) * sinp # 
        return x, y, z

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
    "CoordinateConverter",
]

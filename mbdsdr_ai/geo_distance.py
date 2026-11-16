# SPDX-License-Identifier: MIT
"""球面大圆距离唯一真值源（轻量叶子：无 numpy、不 import 任何 mbdsdr_ai 模块）。

平均地球半径取 IUGG R1 = (2a + b) / 3（WGS-84）= 6371.0088 km。这是**球面
haversine 近似**的口径，与 ``geodesy`` 的 WGS-84 / WGS-72 椭球长半轴
（6378137 / 6378135 m）是不同物理量：椭球用于 ECEF 精密几何，平均半径用于
球面大圆距离。两者刻意不统一（见 geodesy 模块「不许统一成一套」的说明）。

历史上 adsb_map / location.places / new_spacetime / xastir_adapter /
acquisition 各写一份 haversine，半径取值不一（6371.0 / 6371.0088 /
6371000.0 m）。本模块收敛为唯一实现与唯一半径，五处再导出或薄包装，
公开函数签名与常量名保持不变。仿 geodesy 的叶子模式，避免与上游成环。
"""
from __future__ import annotations

import math

__all__ = [
    "MEAN_EARTH_RADIUS_KM",
    "MEAN_EARTH_RADIUS_M",
    "haversine_km",
    "haversine_m",
]

#: IUGG 平均半径 R1 = (2a + b) / 3（WGS-84），单位 km。球面大圆距离口径。
MEAN_EARTH_RADIUS_KM = 6371.0088
#: 同一半径的米制：由 km 派生，不独立取整，避免两值漂移。
MEAN_EARTH_RADIUS_M = MEAN_EARTH_RADIUS_KM * 1000.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（km）。atan2 形式，对跖点附近数值稳定。"""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2.0) ** 2)
    # 对跖点处 a 理论上=1，浮点舍入可能溢出到 1+eps，令 sqrt(1-a) 取负报错；
    # 夹到 ≤1 消除溢出（a 为非负项之和，下界不会越界）。
    a = min(1.0, a)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return MEAN_EARTH_RADIUS_KM * c


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（m）。= haversine_km × 1000，单一公式不另写一份。"""
    return haversine_km(lat1, lon1, lat2, lon2) * 1000.0
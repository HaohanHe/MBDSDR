# SPDX-License-Identifier: MIT
"""FireDetector 簇质心经度：圆均值（跨日界线正确性）。

2026-11-15 #108 背景：``sdrangel_plugins.FireDetector.detect`` 原用
``clon = sum(c.lon_deg)/n`` 线性平均簇质心经度。聚类本身用 ``_haversine_km``
（已 wrap-aware），故跨日界线热点（179°E/-179°E 赤道相距 ~222km）在足够
聚类半径下**合法成簇**，但质心经度线性平均 = (179-179)/2 = 0°（真值 180°）
——火点质心被放到地球另一侧。纬度有界 [-90,90]，线性平均正确，不动。

真实消费方：``tool_firedetector_detect`` → 注册 AI 工具 ``sdrangel_firedetector``
→ ``clusters[].lon = round(centroid_lon, 5)`` 用户可见（agent.py:514 接线）。

本文件钉住：
1. 跨日界线簇质心经度取圆均值（±180°），绝不是旧缺陷的 0°；
2. 与独立 numpy 复指数参考逐值相等（不同实现路径）；
3. 非回绕簇圆均值==线性均值（防过度修正）；纬度保持线性；
4. 质心经度域 [-180,180)；单点/空/冷点/多簇边界；
5. 用户可见工具路径读数正确。

全部固定输入，不绑墙钟/不联网。
运行：python -m pytest tests/test_firedetector_circular_centroid.py -q
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai.sdrangel_plugins import (
    FireDetector,
    tool_firedetector_detect,
    _haversine_km,
)


# ─────────────────────────── 参考与工具 ───────────────────────────

def _circ_dist(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _ref_circular_lon(lons):
    """独立圆均值：复指数均值辐角（与生产 Σsin/Σcos 不同路径）+ [-180,180) 归一化。"""
    z = np.mean(np.exp(1j * np.radians(np.asarray(lons, dtype=float))))
    return float((np.degrees(np.angle(z)) + 180.0) % 360.0 - 180.0)


def _one_cluster(lons, lats=None, cluster_km=1e6, temp=340.0):
    """把给定经度（可选纬度）造成热点并强制聚成一簇，返回该簇。"""
    if lats is None:
        lats = [0.0] * len(lons)
    pts = [(la, lo, temp) for la, lo in zip(lats, lons)]
    clusters = FireDetector(threshold_k=320.0, cluster_km=cluster_km).detect(pts)
    assert len(clusters) == 1, f"应聚成 1 簇，got {len(clusters)}"
    return clusters[0]


# ─────────────────────────── 1. 跨日界线正确性 ───────────────────────────

def test_dateline_cluster_realistic():
    """179°E/-179°E 相距 222km，cluster_km=300 合法成簇，质心应在日界线。"""
    d = _haversine_km(0.0, 179.0, 0.0, -179.0)
    assert 200.0 < d < 240.0  # haversine wrap-aware：短弧 2° 而非 358°
    pts = [(0.0, 179.0, 340.0), (0.0, -179.0, 350.0)]
    clusters = FireDetector(threshold_k=320.0, cluster_km=300.0).detect(pts)
    assert len(clusters) == 1
    c = clusters[0]
    assert _circ_dist(c.centroid_lon, 180.0) < 1e-6
    assert c.size == 2 and c.max_temp_k == 350.0
    assert abs(c.centroid_lat) < 1e-9


def test_dateline_centroid_not_zero():
    """显式钉死旧缺陷：179/-179 线性平均得 0°，圆均值绝不能是 0°。"""
    c = _one_cluster([179.0, -179.0])
    assert _circ_dist(c.centroid_lon, 0.0) > 170.0


@pytest.mark.parametrize("lons", [
    [179.0, -179.0],
    [170.0, -170.0],
    [170.0, -170.0, 180.0],
    [100.0, -100.0],
    [-179.0, 179.0, 178.0, -178.0],
])
def test_dateline_centroid_near_180(lons):
    c = _one_cluster(lons)
    assert _circ_dist(c.centroid_lon, 180.0) < 1e-6, f"{lons}: got {c.centroid_lon}"


# ─────────────────────────── 2. 独立参考逐值相等 ───────────────────────────

@pytest.mark.parametrize("lons", [
    [179.0, -179.0],
    [170.0, -170.0, 180.0],
    [10.0, 20.0, 30.0],
    [-179.0, 179.0, 178.0],
    [0.0, 1.0, 2.0],
    [45.0, 45.0, 45.0],
    [-10.0, -20.0, -30.0],
])
def test_centroid_matches_independent_reference(lons):
    c = _one_cluster(lons)
    assert _circ_dist(c.centroid_lon, _ref_circular_lon(lons)) < 1e-6


# ─────────────────────────── 3. 非回绕等价 + 纬度线性 ───────────────────────────

@pytest.mark.parametrize("lons", [
    [10.0, 20.0, 30.0],
    [-30.0, -10.0, 10.0],
    [0.0, 1.0, 2.0, 3.0],
    [11.0, 11.0, 11.01],
])
def test_non_dateline_equals_linear(lons):
    c = _one_cluster(lons)
    lin = sum(lons) / len(lons)
    assert abs(c.centroid_lon - lin) < 1e-9, f"{lons}: got {c.centroid_lon} linear {lin}"


def test_latitude_stays_linear():
    """纬度有界，质心纬度必须是线性平均（不被圆化）。"""
    lats = [10.0, 20.0, 30.0]
    c = _one_cluster([0.0, 0.0, 0.0], lats=lats)
    assert abs(c.centroid_lat - 20.0) < 1e-9


# ─────────────────────────── 4. 域 + 边界 ───────────────────────────

@pytest.mark.parametrize("lons", [
    [179.0, -179.0], [170.0, -170.0], [10.0, 20.0], [0.0], [180.0, 180.0],
])
def test_centroid_lon_domain(lons):
    c = _one_cluster(lons)
    assert -180.0 <= c.centroid_lon < 180.0, f"{lons}: {c.centroid_lon} 越域"


def test_single_hotspot_identity():
    c = _one_cluster([123.456], lats=[-33.0])
    assert abs(c.centroid_lon - 123.456) < 1e-9
    assert abs(c.centroid_lat - (-33.0)) < 1e-9
    assert c.size == 1


def test_size_and_maxtemp_preserved():
    pts = [(0.0, 179.0, 340.0), (0.0, -179.0, 390.0), (0.0, 179.5, 350.0)]
    clusters = FireDetector(threshold_k=320.0, cluster_km=300.0).detect(pts)
    assert len(clusters) == 1
    assert clusters[0].size == 3
    assert clusters[0].max_temp_k == 390.0


def test_cold_points_filtered():
    pts = [(0.0, 179.0, 290.0), (0.0, -179.0, 300.0)]  # 全低于阈值 320
    assert FireDetector(threshold_k=320.0, cluster_km=300.0).detect(pts) == []


def test_empty_input():
    assert FireDetector().detect([]) == []


def test_two_separate_clusters_dateline_and_far():
    """日界线簇 + 远处独立点 → 2 簇，各自质心正确。"""
    pts = [(0.0, 179.0, 340.0), (0.0, -179.0, 350.0), (0.0, 0.0, 360.0)]
    clusters = FireDetector(threshold_k=320.0, cluster_km=300.0).detect(pts)
    assert len(clusters) == 2
    by_size = {c.size: c for c in clusters}
    assert _circ_dist(by_size[2].centroid_lon, 180.0) < 1e-6
    assert abs(by_size[1].centroid_lon - 0.0) < 1e-9


# ─────────────────────────── 5. 用户可见工具路径 ───────────────────────────

def test_tool_path_dateline():
    res = tool_firedetector_detect({
        "points": [{"lat": 0.0, "lon": 179.0, "temp_k": 340.0},
                   {"lat": 0.0, "lon": -179.0, "temp_k": 350.0}],
        "threshold_k": 320.0, "cluster_km": 300.0,
    })
    assert res["cluster_count"] == 1
    lon = res["clusters"][0]["lon"]
    assert _circ_dist(lon, 180.0) < 1e-4, f"工具输出应 ±180°, got {lon}"
    assert -180.0 <= lon < 180.0


def test_tool_path_non_dateline():
    res = tool_firedetector_detect({
        "points": [{"lat": 45.0, "lon": 11.0, "temp_k": 340.0},
                   {"lat": 45.01, "lon": 11.0, "temp_k": 350.0},
                   {"lat": 45.0, "lon": 11.01, "temp_k": 345.0}],
        "threshold_k": 320.0, "cluster_km": 5.0,
    })
    assert res["cluster_count"] == 1
    lon = res["clusters"][0]["lon"]
    assert abs(lon - round((11.0 + 11.0 + 11.01) / 3, 5)) < 1e-5
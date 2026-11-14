# SPDX-License-Identifier: MIT
"""轻量时空要素层的契约钉扎（纯计算，不碰 Qt）。

独立 oracle：期望值全部是规范/几何硬编码，不从产品代码推导。
对应 QGIS 源码机制：QgsTemporalRange 区间语义、usedTemporalRanges 的
区间合并、TemporalIntervalMatchMethod 的最近之前匹配、RedrawLayerOnly
「随时间重绘但不过滤要素」。
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from mbdsdr_ai import spacetime_features as sf

_REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------- 区间语义
def test_infinite_and_half_infinite_are_not_empty():
    assert sf.TimeInterval().is_infinite
    assert not sf.TimeInterval().is_empty
    assert not sf.TimeInterval(None, 100.0).is_empty
    assert not sf.TimeInterval(100.0, None).is_empty
    assert sf.TimeInterval(200.0, 100.0).is_empty


def test_instant_only_empty_when_both_ends_excluded():
    assert not sf.TimeInterval(5.0, 5.0).is_empty
    assert sf.TimeInterval(5.0, 5.0).is_instant
    assert not sf.TimeInterval(5.0, 5.0, True, False).is_empty
    assert not sf.TimeInterval(5.0, 5.0, False, True).is_empty
    assert sf.TimeInterval(5.0, 5.0, False, False).is_empty


def test_contains_respects_boundary_inclusion():
    r = sf.TimeInterval(10.0, 20.0)
    assert r.contains(10.0) and r.contains(20.0) and r.contains(15.0)
    assert not r.contains(9.9) and not r.contains(20.1)
    assert not sf.TimeInterval(10.0, 20.0, False, True).contains(10.0)
    assert not sf.TimeInterval(10.0, 20.0, True, False).contains(20.0)
    assert sf.TimeInterval(None, 10.0).contains(-1e18)
    assert sf.TimeInterval(10.0, None).contains(1e18)


# --------------------------------------------------------------- 球面几何
def test_great_circle_angle_known_values():
    assert sf.great_circle_angle_deg(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0)
    assert sf.great_circle_angle_deg(0.0, 0.0, 0.0, 90.0) == pytest.approx(90.0)
    assert sf.great_circle_angle_deg(0.0, 179.9, 0.0, -179.9) == pytest.approx(
        0.2, abs=1e-9)
    assert sf.great_circle_angle_deg(0.0, 0.0, 0.0, 180.0) == pytest.approx(
        180.0, abs=1e-9)


def test_footprint_contains_is_center_angle_based():
    fp = sf.Footprint(39.9, 116.4, 20.0)
    assert fp.contains(39.9, 116.4)
    assert fp.contains(45.0, 116.4)
    assert not fp.contains(0.0, 0.0)
    # 同纬度 20° 经差只有约 15.3° 中心角（乘 cos φ），不能拿经差当中心角。
    assert 15.0 < fp.angle_to(39.9, 136.4) < 16.0
    assert fp.angle_to(0.0, 0.0) > 20.0
    assert not fp.contains(39.9, 150.0)


# --------------------------------------------------------------- 区间表
def test_merge_ranges_keeps_gaps():
    assert sf.merge_ranges([(0, 10), (5, 15), (30, 40)]) == [(0, 15), (30, 40)]
    assert sf.merge_ranges([]) == []
    assert sf.gaps_between([(0, 10), (30, 40)]) == ((10.0, 30.0),)
    assert sf.gaps_between([(0, 10)]) == ()


def test_match_closest_before_never_invents_a_frame():
    samples = [100.0, 200.0, 300.0]
    assert sf.match_closest_before(250.0, samples) == 200.0
    assert sf.match_closest_before(200.0, samples) == 200.0
    assert sf.match_closest_before(99.0, samples) is None
    assert sf.match_closest_before(1e9, []) is None


# --------------------------------------------------------------- 查询
def _collection():
    return sf.FeatureCollection([
        sf.SpatialFeature("near", "ground_station", 39.9, 116.4, label="本站"),
        sf.SpatialFeature("far", "beacon", 0.0, 0.0, label="远处"),
    ])


def test_query_at_splits_inside_outside_and_reports_reason():
    r = _collection().query_at(
        1000.0, sf.Footprint(39.9, 116.4, 20.0),
        mode=sf.TemporalMode.RANGE_FILTER, basis="simulated",
        has_time_data=True)
    assert r.counts == {"inside": 1, "outside": 1}
    assert [i.feature.feature_id for i in r.inside] == ["near"]
    assert all(i.reason for i in r.items)
    assert r.summary() == "2 个要素 · 1 在圈内 · 0 未知"


def test_missing_footprint_is_unknown_not_outside():
    r = _collection().query_at(1000.0, None, has_time_data=True)
    assert r.unknown_reason == "未获得覆盖圈 · 不推断"
    assert len(r.unknown) == 2
    assert not r.inside


def test_no_time_data_is_honest_empty_state():
    r = _collection().query_at(0.0, None, has_time_data=False)
    assert "未设定时间范围" in r.unknown_reason
    assert "不推断" in r.summary()
    assert r.basis == "unknown"


def test_footprint_age_is_reported_not_hidden():
    """时间跳了但圈是旧时刻算的：必须能算出年龄，角标才不会骗人。"""
    r = _collection().query_at(
        2000.0, sf.Footprint(39.9, 116.4, 20.0, at_unix=1000.0),
        has_time_data=True)
    assert r.footprint_age_s == 1000.0
    assert _collection().query_at(
        2000.0, None, has_time_data=True).footprint_age_s is None


def test_validity_interval_filters_by_time_not_by_guess():
    col = sf.FeatureCollection([
        sf.SpatialFeature("win", "beacon", 39.9, 116.4,
                          valid=sf.TimeInterval(100.0, 200.0)),
    ])
    fp = sf.Footprint(39.9, 116.4, 20.0)
    assert col.query_at(150.0, fp).counts == {"inside": 1}
    assert col.query_at(50.0, fp).counts == {"not_yet": 1}
    assert col.query_at(250.0, fp).counts == {"expired": 1}


def test_redraw_only_never_filters_anything():
    """天空档：全集恒显，时间/空间都不许筛掉要素。"""
    col = sf.FeatureCollection([
        sf.SpatialFeature("a", "star", 39.9, 116.4),
        sf.SpatialFeature("b", "star", 0.0, 0.0,
                          valid=sf.TimeInterval(100.0, 200.0)),
    ])
    r = col.query_at(9999.0, sf.Footprint(39.9, 116.4, 0.001),
                     mode=sf.TemporalMode.REDRAW_ONLY, has_time_data=True)
    assert r.counts == {"shown": 2}
    assert len(r.highlighted) == 2
    assert all("不筛要素" in i.reason for i in r.items)


def test_none_mode_has_no_time_semantics():
    r = _collection().query_at(1000.0, None,
                               mode=sf.TemporalMode.NONE,
                               has_time_data=False)
    assert r.counts == {"shown": 2}


def test_feature_geometry_is_validated():
    with pytest.raises(ValueError):
        sf.SpatialFeature("", "x", 0.0, 0.0)
    with pytest.raises(ValueError):
        sf.SpatialFeature("x", "x", 91.0, 0.0)
    with pytest.raises(ValueError):
        sf.SpatialFeature("x", "x", 0.0, 181.0)


# --------------------------------------------------------------- 广播
class _FakeEngine:
    def __init__(self, t=123.0, following=True):
        self.t, self.following = t, following

    def now_unix(self):
        return self.t

    def is_following_system(self):
        return self.following


def test_bus_dedupes_and_replays_last_to_new_subscriber():
    bus = sf.TemporalBus()
    seen = []
    bus.subscribe(seen.append)
    ctx = sf.TemporalContext(1.0, sf.TimeInterval(1.0, 1.0),
                             basis="real", has_time_data=True)
    assert bus.publish(ctx) is True
    assert bus.publish(ctx) is False
    later = []
    bus.subscribe(later.append)
    assert later == [ctx]


def test_basis_marks_simulated_time_honestly():
    bus = sf.TemporalBus()
    real = bus.publish_from_engine(_FakeEngine(following=True))
    sim = bus.publish_from_engine(_FakeEngine(t=124.0, following=False))
    assert real.basis == "real" and not real.is_simulated
    assert sim.basis == "simulated" and sim.is_simulated


def test_broken_engine_yields_unknown_never_a_guessed_time():
    bus = sf.TemporalBus()
    ctx = bus.publish_from_engine(object())
    assert ctx == sf.TemporalContext.unknown()
    assert ctx.at_unix == 0.0 and not ctx.has_time_data


# --------------------------------------------------------------- 站址
def test_station_upsert_and_clear_never_invents_coordinates():
    col = sf.FeatureCollection()
    f = sf.upsert_ground_station(39.9, 116.4, source="gnss", collection=col)
    assert f.kind == "ground_station" and f.label == "本站"
    assert f.properties == {"source": "gnss"}
    assert f.valid.is_infinite
    assert sf.upsert_ground_station(None, None, collection=col) is None
    assert len(col) == 0


def test_module_has_no_hardcoded_site_coordinates():
    """产品模块不许内置站点/城市坐标；要素一律由真实来源注入。"""
    src = (_REPO / "mbdsdr_ai" / "spacetime_features.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    literals = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    }
    assert 39.9 not in literals and 116.4 not in literals
    assert "北京" not in src and "长春" not in src and "青岛" not in src

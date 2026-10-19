# SPDX-License-Identifier: MIT
"""
position_service.py 位置聚合/轨迹/GeoJSON——确定性离线测试

不联网、无硬件；时钟注入可控时间：
  - report 聚合最新位置（同 source 覆盖）、轨迹追加、超 max_track 修剪；
  - timestamp 缺省用注入时钟补，显式给定不覆盖；
  - get/all_latest/track/count 口径；
  - remove_expired 按 TTL 清理（边界：恰好等于 TTL 不清理，严格大于才清）；
  - 订阅者在 report 时被回调，且回调抛异常不传染（诚实容错）；
  - geojson：Point 坐标序为 [lon, lat]（GeoJSON 规范）、轨迹 >=2 点才出
    LineString、properties 合并 metadata；
  - 线程安全冒烟：多线程 report 不丢（RLock 生效）。

运行：python3 -m pytest mbdsdr_ai/tests/test_position_service.py -v
"""
from __future__ import annotations

import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from mbdsdr_ai.position_service import PositionReport, PositionService  # noqa: E402


class FakeClock:
    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _rep(source, lat, lon, **kw):
    return PositionReport(source=source, source_type="adsb",
                          lat=lat, lon=lon, **kw)


# ---------------------------------------------------------------- 基本聚合

def test_report_and_get_latest():
    svc = PositionService(clock=FakeClock())
    svc.report(_rep("ICAO1", 10.0, 20.0))
    svc.report(_rep("ICAO1", 11.0, 21.0))            # 同 source 覆盖
    got = svc.get("ICAO1")
    assert got is not None and (got.lat, got.lon) == (11.0, 21.0)
    assert svc.count() == 1


def test_timestamp_defaults_to_injected_clock():
    clk = FakeClock(5000.0)
    svc = PositionService(clock=clk)
    svc.report(_rep("A", 1.0, 2.0))
    assert svc.get("A").timestamp == 5000.0
    clk.advance(10.0)
    svc.report(_rep("A", 1.5, 2.5, timestamp=4242.0))   # 显式时间戳不被覆盖
    assert svc.get("A").timestamp == 4242.0


def test_all_latest_returns_copy():
    svc = PositionService(clock=FakeClock())
    svc.report(_rep("A", 1.0, 2.0))
    svc.report(_rep("B", 3.0, 4.0))
    latest = svc.all_latest()
    assert {r.source for r in latest} == {"A", "B"}
    latest.clear()                                     # 改副本不影响内部
    assert svc.count() == 2


def test_track_appends_and_trims_to_max():
    svc = PositionService(clock=FakeClock(), max_track_points=3)
    for i in range(5):
        svc.report(_rep("A", float(i), float(i)))
    tr = svc.track("A")
    assert tr == [(2.0, 2.0), (3.0, 3.0), (4.0, 4.0)]  # 只留最新 3 点


def test_track_unknown_source_empty():
    svc = PositionService(clock=FakeClock())
    assert svc.track("nope") == []


# ---------------------------------------------------------------- TTL 清理

def test_remove_expired_by_ttl():
    clk = FakeClock()
    svc = PositionService(ttl_s=10.0, clock=clk)
    svc.report(_rep("old", 1.0, 1.0))
    clk.advance(10.5)                                   # 恰好超过 TTL
    svc.report(_rep("new", 2.0, 2.0))
    expired = svc.remove_expired()
    assert expired == ["old"]
    assert svc.get("old") is None
    assert svc.get("new") is not None


def test_remove_expired_boundary_equal_not_expired():
    clk = FakeClock()
    svc = PositionService(ttl_s=10.0, clock=clk)
    svc.report(_rep("edge", 1.0, 1.0))
    clk.advance(10.0)                                   # now-ts == TTL，严格大于才清
    assert svc.remove_expired() == []
    assert svc.count() == 1


# ---------------------------------------------------------------- 订阅

def test_subscriber_receives_reports():
    svc = PositionService(clock=FakeClock())
    got = []
    svc.subscribe(lambda r: got.append(r.source))
    svc.report(_rep("A", 1.0, 2.0))
    svc.report(_rep("B", 3.0, 4.0))
    assert got == ["A", "B"]


def test_subscriber_exception_does_not_propagate():
    """一个订阅者炸了不影响其他人，也不影响 report 本身（诚实容错）。"""
    svc = PositionService(clock=FakeClock())
    ok = []

    def boom(_r):
        raise RuntimeError("bad subscriber")

    svc.subscribe(boom)
    svc.subscribe(lambda r: ok.append(r.source))
    svc.report(_rep("A", 1.0, 2.0))                     # 不抛
    assert ok == ["A"]
    assert svc.count() == 1


# ---------------------------------------------------------------- GeoJSON

def test_geojson_point_uses_lon_lat_order():
    svc = PositionService(clock=FakeClock())
    svc.report(_rep("A", 45.0, 125.0))                  # lat=45, lon=125
    gj = svc.geojson()
    assert gj["type"] == "FeatureCollection"
    pts = [f for f in gj["features"]
           if f["geometry"]["type"] == "Point"]
    assert len(pts) == 1
    assert pts[0]["geometry"]["coordinates"] == [125.0, 45.0]   # [lon, lat]
    assert pts[0]["properties"]["source"] == "A"
    assert pts[0]["properties"]["type"] == "adsb"


def test_geojson_linestring_only_with_two_points():
    svc = PositionService(clock=FakeClock())
    svc.report(_rep("A", 1.0, 2.0))
    svc.report(_rep("A", 3.0, 4.0))
    lines = [f for f in svc.geojson()["features"]
             if f["geometry"]["type"] == "LineString"]
    assert len(lines) == 1
    assert lines[0]["geometry"]["coordinates"] == [[2.0, 1.0], [4.0, 3.0]]
    assert lines[0]["properties"]["role"] == "track"


def test_geojson_single_point_no_linestring():
    svc = PositionService(clock=FakeClock())
    svc.report(_rep("A", 1.0, 2.0))
    assert not [f for f in svc.geojson()["features"]
                if f["geometry"]["type"] == "LineString"]


def test_geojson_metadata_merged_into_properties():
    svc = PositionService(clock=FakeClock())
    svc.report(_rep("A", 1.0, 2.0, metadata={"callsign": "BI1AAA", "squawk": "7000"}))
    props = [f for f in svc.geojson()["features"]
             if f["geometry"]["type"] == "Point"][0]["properties"]
    assert props["callsign"] == "BI1AAA"
    assert props["squawk"] == "7000"


# ---------------------------------------------------------------- 并发冒烟

def test_concurrent_reports_no_loss():
    svc = PositionService(clock=FakeClock(), max_track_points=10000)
    n_threads, n_each = 8, 200

    def worker(k):
        for i in range(n_each):
            svc.report(_rep(f"S{k}", float(i), float(i)))

    threads = [threading.Thread(target=worker, args=(k,))
               for k in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert svc.count() == n_threads
    for k in range(n_threads):
        assert len(svc.track(f"S{k}")) == n_each

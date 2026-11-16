# SPDX-License-Identifier: MIT
"""位置中枢：真数据进、去重、桥接成时空要素（纯计算，无 Qt、无硬件）。

背景：PositionService 曾是「全仓零消费」的孤岛——ADS-B 合并出的位置
从不上报、要素层也吃不到。钉住的接线：
  AircraftTracker.update（唯一出口）→ ingest_aircraft（同位置去重）
  → PositionService → upsert_position_report → FeatureCollection。
没有任何位置时一个点都不许画。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.adsb_map import Aircraft, AircraftTracker  # noqa: E402
from mbdsdr_ai.position_service import (  # noqa: E402
    get_position_service, ingest_aircraft, report_from_aircraft,
    reset_position_service)
from mbdsdr_ai.spacetime_features import (  # noqa: E402
    FeatureCollection, upsert_position_report)


@pytest.fixture()
def svc():
    return reset_position_service()


def test_report_from_aircraft_converts_units(svc):
    ac = Aircraft(icao_hex="abc123", callsign="HELLO",
                  lat=39.9, lon=116.4, altitude_ft=10000.0,
                  groundspeed_kt=100.0, track_deg=90.0, last_seen=1763000000.0)
    rep = report_from_aircraft(ac)
    assert rep.source == "ABC123"
    assert rep.source_type == "adsb"
    assert rep.lat == pytest.approx(39.9) and rep.lon == pytest.approx(116.4)
    assert rep.altitude_m == pytest.approx(10000.0 * 0.3048)
    assert rep.speed_mps == pytest.approx(100.0 * 0.514444)
    assert rep.heading_deg == 90.0
    assert rep.timestamp == pytest.approx(1763000000.0)
    assert rep.metadata["callsign"] == "HELLO"


def test_report_from_aircraft_without_position_is_none(svc):
    ac = Aircraft(icao_hex="N0POS", callsign="GHOST")  # lat/lon=None
    assert report_from_aircraft(ac) is None
    assert ingest_aircraft(ac) is None
    assert svc.count() == 0, "没位置不许上报"


def test_ingest_dedups_same_position(svc):
    ac = Aircraft(icao_hex="DUP01", lat=10.0, lon=20.0, last_seen=1.0)
    assert ingest_aircraft(ac) is not None
    ac.last_seen = 2.0  # 识别帧刷新但位置没动
    assert ingest_aircraft(ac) is None
    assert svc.track("DUP01") == [(10.0, 20.0)], "同位置不得刷轨迹"
    ac.lat, ac.lon, ac.last_seen = 10.1, 20.1, 3.0
    assert ingest_aircraft(ac) is not None
    assert svc.track("DUP01") == [(10.0, 20.0), (10.1, 20.1)]


def test_tracker_update_is_the_single_choke_point(svc):
    """跟踪器一合并出位置就上报——不接这根线，地图上就是空的。"""
    tr = AircraftTracker()
    tr.update({"icao": "feed1", "lat": 45.0, "lon": 120.0})
    latest = svc.all_latest()
    assert len(latest) == 1
    assert latest[0].source == "FEED1"
    assert latest[0].source_type == "adsb"
    # 无位置帧绝不上报
    tr2 = AircraftTracker()
    tr2.update({"icao": "feed2", "callsign": "NOPOS"})
    assert svc.get("FEED2") is None


def test_upsert_position_report_bridges_to_features(svc):
    ac = Aircraft(icao_hex="MAP01", callsign="BRIDGE",
                  lat=31.2, lon=121.5, last_seen=1763000000.0)
    rep = ingest_aircraft(ac)
    col = FeatureCollection()
    feat = upsert_position_report(
        rep, track=tuple(svc.track(rep.source)), ttl_s=120.0, collection=col)
    assert feat is not None
    assert feat.feature_id == "pos:MAP01"
    assert feat.kind == "adsb"
    assert feat.label == "BRIDGE"
    assert (feat.lat, feat.lon) == (31.2, 121.5)
    assert feat.valid.contains(1763000000.0), "TTL 窗口内算活着"
    assert not feat.valid.contains(1763000000.0 + 121.0), "过期不算圈内"
    assert len(col) == 1


def test_upsert_rejects_positionless_report(svc):
    from mbdsdr_ai.position_service import PositionReport
    rep = PositionReport(source="X", source_type="adsb",
                         lat=0.0, lon=0.0, timestamp=0.0)
    rep.lat = None  # type: ignore[assignment]
    assert upsert_position_report(rep, collection=FeatureCollection()) is None


def test_reset_isolates_tests(svc):
    ingest_aircraft(Aircraft(icao_hex="ISO1", lat=1.0, lon=2.0, last_seen=0.0))
    assert get_position_service().count() == 1
    fresh = reset_position_service()
    assert fresh.count() == 0
    assert get_position_service() is fresh


# ======================================================================
# APRS 真源（2026-11-16）：造帧 → 解析 → 中枢，同一条上报口径
# ======================================================================
def test_report_from_aprs_converts_units(svc):
    from mbdsdr_ai.position_service import report_from_aprs
    rep = report_from_aprs({
        "source": "KJ6ABC", "source_ssid": 7,
        "latitude": 43.9, "longitude": 125.3,
        "speed": 100.0, "altitude": 10000.0, "course": 270.0,
        "symbol": "/>",
    }, now=1763000000.0)
    assert rep.source == "KJ6ABC-7"
    assert rep.source_type == "aprs"
    assert rep.speed_mps == pytest.approx(100.0 * 0.514444)
    assert rep.altitude_m == pytest.approx(10000.0 * 0.3048)
    assert rep.heading_deg == 270.0
    assert rep.metadata["protocol"] == "aprs"


def test_report_from_aprs_without_position_is_none(svc):
    from mbdsdr_ai.position_service import report_from_aprs
    assert report_from_aprs({"source": "N0POS", "latitude": None,
                            "longitude": None}) is None


def test_aprs_real_codec_chain_feeds_the_hub(svc):
    """真造帧（ax25）→ 真解析（aprs_parser）→ 位置中枢，全链无测试替身。"""
    from mbdsdr_ai.ax25 import build_aprs_position_frame
    from mbdsdr_ai.aprs_parser import parse_aprs_frame

    frame = build_aprs_position_frame("BG4TEST-9", 34.25, 117.15)
    parsed = parse_aprs_frame(frame)
    assert parsed["latitude"] == pytest.approx(34.25, abs=1e-3)
    assert parsed["longitude"] == pytest.approx(117.15, abs=1e-3)

    got = svc.all_latest()
    assert len(got) == 1, "APRS 位置必须经解析咽喉上报"
    assert got[0].source_type == "aprs"
    assert got[0].source.startswith("BG4TEST")
    # 同一帧再解析不刷轨迹（同位置去重）
    parse_aprs_frame(frame)
    assert svc.track(got[0].source) == [(parsed["latitude"], parsed["longitude"])] or \
        len(svc.track(got[0].source)) == 1


def test_aprs_positionless_frame_reports_nothing(svc):
    from mbdsdr_ai.aprs_parser import parse_aprs_frame
    from mbdsdr_ai.ax25 import AX25Frame

    frame = AX25Frame()
    frame.source, frame.source_ssid = "N0POS", 0
    frame.destination, frame.dest_ssid = "APRS", 0
    frame.control, frame.pid = 0x03, 0xF0
    frame.info = b":HELLO   :hello{00"
    parsed = parse_aprs_frame(frame)
    assert parsed["latitude"] is None
    assert svc.count() == 0, "没位置的帧绝不造点"

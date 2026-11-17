# SPDX-License-Identifier: MIT
"""运行时数据源快照（Task 70-73）的守卫。

只用假对象：不开串口、不启线程、不联网、不碰真实硬件。

覆盖：
1. GNSS 在线 → 时间/站址来源均为 gnss，字段完整；
2. GNSS 断开 → 回退来源明确，lat/lon 不被伪造（不拿旧 fix 冒充当前站址）；
3. 无 SDR → ``sdr.connected=false`` + 原因，时间/天空字段仍可用；
4. 控制器 / Web ``/status`` / CLI ``--status`` 三处序列化出的
   ``runtime_sources`` 字段一致；
5. ``sky_health.health_snapshot`` 只透传该快照，不另造第二份时间/站址。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.core.controller import SDRController  # noqa: E402
from mbdsdr_ai.core.cli import run  # noqa: E402
from mbdsdr_ai.gnss_timestamps import nmea_log_to_utc_datetime  # noqa: E402
from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402
from mbdsdr_ai.serial_gnss import nmea_checksum  # noqa: E402
from mbdsdr_ai.sky_health import health_snapshot  # noqa: E402
from mbdsdr_ai.web.server import WebServer  # noqa: E402

FIXED_NOW = 1_700_000_000.0
FIX_LAT = 48.1173
FIX_LON = 11.5167


def _sentence(body: str) -> str:
    return f"${body}*{nmea_checksum(body):02X}"


# 有效 RMC：2023-09-23 12:35:19 UTC（date=230923）
RMC_VALID = _sentence(
    "GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230923,003.1,W")
RMC_UTC = nmea_log_to_utc_datetime([RMC_VALID])


class FakePosition:
    def __init__(self, source="real", lat=None, lon=None, alt=0.0,
                 timestamp=FIXED_NOW):
        self.source = source
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.timestamp = timestamp


class FakeGNSS:
    """最小 GNSS 监视器替身（无串口、无线程）。"""

    def __init__(self, lat=FIX_LAT, lon=FIX_LON):
        self.connected = True
        self.port = "COM7"
        self._position = FakePosition(lat=lat, lon=lon, alt=545.4)
        self._rmc = RMC_VALID

    def is_connected(self) -> bool:
        return self.connected

    def get_position(self):
        if not self.connected:
            return FakePosition(source="none", timestamp=None)
        return self._position

    def get_last_rmc(self):
        return self._rmc if self.connected else None


class FakeTimeEngine:
    def __init__(self):
        self.unix = None
        self.rate = 1.0
        self.set_unix_calls = []
        self.set_time_now_calls = 0

    def set_unix(self, ts):
        self.unix = float(ts)
        self.rate = 0.0
        self.set_unix_calls.append(float(ts))

    def resume(self, rate=1.0):
        self.rate = float(rate)

    def set_time_now(self):
        self.unix = None
        self.rate = 1.0
        self.set_time_now_calls += 1

    def is_following_system(self) -> bool:
        return self.unix is None

    def now_unix(self) -> float:
        return self.unix if self.unix is not None else 0.0


def _ctrl(tmp_path) -> SDRController:
    return SDRController(backend=None, config_dir=str(tmp_path))


def _assert_gnss_fields(snap: dict) -> None:
    for key in ("connected", "port", "fix", "time_source", "precision",
                "site_source", "lat_deg", "lon_deg", "last_update_utc",
                "reason"):
        assert key in snap["gnss"], f"gnss 缺字段 {key}: {snap['gnss']}"
    for key in ("source", "precision"):
        assert key in snap["time"], f"time 缺字段 {key}: {snap['time']}"
    for key in ("source", "lat_deg", "lon_deg"):
        assert key in snap["site"], f"site 缺字段 {key}: {snap['site']}"
    for key in ("connected", "device", "reason", "last_update_utc"):
        assert key in snap["sdr"], f"sdr 缺字段 {key}: {snap['sdr']}"


def test_gnss_online_marks_time_and_site_as_gnss(tmp_path):
    ctrl = _ctrl(tmp_path)
    engine = FakeTimeEngine()
    sources = RuntimeSources(sdr=ctrl, gnss=FakeGNSS(),
                             time_engine=engine, station=ctrl)

    snap = sources.sync_gnss(now_utc=FIXED_NOW)
    _assert_gnss_fields(snap)

    assert snap["gnss"]["connected"] is True
    assert snap["gnss"]["fix"] is True
    assert snap["gnss"]["port"] == "COM7"
    assert snap["gnss"]["time_source"] == "nmea"
    assert snap["gnss"]["precision"]
    assert snap["gnss"]["site_source"] == "gnss"
    assert snap["gnss"]["lat_deg"] == pytest.approx(FIX_LAT)
    assert snap["gnss"]["lon_deg"] == pytest.approx(FIX_LON)

    assert snap["time"]["source"] == "gnss"
    assert snap["time"]["time_source"] == "nmea"
    assert snap["site"]["source"] == "gnss"
    assert snap["site"]["lat_deg"] == pytest.approx(FIX_LAT)
    assert snap["site"]["lon_deg"] == pytest.approx(FIX_LON)

    assert RMC_UTC is not None
    assert engine.set_unix_calls == [pytest.approx(RMC_UTC.timestamp())]
    assert engine.is_following_system() is False


def test_gnss_offline_falls_back_without_forging_coordinates(tmp_path):
    ctrl = _ctrl(tmp_path)
    ctrl.set_ground_station(45.0, 120.0, 10.0, source="user")
    monitor = FakeGNSS()
    engine = FakeTimeEngine()
    sources = RuntimeSources(sdr=ctrl, gnss=monitor,
                             time_engine=engine, station=ctrl)

    online = sources.sync_gnss(now_utc=FIXED_NOW)
    assert online["site"]["source"] == "gnss"
    assert engine.set_unix_calls

    monitor.connected = False
    offline = sources.sync_gnss(now_utc=FIXED_NOW + 1.0)
    _assert_gnss_fields(offline)

    # GNSS 自己的坐标必须清空，不能拿旧 fix 冒充当前定位
    assert offline["gnss"]["connected"] is False
    assert offline["gnss"]["fix"] is False
    assert offline["gnss"]["lat_deg"] is None
    assert offline["gnss"]["lon_deg"] is None
    assert offline["gnss"]["time_source"] == "none"

    # 时间：回退系统时钟，绝不标 gnss
    assert offline["time"]["source"] == "system"
    assert offline["time"]["time_source"] == "none"
    assert engine.set_time_now_calls == 1
    assert engine.is_following_system() is True

    # 站址：回退到接线前的手填值
    assert offline["site"]["source"] == "manual"
    assert offline["site"]["lat_deg"] == pytest.approx(45.0)
    assert offline["site"]["lon_deg"] == pytest.approx(120.0)


def test_gnss_offline_without_manual_site_reports_none(tmp_path):
    ctrl = _ctrl(tmp_path)
    monitor = FakeGNSS()
    engine = FakeTimeEngine()
    sources = RuntimeSources(sdr=ctrl, gnss=monitor,
                             time_engine=engine, station=ctrl)

    sources.sync_gnss(now_utc=FIXED_NOW)
    monitor.connected = False
    snap = sources.sync_gnss(now_utc=FIXED_NOW + 1.0)

    assert snap["site"]["source"] == "none"
    assert snap["site"]["lat_deg"] is None
    assert snap["site"]["lon_deg"] is None
    assert snap["gnss"]["lat_deg"] is None


def test_no_sdr_reports_reason_but_keeps_sky_time_fields(
        monkeypatch, tmp_path):
    ctrl = _ctrl(tmp_path)
    ctrl.set_gnss_monitor(FakeGNSS())
    engine = FakeTimeEngine()
    monkeypatch.setattr("mbdsdr_ai.new_spacetime.get_time_engine",
                        lambda: engine)

    snap = ctrl.sync_runtime_sources()
    _assert_gnss_fields(snap)

    assert snap["sdr"]["connected"] is False
    assert snap["sdr"]["reason"], "无 SDR 时必须给出原因"
    assert snap["sdr"]["device"] is None

    # 天空/授时相关字段仍可用
    assert snap["gnss"]["connected"] is True
    assert snap["time"]["source"] == "gnss"
    assert snap["site"]["source"] == "gnss"


def test_controller_web_cli_runtime_sources_are_identical(
        monkeypatch, tmp_path, capsys):
    ctrl = _ctrl(tmp_path)
    ctrl.set_gnss_monitor(FakeGNSS())
    engine = FakeTimeEngine()
    monkeypatch.setattr("mbdsdr_ai.new_spacetime.get_time_engine",
                        lambda: engine)
    ctrl.sync_runtime_sources()

    controller = ctrl.get_status().to_dict()["runtime_sources"]
    web = WebServer(backend=ctrl).status()["runtime_sources"]

    rc = run(["--status"], controller=ctrl)
    out = capsys.readouterr().out
    start = out.find("{")
    assert start >= 0, f"CLI 未输出 JSON：{out!r}"
    cli = json.loads(out[start:])["runtime_sources"]

    assert rc == 0
    assert web == controller
    assert cli == controller


def test_sky_health_passes_runtime_sources_through(tmp_path):
    ctrl = _ctrl(tmp_path)
    engine = FakeTimeEngine()
    sources = RuntimeSources(sdr=ctrl, gnss=FakeGNSS(),
                             time_engine=engine, station=ctrl)
    snap = sources.sync_gnss(now_utc=FIXED_NOW)

    health = health_snapshot([], FIXED_NOW,
                             {"available": False, "reason": "无解算库"},
                             runtime_sources=snap)
    assert health["runtime_sources"] == snap
    assert health["runtime_sources"] is not snap  # 原样复制，不共享可变对象

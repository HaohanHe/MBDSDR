# SPDX-License-Identifier: MIT
"""R6 GNSS 验收：收到 NMEA ≠ 已定位。

2026-11-21 真机实测抓到一次假 PASS：`RealGNSSMonitor.get_position()` 在
「收到合法 NMEA 但尚未定位」时按设计返回 `source="real"`、经纬度 None，
旧 R6 只等 `source=="real"` 就判 PASS，且坐标检查写成 `(pos.lat or 0)`，
于是 `lat=None/lon=None` 也过了。这里把两条红线钉死：
  1. 必须等到经纬度齐全才算 fix；
  2. 等不到 → SKIP(NEEDS_TIME_WINDOW)，不得 PASS、不得补坐标。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HW = str(ROOT / "scripts" / "hw_acceptance")
if HW not in sys.path:
    sys.path.insert(0, HW)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acceptance_suite  # noqa: E402
import common  # noqa: E402
from mbdsdr_ai.gnss_monitor import GNSSPosition  # noqa: E402


class _FakeMonitor:
    def __init__(self, positions):
        self._positions = list(positions)
        self.port = "COM4"
        self.baudrate = 9600
        self.stopped = False

    def start(self):
        return True

    def get_position(self):
        if len(self._positions) > 1:
            return self._positions.pop(0)
        return self._positions[0]

    def get_gsv_frames(self):
        return []

    def stop(self):
        self.stopped = True


def _patch_monitor(monkeypatch, fake):
    import mbdsdr_ai.gnss_monitor as gm
    monkeypatch.setattr(gm, "RealGNSSMonitor", lambda *a, **k: fake)
    monkeypatch.setattr(acceptance_suite.time, "sleep", lambda _s: None)


def test_ring6_waits_for_complete_coordinates(monkeypatch):
    fake = _FakeMonitor([
        GNSSPosition(source="real"),                       # 有 NMEA，未定位
        GNSSPosition(source="real", lat=43.7734, lon=125.3934,
                     alt=236.6, sats=12, hdop=1.5,
                     utc_time="085340.000"),
    ])
    _patch_monitor(monkeypatch, fake)
    monkeypatch.setenv("MBDSDR_GNSS_WAIT_S", "5")

    res = acceptance_suite.ring6()

    assert res["status"] == common.PASS, res
    assert res["metrics"]["lat"] == pytest.approx(43.7734)
    assert res["metrics"]["lon"] == pytest.approx(125.3934)
    assert res["metrics"]["sats_used"] == 12
    assert res["metrics"]["ttff_s"] is not None
    assert fake.stopped


def test_ring6_partial_fix_is_skip_not_pass(monkeypatch):
    fake = _FakeMonitor([GNSSPosition(source="real")])
    _patch_monitor(monkeypatch, fake)
    monkeypatch.setenv("MBDSDR_GNSS_WAIT_S", "0")

    res = acceptance_suite.ring6()

    assert res["status"] == common.SKIP, res
    assert res["skip_code"] == "NEEDS_TIME_WINDOW"
    # 没拿到 fix 就不许出现坐标字段的数值（metrics 里也不补）。
    assert "lat" not in res["metrics"]
    assert "lon" not in res["metrics"]
    assert fake.stopped


def test_ring6_no_nmea_is_skip(monkeypatch):
    fake = _FakeMonitor([GNSSPosition(source="none")])
    _patch_monitor(monkeypatch, fake)
    monkeypatch.setenv("MBDSDR_GNSS_WAIT_S", "0")

    res = acceptance_suite.ring6()

    assert res["status"] == common.SKIP
    assert res["skip_code"] == "NEEDS_TIME_WINDOW"

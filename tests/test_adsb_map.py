#!/usr/bin/env python3
"""
ADS-B 地图模块单元测试（offscreen，无显示器/无硬件）
====================================================

覆盖：
  1. 等距圆柱投影 project_equirectangular 的已知角点映射。
  2. AircraftTracker 的字段合并、prune 老化、按 last_seen 排序。
  3. haversine_km 的同点零值与半周长数量级。
  4. offscreen 实例化 AdsbMapPanel：空态、喂入一架合成测试帧、计数与列表、
     clear 复位。

注意：本文件中的飞机数据全部是“合成测试帧”（TEST01 / 任意经纬度），
仅用于验证面板逻辑，不代表真实航班、真实位置或真实呼号。
"""

import os
import sys

# 必须在 import PySide6 之前设置 offscreen 平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# desktop/ 无 __init__.py，themes.py 直接放在 desktop/ 下；offscreen 以包
# 方式 import desktop.adsb_map_panel 时，需要把 desktop/ 也加进 sys.path，
# 面板里的 ``from themes import ...`` 才能解析（与 test_desktop_panels 一致）。
_DESKTOP_DIR = os.path.join(REPO_ROOT, "desktop")
if _DESKTOP_DIR not in sys.path:
    sys.path.insert(0, _DESKTOP_DIR)

import pytest  # noqa: E402

from mbdsdr_ai.adsb_map import (  # noqa: E402
    AircraftTracker,
    haversine_km,
    project_equirectangular,
)


# --------------------------------------------------------------------------- #
# 1. 投影
# --------------------------------------------------------------------------- #
def test_projection_origin():
    # (lat=0, lon=0) 在 400x200、padding=10 下应映射到画布正中心 (200, 100)。
    x, y = project_equirectangular(0.0, 0.0, 400.0, 200.0, padding=10.0)
    assert abs(x - 200.0) <= 1.0
    assert abs(y - 100.0) <= 1.0


def test_projection_corners():
    # (lat=90, lon=-180) → 左上 (10, 10)；(lat=-90, lon=180) → 右下 (390, 190)。
    x1, y1 = project_equirectangular(90.0, -180.0, 400.0, 200.0, padding=10.0)
    assert abs(x1 - 10.0) <= 1.0
    assert abs(y1 - 10.0) <= 1.0
    x2, y2 = project_equirectangular(-90.0, 180.0, 400.0, 200.0, padding=10.0)
    assert abs(x2 - 390.0) <= 1.0
    assert abs(y2 - 190.0) <= 1.0


# --------------------------------------------------------------------------- #
# 2. AircraftTracker
# --------------------------------------------------------------------------- #
def test_tracker_update_and_prune():
    tr = AircraftTracker()
    # 合成测试帧：带 lat/lon。
    tr.update({"icao": "AAA111", "callsign": "TEST01",
               "altitude_ft": 35000, "lat": 30.0, "lon": 120.0,
               "velocity": {"groundspeed_kt": 450.0, "track_deg": 90.0}})
    assert len(tr.all()) == 1
    # ttl=0：立刻视为过期，全部清空。
    tr.prune(ttl_sec=0.0)
    assert tr.all() == []


def test_tracker_merge_callsign_and_position():
    tr = AircraftTracker()
    # 第一次只给呼号（识别帧，无位置）。
    tr.update({"icao": "BBB222", "callsign": "TEST02"})
    # 第二次只给位置（位置帧，无呼号）。
    tr.update({"icao": "BBB222", "lat": 31.0, "lon": 121.0,
               "altitude_ft": 30000})
    acs = tr.all()
    assert len(acs) == 1
    a = acs[0]
    assert a.callsign == "TEST02"
    assert a.lat == 31.0 and a.lon == 121.0
    assert a.altitude_ft == 30000


# --------------------------------------------------------------------------- #
# 3. haversine
# --------------------------------------------------------------------------- #
def test_haversine_same_point():
    assert haversine_km(39.9, 116.4, 39.9, 116.4) == pytest.approx(0.0, abs=1e-6)


def test_haversine_half_earth():
    # 赤道上跨 180° 应约等于地球半周长 pi*6371 ≈ 20015 km。
    d = haversine_km(0.0, 0.0, 0.0, 180.0)
    assert abs(d - 20015.1) <= 50.0


# --------------------------------------------------------------------------- #
# 4. offscreen 面板
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_panel_empty_state(qapp):
    from desktop.adsb_map_panel import AdsbMapPanel, ADSB_EMPTY_TEXT
    panel = AdsbMapPanel()
    assert panel.aircraft_count() == 0
    # 空态文案应包含“无 ADS-B 飞机”。
    assert "无 ADS-B 飞机" in ADSB_EMPTY_TEXT
    # 初始表格无行。
    assert panel.table.rowCount() == 0


def test_panel_feed_synthetic_frame(qapp):
    from desktop.adsb_map_panel import AdsbMapPanel
    panel = AdsbMapPanel()
    panel.resize(800, 500)
    # 合成测试帧：lat/lon + 呼号 + 高度。
    frame = {"icao": "CCCDDD", "callsign": "TEST01",
             "altitude_ft": 35000, "lat": 30.0, "lon": 120.0,
             "velocity": {"groundspeed_kt": 460.0, "track_deg": 180.0}}
    panel.update_aircraft([frame])
    assert panel.aircraft_count() == 1
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 0).text() == "TEST01"
    assert panel.table.item(0, 1).text() == "35000"
    # 未设观察者时距离列应为 "--"。
    assert panel.table.item(0, 2).text() == "--"


def test_panel_observer_distance_and_clear(qapp):
    from desktop.adsb_map_panel import AdsbMapPanel
    panel = AdsbMapPanel()
    # 合成测试帧。
    panel.update_aircraft([{"icao": "EEEFFF", "callsign": "TEST03",
                            "altitude_ft": 30000, "lat": 30.0, "lon": 120.0}])
    # 设观察者在同一点附近，距离应为有限数而非 "--"。
    panel.set_observer(30.0, 120.0)
    assert panel.table.item(0, 2).text() != "--"
    # clear 后回到空态。
    panel.clear()
    assert panel.aircraft_count() == 0
    assert panel.table.rowCount() == 0

"""
mbdsdr_ai.sat_tracker 闭环跟踪器单元测试
==========================================
验证项：
  1. TLE 目录加载 / 按名选星；
  2. current_position() 输出结构合理（方位 0-360°、仰角 -90..90°、距离>0、
     校正后频率 = 下行频率 + 多普勒）；
  3. 多普勒符号正确：AOS（升起/接近）频率升高(doppler>0)，
     LOS（落下/远离）频率降低(doppler<0)；
  4. is_visible 阈值（>=5° 可见）；
  5. 无观测者位置时不崩，返回 valid=False。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sat_tracker import SatelliteTracker, BUILTIN_TLE_DATE
from mbdsdr_ai.sat_passes import GroundStation


# 北京附近观测站（39.9N 116.4E，海拔 50m）。
def _beijing_gs() -> GroundStation:
    return GroundStation(lat_deg=39.9, lon_deg=116.4, alt_m=50.0)


def test_builtin_catalog_loaded():
    """内置真实 TLE 至少 5 颗常用卫星。"""
    t = SatelliteTracker(_beijing_gs())
    sats = t.list_satellites()
    names = [s["name"] for s in sats]
    assert len(sats) >= 5
    for expected in ("NOAA 15", "NOAA 18", "NOAA 19", "ISS (ZARYA)"):
        assert expected in names, f"缺内置卫星 {expected}"
    # TLE 每条都是合法的 line1/line2
    for s in sats:
        l1, l2 = s["tle"]
        assert l1.startswith("1 ") and l2.startswith("2 ")
        assert len(l1) >= 69 and len(l2) >= 69


def test_select_by_name_and_id():
    t = SatelliteTracker(_beijing_gs())
    assert t.select_satellite("NOAA 15") is True
    assert t.selected_name == "NOAA 15"
    # 按 NORAD 编号选中
    assert t.select_satellite("25544") is True
    assert t.selected_name == "ISS (ZARYA)"
    # 不存在的卫星
    assert t.select_satellite("NOPE 9999") is False


def test_current_position_ranges():
    """方位/仰角/距离在物理合理范围；校正后频率 = 下行 + 多普勒。"""
    t = SatelliteTracker(_beijing_gs())
    t.select_satellite("NOAA 15")
    t.set_downlink_freq(137.62e6)
    p = t.current_position()
    assert p["valid"] is True
    assert 0.0 <= p["azimuth"] < 360.0
    assert -90.0 <= p["elevation"] <= 90.0
    assert p["range_km"] > 100.0            # LEO 斜距至少几百 km
    assert abs(p["corrected_freq_hz"] - (137.62e6 + p["doppler_hz"])) < 1e-6
    # 多普勒量级合理：LEO 在 137 MHz 上下行偏移约 ±几 kHz
    assert abs(p["doppler_hz"]) < 100e3


def test_doppler_sign_approaching_vs_receding():
    """AOS（接近）doppler>0，LOS（远离）doppler<0。

    用 next_pass() 找到一次真实过境，再把 tracker 的时钟拨到 rise/set 时刻
    读 current_position()，验证径向速度与多普勒符号。
    """
    t = SatelliteTracker(_beijing_gs())
    t.select_satellite("NOAA 15")
    t.set_downlink_freq(137.62e6)
    p = t.next_pass(min_alt=5.0, hours=48.0)
    assert p is not None, "北京附近 48h 内应至少有一次 NOAA 15 过境"

    real_now = t._ts.now
    try:
        t._ts.now = lambda: p.rise_time
        pos_aos = t.current_position()
        t._ts.now = lambda: p.set_time
        pos_los = t.current_position()
    finally:
        t._ts.now = real_now

    # AOS：卫星朝观测者飞来 -> range_rate<0 -> doppler>0（接收频率升高）
    assert pos_aos["range_rate_km_s"] < 0, \
        f"AOS 应接近(range_rate<0)，实际 {pos_aos['range_rate_km_s']:.3f} km/s"
    assert pos_aos["doppler_hz"] > 0, \
        f"AOS 多普勒应为正(频率升高)，实际 {pos_aos['doppler_hz']:.1f} Hz"
    # LOS：卫星远离观测者 -> range_rate>0 -> doppler<0（接收频率降低）
    assert pos_los["range_rate_km_s"] > 0, \
        f"LOS 应远离(range_rate>0)，实际 {pos_los['range_rate_km_s']:.3f} km/s"
    assert pos_los["doppler_hz"] < 0, \
        f"LOS 多普勒应为负(频率降低)，实际 {pos_los['doppler_hz']:.1f} Hz"


def test_is_visible_threshold():
    """中天可见；地平线下不可见。"""
    t = SatelliteTracker(_beijing_gs())
    t.select_satellite("NOAA 15")
    p = t.next_pass(min_alt=5.0, hours=48.0)
    assert p is not None

    real_now = t._ts.now
    try:
        t._ts.now = lambda: p.max_alt_time
        assert t.is_visible(5.0) is True, "中天应可见"
        # 地平线下时刻（中天后很久）应不可见
        far = t._ts.tt_jd(p.max_alt_time.tt + 0.5)  # +0.5 天
        t._ts.now = lambda: far
        assert t.is_visible(5.0) is False, "0.5 天后应已过境/不可见"
    finally:
        t._ts.now = real_now


def test_no_ground_station_no_crash():
    """无观测者位置：选星不崩，current_position 返回 valid=False。"""
    t = SatelliteTracker(None)
    assert t.select_satellite("ISS (ZARYA)") is True
    p = t.current_position()
    assert p["valid"] is False
    assert p["elevation"] is None
    assert p["doppler_hz"] is None
    assert t.is_visible(5.0) is False
    assert t.next_pass() is None
    # 目录仍可用
    assert len(t.list_satellites()) >= 5


def test_downlink_freq_dict():
    """内置下行频率为科学/业余标准值，非 0。"""
    t = SatelliteTracker(_beijing_gs())
    assert t.downlink_freq_for("NOAA 15") == pytest.approx(137.62e6)
    assert t.downlink_freq_for("ISS (ZARYA)") == pytest.approx(145.8e6)
    assert t.downlink_freq_for("UNKNOWN SAT") == 0.0


def test_tle_date_label():
    """内置 TLE 有日期标注。"""
    t = SatelliteTracker(_beijing_gs())
    assert t.tle_date == BUILTIN_TLE_DATE
    assert t.tle_date.startswith("2026-")

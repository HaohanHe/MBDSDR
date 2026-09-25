"""
solar_system.py 测试
====================
验证：
1. 太阳位置：正午 alt ≈ 90° - |lat - dec|
2. 月亮位置：角度范围合理，相位在 [0,1]
3. 行星位置：所有行星角度范围合理
4. 时间/站址输入归一化
"""

import math
import time as _time
from datetime import datetime, timezone

from mbdsdr_ai.solar_system import (
    get_sun_position,
    get_moon_position,
    get_planet_position,
    get_backend_info,
    GroundStation,
    CHANGCHUN,
)


def test_backend_available():
    info = get_backend_info()
    assert info["available"], f"历表后端不可用: {info}"
    print(f"[OK] 后端可用: {info['backend']}")


def test_sun_bounds():
    """太阳位置基本范围检查。"""
    t = _time.time()
    sun = get_sun_position(t, CHANGCHUN)
    assert sun is not None, "太阳位置返回 None"
    assert 0 <= sun.ra_deg < 360, f"RA 越界: {sun.ra_deg}"
    assert -23.5 <= sun.dec_deg <= 23.5, f"太阳赤纬越界: {sun.dec_deg}"
    assert -90 <= sun.alt_deg <= 90, f"仰角越界: {sun.alt_deg}"
    assert 0 <= sun.az_deg < 360, f"方位角越界: {sun.az_deg}"
    assert 0.96 <= sun.distance_au <= 1.04, f"日地距离异常: {sun.distance_au}"
    assert sun.illumination == 1.0, "太阳应 100% 照亮"
    print(f"[OK] 太阳范围: ra={sun.ra_deg:.2f} dec={sun.dec_deg:.2f} "
          f"alt={sun.alt_deg:.2f} az={sun.az_deg:.2f} "
          f"r={sun.distance_au:.4f} AU, 角直径={sun.angular_diameter_deg:.3f}°")


def test_sun_noon_altitude():
    """
    正午太阳高度角：alt_max ≈ 90° - |lat - dec|
    找长春正午（太阳最高）时刻，验证高度角公式。
    """
    station = CHANGCHUN
    lat = station.latitude_deg
    lon = station.longitude_deg

    # 长春经度 125.3E，地方正午 UTC ≈ 12:00 - lon/15 = 03:32 UTC
    # 扫描 02:00 ~ 05:00 UTC（3 小时窗口，步长 5 分钟 = 36 点）
    now = _time.time()
    today_00_utc = now - (now % 86400)  # 今天 00:00 UTC
    noon_utc_approx = today_00_utc + (12.0 - lon / 15.0) * 3600.0
    best_alt = -999
    best_sun = None
    t = noon_utc_approx - 1.5 * 3600.0
    for _ in range(36):
        sun = get_sun_position(t, station)
        if sun is not None and sun.alt_deg > best_alt:
            best_alt = sun.alt_deg
            best_sun = sun
        t += 300  # 5 分钟步长

    assert best_sun is not None, "未找到太阳最高点"
    expected_max_alt = 90.0 - abs(lat - best_sun.dec_deg)
    diff = abs(best_alt - expected_max_alt)
    print(f"[OK] 太阳正午高度: 实测={best_alt:.2f}°, 公式预测={expected_max_alt:.2f}°, "
          f"差={diff:.2f}°, dec={best_sun.dec_deg:.2f}°")
    assert diff < 3.0, f"正午高度角偏差过大: {diff:.2f}°"


def test_moon():
    """月亮位置检查。"""
    t = _time.time()
    moon = get_moon_position(t, CHANGCHUN)
    assert moon is not None, "月亮位置返回 None"
    assert 0 <= moon.ra_deg < 360, f"RA 越界: {moon.ra_deg}"
    assert -90 <= moon.dec_deg <= 90, f"赤纬越界: {moon.dec_deg}"
    assert -90 <= moon.alt_deg <= 90, f"仰角越界: {moon.alt_deg}"
    assert 0 <= moon.az_deg < 360, f"方位角越界: {moon.az_deg}"
    # 月球距离 356000 ~ 407000 km
    assert 356000 <= moon.distance_km <= 407000, f"月距异常: {moon.distance_km}"
    assert 0.0 <= moon.illumination <= 1.0, f"照度越界: {moon.illumination}"
    assert 0.0 <= moon.phase_angle_deg <= 180.0, f"相位角越界: {moon.phase_angle_deg}"
    # 角直径 0.48 ~ 0.56°
    assert 0.45 <= moon.angular_diameter_deg <= 0.60, \
        f"月球角直径异常: {moon.angular_diameter_deg}"
    print(f"[OK] 月亮: ra={moon.ra_deg:.2f} dec={moon.dec_deg:.2f} "
          f"alt={moon.alt_deg:.2f} az={moon.az_deg:.2f}")
    print(f"     距离={moon.distance_km:.0f} km, 相位角={moon.phase_angle_deg:.1f}°, "
          f"照度={moon.illumination:.3f}, 角直径={moon.angular_diameter_deg:.3f}°")


def test_planets():
    """所有大行星位置检查。"""
    t = _time.time()
    for name in ["mercury", "venus", "mars", "jupiter", "saturn"]:
        pos = get_planet_position(name, t, CHANGCHUN)
        assert pos is not None, f"{name} 返回 None"
        assert 0 <= pos.ra_deg < 360, f"{name} RA 越界: {pos.ra_deg}"
        assert -90 <= pos.dec_deg <= 90, f"{name} dec 越界: {pos.dec_deg}"
        assert -90 <= pos.alt_deg <= 90, f"{name} alt 越界: {pos.alt_deg}"
        assert 0 <= pos.az_deg < 360, f"{name} az 越界: {pos.az_deg}"
        assert pos.distance_au and pos.distance_au > 0, f"{name} 距离异常"
        print(f"[OK] {name:8s}: ra={pos.ra_deg:7.2f} dec={pos.dec_deg:7.2f} "
              f"alt={pos.alt_deg:7.2f} az={pos.az_deg:7.2f} "
              f"r={pos.distance_au:5.2f} AU, illum={pos.illumination or 0:.3f}")


def test_time_normalization():
    """测试多种时间输入格式。"""
    now = _time.time()
    # unix 时间戳
    p1 = get_sun_position(now, CHANGCHUN)
    # datetime
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    p2 = get_sun_position(dt, CHANGCHUN)
    assert p1 is not None and p2 is not None
    assert abs(p1.ra_deg - p2.ra_deg) < 0.01, "时间格式不一致"
    print(f"[OK] 时间归一化: unix vs datetime RA 差={abs(p1.ra_deg - p2.ra_deg):.6f}°")


def test_station_normalization():
    """测试多种站址输入格式。"""
    now = _time.time()
    p1 = get_sun_position(now, CHANGCHUN)
    p2 = get_sun_position(now, (43.817, 125.323, 200.0))
    p3 = get_sun_position(now, GroundStation(43.817, 125.323, 200.0))
    assert p1 is not None and p2 is not None and p3 is not None
    assert abs(p1.ra_deg - p2.ra_deg) < 0.001
    assert abs(p1.ra_deg - p3.ra_deg) < 0.001
    print(f"[OK] 站址归一化: GroundStation/tuple/dataclass 一致")


if __name__ == "__main__":
    print("=" * 60)
    print("solar_system.py 测试")
    print("=" * 60)
    test_backend_available()
    test_sun_bounds()
    test_sun_noon_altitude()
    test_moon()
    test_planets()
    test_time_normalization()
    test_station_normalization()
    print("=" * 60)
    print("全部测试通过!")

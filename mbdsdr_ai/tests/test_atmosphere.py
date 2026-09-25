"""atmosphere.py 测试：晨昏时刻 / 折射 / 空气质量 / 天空亮度。

运行：python3 -m pytest mbdsdr_ai/tests/test_atmosphere.py -v
"""
from datetime import date, timezone, timedelta, datetime

import pytest

from mbdsdr_ai.atmosphere import (
    compute_twilight_times,
    atmospheric_refraction,
    airmass,
    sky_brightness_factor,
    is_night,
    sun_altitude_deg,
    TwilightInfo,
)
from mbdsdr_ai.sat_passes import GroundStation


# 长春：43.88°N, 125.32°E, ~200m
CHANGCHUN = GroundStation(lat_deg=43.88, lon_deg=125.32, alt_m=200)
CST = timezone(timedelta(hours=8))


def _local_hm(dt: datetime) -> float:
    """UTC datetime → 本地(CST)小时数。"""
    return dt.astimezone(CST).hour + dt.astimezone(CST).minute / 60.0


class TestRefraction:
    def test_horizon_refraction_about_half_degree(self):
        """alt=0° 折射 ≈ 0.5°（Stellarium Saemundsson 给出 ~0.48°）。"""
        r = atmospheric_refraction(0.0)
        assert 0.4 < r < 0.6, f"horizon refraction={r:.4f}°"

    def test_zenith_refraction_zero(self):
        """alt=90° 折射 ≈ 0。"""
        r = atmospheric_refraction(90.0)
        assert abs(r) < 0.001, f"zenith refraction={r:.6f}°"

    def test_intermediate_altitude(self):
        """alt=30° 折射约 1.7' ≈ 0.028°。"""
        r = atmospheric_refraction(30.0)
        assert 0.02 < r < 0.04, f"30° refraction={r:.4f}°"

    def test_pressure_temperature_scaling(self):
        """高压/低温 → 折射增大；低压/高温 → 折射减小。"""
        r_std = atmospheric_refraction(10.0)
        r_high = atmospheric_refraction(10.0, pressure_hpa=1030.0, temperature_c=-10.0)
        r_low = atmospheric_refraction(10.0, pressure_hpa=980.0, temperature_c=30.0)
        assert r_high > r_std > r_low


class TestAirmass:
    def test_zenith_is_one(self):
        assert abs(airmass(90.0) - 1.0) < 0.01

    def test_horizon_large(self):
        m = airmass(0.0)
        assert 30.0 < m < 50.0, f"horizon airmass={m:.1f}"

    def test_monotonic(self):
        """高度越低，空气质量越大。"""
        assert airmass(10.0) > airmass(45.0) > airmass(90.0)

    def test_below_horizon_zero(self):
        assert airmass(-5.0) == 0.0


class TestSkyBrightness:
    def test_day(self):
        assert sky_brightness_factor(10.0) == 1.0
        assert sky_brightness_factor(90.0) == 1.0

    def test_night(self):
        assert sky_brightness_factor(-20.0) == 0.0

    def test_civil_twilight_value(self):
        """-6° 处约 0.33。"""
        assert abs(sky_brightness_factor(-6.0) - 0.33) < 0.01

    def test_monotonic(self):
        """太阳越高，天空越亮。"""
        assert (sky_brightness_factor(-18)
                < sky_brightness_factor(-12)
                < sky_brightness_factor(-6)
                < sky_brightness_factor(0))


class TestIsNight:
    def test_below_minus6_is_night(self):
        assert is_night(-7.0) is True
        assert is_night(-18.0) is True

    def test_above_minus6_is_day(self):
        assert is_night(-5.0) is False
        assert is_night(10.0) is False  # 白天
        assert is_night(0.0) is False


class TestTwilightTimes:
    def test_equinox_changchun_sunrise_sunset(self):
        """长春秋分日（2026-03-20）日出≈6:00、日落≈18:00 CST（±30min）。"""
        info = compute_twilight_times(date(2026, 3, 20), CHANGCHUN)
        assert info.sunrise is not None, "春分日长春必有日出"
        assert info.sunset is not None
        rise_h = _local_hm(info.sunrise)
        set_h = _local_hm(info.sunset)
        # 春分日，长春纬度 43.9°N，理论昼长 ~12h，日出 ~5:40、日落 ~17:50 CST
        assert 5.0 < rise_h < 6.5, f"日出 CST={rise_h:.2f}h"
        assert 17.0 < set_h < 18.5, f"日落 CST={set_h:.2f}h"

    def test_twilight_ordering(self):
        """晨昏事件顺序正确：天文晨→航海晨→民用晨→日出→日落→民用昏→航海昏→天文昏。"""
        info = compute_twilight_times(date(2026, 3, 20), CHANGCHUN)
        events = [
            info.astronomical_twilight_start,
            info.nautical_twilight_start,
            info.civil_twilight_start,
            info.sunrise,
            info.sunset,
            info.civil_twilight_end,
            info.nautical_twilight_end,
            info.astronomical_twilight_end,
        ]
        assert all(e is not None for e in events)
        for a, b in zip(events[:-1], events[1:]):
            assert a < b, f"顺序错误: {a} >= {b}"

    def test_twilight_altitude_spacing(self):
        """民用晨昏在日出后/日落前约 25-30 分钟（长春 3 月）。"""
        info = compute_twilight_times(date(2026, 3, 20), CHANGCHUN)
        # 日出 - 民用晨始 ≈ 25-35 min
        morning_gap = (info.sunrise - info.civil_twilight_start).total_seconds() / 60.0
        assert 15 < morning_gap < 45, f"晨民用晨昏到日出间隔={morning_gap:.1f}min"

    def test_sun_altitude_on_equinox(self):
        """验证太阳高度计算：春正午长春太阳高度 ≈ 90-43.9 = 46.1°。"""
        # 春分日太阳赤纬≈0；长春纬度 43.88°N，正午 alt ≈ 90-43.88 = 46.1°
        # 选春分日正午（UTC 04:00 ≈ 长春正午 CST 12:00）
        jd_noon = 2461119.667  # 2026-03-20 04:00 UTC
        alt = sun_altitude_deg(jd_noon, 43.88, 125.32)
        assert 44.0 < alt < 48.0, f"正午太阳高度={alt:.2f}°"

    def test_data_class_fields(self):
        info = compute_twilight_times(date(2026, 3, 20), CHANGCHUN)
        d = info.to_dict()
        assert set(d.keys()) == {
            "sunrise", "sunset",
            "civil_twilight_start", "civil_twilight_end",
            "nautical_twilight_start", "nautical_twilight_end",
            "astronomical_twilight_start", "astronomical_twilight_end",
        }

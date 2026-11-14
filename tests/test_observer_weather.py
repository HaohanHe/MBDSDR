# SPDX-License-Identifier: MIT
"""A6 判据：观测者气象必须是带 at_utc/source 的当天实测，不是静态默认值。

覆盖：
  * Observer 不再有 1013.25 hPa / 10 ℃ / 0.2 的默认气象；
  * 缺值/缺时刻/缺来源/过期 → 折射返回 None 且状态 unknown；
  * 静日 vs 高湿日：两组实测输入折射不同且方向正确；
  * 复用 space_weather 同一请求的结构化测量（不新增采集点）；
  * 反证：只给典型值（1013.25/10）但没有采集时刻与来源，必须仍是 unknown。
"""
import os
import sys

os.environ.setdefault("MBDSDR_OFFLINE", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.astronomy import (  # noqa: E402
    EquatorialCoord,
    Observer,
    compute_refraction,
    unix_to_jd,
)
from mbdsdr_ai.environment_facts import (  # noqa: E402
    FACT_HUMIDITY,
    FACT_PRESSURE,
    FACT_TEMPERATURE,
    EnvironmentSnapshot,
)
from mbdsdr_ai.space_weather import _fetch_weather  # noqa: E402

NOW = "2026-11-14T06:00:00Z"


def measured_observer(pressure=1012.0, temperature=18.0, humidity=60.0,
                      at=NOW, source="测试气象站", max_age_s=None) -> Observer:
    return Observer(
        latitude_deg=31.2, longitude_deg=121.5, height_m=50.0,
        pressure_hpa=pressure, temperature_c=temperature, humidity=humidity,
        weather_at_utc=at, weather_source=source,
        weather_max_age_s=max_age_s,
    )


# ---------------------------------------------------------------------------
# 默认值：不存在
# ---------------------------------------------------------------------------
def test_observer_defaults_have_no_typical_weather():
    obs = Observer()
    assert obs.pressure_hpa is None
    assert obs.temperature_c is None
    assert obs.humidity is None
    assert obs.weather_at_utc is None
    assert obs.weather_source == ""
    assert obs.refraction_weather_status(NOW) == "unknown"


def test_typical_values_without_time_and_source_stay_unknown():
    """反证：把典型值填进字段但没有采集时刻/来源，仍是 unknown。"""
    obs = Observer(pressure_hpa=1013.25, temperature_c=10.0, humidity=20.0)
    assert obs.refraction_weather_status(NOW) == "unknown"
    assert compute_refraction(10.0, obs, now=NOW) is None

    # 有值有来源但缺采集时刻 → 仍 unknown
    obs2 = measured_observer(at=None)
    assert obs2.refraction_weather_status(NOW) == "unknown"
    assert compute_refraction(10.0, obs2, now=NOW) is None

    # 有值有时刻但缺来源 → 仍 unknown
    obs3 = measured_observer(source="")
    assert obs3.refraction_weather_status(NOW) == "unknown"


def test_stale_and_partial_weather_are_unknown():
    stale = measured_observer(at="2026-11-13T00:00:00Z", max_age_s=3 * 3600)
    assert stale.refraction_weather_status(NOW) == "stale"
    assert compute_refraction(10.0, stale, now=NOW) is None

    no_pressure = measured_observer(pressure=None)
    assert no_pressure.refraction_weather_status(NOW) == "unknown"
    assert compute_refraction(10.0, no_pressure, now=NOW) is None


# ---------------------------------------------------------------------------
# 静日 vs 高湿日：方向正确
# ---------------------------------------------------------------------------
def test_two_measured_days_differ_in_correct_direction():
    cold_dry = measured_observer(pressure=1030.0, temperature=-5.0,
                                 humidity=20.0)
    hot_humid = measured_observer(pressure=990.0, temperature=32.0,
                                  humidity=95.0)
    r_cold = compute_refraction(10.0, cold_dry, now=NOW)
    r_hot = compute_refraction(10.0, hot_humid, now=NOW)
    assert r_cold is not None and r_hot is not None
    # 高压低温 → 折射更大；低压高温 → 更小。两组输入结果必须不同。
    assert r_cold > r_hot > 0.0
    assert r_cold != pytest.approx(r_hot)


def test_altaz_marks_uncorrected_when_weather_missing():
    jd = unix_to_jd()
    eq = EquatorialCoord(ra_deg=150.0, dec_deg=20.0)

    unknown = eq.to_altaz(Observer(latitude_deg=31.2, longitude_deg=121.5), jd)
    assert unknown.refraction_deg is None
    assert unknown.refraction_status == "unknown"

    known = eq.to_altaz(measured_observer(), jd)
    assert known.refraction_status == "known"
    assert known.refraction_deg is not None


# ---------------------------------------------------------------------------
# 复用 space_weather：同一请求补 surface_pressure，不新增采集点
# ---------------------------------------------------------------------------
def test_space_weather_fetch_exposes_structured_measurements():
    seen = {}

    def fake(url):
        seen["url"] = url
        return {"current": {
            "time": "2026-11-14T05:45",
            "temperature_2m": 21.5,
            "relative_humidity_2m": 88.0,
            "precipitation": 0.2,
            "surface_pressure": 1002.3,
            "weather_code": 3,
        }}

    sample = _fetch_weather(fake, NOW, 31.2, 121.5)
    assert "surface_pressure" in seen["url"]
    assert sample["observed_at"] == "2026-11-14T05:45"
    assert sample["measurements"] == {
        "temperature_c": 21.5,
        "relative_humidity_pct": 88.0,
        "surface_pressure_hpa": 1002.3,
    }


def test_space_weather_missing_measurements_are_not_filled():
    sample = _fetch_weather(
        lambda url: {"current": {"time": "2026-11-14T05:45",
                                 "precipitation": 0.0}},
        NOW, 31.2, 121.5)
    assert sample["measurements"] == {}


def _weather_payload(observed_at=NOW, measurements=None, taken_at=NOW):
    if measurements is None:
        measurements = {"temperature_c": 22.0,
                        "relative_humidity_pct": 91.0,
                        "surface_pressure_hpa": 998.0}
    return {
        "taken_at": taken_at,
        "samples": {
            "weather": {
                "observed_at": observed_at,
                "source": "Open-Meteo current weather",
                "url": "https://api.open-meteo.com/v1/forecast",
                "error": "",
                "measurements": measurements,
            },
        },
    }


def test_observer_reuses_space_weather_snapshot():
    snapshot = EnvironmentSnapshot.from_space_weather(_weather_payload())
    assert snapshot.value(FACT_PRESSURE, now=NOW) == 998.0
    assert snapshot.value(FACT_TEMPERATURE, now=NOW) == 22.0
    assert snapshot.value(FACT_HUMIDITY, now=NOW) == 91.0

    obs = Observer()
    status = obs.apply_weather_snapshot(snapshot)
    assert status == "known"
    assert obs.pressure_hpa == 998.0
    assert obs.temperature_c == 22.0
    assert obs.humidity == 91.0
    assert obs.weather_at_utc == NOW
    assert "Open-Meteo" in obs.weather_source


def test_observer_rejects_incomplete_or_stale_snapshot():
    missing = EnvironmentSnapshot.from_space_weather(
        _weather_payload(measurements={"temperature_c": 22.0}))
    obs = Observer()
    assert obs.apply_weather_snapshot(missing) == "unknown"
    assert obs.pressure_hpa is None
    assert obs.temperature_c == 22.0  # 有用项保留，但不构成可算气象
    assert compute_refraction(10.0, obs, now=NOW) is None

    stale = EnvironmentSnapshot.from_space_weather(
        _weather_payload(observed_at="2026-11-14T00:00:00Z"))
    obs2 = Observer()
    assert obs2.apply_weather_snapshot(stale) == "stale"
    assert obs2.pressure_hpa is None


# ---------------------------------------------------------------------------
# Agent 工具层：不兜底典型值
# ---------------------------------------------------------------------------
def test_agent_observer_tool_does_not_fill_typical_values():
    from mbdsdr_ai.agent import MBDSDRAgent

    agent = object.__new__(MBDSDRAgent)
    agent.observer = Observer()
    agent._reuse_weather_from_space_weather = lambda: "无可用快照"

    text = agent._set_observer_handler({
        "latitude_deg": 31.2, "longitude_deg": 121.5, "height_m": 50.0})
    assert agent.observer.pressure_hpa is None
    assert "未知" in text and "1013.25" not in text

    refraction = agent._refraction_handler({"alt_deg": 10.0})
    assert "未校正" in refraction and "弧分" not in refraction

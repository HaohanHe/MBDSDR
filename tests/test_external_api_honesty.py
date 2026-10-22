# -*- coding: utf-8 -*-
"""外部 API 错误载荷必须显式失败（不得谎报"0 架飞机 / 太空有 0 人 / 无温度"）。

销账背景：运行时外部依赖审计发现三处静默降级——open-notify astros 在错误
载荷下会回 "当前太空有0人"、open-meteo 错误载荷下会回 temperature_c=None、
opensky 错误载荷下会回 count=0。这些都不是"真的为空"，是谎报。

本测试用桩替换 requests.get（不打真实网络），覆盖：
· 错误载荷 -> success=False 且带原因；
· 正常载荷（含"真的为空"）-> success=True。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import openapi_integration as oa  # noqa: E402


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def fake_get(monkeypatch):
    import requests

    def _install(payload):
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: _FakeResp(payload))
    return _install


def test_weather_error_payload_is_not_success(fake_get):
    fake_get({"error": True, "reason": "Latitude must be in range"})
    r = oa.get_weather(999.0, 999.0)
    assert r["success"] is False
    assert "Latitude" in r["error"]


def test_weather_normal_payload_succeeds(fake_get):
    fake_get({"current": {"temperature_2m": 12.3, "wind_speed_10m": 5.0,
                          "relative_humidity_2m": 40.0, "weather_code": 1}})
    r = oa.get_weather(39.9, 116.4)
    assert r["success"] is True
    assert r["temperature_c"] == 12.3


def test_people_error_payload_is_not_zero_people(fake_get):
    """错误载荷不能被报成"太空有 0 人"。"""
    fake_get({"message": "error", "code": 500})
    r = oa.get_people_in_space()
    assert r["success"] is False
    assert "error" in r["error"].lower()


def test_people_genuine_zero_is_success(fake_get):
    """API 明说 success 且 number=0 —— 那是真的 0 人，可信。"""
    fake_get({"message": "success", "number": 0, "people": []})
    r = oa.get_people_in_space()
    assert r["success"] is True
    assert r["number"] == 0


def test_aircraft_error_payload_is_not_zero_aircraft(fake_get):
    fake_get({"message": "rate limited"})
    r = oa.get_aircraft_nearby(39.9, 116.4)
    assert r["success"] is False
    assert "rate limited" in r["error"]


def test_aircraft_genuine_empty_is_success(fake_get):
    fake_get({"time": 1, "states": []})
    r = oa.get_aircraft_nearby(39.9, 116.4)
    assert r["success"] is True
    assert r["count"] == 0

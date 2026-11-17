# SPDX-License-Identifier: MIT
"""Task 18 后续：按名称解析 TLE 时不得在离线/未授权状态下偷偷联网。

口径：
* 默认离线优先——``MBDSDR_OFFLINE`` 或未显式开启 ``MBDSDR_ALLOW_NETWORK``
  时，按名称取 TLE 只读本地缓存，网络打开器调用次数必须为 0；
* 无缓存时如实报「离线且无缓存 TLE」，不阻塞等超时；
* 显式允许（环境变量或 ``allow_network=True``）时只发一次请求，并在返回体
  标注 ``network_used=True``。

全部用替换的网络函数计数，不真正联网。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import decoders, orbit  # noqa: E402
from mbdsdr_ai.orbit import TLEUnavailableError  # noqa: E402

CAT = 25544
NAME = "ISS (ZARYA)"
LINE1 = "1 25544U 98067A   26268.43198945  .00011731  00000+0  21853-3 0  9991"
LINE2 = "2 25544  51.6316 163.9608 0004776 179.9710 180.1280 15.49288785587293"
GOOD_BODY = f"{NAME}\n{LINE1}\n{LINE2}\n".encode("utf-8")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body


class _NetworkCounter:
    """替身网络入口：只计数并返回脚本响应，绝不真发请求。"""

    def __init__(self, body: bytes = GOOD_BODY):
        self.body = body
        self.calls: list = []

    def __call__(self, req, timeout_s, hard_deadline_s):
        self.calls.append(getattr(req, "full_url", str(req)))
        return _FakeResponse(self.body)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """每例独立 TLE 缓存目录与失败冷却表，避免跨用例串味。"""
    monkeypatch.setenv("MBDSDR_TLE_CACHE_DIR", str(tmp_path / "tle_cache"))
    monkeypatch.delenv("MBDSDR_ALLOW_NETWORK", raising=False)
    orbit.reset_tle_cache()
    decoders._TLE_FAIL_UNTIL.clear()
    yield
    orbit.reset_tle_cache()
    decoders._TLE_FAIL_UNTIL.clear()


def _write_cache(tmp_path, age_s: float = 60.0) -> None:
    import time

    cache_dir = tmp_path / "tle_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{CAT}.tle"
    path.write_text(f"{NAME}\n{LINE1}\n{LINE2}\n", encoding="utf-8")
    ts = time.time() - age_s
    os.utime(path, (ts, ts))


# ------------------------------------------------------------ 默认不联网
def test_offline_mode_makes_zero_network_calls(monkeypatch):
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    net = _NetworkCounter()
    monkeypatch.setattr(orbit, "_bounded_urlopen", net)

    with pytest.raises(TLEUnavailableError) as ei:
        orbit.fetch_tle_record(CAT)

    assert net.calls == [], "离线模式不得发起任何网络请求"
    assert ei.value.failure["code"] == "offline"
    assert getattr(ei.value, "network_used", None) is False


def test_without_explicit_permission_makes_zero_network_calls(monkeypatch):
    """既没设 MBDSDR_OFFLINE、也没显式允许联网：同样不得偷偷联网。"""
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    net = _NetworkCounter()
    monkeypatch.setattr(orbit, "_bounded_urlopen", net)

    with pytest.raises(TLEUnavailableError) as ei:
        orbit.fetch_tle_record(CAT)

    assert net.calls == []
    msg = str(ei.value)
    assert "离线且无缓存 TLE" in msg
    assert "MBDSDR_ALLOW_NETWORK" in msg


def test_cached_tle_is_used_offline_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    _write_cache(tmp_path)
    net = _NetworkCounter()
    monkeypatch.setattr(orbit, "_bounded_urlopen", net)

    rec = orbit.fetch_tle_record(CAT)

    assert net.calls == []
    assert rec["refresh_status"] == "cache"
    assert rec["network_used"] is False
    assert (rec["line1"], rec["line2"]) == (LINE1, LINE2)


# --------------------------------------------------- 显式允许时才联网一次
def test_env_permission_makes_exactly_one_call_and_marks(monkeypatch):
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    monkeypatch.setenv("MBDSDR_ALLOW_NETWORK", "1")
    net = _NetworkCounter()
    monkeypatch.setattr(orbit, "_bounded_urlopen", net)

    rec = orbit.fetch_tle_record(CAT)

    assert len(net.calls) == 1
    assert rec["refresh_status"] == "network"
    assert rec["network_used"] is True
    assert (rec["line1"], rec["line2"]) == (LINE1, LINE2)


def test_argument_permission_makes_exactly_one_call(monkeypatch):
    """未设环境变量时，本次调用显式 ``allow_network=True`` 也可联网。"""
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)
    net = _NetworkCounter()
    monkeypatch.setattr(orbit, "_bounded_urlopen", net)

    rec = orbit.fetch_tle_record(CAT, allow_network=True)

    assert len(net.calls) == 1
    assert rec["network_used"] is True


# ---------------------------------------------------- 名称解析的空态与原因
def test_visible_satellites_empty_with_offline_reason(monkeypatch):
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    net = _NetworkCounter()
    monkeypatch.setattr(orbit, "_bounded_urlopen", net)

    from mbdsdr_ai.new_spacetime import compute_visible_satellite_count

    result = compute_visible_satellite_count(
        39.9, 116.4, min_elevation=0.0, timestamp=1_700_000_000.0)

    assert net.calls == []
    assert result["total_visible"] == 0
    assert result["unavailable"] is True
    assert "离线且无缓存 TLE" in result["reason"]
    assert result["network_used"] is False

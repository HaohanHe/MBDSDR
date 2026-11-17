# SPDX-License-Identifier: MIT
"""GNSS 粗指向工具面的行为测试。

合成观测只用于证明「输入 → 输出」的行为（能给出粗指向、没有分辨力就拒绝、
缺字段不补零），不代表真机 SNR 特性。
"""
from __future__ import annotations

import math
import os
import random
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.gnss_sky_reference import angular_separation_deg  # noqa: E402
from mbdsdr_ai.gnss_tools import (  # noqa: E402
    sky_fit_from_args, register_gnss_tools,
)


def _frames(baz: float, bel: float, *, count: int = 24, seed: int = 7):
    """按 ``48 + 20·log10(cosθ)`` 造 GSV 帧，返回 (frames, 真值)。"""
    rng = random.Random(seed)
    sats = []
    guard = 0
    while len(sats) < count and guard < 5000:
        guard += 1
        az = rng.uniform(0.0, 360.0)
        el = rng.uniform(10.0, 85.0)
        sep = angular_separation_deg(baz, bel, az, el)
        if sep > 85.0:
            continue
        snr = 48.0 + 20.0 * math.log10(max(math.cos(math.radians(sep)), 1e-3))
        sats.append({"id": len(sats) + 1, "elevation": round(el, 1),
                     "azimuth": round(az, 1),
                     "snr_db": round(snr + rng.gauss(0.0, 0.3), 1)})
    return [{"talker": "GB", "sats": sats}], (baz, bel)


def test_gsv_frames_yield_a_coarse_boresight():
    frames, (baz, bel) = _frames(120.0, 45.0)
    out = sky_fit_from_args({"frames": frames})
    assert out["ok"] is True
    assert out["input_source"] == "gsv_frames"
    fit = out["fit"]
    sep = angular_separation_deg(fit["azimuth_deg"], fit["elevation_deg"],
                                 baz, bel)
    assert sep < 8.0
    assert fit["discriminative"] is True
    assert "不能替代视觉解算" in out["scope"]


def test_direct_satellite_list_is_accepted():
    frames, (baz, bel) = _frames(300.0, 55.0)
    sats = [{
        "constellation": "BDS", "prn": s["id"],
        "azimuth_deg": s["azimuth"], "elevation_deg": s["elevation"],
        "snr_db_hz": s["snr_db"],
    } for s in frames[0]["sats"]]
    out = sky_fit_from_args({"satellites": sats})
    assert out["ok"] is True
    assert out["input_source"] == "satellites"
    assert angular_separation_deg(
        out["fit"]["azimuth_deg"], out["fit"]["elevation_deg"], baz, bel) < 8.0


def test_imu_agreement_and_conflict_are_reported_not_auto_fixed():
    frames, _ = _frames(200.0, 50.0)
    fit = sky_fit_from_args({"frames": frames})["fit"]

    agree = sky_fit_from_args({
        "frames": frames,
        "imu_azimuth_deg": (fit["azimuth_deg"] + 4.0) % 360.0,
        "imu_elevation_deg": fit["elevation_deg"] - 2.0,
    })
    assert agree["imu_check"]["verdict"] == "agree"

    conflict = sky_fit_from_args({
        "frames": frames,
        "imu_azimuth_deg": (fit["azimuth_deg"] + 180.0) % 360.0,
        "imu_elevation_deg": -fit["elevation_deg"],
    })
    assert conflict["imu_check"]["verdict"] == "conflict"
    assert conflict["imu_check"]["action"] == "recheck_imu_and_satellite_fit"


def test_no_input_gives_no_angle_and_asks_for_real_frames():
    out = sky_fit_from_args({})
    assert out["ok"] is False
    assert out["reason"] == "no_satellites"
    assert "GSV" in out["hint"]
    assert "fit" not in out


def test_flat_snr_is_refused_with_a_usable_hint():
    frames = [{"talker": "GP", "sats": [
        {"id": i, "elevation": 40.0, "azimuth": i * 30.0, "snr_db": 45.0}
        for i in range(8)]}]
    out = sky_fit_from_args({"frames": frames})
    assert out["ok"] is False
    assert out["fit"]["azimuth_deg"] is None
    assert "定向天线" in out["hint"]


def test_bad_satellite_items_are_skipped_not_zero_filled():
    out = sky_fit_from_args({"satellites": [
        {"azimuth_deg": 10.0, "elevation_deg": 30.0, "snr_db_hz": 40.0},
        {"azimuth_deg": None, "elevation_deg": 30.0, "snr_db_hz": 41.0},
        {"azimuth_deg": 20.0, "snr_db_hz": 42.0},
        {"azimuth_deg": 30.0, "elevation_deg": 30.0},
    ]})
    # 缺方位的两条被丢；缺 SNR 的一条保留但不参与拟合（None 不是 0 dB）
    assert out["ok"] is False
    assert out["satellite_count"] == 2
    assert out["satellites_with_snr"] == 1
    assert out["fit"]["satellites_used"] == 1
    assert out["fit"]["reason"].startswith("too_few_satellites")


def test_registration_is_read_only():
    from mbdsdr_ai.tool_registry import ToolRegistry

    class _Agent:
        def __init__(self):
            self.tool_registry = ToolRegistry()

    agent = _Agent()
    register_gnss_tools(agent)
    entry = agent.tool_registry.tools["gnss_sky_fit"]
    assert entry["category"] == "sky"
    assert entry.get("write") is not True


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

# SPDX-License-Identifier: MIT
"""多普勒曲线单一来源：satnogs 适配器不得再自带一份实现。

判据：
- ``DopplerCorrector.build_curve`` 与 ``DopplerSampler`` 在同一时刻给出
  完全相同的多普勒/距离变化率（同一实现，误差 0，而不是「约等于」）；
- ``compute_doppler_curve`` 的点也在同一采样器上，三处口径一致；
- 原始 TLE 两行被保留，供下游复用，不靠重新编码。

反证：把 ``build_curve`` 换回原来的 SGP4 + doppler_shift 自算，第一条
断言会因两套传播器的 ~0.7 Hz 差异而变红（已实测，见提交说明）。
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.gpredict_adapter import TLEParser, GeoStation
from mbdsdr_ai.satnogs_adapter import DopplerCorrector
from mbdsdr_ai.sat_passes import (
    DopplerSampler,
    GroundStation,
    compute_doppler_curve,
    predict_passes,
)

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = ("1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
          ).ljust(69)
ISS_L2 = ("2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
          ).ljust(69)
TLE_LINES = [ISS_L1, ISS_L2]
FREQ_HZ = 435.0e6


def _epoch_unix() -> float:
    tle = TLEParser.parse(ISS_NAME, ISS_L1, ISS_L2)
    return (tle.epoch_jd - 2440587.5) * 86400.0


def test_tle_keeps_raw_lines():
    tle = TLEParser.parse(ISS_NAME, ISS_L1, ISS_L2)
    assert tle.line1 == ISS_L1.strip()
    assert tle.line2 == ISS_L2.strip()


def test_satnogs_curve_is_the_same_implementation():
    tle = TLEParser.parse(ISS_NAME, ISS_L1, ISS_L2)
    corr = DopplerCorrector(tle, GeoStation(39.9, 116.4, 0.05), FREQ_HZ,
                            sample_dt_s=10.0)
    t0 = _epoch_unix() + 500.0
    curve = corr.build_curve(t0, t0 + 120.0)
    assert curve, "应有曲线点"

    sampler = DopplerSampler(TLE_LINES, GroundStation(39.9, 116.4, 50.0))
    assert sampler.ok
    for p in curve:
        r = sampler.sample(sampler.time_at_unix(p.time_unix_s), FREQ_HZ)
        assert r is not None
        _az, _el, _rng, rr, dop = r
        # 同一实现：不是「接近」，而是逐点一致（1e-9 只是浮点表示余量）。
        assert p.doppler_hz == pytest.approx(dop, abs=1e-9)
        assert p.range_rate_kms == pytest.approx(rr, abs=1e-9)
        assert p.rx_freq_hz == pytest.approx(FREQ_HZ + dop, abs=1e-9)


def test_compute_curve_uses_the_same_sampler():
    t0 = _epoch_unix() + 500.0
    passes = predict_passes(TLE_LINES, GroundStation(39.9, 116.4, 50.0),
                            start_unix=t0, hours=24)
    assert passes
    p = passes[0]
    pts = compute_doppler_curve(TLE_LINES, GroundStation(39.9, 116.4, 50.0),
                               p, FREQ_HZ, num_points=21)
    assert len(pts) == 21
    sampler = DopplerSampler(TLE_LINES, GroundStation(39.9, 116.4, 50.0))
    for pt in pts:
        r = sampler.sample(pt.time, FREQ_HZ)
        assert r is not None
        assert pt.doppler_hz == pytest.approx(r[4], abs=1e-9)

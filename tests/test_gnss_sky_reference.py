# SPDX-License-Identifier: MIT
"""GNSS 卫星 SNR → 粗指向的行为测试。

用合成观测（明确标为测试夹具）验证：能恢复已知指向、能在没有分辨力时拒绝、
能与手机 IMU 做一致性检查。合成数据只用于证明算法行为，不代表真机性能。
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

from mbdsdr_ai.gnss_sky_reference import (  # noqa: E402
    GnssSatellite, angular_separation_deg, compare_with_imu, fit_boresight,
    satellites_from_gsv_frames,
)


def _make_sats(baz: float, bel: float, *, count: int = 24,
               noise_db: float = 0.3, seed: int = 7,
               exponent: float = 2.0, offset_db: float = 48.0):
    """按 ``offset + 20·log10(cosθ)`` 造一组卫星，返回 (sats, 真值)。"""
    rng = random.Random(seed)
    sats = []
    guard = 0
    while len(sats) < count and guard < 5000:
        guard += 1
        az = rng.uniform(0.0, 360.0)
        el = rng.uniform(10.0, 85.0)
        sep = angular_separation_deg(baz, bel, az, el)
        if sep > 85.0:          # 背瓣不进模型，避免用不存在的深零点
            continue
        cos_t = max(math.cos(math.radians(sep)), 1e-3)
        snr = offset_db + 10.0 * exponent * math.log10(cos_t) \
            + rng.gauss(0.0, noise_db)
        sats.append(GnssSatellite(
            constellation="BDS" if len(sats) % 2 else "GPS",
            prn=len(sats) + 1, azimuth_deg=az, elevation_deg=el,
            snr_db_hz=round(max(0.0, min(99.0, snr)), 1)))
    return sats


def test_recovers_known_boresight_from_snr():
    sats = _make_sats(120.0, 45.0)
    fit = fit_boresight(sats)
    assert fit.ok is True
    assert fit.discriminative is True
    assert fit.satellites_used == len(sats)
    sep = angular_separation_deg(fit.azimuth_deg, fit.elevation_deg, 120.0, 45.0)
    assert sep < 8.0, f"拟合偏差 {sep:.1f}° 超过网格精度"
    assert fit.uncertainty_deg is not None and fit.uncertainty_deg >= 3.0


def test_flat_snr_is_rejected_not_guessed():
    sats = [GnssSatellite("BDS", i, azimuth_deg=i * 30.0,
                          elevation_deg=40.0, snr_db_hz=45.0)
            for i in range(8)]
    fit = fit_boresight(sats)
    assert fit.ok is False
    assert fit.discriminative is False
    assert fit.reason.startswith("flat_snr")
    assert fit.azimuth_deg is None


def test_too_few_satellites_is_rejected():
    sats = [GnssSatellite("BDS", i, azimuth_deg=i * 40.0,
                          elevation_deg=30.0, snr_db_hz=40.0 + i)
            for i in range(4)]
    fit = fit_boresight(sats)
    assert fit.ok is False
    assert fit.reason.startswith("too_few_satellites")


def test_missing_snr_does_not_count_as_zero():
    sats = [GnssSatellite("BDS", i, azimuth_deg=i * 40.0,
                          elevation_deg=30.0, snr_db_hz=None)
            for i in range(10)]
    fit = fit_boresight(sats)
    assert fit.ok is False
    assert fit.reason.startswith("too_few_satellites")
    assert fit.satellites_used == 0


def test_weak_geometry_is_rejected_as_not_discriminative():
    # SNR 只随方位轻微变化、与候选指向几乎无关 → 不应给出角度
    rng = random.Random(3)
    sats = [GnssSatellite("GPS", i, azimuth_deg=rng.uniform(0, 360),
                          elevation_deg=rng.uniform(10, 85),
                          snr_db_hz=40.0 + rng.uniform(-4, 4))
            for i in range(10)]
    fit = fit_boresight(sats)
    assert fit.ok is False
    assert fit.reason.split(":")[0] in ("not_discriminative", "flat_snr")


def test_imu_agreement_keeps_fusion():
    fit = fit_boresight(_make_sats(200.0, 50.0))
    assert fit.ok
    res = compare_with_imu(fit, fit.azimuth_deg + 4.0, fit.elevation_deg - 2.0)
    assert res["verdict"] == "agree"
    assert res["action"] == "keep_fusion"


def test_imu_conflict_is_reported_not_auto_corrected():
    fit = fit_boresight(_make_sats(200.0, 50.0))
    assert fit.ok
    res = compare_with_imu(fit, (fit.azimuth_deg + 180.0) % 360.0,
                           -fit.elevation_deg)
    assert res["verdict"] == "conflict"
    assert res["action"] == "recheck_imu_and_satellite_fit"
    assert "磁偏角" in res["hint"]


def test_no_imu_returns_coarse_only():
    fit = fit_boresight(_make_sats(30.0, 60.0))
    res = compare_with_imu(fit, None, None)
    assert res["verdict"] == "no_imu"
    assert res["action"] == "use_gnss_coarse"
    assert res["gnss"]["azimuth_deg"] == pytest.approx(fit.azimuth_deg)


def test_unusable_fit_is_reported():
    fit = fit_boresight([])
    res = compare_with_imu(fit, 10.0, 10.0)
    assert res["verdict"] == "unusable_gnss_fit"
    assert res["action"] == "keep_imu_only"


def test_angular_separation_wraps_around_north():
    assert angular_separation_deg(359.0, 0.0, 1.0, 0.0) == pytest.approx(2.0, abs=1e-6)
    # 高仰角上同样的方位差对应更小的球面角距
    assert angular_separation_deg(359.0, 30.0, 1.0, 30.0) == pytest.approx(
        2.0 * math.cos(math.radians(30.0)), abs=1e-3)
    assert angular_separation_deg(90.0, 0.0, 270.0, 0.0) == pytest.approx(180.0, abs=1e-6)


def test_fit_never_returns_below_horizon():
    # 构造一个 SNR 随「下方」增强的病态样本：拟合不得把镜筒指到地下
    sats = [GnssSatellite("BDS", i, azimuth_deg=180.0,
                          elevation_deg=-10.0 + i * 2.0,
                          snr_db_hz=60.0 - abs(i - 5) * 6.0)
            for i in range(12)]
    fit = fit_boresight(sats)
    if fit.ok:
        assert fit.elevation_deg >= -5.0
    else:
        assert "below_horizon" in fit.reason or "not_discriminative" in fit.reason


def test_pure_elevation_gain_cannot_fake_a_boresight():
    """SNR 只随仰角变（路径损耗）、与指向无关时，不得报出指向。"""
    rng = random.Random(11)
    sats = [
        GnssSatellite("GPS", i, azimuth_deg=rng.uniform(0, 360),
                      elevation_deg=el,
                      snr_db_hz=40.0 + 8.0 * math.sin(math.radians(el))
                      + rng.gauss(0.0, 0.2))
        for i, el in enumerate([12, 20, 28, 36, 44, 52, 60, 68, 76, 84], start=1)
    ]
    fit = fit_boresight(sats)
    assert fit.ok is False
    assert "not_discriminative" in fit.reason
    assert fit.azimuth_deg is None


def test_rejection_reports_both_baselines():
    """拒绝不能只说「不行」：要把仰角零模型与方位打乱基线两个数字都摊开。"""
    rng = random.Random(3)
    sats = [GnssSatellite("GPS", i, azimuth_deg=rng.uniform(0, 360),
                          elevation_deg=rng.uniform(10, 85),
                          snr_db_hz=40.0 + rng.uniform(-4, 4))
            for i in range(10)]
    fit = fit_boresight(sats)
    assert fit.ok is False
    assert fit.reason.startswith("not_discriminative")
    assert fit.elevation_only_db is not None
    assert fit.azimuth_permuted_db is not None
    assert f"{fit.azimuth_permuted_db:.1f}" in fit.reason


def test_gsv_frames_are_adapted_without_inventing_missing_fields():
    frames = [
        {"talker": "GB", "sats": [
            {"id": 5, "elevation": 40.0, "azimuth": 120.0, "snr_db": 45.0},
            {"id": 6, "elevation": None, "azimuth": 200.0, "snr_db": 40.0},
            {"id": 7, "elevation": 20.0, "azimuth": None, "snr_db": 30.0},
        ]},
        {"talker": "GP", "sats": [
            {"id": 12, "elevation": 55.0, "azimuth": 300.0, "snr_db": None},
        ]},
    ]
    sats = satellites_from_gsv_frames(frames)
    assert [s.prn for s in sats] == [5, 12]      # 缺角度的两条被跳过
    assert sats[0].constellation == "BDS"
    assert sats[1].constellation == "GPS"
    assert sats[1].snr_db_hz is None             # 缺 SNR 不补零


def test_gsv_frames_can_be_fed_to_fit():
    sats = _make_sats(75.0, 40.0)
    frames = [{"talker": "GB", "sats": [
        {"id": s.prn, "elevation": s.elevation_deg,
         "azimuth": s.azimuth_deg, "snr_db": s.snr_db_hz} for s in sats]}]
    fit = fit_boresight(satellites_from_gsv_frames(frames))
    assert fit.ok is True
    assert angular_separation_deg(fit.azimuth_deg, fit.elevation_deg,
                                  75.0, 40.0) < 8.0

# SPDX-License-Identifier: MIT
"""D2 判据：GNSS 误差预算必须随当天环境变化，缺项必须 uncorrected。

覆盖：
  * IonosphereParams / 实测气象的 known/unknown/stale；
  * 静日 vs 扰动日：电离层与对流层延迟方向正确；
  * SPP/PVT 注入环境后残差与位置误差都不劣于未校正；
  * 未注入/缺湿度时结果字段如实标 uncorrected/partial，σ 为 null；
  * RTK 结果带 error_budget（双差只溯源，不另加改正）。
"""
import math
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("MBDSDR_OFFLINE", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from mbdsdr_ai.environment_facts import (  # noqa: E402
    FACT_HUMIDITY,
    FACT_PRESSURE,
    FACT_TEMPERATURE,
    EnvironmentFact,
    EnvironmentSnapshot,
)
from mbdsdr_ai.geodesy import CoordinateConverter  # noqa: E402
from mbdsdr_ai.gnss_environment import (  # noqa: E402
    GnssAtmosphere,
    IonosphereParams,
    saastamoinen_delay_m,
)
from mbdsdr_ai.gps_nav import pvt_solve  # noqa: E402
from mbdsdr_ai.rtk_solver import RTKFloatSolver, RTKInput, RTKObs  # noqa: E402
from mbdsdr_ai.rtklib_adapter import SPPLocator, SPPInput  # noqa: E402

NOW = datetime(2026, 11, 14, 6, 0, 0, tzinfo=timezone.utc)
WEATHER_AT = "2026-11-14T05:30:00Z"
T_GPST = 1300000000.0
LAT = math.radians(31.2)
LON = math.radians(121.5)

IONO = IonosphereParams(
    alpha=(1.2e-8, -7.4e-9, -5.9e-8, 1.1e-7),
    beta=(1.1e5, -2.2e5, -1.3e5, 1.0e6),
    at_utc="2026-11-14T00:00:00Z",
    source="GPS LNAV subframe 4（测试向量）",
)
IONO_STORM = IonosphereParams(
    alpha=(5.0e-8, -2.0e-8, -9.0e-8, 3.0e-7),
    beta=(2.0e5, -4.0e5, -2.0e5, 2.0e6),
    at_utc="2026-11-14T00:00:00Z",
    source="GPS LNAV subframe 4（扰动测试向量）",
)


def weather_snapshot(pressure=1013.25, temperature=22.0, humidity=55.0,
                     omit=()) -> EnvironmentSnapshot:
    spec = {
        FACT_PRESSURE: (pressure, "hPa"),
        FACT_TEMPERATURE: (temperature, "degC"),
        FACT_HUMIDITY: (humidity, "%"),
    }
    facts = []
    for name, (value, unit) in spec.items():
        if name in omit:
            continue
        facts.append(EnvironmentFact(
            name, value=value, unit=unit, at_utc=WEATHER_AT,
            source="测试气象站", max_age_s=3 * 3600))
    return EnvironmentSnapshot.capture(facts, captured_at="2026-11-14T06:00:00Z")


def atmosphere(iono=IONO, **weather_kwargs) -> GnssAtmosphere:
    return GnssAtmosphere(weather_snapshot(**weather_kwargs), iono, now=NOW)


def synthetic_case():
    """造一组自洽伪距：真值位置 + 已知电离层/对流层延迟。"""
    truth = np.array(CoordinateConverter.llh_to_ecef_m(LAT, LON, 50.0))
    offsets = [
        (2.0e7, 0.0, 1.4e7), (-2.0e7, 0.0, 1.4e7),
        (0.0, 2.0e7, 1.4e7), (0.0, -2.0e7, 1.4e7),
        (1.4e7, 1.4e7, 1.5e7), (-1.4e7, -1.4e7, 1.5e7),
        (1.4e7, -1.4e7, 1.5e7), (-1.4e7, 1.4e7, 1.5e7),
        (0.0, 0.0, 2.4e7), (1.0e7, 0.0, 2.2e7), (0.0, 1.0e7, 2.2e7),
        (2.4e7, 0.0, 0.9e7),  # 低仰角，放大电离层差异
    ]
    atm = atmosphere()
    sats, prs = [], []
    for off in offsets:
        rs = truth + np.array(off)
        rng, e = CoordinateConverter.geodist(rs, truth)
        az, el = CoordinateConverter.satazel(LAT, LON, e)
        if el <= math.radians(10.0):
            continue
        dion = atm.iono_delay(LAT, LON, az, el, T_GPST)
        dtrop = atm.tropo_delay(LAT, 50.0, el)
        sats.append(rs)
        prs.append(rng + (dion or 0.0) + (dtrop or 0.0))
    assert len(sats) >= 5
    return truth, np.array(sats), np.array(prs)


# ---------------------------------------------------------------------------
# 事实与状态
# ---------------------------------------------------------------------------
def test_ionosphere_params_require_time_and_source():
    assert IONO.status(NOW) == "known"
    assert IonosphereParams(IONO.alpha, IONO.beta, at_utc=None,
                            source="x").status(NOW) == "unknown"
    assert IonosphereParams(IONO.alpha, IONO.beta, at_utc=IONO.at_utc,
                            source="").status(NOW) == "unknown"
    stale = IonosphereParams(IONO.alpha, IONO.beta,
                             at_utc="2026-11-12T00:00:00Z", source="x")
    assert stale.status(NOW) == "stale"
    assert not GnssAtmosphere(weather_snapshot(), stale, now=NOW).iono_enabled


def test_troposphere_needs_all_three_measured_facts():
    ok = atmosphere()
    assert ok.tropo_enabled
    assert ok.tropo_delay(LAT, 50.0, math.radians(40.0)) > 0.0

    missing = atmosphere(omit=(FACT_HUMIDITY,))
    assert not missing.tropo_enabled
    assert missing.tropo_delay(LAT, 50.0, math.radians(40.0)) is None
    summary = missing.summary([3.0], [])
    assert summary["status"] == "partial"
    assert summary["troposphere"]["applied"] is False
    assert summary["residual_sigma_m"] is None
    assert "humidity_pct" in summary["note"]


def test_saastamoinen_rejects_non_measured_inputs():
    assert saastamoinen_delay_m(1013.25, 22.0, 55.0, LAT, 50.0,
                                math.radians(40.0)) > 0.0
    assert saastamoinen_delay_m(float("nan"), 22.0, 55.0, LAT, 50.0,
                                math.radians(40.0)) is None
    assert saastamoinen_delay_m(1013.25, 22.0, float("nan"), LAT, 50.0,
                                math.radians(40.0)) is None


def test_quiet_vs_disturbed_environment_direction():
    quiet = atmosphere(iono=IONO, pressure=1010.0, temperature=15.0,
                       humidity=40.0)
    storm = atmosphere(iono=IONO_STORM, pressure=995.0, temperature=30.0,
                       humidity=90.0)
    el = math.radians(30.0)
    az = math.radians(45.0)
    di_q = quiet.iono_delay(LAT, LON, az, el, T_GPST)
    di_s = storm.iono_delay(LAT, LON, az, el, T_GPST)
    dt_q = quiet.tropo_delay(LAT, 50.0, el)
    dt_s = storm.tropo_delay(LAT, 50.0, el)
    assert di_s > di_q > 0.0
    assert dt_s > dt_q > 0.0


# ---------------------------------------------------------------------------
# SPP / PVT
# ---------------------------------------------------------------------------
def test_spp_applies_environment_and_reports_budget():
    truth, sats, prs = synthetic_case()
    inp = SPPInput(rs=sats, dts=np.zeros(len(sats)), pr=prs, t_gpst=T_GPST)
    x0 = tuple(truth + np.array([500.0, -500.0, 300.0]))

    uncorrected = SPPLocator().locate(inp, x0=x0)
    corrected = SPPLocator().locate(inp, x0=x0, atmosphere=atmosphere())

    err_u = np.linalg.norm(
        np.array([uncorrected["x"], uncorrected["y"], uncorrected["z"]]) - truth)
    err_c = np.linalg.norm(
        np.array([corrected["x"], corrected["y"], corrected["z"]]) - truth)
    assert err_c < err_u, "注入当天环境后位置误差必须更小"
    assert corrected["rms_m"] < uncorrected["rms_m"]

    budget = corrected["error_budget"]
    assert budget["status"] == "corrected"
    assert budget["ionosphere"]["delay_m_mean"] > 0.0
    assert budget["troposphere"]["delay_m_mean"] > 0.0
    assert budget["residual_sigma_m"] > 0.0
    assert budget["ionosphere"]["source"] == IONO.source
    assert budget["ionosphere"]["at_utc"] == IONO.at_utc


def test_spp_without_environment_is_explicitly_uncorrected():
    truth, sats, prs = synthetic_case()
    inp = SPPInput(rs=sats, dts=np.zeros(len(sats)), pr=prs, t_gpst=T_GPST)
    result = SPPLocator().locate(inp, x0=tuple(truth))
    budget = result["error_budget"]
    assert budget["status"] == "uncorrected"
    assert budget["residual_sigma_m"] is None
    assert "未注入" in budget["note"]


def test_pvt_applies_environment_and_reports_budget():
    truth, sats, prs = synthetic_case()
    obs = [{"pr_m": float(prs[i]), "sat_pos_ecef_m": sats[i].tolist()}
           for i in range(len(sats))]
    x0 = tuple(truth + np.array([400.0, 300.0, -200.0]))

    base = pvt_solve(obs, x0=x0, t_gpst=T_GPST)
    fixed = pvt_solve(obs, x0=x0, atmosphere=atmosphere(), t_gpst=T_GPST)

    err_b = np.linalg.norm(np.array(base["pos_ecef_m"]) - truth)
    err_f = np.linalg.norm(np.array(fixed["pos_ecef_m"]) - truth)
    assert err_f < err_b
    assert fixed["residual_rms_m"] < base["residual_rms_m"]
    assert fixed["error_budget"]["status"] == "corrected"
    assert fixed["error_budget"]["ionosphere"]["applied"] is True

    no_env = pvt_solve(obs, x0=x0, t_gpst=T_GPST)
    assert no_env["error_budget"]["status"] == "uncorrected"


# ---------------------------------------------------------------------------
# RTK：结果字段只溯源，不另加改正
# ---------------------------------------------------------------------------
def _rtk_input(truth, sats, prs) -> RTKInput:
    base_obs, rover_obs = [], []
    for i, rs in enumerate(sats):
        rng, _ = CoordinateConverter.geodist(rs, truth)
        base_obs.append(RTKObs(prn=i + 1, rs=rs, pseudorange=float(prs[i]),
                               carrier_phase=float(rng)))
        rover_obs.append(RTKObs(prn=i + 1, rs=rs, pseudorange=float(prs[i]),
                                carrier_phase=float(rng)))
    return RTKInput(base_ecef=truth, base_obs=base_obs, rover_obs=rover_obs)


def test_rtk_result_carries_environment_provenance():
    truth, sats, prs = synthetic_case()
    inp = _rtk_input(truth, sats, prs)

    with_env = RTKFloatSolver().solve(inp, atmosphere=atmosphere())
    assert "error_budget" in with_env
    assert with_env["error_budget"]["status"] == "corrected"
    assert with_env["error_budget"]["ionosphere"]["applied"] is False
    assert "双差" in with_env["error_budget"]["note"]

    without = RTKFloatSolver().solve(inp)
    assert without["error_budget"]["status"] == "uncorrected"
    assert without["error_budget"]["residual_sigma_m"] is None

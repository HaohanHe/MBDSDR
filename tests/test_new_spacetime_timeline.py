"""
新时空时间线模块测试
=====================
覆盖：
  1. TLE 字段解析（平均运动 / 倾角 / 偏心率）
  2. SGP4 单点位置几何合法性（仰角 / 方位 / 距离范围）
  3. 过境时间线 rise < max < set 时间有序
  4. 观测站坐标无默认值（缺参应 TypeError）
  5. 无 GNSS/NTP → "未同步"
  6. 有效 GNSS RMC → source="gnss"
  7. 频率-轨道：高度 > 0、LEO 周期 80-120 min、倾角解析正确
  8. 多普勒过境过程有正有负、max_doppler_hz > 0

测试夹具里硬编码的 ISS TLE 是公开已知数据（NORAD CATNR 25544），
观测点统一用赤道 (0°, 0°, 0 m)，不指向任何城市。
"""
import math
from datetime import datetime, timezone

import pytest
from sgp4.api import Satrec, jday

from mbdsdr_ai.new_spacetime_timeline import (
    TimelineEngine,
    PassPoint,
    SatellitePass,
    TimeSyncStatus,
    FreqOrbitPoint,
    _observer_ecef,
    _teme_to_ecef,
    _ecef_to_enu,
    _gmst_rad,
)


# ---- 已知夹具：ISS (ZARYA) 真实 TLE，epoch 2026-09-25 -----------------
ISS_NAME = "ISS"
ISS_L1 = ("1 25544U 98067A   26268.43198945  .00011731  00000+0  21853-3"
          " 0  9991")
ISS_L2 = ("2 25544  51.6316 163.9608 0004776 179.9710 180.1280"
          " 15.49288785587293")
ISS_FREQ_HZ = 437.8e6   # ISS 典型下行频率

# 赤道观测点（非任何城市坐标）
OBS_LAT, OBS_LON, OBS_ALT = 0.0, 0.0, 0.0


# ---------------------------------------------------------------------------
def test_tle_field_extraction():
    """用已知 TLE line2 验证三个静态解析函数。"""
    assert TimelineEngine.tle_mean_motion(ISS_L2) == pytest.approx(15.49288785, abs=1e-6)
    assert TimelineEngine.tle_inclination(ISS_L2) == pytest.approx(51.6316, abs=1e-4)
    assert TimelineEngine.tle_eccentricity(ISS_L2) == pytest.approx(0.0004776, abs=1e-7)


def test_sgp4_position_known():
    """在已知时刻用 ISS TLE 算位置，几何量须落在合法范围。"""
    sat = Satrec.twoline2rv(ISS_L1, ISS_L2)
    assert sat.error == 0

    t = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
    jd_int, jd_fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                         t.second + t.microsecond / 1e6)
    err, r_teme, v_teme = sat.sgp4(jd_int, jd_fr)
    assert err == 0

    gmst = _gmst_rad(jd_int + jd_fr)
    rs = _teme_to_ecef(r_teme[0], r_teme[1], r_teme[2], gmst)
    obs = _observer_ecef(OBS_LAT, OBS_LON, OBS_ALT)
    e, n, u = _ecef_to_enu(rs[0] - obs[0], rs[1] - obs[1], rs[2] - obs[2],
                            OBS_LAT, OBS_LON)
    rng = math.sqrt(e * e + n * n + u * u)
    el = math.degrees(math.asin(max(-1.0, min(1.0, u / rng))))
    az = math.degrees(math.atan2(e, n)) % 360.0

    assert -90.0 <= el <= 90.0
    assert 0.0 <= az < 360.0
    # ISS 在 ~400 km 高度，站心距离 400-40000 km 之间
    assert 400.0 <= rng <= 40000.0


def test_pass_timeline_rise_max_set():
    """赤道上空 24h 内至少 1 次过境，且 rise<max<set 时间有序。"""
    eng = TimelineEngine()
    passes = eng.compute_passes(
        OBS_LAT, OBS_LON, OBS_ALT,
        [(ISS_NAME, ISS_L1, ISS_L2)],
        hours_ahead=24.0, min_elevation=5.0, step_sec=60.0,
        frequencies={ISS_NAME: ISS_FREQ_HZ},
    )
    assert len(passes) >= 1, "赤道上 24h 内应至少看到 1 次 ISS 过境"

    # 至少存在一次形态完整（rise < max < set）的过境
    well = [p for p in passes
            if p.rise_time and p.max_time and p.set_time
            and p.rise_time < p.max_time < p.set_time]
    assert well, "应至少有一次 rise<max<set 时间有序的过境"
    p = well[0]
    assert p.max_elevation >= 5.0
    assert p.duration_sec > 0.0
    assert len(p.trajectory) >= 2


def test_no_observer_default():
    """compute_passes 不接受缺省观测站坐标：缺参必须 TypeError。"""
    eng = TimelineEngine()
    with pytest.raises(TypeError):
        eng.compute_passes(satellites=[(ISS_NAME, ISS_L1, ISS_L2)])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        eng.compute_passes()  # type: ignore[call-arg]


def test_time_sync_none():
    """不传 GNSS、不试 NTP → 未同步。"""
    eng = TimelineEngine()
    st = eng.compute_time_sync(gnss_nmea=None, try_ntp=False)
    assert isinstance(st, TimeSyncStatus)
    assert st.synchronized is False
    assert st.source == "none"
    assert st.detail == "未同步"
    assert st.utc_time is None
    assert st.offset_ms is None


def test_time_sync_gnss():
    """传有效 RMC 语句 → source='gnss'，已同步。"""
    eng = TimelineEngine()
    # 赤道 (0°,0°) 有效定位，2026-09-26 12:00 UTC
    rmc = ("$GNRMC,120000.00,A,0000.0000,N,00000.0000,E,"
           "0.0,0.0,260926,,,A*00")
    st = eng.compute_time_sync(gnss_nmea=rmc, try_ntp=False)
    assert st.synchronized is True
    assert st.source == "gnss"
    assert st.utc_time is not None
    assert "GNSS" in st.detail


def test_freq_orbit_computation():
    """频率-轨道：高度>0、LEO 周期 80-120 min、倾角解析正确。"""
    eng = TimelineEngine()
    pts = eng.compute_freq_orbit(
        [(ISS_NAME, ISS_L1, ISS_L2)],
        frequencies={ISS_NAME: ISS_FREQ_HZ},
    )
    assert len(pts) == 1
    p = pts[0]
    assert isinstance(p, FreqOrbitPoint)
    assert p.catnr == 25544
    assert p.inclination_deg == pytest.approx(51.6316, abs=1e-3)
    assert p.eccentricity == pytest.approx(0.0004776, abs=1e-6)
    assert p.altitude_km > 0.0
    # ISS 轨道 ~400 km 高度
    assert 300.0 < p.altitude_km < 500.0
    # LEO 周期 80-120 分钟
    assert 80.0 < p.period_min < 120.0
    assert p.frequency_mhz == pytest.approx(437.8, abs=1e-6)


def test_doppler_sign():
    """过境过程中多普勒有正有负，max_doppler_hz > 0。"""
    eng = TimelineEngine()
    passes = eng.compute_passes(
        OBS_LAT, OBS_LON, OBS_ALT,
        [(ISS_NAME, ISS_L1, ISS_L2)],
        hours_ahead=24.0, min_elevation=5.0, step_sec=60.0,
        frequencies={ISS_NAME: ISS_FREQ_HZ},
    )
    assert passes, "应有过境"

    # 选一次形态完整、仰角足够大的过境做多普勒符号检查
    well = [p for p in passes
            if p.rise_time and p.max_time and p.set_time
            and p.rise_time < p.max_time < p.set_time
            and p.max_elevation >= 8.0]
    assert well, "应至少有一次仰角>=8°的完整过境"
    p = max(well, key=lambda x: x.max_elevation)

    assert p.max_doppler_hz > 0.0, "最大多普勒幅值应 > 0"
    dos = [pt.doppler_hz for pt in p.trajectory]
    assert any(d > 0 for d in dos), "过境中应有正多普勒（接近段）"
    assert any(d < 0 for d in dos), "过境中应有负多普勒（远离段）"
    # 轨迹点结构正确
    for pt in p.trajectory:
        assert isinstance(pt, PassPoint)
        assert -90.0 <= pt.elevation_deg <= 90.0
        assert 0.0 <= pt.azimuth_deg < 360.0
        assert pt.range_km > 0.0

# SPDX-License-Identifier: MIT
"""
卫星状态快照的受晒字段一致性测试
================================

钉住三件事：

  1. ``orbit._state_from_satrec`` 快照里的 ``sunlit`` / ``sunlit_status`` /
     ``illumination_model`` / ``boundary_uncertainty_s`` 与同一时刻直接调用
     ``sat_illumination.evaluate_illumination`` **逐点一致**（同一份数学）。
  2. 字段契约：名字、取值域、未知语义，供天空/过境面板直接照抄。
  3. ``sat_passes._is_sunlit_geometric`` 的「可见过境」口径与此统一：
     同一模型、同一位置/时刻，只有本影算不亮（半影按照亮）。
  4. 耗时上限（宽松 5 倍余量）：快照单次远低于 30fps × 几十颗的预算。

运行: python -m pytest mbdsdr_ai/tests/test_orbit_sunlit_snapshot.py -v
"""
import time
from datetime import datetime, timezone

import pytest

from sgp4.api import Satrec

from .. import orbit, sat_illumination as si

TLE1 = "1 42960U 17061F   25117.46489195  .00000241  00000+0  78821-4 0  9994"
TLE2 = "2 42960  86.4033 198.9703 0001974 107.2578 252.8834 14.34218356395236"

STATUS_SET = {si.STATUS_SUNLIT, si.STATUS_UMBRA, si.STATUS_PENUMBRA}


def _sat_and_jd(unix):
    sat = Satrec.twoline2rv(TLE1, TLE2)
    jd = unix / 86400.0 + 2440587.5
    return sat, jd


def _direct(unix):
    """独立复算：sgp4 TEME 位置 + 目录太阳方向 → 唯一判据。"""
    sat, jd = _sat_and_jd(unix)
    err, r_teme, _v = sat.sgp4(int(jd), jd - int(jd))
    assert err == 0
    return si.evaluate_illumination(
        r_teme, si.sun_direction_eci(unix), at_unix=unix)


# ---------------------------------------------------------------------------
# 1. 逐点一致
# ---------------------------------------------------------------------------
def test_snapshot_matches_direct_illumination_point_by_point():
    """整轨 200 点（步进 300 s）逐点比对状态与 sunlit 二值。"""
    t0 = 1700000000.0
    samples = 200
    statuses = set()
    for i in range(samples):
        unix = t0 + 300.0 * i
        sat, jd = _sat_and_jd(unix)
        st = orbit._state_from_satrec(sat, "SAT", jd, 39.9, 116.4, 50.0)
        assert st is not None
        ref = _direct(unix)
        assert st["sunlit_status"] == ref.status, \
            f"t={unix:.0f} 快照={st['sunlit_status']} 直算={ref.status}"
        assert st["sunlit"] == ref.sunlit
        assert st["illumination_model"] == ref.model
        assert st["boundary_uncertainty_s"] == ref.boundary_uncertainty_s
        statuses.add(st["sunlit_status"])
    # 一天内既有受晒又有本影；若只有一种，说明样本窗口选错而非实现错
    assert si.STATUS_SUNLIT in statuses and si.STATUS_UMBRA in statuses


# ---------------------------------------------------------------------------
# 2. 字段契约
# ---------------------------------------------------------------------------
def test_snapshot_fields_contract():
    unix = 1700000000.0
    sat, jd = _sat_and_jd(unix)
    st = orbit._state_from_satrec(sat, "SAT", jd, 39.9, 116.4, 50.0)
    assert st is not None
    # 名字固定（界面层照抄，只增不改）
    for key in ("sunlit", "sunlit_status", "illumination_model",
                "boundary_uncertainty_s"):
        assert key in st, f"快照缺少契约字段 {key}"
    assert st["sunlit"] in (True, False, None)
    assert st["sunlit_status"] in STATUS_SET
    assert st["illumination_model"] == si.MODEL_CONICAL
    assert st["boundary_uncertainty_s"] == si.BOUNDARY_UNCERTAINTY_S[si.MODEL_CONICAL]
    # 二值映射：sunlit 只对本影/受晒有确定值，半影必须 None（不猜）
    if st["sunlit_status"] == si.STATUS_PENUMBRA:
        assert st["sunlit"] is None
    elif st["sunlit_status"] == si.STATUS_UMBRA:
        assert st["sunlit"] is False
    else:
        assert st["sunlit"] is True


# ---------------------------------------------------------------------------
# 3. 与 sat_passes「可见过境」口径统一
# ---------------------------------------------------------------------------
def test_sat_passes_uses_same_criterion():
    pytest.importorskip("skyfield")
    from skyfield.api import EarthSatellite

    from ..sat_passes import _is_sunlit_geometric, make_timescale

    ts = make_timescale()
    sat = EarthSatellite(TLE1, TLE2, "SAT", ts)
    for unix in (1700000000.0, 1700001800.0, 1700003600.0, 1700005400.0):
        t = ts.utc(datetime.fromtimestamp(unix, tz=timezone.utc))
        pos = tuple(float(x) for x in sat.at(t).position.km)
        ref = si.evaluate_illumination(pos, si.sun_direction_eci(unix))
        # sat_passes 是二值语义：只有本影判为不亮，半影归受晒
        assert _is_sunlit_geometric(sat, t) == (ref.status != si.STATUS_UMBRA)


# ---------------------------------------------------------------------------
# 4. 性能预算（宽松上限，只防数量级回退）
# ---------------------------------------------------------------------------
def test_snapshot_cost_fits_30fps_budget():
    """50 颗星 × 30fps = 1500 次/秒；单次快照需 ≪ 1 ms（这里卡 0.5 ms）。"""
    unix = 1700000000.0
    sat, jd = _sat_and_jd(unix)
    n = 200
    start = time.perf_counter()
    for _ in range(n):
        assert orbit._state_from_satrec(sat, "SAT", jd, 39.9, 116.4, 50.0)
    per_call = (time.perf_counter() - start) / n
    assert per_call < 0.0005, f"单次快照 {per_call * 1e3:.3f} ms 超过 0.5 ms"
    budget_ms = per_call * 1500
    assert budget_ms < 50.0, f"1500 次/秒将耗 {budget_ms:.1f} ms/s，超出预算"

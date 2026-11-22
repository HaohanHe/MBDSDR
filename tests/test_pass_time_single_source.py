# SPDX-License-Identifier: MIT
"""过境时刻/频率基准单一来源守卫（P1-13 / D4）。

三条链必须指向同一事实源：

1. 粗扫步长：``TimelineEngine.compute_passes`` 缺省值来自
   ``sat_passes.PASS_COARSE_STEP_S``，不再自藏 30 秒字面量；
2. 多普勒公式：``TimelineEngine._state_at`` 调
   ``sat_passes.doppler_hz_from_range_rate``，不重写符号约定；
3. 精度台账：``docs/limitations-register.md`` 的 TimelineEngine 行
   必须与 ``grid_manifest()`` 的数值一致，且不再写「无细扫 / 7.6°」。
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from sgp4.api import Satrec  # noqa: E402

from mbdsdr_ai import sat_passes  # noqa: E402
from mbdsdr_ai import new_spacetime_timeline as nstt  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
FIXED_UNIX = datetime(2008, 9, 20, 6, 0, 0,
                      tzinfo=timezone.utc).timestamp()
SATS = [(ISS_NAME, ISS_L1, ISS_L2)]
# 站址与 sat_passes 对拍用例相同：39.9°N / 116.4°E / 0 m。
OBS_LAT, OBS_LON, OBS_ALT = 39.9, 116.4, 0.0
# 二分后升落时刻的实测仰角残差容差（度）。0.5 s 二分容差 × LEO 升落仰角
# 速率（约 0.2–0.5°/s）给出 ~0.25° 保守上界；固定夹具实测 |残差| ≤ 0.013°，
# 这里取 0.05° 作为明确、可复现的验收门。
CROSSING_ELEV_TOL_DEG = 0.05


def _passes(**kwargs):
    return nstt.TimelineEngine().compute_passes(
        OBS_LAT, OBS_LON, OBS_ALT, SATS,
        hours_ahead=6.0, min_elevation=5.0, now_unix=FIXED_UNIX,
        **kwargs)


def _elevation_at(t: datetime) -> float:
    sat = Satrec.twoline2rv(ISS_L1, ISS_L2)
    obs = nstt._observer_ecef(OBS_LAT, OBS_LON, OBS_ALT)
    st = nstt._state_at(sat, obs, OBS_LAT, OBS_LON, t, 0.0)
    assert st is not None
    return st[0]


def _nearest_grid_time(t: datetime) -> datetime:
    start = datetime.fromtimestamp(FIXED_UNIX, tz=timezone.utc)
    step = sat_passes.PASS_COARSE_STEP_S
    offset = (t - start).total_seconds()
    return start + timedelta(seconds=round(offset / step) * step)


def test_timeline_default_step_is_single_source(monkeypatch):
    """缺省 step_sec 跟随 sat_passes；改常量后下一次调用立即跟随。"""
    got = _passes()
    assert got, "固定夹具应有可见过境"
    assert {p.sampling_step_s for p in got} == {
        sat_passes.PASS_COARSE_STEP_S}

    monkeypatch.setattr(sat_passes, "PASS_COARSE_STEP_S", 120.0)
    got2 = _passes()
    assert got2, "120 秒粗扫仍应看到这次过境"
    assert {p.sampling_step_s for p in got2} == {120.0}, (
        "TimelineEngine 仍在自写步长，没有跟随 sat_passes 单一事实源")


@pytest.mark.parametrize("bad", [0, -5.0, float("nan"), float("inf"), "abc"])
def test_timeline_bad_step_falls_back_to_single_source(bad):
    """0/负/NaN/Inf/非数字不得除零或退化成 1 步，统一回落单一事实源。"""
    got = _passes(step_sec=bad)
    assert got, f"非法步长 {bad!r} 不应把结果变成空列表"
    assert all(p.sampling_step_s == sat_passes.PASS_COARSE_STEP_S
               for p in got)
    assert all(math.isfinite(p.sampling_step_s) and p.sampling_step_s > 0
               for p in got)


def test_timeline_explicit_step_preserved():
    """显式合法步长原样生效（回落只针对缺省/非法值）。"""
    got = _passes(step_sec=30.0)
    assert got
    assert all(p.sampling_step_s == 30.0 for p in got)


def test_limitations_register_timeline_row_matches_manifest():
    """台账与实现对齐：二分容差、细扫口径与兜底语义都必须写实。"""
    text = (Path(_ROOT) / "docs" / "limitations-register.md").read_text(
        encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| `TimelineEngine`")]
    assert len(rows) == 1, f"TimelineEngine 台账行应恰好 1 条，实际 {rows}"
    row = rows[0]
    m = sat_passes.grid_manifest()

    assert "无细扫" not in row and "未细扫" not in row
    assert "7.6" not in row
    assert f"{m['coarse_step_s']:g} 秒" in row
    assert f"{m['fine_step_s']:g} 秒" in row
    assert "refine_culmination" in row
    assert "不是卫星位置精度" in row
    # 升落已二分：容差必须与 grid_manifest 同值，且兜底语义明确写实。
    assert "AOS/LOS 二分" in row
    assert f"{m['bisect_tol_s']:g} 秒" in row
    assert "保留粗扫网格点" in row
    assert "aos_refined" in row and "los_refined" in row


def test_timeline_aos_los_bisected_within_tolerance():
    """固定 ISS TLE：二分后升落仰角残差收敛，且明显优于粗扫网格点。

    反证：删掉 `_refine_crossing` 调用或把二分容差放大到区间宽度，
    `aos_refined/los_refined` 变 False、升落退回 60 秒网格点，本用例红。
    """
    got = _passes()
    full = [p for p in got
            if p.rise_time is not None and p.set_time is not None
            and p.aos_refined and p.los_refined]
    assert full, f"固定夹具应有完整且升落均已二分的过境，实际 {got}"
    assert all(p.crossing_tol_s == sat_passes.PASS_BISECT_TOL_S
               for p in full)
    assert all(p.crossing_refined for p in full)

    for p in full:
        for t in (p.rise_time, p.set_time):
            residual = abs(_elevation_at(t) - 5.0)
            assert residual <= CROSSING_ELEV_TOL_DEG, (
                f"{p.name} 升落时刻 {t.isoformat()} 仰角残差 "
                f"{residual:.4f}° 超过 {CROSSING_ELEV_TOL_DEG}°")

    # 至少一个升落点确实偏离了粗扫网格，且网格点残差大于二分后残差。
    shifts = []
    grid_residuals = []
    for p in full:
        for t in (p.rise_time, p.set_time):
            grid = _nearest_grid_time(t)
            shifts.append(abs((t - grid).total_seconds()))
            grid_residuals.append(abs(_elevation_at(grid) - 5.0))
    assert max(shifts) > 0.0, "升落时刻仍落在粗扫网格点上，二分未生效"
    assert max(grid_residuals) > CROSSING_ELEV_TOL_DEG, (
        "粗扫网格点仰角残差未超过验收容差，二分精度优势无法体现")


def test_timeline_crossing_tolerance_enlarged_keeps_coarse_grid(monkeypatch):
    """反证：把二分容差放大到粗扫区间宽度，必须退回网格点并如实标记。"""
    monkeypatch.setattr(sat_passes, "PASS_BISECT_TOL_S", 3600.0)
    got = _passes()
    assert got, "固定夹具应有可见过境"
    p = max(got, key=lambda x: x.max_elevation)
    assert p.aos_refined is False and p.los_refined is False
    assert p.crossing_refined is False
    assert p.crossing_tol_s == 0.0

    start = datetime.fromtimestamp(FIXED_UNIX, tz=timezone.utc)
    step = sat_passes.PASS_COARSE_STEP_S
    coarse_residuals = []
    for t in (p.rise_time, p.set_time):
        assert t is not None
        offset = (t - start).total_seconds()
        assert abs(offset / step - round(offset / step)) < 1e-9, (
            f"{t.isoformat()} 不是粗扫网格点")
        coarse_residuals.append(abs(_elevation_at(t) - 5.0))
    assert max(coarse_residuals) > CROSSING_ELEV_TOL_DEG, (
        "容差放大后仍得到收敛级残差，说明二分未被真正禁用")


def test_bisect_crossing_rejects_invalid_bracket():
    """端点同侧 / 采样失败 / 区间退化一律返回 None，不拿中点冒充收敛。"""
    assert nstt._bisect_elevation_crossing(
        lambda s: 10.0, 0.0, 60.0, 6.0, 7.0, 5.0) is None
    assert nstt._bisect_elevation_crossing(
        lambda s: None, 0.0, 60.0, 4.0, 6.0, 5.0) is None
    assert nstt._bisect_elevation_crossing(
        lambda s: 4.0 + s, 0.0, 0.0, 4.0, 4.0, 5.0) is None


def test_timeline_doppler_delegates_to_single_formula(monkeypatch):
    """多普勒符号/公式只有 sat_passes 一处：替换实现后 TimelineEngine 必须跟随。"""
    monkeypatch.setattr(sat_passes, "doppler_hz_from_range_rate",
                        lambda freq_hz, range_rate: -4242.0)
    got = _passes(frequencies={ISS_NAME: 437.8e6})
    assert got, "固定夹具应有可见过境"
    points = [pt for p in got for pt in p.trajectory]
    assert points, "过境轨迹不应为空"
    assert {pt.doppler_hz for pt in points} == {-4242.0}, (
        "TimelineEngine 仍在自写多普勒公式，没有走单一实现")


def test_speed_of_light_alias_single_source():
    """公开光速常量是从 sat_passes.C_LIGHT_MPS 派生的别名，不是第二份字面量。"""
    assert nstt.SPEED_OF_LIGHT_KM_S == sat_passes.C_LIGHT_MPS / 1000.0


# ---------------------------------------------------------------------------
# 两条过境路径逐秒对拍：TimelineEngine vs sat_passes.predict_passes
# ---------------------------------------------------------------------------
# 同一 TLE / 站址 / UTC 起点 / 仰角门，两侧各自完整跑一遍，按 AOS 升序配对：
#   - AOS/LOS 时刻差 ≤ 1.0 s：两条路径都声明 0.5 秒二分容差
#     （`sat_passes.PASS_BISECT_TOL_S`），各自最多离真过零点 0.5 s，
#     两个独立二分结果的最坏配对差即 1.0 s；
#   - 中天时刻差 ≤ 2.0 s：两侧中天都走同一个 `refine_culmination()` 的
#     2 秒细扫网格，但网格以各自路径的 AOS 为锚点；AOS 最多差 0.47 s，
#     峰值可能整体挪一格，故按「一格 = 2.0 s」给配对容差；
#   - 最大仰角差 ≤ 0.5°：传播内核同为 SGP4，但参考系链路不同
#     （TimelineEngine：TEME→ECEF 用 GMST IAU-1982；sat_passes：skyfield
#     GCRS + 完整岁差/章动/topocentric 变换）。实测 24 h 60 s 网格仰角差
#     最大 |Δ| = 0.0058°（2008 夹具）/ 0.0034°（2026 夹具），离 0.5° 两个
#     数量级，故 0.5° 是保守门，不是把实测值放宽后的数。
PASS_PARITY_CASES = [
    # (id, tle, lat, lon, alt_m, start_unix, hours, min_el)
    (
        "iss-2008-tle-39.9N116.4E-24h",
        (ISS_NAME, ISS_L1, ISS_L2),
        OBS_LAT, OBS_LON, OBS_ALT,
        FIXED_UNIX, 24.0, 5.0,
    ),
    (
        "iss-2026-tle-equator-24h",
        ("ISS", *(
            "1 25544U 98067A   26268.43198945  .00011731  00000+0  21853-3"
            " 0  9991",
            "2 25544  51.6316 163.9608 0004776 179.9710 180.1280"
            " 15.49288785587293",
        )),
        0.0, 0.0, 0.0,
        datetime(2026, 9, 26, 0, 0, 0, tzinfo=timezone.utc).timestamp(),
        24.0, 10.0,
    ),
]
AOS_LOS_PARITY_TOL_S = 1.0
MAX_TIME_PARITY_TOL_S = 2.0
MAX_ELEV_PARITY_TOL_DEG = 0.5
_WINDOW_END_EPS_S = 1e-6


def _pair_passes(tl_passes, sp_passes, window_end_unix):
    """按 AOS 升序配对两条路径的过境；条数不等 / 窗末截断口径不一致直接判红。

    「允许窗末截断差异时显式处理，不得静默跳过」：本函数不丢弃任何一条，
    先断言条数一致，再逐条校验两侧对「落在窗末」的判定是否一致——若一侧
    把窗末截断的过境算成完整过境而另一侧没有，会在这里带着两侧时刻报错。
    """
    assert len(tl_passes) == len(sp_passes), (
        "两条路径过境条数不一致，不许静默跳过："
        f"TimelineEngine={len(tl_passes)} 条 "
        f"{[p.rise_time for p in tl_passes]}，"
        f"sat_passes={len(sp_passes)} 条 "
        f"{[p.rise_time.utc_datetime() for p in sp_passes]}")
    tl_sorted = sorted(tl_passes, key=lambda p: p.rise_time)
    sp_sorted = sorted(sp_passes, key=lambda p: p.rise_time.utc_datetime())
    pairs = list(zip(tl_sorted, sp_sorted))
    for idx, (a, b) in enumerate(pairs):
        assert a.set_time is not None and a.max_time is not None
        b_set = b.set_time.utc_datetime()
        a_trunc = abs(a.set_time.timestamp() - window_end_unix) <= _WINDOW_END_EPS_S
        b_trunc = abs(b_set.timestamp() - window_end_unix) <= _WINDOW_END_EPS_S
        assert a_trunc == b_trunc, (
            f"第 {idx} 条过境窗末截断口径不一致：TimelineEngine set={a.set_time}"
            f"（截断={a_trunc}），sat_passes set={b_set}（截断={b_trunc}）")
    return pairs


@pytest.mark.parametrize(
    "name,tle,lat,lon,alt_m,start_unix,hours,min_el",
    PASS_PARITY_CASES,
    ids=[c[0] for c in PASS_PARITY_CASES],
)
def test_timeline_matches_sat_passes_predict_passes(
        name, tle, lat, lon, alt_m, start_unix, hours, min_el):
    """同一 ISS TLE/站址/UTC 起点/仰角门下，两条路径逐条过境对拍。

    反证：把 TimelineEngine 的 AOS/LOS 改回粗扫网格点（不二分），AOS 与
    sat_passes 的二分结果会差到数十秒，本用例红；把 `_refine_peak` 的中天
    改回粗扫网格点，中天时刻差 >2 s，本用例同样红。
    """
    sat_name, line1, line2 = tle
    tl_passes = nstt.TimelineEngine().compute_passes(
        lat, lon, alt_m, [tle],
        hours_ahead=hours, min_elevation=min_el, now_unix=start_unix)
    sp_passes = sat_passes.predict_passes(
        [sat_name, line1, line2],
        sat_passes.GroundStation(lat, lon, alt_m),
        start_unix=start_unix, hours=hours, min_alt=min_el)

    assert tl_passes, f"{name}: 固定夹具应有过境，TimelineEngine 返回空"
    assert sp_passes, f"{name}: 固定夹具应有过境，sat_passes 返回空"

    window_end_unix = start_unix + hours * 3600.0
    pairs = _pair_passes(tl_passes, sp_passes, window_end_unix)

    for idx, (a, b) in enumerate(pairs):
        b_rise = b.rise_time.utc_datetime()
        b_set = b.set_time.utc_datetime()
        b_max = b.max_alt_time.utc_datetime()
        d_aos = abs((a.rise_time - b_rise).total_seconds())
        d_los = abs((a.set_time - b_set).total_seconds())
        d_max = abs((a.max_time - b_max).total_seconds())
        d_el = abs(a.max_elevation - b.max_alt)
        assert d_aos <= AOS_LOS_PARITY_TOL_S, (
            f"{name} 第 {idx} 条 AOS 差 {d_aos:.3f} s > "
            f"{AOS_LOS_PARITY_TOL_S} s（TL {a.rise_time} vs SP {b_rise}）")
        assert d_los <= AOS_LOS_PARITY_TOL_S, (
            f"{name} 第 {idx} 条 LOS 差 {d_los:.3f} s > "
            f"{AOS_LOS_PARITY_TOL_S} s（TL {a.set_time} vs SP {b_set}）")
        assert d_max <= MAX_TIME_PARITY_TOL_S, (
            f"{name} 第 {idx} 条中天差 {d_max:.3f} s > "
            f"{MAX_TIME_PARITY_TOL_S} s（TL {a.max_time} vs SP {b_max}）")
        assert d_el <= MAX_ELEV_PARITY_TOL_DEG, (
            f"{name} 第 {idx} 条最大仰角差 {d_el:.4f}° > "
            f"{MAX_ELEV_PARITY_TOL_DEG}°（TL {a.max_elevation:.3f}° vs "
            f"SP {b.max_alt:.3f}°）")

    # 实测值记录（2026-11-22，本沙箱）：2008 夹具 6 条过境 AOS 差 ≤0.469 s、
    # LOS 差 0 s、中天差 ≤1.531 s、最大仰角差 ≤0.0055°；2026 夹具 4 条过境
    # 时刻差全 0 s、最大仰角差 ≤0.0010°。两条路径没有系统性偏差，上述验收门
    # 保留为设计口径而不是按实测放宽的数值。


def test_time_basis_text_consumers_use_grid_manifest():
    """AI/控制器/工具报告的过境时刻文案必须消费 grid_manifest，不自写秒数。"""
    manifest = sat_passes.grid_manifest()
    note = manifest["accuracy_note"]
    assert "粗扫" in note and "细扫" in note
    old = "60 秒粗扫 + 2 秒细扫 + 0.5 秒二分"
    for rel in ("mbdsdr_ai/agent.py", "mbdsdr_ai/core/controller.py",
                "mbdsdr_ai/sdr_tools.py"):
        text = (Path(_ROOT) / rel).read_text(encoding="utf-8")
        assert old not in text, f"{rel} 仍自写旧时刻文案"
        assert "accuracy_note" in text, f"{rel} 未消费 grid_manifest()"

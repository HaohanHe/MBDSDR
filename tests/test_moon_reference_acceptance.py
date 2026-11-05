# SPDX-License-Identifier: MIT
"""月球绝对位置对拍测试（承载 R7 的独立参考与反证）。

两套实现：
  1. 被测：``mbdsdr_ai.astronomy.planets.compute_moon``（本仓 Meeus 截断级数）
  2. 参考：JPL DE421（skyfield 读取）——**外部星历真值**，不是两套自家实现
     的最大分歧。阈值 120″ = DE421 精度 + 本仓级数截断余量（实测约 27″）。

反证：把参考时刻故意拨快 1 小时，断言必须红（月球约 0.55°/h ≈ 1980″）；
拨回原时刻必须重新变绿。缺 ``de421.bsp``/``skyfield`` 时 skip，不补数。
"""
from __future__ import annotations

import datetime
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "scripts", "hw_acceptance")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ring6_sky_mode as sky  # noqa: E402

pytestmark = pytest.mark.skipif(
    sky._moon_reference(2451545.0) is None,
    reason="缺 de421.bsp / skyfield：月球绝对位置不可验（不补数）",
)


def _jd_now() -> float:
    return datetime.datetime.now(datetime.timezone.utc).timestamp() / 86400.0 + 2440587.5


def test_moon_matches_de421_within_tolerance():
    from mbdsdr_ai.astronomy.planets import compute_moon

    jd = _jd_now()
    m = compute_moon(jd)
    ref = sky._moon_reference(jd)
    assert ref is not None
    sep = sky._separation_arcsec(m.ra_deg, m.dec_deg, ref["ra_deg"], ref["dec_deg"])
    assert sep <= sky.MOON_TOL_ARCSEC, (
        f"月球赤道坐标偏离 DE421 {sep:.1f}″ > {sky.MOON_TOL_ARCSEC:.0f}″")


def test_moon_angular_diameter_matches_de421():
    from mbdsdr_ai.astronomy.planets import compute_moon

    jd = _jd_now()
    m = compute_moon(jd)
    ref = sky._moon_reference(jd)
    assert ref is not None
    err = abs(m.angular_diameter_arcsec - ref["dia_arcsec"])
    assert err <= sky.MOON_DIA_TOL_ARCSEC, (
        f"视直径差 {err:.1f}″ > {sky.MOON_DIA_TOL_ARCSEC:.0f}″")


def test_negative_control_hour_shift_goes_red_then_back_green():
    """反证：1 小时错位必须红；改回必须绿（证明断言可证伪）。"""
    from mbdsdr_ai.astronomy.planets import compute_moon

    jd = _jd_now()
    m = compute_moon(jd)

    shifted = sky._moon_reference(jd + 1.0 / 24.0)
    assert shifted is not None
    sep_shift = sky._separation_arcsec(
        m.ra_deg, m.dec_deg, shifted["ra_deg"], shifted["dec_deg"])
    assert sep_shift >= sky.NEG_CTRL_MIN_ARCSEC, (
        f"拨快 1h 只偏 {sep_shift:.1f}″，反证未变红")

    back = sky._moon_reference(jd)
    assert back is not None
    sep_back = sky._separation_arcsec(
        m.ra_deg, m.dec_deg, back["ra_deg"], back["dec_deg"])
    assert sep_back <= sky.MOON_TOL_ARCSEC, (
        f"拨回后仍偏离 {sep_back:.1f}″，未恢复绿")

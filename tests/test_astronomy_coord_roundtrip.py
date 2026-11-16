# SPDX-License-Identifier: MIT
"""天文坐标核确定性符合测试（equatorial ↔ altaz）。

锁定 live 科学核 ``equatorial_to_altaz`` / ``altaz_to_equatorial`` 的三件事，
此前仅有前向 smoke 调用、逆变换零覆盖、无往返/无方位角约定断言：

1. **方位角约定**：``AltAzCoord.az_deg`` 文档写死「0=北，顺时针」，即
   N=0 / E=90 / S=180 / W=270。用**解析已知**的子午圈（HA=0）与卯酉圈
   （HA=±90）cardinal 参考值锁死，前向与逆变换**各自独立**断言——不是
   只靠往返自洽（往返自洽在两个方向同犯一个错时仍会绿）。
2. **赤道↔地平往返**：固定 jd/lat/lon（不绑墙钟、不绑网络），前向再逆变换
   必须恢复 RA/Dec。
3. **逆变换独立正确性**：``altaz_to_equatorial`` 此前无任何测试调用。

参考值全部由球面三角解析推导（见各用例注释），不依赖外部星历。
默认 ``Observer`` 无实测气象 → ``compute_refraction`` 返回 ``None`` →
``alt_deg`` 为几何仰角，往返纯几何、确定性。

反证（见 D:/mbdsdr/output/astro_coord_probe.py）：把前向方位角镜像
（E↔W）或把逆变换 RA 平移 10°，本文件必须变红；还原后复绿。
"""
from __future__ import annotations

import math

import pytest

from mbdsdr_ai.astronomy import (
    AltAzCoord,
    EquatorialCoord,
    Observer,
    jd_to_lst,
)

# 固定参考时刻/站点（确定性）。lon=0 → LST=GMST，简化但不失一般性；
# 约定/往返对任意 lon 成立，此处取 0 只为让 LST 可读。
_JD = 2451545.0          # J2000.0，固定历元，绝不取 wall-clock
_LAT = 40.0
_OBS = Observer(latitude_deg=_LAT, longitude_deg=0.0)

# 角容差（度）。1e-5° = 0.036 角秒：在精确子午/卯酉 cardinal 点上 cos(az)≈±1，
# acos 在该处导数 sqrt-奇异，~1e-16 的输入浮点噪声会放大成 ~1e-6° 的 az 残差
# （实测南中 az=179.99999879°）。1e-5° 既容纳这条固有浮点噪声，又远小于任何
# 真实约定回归（N↔S 翻转 / E↔W 镜像都是 ≥90° 的错），断言仍然有意义。
_TOL_DEG = 1e-5


def _lst_deg() -> float:
    """参考时刻的地方恒星时（度）。直接调内核，不重写公式。"""
    return math.degrees(jd_to_lst(_JD, 0.0)) % 360.0


def _ang_diff(a: float, b: float) -> float:
    """两方位/赤经差归一到 [0,180]，处理 0/360 绕回。"""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


# ---------------------------------------------------------------------------
# 1. 方位角约定：解析已知的 cardinal 参考值（前向）
# ---------------------------------------------------------------------------
# 球面三角（φ=40°）：
#   sin(alt) = sinφ·sinδ + cosφ·cosδ·cos(HA)
#   cos(az)  = (sinδ − sinφ·sin(alt)) / (cosφ·cos(alt))   [az 从北顺时针]
#   sin(az)  = −cosδ·sin(HA) / cos(alt)
# HA = LST − RA，故 RA = LST − HA。
#
#   子午南中（HA=0, δ=0）：alt=90−|φ−δ|=50°，cos(az)=−1 → az=180°（南）
#   子午北中（HA=0, δ=80）：alt=90−|φ−δ|=50°，cos(az)=+1 → az=0°（北）
#   卯酉东升（HA=−90, δ=0）：sin(alt)=cosφ·cos90=0 → alt=0，sin(az)=+1 → az=90°（东）
#   卯酉西落（HA=+90, δ=0）：alt=0，sin(az)=−1 → az=270°（西）
def test_forward_south_transit_is_az180():
    ra = _lst_deg()                      # HA = 0
    eq = EquatorialCoord(ra_deg=ra, dec_deg=0.0)
    az = eq.to_altaz(_OBS, _JD)
    assert abs(az.alt_deg - 50.0) < _TOL_DEG
    assert _ang_diff(az.az_deg, 180.0) < _TOL_DEG


def test_forward_north_transit_is_az0():
    ra = _lst_deg()                      # HA = 0，δ>φ → 北中天
    eq = EquatorialCoord(ra_deg=ra, dec_deg=80.0)
    az = eq.to_altaz(_OBS, _JD)
    assert abs(az.alt_deg - 50.0) < _TOL_DEG
    assert _ang_diff(az.az_deg, 0.0) < _TOL_DEG


def test_forward_east_rising_is_az90():
    ra = (_lst_deg() + 90.0) % 360.0     # HA = −90
    eq = EquatorialCoord(ra_deg=ra, dec_deg=0.0)
    az = eq.to_altaz(_OBS, _JD)
    assert abs(az.alt_deg) < _TOL_DEG
    assert _ang_diff(az.az_deg, 90.0) < _TOL_DEG


def test_forward_west_setting_is_az270():
    ra = (_lst_deg() - 90.0) % 360.0     # HA = +90
    eq = EquatorialCoord(ra_deg=ra, dec_deg=0.0)
    az = eq.to_altaz(_OBS, _JD)
    assert abs(az.alt_deg) < _TOL_DEG
    assert _ang_diff(az.az_deg, 270.0) < _TOL_DEG


# ---------------------------------------------------------------------------
# 2. 逆变换独立正确性（此前零覆盖）：cardinal 参考值反向解算
# ---------------------------------------------------------------------------
# 逆球面三角：
#   sin(δ) = sinφ·sin(alt) + cosφ·cos(alt)·cos(az)
#   sin(HA) = −cos(alt)·sin(az)/cos(δ)
#   cos(HA) = (sin(alt) − sinφ·sin(δ))/(cosφ·cos(δ))
#   RA = LST − HA
#
#   (alt=50, az=180) → sinδ=sin40·sin50+cos40·cos50·cos180=0 → δ=0；
#                      sin(HA)=−cos50·sin180=0，cos(HA)=sin50/(cos40)=1 → HA=0 → RA=LST
#   (alt=0,  az=90)  → sinδ=cos40·cos90≈0 → δ=0；
#                      sin(HA)=−cos0·sin90=−1，cos(HA)=0 → HA=−90 → RA=LST+90
def test_inverse_south_transit_recovers_dec0_ra_lst():
    az = AltAzCoord(alt_deg=50.0, az_deg=180.0)
    eq = az.to_equatorial(_OBS, _JD)
    assert abs(eq.dec_deg) < _TOL_DEG
    assert _ang_diff(eq.ra_deg, _lst_deg()) < _TOL_DEG


def test_inverse_east_horizon_recovers_dec0_ra_lst_plus90():
    az = AltAzCoord(alt_deg=0.0, az_deg=90.0)
    eq = az.to_equatorial(_OBS, _JD)
    assert abs(eq.dec_deg) < _TOL_DEG
    assert _ang_diff(eq.ra_deg, (_lst_deg() + 90.0) % 360.0) < _TOL_DEG


def test_inverse_north_transit_recovers_dec80():
    # (alt=50, az=0) → sinδ=sin40·sin50+cos40·cos50·cos0=cos(40−50)? 直接解：
    #   sinδ=sin40·sin50+cos40·cos50=sin(40+50)? 用和角：=cos(40−50)=cos10≈0.985
    #   → δ=80°；HA=0 → RA=LST。与前向北中用例互为逆。
    az = AltAzCoord(alt_deg=50.0, az_deg=0.0)
    eq = az.to_equatorial(_OBS, _JD)
    assert abs(eq.dec_deg - 80.0) < _TOL_DEG
    assert _ang_diff(eq.ra_deg, _lst_deg()) < _TOL_DEG


# ---------------------------------------------------------------------------
# 3. 赤道↔地平往返（固定 jd/lat/lon，不绑墙钟）
# ---------------------------------------------------------------------------
# δ 网格避开 φ=40（天顶奇异：cos(alt)→0 时 az 被强制 0）与 ±90（cosδ→0）。
@pytest.mark.parametrize("ra", [0.0, 45.0, 90.0, 135.0, 180.0,
                                225.0, 270.0, 315.0, 359.9])
@pytest.mark.parametrize("dec", [-60.0, -30.0, 0.0, 30.0, 60.0])
def test_roundtrip_recovers_ra_dec(ra, dec):
    eq = EquatorialCoord(ra_deg=ra, dec_deg=dec)
    altaz = eq.to_altaz(_OBS, _JD)
    back = altaz.to_equatorial(_OBS, _JD)
    assert _ang_diff(back.ra_deg, ra) < _TOL_DEG, (
        f"RA 往返不闭合: {ra} -> {altaz.az_deg} -> {back.ra_deg}")
    assert abs(back.dec_deg - dec) < _TOL_DEG, (
        f"Dec 往返不闭合: {dec} -> {back.dec_deg}")


def test_roundtrip_default_observer_is_geometric():
    """默认 Observer 无实测气象：折射必须 None、alt 为几何值（不拿标准大气冒充）。"""
    eq = EquatorialCoord(ra_deg=_lst_deg(), dec_deg=0.0)
    az = eq.to_altaz(_OBS, _JD)
    assert az.refraction_deg is None
    assert az.refraction_status == "unknown"
    assert abs(az.alt_deg - 50.0) < _TOL_DEG   # 几何南中，未被折射抬高
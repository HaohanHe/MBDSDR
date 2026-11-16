# SPDX-License-Identifier: MIT
"""astronomy 坐标格式化器六十进制进位一致性（#99）。

ra_to_hms / dec_to_dms 的秒字段用 :04.1f 格式化，边界处会四舍五入到 60.0
产出非法 "DD°MM'60.0"" / "HHhMMm60.0s"（秒必须 00.0–59.9）。二者均 LIVE：
EquatorialCoord.to_dict() / AltAzCoord.to_dict() 调用。本测试钉住：
进位链正确、正常值不变、生产 to_dict() 路径同步、RA 时按 24 归绕、不过度进位。

与 rf_sky_view.format_dms_deg（desktop HUD 另一套格式）互不替代：本模块是
model 侧赤道/地平坐标 dict 的格式化器，层/格式/消费方均不同。
"""
import math
import re

from mbdsdr_ai.astronomy import (
    ra_to_hms, dec_to_dms, lst_to_hms, jd_to_gmst, jd_to_lst,
    EquatorialCoord, AltAzCoord,
)

_DMS_RE = re.compile(r'^([+-])(\d{2})°(\d{2})\'(\d{2}\.\d)"$')
_HMS_RE = re.compile(r'^(\d{2})h(\d{2})m(\d{2}\.\d)s$')
_COLON_RE = re.compile(r'^(\d{2}):(\d{2}):(\d{2}\.\d)$')

# 半格容差：显示秒四舍五入到 0.1"，故重建值与真值差 <= 0.05"
_HALF_GRID_DEG = 0.05 / 3600.0 + 1e-12


def _dms_parts(s):
    m = _DMS_RE.fullmatch(s)
    assert m, f"度分秒格式非法: {s!r}"
    return m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))


def _hms_parts(s):
    m = _HMS_RE.fullmatch(s)
    assert m, f"时分秒格式非法: {s!r}"
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def _dms_to_deg(s):
    sign, d, mi, sec = _dms_parts(s)
    v = d + mi / 60.0 + sec / 3600.0
    return -v if sign == '-' else v


def _hms_to_deg(s):
    h, mi, sec = _hms_parts(s)
    return (h + mi / 60.0 + sec / 3600.0) * 15.0


def _circ_diff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ---------------------------------------------------------------------------
# 1. 边界进位：秒四舍五入到 60.0 必须向分/度/时进位，绝不产出 60.0 秒
# ---------------------------------------------------------------------------
def test_dec_boundary_carries_to_minute():
    dec = 40 + 42 / 60 + 59.97 / 3600      # 40°42'59.97" -> 秒进位
    assert dec_to_dms(dec) == '+40°43\'00.0"'
    _, d, mi, sec = _dms_parts(dec_to_dms(dec))
    assert sec < 60.0 and mi < 60


def test_ra_boundary_carries_to_minute():
    ra = (12 + 34 / 60 + 59.97 / 3600) * 15.0   # 12h34m59.97s
    assert ra_to_hms(ra) == '12h35m00.0s'
    h, mi, sec = _hms_parts(ra_to_hms(ra))
    assert sec < 60.0 and mi < 60 and h < 24


def test_dec_pole_full_carry_chain():
    # 89°59'59.97" -> 秒进位 -> 分进位 -> 度进位到 90（天极）
    dec = 89 + 59 / 60 + 59.97 / 3600
    assert dec_to_dms(dec) == '+90°00\'00.0"'


def test_ra_wrap_at_24h():
    # 23h59m59.97s -> 秒/分/时进位到 24h，RA 循环归绕到 00h
    ra = (23 + 59 / 60 + 59.97 / 3600) * 15.0
    assert ra_to_hms(ra) == '00h00m00.0s'


def test_negative_dec_carries():
    dec = -(40 + 42 / 60 + 59.97 / 3600)
    assert dec_to_dms(dec) == '-40°43\'00.0"'


# ---------------------------------------------------------------------------
# 2. 正常值不变 + 不过度进位（反向用例）
# ---------------------------------------------------------------------------
def test_normal_values_unchanged():
    assert dec_to_dms(39.9042) == '+39°54\'15.1"'
    assert ra_to_hms(150.0) == '10h00m00.0s'
    assert dec_to_dms(0.0) == '+00°00\'00.0"'
    assert dec_to_dms(-45.5) == '-45°30\'00.0"'
    assert ra_to_hms(0.0) == '00h00m00.0s'


def test_non_carry_guard():
    # 59.9" 不到进位阈值，必须原样保留（证明没有把正常秒也进位掉）
    assert dec_to_dms(45 + 59 / 60 + 59.9 / 3600) == '+45°59\'59.9"'
    assert ra_to_hms((6 + 59 / 60 + 59.9 / 3600) * 15.0) == '06h59m59.9s'


# ---------------------------------------------------------------------------
# 3. 密集扫掠：任意度/分边界秒字段恒 <60，且显示值可半格内复算
# ---------------------------------------------------------------------------
def test_dense_sweep_seconds_always_valid():
    fracs = (0.99999, 0.999999, 0.9999999, 0.9999, 0.5, 0.0)
    for d in range(0, 90):
        for mi in (0, 30, 59):
            for frac in fracs:
                dec = d + mi / 60.0 + frac / 3600.0
                s = dec_to_dms(dec)
                _, dd, mm, sec = _dms_parts(s)
                assert sec < 60.0, f"非法秒 {s!r} @ {dec}"
                assert mm < 60, f"非法分 {s!r} @ {dec}"
                assert abs(_dms_to_deg(s) - dec) <= _HALF_GRID_DEG, \
                    f"复算超半格 {s!r} vs {dec}"
    for h in range(0, 24):
        for mi in (0, 30, 59):
            for frac in fracs:
                ra = (h + mi / 60.0 + frac / 3600.0) * 15.0
                s = ra_to_hms(ra)
                hh, mm, sec = _hms_parts(s)
                assert sec < 60.0, f"非法秒 {s!r} @ {ra}"
                assert mm < 60 and hh < 24, f"非法分/时 {s!r} @ {ra}"
                assert _circ_diff(_hms_to_deg(s), ra) <= _HALF_GRID_DEG, \
                    f"复算超半格 {s!r} vs {ra}"


# ---------------------------------------------------------------------------
# 4. 生产 to_dict() 路径同步（EquatorialCoord / AltAzCoord）
# ---------------------------------------------------------------------------
def test_to_dict_propagates_carry():
    dec = 40 + 42 / 60 + 59.97 / 3600
    ra = (12 + 34 / 60 + 59.97 / 3600) * 15.0
    eq = EquatorialCoord(ra_deg=ra, dec_deg=dec).to_dict()
    assert eq['ra_hms'] == '12h35m00.0s'
    assert eq['dec_dms'] == '+40°43\'00.0"'
    assert _hms_parts(eq['ra_hms'])[2] < 60.0
    assert _dms_parts(eq['dec_dms'])[3] < 60.0
    az = AltAzCoord(alt_deg=dec, az_deg=180.0).to_dict()
    assert az['alt_dms'] == '+40°43\'00.0"'
    assert _dms_parts(az['alt_dms'])[3] < 60.0


# ---------------------------------------------------------------------------
# 5. lst_to_hms（恒星时 HH:MM:SS.s）—— agent.py gmst_hms/lst_hms 读数源
# ---------------------------------------------------------------------------
def _colon_parts(s):
    m = _COLON_RE.fullmatch(s)
    assert m, f"恒星时格式非法: {s!r}"
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def _colon_to_hours(s):
    h, mi, sec = _colon_parts(s)
    return h + mi / 60.0 + sec / 3600.0


def _hours_to_rad(hours):
    return hours * math.pi / 12.0


def test_lst_boundary_carries_to_minute():
    hours = 12 + 34 / 60 + 59.97 / 3600     # 12h34m59.97s
    assert lst_to_hms(_hours_to_rad(hours)) == '12:35:00.0'
    h, mi, sec = _colon_parts(lst_to_hms(_hours_to_rad(hours)))
    assert sec < 60.0 and mi < 60 and h < 24


def test_lst_wrap_at_24h():
    hours = 23 + 59 / 60 + 59.97 / 3600
    assert lst_to_hms(_hours_to_rad(hours)) == '00:00:00.0'


def test_lst_normal_unchanged():
    assert lst_to_hms(_hours_to_rad(10.0)) == '10:00:00.0'
    assert lst_to_hms(0.0) == '00:00:00.0'


def test_lst_non_carry_guard():
    hours = 6 + 59 / 60 + 59.9 / 3600       # 59.9s 不到进位阈值
    assert lst_to_hms(_hours_to_rad(hours)) == '06:59:59.9'


def test_lst_dense_sweep_seconds_always_valid():
    fracs = (0.99999, 0.999999, 0.9999999, 0.9999, 0.5, 0.0)
    for h in range(0, 24):
        for mi in (0, 30, 59):
            for frac in fracs:
                hours = h + mi / 60.0 + frac / 3600.0
                s = lst_to_hms(_hours_to_rad(hours))
                hh, mm, sec = _colon_parts(s)
                assert sec < 60.0, f"非法秒 {s!r} @ {hours}h"
                assert mm < 60 and hh < 24, f"非法分/时 {s!r} @ {hours}h"
                diff = abs(_colon_to_hours(s) - hours)
                diff = min(diff, 24.0 - diff)      # 24h 归绕循环差
                assert diff <= 0.05 / 3600.0 + 1e-12, \
                    f"复算超半格 {s!r} vs {hours}h"


def test_lst_sidereal_chain_deterministic():
    # 固定历元（J2000），不绑墙钟：复现 agent.py gmst_hms/lst_hms 读数链
    jd = 2451545.0
    lon = 120.0
    for rad in (jd_to_gmst(jd), jd_to_lst(jd, lon)):
        s = lst_to_hms(rad)
        h, mi, sec = _colon_parts(s)
        assert sec < 60.0 and mi < 60 and h < 24, f"恒星时读数非法: {s!r}"
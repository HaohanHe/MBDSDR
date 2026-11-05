# SPDX-License-Identifier: MIT
"""
tests/test_moon_illumination_real_ephemeris.py
==============================================
月相照度**真值**回归：历表距角 → illum = 0.5·(1 − cos 距角)。

背景（2026-11-05 修复）：三个历表后端曾把「日月距角」当成「月处相位角」
直接套 0.5·(1+cos)，导致照度反转——2026-11-09 新月历表给 illum=0.997
（渲染成满月）。修法是把换算收敛到 ``moon_illum_from_elongation`` 一处，
三个后端共用；渲染层 cos α = 2·illum − 1 不变（它本来就是对的）。

反证（旧实现必红）：
  - 新月：旧值 0.997（断言 illum ≤ 0.02 必红）；
  - 单调性：旧的 0.5·(1+cos 距角) 随距角**递减**，断言递增必红。

真实历表：本机 DE421（``~/.mbdsdr/ephemeris/de421.bsp``）；无星历时
依赖它的用例按环境诚实 skip，不 mock。
"""
import importlib.util
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai import solar_system as sol  # noqa: E402
from rf_sky_view import moon_lit_path, moon_phase_cos_alpha  # noqa: E402

SITE = (40.0, 116.0, 0.0)

# 真实历表取证的四个时刻（DE421，2026-11，UTC）：
NEW_MOON = datetime(2026, 11, 9, 13, 30, tzinfo=timezone.utc).timestamp()
FULL_MOON = datetime(2026, 11, 24, 12, 40, tzinfo=timezone.utc).timestamp()
FIRST_QUARTER = datetime(2026, 11, 17, 13, 20, tzinfo=timezone.utc).timestamp()
LAST_QUARTER = datetime(2026, 11, 1, 19, 20, tzinfo=timezone.utc).timestamp()
WAXING_START = datetime(2026, 11, 9, 9, 40, tzinfo=timezone.utc).timestamp()

SAMPLE_SITE = (40.0, 116.0, 0.0)


def _moon(t_unix, site=SAMPLE_SITE):
    mp = sol.get_moon_position(t_unix, site)
    if mp is None:
        pytest.skip("历表不可用（无 de421.bsp 且 astropy 回退也不可用），本用例不伪造数据")
    return mp


def _elong_from_phase_angle(mp) -> float:
    """历表相位角 → 日月距角（相角 ≈ 180° − 距角）。"""
    return 180.0 - float(mp.phase_angle_deg)


def _old_illum(elong_deg: float) -> float:
    """修复前的错公式（0.5·(1+cos 距角)），仅用于反证对照。"""
    return 0.5 * (1.0 + math.cos(math.radians(elong_deg)))


# --------------------------------------------------------------------------
# 1. 换算函数本身：端点、夹取、严格单调递增
# --------------------------------------------------------------------------
def test_illum_from_elongation_endpoints_and_clamping():
    assert sol.moon_illum_from_elongation(0.0) == pytest.approx(0.0, abs=1e-12)
    assert sol.moon_illum_from_elongation(90.0) == pytest.approx(0.5, abs=1e-12)
    assert sol.moon_illum_from_elongation(180.0) == pytest.approx(1.0, abs=1e-12)
    # 越界夹住，不产生负照度或 >1
    assert sol.moon_illum_from_elongation(-5.0) == 0.0
    assert sol.moon_illum_from_elongation(190.0) == pytest.approx(1.0, abs=1e-12)
    # 相位角语义：距角 0 → 相位角 180（月处背向太阳）
    assert sol.moon_phase_angle_from_elongation(0.0) == pytest.approx(180.0)
    assert sol.moon_phase_angle_from_elongation(180.0) == pytest.approx(0.0)


def test_illumination_is_strictly_increasing_with_elongation():
    """反证：旧的 0.5·(1+cos 距角) 是单调递减，这条必红。"""
    last = -1.0
    for tenth in range(0, 1801):
        elong = tenth / 10.0
        illum = sol.moon_illum_from_elongation(elong)
        assert illum > last, f"距角 {elong}° 处 illum 未递增：{illum} <= {last}"
        last = illum


# --------------------------------------------------------------------------
# 2. 真实历表四态：新月 / 满月 / 上下弦
# --------------------------------------------------------------------------
def test_new_moon_2026_11_09_is_dark_not_full():
    mp = _moon(NEW_MOON)
    elong = _elong_from_phase_angle(mp)
    illum = float(mp.illumination)
    # 距角 ≈ 6°，illum ≈ 0.003（旧实现给 0.997，必红）
    assert elong <= 8.0, f"新月时刻距角应接近 0°，实测 {elong:.2f}°"
    assert illum <= 0.02, f"新月照度应 ≤ 0.02，实测 {illum:.4f}"
    assert illum == pytest.approx(sol.moon_illum_from_elongation(elong), abs=1e-6)
    assert _old_illum(elong) >= 0.9, "反证：旧公式在新月给接近满月（说明旧实现必红）"


def test_full_moon_2026_11_24_is_bright():
    mp = _moon(FULL_MOON)
    elong = _elong_from_phase_angle(mp)
    assert elong >= 174.0, f"满月时刻距角应接近 180°，实测 {elong:.2f}°"
    assert float(mp.illumination) >= 0.98, \
        f"满月照度应 ≥ 0.98，实测 {mp.illumination:.4f}"


@pytest.mark.parametrize("t_unix,label", [
    (FIRST_QUARTER, "上弦"),
    (LAST_QUARTER, "下弦"),
])
def test_quarter_moon_illumination_is_half(t_unix, label):
    mp = _moon(t_unix)
    elong = _elong_from_phase_angle(mp)
    assert 85.0 <= elong <= 95.0, f"{label}距角应 ≈90°，实测 {elong:.2f}°"
    assert float(mp.illumination) == pytest.approx(0.5, abs=0.03), \
        f"{label}照度应 ≈0.5，实测 {mp.illumination:.4f}"


def test_real_waxing_illumination_increases_new_to_full():
    """新月→满月逐 6h 采样：照度必须单调不降（旧实现恰好反向）。"""
    prev = -1.0
    for k in range(0, int((FULL_MOON - WAXING_START) / 21600) + 1):
        mp = _moon(WAXING_START + k * 21600)
        val = float(mp.illumination)
        assert val >= prev - 1e-6, \
            f"第 {k} 个样本照度回落：{val:.4f} < {prev:.4f}（照度方向反转）"
        prev = val


# --------------------------------------------------------------------------
# 3. 三后端一致性（skyfield / jplephem / astropy）
# --------------------------------------------------------------------------
def _backend(kind):
    """构造指定引擎的后端实例；不可用的返回 (None, 原因)。"""
    be = sol._EphemerisBackend(auto_download=False)
    if kind == "skyfield":
        if be._engine != "skyfield":
            return None, "无 de421.bsp（skyfield 引擎不可用）"
        return be, ""
    if kind == "jplephem":
        if importlib.util.find_spec("jplephem") is None:
            return None, "jplephem 未安装"
        if not sol._file_valid(sol.DE421_PATH):
            return None, "无 de421.bsp（jplephem 引擎不可用）"
        from jplephem.spk import SPK
        try:
            spk = SPK.open(sol.DE421_PATH)
        except OSError as exc:
            return None, f"de421.bsp 无法打开（{type(exc).__name__}）"
        handle = sol._EphemerisHandle(engine="jplephem", spk=spk)
        be._apply_handle(handle)
        return be, ""
    if kind == "astropy":
        if importlib.util.find_spec("astropy") is None:
            return None, "astropy 未安装"
        be._init_astropy()
        if be._astropy is None:
            return None, "astropy 回退初始化失败"
        return be, ""
    raise AssertionError(kind)


def _moon_with_backend(be, t_unix):
    old = sol._backend
    sol._backend = be
    try:
        return sol.get_moon_position(t_unix, SITE)
    finally:
        sol._backend = old


def test_three_backends_agree_within_001():
    values, reasons = {}, {}
    for kind in ("skyfield", "jplephem", "astropy"):
        be, why = _backend(kind)
        if be is None:
            reasons[kind] = why
            continue
        mp = _moon_with_backend(be, FIRST_QUARTER)
        if mp is None:
            reasons[kind] = "该后端未返回月球位置"
            continue
        values[kind] = float(mp.illumination)

    if len(values) < 2:
        pytest.skip("可用后端不足两个：" + "；".join(f"{k}={v}" for k, v in reasons.items()))
    spread = max(values.values()) - min(values.values())
    assert spread <= 0.01, \
        f"三后端同一时刻照度不一致（{spread:.4f} > 0.01）：{values}；跳过原因 {reasons}"
    # 值本身必须落在上弦口径（防三后端一起错成同一个反值）
    assert all(abs(v - 0.5) <= 0.03 for v in values.values()), values


# --------------------------------------------------------------------------
# 4. 渲染层亮区面积：新月 ≤ 0.02（旧 illum=0.997 必红）
# --------------------------------------------------------------------------
def _lit_area_fraction(cos_alpha, size=201):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 255))
    r = size / 2.0 - 2.0
    p.drawPath(QTransform(r, 0, 0, r, size / 2.0, size / 2.0)
               .map(moon_lit_path(cos_alpha)))
    p.end()
    lit = total = 0
    for y in range(size):
        for x in range(size):
            if (x - size / 2.0) ** 2 + (y - size / 2.0) ** 2 > r * r:
                continue
            total += 1
            if img.pixelColor(x, y).alpha() > 127:
                lit += 1
    return lit / total if total else 0.0


def test_new_moon_lit_area_is_at_most_002():
    mp = _moon(NEW_MOON)
    illum = float(mp.illumination)
    frac = _lit_area_fraction(moon_phase_cos_alpha(illum))
    assert frac <= 0.02, f"新月亮区面积应 ≤ 0.02，实测 {frac:.4f}"
    # 反证：旧 illum=0.997 的同一渲染原语会画出 ~整盘（断言必红）
    old_frac = _lit_area_fraction(moon_phase_cos_alpha(_old_illum(_elong_from_phase_angle(mp))))
    assert old_frac >= 0.95, f"旧公式亮区应接近整盘（反证基线），实测 {old_frac:.4f}"
    assert frac < old_frac - 0.9

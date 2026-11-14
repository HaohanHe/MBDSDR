# SPDX-License-Identifier: MIT
"""GMST 口径对拍：先分口径、再收敛重复、再常态对拍。

从 QGIS 3.44.15 的「CRS 唯一真值 + 按 CRS 对点名 operation」学来的治理次序。
关键不是「所有 GMST 必须同一个数」——口径 B/C/D 与 A 物理上或数值路径上
不同，合并就是破坏；关键是**同一口径内只有一个实现**，且跨口径差异被钉住。

反证能力（改错必须红）：
* 改坏内核多项式            -> test_kernel_matches_independent_oracle 红
* 在模块里又抄一遍多项式     -> test_no_duplicated_polynomial 红
* 把 C/D 合并成 A            -> test_*_is_a_different_model 红
* 把 B 悄悄换成 A            -> test_simplified_form_keeps_its_own_value 红
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from mbdsdr_ai import (astronomy, atmosphere, celestial_geometry, decoders,
                       gmst, gpredict_adapter, new_spacetime_timeline, orbit,
                       orbit_determination, solar_system)
from mbdsdr_ai.skyengine import jtime

ROOT = Path(__file__).resolve().parents[1]
#: 四个历元，覆盖 J2000 与当前/未来若干年。
JDS = (2451545.0, 2461358.5, 2470000.5, 2460000.123456)


def _independent_folded_rad(jd: float) -> float:
    """改动前 orbit._gmst_days 的逐字复刻，刻意不 import 产品内核。

    这是独立 oracle：如果只测「各入口互相一致」，大家共用同一个错误内核
    时依然全绿——那就不是对拍，是自我确认。
    """
    t = (jd - 2451545.0) / 36525.0
    sec = (67310.54841
           + (876600.0 * 3600.0 + 8640184.812866) * t
           + 0.093104 * t * t
           - 6.2e-6 * t * t * t)
    return math.radians((sec % 86400.0) / 240.0) % (2.0 * math.pi)


def _arcsec_delta(a_rad: float, b_rad: float) -> float:
    d = (math.degrees(b_rad) - math.degrees(a_rad) + 180.0) % 360.0 - 180.0
    return d * 3600.0


# ── A 口径：唯一实现 + 六个委托入口 ────────────────────────────────────
A_RAD_ENTRIES = (
    orbit._gmst_days,
    orbit_determination.gmst_rad,
    new_spacetime_timeline._gmst_rad,
    astronomy.jd_to_gmst,
)


def test_kernel_matches_independent_oracle():
    for jd in JDS:
        assert gmst.gmst_rad_iau1982(jd) == pytest.approx(
            _independent_folded_rad(jd), abs=0.0, rel=0.0), f"JD {jd}"


def test_all_A_entrances_are_bit_identical():
    """委托必须与内核逐位相同，不是「差不多」。"""
    for jd in JDS:
        base = gmst.gmst_rad_iau1982(jd)
        for fn in A_RAD_ENTRIES:
            assert fn(jd) == base, f"{fn.__module__}.{fn.__name__} @ JD {jd}"


def test_degree_exit_is_the_same_kernel():
    for jd in JDS:
        assert jtime.gmst_deg(jd) == pytest.approx(
            gmst.gmst_deg_iau1982(jd), abs=0.0, rel=0.0)
        assert gmst.gmst_deg_iau1982(jd) == pytest.approx(
            math.degrees(gmst.gmst_rad_iau1982(jd)) % 360.0, abs=0.0, rel=0.0)


def test_golden_j2000():
    """Vallado 标准值：J2000 0h UT1 的 GMST = 280.460618375°。"""
    assert gmst.gmst_deg_iau1982(2451545.0) == pytest.approx(
        280.460618375, abs=1e-9)


def test_no_duplicated_polynomial():
    """同一口径内不许再抄一遍折叠式多项式。

    gpredict_adapter 是登记过的口径 D（整链镜像上游 gpredict），不在本检查内。
    """
    needle = "8640184.812866"
    for mod in (orbit, orbit_determination, new_spacetime_timeline, astronomy,
                decoders, jtime):
        src = Path(mod.__file__).read_text(encoding="utf-8-sig")
        assert needle not in src, f"{mod.__name__} 又抄了一遍 A 口径多项式"


# ── B / C / D 口径：登记边界，防误合并 ──────────────────────────────────
def test_simplified_form_keeps_its_own_value():
    """B：IAU1982 简化式，与 A 差 1.8e-5" – 2.3e-4"，不是逐位相同。"""
    for jd in JDS:
        base = gmst.gmst_rad_iau1982(jd)
        for fn in (atmosphere._gmst_deg, solar_system._gmst_deg):
            d = abs(_arcsec_delta(base, math.radians(fn(jd))))
            assert 1e-6 < d < 1e-3, f"{fn.__module__} @ JD {jd}: {d}\""


def test_capitaine_is_a_different_model():
    """C：Capitaine 2003，差 0.0145" – 0.1261"，属星象仪岁差章动链。"""
    for jd in JDS:
        d = abs(_arcsec_delta(gmst.gmst_rad_iau1982(jd),
                              math.radians(celestial_geometry.gmst(jd))))
        assert 0.005 < d < 0.2, f"JD {jd}: {d}\" —— 是不是误合并成 A 了？"


def test_gpredict_thetajd_is_a_separate_numeric_path():
    """D：gpredict ThetaG_JD，整链镜像上游，必须保留自己的数值路径。

    实测 D-A 在六个 JD 上是 -0.0076" ~ +1e-9"，**不是恒定偏差**（与 JD 的
    小数部分相关，因为 ``ut = frac(jd+0.5)`` 与 OMEGA_E_DAY 的取模路径不同）。
    所以「必须差多少」是假判据，会随取样历元翻车；改钉结构：只要还有
    gpredict 的当日 0h 常数与自转项，就说明没被并进 A。
    """
    src = Path(gpredict_adapter.__file__).read_text(encoding="utf-8-sig")
    for marker in ("24110.54841", "OMEGA_E_DAY", "modulus(gmst + SEC_DAY"):
        assert marker in src, f"gpredict 特征项 {marker} 不见了：是不是被换成 A 内核？"
    assert "from .gmst import" not in src and "from mbdsdr_ai.gmst import" not in src
    # 上界仍然要钉：差值再大就说明某一边的公式真错了。
    for jd in JDS:
        d = abs(_arcsec_delta(gmst.gmst_rad_iau1982(jd),
                              gpredict_adapter.thetajd(jd)))
        assert d < 0.02, f"JD {jd}: {d}\" 超出两条 IAU1982 路径的合理差"

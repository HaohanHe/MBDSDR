"""LST = GMST + lon_east (mod 2π) 经度符号约定确定性符合测试。

sidereal foundation 的「经度维」。GMST 数值本身已由 test_gmst_single_source.py
用 Vallado J2000 真值（280.460618375°，abs 1e-9）锁定，本文件**不重复锁 GMST**，
只锁「LST 相对 GMST 的经度增量」这一独立约定：

    LST(jd, lon) - GMST(jd) ≡ +lon_east   (mod 2π)

既有覆盖的边界（诚实标注，避免重复造守卫）：
  * test_sky_readouts.py::test_lst_matches_astronomy_backend 把 jd_to_lst 与
    desktop.rf_sky_view.local_sidereal_time_deg 对拍，但只用东经 LON=125.3，
    两者又共享同一 GMST 定义——它能抓「全局翻经度符号」，却抓不到
    「abs(lon) / 西经被吞」这类只在负经度暴露的错（东经输入下 abs 恒等）。
  * test_full_integration*.py 只对单个东经做 0..2π range smoke，且绑墙钟
    unix_to_jd()——drop-mod 是否越界取决于当时 GMST，非确定性。

本文件补的正是这两处缺口：**西经/负经度维** + **固定 jd 的确定性 mod 归绕**。
全部用固定 jd/lon，不绑墙钟、不绑网络（测试确定性纪律）。纯增量，不改产品代码。
"""
import math

import pytest

from mbdsdr_ai.astronomy import jd_to_gmst, jd_to_lst

# J2000.0 历元，固定值；GMST 数值另由 test_gmst_single_source 锁定，此处只用作参考点。
_JD = 2451545.0

# 实测所有网格经度的 LST-GMST-lon 残差为 0.0（float 精确）；1e-12 rad ≈ 2e-7 arcsec，
# 远小于任何真实约定回归（翻号/abs 至少差 2×|lon|，drop-mod 差 2π），故既能过本质量
# 又能判任何真错。锚定本质量（残差）而非派生量，符合测试确定性纪律。
_TOL_RAD = 1e-12

# 经度网格：东经/西经/零/对跖 ±180/近 ±180 边界/小角，全部固定值（含负经度——既有测试缺）。
_LONS = [0.0, 90.0, -90.0, 120.0, -120.0, 180.0, -180.0, 0.5, -0.5, 179.9, -179.9]


def _circ_diff(a: float, b: float) -> float:
    """两弧度角的圆周差绝对值，归到 [0, π]。"""
    return abs((a - b + math.pi) % (2.0 * math.pi) - math.pi)


@pytest.mark.parametrize("lon", _LONS)
def test_lst_minus_gmst_equals_east_longitude(lon):
    """绝对东正约定：LST - GMST ≡ +lon（mod 2π），独立于 desktop 层自证。"""
    gmst = jd_to_gmst(_JD)
    lst = jd_to_lst(_JD, lon)
    assert _circ_diff(lst - gmst, math.radians(lon)) < _TOL_RAD


@pytest.mark.parametrize("lon", _LONS)
def test_lst_in_0_2pi(lon):
    """LST 必须归绕到 [0, 2π)，对东经/西经都成立（drop-mod 在大东经处越界）。"""
    lst = jd_to_lst(_JD, lon)
    assert 0.0 <= lst < 2.0 * math.pi


def test_east_longitude_advances_lst():
    """东经使 LST 超前 GMST（+90°）；显式方向断言，排除 abs/翻号。"""
    gmst = jd_to_gmst(_JD)
    lst = jd_to_lst(_JD, 90.0)
    assert _circ_diff(lst - gmst, math.radians(90.0)) < _TOL_RAD
    assert _circ_diff(lst, gmst + math.radians(90.0)) < _TOL_RAD


def test_west_longitude_retards_lst():
    """西经使 LST 落后 GMST（-90°）。既有测试只用东经，此维此前无独立断言。"""
    gmst = jd_to_gmst(_JD)
    lst = jd_to_lst(_JD, -90.0)
    assert _circ_diff(lst - gmst, math.radians(-90.0)) < _TOL_RAD
    # 西经结果必须 != 同绝对值东经（abs(lon) 突变在此暴露：两者会被吞成相等）。
    assert _circ_diff(lst, jd_to_lst(_JD, 90.0)) > math.radians(1.0)


def test_zero_longitude_is_gmst_identity():
    """lon=0 → LST 与 GMST 逐位相等（经度增量恒等元）。"""
    assert jd_to_lst(_JD, 0.0) == jd_to_gmst(_JD)


def test_antimeridian_east_west_agree():
    """+180 与 -180 是对跖同一点，LST 必须一致（mod 2π 正确处理 ±180 边界）。"""
    assert _circ_diff(jd_to_lst(_JD, 180.0), jd_to_lst(_JD, -180.0)) < _TOL_RAD


def test_mod_wrap_is_deterministic_when_sum_exceeds_full_circle():
    """固定 jd 构造一个使 GMST+lon 必然 > 2π 的东经，确定性验证 mod 归绕。

    不依赖墙钟：lon 由当前 GMST 反推，保证 gmst + lon_rad = 2π + 0.5。
    drop-mod 突变会返回 2π+0.5（越界 + 归绕值错），在此必红；真实现在 mod 后
    恰为 0.5 rad。GMST 数值本身不在这里断言（另由 single_source 锁定）。
    """
    gmst = jd_to_gmst(_JD)
    lon_rad = 2.0 * math.pi - gmst + 0.5  # 构造：和恰为 2π + 0.5
    assert gmst + lon_rad > 2.0 * math.pi  # 前置：确实越过整圈
    lst = jd_to_lst(_JD, math.degrees(lon_rad))
    assert 0.0 <= lst < 2.0 * math.pi
    assert _circ_diff(lst, 0.5) < _TOL_RAD  # (2π + 0.5) mod 2π = 0.5
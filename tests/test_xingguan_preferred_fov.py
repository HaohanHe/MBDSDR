# SPDX-License-Identifier: MIT
"""
tests/test_xingguan_preferred_fov.py — 星官推荐视场字段（FR-01）

覆盖:
  1. 北斗 ``angular_extent_deg`` ≈ 31.2°（实测口径），
     ``preferred_fov_deg`` 落在 25–35° 的 spec 硬区间；
  2. 参宿/心宿/天狼/昴宿/织女的 preferred 严格等于
     ``clamp(round(extent * PREFERRED_FOV_FACTOR, 1), MIN, MAX)``
     （复用实现常量，不另造系数）；
  3. 小星官（extent < 5°）的 preferred 不高于 5.0 但不为 None；
  4. 合成星官：坐标星 < 2 颗时 extent/preferred 为 None；
  5. 合成 2 颗相距约 10° 的星：extent ≈ 10、preferred ≈ 10.5；
  6. 编辑表内的 priority/emphasized 与表外的 None/False；
  7. 未映射星（ra/dec=None）不参与 extent 计算。

运行:
    python -m pytest tests/test_xingguan_preferred_fov.py -v
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from typing import Optional  # noqa: E402

from mbdsdr_ai.skyengine import xingguan as _xingguan  # noqa: E402

# 上游实测：北斗全部已映射成员星之间的最大角距。
_NORTH_DIPPER_EXTENT_DEG = 31.2
_TABLE_NAMES = ("北斗", "参宿", "心宿", "昴宿", "天狼", "织女")


def _star(ra: Optional[float], dec: Optional[float], hip: int = 1):
    return _xingguan.XingguanStar(
        hip=hip, hyg_id=None, proper="", bayer="",
        ra_deg=ra, dec_deg=dec, vmag=None, con="",
        in_catalog=True, mapped=ra is not None, zh_name="")


def _xg(name_zh: str, stars):
    """用构造器直接造合成星官（不改 JSON，也不走加载器）。"""
    return _xingguan.Xingguan(
        name_zh=name_zh,
        name_zh_source=name_zh,
        name_en="", pinyin="",
        stars=tuple(stars),
        lines=(),
        segment_count=0,
        unresolved_tokens=(),
        primary_star_count_source="",
        added_star_count_source="",
    )


def _all_xingguan():
    return [g for r in _xingguan.load_xingguan() for g in r.xingguan]


def _by_name(name_zh: str):
    hits = [g for g in _all_xingguan() if g.name_zh == name_zh]
    assert hits, f"数据里找不到星官 {name_zh!r}"
    return hits[0]


def _expected_fov(extent: float) -> float:
    fov = round(extent * _xingguan.PREFERRED_FOV_FACTOR, 1)
    return max(_xingguan.PREFERRED_FOV_MIN,
               min(_xingguan.PREFERRED_FOV_MAX, fov))


# ---------------------------------------------------------------------------
# 1. 北斗：spec 硬区间 25–35°
# ---------------------------------------------------------------------------
def test_beidou_extent_matches_measurement():
    beidou = _by_name("北斗")
    extent = beidou.angular_extent_deg
    assert extent is not None
    assert abs(extent - _NORTH_DIPPER_EXTENT_DEG) <= 1.0, \
        f"北斗 extent 与实测相差过大: {extent:.3f}"


def test_beidou_preferred_fov_in_spec_range():
    fov = _by_name("北斗").preferred_fov_deg
    assert fov is not None
    assert 25.0 <= fov <= 35.0, f"北斗 preferred_fov_deg 越界: {fov}"


# ---------------------------------------------------------------------------
# 2. 其余推荐星官：严格按实现公式推导
# ---------------------------------------------------------------------------
def test_recommended_xingguan_fov_follows_formula():
    for name in ("参宿", "心宿", "天狼", "昴宿", "织女"):
        g = _by_name(name)
        extent = g.angular_extent_deg
        assert extent is not None, f"{name} 应有可用的成员坐标"
        assert g.preferred_fov_deg == _expected_fov(extent), \
            f"{name}: {g.preferred_fov_deg} != {_expected_fov(extent)}"


# ---------------------------------------------------------------------------
# 3. 小星官的下限夹取
# ---------------------------------------------------------------------------
def test_small_xingguan_clamped_to_min_but_not_none():
    # 取 extent < 4.5° 的星官：即使乘上 1.05 的取景余量也仍在 5° 下限内。
    small = [g for g in _all_xingguan()
             if (g.angular_extent_deg is not None
                 and g.angular_extent_deg < 4.5)]
    assert small, "数据里应存在 extent < 4.5° 的小星官"
    for g in small:
        assert g.preferred_fov_deg is not None, f"{g.name_zh} 不应为 None"
        assert g.preferred_fov_deg <= 5.0, \
            f"{g.name_zh}: {g.preferred_fov_deg} 未夹到下限"
        assert g.preferred_fov_deg == _xingguan.PREFERRED_FOV_MIN


# ---------------------------------------------------------------------------
# 4. 合成星官：有效坐标不足 2 颗
# ---------------------------------------------------------------------------
def test_single_or_no_coordinate_star_yields_none():
    one = _xg("合成单星", [_star(0.0, 0.0)])
    assert one.angular_extent_deg is None
    assert one.preferred_fov_deg is None

    zero = _xg("合成无星", [])
    assert zero.angular_extent_deg is None
    assert zero.preferred_fov_deg is None

    unmapped_only = _xg("合成未映射", [_star(None, None), _star(None, None)])
    assert unmapped_only.angular_extent_deg is None
    assert unmapped_only.preferred_fov_deg is None


# ---------------------------------------------------------------------------
# 5. 合成两颗相距约 10° 的星
# ---------------------------------------------------------------------------
def test_synthetic_two_stars_ten_degrees_apart():
    g = _xg("合成双星", [_star(0.0, 0.0, 1), _star(10.0, 0.0, 2)])
    assert abs(g.angular_extent_deg - 10.0) < 1e-6
    assert g.preferred_fov_deg == 10.5


def test_unmapped_star_does_not_join_extent():
    g = _xg("合成含未映射", [
        _star(0.0, 0.0, 1),
        _star(10.0, 0.0, 2),
        _star(None, None, 3),
    ])
    assert abs(g.angular_extent_deg - 10.0) < 1e-6, \
        "未映射星不得被当作 (0, 0) 参与角距"
    assert g.preferred_fov_deg == 10.5


# ---------------------------------------------------------------------------
# 6. 编辑表内/表外的 priority/emphasized
# ---------------------------------------------------------------------------
def test_editorial_priority_table_matches_data():
    assert (_by_name("北斗").priority, _by_name("北斗").emphasized) == (1, True)
    assert (_by_name("天狼").priority, _by_name("天狼").emphasized) == (2, True)
    for name in _TABLE_NAMES:
        g = _by_name(name)
        assert g.priority in (1, 2), f"{name} 应在编辑表内"
        assert g.emphasized is True


def test_unlisted_xingguan_has_no_invented_priority():
    unlisted = next(g for g in _all_xingguan()
                    if g.name_zh not in _TABLE_NAMES)
    assert unlisted.priority is None
    assert unlisted.emphasized is False

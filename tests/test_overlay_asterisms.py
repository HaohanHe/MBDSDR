# SPDX-License-Identifier: MIT
"""
tests/test_overlay_asterisms.py — 星官叠加数据层（T08）

覆盖：
  - 屏幕线段裁剪四种状态（both_in / a_in / b_in / crossing / 不穿越）
  - 拒画：站址缺、时刻非法、光轴误差超限 —— 一条线都不给
  - 真实星官数据接入（北斗 / 三台）
  - 窄视场下"线画到边界为止"（F6 核心）：北斗在 2.3° 目镜里必须产生 crossing 段
  - 宽视场下同一星官完整可见（both_in）
  - 名字锚点只在有可见段时存在
  - 缺 HIP 的星官如实上报 data_complete=False
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from mbdsdr_ai.celestial_geometry import GroundStation
from mbdsdr_ai.overlay_asterisms import (
    CLIP_A_IN,
    CLIP_B_IN,
    CLIP_BOTH_IN,
    CLIP_CROSSING,
    KIND_REGION,
    KIND_XINGGUAN,
    OverlayAsterism,
    OverlayResult,
    OverlaySegment,
    OverlayViewport,
    build_overlay,
    clip_segment_to_rect,
)
from mbdsdr_ai.skyengine.xingguan import get_xingguan

GS = GroundStation(120.0, 45.0, 200.0, "bench")
JD = 2460000.5
W, H = 800, 600


# ---------------------------------------------------------------------------
# 测试用投影：以视口中心为切点的 gnomon 投影（独立实现，不复用产品代码，
# 保证测试真的在测裁剪而不是测同一个 bug 的两份拷贝）
# ---------------------------------------------------------------------------
def _unit(az, alt):
    a = np.radians(np.asarray(az, dtype=float))
    e = np.radians(np.asarray(alt, dtype=float))
    return np.column_stack([np.cos(e) * np.cos(a),
                            np.cos(e) * np.sin(a),
                            -np.sin(e)])


def make_project(vp: OverlayViewport):
    az0 = math.radians(vp.center_az_deg)
    alt0 = math.radians(vp.center_alt_deg)
    c = np.array([math.cos(alt0) * math.cos(az0),
                  math.cos(alt0) * math.sin(az0),
                  -math.sin(alt0)])
    e_east = np.array([-math.sin(az0), math.cos(az0), 0.0])
    e_up = np.array([-math.sin(alt0) * math.cos(az0),
                     -math.sin(alt0) * math.sin(az0),
                     -math.cos(alt0)])
    scale = (vp.width_px / 2.0) / math.tan(math.radians(vp.fov_deg / 2.0))

    def project(az, alt):
        v = _unit(az, alt)
        cos_c = v @ c
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = (v @ e_east) / cos_c
            eta = (v @ e_up) / cos_c
            x = vp.width_px / 2.0 + xi * scale
            y = vp.height_px / 2.0 - eta * scale
        bad = (~np.isfinite(x)) | (~np.isfinite(y)) | (cos_c <= 1e-9)
        x = np.where(bad, np.nan, x)
        y = np.where(bad, np.nan, y)
        return x, y

    return project


def _vp(center_az, center_alt, fov=2.3):
    return OverlayViewport(width_px=W, height_px=H, fov_deg=fov,
                           center_az_deg=center_az, center_alt_deg=center_alt,
                           projection="perspective")


@pytest.fixture(scope="module")
def xg():
    return get_xingguan()


def _beidou_center(xg) -> tuple[float, float]:
    """取北斗成员星在该时刻的地平坐标均值作视场中心（任何 jd 都成立）。"""
    from mbdsdr_ai.celestial_geometry import (
        azalt_from_vec_many, j2000_to_altaz_many, vec_from_radec)

    for r in xg.regions:
        for g in r.xingguan:
            if g.name_zh != "北斗":
                continue
            vecs = [vec_from_radec(float(s.ra_deg), float(s.dec_deg))
                    for s in g.stars if s.ra_deg is not None]
            mat = j2000_to_altaz_many(np.vstack(vecs), GS, JD)
            az, alt = azalt_from_vec_many(mat)
            ok = np.isfinite(az) & np.isfinite(alt)
            return float(np.mean(az[ok])), float(np.mean(alt[ok]))
    pytest.fail("星官数据里找不到北斗")


def _beidou_first_star(xg) -> tuple[float, float]:
    """北斗连线的第一颗星的地平坐标——模拟「用户把望远镜指向一颗星」。"""
    from mbdsdr_ai.celestial_geometry import (
        azalt_from_vec_many, j2000_to_altaz_many, vec_from_radec)

    for r in xg.regions:
        for g in r.xingguan:
            if g.name_zh != "北斗":
                continue
            s = g.lines[0][0]
            mat = j2000_to_altaz_many(
                vec_from_radec(float(s.ra_deg), float(s.dec_deg))[None, :], GS, JD)
            az, alt = azalt_from_vec_many(mat)
            return float(az[0]), float(alt[0])
    pytest.fail("星官数据里找不到北斗")


# ---------------------------------------------------------------------------
# 裁剪算法
# ---------------------------------------------------------------------------
def test_clip_both_in():
    c = clip_segment_to_rect((100, 100), (200, 200), W, H)
    assert c is not None and c.state == CLIP_BOTH_IN
    assert c.frac == pytest.approx(1.0)
    assert c.p0 == (100, 100) and c.p1 == (200, 200)


def test_clip_a_in_replaces_far_end_with_border_point():
    c = clip_segment_to_rect((400, 300), (400, 900), W, H)
    assert c is not None and c.state == CLIP_A_IN
    assert c.p0 == (400, 300)
    assert c.p1[1] == pytest.approx(H)      # 端点被换到画面下边界
    assert 0 < c.frac < 1


def test_clip_b_in_replaces_near_end():
    c = clip_segment_to_rect((-100, 300), (400, 300), W, H)
    assert c is not None and c.state == CLIP_B_IN
    assert c.p0[0] == pytest.approx(0.0)
    assert c.p1 == (400, 300)


def test_clip_crossing_both_ends_outside():
    c = clip_segment_to_rect((-50, 300), (900, 300), W, H)
    assert c is not None and c.state == CLIP_CROSSING
    assert c.p0[0] == pytest.approx(0.0)
    assert c.p1[0] == pytest.approx(float(W))


def test_clip_both_out_no_crossing_returns_none():
    assert clip_segment_to_rect((-200, -200), (-100, -300), W, H) is None
    assert clip_segment_to_rect((900, 100), (900, 500), W, H) is None


def test_clip_non_finite_returns_none():
    assert clip_segment_to_rect((float("nan"), 1), (2, 3), W, H) is None


# ---------------------------------------------------------------------------
# 拒画：一条线都不给
# ---------------------------------------------------------------------------
def test_refuse_missing_site(xg):
    vp = _vp(0.0, 45.0)
    r = build_overlay(site=None, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    assert r.drawable is False
    assert r.asterisms == []
    assert "站址" in r.refuse_reason


def test_refuse_bad_time(xg):
    vp = _vp(0.0, 45.0)
    r = build_overlay(site=GS, jd_ut1=float("nan"), viewport=vp,
                      project=make_project(vp), sky_source=xg)
    assert r.drawable is False and r.asterisms == []
    assert "时刻" in r.refuse_reason


def test_refuse_axis_error_over_limit(xg):
    az, alt = _beidou_center(xg)
    vp = _vp(az, alt, fov=40.0)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg,
                      axis_error_deg=3.0, min_confident_axis_deg=1.0)
    assert r.drawable is False and r.asterisms == []
    assert "超过" in r.refuse_reason


def test_axis_error_within_limit_still_draws(xg):
    az, alt = _beidou_center(xg)
    vp = _vp(az, alt, fov=40.0)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg,
                      axis_error_deg=0.2, min_confident_axis_deg=1.0)
    assert r.drawable is True
    assert r.visible_count >= 1


def test_overlay_result_rejects_asterisms_when_not_drawable():
    with pytest.raises(ValueError):
        OverlayResult(drawable=False, refuse_reason="x",
                      asterisms=[OverlayAsterism(
                          key="k", name="n", pronounce="", kind=KIND_XINGGUAN,
                          members_total=0, members_in_view=0, data_complete=True,
                          label_anchor=None, label_az_deg=None,
                          label_alt_deg=None, segments=())])


# ---------------------------------------------------------------------------
# 真实数据：宽视场完整、窄视场到边界
# ---------------------------------------------------------------------------
def test_beidou_wide_fov_full_shape(xg):
    az, alt = _beidou_center(xg)
    vp = _vp(az, alt, fov=40.0)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    assert r.drawable
    bd = [a for a in r.asterisms if a.name == "北斗"]
    assert bd, "北斗应出现在叠加结果里"
    bd = bd[0]
    assert bd.kind == KIND_XINGGUAN
    assert len(bd.segments) >= 6, "北斗七星的 7 个点应形成至少 6 段"
    assert all(s.clip == CLIP_BOTH_IN for s in bd.segments)
    assert bd.label_anchor is not None
    assert bd.members_in_view == bd.members_total


def test_beidou_narrow_fov_lines_reach_border(xg):
    """F6 核心：窄视场下线不能被整条丢掉，必须留 crossing / *_in 段。

    场景取自真实用法——把望远镜指向北斗里的一颗星，视场只有 2.3°，
    这颗星的同官连线必须往画面外延伸并终止在边界上。
    """
    az, alt = _beidou_first_star(xg)
    vp = _vp(az, alt, fov=2.3)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    assert r.drawable, "指向星官成员星时不该一条线都没有"
    bd = [a for a in r.asterisms if a.name == "北斗"][0]
    assert bd.segments, "北斗的线应延伸到画面里"
    states = {s.clip for s in bd.segments}
    assert states & {CLIP_CROSSING, CLIP_A_IN, CLIP_B_IN}, \
        f"应有被裁到边界的段，实际只有 {states}"
    assert r.clipped_segments >= 1
    for s in bd.segments:
        assert len(s.screen) == 2
        for x, y in s.screen:
            assert -1e-6 <= x <= W + 1e-6
            assert -1e-6 <= y <= H + 1e-6
    # 22 颗成员星里，只有少数落在 2.3° 视场内
    assert bd.members_in_view < bd.members_total


def test_empty_patch_gives_no_lines_honestly(xg):
    """指向一片空白天空：一条线都不给，且说清原因（不硬凑）。"""
    az, alt = _beidou_center(xg)
    vp = _vp((az + 180.0) % 360.0, -70.0, fov=0.5)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    assert r.drawable is False
    assert r.asterisms == []
    assert r.refuse_reason


def test_label_anchor_none_when_no_visible_segment(xg):
    """指到没星的天空：不该凭空给名字锚点。"""
    az, alt = _beidou_center(xg)
    vp = _vp((az + 180.0) % 360.0, -80.0, fov=2.3)   # 地平以下
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    for a in r.asterisms:
        if a.label_anchor is not None:
            pytest.fail("没有可见段时不允许有名字锚点")


def test_incomplete_asterisms_flagged(xg):
    """三台 / 西咸 / 羽林军 各缺 1 个 HIP，必须如实标数据不全。"""
    az, alt = _beidou_center(xg)
    vp = _vp(az, alt, fov=170.0)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg,
                      min_visible_frac=0.0)
    by_name = {a.name: a for a in r.asterisms}
    for name in ("三台", "西咸", "羽林军"):
        if name in by_name:
            assert by_name[name].data_complete is False, f"{name} 应标数据不全"
    assert by_name["北斗"].data_complete is True


def test_regions_included_and_tagged(xg):
    az, alt = _beidou_center(xg)
    vp = _vp(az, alt, fov=170.0)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    kinds = {a.kind for a in r.asterisms}
    assert KIND_REGION in kinds, "垣/宿的墙线应一并返回"


def test_result_serialises(xg):
    az, alt = _beidou_center(xg)
    vp = _vp(az, alt, fov=40.0)
    r = build_overlay(site=GS, jd_ut1=JD, viewport=vp,
                      project=make_project(vp), sky_source=xg)
    d = r.to_dict()
    assert d["drawable"] is True
    assert isinstance(d["asterisms"], list) and d["asterisms"]
    seg = d["asterisms"][0]["segments"][0]
    assert set(seg) == {"a", "b", "clip", "screen", "visible_frac"}
    assert set(seg["screen"][0]) == {"x_px", "y_px"}

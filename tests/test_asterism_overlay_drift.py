# SPDX-License-Identifier: MIT
"""
tests/test_asterism_overlay_drift.py
=====================================
锁住「桌面实时星官绘制」与「T08 数据层 ``build_overlay``」不漂移：

  同一视口 / 站址 / 时刻下，两套实现必须给出**相同的可画屏幕线段集合**。

为什么这条测试存在
------------------
桌面热路径的唯一渲染实现是 ``desktop/rf_sky_view.py::_draw_constellations``
（内部 ``_constellation_prepared`` + ``_layer_points`` + ``_constel_path``）；
``mbdsdr_ai/overlay_asterisms.py::build_overlay``（T08）是共享数据契约 /
移动端对拍基准。决策是**不把 T08 接进热路径**（T08 全量约 170 ms/帧 vs 实时
层约 0.05 ms），于是这里不存在「同一个函数被两方调用」的结构性保证——两份
几何实现各自演化。这条测试就是那个保证：任何一方改了口径（投影、裁剪、
去重、高度口径），这里立刻变红。

已知的两侧差异（本测试显式规避，不是要消除它们）
--------------------------------------------------
1. 实时层投影前把几何高度换成视高度（``_display_alt_deg``，含大气折射）；
   T08 用几何高度。→ 测试关掉折射（``set_refraction_params(enabled=False)``）。
2. 实时层丢弃视高度 < -6° 的点；T08 不丢。→ 只比较**两端几何高度都 > 15°**
   的段，避开地平附近的可见性口径差。
3. 实时层对星官内部连线按 ``(min(hip), max(hip))`` 全局去重；T08 不去重。
   → 比较时把段归一化成「两端点排序 + 坐标 round 到 6 位」的集合，重复段
   自然折叠。
4. 实时侧 ``prepared`` 不保留「detail 段属于哪个星官」，所以只能比归一化段
   集合；T08 侧另外收集星官名，仅用于失败信息定位。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_asterism_overlay_drift.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from datetime import datetime, timezone  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.skyengine import xingguan as _xingguan  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    azalt_from_vec_many, j2000_to_altaz_many, vec_from_radec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai.overlay_asterisms import clip_segment_to_rect  # noqa: E402

# 中性示例站点（项目约定：仓库内不出现真实坐标）
LAT, LON, HEIGHT_M = 45.0, 120.0, 0.0

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()

# 比较口径：两端几何高度都必须高于此线（见模块文档第 2 条已知差异）
ALT_FLOOR_DEG = 15.0

FOVS = (60.0, 8.0, 2.3)


def _fix_time():
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()


def _new_view() -> RFSkyView:
    _fix_time()
    v = RFSkyView()
    v.resize(800, 800)
    v.set_observer(LAT, LON, HEIGHT_M)
    v.set_constellation_systems([_constel.SYSTEM_CHINESE])
    v.set_refraction_params(enabled=False)
    return v


def _beidou_direction(gs, jd):
    """「北斗」成员星的向量平均方向 → (az, alt)。

    用单位向量平均而不是 az/alt 算术平均：星官可能横跨 0/360° 方位接缝，
    算术平均会在那里炸掉。

    只平均**参与北斗连线**的成员星（七星），不把 15 颗增星算进去：
    22 颗全算时均值偏离斗勺连线 3.7°，2.3° 目镜视场会整个空白，
    那一段比较就成了空比较（实测：60°/8° 不受影响，2.3° 从 1 段变 0 段）。
    """
    data = _xingguan.get_xingguan()
    beidou = None
    for region in data.regions:
        for xg in region.xingguan:
            if str(getattr(xg, "name_zh", "")) == "北斗":
                beidou = xg
                break
        if beidou is not None:
            break
    assert beidou is not None, "星官数据里找不到「北斗」"

    linked_hips = {s.hip for poly in beidou.lines for s in poly}
    stars = [s for s in beidou.stars if s.hip in linked_hips] \
        if linked_hips else list(beidou.stars)
    stars = [s for s in stars
             if getattr(s, "ra_deg", None) is not None
             and getattr(s, "dec_deg", None) is not None]
    assert stars, "「北斗」成员星没有可用坐标"

    vecs = np.array([vec_from_radec(float(s.ra_deg), float(s.dec_deg))
                     for s in stars])
    mean = vecs.mean(axis=0)
    mean = mean / np.linalg.norm(mean)
    az, alt = azalt_from_vec_many(j2000_to_altaz_many(mean[None, :], gs, jd))
    return float(az[0]), float(alt[0])


def _norm_seg(p0, p1):
    """段归一化：两端点各自 round 到 6 位，再按 (x, y) 排序。"""
    a = (round(float(p0[0]), 6), round(float(p0[1]), 6))
    b = (round(float(p1[0]), 6), round(float(p1[1]), 6))
    return (a, b) if a <= b else (b, a)


def _live_segments(view, prepared, points, alt_by_key):
    """实时层：prepared 拓扑 + 本帧投影点 → 归一化可画段集合。"""
    w, h = float(view.width()), float(view.height())
    out = set()
    for ka, kb in tuple(prepared["region"]) + tuple(prepared["detail"]):
        pa = points.get(ka)
        pb = points.get(kb)
        if pa is None or pb is None:
            continue
        if alt_by_key.get(ka, -90.0) <= ALT_FLOOR_DEG:
            continue
        if alt_by_key.get(kb, -90.0) <= ALT_FLOOR_DEG:
            continue
        clip = clip_segment_to_rect((pa.x(), pa.y()), (pb.x(), pb.y()), w, h)
        if clip is None:
            continue
        out.add(_norm_seg(clip.p0, clip.p1))
    return out


def _t08_segments(res):
    """T08 层：OverlayResult → {归一化段: {星官名, ...}}。"""
    out = {}
    for ast in res.asterisms:
        for seg in ast.segments:
            if seg.a_alt_deg <= ALT_FLOOR_DEG or seg.b_alt_deg <= ALT_FLOOR_DEG:
                continue
            key = _norm_seg(seg.screen[0], seg.screen[-1])
            out.setdefault(key, set()).add(str(ast.name))
    return out


def _endpoint_delta(sa, sb):
    """两个归一化段的最大端点坐标差（两端点已按坐标排序）。"""
    d0 = max(abs(sa[0][0] - sb[0][0]), abs(sa[0][1] - sb[0][1]))
    d1 = max(abs(sa[1][0] - sb[1][0]), abs(sa[1][1] - sb[1][1]))
    return max(d0, d1)


def _fail_report(fov, live, t08):
    """两侧不等时的失败信息：能直接定位到段与星官。"""
    only_live = sorted(live - set(t08))
    only_t08 = sorted(set(t08) - live)
    lines = [
        f"实时层与 T08 在本视场（fov={fov:g}°）漂移：",
        f"  实时层 {len(live)} 段 / T08 {len(t08)} 段",
        f"  只在实时层出现 {len(only_live)} 段",
        f"  只在 T08 出现 {len(only_t08)} 段",
    ]
    for seg in only_t08:
        lines.append(f"    [T08] {seg}  星官={sorted(t08[seg])}")
    for seg in only_live:
        lines.append(f"    [实时] {seg}")

    pairs = []
    for seg_a in only_live:
        if not only_t08:
            break
        seg_b = min(only_t08, key=lambda s: _endpoint_delta(seg_a, s))
        pairs.append((_endpoint_delta(seg_a, seg_b),
                      seg_a, seg_b, sorted(t08[seg_b])))
    pairs.sort(key=lambda item: item[0], reverse=True)
    if pairs:
        lines.append("  端点坐标差最大的前 5 段（保留 3 位小数）：")
        for delta, seg_a, seg_b, names in pairs[:5]:
            lines.append(
                f"    Δ={delta:.3f}px  实时={seg_a}  T08={seg_b}  星官={names}")
    return "\n".join(lines)


@pytest.mark.parametrize("fov", FOVS)
def test_live_layer_matches_t08_overlay(fov):
    view = _new_view()
    gs = view._ground_station()
    jd = view._current_jd()
    assert gs is not None

    center_az, center_alt = _beidou_direction(gs, jd)
    assert center_alt > ALT_FLOOR_DEG, \
        f"「北斗」在该时刻高度 {center_alt:.2f}° 太低，测试无意义"
    view.view_state.look_at(center_az, center_alt)

    view.view_state.zoom_to(fov)
    # zoom_to 会被 min_fov/max_fov 夹取；被夹取就是测试口径变了，要如实知道。
    assert view.view_state.fov_deg == pytest.approx(fov, abs=1e-9), \
        (f"请求视场 {fov:g}° 被夹到 {view.view_state.fov_deg:g}°，"
         f"比较口径与预期不符")

    # ---- 实时侧 ----
    layer = _constel.overlay_layers((_constel.SYSTEM_CHINESE,))[0]
    prepared = view._constellation_prepared(layer)
    points = view._layer_points(prepared, gs, jd)
    altaz = j2000_to_altaz_many(prepared["vecs"], gs, jd)
    _az, alt = azalt_from_vec_many(altaz)
    alt_by_key = {k: float(a) for k, a in zip(prepared["keys"], alt)}
    live = _live_segments(view, prepared, points, alt_by_key)

    # ---- T08 侧 ----
    res = view.overlay_for_current_view(
        site=gs, jd_ut1=jd, culture="chinese", include_regions=True)
    assert res.drawable, f"T08 拒画: {res.refuse_reason}"
    t08 = _t08_segments(res)

    if live != set(t08):
        pytest.fail(_fail_report(fov, live, t08))

    # 非空洞断言：两边都非空，这条测试才真的比了东西。
    assert live, f"{fov:g}° 视场里实时层一段都没有，比较是空的"

    # 宽视场必须有足够样本，否则这条测试等于没比。
    if fov >= 60.0:
        assert len(live) >= 10, \
            f"{fov:g}° 视场只有 {len(live)} 段，比较样本太少（测试形同虚设）"

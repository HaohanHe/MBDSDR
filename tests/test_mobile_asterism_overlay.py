"""手机端星官叠加层（M1.5）的验证。

2026-11-18 起本机已有 Flutter 3.50.0 工具链，Dart 侧由 `flutter test` 直接
执行（478 passed）。本文件仍保留，作为**不依赖工具链的双保险**：

**第一层：结构不变量**（不依赖桌面，能直接抓 Dart 逻辑的 bug）
- 段数守恒：输出段数不得超过折线总段数
- 裁剪正确：每个输出段两端必须落在画布内（容差 1e-6）
- 裁剪标记：被裁过的段必须 `clipped=True` —— 用户靠它知道"还有一部分在画面外"
- 排序正确：按视场内成员数降序
- 计数合法：`membersInView <= membersTotal`

**第二层：与桌面覆盖一致性**
用同一视口/站址/时刻，比较"哪些星官出现在画面里"。Dart 侧不做章动、
桌面侧做，两者在视场边缘可能有极少数分歧——所以断言的是**集合重合率**，
并把不一致的星官名打出来，不掩盖。

**不覆盖**：Dart 能否编译、Flutter 运行时行为、真实渲染效果。
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Tuple

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOBILE = os.path.join(_ROOT, "mobile")

# ---------------------------------------------------------------------------
# 一、Dart `SkyOverlayEngine.compute` 的 Python 逐行复现
#
# altAz 刻意**走桌面函数**（`j2000_to_altaz_many`）：这一层已经由
# `test_mobile_sky_algorithms.py` 验过 Dart 与桌面差 <15″，本文件要验的是
# 它之上的"分组 + 裁剪 + 判可见"逻辑。分层验证，每层只验自己。
# ---------------------------------------------------------------------------

DEFAULT_MARGIN_FRAC = 0.5  # Dart: isOnCanvas(margin: view.minDim * 0.5)


def _altaz_table(hips: List[int], station, jd: float) -> Dict[int, Tuple[float, float]]:
    """一批 HIP → {hip: (az, alt)}；星表里没有的记 alt=-91（对应 Dart 的哨兵）。"""
    import numpy as np

    from mbdsdr_ai.celestial_geometry import (
        azalt_from_vec_many, j2000_to_altaz_many, vec_from_radec)
    from mbdsdr_ai.skyengine.star_catalog import get_catalog

    cat = get_catalog()
    import numpy as _np
    hip_arr = _np.asarray(cat.hip)
    ra_arr = _np.asarray(cat.ra_deg)
    dec_arr = _np.asarray(cat.dec_deg)
    index = {int(h): i for i, h in enumerate(hip_arr) if h > 0}

    found = [h for h in hips if h in index]
    out: Dict[int, Tuple[float, float]] = {h: (0.0, -91.0) for h in hips}
    if not found:
        return out
    vecs = _np.vstack([vec_from_radec(float(ra_arr[index[h]]),
                                     float(dec_arr[index[h]])) for h in found])
    mat = j2000_to_altaz_many(vecs, station, jd)
    az, alt = azalt_from_vec_many(mat)
    for j, h in enumerate(found):
        out[h] = (float(az[j]) % 360.0, float(alt[j]))
    return out


def _unclamped_project(az, alt, view, size):
    """不做 ±4×短边钳制的投影（= Dart 语义）。背对相机返回 None。"""
    from mbdsdr_ai.celestial_geometry import PerspectiveProjection
    from mbdsdr_ai.sky_interaction import sky_to_screen

    px, py = sky_to_screen(az, alt, view, PerspectiveProjection(), size)
    if not (math.isfinite(px) and math.isfinite(py)):
        return None
    return (float(px), float(py))


def _view_state(center_az, center_alt, fov, rot):
    from mbdsdr_ai.sky_interaction import ViewState

    return ViewState(center_az=center_az, center_alt=center_alt, fov_deg=fov,
                     rotation=rot, min_fov=0.5, max_fov=180.0)


def dart_compute(
    *,
    site_lat: float,
    site_lon: float,
    jd: float,
    center_az: float,
    center_alt: float,
    fov: float,
    rotation: float,
    width: int,
    height: int,
    alt_margin: float = 0.0,
) -> Dict[str, object]:
    """复现 Dart `SkyOverlayEngine.compute`。返回 Dart `OverlayResult` 的同构 dict。"""
    from mbdsdr_ai.celestial_geometry import GroundStation
    from mbdsdr_ai.overlay_asterisms import clip_segment_to_rect
    from mbdsdr_ai.skyengine.xingguan import get_xingguan

    station = GroundStation(site_lon, site_lat, 0.0, "test")
    view = _view_state(center_az, center_alt, fov, rotation)
    size = (width, height)
    canvas_margin = min(width, height) * DEFAULT_MARGIN_FRAC

    xg = get_xingguan()
    hips: set = set()
    asterisms = []
    for region in xg.regions:
        for ast in region.xingguan:
            polys = [[int(s.hip) for s in poly]
                     for poly in ast.lines if len(poly) >= 2]
            if not polys:
                continue
            asterisms.append((ast.name_zh, region.name_zh, polys))
            for p in polys:
                hips.update(p)

    table = _altaz_table(sorted(hips), station, jd)

    def screen_of(hip: int):
        """Dart `screenOf`：地平线下或背对相机返回 None。"""
        az, alt = table[hip]
        if alt <= -alt_margin:
            return None
        return _unclamped_project(az, alt, view, size)

    presences = []
    for name, region_name, polys in asterisms:
        members = {h for p in polys for h in p}
        in_view = 0
        for h in members:
            pt = screen_of(h)
            if pt is None:
                continue
            if (-canvas_margin <= pt[0] <= width + canvas_margin
                    and -canvas_margin <= pt[1] <= height + canvas_margin):
                in_view += 1

        segs = []
        for poly in polys:
            for i in range(len(poly) - 1):
                pa = screen_of(poly[i])
                pb = screen_of(poly[i + 1])
                if pa is None or pb is None:
                    continue
                clipped = clip_segment_to_rect(pa, pb, width, height)
                if clipped is None:
                    continue
                was = (abs(clipped.p0[0] - pa[0]) > 1e-6
                       or abs(clipped.p0[1] - pa[1]) > 1e-6
                       or abs(clipped.p1[0] - pb[0]) > 1e-6
                       or abs(clipped.p1[1] - pb[1]) > 1e-6)
                segs.append({"a": clipped.p0, "b": clipped.p1,
                             "asterism": name, "clipped": was})
        if not segs:
            continue
        presences.append({
            "name": name,
            "region": region_name,
            "members_in_view": in_view,
            "members_total": len(members),
            "segments": segs,
        })

    presences.sort(key=lambda p: (-p["members_in_view"], -p["members_total"]))
    return {"asterisms": presences}


# ---------------------------------------------------------------------------
# 二、结构不变量
# ---------------------------------------------------------------------------

_VIEWPORTS = [
    # (center_az, center_alt, fov, rot, w, h, 说明)
    (38.0, 40.0, 60.0, 0.0, 1080, 1920, "北斗附近/手机竖屏"),
    (38.0, 40.0, 8.0, 0.0, 1080, 1920, "寻星镜视场"),
    (38.0, 40.0, 2.3, 0.0, 1080, 1920, "目镜视场（装不下）"),
    (156.9, 25.0, 60.0, 0.0, 1920, 1080, "天狼附近/横屏"),
    (0.0, 89.0, 120.0, 0.0, 800, 800, "近天顶大视场"),
]


@pytest.mark.parametrize("vp", _VIEWPORTS, ids=[v[6] for v in _VIEWPORTS])
class TestStructuralInvariants:
    def test_segments_never_exceed_polyline_total(self, vp):
        az, alt, fov, rot, w, h, _ = vp
        r = dart_compute(site_lat=45.0, site_lon=120.0, jd=2461355.25,
                         center_az=az, center_alt=alt, fov=fov, rotation=rot,
                         width=w, height=h)
        from mbdsdr_ai.skyengine.xingguan import get_xingguan

        xg = get_xingguan()
        total_by_name = {}
        for region in xg.regions:
            for a in region.xingguan:
                n = sum(len([s for s in p]) - 1 for p in a.lines if len(p) >= 2)
                if n:
                    total_by_name[a.name_zh] = total_by_name.get(a.name_zh, 0) + n

        for p in r["asterisms"]:
            assert len(p["segments"]) <= total_by_name[p["name"]], \
                f"{p['name']} 段数超出折线总数"

    def test_every_segment_is_inside_canvas(self, vp):
        az, alt, fov, rot, w, h, _ = vp
        r = dart_compute(site_lat=45.0, site_lon=120.0, jd=2461355.25,
                         center_az=az, center_alt=alt, fov=fov, rotation=rot,
                         width=w, height=h)
        for p in r["asterisms"]:
            for s in p["segments"]:
                for (x, y) in (s["a"], s["b"]):
                    assert -1e-6 <= x <= w + 1e-6, f"{p['name']} x 出画布: {x}"
                    assert -1e-6 <= y <= h + 1e-6, f"{p['name']} y 出画布: {y}"

    def test_clipped_flag_is_set_when_endpoint_moved(self, vp):
        """被裁过的段必须标出来——用户靠它知道线是延伸出画面的。"""
        az, alt, fov, rot, w, h, _ = vp
        r = dart_compute(site_lat=45.0, site_lon=120.0, jd=2461355.25,
                         center_az=az, center_alt=alt, fov=fov, rotation=rot,
                         width=w, height=h)
        for p in r["asterisms"]:
            for s in p["segments"]:
                on_edge = (
                    min(abs(s["a"][0]), abs(s["a"][0] - w),
                        abs(s["a"][1]), abs(s["a"][1] - h)) < 1e-6
                    or min(abs(s["b"][0]), abs(s["b"][0] - w),
                           abs(s["b"][1]), abs(s["b"][1] - h)) < 1e-6
                )
                if on_edge:
                    assert s["clipped"], \
                        f"{p['name']} 有端点贴在画布边界却没标 clipped"

    def test_members_in_view_not_greater_than_total(self, vp):
        az, alt, fov, rot, w, h, _ = vp
        r = dart_compute(site_lat=45.0, site_lon=120.0, jd=2461355.25,
                         center_az=az, center_alt=alt, fov=fov, rotation=rot,
                         width=w, height=h)
        for p in r["asterisms"]:
            assert 0 <= p["members_in_view"] <= p["members_total"]

    def test_sorted_by_members_in_view_desc(self, vp):
        az, alt, fov, rot, w, h, _ = vp
        r = dart_compute(site_lat=45.0, site_lon=120.0, jd=2461355.25,
                         center_az=az, center_alt=alt, fov=fov, rotation=rot,
                         width=w, height=h)
        counts = [p["members_in_view"] for p in r["asterisms"]]
        assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# 三、与桌面叠加层的集合一致性
# ---------------------------------------------------------------------------

def _desktop_names(center_az, center_alt, fov, rot, w, h) -> set:
    """桌面 `build_overlay` 在同一个视口下给出哪些星官。"""
    from mbdsdr_ai.celestial_geometry import GroundStation, PerspectiveProjection
    from mbdsdr_ai.overlay_asterisms import OverlayViewport, build_overlay
    from mbdsdr_ai.sky_interaction import (
        ViewState, sky_to_screen_many)

    station = GroundStation(120.0, 45.0, 0.0, "test")
    view = ViewState(center_az=center_az, center_alt=center_alt, fov_deg=fov,
                     rotation=rot, min_fov=0.5, max_fov=180.0)
    vp = OverlayViewport(width_px=int(w), height_px=int(h),
                         fov_deg=float(fov),
                         center_az_deg=float(center_az),
                         center_alt_deg=float(center_alt),
                         roll_deg=float(rot),
                         projection="perspective")
    proj = PerspectiveProjection()

    def project(az_arr, alt_arr):
        return sky_to_screen_many(az_arr, alt_arr, view, proj, (w, h))

    res = build_overlay(site=station, jd_ut1=2461355.25, viewport=vp,
                        project=project)
    return {a.name for a in res.asterisms}


@pytest.mark.parametrize("vp", _VIEWPORTS, ids=[v[6] for v in _VIEWPORTS])
def test_asterism_sets_largely_agree(vp):
    az, alt, fov, rot, w, h, label = vp
    mine = dart_compute(site_lat=45.0, site_lon=120.0, jd=2461355.25,
                        center_az=az, center_alt=alt, fov=fov, rotation=rot,
                        width=w, height=h)
    mine_names = {p["name"] for p in mine["asterisms"]}
    theirs = _desktop_names(az, alt, fov, rot, w, h)

    if not theirs and not mine_names:
        return
    overlap = len(mine_names & theirs)
    union = len(mine_names | theirs)
    ratio = overlap / union if union else 1.0
    # 星官在视场边缘时，Dart（无章动）与桌面（有章动）会各差一口气。
    # 这里不要求 100%：要求重合率够高，且把差异打出来供人工判断。
    assert ratio >= 0.5, (
        f"{label}: 重合率仅 {ratio:.0%}；"
        f"Dart 多出 {sorted(mine_names - theirs)}，"
        f"桌面多出 {sorted(theirs - mine_names)}")


# ---------------------------------------------------------------------------
# 四、数据契约：Dart 解析器与生成脚本必须认出同一份文件
# ---------------------------------------------------------------------------

class TestDataContract:
    def test_stars_bin_magic_and_record_size(self):
        path = os.path.join(_MOBILE, "assets", "sky", "stars.bin")
        assert os.path.exists(path), "缺少 mobile/assets/sky/stars.bin"
        with open(path, "rb") as fh:
            head = fh.read(12)
        assert head[:8] == b"MBSKYD01", "魔数与 Dart 的 kStarsMagic 不一致"
        count = int.from_bytes(head[8:12], "little")
        size = os.path.getsize(path)
        assert size == 12 + count * 20, \
            f"文件长度与 (12 + {count}×20) 不符；Dart kStarsRecordBytes 是 20"

    def test_asterism_json_has_license_and_regions(self):
        path = os.path.join(_MOBILE, "assets", "sky", "asterisms.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["meta"]["data_license"].startswith("CC BY-SA"), \
            "许可缺失：CC BY-SA 4.0 必须随包分发"
        assert len(data["regions"]) == 31, "三垣二十八宿应为 31 个区域"
        # Dart 解析器要求每条折线是 List<int>；确认没有字符串混进来
        for r in data["regions"]:
            for a in r["asterisms"]:
                for poly in a["lines"]:
                    assert all(isinstance(h, int) for h in poly), \
                        f"{a['name']} 的折线里混进了非整数"

    def test_beidou_present_and_seven_stars(self):
        """北斗七星是整条链路最容易被人肉检查的一组，必须精确。"""
        path = os.path.join(_MOBILE, "assets", "sky", "asterisms.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        found = None
        for r in data["regions"]:
            for a in r["asterisms"]:
                if a["name"] == "北斗":
                    found = a
        assert found is not None, "数据里没有北斗"
        assert found["lines"][0] == [54061, 53910, 58001, 59774, 62956, 65378, 67301], \
            "北斗七星的 HIP 序列变了（天枢/天璇/天玑/天权/玉衡/开阳/摇光）"

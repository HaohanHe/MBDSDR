# SPDX-License-Identifier: MIT
"""
tests/test_all_cultures_render.py — 全部文化包的机械渲染验收

只回答一个问题：注册表里列出的每一个天空文化体系，是否真的能渲染。

覆盖:
  1. 数据层：遍历 ``system_ids()``，``constellation_layer()`` 不抛异常；
     逐包统计组数 / 有 segments 的组数 / 有锚点的组数，并断言「有组就有
     锚点」「至少一个组有 segments」；文件包条目类型必须是
     ``CultureGroupAdapter``；
  2. 离屏渲染：逐个只开启一个体系（其余全关），``_draw_constellations``
     不抛异常，prepared 的 region 段数 > 0，锚点可见时标签队列里出现该
     组的标签；
  3. 同时开启全部体系：绘制一次不抛异常，prepared 缓存覆盖全部 system_id；
  4. 未知 id 仍抛 ``KeyError``。

空包不许静默跳过：确实拿不到任何星表匹配的包必须写进 ``EMPTY_BY_DATA``
并注明原因；本测试同时反查白名单里的包确实为空（白名单腐化也算红）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_all_cultures_render.py -v
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import RFSkyView  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from mbdsdr_ai.skyengine import xingguan as _xingguan  # noqa: E402
from mbdsdr_ai.skyengine.culture_layer import CultureGroupAdapter  # noqa: E402
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    azalt_from_vec, j2000_to_altaz, vec_from_radec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
LAT, LON = 40.0, 116.0

#: 数据层确认「全无星表匹配」的体系 → 原因说明。
#: 当前注册表所有包都能解析出至少一段连线，因此为空；以后真遇到空包时
#: 必须在这里写明原因，而不是让测试静默跳过。
EMPTY_BY_DATA = {}


def _new_view(size: int = 700) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    return v


def _segments_of(item):
    """IAU/星官用 ``segments()`` 方法，文件包适配器用 ``segments`` 元组。"""
    segs = item.segments
    return segs() if callable(segs) else segs


def _anchor_of(item, system_id):
    """中国星官的锚点唯一来源是 ``xingguan_anchor``，其余走 ``anchor()``。"""
    if system_id == _constel.SYSTEM_CHINESE:
        return _xingguan.xingguan_anchor(item)
    return item.anchor()


def _stats(layer):
    """(组数, 有 segments 的组数, 有锚点的组数)。"""
    total = 0
    with_segments = 0
    with_anchor = 0
    for item in layer.items:
        total += 1
        if _segments_of(item):
            with_segments += 1
        if _anchor_of(item, layer.system_id) is not None:
            with_anchor += 1
    return total, with_segments, with_anchor


def _draw_layer_queue(v: RFSkyView):
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        v._label_items = []
        v._draw_constellations(painter)
    finally:
        painter.end()
    return list(v._label_items)


def _texts(items):
    return [t for _p, _pos, lines, _c in items for t, _s, _d in lines]


def _comfortable_anchor(v: RFSkyView, layer):
    """在所有锚点里挑一个仰角最接近 35° 的，返回 (item, az, alt)。

    不写死「必须高于 15°」：像 maori 在当前历元锚点最高只有 5° 左右，
    写死阈值会把「数据本来就在低空」误判成「渲染失败」。这里只要求
    锚点在地平以上，并按 35° 优选一个投影舒服的方向。
    """
    gs = v._ground_station()
    jd = v._current_jd()
    candidates = []
    for item in layer.items:
        anchor = _anchor_of(item, layer.system_id)
        if anchor is None:
            continue
        az, alt = azalt_from_vec(j2000_to_altaz(
            vec_from_radec(anchor.ra_deg, anchor.dec_deg), gs, jd))
        if math.isfinite(az) and math.isfinite(alt) and alt > 0.0:
            candidates.append((item, az, alt))
    assert candidates, f"{layer.system_id} 当前历元没有任何地平以上的锚点"
    candidates.sort(key=lambda row: abs(row[2] - 35.0))
    return candidates[0]


def _expected_label(layer, item) -> str:
    """单体系开启时（multi=False）该组标签的精确文本。"""
    if layer.system_id == _constel.SYSTEM_IAU:
        return _constel.iau_constellation_label(item.abbr)
    if layer.system_id == _constel.SYSTEM_CHINESE:
        return _xingguan.xingguan_label(item.key)
    return item.display_name


# ---------------------------------------------------------------------------
# 1. 数据层：逐包枚举
# ---------------------------------------------------------------------------
def test_every_registered_system_has_renderable_items():
    ids = _constel.system_ids()
    assert len(ids) >= 15, f"注册文化包数量异常偏少: {ids}"
    assert len(set(ids)) == len(ids), f"system_ids() 存在重复: {ids}"

    report = {}
    for sid in ids:
        layer = _constel.constellation_layer(sid)      # 不抛异常
        assert layer.system_id == sid
        total, with_segments, with_anchor = _stats(layer)
        report[sid] = (total, with_segments, with_anchor)

        if total:
            assert with_anchor > 0, (
                f"{sid} 有 {total} 个组却没有任何标签锚点（无法标名）")
        if with_segments == 0:
            assert sid in EMPTY_BY_DATA, (
                f"{sid} 没有任何可画 segments：要么是数据/星表匹配坏了，"
                f"要么请把它写进 EMPTY_BY_DATA 并说明原因（不允许静默跳过）")

        if sid not in (_constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE):
            assert all(isinstance(item, CultureGroupAdapter)
                       for item in layer.items), (
                f"{sid} 不是 IAU/中国，条目类型却不是 CultureGroupAdapter")

    # 白名单反向校验：写进去的包必须真的为空，且必须是已注册体系。
    for sid, reason in EMPTY_BY_DATA.items():
        assert reason, f"EMPTY_BY_DATA[{sid!r}] 必须写明原因"
        assert sid in report, f"EMPTY_BY_DATA 里的 {sid!r} 不是已注册体系"
        assert report[sid][1] == 0, (
            f"EMPTY_BY_DATA[{sid!r}] 已过期：该包现在有可画 segments")


# ---------------------------------------------------------------------------
# 2. 离屏渲染：每个体系单独开启
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("system_id", _constel.system_ids())
def test_each_culture_alone_renders_and_labels(system_id):
    v = _new_view()
    layer = _constel.constellation_layer(system_id)
    _total, _with_segments, with_anchor = _stats(layer)

    item, az, alt = _comfortable_anchor(v, layer)
    v.set_constellation_systems([system_id])       # 其余全关
    v.view_state.look_at(az, alt)
    v.view_state.zoom_to(120.0)

    texts = _texts(_draw_layer_queue(v))            # 不抛异常

    prepared = v._constellation_prepared(layer)
    assert len(prepared["region"]) > 0, f"{system_id} 没有任何可画段"
    assert with_anchor > 0, f"{system_id} 没有任何标签锚点"
    assert _expected_label(layer, item) in texts, (
        f"{system_id} 的锚点可见却没有入标签队列: {texts[:8]}")


# ---------------------------------------------------------------------------
# 3. 全部体系同时开启
# ---------------------------------------------------------------------------
def test_all_cultures_together_draw_once():
    ids = _constel.system_ids()
    v = _new_view()
    v.set_constellation_systems(ids)
    v.view_state.look_at(180.0, 40.0)
    v.view_state.zoom_to(120.0)

    _draw_layer_queue(v)                            # 不抛异常

    cached = set(v._constel_prepared)
    assert set(ids) <= cached, f"prepared 缓存缺体系: {set(ids) - cached}"
    assert len(v._constel_prepared) == len(ids), (
        f"prepared 缓存条目 {len(v._constel_prepared)} != system_ids {len(ids)}")
    for sid in ids:
        assert len(v._constel_prepared[sid]["region"]) > 0, (
            f"全开绘制时 {sid} 的 region 为空")


# ---------------------------------------------------------------------------
# 4. 未知体系
# ---------------------------------------------------------------------------
def test_unknown_system_still_raises_key_error():
    with pytest.raises(KeyError):
        _constel.constellation_layer("does_not_exist")
    v = _new_view()
    with pytest.raises(KeyError):
        v.set_constellation_layer_visible("does_not_exist", True)
    with pytest.raises(KeyError):
        v.set_constellation_systems(["does_not_exist"])

# SPDX-License-Identifier: MIT
"""
tests/test_sky_culture_layers.py — 多文化天空接入渲染

覆盖:
  1. 数据层 ``system_ids()`` 至少含 IAU、中国星官与全部已收文件包；
  2. 文件文化包适配器能产出 segments 与 anchor；
  3. 离屏 ``RFSkyView``：默认只开 IAU（徽标仍为 "IAU"）；开启 japanese
     后 ``_draw_constellations`` 不抛异常且标签队列出现该文化标签；
     关闭后不再出现；
  4. 未知体系 id 仍抛 ``KeyError``。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_culture_layers.py -v
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
from mbdsdr_ai.celestial_geometry import (  # noqa: E402
    azalt_from_vec, j2000_to_altaz, vec_from_radec,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
LAT, LON = 40.0, 116.0
CULTURE_IDS = (
    "japanese", "indian", "norse", "indian_nakshatras", "seri",
    "maori", "mongolian", "sami", "hawaiian_starlines",
    "tukano", "boorong", "russian_siberian", "norse_edda",
    "anutan", "samoan", "tongan", "vanuatu_netwar", "tikuna",
    "ruanui_sky_tahiti_and_society_islands",
    "chinese_song_dynasty", "chinese_yuan_dynasty", "chinese_manchu",
    "chinese_xianglin", "chinese_chenzhuo", "modern_rey",
    "belarusian", "modern", "modern_chinese",
)


def _new_view(size: int = 700) -> RFSkyView:
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()
    v = RFSkyView()
    v.resize(size, size)
    v.set_observer(LAT, LON, 0.0)
    return v


def _draw_layer_queue(v: RFSkyView, fov: float = 120.0):
    img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        v._label_items = []
        v._draw_constellations(painter)
    finally:
        painter.end()
    return list(v._label_items)


def _visible_anchor(v: RFSkyView, layer):
    """找一个当前在地平以上的锚点，返回 (item, star, az, alt)。"""
    gs = v._ground_station()
    jd = v._current_jd()
    for item in layer.items:
        anchor = item.anchor()
        if anchor is None:
            continue
        az, alt = azalt_from_vec(j2000_to_altaz(
            vec_from_radec(anchor.ra_deg, anchor.dec_deg), gs, jd))
        if math.isfinite(alt) and alt > 15.0:
            return item, anchor, az, alt
    raise AssertionError("该文化在当前历元没有高于 15° 的锚点")


def _texts(items):
    return [t for _p, _pos, lines, _c in items for t, _s, _d in lines]


# ---------------------------------------------------------------------------
# 1. 体系枚举
# ---------------------------------------------------------------------------
def test_system_ids_include_all_collected_cultures():
    """数量只做下限断言；新增合规文化包不应让本测试失效。"""
    ids = _constel.system_ids()
    assert len(ids) >= len(CULTURE_IDS) + 2
    assert {
        _constel.SYSTEM_IAU, _constel.SYSTEM_CHINESE, *CULTURE_IDS
    } <= set(ids)


# ---------------------------------------------------------------------------
# 2. 文件文化包适配器
# ---------------------------------------------------------------------------
def test_japanese_layer_produces_segments_and_anchor():
    layer = _constel.constellation_layer("japanese")
    assert layer.system_id == "japanese"
    assert layer.items
    assert any(item.segments for item in layer.items)
    assert any(item.anchor() is not None for item in layer.items)
    for item in layer.items:
        for sa, sb in item.segments:
            assert all(math.isfinite(v) for v in
                       (sa.ra_deg, sa.dec_deg, sb.ra_deg, sb.dec_deg))
            assert sa.hip > 0 and sb.hip > 0


# ---------------------------------------------------------------------------
# 3. 离屏渲染
# ---------------------------------------------------------------------------
def test_default_iau_only_text_unchanged():
    v = _new_view()
    assert v.constellation_systems_text() == "IAU"


def test_japanese_layer_draws_and_labels_then_disappears():
    v = _new_view()
    layer = _constel.constellation_layer("japanese")
    item, _anchor, az, alt = _visible_anchor(v, layer)

    v.set_constellation_systems([_constel.SYSTEM_IAU, "japanese"])
    v.view_state.look_at(az, alt)
    v.view_state.zoom_to(120.0)

    items = _draw_layer_queue(v)
    texts = _texts(items)
    expected = f"[{layer.tag}] {item.display_name}"
    assert expected in texts, f"缺文化标签 {expected!r}: {texts[:8]}"

    v.set_constellation_layer_visible("japanese", False)
    texts_after = _texts(_draw_layer_queue(v))
    assert not any(t.startswith(f"[{layer.tag}]") for t in texts_after)
    assert v.constellation_systems_text() == "IAU"


@pytest.mark.parametrize("system_id", CULTURE_IDS)
def test_every_culture_pack_renders_without_error(system_id):
    """每个文件包都能完成 prepared + 绘制（不要求锚点都在地平以上）。"""
    v = _new_view()
    v.set_constellation_systems([system_id])
    v.view_state.look_at(180.0, 40.0)
    v.view_state.zoom_to(120.0)
    _draw_layer_queue(v)
    prepared = v._constellation_prepared(
        _constel.constellation_layer(system_id))
    assert prepared["region"], f"{system_id} 没有任何可画段"
    assert prepared["labels"], f"{system_id} 没有任何标签锚点"


# ---------------------------------------------------------------------------
# 4. 未知体系
# ---------------------------------------------------------------------------
def test_unknown_system_still_raises_key_error():
    with pytest.raises(KeyError):
        _constel.constellation_layer("does_not_exist")
    with pytest.raises(KeyError):
        _constel.system_info("does_not_exist")

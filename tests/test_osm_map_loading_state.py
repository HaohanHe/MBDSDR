# SPDX-License-Identifier: MIT
"""地图加载态必须可终止（tests/test_osm_map_loading_state.py）
=============================================================

旧实现的 HUD 判据是 ``loader.inflight() > 0``：QNetworkReply 默认**没有**超时
（0 = 永不超时），离线或代理拦截时套接字能挂几分钟到十几分钟，于是

- 蓝条「正在加载地图…」从启动 3 s 一直挂到十几分钟，瓦片数恒为 0；
- 红色「瓦片未加载」要求 ``fail_count >= 3``，而失败根本没落账，永不触发；
- 零瓦片时右下仍照挂 © OpenStreetMap 署名，等于伪造来源。

改后：请求带传输超时；HUD 只在「最近 TILE_LOADING_GRACE_S 内确有瓦片活动」
时算加载中，否则给「瓦片加载失败（原因）」；署名只在真画出瓦片时显示。

反证：把 HUD 判据改回 ``inflight() > 0`` → 停滞用例必红；去掉署名门槛 →
署名用例必红。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _loader(tmp_path):
    from osm_map import TileCache, TileLoader
    return TileLoader(TileCache(root=str(tmp_path)), online=True)


def test_stuck_inflight_falls_back_to_failure(qtapp, tmp_path):
    """请求挂着不动（离线）时，加载态必须在宽限期后落成 failed。"""
    from osm_map import TILE_LOADING_GRACE_S

    loader = _loader(tmp_path)
    loader._inflight.add((3, 1, 1))
    loader.last_activity_t = time.monotonic()
    assert loader.status_line() == ("loading", "")

    # 没有任何成功/失败落账，也没有新活动：宽限期一过必须给明确结论
    loader.last_activity_t = time.monotonic() - TILE_LOADING_GRACE_S - 1.0
    state, why = loader.status_line()
    assert state == "failed", f"离线时仍在报加载中：{state!r}"
    assert "超时" in why, f"失败原因必须可读：{why!r}"


def test_failure_reason_comes_from_real_error(qtapp, tmp_path):
    """有真实失败落账时，HUD 用那条真实原因，而不是笼统的「未加载」。"""
    loader = _loader(tmp_path)
    loader.fail_count = 2
    loader.last_error = "Connection refused·HTTP 403"

    state, why = loader.status_line()

    assert state == "failed"
    assert why == "Connection refused·HTTP 403"


def test_hud_text_terminates(qtapp, tmp_path):
    """组件级：同一场景先蓝条后红条，文案都取自同一判据。"""
    from osm_map import OsmMapView, TILE_LOADING_GRACE_S

    view = OsmMapView(online=True)
    view.resize(500, 360)
    view._loader._inflight.add((3, 1, 1))
    view._loader.fail_count = 1
    view._loader.last_error = "传输超时"
    view._loader.last_activity_t = time.monotonic()
    assert view._loading_hud()[0] == "loading"

    view._loader.last_activity_t = time.monotonic() - TILE_LOADING_GRACE_S - 1.0
    state, msg = view._loading_hud()
    assert state == "failed"
    assert "瓦片加载失败" in msg and "传输超时" in msg


def test_attribution_requires_real_tiles(qtapp):
    """零瓦片不得挂 @ OpenStreetMap 署名（署名前提是确有瓦片）。"""
    from osm_map import OsmMapView

    view = OsmMapView(online=True, show_attribution=True)
    assert view._drawn_tiles == 0
    assert view._attr_visible() is False, "零瓦片时不得显示数据来源署名"

    view._drawn_tiles = 4
    assert view._attr_visible() is True

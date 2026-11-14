# SPDX-License-Identifier: MIT
"""地点检索与地点面板的契约（2026-11-09）。

三条边界，全部用**不联网**的假数据钉住：

1. 数据源缺表/断网 → 明确说不可用；不编"最近的大城市"顶上；
2. 面板只发意图（site_chosen / cleared），不自己写配置；
3. 0,0 与越界坐标的判定与站址层保持一致（0,0 合法，越界拒绝）。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.location import places as P  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------- #
# provider 层
# ---------------------------------------------------------------------- #
def test_offline_provider_missing_table_is_explicitly_unavailable(tmp_path):
    prov = P.OfflineJsonProvider(str(tmp_path / "nope.json"))
    ok, reason = prov.available()
    assert ok is False
    assert "不存在" in reason or "未内置" in reason
    # 不可用时 search 返回空，不编造
    assert prov.search("北京") == []
    assert prov.nearest(39.9, 116.4) is None


def _write_table(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"meta": {"licence": "ODbL-1.0"}, "places": rows}, f,
                  ensure_ascii=False)


def test_offline_provider_search_order_and_nearest(tmp_path):
    path = str(tmp_path / "places.json")
    _write_table(path, [
        ["北京", 39.9042, 116.4074, 21500000, "CN"],
        ["北海", 21.4735, 109.1195, 1850000, "CN"],
        ["长春", 43.8171, 125.3235, 9000000, "CN"],
    ])
    prov = P.OfflineJsonProvider(path)
    ok, _ = prov.available()
    assert ok is True

    # 精确命中排在包含匹配之前
    hits = prov.search("北")
    assert [h.name for h in hits][0] == "北京"
    # 完全匹配只有一条
    assert [h.name for h in prov.search("长春")] == ["长春"]
    assert prov.search("") == []
    assert prov.search("不存在的城市") == []

    near = prov.nearest(39.95, 116.45)
    assert near is not None and near.name == "北京"
    assert P.haversine_km(39.9042, 116.4074, 39.9042, 116.4074) == pytest.approx(0.0)


def test_offline_provider_skips_bad_rows(tmp_path):
    path = str(tmp_path / "places.json")
    _write_table(path, [
        ["坏行", "x", "y"],
        ["缺列"],
        ["好行", 10.0, 20.0],
    ])
    prov = P.OfflineJsonProvider(path)
    assert [p.name for p in prov._load()] == ["好行"]


def test_nominatim_parses_chinese_name(monkeypatch):
    raw = [{
        "lat": "39.9057136", "lon": "116.3912972",
        "name": "北京市", "display_name": "北京市, 中国",
        "type": "city", "importance": 0.795,
        "address": {"country": "中国"},
    }]
    monkeypatch.setattr(P, "_http_get_json", lambda url, timeout=6.0: raw)
    prov = P.OsmNominatimProvider(min_interval=0.0)
    hits = prov.search("Beijing")
    assert len(hits) == 1
    p = hits[0]
    assert p.name == "北京市"
    assert p.lat == pytest.approx(39.9057136)
    assert p.country == "中国"
    assert prov.last_error == ""


def test_nominatim_failure_is_empty_not_exception(monkeypatch):
    def boom(url, timeout=6.0):
        raise OSError("network down")

    monkeypatch.setattr(P, "_http_get_json", boom)
    prov = P.OsmNominatimProvider(min_interval=0.0)
    assert prov.search("北京") == []
    assert prov.nearest(39.9, 116.4) is None
    assert "network down" in prov.last_error


def test_nominatim_rejects_bad_coordinates(monkeypatch):
    monkeypatch.setattr(P, "_http_get_json", lambda url, timeout=6.0: [
        {"lat": "999", "lon": "116", "name": "假坐标"},
        {"lat": "39.9", "lon": "116.4"},          # 没名字 → 也不收
    ])
    prov = P.OsmNominatimProvider(min_interval=0.0)
    assert prov.search("x") == []


# ---------------------------------------------------------------------- #
# UI 层（注入假 provider，测试不联网）
# ---------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self, hits=None, fail=False):
        self.hits = hits or []
        self.fail = fail
        self.calls = []

    def available(self):
        return (True, "")

    def search(self, query, limit=8):
        self.calls.append(query)
        if self.fail:
            raise OSError("boom")
        return list(self.hits)

    def nearest(self, lat, lon):
        return None


def _panel(qapp, hits=None, fail=False):
    from location_panel import LocationPanel
    prov = _FakeProvider(hits, fail=fail)
    return LocationPanel(None, provider=prov, async_search=False), prov


def test_panel_search_fills_list_and_selects(qapp):
    p1 = P.Place("北京", 39.9042, 116.4074, source="osm", detail="北京市, 中国")
    panel, prov = _panel(qapp, hits=[p1])
    try:
        panel.search_edit.setText("北京")
        panel._do_search()
        assert prov.calls == ["北京"]
        assert panel.result_list.count() == 1
        item = panel.result_list.item(0)
        panel._on_item(item)
        assert panel._pending is p1
        assert panel.apply_btn.isEnabled() is True
        assert "北京" in panel.pending_label.text()
    finally:
        panel.close()


def test_panel_no_match_says_so(qapp):
    panel, _ = _panel(qapp, hits=[])
    try:
        panel.search_edit.setText("不存在")
        panel._do_search()
        assert panel.result_list.count() == 1
        assert panel.apply_btn.isEnabled() is False
        assert "没有匹配" in panel.hint.text()
    finally:
        panel.close()


def test_panel_search_failure_degrades_to_offline_hint(qapp):
    panel, _ = _panel(qapp, fail=True)
    try:
        panel.search_edit.setText("北京")
        panel._do_search()
        assert "直接在地图上点" in panel.hint.text()
    finally:
        panel.close()


def test_panel_map_pick_sets_pending(qapp):
    panel, _ = _panel(qapp)
    try:
        panel._on_map_picked(31.2304, 121.4737)
        assert panel._pending is not None
        assert panel._pending.source == "map"
        assert panel.apply_btn.isEnabled() is True
    finally:
        panel.close()


def test_map_pick_keeps_current_site_marker(qapp):
    """点地图只出「待确认」标记：当前站址标记不许跟着搬家。"""
    panel, _ = _panel(qapp)
    try:
        panel.set_current(45.0, 120.0, "手动设置")
        panel._on_map_picked(31.2304, 121.4737)
        view = panel.map_view
        assert view.marker() == (45.0, 120.0)
        pick = view.pick_marker()
        assert pick is not None and pick[0] == pytest.approx(31.2304, abs=1e-6)

        panel._on_clear()
        assert view.pick_marker() is None
        assert view.marker() is None
    finally:
        panel.close()


def test_panel_emits_site_and_clear_but_never_writes(qapp):
    p1 = P.Place("长春", 43.8171, 125.3235, source="osm")
    panel, _ = _panel(qapp, hits=[p1])
    chosen, cleared = [], []
    panel.site_chosen.connect(chosen.append)
    panel.cleared.connect(lambda: cleared.append(True))
    try:
        panel.search_edit.setText("长春")
        panel._do_search()
        panel._on_item(panel.result_list.item(0))
        panel._on_apply()
        assert len(chosen) == 1
        assert chosen[0].name == "长春"
        # 面板自己没有任何"写配置"的口子：只有这两个信号
        assert not hasattr(panel, "_save_gui_config")

        panel._on_clear()
        assert cleared == [True]
        assert panel.apply_btn.isEnabled() is False
    finally:
        panel.close()


def test_panel_shows_current_and_source(qapp):
    panel, _ = _panel(qapp)
    try:
        panel.set_current(45.0, 120.0, "手动设置")
        assert "45.0000" in panel.current_label.text()
        assert "手动设置" in panel.current_label.text()
        panel.set_current(None, None)
        assert "未设置" in panel.current_label.text()
    finally:
        panel.close()


def test_panel_fits_small_screen_and_apply_stays_reachable(qapp):
    """小屏（1000×600）上面板必须整体落在可用区，按钮不能被挤出屏幕。

    实测缺陷（2026-11-11）：Qt.Tool 首次 show 时先给小窗一个初始位置，
    布局随后把窗口撑到 632px 高，位置不收敛——1000×600 里底边到 1029，
    「使用此地点」整排在屏幕外，选点流程走不完。
    """
    from PySide6.QtCore import QRect
    from location_panel import LocationPanel
    panel = LocationPanel(None, provider=_FakeProvider([]),
                          async_search=False, map_online=False)
    try:
        panel.show()
        qapp.processEvents()
        avail = QRect(0, 0, 1000, 600)
        panel.fit_to_available(avail)
        qapp.processEvents()
        assert avail.contains(panel.geometry()), (
            f"面板必须完整落在可用区：{panel.geometry()} vs {avail}")
        btn = panel.apply_btn
        assert btn.isVisible() is True
        bottom = btn.mapToGlobal(btn.rect().bottomRight())
        assert avail.contains(bottom), (
            f"「使用此地点」必须可点到，实际底边在 {bottom}")
        # 地图是唯一可伸缩项：缩的是它，搜索/结果/手填一行没少。
        assert panel.search_edit.isVisible() and panel.result_list.isVisible()
    finally:
        panel.close()


class _GatedWorker:
    """只提供 isRunning()：模拟「上一轮搜索还在跑」。"""

    def __init__(self, running=True):
        self.running = running

    def isRunning(self):
        return self.running


def test_new_query_while_searching_is_queued_not_dropped(qapp):
    """异步搜索在途时再提交：新关键词排队，提示写明这次还没搜，结束后真的搜。

    实测缺陷（2026-11-14）：`_do_search` 在 worker 未结束时直接 return，
    用户新输入的关键词静默消失，回来的结果还是旧词的，输入框却已是新词。
    """
    from location_panel import LocationPanel
    prov = _FakeProvider([])
    panel = LocationPanel(None, provider=prov, async_search=True,
                          map_online=False)
    started = []
    panel._run_search = lambda p, q: started.append((p, q))
    try:
        panel._worker = _GatedWorker(running=True)
        panel.search_edit.setText("上海")
        panel._do_search()

        # ① 不并发起第二次搜索（一根棒子一个请求）
        assert prov.calls == []
        # ② 新关键词被保留，不是静默丢
        assert panel._queued_query == "上海"
        # ③ 用户看得见「这次还没搜」，而不是把旧结果当新词答案
        hint = panel.hint.text()
        assert "还在进行" in hint and "上海" in hint, hint

        # ④ 上一轮结束后，排队的关键词必须真的去搜（不是只记一笔）
        panel._worker.running = False
        panel._on_search_done([])
        assert started and started[0][1] == "上海", (
            f"上一轮结束后没有开始排队的那次搜索：{started}")
        assert panel._queued_query is None
    finally:
        panel._worker = None
        panel.close()


def test_normal_search_clears_stale_queue(qapp):
    """没有在途搜索时，新搜索直接执行并把残留队列清掉（不重复搜旧词）。"""
    from location_panel import LocationPanel
    prov = _FakeProvider([])
    panel = LocationPanel(None, provider=prov, async_search=False,
                          map_online=False)
    try:
        panel._queued_query = "旧词"
        panel.search_edit.setText("北京")
        panel._do_search()
        assert prov.calls == ["北京"]
        assert panel._queued_query is None
    finally:
        panel.close()

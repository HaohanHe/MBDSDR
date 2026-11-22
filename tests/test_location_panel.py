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
def qapp(isolated_profile):
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
    return LocationPanel(None, provider=prov, async_search=False,
                         map_online=False), prov


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


@pytest.mark.parametrize("async_search", [False, True], ids=["sync", "async"])
def test_panel_search_failure_degrades_to_offline_hint(qapp, async_search):
    from location_panel import LocationPanel

    prov = _FakeProvider(fail=True)  # 无 last_error 的旧 provider 仍兼容
    panel = LocationPanel(provider=prov, async_search=async_search, map_online=False)
    try:
        panel.search_edit.setText("示例查询")
        panel.search_btn.click()
        _wait_for_search(qapp, panel)
        hint = panel.hint.text()
        assert "OSError: boom" in hint and "示例查询" in hint
        assert "经纬网仍可点选" in hint and "手填经纬度" in hint
        assert "没有匹配" not in hint
        assert prov.calls == ["示例查询"]
    finally:
        _close_search_panel(qapp, panel)


def _wait_until(qapp, predicate):
    import time
    from PySide6.QtTest import QTest

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    pytest.fail("地点搜索未在 3 秒内完成")


def _wait_for_search(qapp, panel):
    """Pump Qt until both the UI result and the worker completion arrive."""
    from PySide6.QtCore import QThread

    _wait_until(qapp, lambda: panel.search_btn.isEnabled()
                and not any(w.isRunning() for w in panel.findChildren(QThread)))


def _close_search_panel(qapp, panel):
    from PySide6.QtCore import QThread

    # 即使断言失败也先收线程；close() 本来只是隐藏，不能替代等待。
    for worker in panel.findChildren(QThread):
        assert worker.wait(3000), "地点搜索线程未退出"
    qapp.processEvents()
    panel.close()


@pytest.mark.parametrize("async_search", [False, True], ids=["sync", "async"])
def test_panel_real_provider_http_failure_is_not_no_match(
        qapp, monkeypatch, tmp_path, async_search):
    """真实 provider 的 [] + last_error 必须穿过 UI，不能冒充零匹配。"""
    from urllib.parse import parse_qs, urlparse
    from PySide6.QtCore import Qt
    from location_panel import LocationPanel
    from osm_map import TileCache

    calls = []

    def http(url, timeout=6.0):
        query = parse_qs(urlparse(url).query)["q"][0]
        calls.append(query)
        if query == "故障查询":
            raise OSError("network down: test transport")
        return [{"name": "示例站点", "lat": "0", "lon": "0"}]

    monkeypatch.setattr(P, "_http_get_json", http)
    prov = P.OsmNominatimProvider(min_interval=0.0)
    panel = LocationPanel(provider=prov, async_search=async_search,
                          map_online=False, map_cache=TileCache(root=tmp_path))
    chosen, cleared = [], []
    panel.site_chosen.connect(chosen.append)
    panel.cleared.connect(lambda: cleared.append(True))
    panel.set_current(0.0, 0.0, "示例站点")
    current_text = panel.current_label.text()
    try:
        panel.search_edit.setText("示例站点")
        panel.search_btn.click()
        _wait_for_search(qapp, panel)
        assert panel.result_list.item(0).data(Qt.UserRole).name == "示例站点"

        panel.search_edit.setText("故障查询")
        panel.search_btn.click()
        _wait_for_search(qapp, panel)
        assert "network down: test transport" in prov.last_error
        hint = panel.hint.text()
        assert "network down: test transport" in hint, hint
        assert "故障查询" in hint and "搜索失败" in hint
        assert "没有匹配" not in hint
        assert "恢复网络后" in hint and "再搜" in hint
        assert "经纬网" in hint and "仍可" in hint and "手填" in hint
        assert "#ff8a7a" in panel.hint.styleSheet()
        assert panel._results == []
        assert panel.result_list.count() == 1
        item = panel.result_list.item(0)
        assert item.data(Qt.UserRole) is None
        assert not item.flags() & Qt.ItemIsSelectable
        assert "搜索失败" in item.text()
        assert panel.current_label.text() == current_text
        assert panel.map_view.marker() == (0.0, 0.0)
        assert panel.map_view.pick_marker() is None
        assert panel._pending is None and not panel.apply_btn.isEnabled()
        assert chosen == [] and cleared == []
        assert calls == ["示例站点", "故障查询"]  # 无自动重试、无自动选址
    finally:
        _close_search_panel(qapp, panel)


@pytest.mark.parametrize("async_search", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("recovery", ["empty", "success"])
def test_real_provider_recovery_clears_error(qapp, monkeypatch, tmp_path,
                                            async_search, recovery):
    from PySide6.QtCore import Qt
    from location_panel import LocationPanel
    from osm_map import TileCache

    calls = []

    def http(url, timeout=6.0):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("temporary transport failure")
        if recovery == "empty":
            return []
        return [{"name": "恢复结果", "lat": "0", "lon": "0"}]

    monkeypatch.setattr(P, "_http_get_json", http)
    prov = P.OsmNominatimProvider(min_interval=0.0)
    panel = LocationPanel(provider=prov, async_search=async_search,
                          map_online=False, map_cache=TileCache(root=tmp_path))
    try:
        panel.search_edit.setText("示例查询")
        panel.search_btn.click()
        _wait_for_search(qapp, panel)
        assert "temporary transport failure" in panel.hint.text()
        panel.search_btn.click()  # 只有用户再次搜索才恢复；同词也不能拿旧错误
        _wait_for_search(qapp, panel)
        assert prov.last_error == ""
        hint = panel.hint.text()
        assert "temporary transport failure" not in hint
        assert "搜索失败" not in hint
        assert "#ff8a7a" not in panel.hint.styleSheet()
        item = panel.result_list.item(0)
        if recovery == "empty":
            assert "没有匹配" in hint and "换个写法" in hint
            assert item.data(Qt.UserRole) is None
            assert not item.flags() & Qt.ItemIsSelectable
        else:
            assert "找到 1 个结果" in hint
            assert item.data(Qt.UserRole).name == "恢复结果"
        assert panel._pending is None and not panel.apply_btn.isEnabled()
        assert len(calls) == 2
    finally:
        _close_search_panel(qapp, panel)


@pytest.mark.parametrize("outcome", ["failure", "empty", "success"])
def test_async_result_snapshots_error_and_query_before_ui_delivery(
        qapp, monkeypatch, tmp_path, outcome):
    from location_panel import LocationPanel
    from osm_map import TileCache

    calls = []

    def http(url, timeout=6.0):
        calls.append(url)
        if outcome == "failure":
            raise OSError("original request refused")
        if outcome == "empty":
            return []
        return [{"name": "原查询结果", "lat": "0", "lon": "0"}]

    monkeypatch.setattr(P, "_http_get_json", http)
    prov = P.OsmNominatimProvider(min_interval=0.0)
    panel = LocationPanel(provider=prov, map_online=False,
                          map_cache=TileCache(root=tmp_path))
    try:
        panel.search_edit.setText("原查询")
        panel.search_btn.click()
        assert panel._worker.wait(3000)  # 不泵 UI；结果已发出、尚未渲染
        prov.last_error = "unrelated later failure"
        panel.search_edit.setText("尚未提交的文字")
        _wait_for_search(qapp, panel)
        hint = panel.hint.text()
        assert "原查询" in hint and "尚未提交的文字" not in hint
        assert "unrelated later failure" not in hint
        if outcome == "failure":
            assert "original request refused" in hint
        elif outcome == "empty":
            assert "没有匹配" in hint and "换个写法" in hint
        else:
            assert "找到 1 个结果" in hint
        assert len(calls) == 1
    finally:
        _close_search_panel(qapp, panel)


@pytest.mark.parametrize("async_search", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("unavailable", [True, False], ids=["unavailable", "exception"])
def test_provider_preflight_failure_clears_old_results_with_reason(
        qapp, async_search, unavailable):
    from PySide6.QtCore import Qt
    from location_panel import LocationPanel

    class Provider(_FakeProvider):
        def available(self):
            if not self.calls:
                return True, ""
            if unavailable:
                return False, "离线城市表不可读"
            raise OSError("provider check failed")

    prov = Provider([P.Place("示例站点", 0.0, 0.0)])
    panel = LocationPanel(provider=prov, async_search=async_search, map_online=False)
    try:
        panel.search_edit.setText("前次查询")
        panel.search_btn.click()
        _wait_for_search(qapp, panel)
        assert panel.result_list.item(0).data(Qt.UserRole) is not None
        panel.search_edit.setText("本次查询")
        panel.search_btn.click()
        _wait_for_search(qapp, panel)
        reason = "离线城市表不可读" if unavailable else "OSError: provider check failed"
        assert reason in panel.hint.text()
        assert "本次查询" in panel.hint.text()
        assert "经纬网仍可点选" in panel.hint.text()
        assert panel._results == []
        assert panel.result_list.item(0).data(Qt.UserRole) is None
        assert prov.calls == ["前次查询"]
        assert not panel.apply_btn.isEnabled()
    finally:
        _close_search_panel(qapp, panel)


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


@pytest.mark.parametrize("width", [460, 760], ids=["minimum", "regular"])
def test_map_hint_has_its_own_row_and_enough_text_height(qapp, tmp_path, width):
    """窄窗不能把地图提示挤进工具行；长状态文字换行后仍须完整显示。"""
    from PySide6.QtCore import QEvent, QRect, Qt
    from location_panel import LocationPanel
    from osm_map import TileCache

    panel = LocationPanel(provider=_FakeProvider(fail=True), async_search=False,
                          map_online=False, map_cache=TileCache(root=tmp_path))
    try:
        panel.set_current(0.0, 0.0, "示例站点")
        panel._on_map_picked(0.3, 0.8)
        panel.search_edit.setText("故障查询")
        panel.search_btn.click()
        panel.resize(width, 740)
        panel.show()
        qapp.processEvents()
        assert panel.width() == width and panel.minimumWidth() == 460
        hint = panel.map_hint
        assert hint.text() == "拖拽平移 · 滚轮缩放 · 单击选点"
        assert "单击选点只出琥珀色虚线圈（待确认）" in hint.toolTip()
        tools = [panel.zoom_out_btn, panel.zoom_in_btn, panel.recenter_btn,
                 panel.style_combo, panel.download_btn, panel.map_zoom_label]
        long_hint = ("底图加载失败：请求超时。请检查网络或代理，恢复网络后重试；"
                     "未缓存区域仍可在经纬网上点选坐标，也可以在设置里手填经纬度。")
        for text in (hint.text(), long_hint):
            hint.setText(text)
            qapp.processEvents()
            assert hint.isVisible() and hint.wordWrap()
            assert hint.geometry().left() == panel.map_view.geometry().left()
            assert hint.geometry().right() == panel.map_view.geometry().right()
            assert hint.geometry().bottom() < panel.current_label.geometry().top()
            for index, tool in enumerate(tools):
                assert tool.isVisible() and panel.rect().contains(tool.geometry())
                assert tool.geometry().bottom() < hint.geometry().top()
                assert all(not tool.geometry().intersects(other.geometry())
                           for other in tools[index + 1:])
            content = hint.contentsRect()
            needed = hint.fontMetrics().boundingRect(
                QRect(0, 0, content.width(), 10000), Qt.TextWordWrap, text)
            assert 0 < needed.height() <= content.height()
            assert needed.width() <= content.width()
            assert hint.height() >= hint.heightForWidth(hint.width())
            if width == 460 and text != "拖拽平移 · 滚轮缩放 · 单击选点":
                assert needed.height() > hint.fontMetrics().height()
    finally:
        panel.close()
        panel.deleteLater()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)


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


@pytest.mark.parametrize("next_result", ["failure", "empty", "success"])
def test_new_query_while_searching_is_queued_not_dropped(
        qapp, monkeypatch, tmp_path, next_result):
    """真实 QThread：done 不是 finished，排队查询不能复用旧错误或抢跑。"""
    from threading import Event
    from urllib.parse import parse_qs, urlparse
    from PySide6.QtCore import Qt
    import location_panel as LP
    from osm_map import TileCache

    first_http = Event()
    release_http = Event()
    first_done = Event()
    release_finish = Event()
    second_http = Event()
    release_second = Event()
    calls, delivered = [], []

    class FinishingWorker(LP._SearchWorker):
        def run(self):
            super().run()
            if self._query == "旧查询":
                first_done.set()
                # 只放大 run() 发 done 到真正退出之间的时序窗口。
                release_finish.wait(3.0)

    def http(url, timeout=6.0):
        query = parse_qs(urlparse(url).query)["q"][0]
        calls.append(query)
        if query == "旧查询":
            first_http.set()
            assert release_http.wait(3.0)
            raise OSError("old transport unavailable")
        second_http.set()
        assert release_second.wait(3.0)
        if next_result == "failure":
            raise TimeoutError("new request timed out")
        if next_result == "empty":
            return []
        return [{"name": "新查询结果", "lat": "0", "lon": "0"}]

    monkeypatch.setattr(P, "_http_get_json", http)
    monkeypatch.setattr(LP, "_SearchWorker", FinishingWorker)
    prov = P.OsmNominatimProvider(min_interval=0.0)
    panel = LP.LocationPanel(provider=prov, map_online=False,
                             map_cache=TileCache(root=tmp_path))
    chosen = []
    panel.site_chosen.connect(chosen.append)
    try:
        panel.search_edit.setText("旧查询")
        panel.search_btn.click()
        assert first_http.wait(3.0)
        first_worker = panel._worker
        first_worker.done.connect(delivered.append)
        panel.search_edit.setText("被替换的排队词")
        panel.search_edit.returnPressed.emit()
        panel.search_edit.setText("新查询")
        panel.search_edit.returnPressed.emit()
        assert calls == ["旧查询"]
        assert panel._queued_query == "新查询"
        assert "还在进行" in panel.hint.text() and "新查询" in panel.hint.text()

        release_http.set()
        assert first_done.wait(3.0)
        _wait_until(qapp, lambda: bool(delivered))
        assert panel._worker is first_worker, "done 时旧线程仍在跑，必须等 finished"
        assert not second_http.is_set()
        assert not panel.search_btn.isEnabled()
        assert "新查询" in panel.hint.text()
        assert "old transport unavailable" not in panel.hint.text()

        release_finish.set()
        assert first_worker.wait(3000)
        _wait_until(qapp, second_http.is_set)
        assert calls == ["旧查询", "新查询"]
        assert "搜索中" in panel.hint.text() and "新查询" in panel.hint.text()
        assert "old transport unavailable" not in panel.hint.text()
        assert "old transport unavailable" not in panel.result_list.item(0).text()
        release_second.set()
        _wait_for_search(qapp, panel)
        hint = panel.hint.text()
        assert "新查询" in hint and "old transport unavailable" not in hint
        if next_result == "failure":
            assert "new request timed out" in hint and "搜索失败" in hint
            assert panel._results == []
        elif next_result == "empty":
            assert "没有匹配" in hint and "换个写法" in hint
            assert panel._results == []
        else:
            assert "找到 1 个结果" in hint
            assert panel.result_list.item(0).data(Qt.UserRole).name == "新查询结果"
        assert panel._queued_query is None
        assert chosen == [] and not panel.apply_btn.isEnabled()
        assert calls == ["旧查询", "新查询"]
    finally:
        release_http.set()
        release_finish.set()
        release_second.set()
        _wait_for_search(qapp, panel)
        _close_search_panel(qapp, panel)


def test_closing_panel_keeps_inflight_worker_safe_and_never_applies(
        qapp, monkeypatch, tmp_path):
    from threading import Event
    from PySide6.QtCore import QEvent, QThread
    from location_panel import LocationPanel
    from osm_map import TileCache

    started, release = Event(), Event()

    def http(url, timeout=6.0):
        started.set()
        assert release.wait(3.0)
        raise OSError("closed panel request failed")

    monkeypatch.setattr(P, "_http_get_json", http)
    panel = LocationPanel(provider=P.OsmNominatimProvider(min_interval=0.0),
                          map_online=False, map_cache=TileCache(root=tmp_path))
    chosen, cleared = [], []
    panel.site_chosen.connect(chosen.append)
    panel.cleared.connect(lambda: cleared.append(True))
    panel.set_current(0.0, 0.0, "示例站点")
    try:
        panel.show()
        panel.search_edit.setText("关闭时的查询")
        panel.search_btn.click()
        assert started.wait(3.0)
        panel.close()  # 现有语义：仅隐藏，不销毁仍在运行的 worker
        assert not panel.isVisible()
        assert panel._worker.isRunning()
        release.set()
        _wait_for_search(qapp, panel)
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
        assert panel._worker is None
        assert panel.findChildren(QThread) == []
        assert "closed panel request failed" in panel.hint.text()
        assert not panel.isVisible()
        assert chosen == [] and cleared == []
        assert panel.map_view.marker() == (0.0, 0.0)
    finally:
        release.set()
        _close_search_panel(qapp, panel)


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

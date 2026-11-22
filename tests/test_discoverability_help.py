"""可发现性护栏：能力目录 / 快捷键一览 / 三步上手必须与真实注册表一致。

钉住的红线（docs/discoverability.md）：
  * 目录里每个 source 都必须在对应注册表里真实存在，不许手抄会漂移的清单；
  * 触发不了的条目必须标 unavailable + 写原因，不许出现点了没反应的能力；
  * 快捷键面板的键位必须逐字等于 desktop/shortcuts.py，且每条恰好出现一次；
  * 三步上手引用的键位必须仍登记在 shortcuts.py；
  * 帮助面板非模态、默认不弹出，不遮挡主界面。

反证：删掉 panel_registry 的 summary、把 help_panel 换成硬编码清单、
给 shortcuts.py 的条目改名而不改面板、或让 PTT 能力失去 `tx_button`，
本文件都会红。
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def catalog():
    from capability_catalog import build_catalog
    return build_catalog()


@pytest.fixture(scope="module")
def main_window(qtapp, isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不拖垮用例
        pass
    w.close()


def _by_prefix(items, prefix):
    return [x for x in items if x.source.startswith(prefix)]


# ---------------------------------------------------------------------------
# 目录 = 注册表
# ---------------------------------------------------------------------------
def test_catalog_sources_come_from_real_registries(catalog):
    from panel_registry import registry
    import mode_registry
    from mbdsdr_ai import decoder_registry
    from mbdsdr_ai.decode_router import DIGITAL_MODE_CAPABILITIES
    from mbdsdr_ai.core.mcp_server import ALL_DOMAIN_NAMES
    from capability_catalog import SHORTCUT_CAPABILITIES

    panel_ids = {s.id for s in registry().list()}
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "panel_registry:")}
            == panel_ids)
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "mode_registry:")}
            == {m.mode for m in mode_registry.list_modes()})
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "decode_router:")}
            == set(DIGITAL_MODE_CAPABILITIES))
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "decoder_registry:")}
            == {d.mode for d in decoder_registry.list_decoders()})
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "mcp:")}
            == set(ALL_DOMAIN_NAMES))
    assert ({x.source.split(":", 1)[1] for x in _by_prefix(catalog, "shortcut:")}
            == {sid for sid, _c, _d in SHORTCUT_CAPABILITIES})


def test_ai_domain_counts_match_live_tool_registry(catalog):
    """AI 工具域的工具名/数量必须来自工具表实况，不是手抄映射的快照。"""
    from mbdsdr_ai.core import mcp_server as mcp

    live = [t["name"] for t in mcp._build_tool_table()]
    buckets = mcp.tool_domain_view()
    diff = set(buckets) ^ set(mcp.ALL_DOMAIN_NAMES)
    assert not diff, f"域名表与工具映射分叉: {sorted(diff)}"
    stray = mcp.unmapped_tool_names()
    assert not stray, f"工具表里有工具没登记域: {stray}"
    total = sum(len(v) for v in buckets.values())
    assert total == len(live), f"域内工具总数 {total} != 工具表实际 {len(live)}"

    rows = _by_prefix(catalog, "mcp:")
    assert len(rows) == len(mcp.ALL_DOMAIN_NAMES)
    for dom in mcp.ALL_DOMAIN_NAMES:
        row = next(x for x in rows if x.source == f"mcp:{dom}")
        if buckets[dom]:
            assert f"{len(buckets[dom])} 个工具" in row.trigger
            assert row.status == "available"
        else:
            assert "为空" in row.trigger
            assert row.status == "unavailable" and row.note


def test_new_unmapped_tool_makes_catalog_red(monkeypatch):
    """反证：工具表多一个未登记域的工具 → 目录如实列出，一致性断言会红。"""
    from mbdsdr_ai.core import mcp_server as mcp

    original = mcp._build_tool_table

    def fake_table():
        rows = list(original())
        rows.append({"name": "zz_probe_unmapped", "description": "",
                     "inputSchema": {"type": "object"}})
        return rows

    monkeypatch.setattr(mcp, "_build_tool_table", fake_table)
    assert mcp.unmapped_tool_names() == ["zz_probe_unmapped"]
    assert sum(len(v) for v in mcp.tool_domain_view().values()) == len(fake_table()) - 1

    from capability_catalog import build_catalog
    stray = [x for x in build_catalog() if x.source == "mcp:unmapped"]
    assert len(stray) == 1
    assert "zz_probe_unmapped" in stray[0].note
    assert stray[0].status == "unavailable"


def test_every_panel_row_uses_registry_summary(catalog):
    from panel_registry import registry
    specs = {s.id: s for s in registry().list()}
    for cap in _by_prefix(catalog, "panel_registry:"):
        spec = specs[cap.source.split(":", 1)[1]]
        assert cap.title == spec.name
        assert spec.summary, f"面板 {spec.id} 缺 summary，能力目录会变成空话"
        assert cap.description == spec.summary


def test_catalog_has_all_categories(catalog):
    from capability_catalog import CATEGORIES
    seen = {x.category for x in catalog}
    assert seen == set(CATEGORIES)


# ---------------------------------------------------------------------------
# 诚实性：不可用必须写原因；可用必须有真实触发
# ---------------------------------------------------------------------------
def test_status_and_reason_contract(catalog):
    from capability_catalog import CATEGORIES
    for cap in catalog:
        assert cap.category in CATEGORIES
        assert cap.status in ("available", "conditional", "unavailable")
        if cap.status == "unavailable":
            assert cap.note, f"{cap.title} 标了不可用却没写原因"
            assert cap.trigger.startswith("无"), f"{cap.title} 不可用却给了入口"
        if cap.status == "conditional":
            assert cap.note, f"{cap.title} 需条件却没写条件"
        if cap.status == "available":
            assert cap.trigger and not cap.trigger.startswith("无"), \
                f"{cap.title} 写可用却没有触发方式"


def test_unwired_digital_modes_are_marked_unavailable(catalog):
    from mbdsdr_ai.decode_router import (
        DIGITAL_MODE_CAPABILITIES, EVIDENCE_NO_SAMPLE,
    )
    rows = {x.title: x for x in _by_prefix(catalog, "decode_router:")}
    for key, cap in DIGITAL_MODE_CAPABILITIES.items():
        row = rows[cap.mode.upper()]
        if not cap.dependency:
            assert row.status == "unavailable"
            assert "not_implemented" in row.note
        elif cap.evidence == EVIDENCE_NO_SAMPLE:
            # 无样本：有入口但不得显示为「已支持」。
            assert row.status == "conditional"
            assert "未验证" in row.note and "不宣称已支持" in row.note
            assert "--decode-file" in row.trigger
        else:
            assert row.status == "available"
            assert "--decode-file" in row.trigger


def test_decoder_registry_reports_wiring_gap(catalog):
    rows = {x.title: x for x in _by_prefix(catalog, "decoder_registry:")}
    assert rows["VOR"].status == "unavailable"
    assert "未接线" in rows["VOR"].note


def test_static_capabilities_symbols_exist(catalog, main_window):
    """无注册表可查的两条：直接调真实对象验行为，不靠扫源码口说。"""
    sky = [x for x in catalog if x.source.endswith(":_build_search_widgets")]
    tx = [x for x in catalog if x.source.endswith(":_tx_block_reason")]
    assert sky and tx

    from rf_sky_view import RFSkyView
    v = RFSkyView()
    build = getattr(v, "_build_search_widgets", None)
    assert callable(build), "天球搜索入口方法消失（目录里的「搜索天体」成空话）"
    build()
    assert getattr(v, "_search_edit", None) is not None and v._search_edit.placeholderText(), \
        "天球搜索框没建出来"

    assert callable(getattr(main_window, "_tx_block_reason", None)), \
        "TX 拦截原因出口消失"
    assert getattr(main_window, "tx_button", None) is not None, "TX 按钮控件消失"
    reason = main_window._tx_block_reason()
    assert isinstance(reason, str) and "未连接" in reason, (
        "未连接时 TX 必须给出可读原因，实际 " f"{reason!r}")


def test_unavailable_panels_would_be_caught(qtapp):
    """不存在于注册表的面板不能出现在目录（防止手抄旧清单）。"""
    from panel_registry import registry
    ids = {s.id for s in registry().list()}
    from capability_catalog import build_catalog
    for cap in build_catalog():
        if cap.source.startswith("panel_registry:"):
            assert cap.source.split(":", 1)[1] in ids


# ---------------------------------------------------------------------------
# 搜索 / 分类过滤
# ---------------------------------------------------------------------------
def test_search_and_category_filter(catalog):
    from capability_catalog import search_capabilities
    assert len(search_capabilities(catalog, "")) == len(catalog)
    ft8 = search_capabilities(catalog, "ft8")
    assert ft8 and any("FT8" in x.title for x in ft8)
    spec = search_capabilities(catalog, "频谱")
    assert any("频谱" in x.title for x in spec)
    decode_only = search_capabilities(catalog, "", "解码")
    assert decode_only and all(x.category == "解码" for x in decode_only)
    assert not search_capabilities(catalog, "不存在的词_zzz")


# ---------------------------------------------------------------------------
# 快捷键面板：单一事实源
# ---------------------------------------------------------------------------
def test_shortcuts_panel_is_generated_from_single_source():
    from shortcuts import SHORTCUTS
    from shortcuts_panel import grouped_shortcuts, key_text, trigger_text

    rows = [s for _g, group in grouped_shortcuts() for s in group]
    assert len(rows) == len(SHORTCUTS)
    assert {s.id for s in rows} == {s.id for s in SHORTCUTS}
    for s in rows:
        assert key_text(s) == (s.key or "无快捷键")
        assert s.label in trigger_text(s)
    # 无键项必须有原因（否则面板会出现「无快捷键」但不知为什么）
    for s in SHORTCUTS:
        if not s.key:
            assert s.note, f"{s.id} 无键也无说明"


def test_shortcut_search_filters():
    from shortcuts_panel import grouped_shortcuts
    rows = [s for _g, group in grouped_shortcuts("录音") for s in group]
    assert rows and all("录音" in (s.label + s.note + (s.key or "")) for s in rows)
    assert not grouped_shortcuts("不存在_zzz")


# ---------------------------------------------------------------------------
# 按目的选路（旧三步上手 API 保留，内容不再串行）
# ---------------------------------------------------------------------------
def test_onboarding_steps_use_registered_shortcuts():
    from help_panel import onboarding_steps
    from shortcuts import BY_ID
    steps = onboarding_steps()
    assert [s["title"] for s in steps] == ["听信号", "回看已有录音", "看天空"]
    assert [s["shortcut_id"] for s in steps] == ["file.connect", "vfo.replay", "view.planetarium"]
    ids = [s["shortcut_id"] for s in steps if s["shortcut_id"]]
    for sid in ids:
        assert sid in BY_ID
        assert BY_ID[sid].key
        assert BY_ID[sid].key in next(s["body"] for s in steps
                                      if s["shortcut_id"] == sid)
    listen, replay, sky = [s["body"] for s in steps]
    assert "伪造" in listen  # 明确不演示假数据
    assert "连接不等于自动出声" in listen and "由你" in listen
    assert "实际支持的频率和解调" in listen and "不猜电台频率" in listen
    assert "不需要连接设备" in replay and "录音不是直播" in replay
    assert "不需要 SDR" in sky and "真实观测地点" in sky
    assert "缺地点请选地点" in sky and "TLE" in sky and "缺失或过期请更新轨道" in sky
    assert "左栏" in sky and "独立星象仪窗口，两者不同" in sky
    assert "不代表已定位" in sky and "不代表收到信号" in sky


# ---------------------------------------------------------------------------
# 路线使用真实 Qt 动作/主窗口接口，不造 SDR 数据
# ---------------------------------------------------------------------------
@pytest.fixture
def route_window(qtapp, isolated_profile):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QComboBox, QMainWindow, QPushButton, QTabWidget, QWidget
    from main_window import MainWindow
    from shortcuts import BY_ID

    class Window(QMainWindow):
        def select_tab(self, key):
            self.calls.append(key)
            return MainWindow.select_tab(self, key)

    window = Window()
    window.calls = []
    window._app_mode = "ide"
    window.left_tab = QTabWidget(window)
    window.setCentralWidget(window.left_tab)
    for key in ("rf_sky", "spectrum"):
        i = window.left_tab.addTab(QWidget(), key)
        window.left_tab.tabBar().setTabData(i, key)
    window.right_tab = QTabWidget(window)
    window.device_panel = QWidget()
    window.device_panel.dev_combo = QComboBox(window.device_panel)
    window.right_tab.addTab(window.device_panel, "设备")
    window.right_tab.tabBar().setTabData(0, "device")
    window.listen_combo = QComboBox(window)
    window.listen_combo.addItem("停止解调", "idle")
    window.listen_combo.addItem("收听", "listen")
    window.listen_combo.currentIndexChanged.connect(lambda: window.calls.append("listen"))
    window.replay_btn = QPushButton("回放", window)
    window.replay_btn.clicked.connect(lambda: window.calls.append("replay"))
    window._planetarium_location_btn = QPushButton("地点", window)
    window._planetarium_location_btn.clicked.connect(lambda: window.calls.append("location"))
    window._planetarium_window = QWidget()
    window.menu_actions = {}

    def toggle():
        window.calls.append("planetarium")
        window._app_mode = "ide" if window._app_mode == "planetarium" else "planetarium"

    for menu, sid, slot in (
        ("文件(&F)", "file.connect", lambda: window.calls.append("connect")),
        ("视图(&V)", "view.planetarium", toggle),
        ("工具(&T)", "tools.sat_tle", lambda: window.calls.append("tle")),
    ):
        action = QAction(BY_ID[sid].label, window)
        action.triggered.connect(slot)
        window.menuBar().addMenu(menu).addAction(action)
        window.menu_actions[sid] = action
    yield window
    window._planetarium_window.close()
    window._planetarium_window.deleteLater()
    window.close()
    window.deleteLater()


def _route_button(panel, source):
    from PySide6.QtWidgets import QPushButton
    button = panel.findChild(QPushButton, f"help:{source}")
    assert button is not None, source
    return button


def test_help_routes_use_index_and_run_once(route_window, monkeypatch):
    import command_palette
    from dataclasses import replace
    from help_panel import HelpPanel
    from PySide6.QtWidgets import QGroupBox

    indexed, ran = [], []
    original = command_palette.build_index

    def build(window):
        indexed.append(window)
        commands = []
        for command in original(window):
            if command.run is not None:
                def run(c=command):
                    ran.append(c.source)
                    return c.run()
                command = replace(command, run=run)
            commands.append(command)
        return commands

    monkeypatch.setattr(command_palette, "build_index", build)
    panel = HelpPanel(route_window)
    assert [g.title() for g in panel.tabs.widget(0).findChildren(QGroupBox)] == [
        "看天空", "听信号", "回看已有录音"]
    assert route_window.calls == [] and ran == []
    route_window.left_tab.setCurrentIndex(1)
    _route_button(panel, "tab:rf_sky").click()
    assert route_window.calls == ["rf_sky"]
    assert ran == ["tab:rf_sky"]
    assert route_window.left_tab.tabBar().tabData(route_window.left_tab.currentIndex()) == "rf_sky"
    _route_button(panel, "tab:rf_sky").click()
    assert route_window.calls == ["rf_sky", "rf_sky"]
    assert route_window.left_tab.currentIndex() == 0
    _route_button(panel, "panel:device").click()
    _route_button(panel, "tab:spectrum").click()
    assert route_window.calls == ["rf_sky", "rf_sky", "device", "spectrum"]
    assert ran == ["tab:rf_sky", "tab:rf_sky", "panel:device", "tab:spectrum"]
    assert route_window.listen_combo.currentData() == "idle"
    assert all(w is route_window for w in indexed)


def test_help_planetarium_is_idempotent_and_distinct(route_window):
    from help_panel import HelpPanel
    panel = HelpPanel(route_window)
    _route_button(panel, "menu:view.planetarium").click()
    assert route_window.calls == ["planetarium"]
    assert route_window._app_mode == "planetarium"
    _route_button(panel, "menu:view.planetarium").click()
    assert route_window.calls == ["planetarium"]  # 不能反向 toggle
    assert route_window._app_mode == "planetarium"
    assert not _route_button(panel, "tab:rf_sky").isEnabled()
    assert "独立星象仪" in _route_button(panel, "tab:rf_sky").toolTip()
    assert route_window._planetarium_window.isVisible()


@pytest.mark.parametrize("source,call", [
    ("control:_planetarium_location_btn", "location"),
    ("menu:tools.sat_tle", "tle"),
    ("control:replay_btn", "replay"),
])
def test_help_explicit_actions_only_once(route_window, source, call):
    from help_panel import HelpPanel
    panel = HelpPanel(route_window)
    assert route_window.calls == []
    _route_button(panel, source).click()
    assert route_window.calls == [call]
    assert panel.last_route_result["ok"]


@pytest.mark.parametrize("after_open", [False, True], ids=["already-busy", "becomes-busy"])
@pytest.mark.parametrize("state,reason", [
    ("playing", "正在回放或已暂停，请先在回放条停止当前回放。"),
    ("paused", "正在回放或已暂停，请先在回放条停止当前回放。"),
    ("loading", "正在读取录音，请在现有回放入口取消或等待。"),
    ("load-finished", "正在读取录音，请在现有回放入口取消或等待。"),
])
def test_help_replay_preserves_busy_work(route_window, state, reason, after_open):
    """选择文件不能退化成停止/取消；帮助打开后的状态变化也必须重检。"""
    import threading
    from PySide6.QtCore import QTimer
    from help_panel import HelpPanel

    panel = None
    timer = QTimer(route_window)
    release = threading.Event()
    loader = threading.Thread(target=release.wait)
    route_window._replay_timer = None
    route_window._replay_load_thread = None
    try:
        if after_open:
            panel = HelpPanel(route_window)
            panel.show()
            assert _route_button(panel, "control:replay_btn").isEnabled()
        if state in ("loading", "load-finished"):
            loader.start()
            route_window._replay_load_thread = loader
            if state == "load-finished":
                # worker 已结束，但 GUI 尚未消费结果，原回放按钮仍会取消。
                release.set()
                loader.join(timeout=5)
            assert loader.is_alive() == (state == "loading")
        else:
            route_window._replay_timer = timer
            if state == "playing":
                timer.start(60000)
            assert timer.isActive() == (state == "playing")
        if panel is None:
            panel = HelpPanel(route_window)
            assert not _route_button(panel, "control:replay_btn").isEnabled()
            panel.show()
        button = _route_button(panel, "control:replay_btn")
        assert route_window.replay_btn.isEnabled()
        assert route_window.calls == []
        button.click()
        assert route_window.calls == [], "帮助选文件入口不得触发原回放按钮的停止/取消动作"
        assert not button.isEnabled()
        assert reason in button.toolTip()
        assert reason in button.accessibleDescription()
        note = next(n for b, n, _s in panel._route_entries if b is button)
        assert reason in note.text() and not note.isHidden()
        assert panel.isVisible()
        if after_open:
            assert panel.last_route_result == {
                "ok": False, "status": "UNAVAILABLE", "reason": reason}
        if state in ("loading", "load-finished"):
            assert route_window._replay_load_thread is loader
            assert loader.is_alive() == (state == "loading")
        else:
            assert route_window._replay_timer is timer
            assert timer.isActive() == (state == "playing")
    finally:
        timer.stop()
        release.set()
        if loader.ident is not None:
            loader.join(timeout=5)
        if panel is not None:
            panel.close()


def test_help_without_parent_disables_routes_with_visible_reasons(qtapp, isolated_profile):
    from help_panel import HelpPanel
    panel = HelpPanel()
    for button, note, _source in panel._route_entries:
        assert not button.isEnabled()
        assert "没有主窗口" in note.text()
        assert not note.isHidden()
        button.click()
    assert panel.last_route_result == {}
    panel.deleteLater()


@pytest.mark.parametrize("missing", ["tab", "select", "control", "menu", "state", "device"])
def test_help_missing_entry_is_not_a_dead_link(route_window, missing):
    from help_panel import HelpPanel
    if missing == "tab":
        route_window.left_tab.removeTab(0)
        source, reason = "tab:rf_sky", "没有这个页签"
    elif missing == "select":
        route_window.select_tab = None
        source, reason = "tab:rf_sky", "select_tab"
    elif missing == "control":
        route_window.replay_btn = None
        source, reason = "control:replay_btn", "没有这个按钮"
    elif missing == "menu":
        route_window.menu_actions["tools.sat_tle"].setText("入口已移除")
        source, reason = "menu:tools.sat_tle", "真实命令入口"
    elif missing == "state":
        del route_window._app_mode
        source, reason = "menu:view.planetarium", "无法确认"
    else:
        route_window.device_panel.dev_combo.setEnabled(False)
        source, reason = "panel:device", "设备选择控件"
    panel = HelpPanel(route_window)
    button = _route_button(panel, source)
    assert not button.isEnabled() and reason in button.toolTip()
    note = next(n for b, n, _s in panel._route_entries if b is button)
    assert reason in note.text() and not note.isHidden()
    button.click()
    assert route_window.calls == []


@pytest.mark.parametrize("source", ["tab:rf_sky", "menu:tools.sat_tle", "control:replay_btn"])
def test_help_rechecks_disabled_targets_at_click(route_window, source):
    from help_panel import HelpPanel
    panel = HelpPanel(route_window)
    button = _route_button(panel, source)
    assert button.isEnabled()
    if source.startswith("tab:"):
        route_window.left_tab.setTabEnabled(0, False)
    elif source.startswith("menu:"):
        action = route_window.menu_actions["tools.sat_tle"]
        action.setEnabled(False)
        action.setToolTip("离线，暂不能更新轨道")
    else:
        route_window.replay_btn.setEnabled(False)
        route_window.replay_btn.setToolTip("录音读取中")
    button.click()  # 面板仍开着，不能使用旧索引里的 run
    assert not button.isEnabled()
    assert not panel.last_route_result["ok"]
    assert panel.last_route_result["reason"]
    assert route_window.calls == []


def test_help_failed_command_keeps_reason_visible(route_window, monkeypatch):
    from help_panel import HelpPanel
    monkeypatch.setattr(route_window, "select_tab", lambda _key: {
        "ok": False, "status": "ERROR", "reason": "目标页签已移除"})
    panel = HelpPanel(route_window)
    panel.show()
    _route_button(panel, "tab:rf_sky").click()
    assert panel.isVisible()
    assert panel.last_route_result == {"ok": False, "status": "ERROR", "reason": "目标页签已移除"}
    note = next(n for _b, n, s in panel._route_entries if s == "tab:rf_sky")
    assert note.text() == "目标页签已移除" and not note.isHidden()


def test_help_keyboard_navigation_and_escape(route_window, qtapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTextBrowser
    from help_panel import HelpPanel
    panel = HelpPanel(route_window)
    panel.show()
    panel.activateWindow()
    qtapp.processEvents()
    buttons = [b for b, _n, _s in panel._route_entries]
    buttons[0].setFocus()
    qtapp.processEvents()
    for previous, current in zip(buttons, buttons[1:]):
        QTest.keyClick(previous, Qt.Key_Tab)
        assert current.hasFocus(), current.text()
    for button in buttons:
        assert button.focusPolicy() == Qt.StrongFocus and not button.autoDefault()
    # 回放只有焦点上的空格才激活；Tab 穿越不产生动作。
    assert route_window.calls == []
    QTest.keyClick(buttons[-1], Qt.Key_Space)
    assert route_window.calls == ["replay"]
    panel.show()
    QTest.keyClick(buttons[0], Qt.Key_Escape)
    assert not panel.isVisible()
    assert route_window.calls == ["replay"]
    assert all(not b.openLinks() for b in panel.findChildren(QTextBrowser))


def test_help_route_targets_exist_on_real_main_window(main_window, qtapp, monkeypatch):
    """真实 MainWindow + 真命令索引：只导航，不执行连接/TLE/文件播放。"""
    from help_panel import HelpPanel
    from PySide6.QtTest import QSignalSpy
    original = main_window.select_tab
    selected = []

    def select(key):
        selected.append(key)
        return original(key)

    monkeypatch.setattr(main_window, "select_tab", select)
    replay = QSignalSpy(main_window.replay_btn.clicked)
    connect = QSignalSpy(main_window._shortcut_actions["file.connect"].triggered)
    listening = QSignalSpy(main_window.listen_combo.currentIndexChanged)
    mode = main_window._app_mode
    panel = HelpPanel(main_window)
    for button, note, _source in panel._route_entries:
        assert button.isEnabled(), note.text()
    for source in ("tab:rf_sky", "tab:rf_sky", "panel:device", "tab:spectrum"):
        _route_button(panel, source).click()
        assert panel.last_route_result["ok"], panel.last_route_result
    assert selected == ["rf_sky", "rf_sky", "device", "spectrum"]
    assert main_window._app_mode == mode
    assert replay.count() == connect.count() == listening.count() == 0
    assert main_window._listen_state == "idle"
    panel.close()
    qtapp.processEvents()


def test_help_idle_replay_opens_file_picker_once(main_window, monkeypatch):
    """空闲入口沿用真实回放接线；只替换文件对话框边界，不读盘/播放。"""
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QFileDialog
    from help_panel import HelpPanel

    assert main_window._replay_timer is None
    assert main_window._replay_load_thread is None
    choices = []

    def choose_file(*args):
        choices.append(args)
        return "", ""  # 用户取消选择：不启动读取或播放。

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_file)
    replay = QSignalSpy(main_window.replay_btn.clicked)
    panel = HelpPanel(main_window)
    try:
        panel.show()
        button = _route_button(panel, "control:replay_btn")
        assert button.isEnabled()
        assert choices == [] and replay.count() == 0
        button.click()
        assert replay.count() == 1
        assert len(choices) == 1
        assert choices[0][0] is main_window and choices[0][1] == "选择录音文件"
        assert panel.last_route_result["ok"]
        assert not panel.isVisible()
        assert main_window._replay_timer is None
        assert main_window._replay_load_thread is None
    finally:
        panel.close()


def test_help_real_planetarium_does_not_toggle_back(main_window, qtapp):
    from help_panel import HelpPanel
    from PySide6.QtTest import QSignalSpy
    assert main_window._app_mode == "ide"
    assert "未连接" in main_window._tx_block_reason()
    action = QSignalSpy(main_window._planetarium_action.triggered)
    connect = QSignalSpy(main_window._shortcut_actions["file.connect"].triggered)
    listening = QSignalSpy(main_window.listen_combo.currentIndexChanged)
    panel = HelpPanel(main_window)
    try:
        assert panel._resolve_route("control:replay_btn")[0] is main_window.replay_btn
        assert panel._resolve_route("control:_planetarium_location_btn")[0] is main_window._planetarium_location_btn
        button = _route_button(panel, "menu:view.planetarium")
        button.click()
        qtapp.processEvents()
        assert main_window._app_mode == "planetarium"
        assert action.count() == 1
        assert main_window._planetarium_window.isVisible()
        panel.show()
        button.click()
        qtapp.processEvents()
        assert main_window._app_mode == "planetarium"
        assert action.count() == 1
        assert connect.count() == listening.count() == 0
        assert main_window._listen_state == "idle"
    finally:
        panel.close()
        main_window.set_app_mode("ide")


# ---------------------------------------------------------------------------
# Qt 冒烟
# ---------------------------------------------------------------------------
def test_help_panel_smoke(qtapp, catalog):
    from help_panel import HelpPanel
    dlg = HelpPanel(initial_tab=1)
    assert not dlg.isModal()          # 不强制弹窗
    assert not dlg.isVisible()        # 构造时不抢焦点
    assert dlg.visible_row_count() == len(catalog)

    dlg.search_edit.setText("ft8")
    assert 0 < dlg.visible_row_count() < len(catalog)

    dlg.search_edit.setText("不存在的词_zzz")
    assert dlg.visible_row_count() == 0
    assert dlg.tree.isHidden()            # 无结果时列表隐藏
    assert not dlg.empty_label.isHidden()  # 空态文案出现
    assert "换个关键词" in dlg.empty_label.text()
    dlg.deleteLater()


def test_shortcuts_panel_smoke(qtapp):
    from shortcuts_panel import ShortcutsPanel
    from shortcuts import SHORTCUTS
    dlg = ShortcutsPanel()
    assert not dlg.isModal()
    assert not dlg.isVisible()
    assert sum(t.childCount() for t in
               (dlg.tree.topLevelItem(i)
                for i in range(dlg.tree.topLevelItemCount()))) == len(SHORTCUTS)
    dlg.search_edit.setText("卫星")
    assert 0 < sum(t.childCount() for t in
                   (dlg.tree.topLevelItem(i)
                    for i in range(dlg.tree.topLevelItemCount())))
    dlg.deleteLater()


def test_help_menu_is_wired_in_main_window(main_window, qtapp):
    """帮助菜单三个入口必须真的打开对应面板，且落在正确的页签。"""
    from PySide6.QtGui import QAction

    acts = {a.text(): a for a in main_window.findChildren(QAction)}
    for label, tab in (("三步上手", 0), ("能力目录", 1)):
        assert label in acts, f"帮助菜单缺「{label}」入口"
        acts[label].trigger()
        qtapp.processEvents()
        dlg = getattr(main_window, "_help_dialog", None)
        assert dlg is not None and dlg.isVisible(), f"「{label}」没有打开帮助面板"
        assert dlg.tabs.currentIndex() == tab, (
            f"「{label}」打开的页签是 {dlg.tabs.currentIndex()}，应为 {tab}")
        dlg.close()
        qtapp.processEvents()

    assert "快捷键一览" in acts, "帮助菜单缺「快捷键一览」入口"
    acts["快捷键一览"].trigger()
    qtapp.processEvents()
    sc = getattr(main_window, "_shortcuts_dialog", None)
    assert sc is not None and sc.isVisible(), "「快捷键一览」没有打开面板"
    sc.close()
    qtapp.processEvents()


def test_deep_right_column_jumps_are_discoverable(main_window):
    """右栏埋得最深的几处必须在命令面板里看得见、点得动（不靠新快捷键）。

    可发现性口径：入口要么在菜单里，要么在命令面板（Ctrl+K 模糊搜索）里；
    「跳到设备」「跳到增益」「打开设置」「导出」走后者，且必须是 available + 有 run。
    """
    from command_palette import build_index

    rows = {c.title: c for c in build_index(main_window)}
    for title in ("跳到设备", "跳到增益", "打开设置", "导出"):
        row = rows.get(title)
        assert row is not None, f"命令面板缺「{title}」入口（可发现性断了）"
        assert row.group == "面板"
        assert row.status == "available", (
            f"「{title}」应为可用入口，实际 {row.status}：{row.reason}")
        assert row.run is not None, f"「{title}」标了可用却没有执行入口"
        assert row.description.strip(), f"「{title}」没写「跳到哪、省掉什么」"


# ---------------------------------------------------------------------------
# 能力断言 ↔ AI 工具注册表实况（2026-11-11，A1「不暴露发射工具」事故）
# ---------------------------------------------------------------------------
#: 目录文案里可能点名的 AI 工具名（前缀白名单，避免把普通英文词当工具名）
_TOOL_MENTION_RE = re.compile(
    r"\b(?:radio|sdr|gimbal|vfo|code|memory|astro|tc|doppler|lro|baseband|"
    r"file_change|guardian|evolution|satdump|rtlsdr|hackrf|limesdr|bladerf)"
    r"_[a-z0-9_]*[a-z0-9]\b")
#: 否定词：与工具名同句出现即表示「这个工具不存在 / 不暴露」
_DENIAL_WORDS = ("不暴露", "未暴露", "不提供", "未提供", "未注册", "没有提供",
                 "不会调用", "不具备")


def _ai_tool_surface() -> set:
    """AI 工具面实况 = agent 工具注册表 ∪ MCP 工具表（两条通道都算工具面）。"""
    from mbdsdr_ai.core import mcp_server as mcp
    from mbdsdr_ai.sdr_backend import SDRBackendManager
    from mbdsdr_ai.sdr_tools import register_sdr_tools
    from mbdsdr_ai.tool_registry import ToolRegistry

    class _Agent:
        pass

    ag = _Agent()
    ag.tool_registry = ToolRegistry()
    ag.sdr_manager = SDRBackendManager()
    register_sdr_tools(ag)
    names = set(ag.tool_registry.tools)
    names |= {t["name"] for t in mcp._build_tool_table()}
    return names


def test_catalog_tool_claims_match_registry(catalog):
    """目录点名「有」的工具必须真实存在，点名「没有」的必须真的没有。

    反证：把 `capability_catalog.STATIC_CAPABILITIES` 的发射条目改回
    「AI 工具面不暴露发射工具」——`radio_ptt` 在注册表里，本用例立即红；
    反过来删掉注册表里的 `radio_send_cw` 而目录仍点名它，同样红。
    """
    real = _ai_tool_surface()
    mentioned, denied = set(), []
    for cap in catalog:
        text = " ".join((cap.title, cap.description, cap.trigger, cap.note))
        mentioned |= set(_TOOL_MENTION_RE.findall(text))
        for clause in re.split(r"[；;。\n]", text):
            hits = set(_TOOL_MENTION_RE.findall(clause))
            if hits and any(w in clause for w in _DENIAL_WORDS):
                denied.extend(sorted(hits))
    ghost = sorted(mentioned - real)
    assert not ghost, f"目录点名了注册表里不存在的工具: {ghost}"
    false_denial = sorted(set(denied) & real)
    assert not false_denial, (
        f"目录声称「不提供/不暴露」但注册表里确实有: {false_denial}")
    assert {"radio_ptt", "radio_send_cw"} <= mentioned, (
        "发射能力条目必须如实点名 AI 工具面里的发射工具，不许再写「不暴露」")


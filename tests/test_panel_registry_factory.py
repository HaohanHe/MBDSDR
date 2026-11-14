# SPDX-License-Identifier: MIT
"""
PanelSpec.factory 判据（第 2 条：面板构造从硬编码改为声明式）
=============================================================

钉住的行为：
  1. 注册表里每个面板都声明了零参工厂，`create(id)` 能造出控件；
  2. 未知 id / 无工厂返回 None，不抛异常、不缓存实例；
  3. MainWindow 的每个面板确实经过注册表工厂构造（用探针记录调用），
     而不是在窗口里 `ControlPanel()` 硬编码；
  4. `main_window.py` 不再出现这些面板类的直接构造调用（源码级守卫）；
  5. 工厂缺失时 MainWindow 立即报错，而不是无声地拿到 None。
"""
import ast
import os
import sys
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

#: 必须由注册表工厂构造的面板类（出现直接构造即视为回退到硬编码）。
PANEL_CLASSES = {
    "ControlPanel", "StatusPanel", "AIPanel", "SatTrackPanel", "ModulePanel",
    "WeatherPanel", "NewSpacetimePanel", "AdsbMapPanel", "AprsPanel",
    "SatelliteImagePanel", "RFSkyView",
    # 右栏固定页签（本笔收编）
    "ScannerPanel", "ModulationPanel", "BookmarkPanel", "AnrPanel",
    "ConstellationPanel", "DevicePanel", "GainPanel", "AdsbPanel",
}

#: 如实保留的例外：这些面板确实需要外部参数，零参构造会改变语义，
#: 因此仍由 MainWindow 直接构造（不得再新增例外）。
#:   VfoPanel(vfo_manager=...)            —— 必须共享主窗口的 VFO 列表
#:   ServiceSettingsPanel(settings=...)   —— 必须注入真实设置对象
#:   SettingsPanel(settings=..., host=...)—— 需要设置对象与宿主回调
BLOCKED_PARAM_PANELS = {"VfoPanel", "ServiceSettingsPanel", "SettingsPanel"}

#: 必须经 `_create_panel("id")` / `registry().create("id")` 构造的面板。
EXPECTED_IDS = {
    "spectrum", "rf_sky", "sat_track", "adsb", "control", "status", "ai",
    "module", "weather", "new_spacetime", "aprs", "sat_image",
    "scanner", "modulation", "bookmark", "anr", "constellation", "device",
    "gain", "adsb_list",
}

#: 右栏固定页签：不进「面板」菜单，但必须能在能力目录里查到。
FIXED_TAB_IDS = {
    "scanner", "modulation", "bookmark", "anr", "constellation", "device",
    "gain", "adsb_list",
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def main_window(qapp, isolated_profile):
    """构造一次主窗口；weather / scanner 工厂各包一层探针记录调用。"""
    from panel_registry import registry

    reg = registry()
    originals = {pid: replace(reg.get(pid)) for pid in ("weather", "scanner")}
    calls = []

    for pid in ("weather", "scanner"):
        def _spy(pid=pid):
            calls.append(pid)
            return originals[pid].factory()

        reg.register(replace(originals[pid], factory=_spy))
    try:
        from main_window import MainWindow
        window = MainWindow()
        window._factory_spy_calls = calls
        yield window
        try:
            window._disconnect()
        except Exception:  # noqa: BLE001 - 收尾失败不掩盖判据
            pass
        window.close()
    finally:
        for pid, spec in originals.items():
            reg.register(spec)


# ---------------------------------------------------------------------------
# 注册表本体
# ---------------------------------------------------------------------------
def test_every_registered_panel_declares_factory():
    """所有默认面板都有工厂：没有 factory 的 id 会让宿主拿到 None。"""
    from panel_registry import registry

    missing = registry().factoryless()
    assert missing == [], f"以下面板未声明构造工厂：{missing}"


def test_create_builds_fresh_widget_per_call(qapp):
    """create() 每次返回新实例（不缓存、不共享 Qt 父对象）。"""
    from panel_registry import registry

    a = registry().create("weather")
    b = registry().create("weather")
    try:
        assert isinstance(a, QWidget) and isinstance(b, QWidget)
        assert a is not b, "create() 不得复用同一控件实例"
        assert type(a).__name__ == "WeatherPanel"
    finally:
        a.deleteLater()
        b.deleteLater()


def test_create_unknown_id_returns_none(qapp):
    """未知 id 返回 None（宿主据此跳过该页签），不抛异常。"""
    from panel_registry import registry

    assert registry().create("no_such_panel") is None


@pytest.mark.parametrize("panel_id", sorted(EXPECTED_IDS))
def test_each_declared_panel_factory_produces_widget(qapp, panel_id):
    """逐面板验证工厂可用；可选面板依赖存在时必须能造出控件。"""
    from panel_registry import registry

    widget = registry().create(panel_id)
    if panel_id in ("aprs", "sat_image"):
        # 可选面板：依赖缺失时返回 None 是合法降级，但不得抛异常。
        if widget is None:
            pytest.skip(f"{panel_id} 的可选依赖缺失（工厂按约返回 None）")
    assert isinstance(widget, QWidget), f"{panel_id} 工厂没有返回控件：{widget!r}"
    widget.deleteLater()


def test_fixed_right_tabs_are_catalogued_without_fake_menu_entry():
    """收编的右栏固定页签：目录里查得到，且不谎称「面板菜单」可开关。"""
    from capability_catalog import build_catalog
    from panel_registry import registry

    caps = {c.source: c for c in build_catalog()}
    for pid in sorted(FIXED_TAB_IDS):
        spec = registry().get(pid)
        assert spec is not None, f"注册表缺右栏固定页签 {pid}"
        assert spec.name and spec.summary, f"{pid} 缺名称或说明"
        assert spec.closable is False, \
            f"{pid} 没有关闭按钮，不应出现在「面板」菜单（closable 必须为 False）"
        cap = caps.get(f"panel_registry:{pid}")
        assert cap is not None, f"能力目录漏了 {pid}"
        assert cap.trigger.startswith("右栏固定页签"), \
            f"{pid} 的入口文案与真实入口不符：{cap.trigger!r}"


# ---------------------------------------------------------------------------
# MainWindow 接线
# ---------------------------------------------------------------------------
def test_main_window_builds_panels_through_registry(main_window):
    """主窗口构建时确实调用了注册表工厂（不是直接 new 面板类）。"""
    assert main_window._factory_spy_calls == ["weather", "scanner"], \
        f"MainWindow 没有经注册表工厂构造面板：{main_window._factory_spy_calls}"


def test_main_window_panels_attached_by_id(main_window):
    """登记表里的控件与窗口属性是同一个对象（挂载/构造同一份）。"""
    from panel_registry import registry

    pairs = [
        ("spectrum", main_window.spectrum),
        ("rf_sky", main_window.sky_view),
        ("control", main_window.control_panel),
        ("status", main_window.status_panel),
        ("ai", main_window.ai_panel),
        ("module", main_window.module_panel),
        ("sat_track", main_window.sat_track_panel),
        ("weather", main_window.weather_panel),
        ("new_spacetime", main_window.new_spacetime_panel),
        ("adsb", main_window.adsb_map_panel),
        # 右栏固定页签
        ("scanner", main_window.scanner_panel),
        ("modulation", main_window.modulation_panel),
        ("bookmark", main_window.bookmark_panel),
        ("anr", main_window.anr_panel),
        ("constellation", main_window.constellation_panel),
        ("device", main_window.device_panel),
        ("gain", main_window.gain_panel),
        ("adsb_list", main_window.digital_adsb_panel),
    ]
    for panel_id, widget in pairs:
        assert registry().widget(panel_id) is widget, \
            f"{panel_id} 未挂进注册表或挂的不是同一个控件"


def test_main_window_refuses_missing_factory(main_window, monkeypatch):
    """工厂缺失必须立刻报错，不能把 None 往后传。"""
    from panel_registry import PanelRegistry

    monkeypatch.setattr(PanelRegistry, "create", lambda self, pid: None)
    with pytest.raises(RuntimeError, match="未在 panel_registry 声明"):
        main_window._create_panel("weather")


def test_optional_panel_refuses_missing_factory(main_window, monkeypatch):
    """可选页签同样：注册表没声明工厂是接线错误，必须当场炸。"""
    from panel_registry import PanelRegistry

    monkeypatch.setattr(PanelRegistry, "get", lambda self, pid: None)
    with pytest.raises(RuntimeError, match="未在 panel_registry 声明"):
        main_window._create_optional_panel("scanner")


def test_optional_panel_allows_missing_dependency_only(main_window, monkeypatch):
    """工厂存在但依赖缺失（返回 None）才允许降级，不得与「没声明」混淆。"""
    from panel_registry import registry

    reg = registry()
    real_get = reg.get
    monkeypatch.setattr(
        reg, "get",
        lambda pid: (replace(real_get("scanner"), factory=lambda: None)
                     if pid == "scanner" else real_get(pid)))
    assert main_window._create_optional_panel("scanner") is None


def test_main_window_source_has_no_hardcoded_panel_constructors():
    """源码守卫：main_window 不再直接构造这些面板类。"""
    path = os.path.join(_ROOT, "desktop", "main_window.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)

    direct = []
    created_ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name in PANEL_CLASSES:
            direct.append((name, node.lineno))
        if name in ("_create_panel", "_create_optional_panel") and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                created_ids.add(arg.value)
        if name == "create" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                created_ids.add(arg.value)

    # 只允许「确实需要外部参数」的三个例外继续直接构造；新增一个都算回退。
    hardcoded = {name for name, _line in direct}
    assert hardcoded <= BLOCKED_PARAM_PANELS, \
        f"出现未登记的硬编码构造：{sorted(hardcoded - BLOCKED_PARAM_PANELS)}"
    assert EXPECTED_IDS <= created_ids, \
        f"以下面板没有走注册表工厂构造：{EXPECTED_IDS - created_ids}"
